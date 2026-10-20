"""Deterministic checks for the chapter 3 mathematical experiments."""
import unittest

from examples.ch03_decision_process import (
    CAUSES, IDENTITY, PRIOR, coding_mdp, evaluate_diagnosis,
)
from mini_agent.decision_process import Transition, belief_update, value_iteration


class ValueIterationTests(unittest.TestCase):
    def test_discounted_optimal_decision(self) -> None:
        result = value_iteration(coding_mdp(), discount=0.9)
        self.assertEqual(result.policy["start"], "inspect")
        self.assertEqual(result.policy["inspected"], "targeted_fix")
        self.assertAlmostEqual(result.values["start"], 5.3)
        self.assertAlmostEqual(result.values["inspected"], 7.0)
        self.assertNotIn("done", result.policy)

    def test_low_discount_prefers_immediate_action(self) -> None:
        result = value_iteration(coding_mdp(), discount=0.2)
        self.assertEqual(result.policy["start"], "quick_fix")
        self.assertAlmostEqual(result.values["start"], 3.0)

    def test_gamma_bounds_and_iteration_limit(self) -> None:
        for invalid in (-0.01, 1.0, 2.0):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                value_iteration(coding_mdp(), discount=invalid)
        with self.assertRaisesRegex(RuntimeError, "did not converge"):
            value_iteration(coding_mdp(), max_iterations=1)

    def test_invalid_transition_mass(self) -> None:
        model = {"start": {"fix": (Transition(0.8, "done", 5.0, True),)}, "done": {}}
        with self.assertRaisesRegex(ValueError, "sum"):
            value_iteration(model)

    def test_unknown_state(self) -> None:
        model = {"start": {"fix": (Transition(1.0, "missing", 5.0, True),)}}
        with self.assertRaisesRegex(ValueError, "Unknown next state"):
            value_iteration(model)


class POMDPTests(unittest.TestCase):
    def test_bayesian_posterior(self) -> None:
        posterior = belief_update(
            PRIOR, IDENTITY, {"dependency": 0.8, "logic": 0.2},
        )
        self.assertAlmostEqual(posterior["dependency"], 0.8)
        self.assertAlmostEqual(posterior["logic"], 0.2)
        self.assertAlmostEqual(sum(posterior.values()), 1.0)

    def test_informative_inspection(self) -> None:
        direct = evaluate_diagnosis(0.8, inspect=False)
        inspected = evaluate_diagnosis(0.8, inspect=True)
        self.assertAlmostEqual(direct.success_rate, 0.5)
        self.assertAlmostEqual(direct.expected_reward, 3.0)
        self.assertAlmostEqual(inspected.success_rate, 0.8)
        self.assertAlmostEqual(inspected.expected_reward, 6.2)

    def test_useless_inspection(self) -> None:
        inspected = evaluate_diagnosis(0.5, inspect=True)
        self.assertAlmostEqual(inspected.success_rate, 0.5)
        self.assertAlmostEqual(inspected.expected_reward, 2.0)

    def test_information_threshold(self) -> None:
        self.assertLess(
            evaluate_diagnosis(0.55, inspect=True).expected_reward,
            evaluate_diagnosis(0.55, inspect=False).expected_reward,
        )
        self.assertGreater(
            evaluate_diagnosis(0.60, inspect=True).expected_reward,
            evaluate_diagnosis(0.60, inspect=False).expected_reward,
        )

    def test_impossible_observation(self) -> None:
        with self.assertRaisesRegex(ValueError, "zero probability"):
            belief_update(PRIOR, IDENTITY, {"dependency": 0.0, "logic": 0.0})

    def test_bad_distribution(self) -> None:
        with self.assertRaisesRegex(ValueError, "normalized"):
            belief_update({"dependency": 0.8, "logic": 0.8}, IDENTITY, PRIOR)
        with self.assertRaisesRegex(ValueError, "sum to one"):
            belief_update(PRIOR, {s: {s: 0.8} for s in CAUSES}, PRIOR)

    def test_invalid_accuracy(self) -> None:
        for accuracy in (0.4, 1.1):
            with self.subTest(accuracy=accuracy), self.assertRaises(ValueError):
                evaluate_diagnosis(accuracy, inspect=True)


if __name__ == "__main__":
    unittest.main()
