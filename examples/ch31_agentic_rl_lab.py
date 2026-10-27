"""Chapter 31: compare three genuine tabular policy-gradient methods offline."""
from mini_agent.agentic_rl import (
    ArithmeticTask, TabularPolicy, TrainingConfig,
    evaluate_rollouts, exact_success_probability, train_policy,
)


def main() -> None:
    train_task = ArithmeticTask("reach-eight", initial=1, target=8, horizon=2)
    heldout_task = ArithmeticTask("unseen-goal", initial=2, target=10, horizon=2)
    initial = exact_success_probability(TabularPolicy(), train_task)

    print("Chapter 31: policy learns a two-tool path from TERMINAL feedback.")
    print("  Public training task: 1 -> add3 -> 4 -> mul2 -> 8")
    print(f"  Untrained exact success: {initial:.3f}")
    print("  'Exact' enumerates all actions in this finite toy environment.")

    for mode in ("reinforce", "ppo", "grpo"):
        config = TrainingConfig(
            mode=mode, batch_size=32, group_size=8,
            learning_rate=2.0, epochs=1 if mode == "reinforce" else 4,
            clip_epsilon=.2, kl_beta=.01,
        )
        learned, history = train_policy(train_task, config, rounds=140, seed=31)
        exact = exact_success_probability(learned, train_task)
        successes, trials = evaluate_rollouts(learned, train_task, 2031, 1000)
        heldout = exact_success_probability(learned, heldout_task)
        print(f"\n  {mode.upper()}:")
        print(f"    learned exact success  : {exact:.3f}")
        print(f"    fresh rollout success : {successes}/{trials}")
        print(f"    untrained heldout goal: {heldout:.3f}")
        print(f"    collected training trajectories: {sum(h.collected for h in history)}")
        print(f"    clipping terms (diagnostic): {sum(h.clipped_terms for h in history)}")
        if mode == "grpo":
            print(f"    zero-variance groups: {sum(h.zero_variance_groups for h in history)}")
        assert exact > initial, "Training must beat the uniform toy baseline."
        assert abs(heldout - initial) < 1e-10

    print("\nLimitations: no LLM, GPU, tokens, value network, GAE, real tools,")
    print("external side effects, confidential heldout tasks or production sandbox.")


if __name__ == "__main__":
    main()
