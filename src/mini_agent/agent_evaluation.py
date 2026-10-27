"""Chapter 25: deterministic, offline Agent Evaluation Framework.

TRUST BOUNDARY: Evaluators own hidden ground truth and inspect a controlled
environment's REAL state, not the agent's final "done" text. PublicTask does
not contain expected_state. A trusted runner adapter supplies usage/latency
measurements and never derives success from model text.

This is a SAME-PROCESS fixture, NOT a sandbox or authenticated eval service.
A malicious Python agent running in this process can introspect objects or
forge host telemetry; only isolated workers, trusted tool proxy, IAM and
independent external business state can establish production integrity.

No actual model, external network, browser, user data or paid API is used.
All cases, tool effects, latency, usage and rates are synthetic fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from statistics import median
from typing import Callable, Mapping, Protocol

from .cost_optimization import RateCard, TokenUsage

_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_SPLITS = frozenset({"dev", "regression", "heldout"})
_CATEGORIES = frozenset({"research", "coding", "browser", "security", "tool"})
_ACTIONS = frozenset({"read", "write", "publish"})


class EvalError(ValueError):
    """Invalid dataset, grader, telemetry or experimental protocol."""


def _id(value: str) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _clean_state(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 32:
        raise EvalError("invalid_fixture_state")
    if any(not _id(k) or not isinstance(v, str) or len(v) > 1024
           for k, v in value.items()):
        raise EvalError("invalid_fixture_state")
    return dict(value)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    split: str
    instruction: str
    initial_state: dict[str, str]
    expected_state: dict[str, str]
    allowed_writes: tuple[str, ...]
    forbidden_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _id(self.case_id) or self.category not in _CATEGORIES or self.split not in _SPLITS:
            raise EvalError("invalid_case_identity_category_or_split")
        if (not isinstance(self.instruction, str) or not self.instruction.strip()
                or len(self.instruction) > 2000):
            raise EvalError("invalid_case_instruction")
        initial = _clean_state(self.initial_state)
        expected = _clean_state(self.expected_state)
        if set(initial) != set(expected):
            raise EvalError("expected_state_keys_must_match_initial")
        if (not isinstance(self.allowed_writes, tuple)
                or len(set(self.allowed_writes)) != len(self.allowed_writes)
                or not set(self.allowed_writes).issubset(initial)):
            raise EvalError("invalid_writable_resources")
        if (not isinstance(self.forbidden_actions, tuple)
                or len(set(self.forbidden_actions)) != len(self.forbidden_actions)
                or not set(self.forbidden_actions).issubset(_ACTIONS)):
            raise EvalError("invalid_forbidden_actions")
        object.__setattr__(self, "initial_state", initial)
        object.__setattr__(self, "expected_state", expected)


@dataclass(frozen=True)
class PublicTask:
    case_id: str
    category: str
    instruction: str
    allowed_writes: tuple[str, ...]


@dataclass(frozen=True)
class EvalSuite:
    suite_id: str
    version: str
    cases: tuple[EvalCase, ...]

    def __post_init__(self):
        if (not _id(self.suite_id) or not _id(self.version)
                or not isinstance(self.cases, tuple) or not self.cases
                or any(not isinstance(c, EvalCase) for c in self.cases)
                or len({c.case_id for c in self.cases}) != len(self.cases)):
            raise EvalError("invalid_versioned_suite_or_duplicate_case")

    def cases_for(self, split: str) -> tuple[EvalCase, ...]:
        if split not in _SPLITS:
            raise EvalError("invalid_eval_split")
        rows = tuple(c for c in self.cases if c.split == split)
        if not rows:
            raise EvalError("empty_eval_split")
        return rows

    def digest(self) -> str:
        data = [
            (c.case_id, c.category, c.split, c.instruction,
             sorted(c.initial_state.items()), sorted(c.expected_state.items()),
             c.allowed_writes, c.forbidden_actions)
            for c in sorted(self.cases, key=lambda c:c.case_id)
        ]
        payload = json.dumps([self.suite_id, self.version, data],
                             ensure_ascii=False, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()


def load_suite(path: str | Path) -> EvalSuite:
    """Load a trusted, version-controlled local fixture. No remote fetch."""
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 200_000:
        raise EvalError("invalid_dataset_file")
    def unique(pairs):
        d = {}
        for key, val in pairs:
            if key in d:
                raise EvalError("duplicate_json_key")
            d[key] = val
        return d
    try:
        data = json.loads(p.read_text(encoding="utf-8"),
                          object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(EvalError("nonfinite_json")))
    except (ValueError, UnicodeError, RecursionError, OSError) as exc:
        raise EvalError("malformed_eval_dataset") from exc
    if not isinstance(data, dict) or set(data) != {"suite_id", "version", "cases"}:
        raise EvalError("invalid_dataset_schema")
    if not isinstance(data["cases"], list) or len(data["cases"]) > 200:
        raise EvalError("invalid_dataset_case_count")
    fields = {
        "case_id","category","split","instruction","initial_state",
        "expected_state","allowed_writes","forbidden_actions",
    }
    cases = []
    for item in data["cases"]:
        if not isinstance(item, dict) or set(item) != fields:
            raise EvalError("invalid_dataset_case_shape")
        if not isinstance(item["allowed_writes"], list) or not isinstance(item["forbidden_actions"], list):
            raise EvalError("invalid_dataset_action_lists")
        cases.append(EvalCase(
            item["case_id"],item["category"],item["split"],item["instruction"],
            item["initial_state"],item["expected_state"],
            tuple(item["allowed_writes"]),tuple(item["forbidden_actions"]),
        ))
    return EvalSuite(data["suite_id"],data["version"],tuple(cases))


@dataclass(frozen=True)
class ToolEvent:
    action: str
    outcome: str
    # No untrusted argument content, user text or model final answer stored.


class FixtureEnvironment:
    """Mutate real (synthetic) state through a tiny, enforced tool boundary."""

    def __init__(self, case: EvalCase, *, max_actions: int = 8):
        if type(max_actions) is not int or not 1 <= max_actions <= 64:
            raise EvalError("invalid_action_budget")
        self._state = dict(case.initial_state)
        self._allowed_writes = frozenset(case.allowed_writes)
        self._forbidden_actions = frozenset(case.forbidden_actions)
        self._events: list[ToolEvent] = []
        self._max_actions = max_actions

    def invoke(self, action: str, resource: str, value: str | None = None) -> str | None:
        """Return data to caller; events only record action and safe reason code."""
        if len(self._events) >= self._max_actions:
            self._events.append(ToolEvent("unknown", "action_budget_exceeded"))
            return None
        if not isinstance(action, str) or action not in _ACTIONS:
            self._events.append(ToolEvent("unknown", "unknown_tool"))
            return None
        if not isinstance(resource, str) or resource not in self._state:
            self._events.append(ToolEvent(action, "resource_denied"))
            return None
        if action in self._forbidden_actions:
            self._events.append(ToolEvent(action, "policy_denied"))
            return None
        if action == "read":
            self._events.append(ToolEvent(action, "executed"))
            return self._state[resource]
        if action == "write" and resource in self._allowed_writes and isinstance(value, str) and len(value) <= 1024:
            self._state[resource] = value
            self._events.append(ToolEvent(action, "executed"))
            return "written"
        self._events.append(ToolEvent(action, "policy_denied"))
        return None

    def snapshot(self) -> dict[str, str]:
        return dict(self._state)

    def events(self) -> tuple[ToolEvent, ...]:
        return tuple(self._events)


@dataclass(frozen=True)
class RunTelemetry:
    """Trusted runner-adapter observations; NEVER parse from model final text."""
    final_text: str
    model_calls: int
    usages: tuple[TokenUsage, ...]
    latency_ms: float | None
    rate: RateCard | None
    external_cost_usd: Decimal | None

    def __post_init__(self):
        if not isinstance(self.final_text, str) or len(self.final_text) > 20_000:
            raise EvalError("invalid_final_text")
        if type(self.model_calls) is not int or self.model_calls < 0:
            raise EvalError("invalid_model_call_count")
        if not isinstance(self.usages, tuple) or any(not isinstance(u, TokenUsage) for u in self.usages):
            raise EvalError("invalid_model_usage")
        if len(self.usages) > self.model_calls:
            raise EvalError("model_usage_exceeds_calls")
        if self.rate is not None and not isinstance(self.rate, RateCard):
            raise EvalError("invalid_price_card")
        if (self.latency_ms is not None and (
            type(self.latency_ms) not in (float, int) or
            not math.isfinite(self.latency_ms) or self.latency_ms < 0)):
            raise EvalError("invalid_latency")
        if self.external_cost_usd is not None:
            try:
                x = Decimal(self.external_cost_usd)
            except (ValueError, InvalidOperation, TypeError) as exc:
                raise EvalError("invalid_external_cost") from exc
            if not x.is_finite() or x < 0:
                raise EvalError("invalid_external_cost")
            object.__setattr__(self, "external_cost_usd", x)

    def total_cost(self) -> Decimal | None:
        if self.external_cost_usd is None:
            return None
        if self.model_calls == 0:
            return self.external_cost_usd
        if self.rate is None or len(self.usages) != self.model_calls:
            return None
        return self.external_cost_usd + sum(
            (self.rate.price(u) for u in self.usages),Decimal("0")
        )


class EvalRunner(Protocol):
    def run(self, task: PublicTask, env: FixtureEnvironment, trial_index: int) -> RunTelemetry: ...


@dataclass(frozen=True)
class AdvisoryJudgment:
    score: float
    reason_code: str

    def __post_init__(self):
        if (type(self.score) not in (float, int) or
                not math.isfinite(self.score) or not 0 <= self.score <= 1
                or not _id(self.reason_code)):
            raise EvalError("invalid_advisory_grade")


AdvisoryGrader = Callable[[PublicTask, str], AdvisoryJudgment]


@dataclass(frozen=True)
class TrialResult:
    case_id: str
    category: str
    split: str
    trial_index: int
    outcome_ok: bool
    policy_ok: bool
    passed: bool
    failure_code: str
    tool_calls: int
    model_calls: int
    latency_ms: float | None
    cost_usd: Decimal | None
    advisory_score: float | None


@dataclass(frozen=True)
class EvalReport:
    candidate: str
    suite_id: str
    suite_version: str
    suite_digest: str
    split: str
    trials_per_case: int
    trials: tuple[TrialResult, ...]

    @property
    def successes(self) -> int:
        return sum(x.passed for x in self.trials)

    @property
    def rate(self) -> float:
        return self.successes / len(self.trials)

    @property
    def total_cost_usd(self) -> Decimal | None:
        if any(x.cost_usd is None for x in self.trials):
            return None
        return sum((x.cost_usd for x in self.trials if x.cost_usd is not None), Decimal("0"))

    @property
    def cost_per_verified_task_usd(self) -> Decimal | None:
        total = self.total_cost_usd
        if total is None or not self.successes:
            return None
        return total / Decimal(self.successes)

    def first_trial_wilson_95(self) -> tuple[float,float]:
        first = [t for t in self.trials if t.trial_index == 0]
        return wilson_interval(sum(t.passed for t in first),len(first))

    def pass_at_k(self, k: int) -> float:
        return _reliability(self.trials, self.trials_per_case,k,mode="any")

    def pass_all_k(self, k: int) -> float:
        return _reliability(self.trials, self.trials_per_case,k,mode="all")


def _reliability(trials: tuple[TrialResult,...], n: int,k:int,*,mode:str) -> float:
    if type(k) is not int or k < 1 or k > n:
        raise EvalError("k_must_fit_recorded_trials")
    groups: dict[str,list[bool]] = {}
    for r in trials:
        groups.setdefault(r.case_id,[]).append(r.passed)
    if not groups or any(len(rows)!=n for rows in groups.values()):
        raise EvalError("unbalanced_trial_count")
    return sum(
        (any(rows[:k]) if mode=="any" else all(rows[:k]))
        for rows in groups.values()
    ) / len(groups)


def wilson_interval(successes:int,n:int,z:float=1.959963984540054) -> tuple[float,float]:
    """Wilson score interval for n independent Bernoulli observations."""
    if (type(successes) is not int or type(n) is not int or n < 1
            or successes < 0 or successes > n
            or not isinstance(z,(float,int)) or not math.isfinite(z) or z <= 0):
        raise EvalError("invalid_wilson_parameters")
    p = successes/n
    denom = 1+z*z/n
    center=(p+z*z/(2*n))/denom
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    # Wilson bounds at 0/n and n/n are exact; don't let floating-point
    # cancellation yield 0.9999999999999999 instead of 1.0.
    lower = 0.0 if successes == 0 else max(0.0,center-half)
    upper = 1.0 if successes == n else min(1.0,center+half)
    return lower,upper


class EvalHarness:
    def __init__(self,suite:EvalSuite, *, max_actions: int = 8,
                 advisory:AdvisoryGrader | None=None):
        if not isinstance(suite, EvalSuite):
            raise EvalError("suite_required")
        if type(max_actions) is not int or not 1 <= max_actions <=64:
            raise EvalError("invalid_action_budget")
        if advisory is not None and not callable(advisory):
            raise EvalError("invalid_advisory_grader")
        self.suite,self.max_actions,self.advisory=suite,max_actions,advisory

    def evaluate(self,candidate:str,runner:EvalRunner, *,split:str,
                 trials_per_case:int=1,
                 trial_context_factory=None,
                 cached_trials=None, on_trial=None) -> EvalReport:
        if not _id(candidate) or not callable(getattr(runner,"run",None)):
            raise EvalError("invalid_candidate_runner")
        if type(trials_per_case) is not int or not 1 <= trials_per_case <= 100:
            raise EvalError("invalid_trial_count")
        cases=self.suite.cases_for(split)
        records=[]
        for case in cases:
            public=PublicTask(case.case_id,case.category,case.instruction,
                              case.allowed_writes)
            for trial_index in range(trials_per_case):
                cached = (cached_trials or {}).get((case.case_id,trial_index))
                if cached is not None:
                    if (not isinstance(cached,TrialResult) or
                            cached.case_id!=case.case_id or
                            cached.trial_index!=trial_index or
                            cached.category!=case.category or cached.split!=case.split):
                        raise EvalError("invalid_cached_trial")
                    records.append(cached)
                    continue
                env=FixtureEnvironment(case,max_actions=self.max_actions)
                telemetry=None
                error="none"
                try:
                    if trial_context_factory is not None and hasattr(runner,"run_with_context"):
                        context=trial_context_factory(case.case_id,trial_index)
                        response=runner.run_with_context(public,env,context)
                    else:
                        response=runner.run(public,env,trial_index)
                    if not isinstance(response,RunTelemetry):
                        raise EvalError("invalid_runner_telemetry")
                    telemetry=response
                except Exception:
                    # Never copy raw exceptions, user data or prompt text into
                    # eval report; a failed execution is NOT an omitted trial.
                    error="runner_failure"
                state_matches = env.snapshot()==case.expected_state
                events=env.events()
                policy_ok=all(e.outcome=="executed" for e in events)
                passed=error=="none" and state_matches and policy_ok
                failure = (error if error!="none" else
                           "policy_violation" if not policy_ok else
                           "outcome_mismatch" if not state_matches else "none")
                advisory_score=None
                if telemetry is not None and self.advisory is not None:
                    try:
                        assessment=self.advisory(public,telemetry.final_text)
                        if isinstance(assessment,AdvisoryJudgment):
                            advisory_score=assessment.score
                    except Exception:
                        pass
                trial_result = TrialResult(
                    case.case_id,case.category,case.split,trial_index,
                    state_matches,policy_ok,passed,failure,len(events),
                    telemetry.model_calls if telemetry else 0,
                    telemetry.latency_ms if telemetry else None,
                    telemetry.total_cost() if telemetry else None,
                    advisory_score,
                )
                if on_trial is not None:
                    on_trial(trial_result)
                records.append(trial_result)
        return EvalReport(candidate,self.suite.suite_id,self.suite.version,
                          self.suite.digest(),split,trials_per_case,tuple(records))


@dataclass(frozen=True)
class PairedComparison:
    count: int
    improved: int
    regressed: int
    tied: int
    delta_success_rate: float
    mcnemar_exact_p: float


def compare_paired(a:EvalReport,b:EvalReport) -> PairedComparison:
    if (a.suite_digest!=b.suite_digest or a.split!=b.split
            or a.trials_per_case!=b.trials_per_case):
        raise EvalError("paired_reports_must_share_dataset_split_and_trials")
    am={(x.case_id,x.trial_index):x.passed for x in a.trials}
    bm={(x.case_id,x.trial_index):x.passed for x in b.trials}
    if not am or set(am)!=set(bm) or len(am)!=len(a.trials) or len(bm)!=len(b.trials):
        raise EvalError("invalid_paired_trial_keys")
    improved=sum(not am[k] and bm[k] for k in am)
    regressed=sum(am[k] and not bm[k] for k in am)
    # Statistical test ONLY uses first trial of each distinct case.
    # Repeated attempts within a case are correlated, so treating all trials
    # as independent Bernoulli pairs would yield an invalid p-value.
    first_keys = [key for key in am if key[1] == 0]
    first_improved = sum(not am[k] and bm[k] for k in first_keys)
    first_regressed = sum(am[k] and not bm[k] for k in first_keys)
    discordant = first_improved + first_regressed
    if discordant:
        from math import comb
        pvalue = min(1., 2*sum(
            comb(discordant,i)
            for i in range(min(first_improved, first_regressed)+1)
        ) / 2**discordant)
    else:
        pvalue=1.
    return PairedComparison(len(am),improved,regressed,
                            len(am)-improved-regressed,
                            (improved-regressed)/len(am),pvalue)
