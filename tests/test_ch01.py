"""Chapter 1 offline architecture comparison tests."""
import unittest

from examples.ch01_workflow_vs_agent import run_scenario


class Chapter1ControlFlowTests(unittest.TestCase):
    def test_primary_hit(self) -> None:
        pipeline = run_scenario(True, "pipeline")
        workflow = run_scenario(True, "workflow")
        policy = run_scenario(True, "policy_loop")
        self.assertEqual({pipeline["answer"], workflow["answer"], policy["answer"]}, {"FOUND:42"})
        self.assertEqual(pipeline["tools"], ("primary", "backup"))
        self.assertEqual(workflow["tools"], ("primary",))
        self.assertEqual(policy["tools"], ("primary",))
        self.assertEqual(policy["model_steps"], 2)

    def test_primary_miss(self) -> None:
        for architecture in ("pipeline", "workflow", "policy_loop"):
            result = run_scenario(False, architecture)
            self.assertEqual(result["answer"], "FOUND:42")
            self.assertEqual(result["tools"], ("primary", "backup"))
        self.assertEqual(run_scenario(False, "policy_loop")["model_steps"], 3)

    def test_unknown_architecture(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown architecture"):
            run_scenario(True, "unknown")


if __name__ == "__main__":
    unittest.main()
