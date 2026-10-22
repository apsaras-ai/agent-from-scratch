"""Chapter 15: real subprocess regression tests over trusted temporary files."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mini_agent.coding_agent import (
    CodingAgent, Edit, ModelPatchPlanner, PatchRejected, RepairContext,
    Snapshot, TestSpec, TrustedPythonTests, digest, intact,
)
from mini_agent.coding_fixture import (
    ACCEPTANCE, BUGGY_SOURCE, ISSUE, TARGETED, FixturePlanner, make_fixture,
)


class CodingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot = make_fixture(self.root)
        self.runner = TrustedPythonTests()
        self.good = Edit("range_tools.py", digest(BUGGY_SOURCE.encode()),
                         "return sum(range(start, stop))", "return sum(range(start, stop + 1))")

    def tearDown(self):
        self.temp.cleanup()

    def run_agent(self, planner=None, attempts=3):
        return CodingAgent(planner or FixturePlanner(), self.runner,
                           max_attempts=attempts).run(
            self.snapshot, ISSUE, targeted=TARGETED, acceptance=ACCEPTANCE)

    def test_real_feedback_loop_and_source_unchanged(self):
        result = self.run_agent()
        self.assertEqual((result.baseline.tests, result.baseline.failures), (4, 3))
        self.assertEqual(result.status, "verified")
        self.assertEqual([a.reason for a in result.attempts],
                         ["targeted_tests_failed", "verified_on_fixture"])
        self.assertTrue(result.attempts[-1].acceptance.passed)
        self.assertEqual(result.attempts[-1].acceptance.tests, 1)
        self.assertIn("+    return sum(range(start, stop + 1))", result.patch)
        self.assertTrue(self.snapshot.source_unchanged())

    def test_budget_exhaustion_does_not_export_bad_diff(self):
        result = self.run_agent(attempts=1)
        self.assertEqual(result.status, "attempt_budget_exhausted")
        self.assertEqual(result.patch, "")
        self.assertTrue(self.snapshot.source_unchanged())

    def test_success_on_last_allowed_attempt(self):
        self.assertEqual(self.run_agent(attempts=2).status, "verified")

    def test_independent_acceptance_rejects_overfitting(self):
        good = self.good
        feedback = []
        class Overfit:
            def propose(self, context):
                feedback.append(context.feedback)
                return replace(good, after=(good.after + " if start >= 0 else 0")) if context.attempt == 1 else good
        result = self.run_agent(Overfit())
        self.assertEqual(result.status, "verified")
        self.assertTrue(result.attempts[0].targeted.passed)
        self.assertFalse(result.attempts[0].acceptance.passed)
        self.assertEqual(result.attempts[0].reason, "acceptance_failed")
        self.assertIn("Independent acceptance failed", feedback[1])

    def test_stale_candidate_rejected_then_corrected(self):
        good = self.good
        class Retry:
            def propose(self, context):
                return replace(good, expected_sha256="0" * 64) if context.attempt == 1 else good
        result = self.run_agent(Retry())
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.attempts[0].reason, "patch_rejected:stale_snapshot")
        self.assertIsNone(result.attempts[0].targeted)

    def test_unauthorized_and_escaping_paths(self):
        for path in ("../outside.py", "/outside.py", "C:/outside.py",
                     "folder\\x.py", "./range_tools.py", "x//file.py",
                     "test_regression.py", "test_acceptance.py", "missing.py"):
            with self.subTest(path=path), self.assertRaises(PatchRejected):
                self.snapshot.prepare(replace(self.good, path=path))
        self.assertTrue(self.snapshot.source_unchanged())

    def test_exact_replacement_contract(self):
        invalid = [
            replace(self.good, before=""),
            replace(self.good, before="not present"),
            replace(self.good, before="stop"),
            replace(self.good, after=self.good.before),
            replace(self.good, after="return = 3"),
            replace(self.good, after="x" * 70000),
            replace(self.good, after="return 1\r\n"),
        ]
        for edit in invalid:
            with self.subTest(edit=edit.after[:30]), self.assertRaises(PatchRejected):
                self.snapshot.prepare(edit)

    def test_parent_symlink_and_leaf_symlink_rejected(self):
        try:
            (self.root / "alias.py").symlink_to(self.root / "range_tools.py")
            (self.root / "linked").symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit test symlinks")
        for name in ("alias.py", "linked/range_tools.py"):
            with self.subTest(name=name), self.assertRaises(PatchRejected):
                Snapshot(self.root, editable=(name,), protected=())

    def test_snapshot_does_not_copy_unlisted_secrets(self):
        (self.root / ".env").write_text("NOT_A_REAL_SECRET", encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.snapshot.populate(target)
            self.assertFalse((target / ".env").exists())
            self.assertTrue(intact(target, self.snapshot.files))

    def test_workspace_tampering_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.snapshot.populate(target)
            (target / TARGETED.filename).write_text("# replaced\n", encoding="utf-8")
            self.assertFalse(intact(target, self.snapshot.files))

    def test_test_mutation_by_candidate_halts(self):
        # Benign fault injection limited to the throwaway working copy.
        extra = ("return sum(range(start, stop + 1))\n\n"
                 "from pathlib import Path\n"
                 "(Path(__file__).parent / 'test_regression.py').write_text('# changed\\n')")
        candidate = replace(self.good, after=extra)
        class MutatesTests:
            def propose(self, context):
                return candidate
        result = self.run_agent(MutatesTests())
        self.assertEqual(result.status, "integrity_failed")
        self.assertEqual(result.patch, "")
        self.assertTrue(self.snapshot.source_unchanged())

    def test_source_drift_is_not_overwritten(self):
        good, root = self.good, self.root
        class ConcurrentEditor:
            def propose(self, context):
                (root / "range_tools.py").write_text(BUGGY_SOURCE + "# another editor\n", encoding="utf-8")
                return good
        result = self.run_agent(ConcurrentEditor())
        self.assertEqual(result.status, "source_changed")
        self.assertFalse(result.patch)
        self.assertIn("another editor", (self.root / "range_tools.py").read_text())

    def test_planner_exception_is_not_success(self):
        class BrokenPlanner:
            def propose(self, context):
                raise RuntimeError("PRIVATE_TOKEN_DO_NOT_LEAK")
        result = self.run_agent(BrokenPlanner())
        self.assertEqual(result.status, "planner_error")
        self.assertNotIn("PRIVATE_TOKEN", repr(result.attempts))

    def test_baseline_passes_without_claiming_a_repair(self):
        expected, _ = self.snapshot.prepare(self.good)
        (self.root / "range_tools.py").write_bytes(expected["range_tools.py"])
        self.snapshot = Snapshot(self.root, editable=("range_tools.py",),
                                 protected=(TARGETED.filename, ACCEPTANCE.filename))
        result = self.run_agent()
        self.assertEqual(result.status, "baseline_already_passes")
        self.assertEqual(result.attempts, ())
        self.assertEqual(result.patch, "")

    def test_bad_baseline_not_treated_as_bug_reproduction(self):
        (self.root / "range_tools.py").write_text("raise ImportError('environment broken')\n", encoding="utf-8")
        self.snapshot = Snapshot(self.root, editable=("range_tools.py",),
                                 protected=(TARGETED.filename, ACCEPTANCE.filename))
        self.assertEqual(self.run_agent().status, "baseline_not_reproduced")

    def test_empty_test_suite_cannot_pass(self):
        (self.root / "empty_tests.py").write_text("# no tests\n", encoding="utf-8")
        result = self.runner.run(self.root, TestSpec("empty_tests.py", 1))
        self.assertEqual((result.tests, result.returncode), (0, 0))
        self.assertFalse(result.passed)

    def test_skip_and_wrong_inventory_cannot_pass(self):
        content = "import unittest\nclass T(unittest.TestCase):\n    @unittest.skip('skip')\n    def test_x(self): pass\n"
        (self.root / "skip_tests.py").write_text(content, encoding="utf-8")
        skipped = self.runner.run(self.root, TestSpec("skip_tests.py", 1))
        self.assertFalse(skipped.passed)
        self.assertEqual(skipped.skipped, 1)
        (self.root / "skip_tests.py").write_text(content.replace("    @unittest.skip('skip')\n", ""), encoding="utf-8")
        self.assertFalse(self.runner.run(self.root, TestSpec("skip_tests.py", 2)).passed)

    def test_timeout_is_failure_not_success(self):
        (self.root / "slow_tests.py").write_text("import time\ntime.sleep(3)\n", encoding="utf-8")
        result = TrustedPythonTests(0.05).run(self.root, TestSpec("slow_tests.py", 1))
        self.assertTrue(result.timed_out)
        self.assertFalse(result.passed)

    def test_child_does_not_inherit_api_key(self):
        text = ("import os, unittest\nclass T(unittest.TestCase):\n"
                "    def test_environment(self):\n"
                "        self.assertNotIn('OPENAI_API_KEY', os.environ)\n")
        (self.root / "env_tests.py").write_text(text, encoding="utf-8")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fake-do-not-inherit"}):
            self.assertTrue(self.runner.run(self.root, TestSpec("env_tests.py", 1)).passed)

    def test_invalid_configuration(self):
        for name in ("test_*.py", "../x.py", "nested/x.py"):
            with self.assertRaises(ValueError):
                TestSpec(name, 1)
        for value in (0, -1, True, float("nan")):
            with self.assertRaises(ValueError):
                TrustedPythonTests(value)
        with self.assertRaises(ValueError):
            Snapshot(self.root, editable=(TARGETED.filename,), protected=(TARGETED.filename,))

    @unittest.skipUnless(shutil.which("git"), "Git unavailable")
    def test_exported_diff_passes_git_apply_check(self):
        expected, diff = self.snapshot.prepare(self.good)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.snapshot.populate(target)
            for command in (["git", "apply", "--check", "-"], ["git", "apply", "-"]):
                subprocess.run(command, cwd=target, input=diff, text=True,
                               check=True, capture_output=True, timeout=3)
            self.assertTrue(intact(target, expected))


class ModelPlannerTests(unittest.TestCase):
    def setUp(self):
        self.context = RepairContext(ISSUE, "range_tools.py", (
            ("range_tools.py", digest(BUGGY_SOURCE.encode()), BUGGY_SOURCE),
        ), "tests failed", 1)
        self.data = dict(path="range_tools.py", expected_sha256=digest(BUGGY_SOURCE.encode()),
                         before="return sum(range(start, stop))", after="return sum(range(start, stop + 1))")

    def model(self, content, reason="stop", calls=()):
        return SimpleNamespace(complete=lambda messages, tools:
                               SimpleNamespace(content=content, finish_reason=reason, tool_calls=calls))

    def test_strict_patch_adapter(self):
        edit = ModelPatchPlanner(self.model(json.dumps(self.data))).propose(self.context)
        self.assertEqual(edit, Edit(**self.data))

    def test_invalid_output_never_becomes_patch(self):
        invalid = ["not json", "```json\n{}\n```", "[]", '{"path":"a","path":"b"}',
                   json.dumps({**self.data, "command": "do not execute"}),
                   json.dumps({**self.data, "after": 42})]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(PatchRejected):
                ModelPatchPlanner(self.model(payload)).propose(self.context)
        for reason, calls in (("length", ()), ("refusal", ()), ("stop", ("tool",))):
            with self.assertRaises(PatchRejected):
                ModelPatchPlanner(self.model(json.dumps(self.data), reason, calls)).propose(self.context)

    def test_model_adapter_to_real_controller_with_fake_model(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = make_fixture(Path(directory))
            proposer = ModelPatchPlanner(self.model(json.dumps(self.data)))
            result = CodingAgent(proposer, TrustedPythonTests(), max_attempts=1).run(
                snapshot, ISSUE, targeted=TARGETED, acceptance=ACCEPTANCE)
            self.assertEqual(result.status, "verified")
            self.assertEqual(len(result.attempts), 1)


if __name__ == "__main__":
    unittest.main()
