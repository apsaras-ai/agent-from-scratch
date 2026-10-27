"""Chapter 31 contract tests: actual offline policy updates, not LLM training."""
from __future__ import annotations

from dataclasses import replace
from math import isfinite
from random import Random
import unittest

from mini_agent.agentic_rl import (
    ACTIONS, ArithmeticTask, RLContractError, TabularPolicy, ToolStep,
    TrainingConfig, Trajectory, clipped_surrogate, evaluate_rollouts,
    exact_success_probability, group_relative_advantages, legal_actions,
    rollout, surrogate_gradient_factor, train_policy, train_round,
    trusted_transition, verify_trajectory,
)


class HostEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.task = ArithmeticTask()

    def test_known_unique_successful_two_turn_tool_path(self):
        steps = (ToolStep(1, "add3", 4), ToolStep(4, "mul2", 8))
        result = verify_trajectory(Trajectory(self.task, steps, 8, 1.0))
        self.assertEqual((result.reward, result.tool_calls, result.final_state),
                         (1.0, 2, 8))

    def test_valid_but_wrong_trajectory_does_not_claim_success(self):
        steps = (ToolStep(1, "mul2", 2), ToolStep(2, "mul2", 4))
        report = verify_trajectory(Trajectory(self.task, steps, 4, 0.0))
        self.assertEqual(report.reward, 0)

    def test_untrusted_trajectory_cannot_forge_tool_output(self):
        steps = (ToolStep(1, "add3", 8), ToolStep(8, "mul2", 16))
        with self.assertRaises(RLContractError):
            verify_trajectory(Trajectory(self.task, steps, 16, 0.0))

    def test_untrusted_trajectory_cannot_forge_reward_or_final(self):
        good = (ToolStep(1, "add3", 4), ToolStep(4, "mul2", 8))
        for final, reward in ((8, 0.0), (999, 1.0), (True, 1.0), (8, float("nan"))):
            with self.subTest(final=final, reward=reward):
                with self.assertRaises(RLContractError):
                    verify_trajectory(Trajectory(self.task, good, final, reward))

    def test_unknown_tool_and_out_of_bounds_transition_are_rejected(self):
        with self.assertRaises(RLContractError):
            trusted_transition(self.task, 1, "rm -rf /")
        with self.assertRaises(RLContractError):
            trusted_transition(replace(self.task, max_value=8), 7, "mul2")
        self.assertEqual(legal_actions(replace(self.task, max_value=8), 7), ("add1",))

    def test_incomplete_trajectory_cannot_receive_terminal_reward(self):
        with self.assertRaises(RLContractError):
            verify_trajectory(Trajectory(self.task, (ToolStep(1, "add3", 4),), 4, 0.0))

    def test_invalid_tasks_and_budget_types_fail_closed(self):
        for bad in (
            dict(horizon=0), dict(horizon=True), dict(max_value=-1),
            dict(initial=-1), dict(target=500), dict(task_id="../../oops"),
        ):
            with self.subTest(bad=bad), self.assertRaises(RLContractError):
                ArithmeticTask(**bad)

    def test_only_allowlisted_actions_can_be_sampled(self):
        p = TabularPolicy()
        random = Random(5)
        for _ in range(100):
            t = rollout(p, self.task, random)
            self.assertTrue(all(s.action in ACTIONS for s in t.steps))
            self.assertEqual(verify_trajectory(t).reward, t.reported_reward)


class PolicyMathTests(unittest.TestCase):
    def setUp(self):
        self.task = ArithmeticTask()

    def test_uniform_policy_exact_probability_is_one_ninth(self):
        p = TabularPolicy()
        self.assertAlmostEqual(exact_success_probability(p, self.task), 1 / 9)
        successes, n = evaluate_rollouts(p, self.task, seed=999, episodes=9000)
        self.assertLess(abs(successes / n - 1 / 9), 0.025)

    def test_probabilities_sum_to_one_and_illegal_actions_masked(self):
        policy = TabularPolicy()
        tight = replace(self.task, max_value=8)
        probabilities = policy.probabilities(tight, (7, 8, 1))
        self.assertEqual(probabilities, {"add1": 1.0})
        self.assertAlmostEqual(sum(policy.probabilities(self.task, (1, 8, 2)).values()), 1)

    def test_extreme_logits_stably_normalized(self):
        policy = TabularPolicy({(1, 8, 2): {"add3": 80.0, "mul2": -80.0}})
        probs = policy.probabilities(self.task, (1, 8, 2))
        self.assertTrue(all(isfinite(v) for v in probs.values()))
        self.assertAlmostEqual(sum(probs.values()), 1)
        self.assertGreater(probs["add3"], .99999)

    def test_group_normalization_and_zero_variance(self):
        values = group_relative_advantages((0.0, 1.0, 0.0, 1.0))
        self.assertEqual(values, (-1.0, 1.0, -1.0, 1.0))
        self.assertEqual(group_relative_advantages((1.0, 1.0, 1.0)), (0.0,) * 3)

    def test_invalid_group_inputs(self):
        for group in ((1.0,), (1, True), (0.0, float("nan")), (0.0, -1.0)):
            with self.subTest(group=group), self.assertRaises(RLContractError):
                group_relative_advantages(group)

    def test_ppo_clips_both_positive_and_negative_advantages(self):
        self.assertAlmostEqual(clipped_surrogate(1.5, 1.0, .2), 1.2)
        self.assertEqual(surrogate_gradient_factor(1.5, 1.0, .2), 0)
        self.assertAlmostEqual(clipped_surrogate(.6, -1.0, .2), -.8)
        self.assertEqual(surrogate_gradient_factor(.6, -1.0, .2), 0)
        self.assertAlmostEqual(clipped_surrogate(1.5, -1.0, .2), -1.5)
        self.assertAlmostEqual(surrogate_gradient_factor(1.5, -1.0, .2), -1.5)

    def test_clip_input_rejects_nan_and_infinity(self):
        with self.assertRaises(RLContractError):
            clipped_surrogate(float("nan"), 1, .2)
        with self.assertRaises(RLContractError):
            surrogate_gradient_factor(.9, float("inf"), .2)

    def test_policy_does_not_leak_mutations_through_clone(self):
        p = TabularPolicy({(1, 8, 2): {"add3": .3}})
        clone = p.clone()
        clone.logits[(1, 8, 2)]["add3"] = 5
        self.assertEqual(p.logits[(1, 8, 2)]["add3"], .3)


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.task = ArithmeticTask()

    def test_reinforce_requires_one_epoch(self):
        with self.assertRaises(RLContractError):
            TrainingConfig(mode="reinforce")
        TrainingConfig(mode="reinforce", epochs=1)

    def test_grpo_requires_divisible_groups_and_positive_budgets(self):
        for cfg in (
            {"batch_size": 7, "group_size": 4},
            {"group_size": 1},
            {"batch_size": True},
            {"learning_rate": float("inf")},
            {"clip_epsilon": 1.0},
            {"kl_beta": -1.0},
        ):
            with self.subTest(cfg=cfg), self.assertRaises(RLContractError):
                TrainingConfig(**cfg)

    def test_grpo_zero_variance_group_does_not_invent_reward_gradient(self):
        # Both actions lead to the same failure; no success available within 1 step.
        task = ArithmeticTask("impossible", initial=1, target=31, horizon=1)
        p = TabularPolicy()
        cfg = TrainingConfig(mode="grpo", batch_size=16, group_size=8, kl_beta=0)
        report = train_round(p, task, Random(4), cfg)
        self.assertEqual(report.verified_successes, 0)
        self.assertEqual(report.zero_variance_groups, 2)
        self.assertEqual(report.policy_gradient_terms, 0)
        self.assertEqual(exact_success_probability(p, task), 0.0)

    def test_kl_regularizer_works_with_no_reward_variance(self):
        task = ArithmeticTask("impossible", 1, 31, 1)
        p = TabularPolicy({(1, 31, 1): {"add3": 2.0}})
        old_prob = p.probabilities(task, (1, 31, 1))["add3"]
        report = train_round(p, task, Random(4), TrainingConfig(
            batch_size=16, group_size=8, kl_beta=.5, learning_rate=2),
        )
        self.assertEqual(report.policy_gradient_terms, 0)
        self.assertLess(p.probabilities(task, (1, 31, 1))["add3"], old_prob)

    def test_grpo_actual_policy_learning_improves_independent_exact_success(self):
        baseline = exact_success_probability(TabularPolicy(), self.task)
        policy, reports = train_policy(
            self.task, TrainingConfig(mode="grpo", batch_size=32,
                                      group_size=8, epochs=4, learning_rate=2),
            rounds=140, seed=31,
        )
        after = exact_success_probability(policy, self.task)
        self.assertEqual(len(reports), 140)
        self.assertGreater(after, baseline + .4)
        self.assertGreater(after, .6)
        self.assertGreater(sum(r.verified_successes for r in reports[-10:]), 0)

    def test_seeded_training_reproducible(self):
        cfg = TrainingConfig(mode="ppo", batch_size=16, epochs=3)
        a, hist_a = train_policy(self.task, cfg, rounds=6, seed=42)
        b, hist_b = train_policy(self.task, cfg, rounds=6, seed=42)
        self.assertEqual(a.logits, b.logits)
        self.assertEqual(hist_a, hist_b)

    def test_modes_are_real_updates_and_not_sft_label_lookup(self):
        for mode in ("reinforce", "ppo", "grpo"):
            cfg = TrainingConfig(mode=mode, batch_size=48, epochs=1,
                                 group_size=8, learning_rate=3)
            policy, hist = train_policy(self.task, cfg, rounds=90, seed=17)
            self.assertGreater(exact_success_probability(policy, self.task), 1/9)
            self.assertTrue(any(r.verified_successes > 0 for r in hist))

    def test_holdout_task_is_not_magically_learned_by_unseen_state_table(self):
        learned, _ = train_policy(
            self.task, TrainingConfig(batch_size=16, group_size=8),
            rounds=20, seed=11,
        )
        # Different target => entirely new observation keys. Explicit limitation.
        unseen = ArithmeticTask("heldout", 2, 10, 2)
        self.assertAlmostEqual(exact_success_probability(learned, unseen), 1 / 9)

    def test_rejected_numeric_and_invalid_trajectory_cannot_be_rewarded(self):
        self.assertEqual(verify_trajectory(rollout(TabularPolicy(), self.task, Random(2))).tool_calls, 2)
        with self.assertRaises(RLContractError):
            train_policy(self.task, TrainingConfig(), rounds=0)
        with self.assertRaises(RLContractError):
            evaluate_rollouts(TabularPolicy(), self.task, seed=True)


if __name__ == "__main__":
    unittest.main()
