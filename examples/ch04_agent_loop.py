"""Chapter 4 offline Agent Loop: (8 + 13) * 2 using two tool calls.

The RuleBasedDemoModel is deterministic, not an LLM. It demonstrates an
observation-driven loop, NOT model reasoning quality. For actual inference
use examples/ch04_live_agent.py with your own credentials.
"""
from __future__ import annotations

import json
from typing import Any

from mini_agent.agent_v2 import AgentKernel, ModelReply, ToolCall
from mini_agent.demo_tools import arithmetic_tools


class RuleBasedDemoModel:
    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelReply:
        results = [msg for msg in messages if msg["role"] == "tool"]
        if not results:
            return ModelReply(
                tool_calls=(ToolCall("call_add", "add", '{"a":8,"b":13}'),),
                finish_reason="tool_calls",
            )
        last_result = json.loads(results[-1]["content"])
        if not last_result.get("ok"):
            return ModelReply(content=f"Tool failed: {last_result['error']}")
        if len(results) == 1:
            return ModelReply(
                tool_calls=(
                    ToolCall(
                        "call_multiply", "multiply",
                        json.dumps({"a": last_result["result"], "b": 2}),
                    ),
                ),
                finish_reason="tool_calls",
            )
        return ModelReply(content=f"Answer: {last_result['result']}")


def main() -> None:
    agent = AgentKernel(RuleBasedDemoModel(), arithmetic_tools())
    result = agent.run("Calculate (8 + 13) * 2 using tools.")
    print("Status:", result.status)
    print("Answer:", result.output)
    print("Model calls:", result.model_calls)
    print("Tool calls:", result.tool_calls)
    for event in result.events:
        print("Event:", event.kind, event.detail)


if __name__ == "__main__":
    main()
