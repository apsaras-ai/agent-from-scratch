"""Chapter 5: distinguish Task, Run, Step, Event and replay.

A completed chapter-4 Agent Kernel session is captured POST HOC.
The event store is ONLY in memory; this is NOT crash-safe execution.
"""
from mini_agent.agent_v2 import AgentKernel
from mini_agent.demo_tools import arithmetic_tools
from mini_agent.run_capture import capture_kernel_run
from mini_agent.run_domain import (
    EventType, InMemoryEventStore, RunEvent, TaskSpec, VersionConflict, emit, replay,
)
from examples.ch04_agent_loop import RuleBasedDemoModel


def main() -> None:
    task = TaskSpec("task-arithmetic", "Calculate (8 + 13) * 2")
    result = AgentKernel(
        RuleBasedDemoModel(), arithmetic_tools()
    ).run(task.objective)

    store = InMemoryEventStore()
    snapshot = capture_kernel_run(store, task, "run-001", result)
    print("Task:", task.task_id)
    print("Run:", snapshot.run_id)
    print("Status:", snapshot.status.value)
    print("Steps:", len(snapshot.steps))
    print("Events:", snapshot.version)
    print("Replay matches:", replay(store.events_for(snapshot.run_id)) == snapshot)
    print("Result:", snapshot.result)

    # Model a separate attempt to demonstrate waiting for human approval.
    rid = "run-approval"
    emit(store, rid, "1", EventType.RUN_CREATED, task_id=task.task_id)
    emit(store, rid, "2", EventType.RUN_STARTED)
    emit(store, rid, "3", EventType.RUN_PAUSED, reason="human approval needed")
    print("Approval state:", store.load(rid).status.value)
    emit(store, rid, "4", EventType.RUN_RESUMED)

    # Two actors read the same version. Exactly one append can win.
    expected = store.version(rid)
    store.append(
        RunEvent("5", rid, EventType.RUN_FAILED, {"reason": "demo_stop"}),
        expected_version=expected,
    )
    try:
        store.append(
            RunEvent("6", rid, EventType.RUN_CANCELLED, {"reason": "stale_writer"}),
            expected_version=expected,
        )
    except VersionConflict:
        print("Stale writer: rejected")

    print("Stored event history survives replay, but NOT process restart.")


if __name__ == "__main__":
    main()
