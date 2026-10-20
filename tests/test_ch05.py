"""Chapter 5: pure transition rules, event replay and concurrency contracts."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest import TestCase

from examples.ch04_agent_loop import RuleBasedDemoModel
from mini_agent.agent_v2 import AgentKernel
from mini_agent.demo_tools import arithmetic_tools
from mini_agent.run_capture import capture_kernel_run
from mini_agent.run_domain import (
    EventIdentityConflict, EventType, InMemoryEventStore,
    InvalidTransition, RunEvent, RunStatus, TaskSpec, VersionConflict,
    apply_event, emit, replay,
)


class DomainTests(TestCase):
    def setUp(self) -> None:
        self.store = InMemoryEventStore()

    def _append(self, run_id: str, event_id: str, kind: EventType, **data: str):
        return emit(self.store, run_id, event_id, kind, **data)

    def _running(self, run_id: str = "run-a") -> str:
        self._append(run_id, "created", EventType.RUN_CREATED, task_id="task-1")
        self._append(run_id, "started", EventType.RUN_STARTED)
        return run_id

    def test_task_is_distinct_from_run(self) -> None:
        task = TaskSpec("task-1", "Fix the failing test")
        self.assertEqual(task.task_id, "task-1")
        for run_id in ("run-a", "run-b"):
            state = self._append(
                run_id, "created", EventType.RUN_CREATED, task_id=task.task_id
            )
            self.assertEqual(state.task_id, task.task_id)
            self.assertEqual(state.run_id, run_id)
            self.assertEqual(state.version, 1)
        self.assertEqual(self.store.version("run-a"), 1)
        self.assertEqual(self.store.version("run-b"), 1)
        self.assertIsNone(self.store.load("unknown-run"))

    def test_running_steps_terminal_and_replay(self) -> None:
        run_id = self._running()
        self._append(
            run_id, "s1", EventType.STEP_STARTED,
            step_id="step-1", activity="model",
        )
        self._append(
            run_id, "s2", EventType.STEP_FINISHED,
            step_id="step-1", outcome="succeeded", detail="proposal",
        )
        self._append(
            run_id, "s3", EventType.STEP_STARTED,
            step_id="step-2", activity="tool",
        )
        self._append(
            run_id, "s4", EventType.STEP_FINISHED,
            step_id="step-2", outcome="failed", detail="tool_error",
        )
        ended = self._append(
            run_id, "final", EventType.RUN_COMPLETED, result="Reported failure"
        )
        self.assertEqual(ended.status, RunStatus.COMPLETED)
        self.assertEqual(ended.version, 7)
        self.assertEqual(
            [step.outcome for step in ended.steps], ["succeeded", "failed"]
        )
        self.assertEqual(replay(self.store.events_for(run_id)), ended)
        self.assertIsNone(ended.active_step_id)
        with self.assertRaises(InvalidTransition):
            self._append(run_id, "another", EventType.RUN_PAUSED, reason="too late")
        self.assertEqual(self.store.version(run_id), 7)

    def test_waiting_approval_and_resume(self) -> None:
        run_id = self._running()
        paused = self._append(
            run_id, "pause", EventType.RUN_PAUSED, reason="needs human approval"
        )
        self.assertEqual(paused.status, RunStatus.WAITING_APPROVAL)
        with self.assertRaises(InvalidTransition):
            self._append(
                run_id, "step", EventType.STEP_STARTED,
                step_id="step-1", activity="tool",
            )
        resumed = self._append(run_id, "resume", EventType.RUN_RESUMED)
        self.assertEqual(resumed.status, RunStatus.RUNNING)
        ended = self._append(
            run_id, "cancel", EventType.RUN_CANCELLED, reason="user cancelled"
        )
        self.assertEqual(ended.status, RunStatus.CANCELLED)

    def test_cannot_finish_with_active_step(self) -> None:
        run_id = self._running()
        self._append(
            run_id, "step", EventType.STEP_STARTED,
            step_id="step-1", activity="tool",
        )
        for kind, data in (
            (EventType.RUN_COMPLETED, {"result": "done"}),
            (EventType.RUN_FAILED, {"reason": "failed"}),
            (EventType.RUN_CANCELLED, {"reason": "cancel"}),
            (EventType.RUN_PAUSED, {"reason": "approval"}),
        ):
            with self.subTest(kind=kind), self.assertRaises(InvalidTransition):
                self._append(run_id, "invalid-" + kind.value, kind, **data)
        self.assertEqual(self.store.version(run_id), 3)
        with self.assertRaises(InvalidTransition):
            self._append(
                run_id, "overlap", EventType.STEP_STARTED,
                step_id="step-2", activity="model",
            )
        with self.assertRaises(InvalidTransition):
            self._append(
                run_id, "wrong-step", EventType.STEP_FINISHED,
                step_id="other", outcome="succeeded", detail="wrong",
            )
        self._append(
            run_id, "correct-finish", EventType.STEP_FINISHED,
            step_id="step-1", outcome="succeeded", detail="ok",
        )
        self.assertEqual(self.store.version(run_id), 4)

    def test_invalid_event_order_rejected(self) -> None:
        with self.assertRaises(InvalidTransition):
            self._append("run-empty", "start", EventType.RUN_STARTED)
        self.assertEqual(self.store.version("run-empty"), 0)
        self._append("run-a", "create", EventType.RUN_CREATED, task_id="t")
        with self.assertRaises(InvalidTransition):
            self._append("run-a", "create-2", EventType.RUN_CREATED, task_id="t")
        with self.assertRaises(InvalidTransition):
            self._append("run-a", "complete-early", EventType.RUN_COMPLETED, result="x")
        self.assertEqual(self.store.version("run-a"), 1)

    def test_idempotent_identical_event_and_conflicting_payload(self) -> None:
        event = RunEvent("event-1", "run-x", EventType.RUN_CREATED, {"task_id": "task"})
        first = self.store.append(event, expected_version=0)
        again = self.store.append(event, expected_version=0)
        self.assertEqual(first, again)
        self.assertEqual(self.store.version("run-x"), 1)
        with self.assertRaises(EventIdentityConflict):
            self.store.append(
                RunEvent("event-1", "run-x", EventType.RUN_CREATED, {"task_id": "other"}),
                expected_version=1,
            )

    def test_stale_writer_is_rejected(self) -> None:
        run_id = self._running()
        stale = self.store.version(run_id)
        self.store.append(
            RunEvent("pause", run_id, EventType.RUN_PAUSED, {"reason": "approval"}),
            expected_version=stale,
        )
        with self.assertRaises(VersionConflict):
            self.store.append(
                RunEvent("fail", run_id, EventType.RUN_FAILED, {"reason": "network"}),
                expected_version=stale,
            )
        self.assertEqual(self.store.version(run_id), stale + 1)

    def test_store_defensive_copy(self) -> None:
        data = {"task_id": "task-original"}
        event = RunEvent("create", "run-a", EventType.RUN_CREATED, data)
        self.store.append(event, expected_version=0)
        data["task_id"] = "mutated"
        fetched = self.store.events_for("run-a")
        fetched[0].data["task_id"] = "also-mutated"
        self.assertEqual(self.store.load("run-a").task_id, "task-original")

    def test_validate_field_contracts(self) -> None:
        invalid = (
            ("", "run", EventType.RUN_STARTED, {}),
            ("e", "", EventType.RUN_STARTED, {}),
            ("e", "run", EventType.RUN_CREATED, {}),
            ("e", "run", EventType.RUN_CREATED, {"task_id": ""}),
            ("e", "run", EventType.STEP_STARTED,
             {"step_id": "s", "activity": "browser"}),
            ("e", "run", EventType.STEP_FINISHED,
             {"step_id": "s", "outcome": "unknown", "detail": "x"}),
        )
        for event_id, run_id, kind, data in invalid:
            with self.subTest(kind=kind, data=data), self.assertRaises(ValueError):
                RunEvent(event_id, run_id, kind, data)
        with self.assertRaises(ValueError):
            TaskSpec("", "goal")
        with self.assertRaises(ValueError):
            self.store.append(
                RunEvent("e", "r", EventType.RUN_CREATED, {"task_id": "t"}),
                expected_version=True,
            )

    def test_replay_is_pure_and_does_not_invoke_tools(self) -> None:
        events = [
            RunEvent("c", "run", EventType.RUN_CREATED, {"task_id": "task"}),
            RunEvent("s", "run", EventType.RUN_STARTED, {}),
            RunEvent("done", "run", EventType.RUN_COMPLETED, {"result": "ok"}),
        ]
        self.assertEqual(replay(events).status, RunStatus.COMPLETED)
        self.assertEqual(replay(events), replay(events))
        with self.assertRaises(InvalidTransition):
            apply_event(replay(events), RunEvent(
                "again", "run", EventType.RUN_STARTED, {},
            ))


class IntegrationTests(TestCase):
    def test_capture_existing_agent_kernel_trace(self) -> None:
        store = InMemoryEventStore()
        task = TaskSpec("task-42", "Calculate (8 + 13) * 2")
        result = AgentKernel(
            RuleBasedDemoModel(), arithmetic_tools(),
        ).run(task.objective)
        snapshot = capture_kernel_run(store, task, "run-1", result)
        self.assertEqual(snapshot.status, RunStatus.COMPLETED)
        self.assertEqual(snapshot.result, "Answer: 42")
        self.assertEqual([s.activity for s in snapshot.steps],
                         ["model", "tool", "model", "tool", "model"])
        self.assertTrue(all(s.outcome == "succeeded" for s in snapshot.steps))
        self.assertEqual(snapshot.version, 13)
        self.assertEqual(snapshot, replay(store.events_for("run-1")))
        with self.assertRaisesRegex(ValueError, "fresh run_id"):
            capture_kernel_run(store, task, "run-1", result)

    def test_halted_run_is_failed_not_completed(self) -> None:
        class EndlessToolModel:
            def complete(self, messages, tools):
                from mini_agent.agent_v2 import ModelReply, ToolCall
                return ModelReply(
                    tool_calls=(ToolCall("a", "add", '{"a":1,"b":2}'),),
                    finish_reason="tool_calls",
                )

        store = InMemoryEventStore()
        result = AgentKernel(
            EndlessToolModel(), arithmetic_tools(), max_model_calls=1,
        ).run("Compute")
        self.assertEqual(result.status, "halted")
        snapshot = capture_kernel_run(
            store, TaskSpec("t", "Compute"), "r", result,
        )
        self.assertEqual(snapshot.status, RunStatus.FAILED)
        self.assertEqual(snapshot.reason, "max_model_calls")
        self.assertEqual(snapshot.steps, ())


if __name__ == "__main__":
    import unittest
    unittest.main()
