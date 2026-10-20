"""Minimal educational agent loop. This is not a production sandbox."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol
import json


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = "tool-call"


@dataclass(frozen=True)
class Response:
    text: str | None = None
    tool_call: ToolCall | None = None


class Model(Protocol):
    def complete(self, messages: list[dict[str, Any]]) -> Response: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, function: Callable[..., Any]) -> None:
        if not name or name in self._tools:
            raise ValueError(f"Invalid or duplicate tool name: {name!r}")
        self._tools[name] = function

    def invoke(self, call: ToolCall) -> str:
        if call.name not in self._tools:
            raise ValueError(f"Unregistered tool: {call.name}")
        if not isinstance(call.arguments, dict):
            raise TypeError("Tool arguments must be a JSON object")
        return str(self._tools[call.name](**call.arguments))


@dataclass(frozen=True)
class AgentResult:
    text: str
    steps: int
    messages: list[dict[str, Any]]


class Agent:
    def __init__(self, model: Model, tools: ToolRegistry, max_steps: int = 8) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        self.model, self.tools, self.max_steps = model, tools, max_steps

    def run(self, task: str) -> AgentResult:
        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
        for step in range(1, self.max_steps + 1):
            response = self.model.complete(messages)
            if response.tool_call is None:
                if response.text is None:
                    raise ValueError("Model returned neither text nor a tool call")
                messages.append({"role": "assistant", "content": response.text})
                return AgentResult(response.text, step, messages)
            call = response.tool_call
            messages.append({"role": "assistant", "tool_call": {"name": call.name, "arguments": call.arguments, "call_id": call.call_id}})
            try:
                observation = self.tools.invoke(call)
            except (ValueError, TypeError, KeyError) as exc:
                observation = f"Tool error: {type(exc).__name__}: {exc}"
            messages.append({"role": "tool", "name": call.name, "call_id": call.call_id, "content": observation})
        raise RuntimeError(f"Agent exceeded max_steps={self.max_steps}")


class ScriptedModel:
    """Deterministic fake model for the offline teaching example."""
    def __init__(self, responses: list[Response]) -> None:
        self._responses = iter(responses)

    def complete(self, messages: list[dict[str, Any]]) -> Response:
        try:
            return next(self._responses)
        except StopIteration as exc:
            raise RuntimeError("Scripted model has no more responses") from exc
