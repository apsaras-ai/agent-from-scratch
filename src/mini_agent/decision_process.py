"""Tiny tabular decision-process algorithms for chapter 3.

The models in this module are deliberately small and KNOWN. Value iteration
here is planning by dynamic programming, not model-free reinforcement learning.
No LLM or external services are involved.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isclose, isfinite


@dataclass(frozen=True)
class Transition:
    probability: float
    next_state: str
    reward: float
    terminated: bool = False


@dataclass(frozen=True)
class ValueIterationResult:
    values: dict[str, float]
    policy: dict[str, str]
    iterations: int
    residual: float


def _probability(value: float) -> bool:
    return (
        type(value) in (float, int)
        and isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _validate_mdp(
    dynamics: Mapping[str, Mapping[str, Sequence[Transition]]],
) -> None:
    if not dynamics:
        raise ValueError("MDP must contain at least one state")
    for state, actions in dynamics.items():
        for action, outcomes in actions.items():
            if not action or not outcomes:
                raise ValueError(f"Empty action/outcomes in state {state!r}")
            mass = 0.0
            for outcome in outcomes:
                if not _probability(outcome.probability):
                    raise ValueError("Transition probability must be in [0, 1]")
                if not isfinite(outcome.reward):
                    raise ValueError("Transition reward must be finite")
                if outcome.next_state not in dynamics:
                    raise ValueError(f"Unknown next state: {outcome.next_state!r}")
                if not outcome.terminated and not dynamics[outcome.next_state]:
                    raise ValueError("Nonterminal transition enters a terminal state")
                mass += outcome.probability
            if not isclose(mass, 1.0, rel_tol=0, abs_tol=1e-9):
                raise ValueError(f"Probabilities for {state}/{action} sum to {mass}")


def _q_value(
    outcomes: Sequence[Transition],
    values: Mapping[str, float],
    discount: float,
) -> float:
    return sum(
        x.probability
        * (x.reward + (0.0 if x.terminated else discount * values[x.next_state]))
        for x in outcomes
    )


def value_iteration(
    dynamics: Mapping[str, Mapping[str, Sequence[Transition]]],
    *,
    discount: float = 0.9,
    tolerance: float = 1e-10,
    max_iterations: int = 10_000,
) -> ValueIterationResult:
    """Compute an optimal stationary policy for a finite discounted MDP.

    Uses synchronous Bellman optimality backups. A state with no actions is
    treated as terminal and assigned value zero. The convergence guarantee
    for bounded rewards uses 0 <= discount < 1.
    """
    if type(discount) not in (int, float) or not isfinite(discount) or not 0 <= discount < 1:
        raise ValueError("discount must satisfy 0 <= discount < 1")
    if not isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance must be positive and finite")
    if type(max_iterations) is not int or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")
    _validate_mdp(dynamics)

    values = {state: 0.0 for state in dynamics}
    for iteration in range(1, max_iterations + 1):
        # Jacobi style: all states use the same previous iteration values.
        new_values = {
            state: (
                max(
                    _q_value(outcomes, values, discount)
                    for outcomes in actions.values()
                )
                if actions else 0.0
            )
            for state, actions in dynamics.items()
        }
        residual = max(abs(new_values[s] - values[s]) for s in values)
        values = new_values
        if residual <= tolerance:
            break
    else:
        raise RuntimeError("Value iteration did not converge within max_iterations")

    # Sort action names to ensure repeatable tie breaking.
    policy = {
        state: max(
            sorted(actions),
            key=lambda action: _q_value(actions[action], values, discount),
        )
        for state, actions in dynamics.items()
        if actions
    }
    return ValueIterationResult(values, policy, iteration, residual)


def belief_update(
    prior: Mapping[str, float],
    transitions: Mapping[str, Mapping[str, float]],
    observation_likelihood: Mapping[str, float],
) -> dict[str, float]:
    """One Bayesian filtering step for a finite POMDP with a known action.

    transitions[old_state][new_state] = P(new_state | old_state, action)
    observation_likelihood[new_state] = P(observed | new_state, action)

    Returns P(new_state | observed, action, previous belief).
    """
    states = set(prior)
    if not states or set(transitions) != states or set(observation_likelihood) != states:
        raise ValueError("Belief, transition and likelihood state sets must match")
    if not all(_probability(p) for p in prior.values()) or not isclose(
        sum(prior.values()), 1.0, rel_tol=0, abs_tol=1e-9
    ):
        raise ValueError("Prior must be a normalized probability distribution")

    predicted = {state: 0.0 for state in prior}
    for old_state, row in transitions.items():
        if not row or not set(row).issubset(states):
            raise ValueError("Transition row contains unknown/no states")
        if not all(_probability(p) for p in row.values()) or not isclose(
            sum(row.values()), 1.0, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError("Every transition row must sum to one")
        for new_state, chance in row.items():
            predicted[new_state] += prior[old_state] * chance

    if not all(_probability(p) for p in observation_likelihood.values()):
        raise ValueError("Observation likelihoods must be in [0, 1]")
    unnormalized = {
        state: predicted[state] * observation_likelihood[state]
        for state in states
    }
    evidence = sum(unnormalized.values())
    if evidence <= 0.0:
        raise ValueError("Observed event has zero probability under the model")
    return {state: unnormalized[state] / evidence for state in prior}
