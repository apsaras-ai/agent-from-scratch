"""Chapter 11: in-memory DAG workflow with branches, parallelism and approval.

Node handlers are trusted async Python functions, not arbitrary model code.
Approval resumes only with a token supplied by the trusted caller.
No durable storage, distributed worker leases, sandbox, or exactly-once effects.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping


class WorkflowError(ValueError):
    pass


Handler = Callable[[Mapping[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Node:
    node_id: str
    handler: Handler
    needs: tuple[str, ...] = ()
    route: str | None = None
    approval: bool = False


@dataclass
class Run:
    status: str = "created"
    results: dict[str, Any] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)
    events: list[str] = field(default_factory=list)
    approval_for: str | None = None
    steps: int = 0


class Workflow:
    def __init__(self, nodes: tuple[Node, ...], *, max_parallel=3,
                 max_steps=30):
        if not nodes or len({n.node_id for n in nodes}) != len(nodes):
            raise WorkflowError("Nodes require unique identities")
        if type(max_parallel) is not int or max_parallel < 1:
            raise ValueError("max_parallel must be positive")
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps must be positive")
        ids = {n.node_id for n in nodes}
        for n in nodes:
            if not n.node_id or any(d not in ids or d == n.node_id for d in n.needs):
                raise WorkflowError("Invalid dependency")
            if n.route is not None and n.route not in ids:
                raise WorkflowError("Unknown route destination")
        graph = {n.node_id: n.needs for n in nodes}
        visiting, seen = set(), set()
        def visit(node):
            if node in visiting:
                raise WorkflowError("Dependency cycle")
            if node in seen:
                return
            visiting.add(node)
            for dep in graph[node]:
                visit(dep)
            visiting.remove(node)
            seen.add(node)
        for key in ids:
            visit(key)
        self.nodes = {n.node_id: n for n in nodes}
        self.order = [n.node_id for n in nodes]
        self.max_parallel = max_parallel
        self.max_steps = max_steps

    async def run(self, state: Run | None = None, *,
                  approved: str | None = None) -> Run:
        state = state or Run()
        if state.status in ("completed", "failed"):
            raise WorkflowError("Terminal runs cannot be resumed")
        if state.status == "waiting_approval":
            if approved != state.approval_for:
                return state
            state.events.append("approved:" + approved)
            state.approval_for = None
        state.status = "running"
        while state.steps < self.max_steps:
            pending = [
                self.nodes[n] for n in self.order
                if n not in state.results and n not in state.skipped
            ]
            if not pending:
                state.status = "completed"
                return state
            ready = [n for n in pending if set(n.needs).issubset(state.results)]
            if not ready:
                # A branch may make descendants unreachable; mark nodes whose
                # predecessors are permanently skipped as skipped, then retry.
                blocked = [n for n in pending if set(n.needs) & state.skipped]
                if blocked:
                    for n in blocked:
                        state.skipped.add(n.node_id)
                        state.events.append("skipped:" + n.node_id)
                    continue
                state.status = "failed"
                state.events.append("deadlock")
                return state
            approval = next((n for n in ready if n.approval), None)
            if approval is not None and approved != approval.node_id:
                state.status = "waiting_approval"
                state.approval_for = approval.node_id
                state.events.append("approval_required:" + approval.node_id)
                return state
            approved = None  # a grant is good for one specific node only
            batch = ready[:min(self.max_parallel, self.max_steps-state.steps)]
            context = dict(state.results)  # stable snapshot for concurrent work
            tasks = [asyncio.create_task(n.handler(context)) for n in batch]
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            state.steps += len(batch)
            for node, outcome in zip(batch, outcomes):
                if isinstance(outcome, BaseException):
                    state.status = "failed"
                    state.events.append("failed:" + node.node_id)
                    return state
                state.results[node.node_id] = outcome
                state.events.append("finished:" + node.node_id)
                if node.route:
                    # Conditional route: only follow target if returned True.
                    # This demo supports one optional branch, not general
                    # dynamic DAG insertion.
                    if type(outcome) is not bool:
                        state.status = "failed"
                        state.events.append("invalid_route_result")
                        return state
                    if not outcome:
                        state.skipped.add(node.route)
                        state.events.append("skipped:" + node.route)
        state.status = "failed"
        state.events.append("step_budget_exhausted")
        return state
