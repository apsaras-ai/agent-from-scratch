"""Trusted, dependency-free repository fixture and rule planner for chapter 15.

The planner is scripted, not an LLM. Files and unittest execution are real.
"""
from pathlib import Path

from .coding_agent import Edit, RepairContext, Snapshot, TestSpec

BUGGY_SOURCE = '''def inclusive_sum(start: int, stop: int) -> int:
    """Sum every integer in the inclusive interval; reject reversed bounds."""
    if start > stop:
        raise ValueError("start must not exceed stop")
    return sum(range(start, stop))
'''
REGRESSION_TESTS = '''import unittest
from range_tools import inclusive_sum

class RegressionTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(inclusive_sum(1, 3), 6)
    def test_single(self):
        self.assertEqual(inclusive_sum(4, 4), 4)
    def test_zero(self):
        self.assertEqual(inclusive_sum(0, 2), 3)
    def test_reversed(self):
        with self.assertRaises(ValueError):
            inclusive_sum(3, 1)
'''
ACCEPTANCE_TESTS = '''import unittest
from range_tools import inclusive_sum

class AcceptanceTests(unittest.TestCase):
    def test_all_intervals(self):
        for start in range(-4, 5):
            for stop in range(start, 5):
                with self.subTest(start=start, stop=stop):
                    expected = (start + stop) * (stop - start + 1) // 2
                    self.assertEqual(inclusive_sum(start, stop), expected)
'''
TARGETED = TestSpec("test_regression.py", 4)
ACCEPTANCE = TestSpec("test_acceptance.py", 1)
ISSUE = "inclusive_sum must include both endpoints, preserve negative intervals, and reject reversed bounds."


def make_fixture(root: Path) -> Snapshot:
    root.mkdir(parents=True, exist_ok=True)
    files = {"range_tools.py": BUGGY_SOURCE,
             TARGETED.filename: REGRESSION_TESTS,
             ACCEPTANCE.filename: ACCEPTANCE_TESTS}
    for name, text in files.items():
        path = root / name
        if path.exists() or path.is_symlink():
            raise FileExistsError("Use a fresh empty fixture directory")
    for name, text in files.items():
        (root / name).write_bytes(text.encode("utf-8"))
    return Snapshot(root, editable=("range_tools.py",),
                    protected=(TARGETED.filename, ACCEPTANCE.filename))


class FixturePlanner:
    """Inject one incorrect candidate so the feedback/rollback path is exercised."""

    def propose(self, context: RepairContext) -> Edit:
        name, version, _ = context.files[0]
        stop = "stop + 2" if context.attempt == 1 else "stop + 1"
        return Edit(name, version, "return sum(range(start, stop))",
                    f"return sum(range(start, {stop}))")
