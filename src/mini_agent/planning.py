"""Chapter 10: deterministic planning, feedback, replanning and verification.

The executor runs a finite directed acyclic task plan, not an LLM's private
reasoning. The verifier reads the environment independently of the worker's
claims. No actual shell/network access, concurrency, durable state, or sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    action: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class Observation:
    ok: bool
    detail: str


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    evidence: str


@dataclass(frozen=True)
class PlanResult:
    status: str
    reason: str
    attempts: int
    completed: tuple[str, ...]
    observations: tuple[tuple[str, Observation], ...]
    verification: CheckResult | None


def validate_plan(steps: tuple[PlanStep, ...]) -> None:
    ids = [s.step_id for s in steps]
    if not ids or any(not x for x in ids) or len(set(ids)) != len(ids):
        raise PlanError("Plan requires unique nonempty step IDs")
    known = set(ids)
    for step in steps:
        if not step.action or step.step_id in step.dependencies:
            raise PlanError("Invalid step action or self dependency")
        if any(dep not in known for dep in step.dependencies):
            raise PlanError("Unknown dependency")
    graph = {s.step_id: s.dependencies for s in steps}
    visited: set[str] = set()
    visiting: set[str] = set()

    def walk(node: str):
        if node in visiting:
            raise PlanError("Dependency cycle")
        if node in visited:
            return
        visiting.add(node)
        for dep in graph[node]:
            walk(dep)
        visiting.remove(node)
        visited.add(node)

    for node in ids:
        walk(node)


class ToyRepository:
    """An intentionally inspectable environment independent of agent claims."""

    def __init__(self, *, broken: bool = True):
        self.broken = broken
        self.inspected = False
        self.test_passed = False
        self.actions: list[str] = []

    def execute(self, action: str) -> Observation:
        self.actions.append(action)
        if action == "inspect":
            self.inspected = True
            return Observation(True, "Found an incorrect equality check")
        if action == "patch":
            if not self.inspected:
                return Observation(False, "Must inspect before patch")
            self.broken = False
            return Observation(True, "Patch applied")
        if action == "test":
            self.test_passed = not self.broken
            return Observation(self.test_passed, "PASS" if self.test_passed else "FAIL")
        if action == "claim_success":
            return Observation(True, "Agent claims tests passed")
        return Observation(False, "Unknown action")

    def verify(self) -> CheckResult:
        # Independent evidence: a claim or previous successful tool message
        # is not enough. Check the actual environment and rerun the test.
        passed = not self.broken and self.test_passed
        return CheckResult(passed, "verified_repository_and_test" if passed
                           else "repository_or_test_not_verified")


class PlanExecutor:
    def __init__(
        self, environment: ToyRepository,
        *,
        max_actions: int = 8,
        replan: Callable[[tuple[PlanStep, ...], str, Observation],
                         tuple[PlanStep, ...] | None] | None = None,
    ):
        if type(max_actions) is not int or max_actions < 1:
            raise ValueError("max_actions must be positive")
        self.environment = environment
        self.max_actions = max_actions
        self.replan = replan

    def run(self, steps: tuple[PlanStep, ...]) -> PlanResult:
        validate_plan(steps)
        completed: set[str] = set()
        history: list[tuple[str, Observation]] = []
        action_count = 0
        attempted: set[str] = set()
        current = steps

        def finish(status: str, reason: str, check: CheckResult | None = None):
            return PlanResult(status, reason, action_count, tuple(sorted(completed)),
                              tuple(history), check)

        while action_count < self.max_actions:
            eligible = next((
                step for step in current
                if step.step_id not in completed
                and step.step_id not in attempted
                and set(step.dependencies).issubset(completed)
            ), None)
            if eligible is None:
                if all(s.step_id in completed for s in current):
                    check = self.environment.verify()
                    return finish("completed" if check.passed else "failed",
                                  "verified" if check.passed else "verification_failed",
                                  check)
                return finish("failed", "plan_stalled")
            attempted.add(eligible.step_id)
            observation = self.environment.execute(eligible.action)
            action_count += 1
            history.append((eligible.step_id, observation))
            if observation.ok:
                completed.add(eligible.step_id)
                continue
            if self.replan is None:
                return finish("failed", "step_failed")
            replacement = self.replan(current, eligible.step_id, observation)
            if replacement is None:
                return finish("failed", "replan_declined")
            validate_plan(replacement)
            # A new plan may only retain completed steps with unchanged
            # actions/dependencies; otherwise the completion proof is stale.
            old = {s.step_id: s for s in current}
            new = {s.step_id: s for s in replacement}
            if any(sid not in new or new[sid] != old[sid] for sid in completed):
                return finish("failed", "invalid_replan_completed_steps")
            current = replacement
            attempted.clear()
        return finish("failed", "action_budget_exhausted")
