"""Chapter 26: offline, reproducible multi-domain benchmark protocol.

The benchmark orchestrates Chapter 25's independent outcome grader and tracks
immutable dataset/config fingerprints. Scores come from real synthetic state
transitions, never model self-reports. No benchmark named here (SWE-bench,
WebArena, OSWorld, GAIA, BrowseComp) is executed or reproduced.

Not a secure sandbox. Fake model and environment run in the same Python
process. Production needs isolated runners, hidden graders and authenticated
telemetry from tools and providers.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from typing import Mapping

from .agent_evaluation import (
    EvalError, EvalHarness, EvalReport, EvalRunner, EvalSuite,
    compare_paired,
)

DOMAINS = frozenset({"coding", "browser", "research", "security", "tool"})


@dataclass(frozen=True)
class BenchmarkProtocol:
    benchmark_id: str
    benchmark_version: str
    model_version: str
    prompt_version: str
    tool_version: str
    environment_version: str
    verifier_version: str
    isolation_profile: str
    seed: int
    split: str
    trials_per_case: int

    def __post_init__(self) -> None:
        parts = (
            self.benchmark_id, self.benchmark_version,
            self.model_version, self.prompt_version,
            self.tool_version, self.environment_version,
            self.verifier_version, self.isolation_profile,
        )
        if any(not isinstance(x, str) or not x or len(x) > 128 for x in parts):
            raise EvalError("invalid_benchmark_protocol")
        if type(self.seed) is not int or not 0 <= self.seed < 2**64:
            raise EvalError("invalid_benchmark_seed")
        if self.split not in {"dev", "regression", "heldout"}:
            raise EvalError("invalid_benchmark_split")
        if type(self.trials_per_case) is not int or not 1 <= self.trials_per_case <= 30:
            raise EvalError("invalid_benchmark_trial_count")

    def digest(self) -> str:
        data = json.dumps(self.__dict__, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
        return sha256(data.encode()).hexdigest()


@dataclass(frozen=True)
class DomainScore:
    domain: str
    trials: int
    successes: int
    policy_violations: int
    outcome_failures: int
    execution_failures: int

    @property
    def success_rate(self) -> float:
        return self.successes / self.trials


@dataclass(frozen=True)
class BenchmarkResult:
    protocol: BenchmarkProtocol
    protocol_digest: str
    report: EvalReport
    domains: tuple[DomainScore, ...]
    failure_counts: tuple[tuple[str, int], ...]

    @property
    def score(self) -> float:
        """Macro-average domain success, not a universal benchmark metric."""
        return sum(x.success_rate for x in self.domains) / len(self.domains)

    def safe_summary(self) -> dict:
        """No raw task text, hidden answers, tool args or model final text."""
        return {
            "candidate": self.report.candidate,
            "suite_id": self.report.suite_id,
            "suite_version": self.report.suite_version,
            "suite_digest": self.report.suite_digest,
            "protocol_digest": self.protocol_digest,
            "split": self.protocol.split,
            "trials": len(self.report.trials),
            "successes": self.report.successes,
            "micro_success_rate": self.report.rate,
            "macro_domain_success_rate": self.score,
            "domains": [d.__dict__ | {"success_rate": d.success_rate}
                        for d in self.domains],
            "failure_counts": dict(self.failure_counts),
            "total_cost_usd": (
                str(self.report.total_cost_usd)
                if self.report.total_cost_usd is not None else None
            ),
            "cost_per_verified_task_usd": (
                str(self.report.cost_per_verified_task_usd)
                if self.report.cost_per_verified_task_usd is not None else None
            ),
        }


def run_benchmark(
    suite: EvalSuite,
    protocol: BenchmarkProtocol,
    candidate: str,
    runner: EvalRunner,
) -> BenchmarkResult:
    if suite.suite_id != protocol.benchmark_id or suite.version != protocol.benchmark_version:
        raise EvalError("benchmark_suite_version_mismatch")
    harness = EvalHarness(suite)
    report = harness.evaluate(
        candidate, runner, split=protocol.split,
        trials_per_case=protocol.trials_per_case,
    )
    domains = []
    counts: dict[str, int] = {}
    for domain in sorted({t.category for t in report.trials}):
        rows = [t for t in report.trials if t.category == domain]
        if domain not in DOMAINS:
            raise EvalError("unsupported_benchmark_domain")
        domains.append(DomainScore(
            domain, len(rows), sum(t.passed for t in rows),
            sum(not t.policy_ok for t in rows),
            sum(not t.outcome_ok for t in rows),
            sum(t.failure_code == "runner_failure" for t in rows),
        ))
        for t in rows:
            if not t.passed:
                counts[t.failure_code] = counts.get(t.failure_code, 0) + 1
    if not domains:
        raise EvalError("empty_benchmark")
    return BenchmarkResult(protocol, protocol.digest(), report,
                           tuple(domains), tuple(sorted(counts.items())))


@dataclass(frozen=True)
class RegressionGate:
    """Protect domain regressions; never substitute mean score for safety."""
    max_allowed_regressions: int = 0
    minimum_micro_rate: float = 0.0
    require_cost_observed: bool = False

    def __post_init__(self) -> None:
        if type(self.max_allowed_regressions) is not int or self.max_allowed_regressions < 0:
            raise EvalError("invalid_regression_limit")
        if not isinstance(self.minimum_micro_rate, (int, float)) or not 0 <= self.minimum_micro_rate <= 1:
            raise EvalError("invalid_minimum_rate")
        if type(self.require_cost_observed) is not bool:
            raise EvalError("invalid_cost_requirement")


@dataclass(frozen=True)
class GateDecision:
    action: str  # approve / block / insufficient_evidence
    reason: str
    paired_improvements: int
    paired_regressions: int


def gate_candidate(
    baseline: BenchmarkResult,
    candidate: BenchmarkResult,
    policy: RegressionGate,
) -> GateDecision:
    if baseline.protocol_digest != candidate.protocol_digest:
        raise EvalError("benchmark_protocol_mismatch")
    paired = compare_paired(baseline.report, candidate.report)
    if policy.require_cost_observed and (
        baseline.report.total_cost_usd is None
        or candidate.report.total_cost_usd is None
    ):
        return GateDecision("insufficient_evidence", "missing_cost",
                            paired.improved, paired.regressed)
    # A policy violation is never justified by higher average success.
    if any(not t.policy_ok for t in candidate.report.trials):
        return GateDecision("block", "unsafe_tool_attempt",
                            paired.improved, paired.regressed)
    if paired.regressed > policy.max_allowed_regressions:
        return GateDecision("block", "task_regression",
                            paired.improved, paired.regressed)
    if candidate.report.rate < policy.minimum_micro_rate:
        return GateDecision("block", "success_rate_below_floor",
                            paired.improved, paired.regressed)
    return GateDecision("approve", "regression_gates_satisfied",
                        paired.improved, paired.regressed)


def assert_reproducible(first: BenchmarkResult, repeat: BenchmarkResult) -> None:
    """Only proves equality of repeated local fixture outcomes/measurements."""
    if first.protocol_digest != repeat.protocol_digest:
        raise EvalError("benchmark_protocol_mismatch")
    if first.report.suite_digest != repeat.report.suite_digest:
        raise EvalError("benchmark_dataset_mismatch")
    if first.safe_summary() != repeat.safe_summary():
        raise EvalError("benchmark_nondeterministic_result")
