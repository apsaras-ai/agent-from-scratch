"""Chapter 3: exact finite-MDP planning and Bayesian fault diagnosis.

All results are computed analytically by enumerating a tiny model.
No LLM, Monte Carlo sampling, or hidden random seed is involved.
"""
from __future__ import annotations

from dataclasses import dataclass

from mini_agent.decision_process import Transition, belief_update, value_iteration


def coding_mdp() -> dict[str, dict[str, tuple[Transition, ...]]]:
    """Fully observable *toy* MDP, separate from the POMDP below."""
    return {
        "start": {
            "quick_fix": (
                Transition(0.5, "done", 10.0, True),
                Transition(0.5, "failed", -4.0, True),
            ),
            "inspect": (Transition(1.0, "inspected", -1.0),),
        },
        "inspected": {
            "targeted_fix": (Transition(1.0, "done", 7.0, True),),
        },
        "done": {},
        "failed": {},
    }


@dataclass(frozen=True)
class StrategyScore:
    success_rate: float
    expected_reward: float


CAUSES = ("dependency", "logic")
PRIOR = {"dependency": 0.5, "logic": 0.5}
IDENTITY = {state: {state: 1.0} for state in CAUSES}


def diagnostic_likelihood(actual_cause: str, reported_cause: str, accuracy: float) -> float:
    return accuracy if actual_cause == reported_cause else 1.0 - accuracy


def choose_patch(posterior: dict[str, float]) -> str:
    """Choose MAP estimate; lexicographic order resolves equal probabilities."""
    return max(sorted(posterior), key=lambda state: posterior[state])


def evaluate_diagnosis(accuracy: float, *, inspect: bool) -> StrategyScore:
    """Exact expected score of either patch-now or inspect-then-patch.

    State is an unchanged hidden bug cause. Inspection reports the true
    cause with probability 'accuracy', costs 1, and patching is terminal:
    correct patch yields +10; incorrect patch yields -4.
    """
    if not 0.5 <= accuracy <= 1.0:
        raise ValueError("accuracy must be between 0.5 and 1.0")

    if not inspect:
        patch = choose_patch(PRIOR)
        success = PRIOR[patch]
        return StrategyScore(success, 10.0 * success - 4.0 * (1.0 - success))

    success = 0.0
    expected_reward = -1.0  # One inspection, regardless of observation.
    for actual_cause in CAUSES:
        for observed_cause in CAUSES:
            joint = PRIOR[actual_cause] * diagnostic_likelihood(
                actual_cause, observed_cause, accuracy
            )
            # Identity transitions: inspection does not alter the root cause.
            likelihoods = {
                state: diagnostic_likelihood(state, observed_cause, accuracy)
                for state in CAUSES
            }
            posterior = belief_update(PRIOR, IDENTITY, likelihoods)
            correct = choose_patch(posterior) == actual_cause
            success += joint * int(correct)
            expected_reward += joint * (10.0 if correct else -4.0)
    return StrategyScore(success, expected_reward)


def main() -> None:
    for discount in (0.9, 0.2):
        result = value_iteration(coding_mdp(), discount=discount)
        print(
            f"MDP discount={discount:.1f}: "
            f"start_action={result.policy['start']} "
            f"start_value={result.values['start']:.2f}"
        )

    posterior = belief_update(
        PRIOR,
        IDENTITY,
        {"dependency": 0.8, "logic": 0.2},
    )
    print(f"POMDP posterior P(dependency | dependency_report)={posterior['dependency']:.2f}")
    for accuracy in (0.5, 0.55, 0.6, 0.8, 1.0):
        direct = evaluate_diagnosis(accuracy, inspect=False)
        inspected = evaluate_diagnosis(accuracy, inspect=True)
        preferred = "inspect" if inspected.expected_reward > direct.expected_reward else "direct"
        print(
            f"accuracy={accuracy:.2f} direct_reward={direct.expected_reward:.2f} "
            f"inspect_reward={inspected.expected_reward:.2f} "
            f"inspect_success={inspected.success_rate:.2f} preferred={preferred}"
        )


if __name__ == "__main__":
    main()
