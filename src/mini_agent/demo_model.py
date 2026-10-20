"""Deterministic observation-driven policy for teaching Agent loops.

This is NOT a real language model. Reusable across chapters 4 and 5.
"""
from __future__ import annotations

import json
from typing import Any
from .agent_v2 import ModelReply, ToolCall


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


