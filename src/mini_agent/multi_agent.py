"""Chapter 17: a bounded, deterministic multi-agent orchestration laboratory.

The supervisor owns role-based delegation, DAG dependencies, result validation,
single-run shared state, optimistic versions and independent acceptance.
Specialists return DATA proposals; they cannot mutate Blackboard through the
provided interface or grant themselves new permissions.

This is NOT a secure Python sandbox, a durable distributed runtime, or an LLM
benchmark. Worker timeouts are cooperative, not hard process termination.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Protocol


class CollaborationError(ValueError):
    """Malformed plan, unauthorized proposal, or invalid contract."""


class StateConflict(CollaborationError):
    """Concurrent writes or stale shared-state versions."""


@dataclass(frozen=True)
class Artifact:
    key: str
    content: str
    run_id: str
    producer_task: str
    producer_agent: str
    version: int
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposedArtifact:
    key: str
    content: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentReply:
    task_id: str
    writes: tuple[ProposedArtifact, ...]
    summary: str = ""


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    readable_keys: frozenset[str]
    writable_keys: frozenset[str]

    def __post_init__(self) -> None:
        if (not isinstance(self.agent_id, str) or not self.agent_id.strip()
                or not isinstance(self.readable_keys, frozenset)
                or not isinstance(self.writable_keys, frozenset)
                or any(not isinstance(k, str) or not k for k in
                       self.readable_keys | self.writable_keys)):
            raise CollaborationError("Invalid agent identity/capabilities")


@dataclass(frozen=True)
class DelegatedTask:
    task_id: str
    agent_id: str
    objective: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_id or not self.agent_id or not self.objective:
            raise CollaborationError("Nonempty task ID, agent ID and objective required")
        for names in (self.inputs, self.outputs, self.depends_on):
            if any(not isinstance(x, str) or not x for x in names) or len(set(names)) != len(names):
                raise CollaborationError("Duplicate/empty task key or dependency")
        if not self.outputs:
            raise CollaborationError("A task must declare an output key")


@dataclass(frozen=True)
class Delegation:
    run_id: str
    task_id: str
    agent_id: str
    objective: str
    input_keys: tuple[str, ...]
    output_keys: tuple[str, ...]


class Worker(Protocol):
    async def act(self, delegation: Delegation, inputs: Mapping[str, Artifact]) -> AgentReply: ...


@dataclass(frozen=True)
class Specialist:
    identity: AgentIdentity
    worker: Worker


@dataclass(frozen=True)
class Verification:
    passed: bool
    evidence: str


Verifier = Callable[[Mapping[str, Artifact]], Verification]


@dataclass(frozen=True)
class TeamEvent:
    kind: str
    task_id: str
    detail: str


@dataclass(frozen=True)
class TeamResult:
    status: str
    reason: str
    run_id: str
    artifacts: Mapping[str, Artifact]
    completed_tasks: tuple[str, ...]
    worker_invocations: int
    waves: int
    events: tuple[TeamEvent, ...]
    verification: Verification | None


@dataclass(frozen=True)
class HostResolution:
    """An explicit host-side judgment; not a token created by a worker.

    The application must authenticate the decider before constructing one.
    This teaching model has NO authentication or tamper-proof audit log.
    """
    run_id: str
    key: str
    content: str
    expected_version: int | None
    decided_by: str
    rationale: str


class Blackboard:
    """Per-run shared state. Every wave commits atomically in one process."""

    def __init__(self, run_id: str, *, max_content_chars: int = 12_000) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be nonempty")
        if type(max_content_chars) is not int or max_content_chars < 1:
            raise ValueError("max_content_chars must be positive")
        self.run_id = run_id
        self.max_content_chars = max_content_chars
        self._items: dict[str, Artifact] = {}
        self._events: list[TeamEvent] = []

    def seed(self, key: str, content: str, *, evidence_ids: tuple[str, ...] = ()) -> Artifact:
        """Trusted host supplies input. Never expose this method to agents."""
        if key in self._items:
            raise StateConflict("seed_key_already_exists")
        artifact = self._make(key, content, evidence_ids, "host", "seed", 1)
        self._items[key] = artifact
        self._events.append(TeamEvent("seed", "host", key))
        return artifact

    def _make(
        self, key: str, content: str, evidence_ids: tuple[str, ...],
        agent_id: str, task_id: str, version: int,
    ) -> Artifact:
        if not isinstance(key, str) or not key or not isinstance(content, str) or not content.strip() or len(content) > self.max_content_chars:
            raise CollaborationError("invalid_artifact_content_or_key")
        if not isinstance(evidence_ids, tuple) or any(not isinstance(x, str) or not x for x in evidence_ids):
            raise CollaborationError("invalid_evidence_ids")
        if len(evidence_ids) > 32:
            raise CollaborationError("evidence_count_exceeded")
        return Artifact(key, content, self.run_id, task_id, agent_id, version, evidence_ids)

    def snapshot(self) -> Mapping[str, Artifact]:
        return MappingProxyType(dict(self._items))

    def view(self, keys: tuple[str, ...]) -> Mapping[str, Artifact]:
        missing = set(keys) - self._items.keys()
        if missing:
            raise CollaborationError("missing_declared_input:" + ",".join(sorted(missing)))
        return MappingProxyType({key: self._items[key] for key in keys})

    def versions(self, keys: tuple[str, ...]) -> dict[str, int | None]:
        return {key: self._items[key].version if key in self._items else None for key in keys}

    def commit_wave(
        self,
        actions: list[tuple[DelegatedTask, Specialist, AgentReply, dict[str, int | None]]],
    ) -> tuple[Artifact, ...]:
        """Validate ALL proposals and CAS ALL writes before applying any.

        This is one-process atomicity, not a distributed transaction. Two
        writers of the same key in a wave are rejected rather than last-wins.
        """
        staged: dict[str, Artifact] = {}
        for task, specialist, reply, expected in actions:
            if not isinstance(reply, AgentReply) or reply.task_id != task.task_id:
                raise CollaborationError("worker_reply_task_mismatch")
            if not isinstance(reply.summary, str) or len(reply.summary) > 2000:
                raise CollaborationError("invalid_worker_summary")
            if (not isinstance(reply.writes, tuple) or len(reply.writes) > 32
                    or any(not isinstance(p, ProposedArtifact) for p in reply.writes)):
                raise CollaborationError("invalid_worker_writes")
            keys = [proposal.key for proposal in reply.writes]
            if len(set(keys)) != len(keys) or set(keys) != set(task.outputs):
                raise CollaborationError("worker_outputs_do_not_match_delegation")
            for proposal in reply.writes:
                key = proposal.key
                if key not in specialist.identity.writable_keys:
                    raise CollaborationError("worker_attempted_unauthorized_write")
                if key in staged:
                    raise StateConflict("concurrent_write_same_key:" + key)
                current = self._items.get(key)
                version = current.version if current else None
                if key not in expected or expected[key] != version:
                    raise StateConflict("stale_artifact_version:" + key)
                staged[key] = self._make(
                    key, proposal.content, proposal.evidence_ids,
                    specialist.identity.agent_id, task.task_id,
                    (version or 0) + 1,
                )
        self._items.update(staged)
        for task, _, _, _ in actions:
            self._events.append(TeamEvent("committed", task.task_id, ",".join(task.outputs)))
        return tuple(staged.values())

    def reconcile(self, decision: HostResolution) -> Artifact:
        """Explicit host override with a CAS; never performed automatically."""
        if (decision.run_id != self.run_id
                or not isinstance(decision.decided_by, str)
                or not decision.decided_by.strip()
                or not isinstance(decision.rationale, str)
                or not decision.rationale.strip()
                or (decision.expected_version is not None and (
                    type(decision.expected_version) is not int or decision.expected_version < 1))):
            raise CollaborationError("invalid_host_resolution")
        actual = self.versions((decision.key,))[decision.key]
        if decision.expected_version != actual:
            raise StateConflict("stale_host_resolution")
        artifact = self._make(
            decision.key, decision.content, (), "host:" + decision.decided_by,
            "resolution", (actual or 0) + 1,
        )
        self._items[decision.key] = artifact
        self._events.append(TeamEvent("host_resolved", decision.decided_by, decision.key))
        return artifact


def validate_plan(tasks: tuple[DelegatedTask, ...], specialists: Mapping[str, Specialist]) -> None:
    if not tasks or len({task.task_id for task in tasks}) != len(tasks):
        raise CollaborationError("plan_must_have_unique_tasks")
    ids = {task.task_id for task in tasks}
    graph = {task.task_id: task.depends_on for task in tasks}
    for task in tasks:
        specialist = specialists.get(task.agent_id)
        if specialist is None or specialist.identity.agent_id != task.agent_id:
            raise CollaborationError("unknown_specialist:" + task.agent_id)
        if not set(task.inputs).issubset(specialist.identity.readable_keys):
            raise CollaborationError("read_capability_violation")
        if not set(task.outputs).issubset(specialist.identity.writable_keys):
            raise CollaborationError("write_capability_violation")
        if any(dep not in ids or dep == task.task_id for dep in task.depends_on):
            raise CollaborationError("unknown_or_self_dependency")
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(task_id: str) -> None:
        if task_id in visiting:
            raise CollaborationError("dependency_cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dep in graph[task_id]:
            walk(dep)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in ids:
        walk(task_id)


class Supervisor:
    """Static-DAG orchestrator with bounded, parallel, isolated delegation.

    All worker outputs are untrusted proposals. Each batch shares a logical
    snapshot of Blackboard; they cannot observe another worker's uncommitted
    artifacts. Only the supervisor calls commit_wave and the verifier.
    """

    def __init__(
        self, specialists: Mapping[str, Specialist], *,
        max_parallel: int = 3, max_delegations: int = 12,
        worker_timeout_s: float = 2.0,
    ) -> None:
        if type(max_parallel) is not int or max_parallel < 1:
            raise ValueError("max_parallel must be positive")
        if type(max_delegations) is not int or max_delegations < 1:
            raise ValueError("max_delegations must be positive")
        if type(worker_timeout_s) not in (int, float) or not 0 < worker_timeout_s <= 3600:
            raise ValueError("invalid_worker_timeout")
        if not specialists:
            raise ValueError("at_least_one_specialist_required")
        self.specialists = dict(specialists)
        self.max_parallel = max_parallel
        self.max_delegations = max_delegations
        self.worker_timeout_s = float(worker_timeout_s)

    async def execute(
        self,
        tasks: tuple[DelegatedTask, ...],
        *,
        board: Blackboard,
        verifier: Verifier,
    ) -> TeamResult:
        validate_plan(tasks, self.specialists)
        completed: set[str] = set()
        events: list[TeamEvent] = []
        invocations, waves = 0, 0

        def finish(status: str, reason: str, check: Verification | None = None) -> TeamResult:
            events.append(TeamEvent("stopped", "", reason))
            return TeamResult(
                status, reason, board.run_id, board.snapshot(),
                tuple(t.task_id for t in tasks if t.task_id in completed),
                invocations, waves, tuple(events), check,
            )

        async def invoke(task: DelegatedTask, specialist: Specialist, inputs: Mapping[str, Artifact]) -> AgentReply:
            delegation = Delegation(
                board.run_id, task.task_id, task.agent_id, task.objective,
                task.inputs, task.outputs,
            )
            return await asyncio.wait_for(
                specialist.worker.act(delegation, inputs),
                timeout=self.worker_timeout_s,
            )

        while len(completed) < len(tasks):
            ready = [task for task in tasks
                     if task.task_id not in completed
                     and set(task.depends_on).issubset(completed)]
            if not ready:
                return finish("failed", "plan_stalled")
            wave = ready[:self.max_parallel]
            if invocations + len(wave) > self.max_delegations:
                return finish("failed", "delegation_budget_exhausted")

            tasks_ready = []
            try:
                for task in wave:
                    inputs = board.view(task.inputs)
                    versions = board.versions(task.outputs)
                    specialist = self.specialists[task.agent_id]
                    tasks_ready.append((task, specialist, inputs, versions))
            except CollaborationError as exc:
                return finish("failed", str(exc))

            waves += 1
            invocations += len(wave)
            for task in wave:
                events.append(TeamEvent("delegated", task.task_id, task.agent_id))
            results = await asyncio.gather(
                *(invoke(task, specialist, inputs)
                  for task, specialist, inputs, _ in tasks_ready),
                return_exceptions=True,
            )
            for task, reply in zip(wave, results):
                if isinstance(reply, BaseException):
                    events.append(TeamEvent("worker_failure", task.task_id, type(reply).__name__))
                    return finish("failed", "worker_failed_or_timed_out")
            try:
                board.commit_wave([
                    (task, specialist, reply, versions)
                    for (task, specialist, _, versions), reply in zip(tasks_ready, results)
                ])
            except StateConflict as exc:
                events.append(TeamEvent("write_conflict", ",".join(t.task_id for t in wave), str(exc)))
                return finish("failed", "shared_state_conflict")
            except CollaborationError as exc:
                events.append(TeamEvent("invalid_reply", ",".join(t.task_id for t in wave), str(exc)))
                return finish("failed", "invalid_worker_reply")
            completed.update(task.task_id for task in wave)
            events.append(TeamEvent("wave_committed", ",".join(t.task_id for t in wave), str(waves)))

        try:
            verification = verifier(board.snapshot())
            if not isinstance(verification, Verification):
                return finish("failed", "invalid_verification_response")
        except Exception as exc:
            events.append(TeamEvent("verifier_error", "", type(exc).__name__))
            return finish("failed", "verifier_error")
        if not verification.passed:
            return finish("failed", "independent_verification_failed", verification)
        return finish("verified", "independent_verification_passed", verification)
