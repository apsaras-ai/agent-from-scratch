"""Chapter 10: planning DAG, replanning, verification and budgets."""
import unittest
from mini_agent.planning import (
    PlanStep, PlanExecutor, ToyRepository, Observation, PlanError, validate_plan,
)


def repair_plan():
    return (
        PlanStep("inspect", "inspect"),
        PlanStep("patch", "patch", ("inspect",)),
        PlanStep("test", "test", ("patch",)),
    )


class PlanningTests(unittest.TestCase):
    def test_valid_dag_and_real_verification(self):
        env = ToyRepository()
        result = PlanExecutor(env, max_actions=4).run(repair_plan())
        self.assertEqual(env.actions, ["inspect", "patch", "test"])
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.verification.passed)
        self.assertEqual(result.attempts, 3)

    def test_false_claim_not_success(self):
        result = PlanExecutor(ToyRepository()).run((
            PlanStep("claim", "claim_success"),
        ))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "verification_failed")

    def test_replan_after_failure(self):
        env = ToyRepository()
        def correct(old, failed_id, observation):
            self.assertEqual((failed_id, observation.detail), ("test", "FAIL"))
            return repair_plan()
        result = PlanExecutor(env, replan=correct, max_actions=5).run((
            PlanStep("test", "test"),
        ))
        self.assertEqual(result.status, "completed")
        self.assertEqual(env.actions, ["test", "inspect", "patch", "test"])
        self.assertEqual(result.attempts, 4)

    def test_failed_step_without_replanner(self):
        result = PlanExecutor(ToyRepository()).run((PlanStep("patch", "patch"),))
        self.assertEqual(result.reason, "step_failed")

    def test_budget(self):
        result = PlanExecutor(ToyRepository(), max_actions=2).run(repair_plan())
        self.assertEqual(result.reason, "action_budget_exhausted")
        self.assertEqual(result.attempts, 2)

    def test_invalid_plans(self):
        cases = [
            (PlanStep("a", "inspect", ("missing",)),),
            (PlanStep("a", "inspect", ("b",)), PlanStep("b", "patch", ("a",))),
            (PlanStep("a", "inspect"), PlanStep("a", "patch")),
            (PlanStep("a", "inspect", ("a",)),),
        ]
        for plan in cases:
            with self.subTest(plan=plan), self.assertRaises(PlanError):
                validate_plan(plan)

    def test_replanner_cannot_invalidate_completed_steps(self):
        env = ToyRepository()
        original = (PlanStep("inspect", "inspect"),
                    PlanStep("fail", "unknown", ("inspect",)))
        def invalid(_, __, ___):
            return (PlanStep("inspect", "patch"),
                    PlanStep("test", "test", ("inspect",)))
        result = PlanExecutor(env, replan=invalid).run(original)
        self.assertEqual(result.reason, "invalid_replan_completed_steps")

    def test_replan_declined(self):
        result = PlanExecutor(
            ToyRepository(), replan=lambda *args: None
        ).run((PlanStep("test", "test"),))
        self.assertEqual(result.reason, "replan_declined")


if __name__ == "__main__":
    unittest.main()
