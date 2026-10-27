"""Ch30: trusted verification, reward-model audit and offline feedback learning.

This is a SMALL, SINGLE-PROCESS, SYNTHETIC teaching lab. It neither trains
an LLM nor executes untrusted code. A toy arithmetic DSL from chapter 29 is
replayed by host-owned checks; a biased proxy reward illustrates Goodhart-like
selection failures. Tabular feedback fitting learns from independently checked
one-step TRAIN trajectories, never heldout examples.

IMPORTANT: trusted labels, split access, authorizations and metrics depend on
the *caller*. Dataclass freezing and Python interfaces cannot stop hostile
same-process code or authenticate a remote verifier. Use separate IAM/sandbox
boundaries in production.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Callable, Sequence

from .inference_search import SynthesisTask, _transition


class FeedbackError(ValueError):
    """Safe validation error; never interpolate an untrusted task/payload."""


_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_OPERATORS = ("add1", "add3", "mul2", "sub1")


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Verification:
    task_id: str
    path_digest: str
    process_ok: bool
    outcome_ok: bool
    first_error_step: int | None  # 1-indexed; no later steps executed
    step_labels: tuple[bool, ...]  # only verified, executed prefixes
    final_state: int | None
    reason: str


class TrustedArithmeticVerifier:
    """Host-controlled, deterministic, cost-free symbolic verifier.

    "Process" means legal and bounded transitions, not full mathematical
    proof of free-form LLM reasoning. "Outcome" means the final task target
    is reached, not that textual explanation is persuasive.
    """

    def verify(
        self, task: SynthesisTask, actions: tuple[str, ...], *,
        claimed_final: int | None = None,
    ) -> Verification:
        if not isinstance(task, SynthesisTask):
            raise FeedbackError("trusted_task_required")
        if (not isinstance(actions, tuple) or len(actions) > 32
                or any(not isinstance(a, str) or len(a) > 80 for a in actions)):
            raise FeedbackError("invalid_action_path")
        if claimed_final is not None and type(claimed_final) is not int:
            raise FeedbackError("invalid_claimed_state")
        digest = _digest((task.task_id, actions))
        state = task.initial
        passed: list[bool] = []
        for index, action in enumerate(actions, start=1):
            if index > task.max_steps or action not in task.allowed_ops:
                return Verification(task.task_id, digest, False, False,
                                    index, tuple(passed + [False]), state,
                                    "invalid_or_over_budget_step")
            next_state = _transition(state, action, task.max_abs_value)
            if next_state is None:
                return Verification(task.task_id, digest, False, False,
                                    index, tuple(passed + [False]), state,
                                    "invalid_or_out_of_range_transition")
            passed.append(True)
            state = next_state
        outcome = (state == task.target and
                   (claimed_final is None or claimed_final == state))
        return Verification(
            task.task_id, digest, True, outcome, None, tuple(passed),
            state, "verified" if outcome else
            "claimed_state_mismatch" if state == task.target else "target_not_reached",
        )


@dataclass(frozen=True)
class RewardAudit:
    total: int
    positives: int
    tp: int
    fp: int
    tn: int
    fn: int
    brier: float
    expected_calibration_error: float

    @property
    def false_positive_rate(self) -> float | None:
        denominator = self.fp + self.tn
        return self.fp / denominator if denominator else None

    @property
    def precision(self) -> float | None:
        denominator = self.tp + self.fp
        return self.tp / denominator if denominator else None


def _probability(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise FeedbackError("invalid_reward_probability")
    return float(value)


def audit_reward_model(
    task_paths: Sequence[tuple[SynthesisTask, tuple[str, ...], float]],
    *, threshold: float = 0.5, bins: int = 5,
    verifier: TrustedArithmeticVerifier | None = None,
) -> RewardAudit:
    """Brier/ECE and confusion matrix using INDEPENDENT host truth labels.

    ECE is a descriptive bin estimator, not a confidence interval. Tiny
    samples or a shifted distribution cannot establish good calibration.
    """
    if not task_paths or len(task_paths) > 10_000 or type(bins) is not int or not 2 <= bins <= 50:
        raise FeedbackError("invalid_reward_audit_dataset")
    threshold = _probability(threshold)
    oracle = verifier or TrustedArithmeticVerifier()
    rows: list[tuple[float, bool]] = []
    for task, actions, score in task_paths:
        p = _probability(score)
        label = oracle.verify(task, actions).outcome_ok
        rows.append((p, label))
    tp = sum(p >= threshold and label for p, label in rows)
    fp = sum(p >= threshold and not label for p, label in rows)
    tn = sum(p < threshold and not label for p, label in rows)
    fn = sum(p < threshold and label for p, label in rows)
    brier = sum((p - int(label)) ** 2 for p, label in rows) / len(rows)
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for p, label in rows:
        buckets[min(bins - 1, int(p * bins))].append((p, label))
    ece = 0.0
    for bucket in buckets:
        if bucket:
            confidence = sum(p for p, _ in bucket) / len(bucket)
            accuracy = sum(label for _, label in bucket) / len(bucket)
            ece += len(bucket) / len(rows) * abs(confidence - accuracy)
    return RewardAudit(len(rows), sum(label for _, label in rows),
                       tp, fp, tn, fn, brier, ece)


def biased_length_reward(task: SynthesisTask, actions: tuple[str, ...]) -> float:
    """Deliberately BAD reward: favors longer paths, ignores task outcome."""
    return min(1.0, len(actions) / task.max_steps)


@dataclass(frozen=True)
class RewardHackExample:
    proxy_selected: tuple[str, ...]
    proxy_verified: bool
    verified_selected: tuple[str, ...] | None
    verified_success: bool
    tested_candidates: int


def compare_proxy_and_host(
    task: SynthesisTask, candidates: tuple[tuple[str, ...], ...],
    *, proxy: Callable[[SynthesisTask, tuple[str, ...]], float] = biased_length_reward,
    verifier: TrustedArithmeticVerifier | None = None,
) -> RewardHackExample:
    if not isinstance(task, SynthesisTask) or not isinstance(candidates, tuple) or not 1 <= len(candidates) <= 100:
        raise FeedbackError("invalid_selection_candidates")
    oracle = verifier or TrustedArithmeticVerifier()
    scores = [_probability(proxy(task, candidate)) for candidate in candidates]
    best_idx = max(range(len(candidates)), key=lambda i: (scores[i], -i))
    preferred = candidates[best_idx]
    preferred_good = oracle.verify(task, preferred).outcome_ok
    # Correct reference behavior: inspect ALL candidates with host checks.
    # This is expensive in real systems; every call should be budgeted.
    chosen: tuple[str, ...] | None = None
    for candidate in candidates:
        if oracle.verify(task, candidate).outcome_ok:
            chosen = candidate
            break
    return RewardHackExample(
        preferred, preferred_good, chosen, chosen is not None, len(candidates)
    )


@dataclass(frozen=True)
class FeedbackCase:
    task: SynthesisTask
    split: str

    def __post_init__(self) -> None:
        if not isinstance(self.task, SynthesisTask) or self.task.max_steps != 1:
            raise FeedbackError("feedback_case_must_be_one_step_task")
        if self.split not in ("train", "heldout"):
            raise FeedbackError("invalid_feedback_split")


@dataclass(frozen=True)
class FeedbackSuite:
    suite_id: str
    version: str
    cases: tuple[FeedbackCase, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.suite_id, str) or not _NAME.fullmatch(self.suite_id)
                or not isinstance(self.version, str) or not _NAME.fullmatch(self.version)
                or not isinstance(self.cases, tuple) or not self.cases
                or any(not isinstance(x, FeedbackCase) for x in self.cases)):
            raise FeedbackError("invalid_feedback_suite")
        ids = [c.task.task_id for c in self.cases]
        if len(set(ids)) != len(ids):
            raise FeedbackError("duplicate_feedback_task_id")
        if not {"train", "heldout"}.issubset({c.split for c in self.cases}):
            raise FeedbackError("missing_train_or_heldout_split")
        # Exact same state/target pair across splits is data contamination.
        signatures: dict[tuple[int, int, tuple[str, ...]], str] = {}
        for case in self.cases:
            signature = (case.task.initial, case.task.target, case.task.allowed_ops)
            if signature in signatures and signatures[signature] != case.split:
                raise FeedbackError("cross_split_duplicate_problem")
            signatures[signature] = case.split

    def for_split(self, split: str) -> tuple[SynthesisTask, ...]:
        if split not in ("train", "heldout"):
            raise FeedbackError("invalid_feedback_split")
        return tuple(c.task for c in self.cases if c.split == split)

    def digest(self) -> str:
        records = [
            (c.split, c.task.task_id, c.task.initial, c.task.target,
             c.task.max_steps, c.task.allowed_ops, c.task.max_abs_value)
            for c in sorted(self.cases, key=lambda x: x.task.task_id)
        ]
        return _digest((self.suite_id, self.version, records))


def load_feedback_suite(path: str | Path) -> FeedbackSuite:
    """Trusted versioned local fixture; no remote fetch or unsafe deserialization."""
    source = Path(path)
    if (source.is_symlink() or not source.is_file() or
            source.stat().st_size > 100_000):
        raise FeedbackError("invalid_feedback_dataset_file")

    def unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        data = {}
        for key, val in pairs:
            if key in data:
                raise FeedbackError("duplicate_json_key")
            data[key] = val
        return data

    try:
        data = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=unique_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(
                FeedbackError("nonfinite_feedback_json")
            ),
        )
    except (UnicodeError, ValueError, OSError, RecursionError) as exc:
        raise FeedbackError("invalid_feedback_json") from exc
    if not isinstance(data, dict) or set(data) != {"suite_id", "version", "cases"}:
        raise FeedbackError("invalid_feedback_dataset_schema")
    if not isinstance(data["cases"], list) or not 1 <= len(data["cases"]) <= 200:
        raise FeedbackError("invalid_feedback_cases")
    cases = []
    for row in data["cases"]:
        if not isinstance(row, dict) or set(row) != {
            "task_id", "initial", "target", "split", "allowed_ops",
            "max_steps", "max_abs_value"
        }:
            raise FeedbackError("invalid_feedback_case_shape")
        if not isinstance(row["allowed_ops"], list):
            raise FeedbackError("invalid_feedback_operator_list")
        cases.append(FeedbackCase(SynthesisTask(
            task_id=row["task_id"],
            initial=row["initial"],
            target=row["target"],
            max_steps=row["max_steps"],
            allowed_ops=tuple(row["allowed_ops"]),
            max_abs_value=row["max_abs_value"],
        ), row["split"]))
    return FeedbackSuite(data["suite_id"], data["version"], tuple(cases))


@dataclass(frozen=True)
class FeedbackRecord:
    task_id: str
    action: str
    label: bool
    process_ok: bool
    path_digest: str
    suite_digest: str
    source: str

    def __post_init__(self) -> None:
        if (not isinstance(self.task_id, str) or not _NAME.fullmatch(self.task_id)
                or self.action not in _OPERATORS
                or type(self.label) is not bool
                or type(self.process_ok) is not bool
                or not re.fullmatch("[0-9a-f]{64}", self.path_digest)
                or not re.fullmatch("[0-9a-f]{64}", self.suite_digest)
                or self.source != "independent_host_verifier"):
            raise FeedbackError("invalid_feedback_record")


def collect_train_feedback(
    suite: FeedbackSuite, *,
    verifier: TrustedArithmeticVerifier | None = None,
) -> tuple[FeedbackRecord, ...]:
    """Return explicitly sourced, independently checked TRAIN-only labels."""
    if not isinstance(suite, FeedbackSuite):
        raise FeedbackError("feedback_suite_required")
    oracle = verifier or TrustedArithmeticVerifier()
    rows = []
    for task in suite.for_split("train"):
        for action in task.allowed_ops:
            evidence = oracle.verify(task, (action,))
            rows.append(FeedbackRecord(
                task.task_id, action, evidence.outcome_ok, evidence.process_ok,
                evidence.path_digest, suite.digest(), "independent_host_verifier"
            ))
    return tuple(rows)


@dataclass(frozen=True)
class TabularFeedbackPolicy:
    """Empirical Beta(1,1)-smoothed success rate per (target-initial, op).

    Policy learns from SUPERVISED trusted binary labels. It does not update
    any language model weights, calculate policy gradients or perform RL.
    """
    suite_digest: str
    counts: tuple[tuple[int, str, int, int], ...]  # delta, op, successes, trials

    def choose(self, task: SynthesisTask) -> str:
        if not isinstance(task, SynthesisTask) or task.max_steps != 1:
            raise FeedbackError("one_step_task_required")
        lookup = {(d, op): (wins, total) for d, op, wins, total in self.counts}
        delta = task.target - task.initial
        legal = [
            op for op in task.allowed_ops
            if _transition(task.initial, op, task.max_abs_value) is not None
        ]
        if not legal:
            raise FeedbackError("no_legal_policy_actions")
        # Unseen (delta, op) => prior 0.5. Ties follow host action ordering.
        return max(legal, key=lambda op: (
            ((lookup.get((delta, op), (0, 0))[0] + 1) /
             (lookup.get((delta, op), (0, 0))[1] + 2)),
            -legal.index(op),
        ))


def fit_tabular_feedback_policy(
    suite: FeedbackSuite,
    feedback: tuple[FeedbackRecord, ...],
    *,
    verifier: TrustedArithmeticVerifier | None = None,
) -> TabularFeedbackPolicy:
    """Fail closed on unverified labels, tampering and heldout contamination."""
    if not isinstance(suite, FeedbackSuite) or not isinstance(feedback, tuple) or not feedback:
        raise FeedbackError("nonempty_train_feedback_required")
    oracle = verifier or TrustedArithmeticVerifier()
    train = {t.task_id: t for t in suite.for_split("train")}
    seen: set[tuple[str, str]] = set()
    counts: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0])
    for item in feedback:
        if not isinstance(item, FeedbackRecord):
            raise FeedbackError("trusted_feedback_record_required")
        task = train.get(item.task_id)
        if task is None or item.suite_digest != suite.digest():
            raise FeedbackError("heldout_or_mismatched_feedback")
        if item.action not in task.allowed_ops:
            raise FeedbackError("unapproved_action_feedback")
        key = (item.task_id, item.action)
        if key in seen:
            raise FeedbackError("duplicate_feedback_evidence")
        seen.add(key)
        independent = oracle.verify(task, (item.action,))
        if (independent.outcome_ok != item.label or
                independent.process_ok != item.process_ok or
                independent.path_digest != item.path_digest):
            raise FeedbackError("feedback_label_tampering")
        row = counts[(task.target - task.initial, item.action)]
        row[0] += int(item.label)
        row[1] += 1
    # This teaching dataset enumerates every permitted action for each TRAIN
    # state. Selective positive-only feedback would bias the estimates.
    expected = {
        (task.task_id, op)
        for task in train.values() for op in task.allowed_ops
    }
    if seen != expected:
        raise FeedbackError("incomplete_training_action_coverage")
    encoded = tuple(sorted(
        (delta, op, wins, total)
        for (delta, op), (wins, total) in counts.items()
    ))
    return TabularFeedbackPolicy(suite.digest(), encoded)


@dataclass(frozen=True)
class PolicyEvaluation:
    trials: int
    verified: int
    illegal_actions: int

    @property
    def success_rate(self) -> float:
        return self.verified / self.trials


def evaluate_one_step_policy(
    tasks: tuple[SynthesisTask, ...], *,
    policy: TabularFeedbackPolicy | None = None,
    verifier: TrustedArithmeticVerifier | None = None,
) -> PolicyEvaluation:
    if not isinstance(tasks, tuple) or not tasks or any(
        not isinstance(task, SynthesisTask) or task.max_steps != 1 for task in tasks
    ):
        raise FeedbackError("nonempty_one_step_evaluation_required")
    oracle = verifier or TrustedArithmeticVerifier()
    verified = 0
    illegal = 0
    for task in tasks:
        action = policy.choose(task) if policy is not None else task.allowed_ops[0]
        evidence = oracle.verify(task, (action,))
        verified += int(evidence.outcome_ok)
        illegal += int(not evidence.process_ok)
    return PolicyEvaluation(len(tasks), verified, illegal)

def evaluate_heldout_policy(
    suite: FeedbackSuite, *,
    policy: TabularFeedbackPolicy | None = None,
    verifier: TrustedArithmeticVerifier | None = None,
) -> PolicyEvaluation:
    """Use only new Case IDs and a policy fitted to this exact suite."""
    if not isinstance(suite, FeedbackSuite):
        raise FeedbackError("feedback_suite_required")
    if policy is not None and (
        not isinstance(policy, TabularFeedbackPolicy)
        or policy.suite_digest != suite.digest()
    ):
        raise FeedbackError("heldout_policy_dataset_mismatch")
    return evaluate_one_step_policy(
        suite.for_split("heldout"), policy=policy, verifier=verifier
    )
