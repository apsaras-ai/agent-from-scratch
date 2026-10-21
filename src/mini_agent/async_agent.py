"""Chapter 7: bridge chapter 6's synchronous Model to async ToolRuntime.

The model call runs in asyncio.to_thread. Cancelling this coroutine may not
interrupt a blocking HTTP call, and cancelling tool execution may not undo
external effects. This is a teaching bridge, NOT durable execution.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from .agent_v2 import ModelReply
from .tool_runtime import AsyncToolRuntime, ToolPolicy


class SyncToolModel(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
    ) -> ModelReply: ...


@dataclass(frozen=True)
class AsyncAgentResult:
    status: str
    reason: str
    output: str | None
    messages: tuple[dict[str, Any], ...]
    model_calls: int
    tool_calls: int


class AsyncToolAgent:
    def __init__(self, model: SyncToolModel, runtime: AsyncToolRuntime,
                 *, max_model_calls: int = 8, max_tool_calls: int = 12):
        if type(max_model_calls) is not int or max_model_calls < 1:
            raise ValueError("max_model_calls must be positive")
        if type(max_tool_calls) is not int or max_tool_calls < 0:
            raise ValueError("max_tool_calls must be nonnegative")
        self.model = model
        self.runtime = runtime
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls

    async def run(self, task: str, policy: ToolPolicy, *,
                  system: str = "You are a helpful assistant.") -> AsyncAgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        model_calls = 0
        tool_calls = 0

        def finish(status: str, reason: str, output: str | None = None):
            return AsyncAgentResult(
                status, reason, output, tuple(deepcopy(messages)),
                model_calls, tool_calls,
            )

        while model_calls < self.max_model_calls:
            try:
                reply = await asyncio.to_thread(
                    self.model.complete, deepcopy(messages),
                    self.runtime.catalog.specs_for(policy),
                )
            except Exception as exc:
                return finish("halted", "model_error:" + type(exc).__name__)
            model_calls += 1
            if not isinstance(reply, ModelReply):
                return finish("halted", "invalid_model_reply")
            if reply.finish_reason in ("length", "refusal", "content_filter"):
                return finish("halted", "model_" + reply.finish_reason)
            if reply.finish_reason not in ("stop", "tool_calls"):
                return finish("halted", "unsupported_finish_reason")
            if reply.tool_calls:
                if reply.finish_reason != "tool_calls":
                    return finish("halted", "inconsistent_model_reply")
                if model_calls >= self.max_model_calls:
                    return finish("halted", "max_model_calls")
                if tool_calls + len(reply.tool_calls) > self.max_tool_calls:
                    return finish("halted", "max_tool_calls")
                if len({call.id for call in reply.tool_calls}) != len(reply.tool_calls):
                    return finish("halted", "duplicate_tool_call_ids")
                messages.append({
                    "role": "assistant", "content": reply.content,
                    "tool_calls": [
                        {"id": c.id, "type": "function", "function": {
                            "name": c.name, "arguments": c.arguments_json,
                        }} for c in reply.tool_calls
                    ],
                })
                results = await self.runtime.execute_many(reply.tool_calls, policy)
                for result in results:
                    messages.append({
                        "role": "tool", "tool_call_id": result.call_id,
                        "content": result.observation(),
                    })
                tool_calls += len(reply.tool_calls)
                continue
            if reply.finish_reason != "stop" or not isinstance(reply.content, str):
                return finish("halted", "missing_final_text")
            messages.append({"role": "assistant", "content": reply.content})
            return finish("completed", "model_final_not_verified", reply.content)
        return finish("halted", "max_model_calls")
