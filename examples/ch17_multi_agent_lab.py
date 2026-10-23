"""Chapter 17: reproducible single-worker vs multi-specialist comparison.

    python examples/ch17_multi_agent_lab.py

All sources, workers and acceptance tests are synthetic and deterministic.
Elapsed timings are illustrative I/O-delay measurements, not LLM benchmarks.
"""
from __future__ import annotations

import asyncio

from mini_agent.multi_agent import (
    AgentIdentity, AgentReply, Blackboard, DelegatedTask, HostResolution,
    ProposedArtifact, Specialist, Supervisor, Verification,
)
from mini_agent.multi_agent_fixture import run_fixture


async def conflicting_writes_demo() -> None:
    class Writer:
        def __init__(self, message):
            self.message = message

        async def act(self, delegation, inputs):
            return AgentReply(
                delegation.task_id,
                (ProposedArtifact("summary", self.message),),
            )

    specialists = {
        "a": Specialist(
            AgentIdentity("a", frozenset(), frozenset({"summary"})),
            Writer("The answer is A"),
        ),
        "b": Specialist(
            AgentIdentity("b", frozenset(), frozenset({"summary"})),
            Writer("The answer is B"),
        ),
    }
    tasks = (
        DelegatedTask("task-a", "a", "Draft answer A", (), ("summary",)),
        DelegatedTask("task-b", "b", "Draft answer B", (), ("summary",)),
    )
    board = Blackboard("conflict-demo")
    result = await Supervisor(specialists, max_parallel=2).execute(
        tasks, board=board, verifier=lambda _: Verification(True, "not_reached"),
    )
    assert result.reason == "shared_state_conflict"
    assert "summary" not in result.artifacts
    print("Conflicting writes:", result.reason, "automatic_commit=", False)

    # An explicitly trusted host resolves ambiguity with version checking.
    resolved = board.reconcile(HostResolution(
        run_id="conflict-demo", key="summary", content="Reviewed answer: A",
        expected_version=None, decided_by="host-reviewer",
        rationale="Compared synthetic sources and approved variant A",
    ))
    print("Host resolution:", resolved.content, "version=", resolved.version)


async def main() -> None:
    single = await run_fixture(specialized=False)
    multi = await run_fixture(specialized=True)

    for name, experiment in (("single", single), ("multi", multi)):
        run = experiment.result
        print(
            f"{name}: status={run.status} waves={run.waves} "
            f"worker_invocations={run.worker_invocations} "
            f"peak_concurrent_io={experiment.peak_concurrent_io} "
            f"elapsed_ms={experiment.elapsed_ms:.1f}"
        )
        print(f"  verifier: {run.verification.evidence if run.verification else run.reason}")

    assert single.result.status == multi.result.status == "verified"
    assert single.result.artifacts["research_brief"].content == multi.result.artifacts["research_brief"].content
    assert single.result.waves == 3 and multi.result.waves == 2
    assert single.peak_concurrent_io == 1 and multi.peak_concurrent_io == 2
    print("The measured overlap is for artificial I/O, not a quality advantage.")
    await conflicting_writes_demo()


if __name__ == "__main__":
    asyncio.run(main())
