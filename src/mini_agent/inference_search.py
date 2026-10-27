"""Chapter 29: inference-time search with explicit host-owned budgets.

OFFLINE EDUCATIONAL LAB: A synthetic arithmetic-program task replaces actual
LLM tokens, code execution and tool side effects. A toy stochastic proposer
suggests operator names; a trusted arithmetic environment applies an exact
allowlisted DSL; the host verifier replays selected programs from scratch.

The demo models four distinct selection methods: Best-of-N verified rollouts,
self-consistency over *final values*, heuristic beam search, and MCTS/UCT.
This is NOT an implementation of language-model reasoning, Tree-of-Thoughts
prompts, LATS training, or a safe general-purpose code sandbox.

Strict local hard ceilings cover proposal calls, step transitions,
independent verifier invocations, and candidate/rollout count. Monetary rates
are synthetic Decimal fixtures, not current model/provider prices.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import math
import random
import re
from typing import Protocol

_VALID_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
_OPS = ("add1", "add3", "mul2", "sub1")
_METHODS = ("best_of_n", "self_consistency", "beam", "mcts")


class SearchError(ValueError):
    """Safe control-plane error code, not raw model/trace payload."""


def _positive_cost(value: Decimal | str) -> Decimal:
    try:
        out = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SearchError("invalid_synthetic_unit_cost") from exc
    if not out.is_finite() or out < 0:
        raise SearchError("invalid_synthetic_unit_cost")
    return out


@dataclass(frozen=True)
class SynthesisTask:
    task_id: str
    initial: int
    target: int
    max_steps: int
    allowed_ops: tuple[str, ...] = _OPS
    max_abs_value: int = 128

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not _VALID_ID.fullmatch(self.task_id):
            raise SearchError("invalid_search_task_id")
        if any(type(v) is not int for v in (
            self.initial, self.target, self.max_steps, self.max_abs_value
        )):
            raise SearchError("invalid_integer_task_parameter")
        if (not 1 <= self.max_steps <= 10 or
                not 1 <= self.max_abs_value <= 4096 or
                not 0 <= self.initial <= self.max_abs_value or
                not 0 <= self.target <= self.max_abs_value):
            raise SearchError("invalid_synthesis_task_bounds")
        if (not isinstance(self.allowed_ops, tuple) or
                not self.allowed_ops or
                len(set(self.allowed_ops)) != len(self.allowed_ops) or
                any(op not in _OPS for op in self.allowed_ops)):
            raise SearchError("invalid_operator_allowlist")


@dataclass(frozen=True)
class SearchBudget:
    proposals: int = 120
    transitions: int = 160
    verifications: int = 40
    candidates: int = 40
    proposal_cost: Decimal = Decimal("0.001")
    transition_cost: Decimal = Decimal("0.0002")
    verification_cost: Decimal = Decimal("0.002")

    def __post_init__(self) -> None:
        for field_name in ("proposals", "transitions", "verifications", "candidates"):
            val = getattr(self, field_name)
            if type(val) is not int or not 1 <= val <= 10_000:
                raise SearchError("invalid_search_budget")
        for field_name in ("proposal_cost", "transition_cost", "verification_cost"):
            object.__setattr__(self, field_name, _positive_cost(getattr(self, field_name)))


@dataclass(frozen=True)
class Candidate:
    actions: tuple[str, ...]
    state: int


@dataclass(frozen=True)
class SearchResult:
    task_id: str
    method: str
    success: bool
    program: tuple[str, ...] | None
    final_state: int | None
    reason: str
    candidates: int
    proposals: int
    transitions: int
    verifications: int
    rejected_actions: int
    synthetic_cost: Decimal


class ActionProposer(Protocol):
    def choose(
        self, task: SynthesisTask, current: int,
        options: tuple[str, ...], rng: random.Random,
    ) -> str: ...


def _transition(current: int, op: str, bound: int) -> int | None:
    """Host-owned pure arithmetic DSL: no eval(), subprocess, files or network."""
    if op == "add1":
        result = current + 1
    elif op == "add3":
        result = current + 3
    elif op == "mul2":
        result = current * 2
    elif op == "sub1":
        result = current - 1
    else:
        return None
    if not 0 <= result <= bound:
        return None
    return result


class ToyProposer:
    """Small deterministic-seed stochastic policy, not a pretrained LLM.

    Usually selects an operation that gets closer to the publicly visible
    target, but occasionally explores; getting closer can be a local trap.
    """

    def __init__(self, greedy_probability: float = 0.6) -> None:
        if (type(greedy_probability) not in (int, float) or
                not math.isfinite(greedy_probability) or
                not 0 <= greedy_probability <= 1):
            raise SearchError("invalid_greedy_probability")
        self.greedy_probability = float(greedy_probability)

    def choose(
        self, task: SynthesisTask, current: int,
        options: tuple[str, ...], rng: random.Random,
    ) -> str:
        legal = [
            op for op in options
            if op in task.allowed_ops and
            _transition(current, op, task.max_abs_value) is not None
        ]
        if not legal:
            return options[0] if options else ""
        if rng.random() < self.greedy_probability:
            return min(legal, key=lambda op: (
                abs(task.target - _transition(current, op, task.max_abs_value)),
                legal.index(op),
            ))
        return rng.choice(legal)


class _BudgetMeter:
    def __init__(self, budget: SearchBudget):
        self.budget = budget
        self.proposals = 0
        self.transitions = 0
        self.verifications = 0
        self.candidates = 0
        self.rejected = 0

    def spend(self, kind: str) -> bool:
        field_name = {
            "proposal": "proposals",
            "transition": "transitions",
            "verification": "verifications",
            "candidate": "candidates",
        }[kind]
        current = getattr(self, field_name)
        if current >= getattr(self.budget, field_name):
            return False
        setattr(self, field_name, current + 1)
        return True

    def cost(self) -> Decimal:
        return (
            self.proposals * self.budget.proposal_cost +
            self.transitions * self.budget.transition_cost +
            self.verifications * self.budget.verification_cost
        )


class _SearchSession:
    def __init__(
        self, task: SynthesisTask, budget: SearchBudget,
        proposer: ActionProposer, seed: int,
    ):
        self.task = task
        self.meter = _BudgetMeter(budget)
        self.proposer = proposer
        self.rng = random.Random(seed)
        self.exhausted = False
        self.last_validated: Candidate | None = None

    def proposed_step(
        self, state: int, options: tuple[str, ...] | None = None,
    ) -> tuple[str, int] | None:
        if not self.meter.spend("proposal"):
            self.exhausted = True
            return None
        choices = self.task.allowed_ops if options is None else options
        try:
            op = self.proposer.choose(self.task, state, choices, self.rng)
        except Exception:
            self.meter.rejected += 1
            return None
        if not isinstance(op, str) or op not in self.task.allowed_ops or op not in choices:
            self.meter.rejected += 1
            return None
        if not self.meter.spend("transition"):
            self.exhausted = True
            return None
        next_state = _transition(state, op, self.task.max_abs_value)
        if next_state is None:
            self.meter.rejected += 1
            return None
        return op, next_state

    def rollout(self, start: Candidate) -> Candidate | None:
        cur = start
        while len(cur.actions) < self.task.max_steps and not self.exhausted:
            if cur.state == self.task.target:
                break
            step = self.proposed_step(cur.state)
            if step is None:
                if not self.exhausted:
                    # Unsupported/invalid proposal fails this rollout.
                    return None
                break
            op, nxt = step
            cur = Candidate(cur.actions + (op,), nxt)
        return cur if cur.actions else None

    def verify(self, candidate: Candidate) -> bool:
        if not self.meter.spend("verification"):
            self.exhausted = True
            return False
        # Trusted replay from the ACTUAL initial state. The proposer never
        # gets an authoritative "verified" flag or verification credentials.
        cur = self.task.initial
        if len(candidate.actions) > self.task.max_steps:
            return False
        for action in candidate.actions:
            if action not in self.task.allowed_ops:
                return False
            state = _transition(cur, action, self.task.max_abs_value)
            if state is None:
                return False
            cur = state
        ok = cur == candidate.state == self.task.target
        if ok:
            self.last_validated = candidate
        return ok

    def output(self, method: str) -> SearchResult:
        w = self.last_validated
        meter = self.meter
        reason = ("verified" if w else
                  "budget_exhausted" if self.exhausted else
                  "no_verified_solution")
        return SearchResult(
            self.task.task_id, method, w is not None,
            w.actions if w else None,
            w.state if w else None,
            reason, meter.candidates, meter.proposals, meter.transitions,
            meter.verifications, meter.rejected, meter.cost(),
        )


def _best_of_n(ctx: _SearchSession) -> None:
    """Independent sample paths with a PERFECT binary host verifier.

    This illustrates verifier-guided Best-of-N, not model-score reranking;
    the first independently verified candidate wins. Verification is charged
    for every tested path.
    """
    while ctx.meter.spend("candidate") and not ctx.exhausted:
        candidate = ctx.rollout(Candidate((), ctx.task.initial))
        if candidate and ctx.verify(candidate):
            return


def _self_consistency(ctx: _SearchSession) -> None:
    """Sample paths, vote by FINAL NUMERIC ANSWER, then verify ONE winner.

    Intentionally does not choose by hidden ground truth; correlated wrong
    responses can dominate, making majority vote confidently wrong.
    """
    results: list[Candidate] = []
    while ctx.meter.spend("candidate") and not ctx.exhausted:
        candidate = ctx.rollout(Candidate((), ctx.task.initial))
        if candidate:
            results.append(candidate)
    if not results:
        return
    counts = Counter(candidate.state for candidate in results)
    best_state = min(
        counts, key=lambda state: (-counts[state], next(
            i for i, c in enumerate(results) if c.state == state
        ))
    )
    chosen = next(c for c in results if c.state == best_state)
    ctx.verify(chosen)


def _beam(ctx: _SearchSession, *, width: int, branch: int) -> None:
    """Bounded heuristic beam (approximate Tree-of-Thoughts abstraction).

    Nodes are symbolic arithmetic states, NOT actual LM chain-of-thought.
    Public-target distance is a heuristic, not an independent verifier.
    """
    beam = [Candidate((), ctx.task.initial)]
    seen: set[tuple[int, int]] = {(ctx.task.initial, 0)}
    for _ in range(ctx.task.max_steps):
        if ctx.exhausted or not beam:
            return
        frontier: list[Candidate] = []
        for node in beam:
            for _ in range(branch):
                if ctx.exhausted:
                    break
                step = ctx.proposed_step(node.state)
                if step is None:
                    continue
                op, nxt = step
                path = node.actions + (op,)
                key = (nxt, len(path))
                if key in seen:
                    continue
                seen.add(key)
                candidate = Candidate(path, nxt)
                if nxt == ctx.task.target:
                    if not ctx.meter.spend("candidate"):
                        ctx.exhausted = True
                        return
                    if ctx.verify(candidate):
                        return
                frontier.append(candidate)
        beam = sorted(
            frontier, key=lambda c: (
                abs(ctx.task.target - c.state), len(c.actions), c.actions
            )
        )[:width]


@dataclass
class _MCTSNode:
    state: int
    path: tuple[str, ...]
    parent: _MCTSNode | None = None
    children: list[_MCTSNode] = field(default_factory=list)
    unexpanded: list[str] = field(default_factory=list)
    visits: int = 0
    value: float = 0.0


def _node(task: SynthesisTask, state: int, path: tuple[str, ...],
          parent: _MCTSNode | None) -> _MCTSNode:
    return _MCTSNode(
        state, path, parent, [],
        [op for op in task.allowed_ops
         if _transition(state, op, task.max_abs_value) is not None],
    )


def _uct(child: _MCTSNode, parent_visits: int, exploration: float) -> float:
    if child.visits == 0:
        return float("inf")
    return (child.value / child.visits +
            exploration * math.sqrt(math.log(max(2, parent_visits)) / child.visits))


def _mcts(ctx: _SearchSession, *, exploration: float) -> None:
    """Finite-horizon MCTS: selection / expansion / rollout / backup (UCT).

    Reward is ONLY the host verifier's terminal binary outcome, not model
    confidence or execution-side effects. Tree edges are safe pure DSL ops.
    """
    root = _node(ctx.task, ctx.task.initial, (), None)
    while ctx.meter.spend("candidate") and not ctx.exhausted:
        current = root
        chain = [root]
        # Selection & expansion: one new edge per simulation.
        while len(current.path) < ctx.task.max_steps and current.state != ctx.task.target:
            if current.unexpanded:
                step = ctx.proposed_step(current.state, tuple(current.unexpanded))
                if step is None:
                    break
                op, next_state = step
                current.unexpanded.remove(op)
                current = _node(
                    ctx.task, next_state, current.path + (op,), current
                )
                chain[-1].children.append(current)
                chain.append(current)
                break
            if not current.children:
                break
            current = max(
                current.children,
                key=lambda node: (
                    _uct(node, chain[-1].visits, exploration),
                    -chain[-1].children.index(node),
                )
            )
            chain.append(current)
        # Default policy rollout does not mutate the already stored tree.
        terminal = ctx.rollout(Candidate(current.path, current.state))
        success = bool(terminal and ctx.verify(terminal))
        # Terminal reward is bounded [0,1]; parent visits track simulations.
        for node in chain:
            node.visits += 1
            node.value += float(success)
        if success:
            return


def derive_search_seed(base_seed: int, task_id: str, trial_index: int) -> int:
    """Stable case/trial seed; no claim of real-model deterministic sampling."""
    if (type(base_seed) is not int or not 0 <= base_seed < 2**64
            or not isinstance(task_id, str) or not _VALID_ID.fullmatch(task_id)
            or type(trial_index) is not int or not 0 <= trial_index < 10_000):
        raise SearchError("invalid_search_seed")
    payload = f"{base_seed}:{task_id}:{trial_index}".encode("utf-8")
    return int.from_bytes(sha256(payload).digest()[:8], "big")


def run_search(
    task: SynthesisTask, method: str, *,
    budget: SearchBudget = SearchBudget(),
    proposer: ActionProposer | None = None,
    seed: int = 42,
    width: int = 4,
    branch: int = 3,
    exploration: float = 1.41421356237,
) -> SearchResult:
    if not isinstance(task, SynthesisTask) or not isinstance(budget, SearchBudget):
        raise SearchError("invalid_task_or_budget")
    if method not in _METHODS:
        raise SearchError("unknown_search_method")
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise SearchError("invalid_search_seed")
    if any(type(v) is not int or not 1 <= v <= 20 for v in (width, branch)):
        raise SearchError("invalid_beam_parameters")
    if (type(exploration) not in (float, int) or
            not math.isfinite(exploration) or exploration < 0 or exploration > 100):
        raise SearchError("invalid_uct_exploration")
    policy = proposer if proposer is not None else ToyProposer()
    if not callable(getattr(policy, "choose", None)):
        raise SearchError("trusted_proposer_required")
    ctx = _SearchSession(task, budget, policy, seed)
    if task.initial == task.target:
        # An empty program is legitimate, but only the independent host
        # verifier may certify it; budget still counts the verifier call.
        ctx.verify(Candidate((), task.initial))
    elif method == "best_of_n":
        _best_of_n(ctx)
    elif method == "self_consistency":
        _self_consistency(ctx)
    elif method == "beam":
        _beam(ctx, width=width, branch=branch)
    else:
        _mcts(ctx, exploration=float(exploration))
    return ctx.output(method)


@dataclass(frozen=True)
class StrategyScore:
    method: str
    tasks: int
    verified: int
    total_synthetic_cost: Decimal
    total_proposals: int
    total_verifications: int

    @property
    def success_rate(self) -> float:
        return self.verified / self.tasks


def compare_strategies(
    tasks: tuple[SynthesisTask, ...], *,
    methods: tuple[str, ...] = _METHODS,
    budget: SearchBudget = SearchBudget(),
    seed: int = 29,
) -> tuple[StrategyScore, ...]:
    if (not isinstance(tasks, tuple) or not tasks or
            any(not isinstance(x, SynthesisTask) for x in tasks) or
            len({x.task_id for x in tasks}) != len(tasks)):
        raise SearchError("nonempty_unique_search_tasks_required")
    if (not isinstance(methods, tuple) or not methods or
            len(set(methods)) != len(methods) or
            any(method not in _METHODS for method in methods)):
        raise SearchError("invalid_method_comparison")
    rows: list[StrategyScore] = []
    for method in methods:
        results = [
            run_search(
                task, method, budget=budget,
                seed=derive_search_seed(seed, task.task_id, 0),
            )
            for task in tasks
        ]
        rows.append(StrategyScore(
            method, len(results), sum(x.success for x in results),
            sum((x.synthetic_cost for x in results), Decimal("0")),
            sum(x.proposals for x in results),
            sum(x.verifications for x in results),
        ))
    return tuple(rows)
