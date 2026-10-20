"""Chapter 5: Event-sourced RUN *domain model* (in-memory teaching version).

Separate task intent, one execution attempt, its steps, and immutable event
identities. The projector is pure and has NO tool/model side effects.
This is not durable execution: events disappear when the process exits.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock
from typing import Any, Iterable


class VersionConflict(RuntimeError):
    """The caller's expected run version is stale."""


class InvalidTransition(ValueError):
    """An event violates the run's state-machine invariants."""


class EventIdentityConflict(ValueError):
    """An event ID was previously used with different data."""


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(str, Enum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    STEP_STARTED = "step.started"
    STEP_FINISHED = "step.finished"
    RUN_PAUSED = "run.paused"
    RUN_RESUMED = "run.resumed"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"


_REQUIRED: dict[EventType, frozenset[str]] = {
    EventType.RUN_CREATED: frozenset({"task_id"}),
    EventType.RUN_STARTED: frozenset(),
    EventType.STEP_STARTED: frozenset({"step_id", "activity"}),
    EventType.STEP_FINISHED: frozenset({"step_id", "outcome", "detail"}),
    EventType.RUN_PAUSED: frozenset({"reason"}),
    EventType.RUN_RESUMED: frozenset(),
    EventType.RUN_COMPLETED: frozenset({"result"}),
    EventType.RUN_FAILED: frozenset({"reason"}),
    EventType.RUN_CANCELLED: frozenset({"reason"}),
}


@dataclass(frozen=True)
class TaskSpec:
    """What should be done; multiple runs may use the same task_id."""
    task_id: str
    objective: str

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a nonempty string")
        if not isinstance(self.objective, str) or not self.objective.strip():
            raise ValueError("objective must be a nonempty string")


@dataclass(frozen=True)
class RunEvent:
    event_id: str
    run_id: str
    kind: EventType
    data: dict[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be nonempty")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id must be nonempty")
        if not isinstance(self.kind, EventType):
            raise ValueError("kind must be a supported EventType")
        if not isinstance(self.data, dict) or set(self.data) != _REQUIRED[self.kind]:
            raise ValueError(f"Invalid data fields for {self.kind.value}")
        if any(type(value) is not str or not value.strip() for value in self.data.values()):
            raise ValueError("Event data values must be nonempty strings")
        if self.kind is EventType.STEP_STARTED and self.data["activity"] not in {"model", "tool"}:
            raise ValueError("activity must be model or tool")
        if self.kind is EventType.STEP_FINISHED and self.data["outcome"] not in {"succeeded", "failed"}:
            raise ValueError("outcome must be succeeded or failed")


@dataclass(frozen=True)
class StepState:
    step_id: str
    activity: str
    outcome: str = "active"
    detail: str = ""


@dataclass(frozen=True)
class RunState:
    run_id: str
    task_id: str
    status: RunStatus
    version: int
    steps: tuple[StepState, ...] = ()
    active_step_id: str | None = None
    result: str | None = None
    reason: str | None = None


def apply_event(state: RunState | None, event: RunEvent) -> RunState:
    """Pure state transition; rejects impossible event orders."""
    kind = event.kind
    d = event.data
    if state is None:
        if kind is not EventType.RUN_CREATED:
            raise InvalidTransition("First event must be run.created")
        return RunState(event.run_id, d["task_id"], RunStatus.CREATED, 1)

    if state.run_id != event.run_id:
        raise InvalidTransition("Cross-run event in one event stream")
    if kind is EventType.RUN_CREATED:
        raise InvalidTransition("Run has already been created")
    if state.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
        raise InvalidTransition("Terminal run cannot be changed")
    next_version = state.version + 1

    if kind is EventType.RUN_STARTED:
        if state.status is not RunStatus.CREATED:
            raise InvalidTransition("Only created run may start")
        return replace(state, version=next_version, status=RunStatus.RUNNING)

    if kind is EventType.STEP_STARTED:
        if state.status is not RunStatus.RUNNING or state.active_step_id is not None:
            raise InvalidTransition("Cannot start overlapping or inactive steps")
        if any(step.step_id == d["step_id"] for step in state.steps):
            raise InvalidTransition("step_id must be unique within a run")
        step = StepState(d["step_id"], d["activity"])
        return replace(
            state, version=next_version, steps=state.steps + (step,),
            active_step_id=d["step_id"],
        )

    if kind is EventType.STEP_FINISHED:
        if state.status is not RunStatus.RUNNING or state.active_step_id != d["step_id"]:
            raise InvalidTransition("Cannot finish an unstarted/different step")
        steps = tuple(
            replace(step, outcome=d["outcome"], detail=d["detail"])
            if step.step_id == d["step_id"] else step
            for step in state.steps
        )
        return replace(state, version=next_version, steps=steps, active_step_id=None)

    if kind is EventType.RUN_PAUSED:
        if state.status is not RunStatus.RUNNING or state.active_step_id is not None:
            raise InvalidTransition("Can pause only between steps while running")
        return replace(
            state, version=next_version, status=RunStatus.WAITING_APPROVAL,
            reason=d["reason"],
        )

    if kind is EventType.RUN_RESUMED:
        if state.status is not RunStatus.WAITING_APPROVAL:
            raise InvalidTransition("Only paused run may resume")
        return replace(state, version=next_version, status=RunStatus.RUNNING, reason=None)

    if kind is EventType.RUN_COMPLETED:
        if state.status is not RunStatus.RUNNING or state.active_step_id is not None:
            raise InvalidTransition("Cannot complete run with an active step")
        return replace(
            state, version=next_version, status=RunStatus.COMPLETED, result=d["result"],
        )

    if kind is EventType.RUN_FAILED:
        if state.status not in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
            raise InvalidTransition("Cannot fail a run in this state")
        if state.active_step_id is not None:
            raise InvalidTransition("Close active step before failing run")
        return replace(
            state, version=next_version, status=RunStatus.FAILED, reason=d["reason"],
        )

    if kind is EventType.RUN_CANCELLED:
        if state.active_step_id is not None:
            raise InvalidTransition("Close active step before cancelling run")
        return replace(
            state, version=next_version, status=RunStatus.CANCELLED, reason=d["reason"],
        )

    raise InvalidTransition(f"Unsupported event: {kind}")


def replay(events: Iterable[RunEvent]) -> RunState | None:
    """Recreate domain state from immutable event facts, without tool execution."""
    state: RunState | None = None
    for event in events:
        state = apply_event(state, event)
    return state


class InMemoryEventStore:
    """Atomic within one Python process; optimistic expected-version check.

    The event ID is unique *within a run*. A retried identical append is
    idempotent at this store boundary. It cannot make external tools exactly
    once or survive a process crash. This is a learning aid, not a database.
    """

    def __init__(self) -> None:
        self._streams: dict[str, tuple[RunEvent, ...]] = {}
        self._lock = RLock()

    def version(self, run_id: str) -> int:
        with self._lock:
            return len(self._streams.get(run_id, ()))

    def load(self, run_id: str) -> RunState | None:
        with self._lock:
            return replay(deepcopy(self._streams.get(run_id, ())))

    def events_for(self, run_id: str) -> tuple[RunEvent, ...]:
        with self._lock:
            return deepcopy(self._streams.get(run_id, ()))

    def append(self, event: RunEvent, *, expected_version: int) -> RunState:
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("expected_version must be a nonnegative integer")
        with self._lock:
            previous = self._streams.get(event.run_id, ())
            for stored in previous:
                if stored.event_id == event.event_id:
                    if stored != event:
                        raise EventIdentityConflict(
                            "Reused event_id has different payload"
                        )
                    # Return CURRENT materialized state, not the historical
                    # snapshot from the original append.
                    state = replay(deepcopy(previous))
                    assert state is not None
                    return state
            if expected_version != len(previous):
                raise VersionConflict(
                    f"run {event.run_id} expected version {expected_version}, "
                    f"actual version {len(previous)}"
                )
            # Validate the prospective transition before changing the store.
            updated = replay(previous + (deepcopy(event),))
            assert updated is not None
            self._streams[event.run_id] = previous + (deepcopy(event),)
            return updated


def emit(
    store: InMemoryEventStore,
    run_id: str,
    event_id: str,
    kind: EventType,
    **data: str,
) -> RunState:
    """Convenience helper for a single-writer demo (not atomic read + append).

    Concurrent writers must pass their own expected_version or retry on
    VersionConflict with a fresh business decision.
    """
    event = RunEvent(event_id, run_id, kind, data)
    return store.append(event, expected_version=store.version(run_id))
