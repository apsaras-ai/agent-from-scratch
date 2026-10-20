"""Chapter 4: an educational, synchronous Agent Kernel.

This is a teaching implementation, NOT a secure tool sandbox or durable
production runtime. No shell/FS/browser tools are registered by default.
Previous chapters continue to use mini_agent.core (the v0.1 kernel).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from typing import Any, Callable, Protocol


class ProtocolError(ValueError):
    """Invalid structured response from a model adapter."""


class ToolError(ValueError):
    """A tool request cannot be executed safely or correctly."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ModelReply:
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"


class Model(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelReply: ...


@dataclass(frozen=True)
class Event:
    kind: str
    detail: str


@dataclass(frozen=True)
class RunResult:
    status: str
    reason: str
    output: str | None
    messages: tuple[dict[str, Any], ...]
    events: tuple[Event, ...]
    model_calls: int
    tool_calls: int


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    read_only: bool = True


def _check_schema(schema: dict[str, Any]) -> None:
    """Validate ONLY the shallow subset of JSON Schema supported in Ch. 4.

    Root: object, properties with scalar types, required, and
    additionalProperties=false. This is NOT a full JSON Schema validator.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("Tool schema must have root type=object")
    props = schema.get("properties")
    required = schema.get("required", [])
    if not isinstance(props, dict) or not isinstance(required, list):
        raise ValueError("Tool schema needs properties object and required list")
    if schema.get("additionalProperties") is not False:
        raise ValueError("Tool schema must set additionalProperties=false")
    if any(not isinstance(x, str) or x not in props for x in required):
        raise ValueError("Required fields must be declared property names")
    if len(set(required)) != len(required):
        raise ValueError("Duplicate required fields")
    supported = {"string", "integer", "number", "boolean"}
    for name, spec in props.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Property names must be non-empty strings")
        if not isinstance(spec, dict) or spec.get("type") not in supported:
            raise ValueError(f"Unsupported property type in {name!r}")
        if set(spec) - {"type", "description"}:
            raise ValueError(f"Unsupported schema keywords in property {name!r}")
    if set(schema) - {"type", "properties", "required", "additionalProperties", "description"}:
        raise ValueError("Unsupported root schema keywords")


def _matches_type(value: Any, type_name: str) -> bool:
    if type_name == "integer":
        return type(value) is int  # bool is a subclass of int!
    if type_name == "number":
        return type(value) in (int, float) and math.isfinite(value)
    if type_name == "boolean":
        return type(value) is bool
    if type_name == "string":
        return type(value) is str
    return False


def validate_arguments(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Parse model-provided JSON and validate a small, explicit schema subset."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ToolError("invalid_json") from exc
    if not isinstance(value, dict):
        raise ToolError("arguments_must_be_an_object")
    props = schema["properties"]
    missing = set(schema.get("required", [])) - set(value)
    if missing:
        raise ToolError("missing_required:" + ",".join(sorted(missing)))
    unexpected = set(value) - set(props)
    if unexpected:
        raise ToolError("unexpected_properties:" + ",".join(sorted(unexpected)))
    for name, actual in value.items():
        if not _matches_type(actual, props[name]["type"]):
            raise ToolError(f"wrong_type:{name}")
    return value


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError(f"Empty or duplicate tool name: {tool.name!r}")
        _check_schema(tool.parameters)
        self._tools[tool.name] = tool

    def descriptions(self) -> list[dict[str, Any]]:
        """Return provider-neutral Chat Completions function specs."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": deepcopy(tool.parameters),
                },
            }
            for tool in self._tools.values()
        ]

    def invoke(self, call: ToolCall, *, allow_writes: bool = False) -> dict[str, Any]:
        """Return a structured observation; never execute an unregistered tool."""
        tool = self._tools.get(call.name)
        if tool is None:
            return {"ok": False, "error": "unknown_tool"}
        if not tool.read_only and not allow_writes:
            return {"ok": False, "error": "write_requires_approval"}
        try:
            arguments = validate_arguments(call.arguments_json, tool.parameters)
        except ToolError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            value = tool.handler(**arguments)
            # Ensure it can be serialized before reporting success.
            json.dumps(value, allow_nan=False)
            return {"ok": True, "result": value}
        except Exception as exc:
            # Do not expose arbitrary exception messages to models or logs.
            return {"ok": False, "error": "tool_execution_failed",
                    "error_type": type(exc).__name__}


class AgentKernel:
    """Serial, bounded Agent Loop with messages and observable events."""

    def __init__(
        self,
        model: Model,
        tools: ToolRegistry,
        *,
        max_model_calls: int = 8,
        max_tool_calls: int = 12,
        max_observation_chars: int = 8000,
    ) -> None:
        if type(max_model_calls) is not int or max_model_calls < 1:
            raise ValueError("max_model_calls must be a positive integer")
        if type(max_tool_calls) is not int or max_tool_calls < 0:
            raise ValueError("max_tool_calls must be a nonnegative integer")
        if type(max_observation_chars) is not int or max_observation_chars < 256:
            raise ValueError("max_observation_chars must be >= 256")
        self.model = model
        self.tools = tools
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.max_observation_chars = max_observation_chars

    def run(
        self, task: str, *, system: str = "You are a helpful assistant.",
        allow_writes: bool = False,
    ) -> RunResult:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": task},
        ]
        events: list[Event] = [Event("started", "new_task")]
        model_calls = 0
        tool_calls = 0

        def finish(status: str, reason: str, output: str | None = None) -> RunResult:
            events.append(Event("stopped", reason))
            return RunResult(
                status, reason, output, tuple(deepcopy(messages)),
                tuple(events), model_calls, tool_calls,
            )

        while model_calls < self.max_model_calls:
            events.append(Event("model_requested", str(model_calls + 1)))
            try:
                reply = self.model.complete(
                    deepcopy(messages), self.tools.descriptions()
                )
            except Exception as exc:
                return finish("halted", "model_error:" + type(exc).__name__)
            model_calls += 1

            if not isinstance(reply, ModelReply):
                return finish("halted", "invalid_model_reply")
            if reply.finish_reason in ("length", "content_filter", "refusal"):
                return finish("halted", "model_" + reply.finish_reason)
            if reply.finish_reason not in ("stop", "tool_calls"):
                return finish("halted", "unsupported_finish_reason")
            if reply.tool_calls:
                if reply.finish_reason != "tool_calls":
                    return finish("halted", "invalid_model_reply")
                if len({call.id for call in reply.tool_calls}) != len(reply.tool_calls):
                    return finish("halted", "duplicate_tool_call_ids")
                if any(not call.id or not call.name for call in reply.tool_calls):
                    return finish("halted", "invalid_tool_call")
                # Preflight: don't execute side effects when no later model turn
                # remains to see the observation, or when tool budget is short.
                if model_calls >= self.max_model_calls:
                    return finish("halted", "max_model_calls")
                if tool_calls + len(reply.tool_calls) > self.max_tool_calls:
                    return finish("halted", "max_tool_calls")

                messages.append({
                    "role": "assistant",
                    "content": reply.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.arguments_json,
                            },
                        }
                        for call in reply.tool_calls
                    ],
                })
                for call in reply.tool_calls:
                    observation = self.tools.invoke(
                        call, allow_writes=allow_writes
                    )
                    serialized = json.dumps(
                        observation, ensure_ascii=False, allow_nan=False
                    )
                    if len(serialized) > self.max_observation_chars:
                        # Preserve valid JSON, but make truncated observations
                        # visibly incomplete. This is not full context management.
                        serialized = json.dumps({
                            "ok": observation["ok"],
                            "truncated": True,
                            "excerpt": serialized[:self.max_observation_chars - 100],
                        }, ensure_ascii=False)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": serialized,
                    })
                    tool_calls += 1
                    events.append(Event(
                        "tool_result",
                        f"{call.name}:{'ok' if observation['ok'] else 'error'}"
                    ))
                continue

            if reply.finish_reason == "tool_calls":
                return finish("halted", "missing_tool_calls")
            if not isinstance(reply.content, str):
                return finish("halted", "missing_final_content")
            messages.append({"role": "assistant", "content": reply.content})
            events.append(Event("final_answer", "model_text_not_verified"))
            return finish("completed", "model_final", reply.content)

        return finish("halted", "max_model_calls")
