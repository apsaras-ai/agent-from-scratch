"""Chapter 23: deterministic performance / cost optimizer (offline teaching lab).

This module does NOT invoke providers or assume that fewer tokens improve
quality. Rates and timing estimates are explicit fixtures, not market prices.
Only a trusted independent verifier may mark a candidate acceptable.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Callable, Mapping
import json
import math


class OptimizationError(ValueError): pass


def money(value: str | Decimal) -> Decimal:
    try:
        d = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise OptimizationError("invalid_money") from exc
    if not d.is_finite() or d < 0:
        raise OptimizationError("invalid_nonnegative_money")
    return d


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in (
            self.input_tokens, self.output_tokens, self.cached_input_tokens
        )) or self.cached_input_tokens > self.input_tokens:
            raise OptimizationError("invalid_token_usage")


@dataclass(frozen=True)
class RateCard:
    input_per_million: Decimal
    output_per_million: Decimal
    cached_per_million: Decimal
    def __post_init__(self):
        for field in ("input_per_million","output_per_million","cached_per_million"):
            object.__setattr__(self, field, money(getattr(self, field)))

    def price(self, usage: TokenUsage) -> Decimal:
        return (
            Decimal(usage.input_tokens - usage.cached_input_tokens) *
            self.input_per_million +
            Decimal(usage.cached_input_tokens) * self.cached_per_million +
            Decimal(usage.output_tokens) * self.output_per_million
        ) / Decimal(1_000_000)


@dataclass(frozen=True)
class Operation:
    operation_id: str
    kind: str
    duration_ms: float
    dependencies: tuple[str, ...] = ()
    usage: TokenUsage | None = None
    rate: RateCard | None = None
    tool_cost_usd: Decimal = Decimal("0")
    def __post_init__(self):
        if not self.operation_id or self.kind not in ("model","tool","verify"):
            raise OptimizationError("invalid_operation")
        if not isinstance(self.duration_ms, (float,int)) or isinstance(self.duration_ms,bool) or not math.isfinite(self.duration_ms) or self.duration_ms < 0:
            raise OptimizationError("invalid_duration")
        if (self.usage is None) != (self.rate is None):
            raise OptimizationError("usage_requires_explicit_rate")
        if self.kind != "model" and self.usage is not None:
            raise OptimizationError("token_usage_only_for_model")
        object.__setattr__(self, "tool_cost_usd", money(self.tool_cost_usd))


@dataclass(frozen=True)
class Budget:
    max_cost_usd: Decimal
    max_duration_ms: float
    max_model_calls: int
    def __post_init__(self):
        object.__setattr__(self, "max_cost_usd", money(self.max_cost_usd))
        if not math.isfinite(self.max_duration_ms) or self.max_duration_ms <= 0 or type(self.max_model_calls) is not int or self.max_model_calls < 0:
            raise OptimizationError("invalid_budget")


@dataclass(frozen=True)
class Evaluation:
    candidate: str
    total_cost_usd: Decimal
    critical_path_ms: float
    serial_duration_ms: float
    model_calls: int
    verified: bool
    evidence: str
    budget_ok: bool
    accepted: bool


def critical_path(ops: tuple[Operation,...]) -> tuple[float, float]:
    ids = {o.operation_id for o in ops}
    if len(ids) != len(ops):
        raise OptimizationError("duplicate_operation")
    by_id = {o.operation_id:o for o in ops}
    elapsed: dict[str,float] = {}
    visiting: set[str] = set()
    def finish(key: str) -> float:
        if key in elapsed:
            return elapsed[key]
        if key in visiting:
            raise OptimizationError("dependency_cycle")
        visiting.add(key)
        op = by_id[key]
        if key in op.dependencies or any(k not in ids for k in op.dependencies):
            raise OptimizationError("invalid_dependency")
        elapsed[key] = op.duration_ms + max((finish(dep) for dep in op.dependencies),default=0)
        visiting.remove(key)
        return elapsed[key]
    for key in ids:
        finish(key)
    return max(elapsed.values(),default=0),sum(o.duration_ms for o in ops)


class CostOptimizer:
    def __init__(self, verifier: Callable[[str], tuple[bool,str]]):
        if not callable(verifier):
            raise OptimizationError("host_verifier_required")
        self.verifier = verifier

    def evaluate(self, candidate: str, ops: tuple[Operation,...],
                 budget: Budget) -> Evaluation:
        duration, serial = critical_path(ops)
        cost = sum((op.tool_cost_usd +
                    (op.rate.price(op.usage) if op.rate else Decimal("0"))
                    for op in ops), Decimal("0"))
        calls = sum(op.kind == "model" for op in ops)
        is_verified, evidence = self.verifier(candidate)
        if type(is_verified) is not bool or not isinstance(evidence,str):
            raise OptimizationError("invalid_host_verification")
        fits = (cost <= budget.max_cost_usd and
                duration <= budget.max_duration_ms and
                calls <= budget.max_model_calls)
        return Evaluation(candidate,cost,duration,serial,calls,is_verified,
                          evidence,fits,fits and is_verified)

    def choose(self, options: Mapping[str,tuple[Operation,...]],
               budget: Budget) -> tuple[Evaluation,tuple[Evaluation,...]]:
        if not options:
            raise OptimizationError("at_least_one_candidate")
        results = tuple(self.evaluate(name,ops,budget)
                        for name,ops in sorted(options.items()))
        eligible = [r for r in results if r.accepted]
        if not eligible:
            raise OptimizationError("no_verified_candidate_within_budget")
        chosen = min(eligible,key=lambda r:(r.total_cost_usd,r.critical_path_ms,r.candidate))
        return chosen,results


def cache_key(*, tenant: str, model: str, prompt: str,
              policy_version: str, tool_version: str) -> str:
    """Tenant- and version-scoped logical key; NOT a secure cache store."""
    if not all(isinstance(v,str) and v for v in (
        tenant,model,prompt,policy_version,tool_version
    )):
        raise OptimizationError("invalid_cache_scope")
    payload = json.dumps([tenant,model,prompt,policy_version,tool_version],
                         separators=(",",":"),ensure_ascii=False)
    return sha256(payload.encode()).hexdigest()


class ScopedFixtureCache:
    """Small explicit test fixture. Cached answers are untrusted until verified."""
    def __init__(self):
        self._items: dict[str,str] = {}
    def put(self, key: str, value: str):
        if not isinstance(key,str) or not isinstance(value,str):
            raise OptimizationError("invalid_cache_entry")
        self._items[key]=value
    def get(self,key: str) -> str | None:
        return self._items.get(key)
