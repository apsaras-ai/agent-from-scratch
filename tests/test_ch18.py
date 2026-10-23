"""Chapter 18: true Linux host-process tests, plus capacity lifecycle tests.

IMPORTANT: This tests resource *controls* for trusted fixture scripts only.
There is NO process namespace, network namespace, seccomp, cgroup allocation,
hypervisor isolation, or protection against hostile Python code.
"""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mini_agent.process_executor import (
    ProcessLimits, ProcessRequest, ProcessRunnerError,
    TrustedProcessExecutor, TrustedScriptCatalog,
)
from mini_agent.sandbox_diagnostics import cgroup_v2_summary, namespace_summary
from mini_agent.sandbox_reservations import (
    AdmissionError, HostCapacity, ReservationController, SandboxAllocation,
)

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "ch18_trusted_worker.py"


@unittest.skipUnless(sys.platform == "linux", "POSIX Linux rlimit experiment")
class HostProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = TrustedScriptCatalog({"fixture": FIXTURE})
        cls.runner = TrustedProcessExecutor(cls.catalog)

    def test_trusted_registration_and_manifest_digest(self):
        self.assertEqual(len(self.catalog.digest("fixture")), 64)
        self.assertEqual(self.catalog.contents("fixture"), FIXTURE.read_bytes())
        with self.assertRaisesRegex(ProcessRunnerError, "unknown_or_unapproved"):
            self.catalog.contents("random-script")
        with self.assertRaisesRegex(ProcessRunnerError, "unknown_or_unapproved"):
            self.runner.run(ProcessRequest("job-unknown", "unknown"))

    def test_normal_fixture_reports_resource_limits_and_scrubbed_environment(self):
        prior = os.environ.get("CH18_PRIVATE_TEST_TOKEN")
        os.environ["CH18_PRIVATE_TEST_TOKEN"] = "this-must-not-enter-child"
        try:
            limits = ProcessLimits(
                wall_seconds=2, cpu_seconds=2,
                memory_mb=256, max_file_bytes=4096,
                max_open_files=48, max_output_bytes=4096,
            )
            result = self.runner.run(
                ProcessRequest("env-1", "fixture", ("report",)), limits)
        finally:
            if prior is None:
                os.environ.pop("CH18_PRIVATE_TEST_TOKEN", None)
            else:
                os.environ["CH18_PRIVATE_TEST_TOKEN"] = prior
        self.assertEqual((result.status, result.reason), ("completed", "exit_zero"))
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.workspace_reclaimed)
        self.assertEqual(result.lifecycle,
                         ("created", "allocated", "prepared", "running",
                          "completed", "reclaimed"))
        data = json.loads(result.stdout)
        self.assertEqual(data["job"], "env-1")
        self.assertFalse(data["private_token_present"])
        self.assertEqual(data["memory_address_limit"], 256 * 1024 * 1024)
        self.assertEqual(data["file_size_limit"], 4096)
        self.assertEqual(data["cpu_limit"], 2)
        self.assertEqual(data["open_file_limit"], 48)
        self.assertEqual(data["home"], data["cwd"])
        self.assertFalse(Path(data["cwd"]).exists())
        self.assertIn("NOT_A_SANDBOX", result.execution_boundary)

    def test_no_shell_metacharacter_interpretation(self):
        payload = "; echo UNSAFE > /tmp/somewhere"
        result = self.runner.run(
            ProcessRequest("literal", "fixture", ("echo", payload)))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stdout.strip(), payload)

    def test_nonzero_exit_is_not_success(self):
        result = self.runner.run(
            ProcessRequest("failure", "fixture", ("exit7",)))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.exit_code, 7)
        self.assertIn("synthetic controlled failure", result.stderr)
        self.assertTrue(result.workspace_reclaimed)

    def test_workspace_is_reclaimed_after_trusted_write(self):
        result = self.runner.run(ProcessRequest("scratch", "fixture", ("write",)))
        self.assertEqual(result.status, "completed")
        scratch = Path(result.stdout.strip())
        self.assertFalse(scratch.exists())
        self.assertTrue(result.workspace_reclaimed)

    def test_wall_deadline_kills_fixture_and_reclaims_workspace(self):
        result = self.runner.run(
            ProcessRequest("slow", "fixture", ("sleep",)),
            ProcessLimits(wall_seconds=.15),
        )
        self.assertEqual((result.status, result.reason),
                         ("timed_out", "wall_deadline_exceeded"))
        self.assertTrue(result.workspace_reclaimed)
        self.assertEqual(result.lifecycle[-2:], ("timed_out", "reclaimed"))

    def test_stdout_flood_is_bounded_and_terminated(self):
        result = self.runner.run(
            ProcessRequest("flood", "fixture", ("spam",)),
            ProcessLimits(max_output_bytes=512),
        )
        self.assertEqual(result.status, "output_limited")
        self.assertEqual(result.output_bytes, 512)
        self.assertLessEqual(len(result.stdout.encode()), 512)
        self.assertTrue(result.workspace_reclaimed)

    def test_file_size_rlimit_fails_without_workspace_leak(self):
        result = self.runner.run(
            ProcessRequest("bigfile", "fixture", ("bigfile",)),
            ProcessLimits(max_file_bytes=1024, wall_seconds=3),
        )
        self.assertEqual(result.status, "failed")
        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(result.workspace_reclaimed)

    def test_limits_validate_bool_negative_nan_and_zero(self):
        bad = [
            {"wall_seconds": 0},
            {"wall_seconds": float("nan")},
            {"wall_seconds": True},
            {"cpu_seconds": True},
            {"cpu_seconds": 0},
            {"memory_mb": 30},
            {"max_file_bytes": 0},
            {"max_output_bytes": 1},
            {"max_open_files": -1},
        ]
        for kwargs in bad:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ProcessLimits(**kwargs)

    def test_unapproved_script_and_invalid_ids_rejected(self):
        for kwargs in (
            {"job_id": "../escape", "script_id": "fixture"},
            {"job_id": "", "script_id": "fixture"},
            {"job_id": "valid", "script_id": "unknown/path"},
            {"job_id": "valid", "script_id": "fixture", "args": ("x",) * 9},
            {"job_id": "valid", "script_id": "fixture", "args": ("nul\x00bad",)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ProcessRunnerError):
                ProcessRequest(**kwargs)

    def test_registration_rejects_symlink_and_oversized(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            target = base / "actual.py"
            target.write_text("print('ok')", encoding="utf-8")
            link = base / "link.py"
            link.symlink_to(target)
            with self.assertRaises(ProcessRunnerError):
                TrustedScriptCatalog({"bad": link})
            with self.assertRaises(ProcessRunnerError):
                TrustedScriptCatalog({"bad": target}, max_script_bytes=2)

    def test_invalid_scratch_parent_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            link = parent / "symlink"
            link.symlink_to(parent, target_is_directory=True)
            with self.assertRaises(ProcessRunnerError):
                TrustedProcessExecutor(self.catalog, scratch_parent=link)


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.current_time = 0.0
        self.admission = ReservationController(
            HostCapacity(2000, 2048, 2048),
            clock=lambda: self.current_time,
        )
        self.plan = SandboxAllocation(1000, 512, 128, 512, 10.0)

    def test_capacity_includes_runtime_overhead(self):
        first = self.admission.reserve("run-a", "tenant", self.plan)
        self.assertEqual(first.state, "reserved")
        self.assertEqual(self.admission.usage().memory_mb, 640)
        self.admission.reserve("run-b", "tenant", self.plan)
        self.assertEqual(self.admission.usage().memory_mb, 1280)
        with self.assertRaisesRegex(AdmissionError, "resource_capacity"):
            self.admission.reserve("run-c", "tenant", self.plan)
        self.assertEqual(self.admission.usage().cpu_millis, 2000)

    def test_expiry_marks_for_reaping_but_does_not_release_capacity(self):
        self.admission.reserve("run-a", "tenant", self.plan)
        self.admission.mark_running("run-a")
        self.current_time = 10.0
        self.assertEqual(self.admission.scan_expired(), ("run-a",))
        self.assertEqual(self.admission.get("run-a").state, "reap_required")
        self.assertEqual(self.admission.usage().memory_mb, 640)
        self.assertEqual(self.admission.scan_expired(), ())
        with self.assertRaisesRegex(AdmissionError, "cleanup_confirmation"):
            self.admission.confirm_destroyed("run-a", cleanup_confirmed=False)
        self.admission.confirm_destroyed("run-a", cleanup_confirmed=True)
        self.assertEqual(self.admission.usage().memory_mb, 0)
        self.assertEqual(self.admission.get("run-a").state, "destroyed")

    def test_expired_reservation_cannot_start(self):
        self.admission.reserve("run-a", "tenant", self.plan)
        self.current_time = 11
        with self.assertRaisesRegex(AdmissionError, "lease_expired"):
            self.admission.mark_running("run-a")
        self.assertEqual(self.admission.scan_expired(), ("run-a",))

    def test_duplicate_run_is_rejected_even_after_destroyed(self):
        self.admission.reserve("run-a", "tenant", self.plan)
        self.admission.mark_reap_required("run-a")
        self.admission.confirm_destroyed("run-a", cleanup_confirmed=True)
        with self.assertRaisesRegex(AdmissionError, "already_used"):
            self.admission.reserve("run-a", "tenant", self.plan)

    def test_release_requires_reap_state(self):
        self.admission.reserve("run-a", "tenant", self.plan)
        with self.assertRaisesRegex(AdmissionError, "reap_not_requested"):
            self.admission.confirm_destroyed("run-a", cleanup_confirmed=True)
        self.admission.mark_reap_required("run-a")
        with self.assertRaises(AdmissionError):
            self.admission.mark_reap_required("run-a")

    def test_resource_spec_validations(self):
        invalid = [
            lambda: HostCapacity(True, 1024, 1024),
            lambda: HostCapacity(100, 0, 1024),
            lambda: SandboxAllocation(0, 512, 128, 512, 20),
            lambda: SandboxAllocation(1000, 512, -1, 512, 20),
            lambda: SandboxAllocation(1000, 512, 128, 512, float("inf")),
            lambda: SandboxAllocation(1000, 512, 128, 512, True),
        ]
        for fn in invalid:
            with self.subTest(fn=fn), self.assertRaises(AdmissionError):
                fn()


class DiagnosticsTests(unittest.TestCase):
    def test_nonexistent_paths_are_reported_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            ns = namespace_summary(Path(folder) / "ns")
            cg = cgroup_v2_summary(Path(folder) / "cg")
            self.assertEqual(ns["pid"], "unavailable")
            self.assertEqual(cg["memory.max"], "unavailable")

    def test_readonly_fake_cgroup_and_namespace(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            cg = folder / "cgroup"
            cg.mkdir()
            (cg / "memory.max").write_text("268435456", encoding="utf-8")
            (cg / "cpu.max").write_text("100000 100000", encoding="utf-8")
            data = cgroup_v2_summary(cg)
            self.assertEqual(data["memory.max"], "268435456")
            self.assertEqual(data["cpu.max"], "100000 100000")
            self.assertEqual(data["pids.max"], "unavailable")
            ns = folder / "ns"
            ns.mkdir()
            (ns / "user").symlink_to("user:[4026531837]")
            self.assertEqual(namespace_summary(ns)["user"], "user:[4026531837]")


if __name__ == "__main__":
    unittest.main()
