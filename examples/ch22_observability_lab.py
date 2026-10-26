"""Chapter 22: real in-process Agent Kernel tracing + offline replay.

Actual Python calls: Agent Kernel -> Fake Gateway -> Tool Registry -> Verifier.
A fake model first requests an unauthorized tool, then makes a legitimate
read and produces the expected answer. The trace preserves error evidence
WITHOUT recording prompt text, tool arguments, tool output, or a fake secret.

No network, external model, database side effect or OTel collector.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

from mini_agent.agent_v2 import AgentKernel, ModelReply, Tool, ToolCall, ToolRegistry
from mini_agent.model_gateway import GatewayResponse, TokenUsage
from mini_agent.observability import RateCard, TraceRecorder
from mini_agent.observability_bridge import TracedGatewayModel, TracedToolRegistry
from mini_agent.trace_store import (
    write_trace_jsonl, read_trace_jsonl, replay_diagnostic,
)

PRIVATE_CANARY = "SYNTHETIC_PRIVATE_CANARY_NOT_FOR_LOGS"
EXPECTED_ANSWER = "The fixture states that queue leases must be checked."


class FakeGateway:
    name = "synthetic"
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        replies = [
            ModelReply(
                tool_calls=(ToolCall(
                    "tool-1", "unapproved_tool",
                    '{"secret":"SYNTHETIC_PRIVATE_CANARY_NOT_FOR_LOGS"}',
                ),),
                finish_reason="tool_calls",
            ),
            ModelReply(
                tool_calls=(ToolCall(
                    "tool-2", "lookup",
                    '{"query":"SYNTHETIC_PRIVATE_CANARY_NOT_FOR_LOGS"}',
                ),),
                finish_reason="tool_calls",
            ),
            ModelReply(content=EXPECTED_ANSWER, finish_reason="stop"),
        ]
        if self.calls > len(replies):
            raise AssertionError("Unexpected fake model invocation")
        return GatewayResponse(
            replies[self.calls-1],
            TokenUsage(
                input_tokens=(60, 100, 90)[self.calls-1],
                output_tokens=(12, 25, 40)[self.calls-1],
            ),
            "synthetic", "fixture-model",
        )


def run_experiment():
    tracer = TraceRecorder()
    tools = ToolRegistry()
    read_count = [0]

    def lookup(query):
        read_count[0] += 1
        return {"value": "queue leases require fencing",
                "canary": PRIVATE_CANARY}

    tools.register(Tool(
        "lookup", "Read an approved synthetic document",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"], "additionalProperties": False,
        },
        handler=lookup,
    ))
    model = TracedGatewayModel(
        FakeGateway(), tracer, provider="synthetic", model="fixture-model",
        rate_card=RateCard("synthetic", "fixture-model", "fixture-2026-10",
                           "1.00", "4.00"),
    )
    kernel = AgentKernel(
        model, TracedToolRegistry(tools, tracer),
        max_model_calls=4, max_tool_calls=3,
    )
    with tracer.span("invoke_agent", attributes={"agent.task.kind": "research"}) as root:
        with tracer.span("plan"):
            # This is synthetic deterministic task setup, NOT hidden CoT.
            pass
        result = kernel.run("Find the fixture rule; do not reveal secrets")
        with tracer.span("verify") as check:
            passed = (
                result.status == "completed" and
                result.output == EXPECTED_ANSWER and read_count[0] == 1
            )
            check.verify(passed=passed, reason="host_fixture_checked")
        root.add_event("checkpoint_saved",
                       attributes={"agent.workflow.phase": "completed"})
    return tracer, result, passed, read_count[0]


def main() -> None:
    tracer, run, passed, count = run_experiment()
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "trace.jsonl"
        write_trace_jsonl(tracer, path)
        raw = path.read_text(encoding="utf-8")
        assert PRIVATE_CANARY not in raw
        assert "Find the fixture rule" not in raw
        replay = read_trace_jsonl(path)
        summary = replay_diagnostic(path)
        print("Agent result:", run.status, "host_verified=", passed)
        print("Approved lookup executions:", count)
        print("Span count:", summary.span_count)
        print("First recorded failure:", summary.primary_failure)
        print("Independent verification:", summary.verified)
        print("Model tokens:", summary.input_tokens, "input /",
              summary.output_tokens, "output")
        print("Fixture-estimated USD:", summary.estimated_usd)
        print("Traceparent:", next(
            f"00-{s.trace_id}-{s.span_id}-01"
            for s in replay if s.operation == "invoke_agent"
        ))
        print("Replay is read-only; model/tool executions during replay:", 0)
        assert summary.verified == "passed"
        assert summary.primary_failure == "execute_tool:tool_rejected_or_failed"
        assert summary.priced_model_calls == 3
        assert summary.input_tokens == 250
        assert summary.output_tokens == 77
        assert summary.estimated_usd == "0.000558"
        assert len(replay) == summary.span_count
    print("This is an offline teaching trace, not live OTel or a real bill.")


if __name__ == "__main__":
    main()
