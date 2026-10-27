"""Chapter 28: Evaluation-Driven Development (EDD) offline workflow.

A trusted evaluator compares complete, paired Chapter-26 benchmark results
against an explicit, pre-registered hypothesis and an ablation. Hard safety
constraints take precedence over quality and cost. This tool does NOT
automatically improve models, edit prompts, deploy code, query users or
promote production traffic.

All quality/cost numbers in the bundled demo come from synthetic fixtures.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import math
import re

from .agent_evaluation import EvalError, EvalSuite, compare_paired
from .benchmark_framework import (
    BenchmarkProtocol, BenchmarkResult, CandidateConfig, RegressionGate,
    gate_candidate, run_benchmark,
)

_ID=re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_FIXES=frozenset({"prompt","context","model","tool","planning","verification"})


@dataclass(frozen=True)
class ChangeHypothesis:
    hypothesis_id: str
    suspected_failure: str
    intervention: str
    candidate_config: CandidateConfig
    minimum_improvements: int = 1
    max_regressions: int = 0
    max_cost_ratio: Decimal = Decimal("1.2")

    def __post_init__(self):
        if (not _ID.fullmatch(self.hypothesis_id)
                or not _ID.fullmatch(self.suspected_failure)
                or self.intervention not in _FIXES
                or not isinstance(self.candidate_config,CandidateConfig)
                or type(self.minimum_improvements) is not int
                or self.minimum_improvements < 0
                or type(self.max_regressions) is not int
                or self.max_regressions < 0):
            raise EvalError("invalid_edd_hypothesis")
        try:
            ratio=Decimal(self.max_cost_ratio)
        except Exception as exc:
            raise EvalError("invalid_cost_ratio") from exc
        if not ratio.is_finite() or ratio < 1:
            raise EvalError("invalid_cost_ratio")
        object.__setattr__(self,"max_cost_ratio",ratio)


@dataclass(frozen=True)
class FailureTriage:
    failure_code: str
    count: int
    # Safe metadata only: case IDs are synthetic in this teaching suite.


def triage(result:BenchmarkResult)->tuple[FailureTriage,...]:
    if not isinstance(result,BenchmarkResult):
        raise EvalError("benchmark_result_required")
    return tuple(FailureTriage(code,num) for code,num in result.failure_counts)


@dataclass(frozen=True)
class EDDDecision:
    action: str  # adopt / reject / inconclusive
    reason: str
    improvements: int
    regressions: int
    baseline_rate: float
    candidate_rate: float
    ablated_rate: float | None
    cost_ratio: Decimal | None
    shared_protocol_digest: str
    baseline_config_digest: str
    candidate_config_digest: str
    ablated_config_digest: str | None


def _cost_ratio(baseline:BenchmarkResult,candidate:BenchmarkResult)->Decimal | None:
    a=baseline.report.cost_per_verified_task_usd
    b=candidate.report.cost_per_verified_task_usd
    if a is None or b is None or a==0:
        return None
    return b/a


def decide_edd(
    baseline:BenchmarkResult,
    candidate:BenchmarkResult,
    hypothesis:ChangeHypothesis,
    *,
    ablated:BenchmarkResult | None,
) -> EDDDecision:
    """Fail closed on missing ablation, unsafe trials or unobserved cost.

    This function cannot infer causality. The ablated runner/config must
    represent the same candidate minus ONE registered intervention. The
    distinction is supplied by a trusted human-owned experiment protocol.
    """
    if not isinstance(hypothesis,ChangeHypothesis):
        raise EvalError("hypothesis_required")
    if baseline.protocol_digest!=candidate.protocol_digest:
        raise EvalError("shared_protocol_mismatch")
    if candidate.candidate_config_digest!=hypothesis.candidate_config.digest():
        raise EvalError("candidate_config_mismatch")
    pair=compare_paired(baseline.report,candidate.report)
    ratio=_cost_ratio(baseline,candidate)
    ablated_rate=None
    if ablated is not None:
        if ablated.protocol_digest!=baseline.protocol_digest:
            raise EvalError("ablation_protocol_mismatch")
        compare_paired(ablated.report,candidate.report)
        ablated_rate=ablated.report.rate

    def result(action,reason):
        return EDDDecision(
            action,reason,pair.improved,pair.regressed,
            baseline.report.rate,candidate.report.rate,ablated_rate,ratio,
            baseline.protocol_digest,baseline.candidate_config_digest,
            candidate.candidate_config_digest,
            ablated.candidate_config_digest if ablated else None,
        )
    if any(not x.policy_ok for x in candidate.report.trials):
        return result("reject","candidate_policy_violation")
    if pair.regressed>hypothesis.max_regressions:
        return result("reject","task_regression")
    if candidate.report.rate<baseline.report.rate:
        return result("reject","quality_regression")
    if ratio is None:
        return result("inconclusive","missing_cost_evidence")
    if ratio>hypothesis.max_cost_ratio:
        return result("reject","cost_budget_exceeded")
    if ablated is None:
        return result("inconclusive","missing_ablation")
    if any(not x.policy_ok for x in ablated.report.trials):
        return result("inconclusive","unsafe_ablation")
    if pair.improved<hypothesis.minimum_improvements:
        return result("inconclusive","insufficient_improvement")
    if candidate.report.rate<=ablated.report.rate:
        return result("inconclusive","ablation_does_not_support_effect")
    return result("adopt","all_pre_registered_gates_satisfied")


@dataclass(frozen=True)
class RegressionCaseProposal:
    """Only a human-review proposal; never inserts a real user record."""
    failure_code: str
    category: str
    source_digest: str
    proposed_split: str = "regression"

    def __post_init__(self):
        if (not _ID.fullmatch(self.failure_code)
                or self.category not in {"coding","browser","research","security","tool"}
                or not re.fullmatch(r"[0-9a-f]{64}",self.source_digest)
                or self.proposed_split!="regression"):
            raise EvalError("invalid_regression_proposal")


def propose_regression_case(
    failure_code:str,category:str,source_public_id:str,
)->RegressionCaseProposal:
    """Hash a synthetic identifier; DO NOT store private text or answers.

    Hashes alone are not anonymization, especially with low-entropy IDs.
    Production requires consent, redaction, ACL, provenance and human review.
    """
    if not isinstance(source_public_id,str) or not _ID.fullmatch(source_public_id):
        raise EvalError("untrusted_source_identifier")
    return RegressionCaseProposal(
        failure_code,category,sha256(source_public_id.encode()).hexdigest()
    )
