"""Chapter 33: genuine offline integration of the book's existing components.

- AgentKernel → ModelGateway fixture → scoped memory/context → read-only tool
- independent Host Verifier → privacy-safe TraceRecorder → SQLite receipt
- read after new SQLite connection, denied guessed success, uncertain crash
No LLM API, third-party code, shell, network, or production sandbox.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from mini_agent.agent_v2 import ModelReply
from mini_agent.integrated_runtime import (
    LookupRequest, MiniAgentRuntime, SQLiteRunJournal, TrustedFactCatalog,
)
from mini_agent.memory_service import Access, MemoryRecord, MemoryService
from mini_agent.model_gateway import GatewayResponse, TokenUsage


class GuessOnlyBackend:
    """Deliberately wrong policy: final guess bypasses the trusted lookup."""
    name = "fixture"
    def complete(self, messages, tools):
        return GatewayResponse(
            ModelReply(content="READY", finish_reason="stop"),
            TokenUsage(), "fixture", "fixture-planner",
        )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ch33_runtime_") as temporary:
        path = Path(temporary) / "run_journal.sqlite"
        journal = SQLiteRunJournal(path)
        catalog = TrustedFactCatalog({
            ("demo", "alice", "alpha"): "READY",
            ("demo", "alice", "beta"): "REVIEWED",
            ("demo", "bob", "alpha"): "BOB_PRIVATE",
        })
        memory = MemoryService()
        now = datetime.now(timezone.utc)
        memory.write(MemoryRecord(
            "hint_alpha", "demo", "alice", "semantic", "alpha",
            "Synthetic note: fetch host source before answering.",
            "host_fixture", now - timedelta(minutes=1),
        ), Access("demo", "alice"))
        memory.write(MemoryRecord(
            "hint_bob", "demo", "bob", "semantic", "alpha",
            "BOB_PRIVATE_MEMORY_NOT_FOR_ALICE",
            "host_fixture", now - timedelta(minutes=1),
        ), Access("demo", "bob"))
        runtime = MiniAgentRuntime(catalog, journal, memory=memory)

        print("Chapter 33: Integrated Mini-Agent Runtime (trusted read-only fixtures)")
        results = []
        for index, key in enumerate(("alpha", "beta"), start=1):
            req = LookupRequest("demo", "alice", f"success_{index}", key)
            outcome = runtime.run(req)
            results.append(outcome)
            print(
                f"  {req.run_id}: status={outcome.receipt.status}, "
                f"verified={outcome.receipt.verified}, "
                f"output={outcome.output}, "
                f"model_calls={outcome.receipt.model_calls}, "
                f"tool_calls={outcome.receipt.tool_calls}, "
                f"memory_hits={outcome.receipt.memory_hits}, "
                f"spans={len(outcome.spans)}"
            )
            assert outcome.receipt.status == "verified"
            assert outcome.executed_reads == 1

        # A different connection sees durable metadata, not prompt or tool data.
        recovered = SQLiteRunJournal(path).read(
            LookupRequest("demo", "alice", "success_1", "alpha")
        )
        assert recovered.status == "verified"
        assert b"BOB_PRIVATE_MEMORY_NOT_FOR_ALICE" not in path.read_bytes()
        print("  SQLite read across connections: verified, no tool/plaintext saved")

        guessed = MiniAgentRuntime(
            catalog, journal,
            backend_factory=lambda _: GuessOnlyBackend(),
        ).run(LookupRequest("demo", "alice", "guess_1", "alpha"))
        print(
            f"  guess_only: status={guessed.receipt.status}, "
            f"host_verified={guessed.receipt.verified}, "
            f"visible_output={guessed.output!r}"
        )
        assert guessed.receipt.status == "rejected"
        assert guessed.output is None
        assert guessed.executed_reads == 0

        crash = LookupRequest("demo", "alice", "interrupted_1", "alpha")
        journal.begin(crash)
        recovered_crash = SQLiteRunJournal(path)
        assert recovered_crash.read(crash).status == "running"
        reconciled = recovered_crash.reconcile_interrupted(crash)
        print(
            "  simulated worker crash: ",
            reconciled.status,
            "— no tool is retried automatically",
        )
        assert reconciled.status == "needs_reconciliation"

        print(f"  verified_count={sum(o.receipt.verified is True for o in results)}"
              f"/{len(results)}; numbers are deterministic synthetic fixtures")
        print("  This demo is NOT a durable replay engine, sandbox, HTTP SDK, or LLM.")


if __name__ == "__main__":
    main()
