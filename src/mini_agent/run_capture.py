"""Chapter 5 bridge: project a completed Chapter-4 run into domain events.

This is POST-HOC recording: it does NOT durably record live execution, cannot
resume a crashed tool, and must not be confused with Temporal-style replay.
"""
from __future__ import annotations

import json
from typing import Any

from .agent_v2 import RunResult
from .run_domain import (
    EventType, InMemoryEventStore, RunState, TaskSpec, emit,
)


def capture_kernel_run(
    store: InMemoryEventStore,
    task: TaskSpec,
    run_id: str,
    result: RunResult,
) -> RunState:
    """Translate *observed* transcript into a safe-to-replay state projection.

    Events describe actions that have ALREADY happened; replaying these
    events reconstructs domain state without re-calling any model or tool.
    """
    if store.load(run_id) is not None:
        raise ValueError("This example only records a fresh run_id")
    event_number = 0
    step_number = 0

    def record(kind: EventType, **data: str) -> RunState:
        nonlocal event_number
        event_number += 1
        return emit(store, run_id, f"event-{event_number}", kind, **data)

    def step(activity: str, outcome: str, detail: str) -> None:
        nonlocal step_number
        step_number += 1
        step_id = f"step-{step_number}"
        record(EventType.STEP_STARTED, step_id=step_id, activity=activity)
        record(
            EventType.STEP_FINISHED,
            step_id=step_id,
            outcome=outcome,
            detail=detail,
        )

    record(EventType.RUN_CREATED, task_id=task.task_id)
    record(EventType.RUN_STARTED)
    pending_ids: list[str] = []
    models_seen = 0
    tools_seen = 0

    for message in result.messages:
        role = message["role"]
        if role in ("system", "user"):
            continue
        if role == "assistant":
            if pending_ids:
                raise ValueError("Assistant response before tool results")
            models_seen += 1
            calls = message.get("tool_calls") or []
            step("model", "succeeded", "tool_proposal" if calls else "final_text")
            pending_ids = [item["id"] for item in calls]
        elif role == "tool":
            call_id = message["tool_call_id"]
            if not pending_ids or call_id != pending_ids.pop(0):
                raise ValueError("Mismatched or orphan tool result")
            try:
                observation: Any = json.loads(message["content"])
                ok = observation["ok"]
                if type(ok) is not bool:
                    raise TypeError("tool ok must be bool")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Unrecognized tool observation") from exc
            tools_seen += 1
            step("tool", "succeeded" if ok else "failed", "ok" if ok else "error")
        else:
            raise ValueError(f"Unrecognized message role {role!r}")

    if pending_ids:
        raise ValueError("Transcript ended with unresolved tool requests")
    # A halted model may fail before producing an assistant message.
    if models_seen > result.model_calls or tools_seen != result.tool_calls:
        raise ValueError("RunResult counts do not match transcript")

    if result.status == "completed":
        if result.output is None:
            raise ValueError("Completed result must include final output")
        return record(EventType.RUN_COMPLETED, result=result.output)
    if result.status == "halted":
        return record(EventType.RUN_FAILED, reason=result.reason)
    raise ValueError("Unsupported RunResult status")
