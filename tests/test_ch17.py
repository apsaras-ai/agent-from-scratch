"""Chapter 17: delegation, least-privilege context, CAS, and verification.

No real LLM calls; no real external side effects. Workers run in-process and
are NOT sandboxed. This test suite validates orchestration contracts only.
"""
from __future__ import annotations

import asyncio
import json
import unittest

from mini_agent.agent_v2 import ModelReply, ToolCall
from mini_agent.multi_agent import (
    AgentIdentity, AgentReply, Artifact, Blackboard, CollaborationError,
    DelegatedTask, Delegation, HostResolution, ProposedArtifact, Specialist,
    StateConflict, Supervisor, Verification, validate_plan,
)
from mini_agent.multi_agent_fixture import (
    ActivityMeter, EXPECTED_BRIEF, make_plan, make_team,
    run_fixture, seeded_board, verifier,
)
from mini_agent.multi_agent_model import ModelSpecialist


class EchoWorker:
    def __init__(self, *, content="ok", task_id_override=None, delay=0.0):
        self.content = content
        self.task_id_override = task_id_override
        self.delay = delay
        self.seen = []

    async def act(self, delegation, inputs):
        self.seen.append((delegation, dict(inputs)))
        if self.delay:
            await asyncio.sleep(self.delay)
        return AgentReply(
            self.task_id_override or delegation.task_id,
            (ProposedArtifact(delegation.output_keys[0], self.content),),
            summary="I claim the result is verified",
        )


def one_task(*, agent="worker", output="result", inputs=()):
    return (DelegatedTask(
        "task", agent, "Write an attributed result", inputs, (output,),
    ),)


def specialist(agent="worker", *, read=(), write=("result",), worker=None):
    return Specialist(
        AgentIdentity(agent, frozenset(read), frozenset(write)),
        worker or EchoWorker(),
    )


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_and_multi_same_verified_output_with_different_waves(self):
        one = await run_fixture(specialized=False)
        team = await run_fixture(specialized=True)
        self.assertEqual(one.result.status, "verified")
        self.assertEqual(team.result.status, "verified")
        self.assertEqual(one.result.verification.evidence, "host_fixture_checked")
        self.assertEqual(one.result.artifacts["research_brief"].content, EXPECTED_BRIEF)
        self.assertEqual(team.result.artifacts["research_brief"].content, EXPECTED_BRIEF)
        self.assertEqual((one.result.waves, team.result.waves), (3, 2))
        self.assertEqual((one.result.worker_invocations, team.result.worker_invocations), (3, 3))
        self.assertEqual((one.peak_concurrent_io, team.peak_concurrent_io), (1, 2))

    async def test_only_declared_inputs_visible_to_each_worker(self):
        meter = ActivityMeter()
        team = make_team(meter, specialized=True)
        board = seeded_board("scoped")
        board.seed("private_secret", "should never be sent")
        result = await Supervisor(team, max_parallel=2).execute(
            make_plan(specialized=True), board=board, verifier=verifier,
        )
        self.assertEqual(result.status, "verified")
        self.assertEqual(team["api"].worker.received, [("lookup-api", ("raw_api",))])
        self.assertEqual(team["tests"].worker.received, [("lookup-tests", ("raw_tests",))])
        self.assertEqual(team["writer"].worker.received, [
            ("compose", ("api_finding", "test_finding"))
        ])

    async def test_shared_state_mapping_is_readonly_and_run_scoped(self):
        board = Blackboard("run-a")
        board.seed("key", "host input")
        view = board.snapshot()
        with self.assertRaises(TypeError):
            view["key"] = "attacker"
        self.assertEqual(view["key"].run_id, "run-a")
        self.assertEqual(board.view(("key",))["key"].content, "host input")
        with self.assertRaises(CollaborationError):
            board.view(("not-authorized-or-missing",))
        with self.assertRaises(StateConflict):
            board.seed("key", "rewrite")

    async def test_duplicate_task_and_dependency_cycles_fail_before_workers(self):
        w = EchoWorker()
        agent = specialist(worker=w)
        invalid = [
            (one_task()[0], one_task()[0]),
            (DelegatedTask("a", "worker", "a", (), ("result",), ("missing",)),),
            (DelegatedTask("a", "worker", "a", (), ("result",), ("b",)),
             DelegatedTask("b", "worker", "b", (), ("result",), ("a",))),
            (DelegatedTask("a", "worker", "a", (), ("result",), ("a",)),),
        ]
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(CollaborationError):
                validate_plan(plan, {"worker": agent})
        self.assertEqual(w.seen, [])

    async def test_agent_capability_rejects_unauthorized_read_and_write(self):
        team = {"worker": specialist(read=("public",), write=("result",))}
        invalid = (
            one_task(inputs=("secret",)),
            one_task(output="root_password"),
            one_task(agent="unknown"),
        )
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(CollaborationError):
                validate_plan(plan, team)

    async def test_worker_cannot_add_undeclared_output_and_wave_atomic(self):
        class MaliciousWorker:
            async def act(self, delegation, inputs):
                return AgentReply(
                    delegation.task_id,
                    (ProposedArtifact("result", "ok"),
                     ProposedArtifact("secrets", "exfiltrate")),
                )

        board = Blackboard("bad-writes")
        team = {"worker": specialist(worker=MaliciousWorker())}
        result = await Supervisor(team).execute(
            one_task(), board=board,
            verifier=lambda _: Verification(True, "not_executed"),
        )
        self.assertEqual((result.status, result.reason), ("failed", "invalid_worker_reply"))
        self.assertNotIn("result", result.artifacts)
        self.assertNotIn("secrets", result.artifacts)
        self.assertIsNone(result.verification)

    async def test_wrong_task_id_in_worker_reply_is_rejected(self):
        worker = EchoWorker(task_id_override="other")
        result = await Supervisor({"worker": specialist(worker=worker)}).execute(
            one_task(), board=Blackboard("wrong-id"),
            verifier=lambda _: Verification(True, "no"),
        )
        self.assertEqual(result.reason, "invalid_worker_reply")
        self.assertEqual(len(result.artifacts), 0)

    async def test_write_conflict_rejects_entire_wave(self):
        a, b = EchoWorker(content="A"), EchoWorker(content="B")
        agents = {
            "a": specialist("a", worker=a, write=("shared",)),
            "b": specialist("b", worker=b, write=("shared",)),
        }
        tasks = (
            DelegatedTask("ta", "a", "a", (), ("shared",)),
            DelegatedTask("tb", "b", "b", (), ("shared",)),
        )
        board = Blackboard("collision")
        result = await Supervisor(agents, max_parallel=2).execute(
            tasks, board=board, verifier=lambda _: Verification(True, "not reached"),
        )
        self.assertEqual(result.reason, "shared_state_conflict")
        self.assertEqual(result.worker_invocations, 2)
        self.assertEqual(result.artifacts, {})
        self.assertIsNone(result.verification)
        self.assertTrue(any(e.kind == "write_conflict" for e in result.events))
        self.assertEqual(len(a.seen), 1)
        self.assertEqual(len(b.seen), 1)

    async def test_explicit_host_resolution_requires_cas_and_rationale(self):
        board = Blackboard("host-r")
        chosen = board.reconcile(HostResolution(
            "host-r", "shared", "Reviewed answer", None,
            "host-admin", "Manually checked the synthetic evidence",
        ))
        self.assertEqual((chosen.version, chosen.producer_task), (1, "resolution"))
        with self.assertRaises(StateConflict):
            board.reconcile(HostResolution(
                "host-r", "shared", "outdated judgment", None,
                "host-admin", "stale",
            ))
        next_version = board.reconcile(HostResolution(
            "host-r", "shared", "Corrected answer", 1,
            "host-admin", "new evidence",
        ))
        self.assertEqual(next_version.version, 2)
        with self.assertRaises(CollaborationError):
            board.reconcile(HostResolution(
                "wrong-run", "shared", "bad", 2, "host-admin", "wrong run",
            ))

    async def test_stale_revision_between_observe_and_commit_rejected(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class Waiter:
            async def act(self, delegation, inputs):
                started.set()
                await release.wait()
                return AgentReply(delegation.task_id, (ProposedArtifact("result", "worker"),))

        board = Blackboard("stale")
        run = asyncio.create_task(Supervisor(
            {"worker": specialist(worker=Waiter())}
        ).execute(one_task(), board=board,
                  verifier=lambda _: Verification(True, "irrelevant")))
        await asyncio.wait_for(started.wait(), 1)
        board.reconcile(HostResolution(
            "stale", "result", "host decided", None, "host", "manual update",
        ))
        release.set()
        result = await run
        self.assertEqual(result.reason, "shared_state_conflict")
        self.assertEqual(board.snapshot()["result"].content, "host decided")
        self.assertEqual(board.snapshot()["result"].version, 1)

    async def test_one_worker_failure_causes_no_wave_commit(self):
        class Broken:
            async def act(self, delegation, inputs):
                raise RuntimeError("internal token or secret")

        agents = {
            "good": specialist("good", write=("one",), worker=EchoWorker()),
            "broken": specialist("broken", write=("two",), worker=Broken()),
        }
        tasks = (DelegatedTask("a", "good", "a", (), ("one",)),
                 DelegatedTask("b", "broken", "b", (), ("two",)))
        result = await Supervisor(agents, max_parallel=2).execute(
            tasks, board=Blackboard("failure"),
            verifier=lambda _: Verification(True, "should not run"),
        )
        self.assertEqual(result.reason, "worker_failed_or_timed_out")
        self.assertEqual(result.artifacts, {})
        self.assertNotIn("internal token", str(result.events))

    async def test_worker_timeout_is_not_success_or_rollback(self):
        slow = EchoWorker(delay=.1)
        result = await Supervisor(
            {"worker": specialist(worker=slow)},
            worker_timeout_s=.01,
        ).execute(one_task(), board=Blackboard("timeout"),
                  verifier=lambda _: Verification(True, "not reached"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "worker_failed_or_timed_out")
        self.assertNotIn("result", result.artifacts)

    async def test_budget_refuses_partial_delegation_wave(self):
        agents = {
            "a": specialist("a", write=("one",)),
            "b": specialist("b", write=("two",)),
        }
        tasks = (DelegatedTask("a", "a", "a", (), ("one",)),
                 DelegatedTask("b", "b", "b", (), ("two",)))
        board = Blackboard("budget")
        result = await Supervisor(
            agents, max_parallel=2, max_delegations=1,
        ).execute(tasks, board=board,
                  verifier=lambda _: Verification(True, "not reached"))
        self.assertEqual(result.reason, "delegation_budget_exhausted")
        self.assertEqual(result.worker_invocations, 0)
        self.assertFalse(board.snapshot())

    async def test_independent_verifier_rejects_self_reported_success(self):
        result = await Supervisor({
            "worker": specialist(worker=EchoWorker(content="MISLEADING")),
        }).execute(one_task(), board=Blackboard("bad-verification"),
                  verifier=lambda items: Verification(
                      items["result"].content == "expected",
                      "checked_external_fixture",
                  ))
        self.assertEqual(result.reason, "independent_verification_failed")
        self.assertFalse(result.verification.passed)
        self.assertEqual(result.artifacts["result"].content, "MISLEADING")

    async def test_verifier_exception_not_fake_success(self):
        def broken_verifier(_):
            raise RuntimeError("verification not available")
        result = await Supervisor({
            "worker": specialist(),
        }).execute(one_task(), board=Blackboard("verifier-down"),
                  verifier=broken_verifier)
        self.assertEqual(result.reason, "verifier_error")
        self.assertIsNone(result.verification)

    async def test_artifact_validation_and_atomicity(self):
        class Overlong:
            async def act(self, delegation, inputs):
                return AgentReply(
                    delegation.task_id,
                    (ProposedArtifact("result", "x" * 101),),
                )
        result = await Supervisor({
            "worker": specialist(worker=Overlong()),
        }).execute(one_task(), board=Blackboard("limit", max_content_chars=100),
                  verifier=lambda _: Verification(True, "no"))
        self.assertEqual(result.reason, "invalid_worker_reply")
        self.assertNotIn("result", result.artifacts)


class ModelSpecialistTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def delegation():
        return Delegation("run", "task", "agent", "Write a note",
                          ("public",), ("note",))

    @staticmethod
    def good_payload():
        return {
            "task_id": "task", "summary": "Unverified draft",
            "writes": [{
                "key": "note", "content": "A sourced note",
                "evidence_ids": ["fixture://source"],
            }],
        }

    async def test_provider_model_adapter_uses_json_text_only_and_empty_tools(self):
        class FakeModel:
            def __init__(self):
                self.seen = []

            def complete(self, messages, tools):
                self.seen.append((messages, tools))
                return ModelReply(content=json.dumps(ModelSpecialistTests.good_payload()))

        model = FakeModel()
        worker = ModelSpecialist(model)
        visible = {"public": Artifact("public", "data", "run", "seed", "host", 1, ("fixture://source",))}
        reply = await worker.act(self.delegation(), visible)
        self.assertEqual(reply.task_id, "task")
        self.assertEqual(reply.writes[0].key, "note")
        self.assertEqual(model.seen[0][1], [])
        self.assertNotIn("unrelated_secret", model.seen[0][0][1]["content"])

    async def test_model_writer_through_supervisor(self):
        class Model:
            def complete(self, messages, tools):
                payload = ModelSpecialistTests.good_payload()
                return ModelReply(content=json.dumps(payload))
        board = Blackboard("model-worker")
        board.seed("public", "fixture data")
        agent = Specialist(
            AgentIdentity("agent", frozenset({"public"}), frozenset({"note"})),
            ModelSpecialist(Model()),
        )
        result = await Supervisor({"agent": agent}).execute(
            (DelegatedTask("task", "agent", "Write note", ("public",), ("note",)),),
            board=board,
            verifier=lambda board: Verification(
                board["note"].content == "A sourced note", "checked_fixture",
            ),
        )
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.artifacts["note"].producer_agent, "agent")

    async def test_malformed_json_or_extra_power_is_rejected(self):
        examples = [
            '{not json',
            '{"task_id":"task","task_id":"other","writes":[],"summary":""}',
            '{"task_id":"task","writes":[],"summary":"","override_policy":true}',
            '{"task_id":"task","writes":[],"summary":"","tokens":NaN}',
            '{"task_id":"task","writes":[{"key":"note","content":"text","evidence_ids":"abc"}],"summary":""}',
        ]
        for raw in examples:
            class FakeModel:
                def complete(self, messages, tools):
                    return ModelReply(content=raw)
            with self.subTest(raw=raw), self.assertRaises(CollaborationError):
                await ModelSpecialist(FakeModel()).act(self.delegation(), {})

    async def test_tool_call_or_refusal_never_becomes_delegated_action(self):
        replies = [
            ModelReply(
                tool_calls=(ToolCall("id", "shell", "{}"),),
                finish_reason="tool_calls",
            ),
            ModelReply(content=None, finish_reason="refusal"),
            ModelReply(content="partial", finish_reason="length"),
        ]
        for answer in replies:
            class FakeModel:
                def complete(self, messages, tools):
                    return answer
            with self.subTest(answer=answer), self.assertRaises(CollaborationError):
                await ModelSpecialist(FakeModel()).act(self.delegation(), {})

    async def test_wrong_task_or_unapproved_write_rejected_by_supervisor(self):
        class FakeModel:
            def complete(self, messages, tools):
                payload = ModelSpecialistTests.good_payload()
                payload["writes"][0]["key"] = "unauthorized"
                return ModelReply(content=json.dumps(payload))
        board = Blackboard("scope")
        result = await Supervisor({
            "agent": Specialist(
                AgentIdentity("agent", frozenset(), frozenset({"note"})),
                ModelSpecialist(FakeModel()),
            ),
        }).execute(
            (DelegatedTask("task", "agent", "Only write note", (), ("note",)),),
            board=board, verifier=lambda _: Verification(True, "no"),
        )
        self.assertEqual(result.reason, "invalid_worker_reply")
        self.assertNotIn("unauthorized", result.artifacts)


if __name__ == "__main__":
    unittest.main()
