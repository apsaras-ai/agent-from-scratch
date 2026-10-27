"""Chapter 31: bounded, offline, multi-turn agentic policy-gradient lab.

This is real stochastic *tabular policy optimization* on a trusted arithmetic
environment, NOT LLM fine-tuning or a full PPO/GRPO implementation. The policy
chooses safe symbolic tools; the host independently replays the whole path.
No eval, shell, network, filesystem mutation or model-generated code execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, isfinite, log, sqrt
from random import Random
from typing import Literal

ACTIONS = ("add1", "add3", "mul2")
Mode = Literal["reinforce", "ppo", "grpo"]
Observation = tuple[int, int, int]  # state, target, remaining steps


class RLContractError(ValueError):
    """Malformed task/trajectory or unauthorized symbolic tool action."""


def _integer(value: object, lo: int, hi: int) -> bool:
    return type(value) is int and lo <= value <= hi


def _real(value: object, lo: float, hi: float) -> bool:
    return type(value) in (float, int) and lo <= value <= hi and isfinite(value)


@dataclass(frozen=True)
class ArithmeticTask:
    """Public fixed-horizon goal. 'Success' is equality after the final step."""
    task_id: str = "reach-eight"
    initial: int = 1
    target: int = 8
    horizon: int = 2
    max_value: int = 32

    def __post_init__(self) -> None:
        if (not isinstance(self.task_id, str)
                or not 1 <= len(self.task_id) <= 64
                or not all(c.isalnum() or c in "-_" for c in self.task_id)
                or not _integer(self.max_value, 1, 10_000)
                or not _integer(self.initial, 0, self.max_value)
                or not _integer(self.target, 0, self.max_value)
                or not _integer(self.horizon, 1, 8)):
            raise RLContractError("invalid_arithmetic_task")


def _next_value(state: int, action: str) -> int:
    if action == "add1":
        return state + 1
    if action == "add3":
        return state + 3
    if action == "mul2":
        return state * 2
    raise RLContractError("action_not_allowlisted")


def legal_actions(task: ArithmeticTask, state: int) -> tuple[str, ...]:
    if not _integer(state, 0, task.max_value):
        raise RLContractError("state_out_of_bounds")
    return tuple(a for a in ACTIONS if _next_value(state, a) <= task.max_value)


def trusted_transition(task: ArithmeticTask, state: int, action: str) -> int:
    """Host-owned deterministic tool; never dispatches arbitrary model strings."""
    if type(action) is not str or action not in legal_actions(task, state):
        raise RLContractError("illegal_or_out_of_bounds_tool")
    return _next_value(state, action)


@dataclass(frozen=True)
class ToolStep:
    state: int
    action: str
    next_state: int


@dataclass(frozen=True)
class Trajectory:
    task: ArithmeticTask
    steps: tuple[ToolStep, ...]
    claimed_final: int
    reported_reward: float


@dataclass(frozen=True)
class VerifiedOutcome:
    reward: float
    final_state: int
    tool_calls: int


def verify_trajectory(trajectory: Trajectory) -> VerifiedOutcome:
    """Replays ALL actions from trusted task initial state, never trusts claims."""
    if not isinstance(trajectory, Trajectory) or not isinstance(trajectory.task, ArithmeticTask):
        raise RLContractError("invalid_trajectory")
    task = trajectory.task
    if (type(trajectory.steps) is not tuple
            or len(trajectory.steps) != task.horizon):
        raise RLContractError("incorrect_trajectory_length")
    state = task.initial
    for record in trajectory.steps:
        if not isinstance(record, ToolStep) or type(record.state) is not int or record.state != state:
            raise RLContractError("forged_observation")
        state = trusted_transition(task, state, record.action)
        if type(record.next_state) is not int or record.next_state != state:
            raise RLContractError("forged_tool_result")
    reward = float(state == task.target)
    if (type(trajectory.claimed_final) is not int
            or trajectory.claimed_final != state
            or not _real(trajectory.reported_reward, 0.0, 1.0)
            or trajectory.reported_reward != reward):
        raise RLContractError("forged_terminal_result")
    return VerifiedOutcome(reward, state, len(trajectory.steps))


@dataclass
class TabularPolicy:
    """Softmax logits indexed by public observations; unseen states are uniform."""
    logits: dict[Observation, dict[str, float]] = field(default_factory=dict)

    def clone(self) -> TabularPolicy:
        return TabularPolicy({obs: dict(x) for obs, x in self.logits.items()})

    def probabilities(self, task: ArithmeticTask, obs: Observation) -> dict[str, float]:
        if type(obs) is not tuple or len(obs) != 3:
            raise RLContractError("invalid_observation")
        state, target, remaining = obs
        if target != task.target or not _integer(remaining, 1, task.horizon):
            raise RLContractError("invalid_observation")
        choices = legal_actions(task, state)
        if not choices:
            raise RLContractError("no_valid_actions")
        weights = self.logits.get(obs, {})
        if not isinstance(weights, dict) or any(
            a not in ACTIONS or not _real(v, -100.0, 100.0)
            for a, v in weights.items()
        ):
            raise RLContractError("invalid_policy_logits")
        scores = [weights.get(a, 0.0) for a in choices]
        offset = max(scores)
        values = [exp(v - offset) for v in scores]
        total = sum(values)
        return {a: v / total for a, v in zip(choices, values)}

    def sample(self, task: ArithmeticTask, obs: Observation, rng: Random) -> str:
        choices = self.probabilities(task, obs)
        threshold = rng.random()
        cumulative = 0.0
        for action, probability in choices.items():
            cumulative += probability
            if threshold < cumulative:
                return action
        return next(reversed(choices))  # floating-point accumulation guard


def rollout(policy: TabularPolicy, task: ArithmeticTask, rng: Random) -> Trajectory:
    """Trusted collector; terminal truth comes from independent replay."""
    state = task.initial
    steps: list[ToolStep] = []
    for turn in range(task.horizon):
        obs = (state, task.target, task.horizon - turn)
        action = policy.sample(task, obs, rng)
        next_state = trusted_transition(task, state, action)
        steps.append(ToolStep(state, action, next_state))
        state = next_state
    result = Trajectory(task, tuple(steps), state, float(state == task.target))
    verify_trajectory(result)
    return result


def clipped_surrogate(ratio: float, advantage: float, epsilon: float) -> float:
    """min(r*A, clip(r,1-e,1+e)*A), including negative-advantage cases."""
    if (not _real(ratio, 0.0, 1e12) or ratio == 0
            or not _real(advantage, -1e6, 1e6)
            or not _real(epsilon, 0.0, 1.0)):
        raise RLContractError("invalid_clip_inputs")
    clipped = max(1.0 - epsilon, min(1.0 + epsilon, ratio))
    return min(ratio * advantage, clipped * advantage)


def surrogate_gradient_factor(ratio: float, advantage: float, epsilon: float) -> float:
    """d clipped objective / d log pi_new(a|s). Zero in clipped plateau."""
    clipped_surrogate(ratio, advantage, epsilon)
    if (advantage > 0 and ratio > 1 + epsilon
            or advantage < 0 and ratio < 1 - epsilon):
        return 0.0
    return ratio * advantage


def group_relative_advantages(rewards: tuple[float, ...]) -> tuple[float, ...]:
    """Population std; zero-variance group has exactly zero advantage."""
    if (type(rewards) is not tuple or len(rewards) < 2
            or any(not _real(r, 0.0, 1.0) for r in rewards)):
        raise RLContractError("invalid_reward_group")
    mean = sum(rewards) / len(rewards)
    std = sqrt(sum((r - mean) ** 2 for r in rewards) / len(rewards))
    if std < 1e-12:
        return tuple(0.0 for _ in rewards)
    return tuple((r - mean) / std for r in rewards)


@dataclass(frozen=True)
class TrainingConfig:
    mode: Mode = "grpo"
    batch_size: int = 32
    group_size: int = 8
    learning_rate: float = 1.2
    epochs: int = 4
    clip_epsilon: float = 0.2
    kl_beta: float = 0.0

    def __post_init__(self) -> None:
        if (self.mode not in ("reinforce", "ppo", "grpo")
                or not _integer(self.batch_size, 2, 4096)
                or not _integer(self.group_size, 2, 256)
                or (self.mode == "grpo" and self.batch_size % self.group_size != 0)
                or not _real(self.learning_rate, 0.000001, 10)
                or not _integer(self.epochs, 1, 32)
                or not _real(self.clip_epsilon, 0.0, 0.999)
                or not _real(self.kl_beta, 0.0, 10.0)):
            raise RLContractError("invalid_training_config")
        if self.mode == "reinforce" and self.epochs != 1:
            raise RLContractError("reinforce_uses_one_on_policy_epoch")


@dataclass(frozen=True)
class TrainingReport:
    collected: int
    verified_successes: int
    mean_reward: float
    zero_variance_groups: int
    clipped_terms: int
    policy_gradient_terms: int
    mean_kl_to_reference: float


def _advantages(outcomes: list[VerifiedOutcome], config: TrainingConfig) -> tuple[list[float], int]:
    rewards = [o.reward for o in outcomes]
    if config.mode == "grpo":
        advantages: list[float] = []
        zero_groups = 0
        for start in range(0, len(rewards), config.group_size):
            values = tuple(rewards[start:start + config.group_size])
            group = group_relative_advantages(values)
            if all(a == 0 for a in group):
                zero_groups += 1
            advantages.extend(group)
        return advantages, zero_groups
    baseline = sum(rewards) / len(rewards)
    # Batch-mean baseline is a biased (by O(1/N)) variance-reduction heuristic.
    # For an unbiased REINFORCE estimate, use a leave-one-out or lagged baseline.
    return [r - baseline for r in rewards], 0


def train_round(
    policy: TabularPolicy,
    task: ArithmeticTask,
    rng: Random,
    config: TrainingConfig,
    reference: TabularPolicy | None = None,
) -> TrainingReport:
    """Collect on-policy batches, verify trajectories, then ascend clipped objective.

    The old distribution stays frozen across epochs, as PPO/GRPO require.
    Steps use trajectory-level terminal advantages (no critic or GAE).
    Reference KL is exact categorical KL for visited *public* observations.
    """
    reference = reference if reference is not None else TabularPolicy()
    old = policy.clone()
    trajectories = [rollout(old, task, rng) for _ in range(config.batch_size)]
    verified = [verify_trajectory(t) for t in trajectories]
    advantages, zero_groups = _advantages(verified, config)
    count = config.batch_size * task.horizon
    clipped_terms = 0
    gradient_terms = 0
    kl_sum = 0.0

    for _epoch in range(config.epochs):
        gradients: dict[Observation, dict[str, float]] = {}
        epoch_kl = 0.0
        for trajectory, advantage in zip(trajectories, advantages):
            for turn, record in enumerate(trajectory.steps):
                obs = (record.state, task.target, task.horizon - turn)
                previous = old.probabilities(task, obs)
                current = policy.probabilities(task, obs)
                action = record.action
                ratio = current[action] / previous[action]
                if config.mode == "reinforce":
                    coefficient = advantage
                else:
                    coefficient = surrogate_gradient_factor(
                        ratio, advantage, config.clip_epsilon,
                    )
                    if (advantage > 0 and ratio > 1 + config.clip_epsilon
                            or advantage < 0 and ratio < 1 - config.clip_epsilon):
                        clipped_terms += 1
                row = gradients.setdefault(obs, {a: 0.0 for a in current})
                if coefficient:
                    gradient_terms += 1
                for a, probability in current.items():
                    row[a] += coefficient * (float(a == action) - probability) / count

                ref = reference.probabilities(task, obs)
                if set(ref) != set(current):
                    raise RLContractError("reference_action_mask_mismatch")
                kl = sum(p * log(p / ref[a]) for a, p in current.items())
                epoch_kl += kl / count
                if config.kl_beta:
                    for a, p in current.items():
                        # Exact gradient of KL(pi_new || pi_ref) w.r.t. categorical logits.
                        row[a] -= config.kl_beta * p * (log(p / ref[a]) - kl) / count
        for obs, row in gradients.items():
            existing = policy.logits.setdefault(obs, {})
            for action, delta in row.items():
                existing[action] = max(-100.0, min(100.0, existing.get(action, 0.0)
                                                   + config.learning_rate * delta))
        kl_sum += epoch_kl
    return TrainingReport(
        collected=config.batch_size,
        verified_successes=sum(int(o.reward) for o in verified),
        mean_reward=sum(o.reward for o in verified) / len(verified),
        zero_variance_groups=zero_groups,
        clipped_terms=clipped_terms,
        policy_gradient_terms=gradient_terms,
        mean_kl_to_reference=kl_sum / config.epochs,
    )


def exact_success_probability(policy: TabularPolicy, task: ArithmeticTask) -> float:
    """Exact for this finite synthetic environment; not a real-agent metric."""
    def visit(state: int, remaining: int) -> float:
        if remaining == 0:
            return float(state == task.target)
        probs = policy.probabilities(task, (state, task.target, remaining))
        return sum(p * visit(trusted_transition(task, state, action), remaining - 1)
                   for action, p in probs.items())
    return visit(task.initial, task.horizon)


def evaluate_rollouts(
    policy: TabularPolicy, task: ArithmeticTask, seed: int, episodes: int = 1000
) -> tuple[int, int]:
    if not _integer(seed, 0, 2**63 - 1) or not _integer(episodes, 1, 20_000):
        raise RLContractError("invalid_evaluation_arguments")
    rng = Random(seed)
    passed = sum(int(verify_trajectory(rollout(policy, task, rng)).reward)
                 for _ in range(episodes))
    return passed, episodes


def train_policy(
    task: ArithmeticTask,
    config: TrainingConfig,
    rounds: int,
    seed: int = 31,
) -> tuple[TabularPolicy, list[TrainingReport]]:
    if not _integer(rounds, 1, 300) or not _integer(seed, 0, 2**63 - 1):
        raise RLContractError("invalid_training_arguments")
    rng = Random(seed)
    policy = TabularPolicy()
    reference = policy.clone()  # fixed throughout rounds
    history = []
    for _ in range(rounds):
        history.append(train_round(policy, task, rng, config, reference))
    return policy, history
