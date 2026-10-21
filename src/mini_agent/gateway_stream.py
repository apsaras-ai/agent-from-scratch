"""Chapter 6: small SSE parsers for OpenAI Chat and Anthropic Messages.

Yields text_delta progressively, but tool calls only AFTER a complete stream.
Consumers must wait for 'done' before executing any tool. Parsing fragmented
JSON before stream completion would be unsafe.

Only text + client-side tools; no multimodal, reasoning blocks, server-side
tools, resumable streams or unknown event extensions. Keep API tests pinned.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Iterator

from .agent_v2 import ModelReply, ToolCall
from .model_gateway import (
    GatewayProtocolError, GatewayResponse, TokenUsage,
    anthropic_usage, openai_usage,
)


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: TokenUsage | None = None
    response: GatewayResponse | None = None


def iter_sse(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Parse complete SSE lines; does NOT assemble arbitrary byte fragments.

    Blank lines terminate an event; comments/pings are ignored. The transport
    must yield newline-delimited decoded UTF-8 lines, not arbitrary chunks.
    """
    event_type = "message"
    data: list[str] = []
    count = 0

    def flush():
        nonlocal event_type, data
        if not data:
            event_type = "message"
            return None
        payload = "\n".join(data)
        event = (event_type, payload)
        event_type = "message"
        data = []
        return event

    for line in lines:
        if not isinstance(line, str):
            raise GatewayProtocolError("stream_line_not_text")
        line = line.rstrip("\r\n")
        if not line:
            event = flush()
            if event is not None:
                yield event
            count = 0
            continue
        if line.startswith(":"):
            continue
        field, sep, value = line.partition(":")
        if not sep:
            value = ""
        elif value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_type = value
        elif field == "data":
            count += len(value)
            if count > 1024 * 1024:
                raise GatewayProtocolError("stream_event_size_limit")
            data.append(value)
    if data:
        # Event streams can end after the last data line without blank line.
        event = flush()
        if event is not None:
            yield event


def _payload(raw: str) -> dict[str, Any]:
    try:
        item = json.loads(raw)
    except ValueError as exc:
        raise GatewayProtocolError("stream_invalid_json") from exc
    if not isinstance(item, dict):
        raise GatewayProtocolError("stream_message_not_object")
    if "error" in item or item.get("type") == "error":
        raise GatewayProtocolError("provider_stream_error")
    return item


def consume_openai(
    lines: Iterable[str], *, provider: str, model: str,
) -> Iterator[StreamEvent]:
    texts: list[str] = []
    call_parts: dict[int, dict[str, str]] = {}
    reason: str | None = None
    usage = TokenUsage()
    done = False
    for _, raw in iter_sse(lines):
        if raw == "[DONE]":
            done = True
            break
        obj = _payload(raw)
        if obj.get("usage") is not None:
            usage = openai_usage(obj["usage"])
        choices = obj.get("choices") or []
        if not isinstance(choices, list):
            raise GatewayProtocolError("stream_choices_not_array")
        for choice in choices:
            if choice.get("index") != 0:
                raise GatewayProtocolError("multiple_chat_choices_not_supported")
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise GatewayProtocolError("stream_delta_not_object")
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise GatewayProtocolError("stream_text_not_string")
                if content:
                    texts.append(content)
                    yield StreamEvent("text_delta", text=content)
            for tool in delta.get("tool_calls") or []:
                index = tool.get("index")
                if type(index) is not int or index < 0:
                    raise GatewayProtocolError("invalid_stream_tool_index")
                parts = call_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if "id" in tool and tool["id"] is not None:
                    if parts["id"] and parts["id"] != tool["id"]:
                        raise GatewayProtocolError("inconsistent_stream_tool_id")
                    parts["id"] = tool["id"]
                fn = tool.get("function") or {}
                if not isinstance(fn, dict):
                    raise GatewayProtocolError("invalid_stream_function")
                name = fn.get("name")
                arguments = fn.get("arguments")
                if name is not None:
                    if not isinstance(name, str):
                        raise GatewayProtocolError("invalid_stream_function_name")
                    parts["name"] += name
                if arguments is not None:
                    if not isinstance(arguments, str):
                        raise GatewayProtocolError("invalid_stream_arguments")
                    parts["arguments"] += arguments
            if choice.get("finish_reason") is not None:
                reason = choice["finish_reason"]

    if not done:
        raise GatewayProtocolError("stream_missing_done")
    if reason not in {"stop", "tool_calls", "length", "content_filter"}:
        raise GatewayProtocolError("unsupported_stream_finish_reason")
    calls = []
    for index in sorted(call_parts):
        part = call_parts[index]
        if not part["id"] or not part["name"]:
            raise GatewayProtocolError("incomplete_stream_tool_call")
        calls.append(ToolCall(part["id"], part["name"], part["arguments"]))
    if (reason == "tool_calls") != bool(calls):
        if reason not in {"length", "content_filter"}:
            raise GatewayProtocolError("inconsistent_stream_finish_reason")
    if reason in {"length", "content_filter"}:
        calls = []  # never expose a partial action on incomplete generation
    reply = ModelReply("".join(texts) if texts else None, tuple(calls), reason)
    for call in calls:
        yield StreamEvent("tool_call", tool_call=call)
    if usage.input_tokens is not None or usage.output_tokens is not None:
        yield StreamEvent("usage", usage=usage)
    yield StreamEvent(
        "done",
        response=GatewayResponse(reply, usage, provider, model),
    )


def consume_anthropic(
    lines: Iterable[str], *, provider: str, model: str,
) -> Iterator[StreamEvent]:
    blocks: dict[int, dict[str, Any]] = {}
    initial_usage: dict[str, Any] = {}
    final_usage: dict[str, Any] = {}
    stop: str | None = None
    started = False
    done = False
    for event_name, raw in iter_sse(lines):
        obj = _payload(raw)
        kind = obj.get("type") or event_name
        if kind == "ping":
            continue
        if kind == "message_start":
            if started:
                raise GatewayProtocolError("duplicate_message_start")
            started = True
            message = obj.get("message") or {}
            initial_usage = message.get("usage") or {}
        elif kind == "content_block_start":
            index = obj.get("index")
            block = obj.get("content_block") or {}
            if type(index) is not int or index < 0 or index in blocks:
                raise GatewayProtocolError("invalid_content_block_index")
            if block.get("type") == "text":
                blocks[index] = {"kind": "text", "text": block.get("text", ""), "closed": False}
            elif block.get("type") == "tool_use":
                if not isinstance(block.get("id"), str) or not isinstance(block.get("name"), str):
                    raise GatewayProtocolError("invalid_tool_use_start")
                blocks[index] = {
                    "kind": "tool_use", "id": block["id"], "name": block["name"],
                    "args": "", "initial_input": block.get("input", {}),
                    "closed": False,
                }
            else:
                raise GatewayProtocolError("unsupported_stream_content_block")
        elif kind == "content_block_delta":
            index = obj.get("index")
            if index not in blocks or blocks[index]["closed"]:
                raise GatewayProtocolError("orphan_content_block_delta")
            delta = obj.get("delta") or {}
            typ = delta.get("type")
            block = blocks[index]
            if block["kind"] == "text" and typ == "text_delta":
                text = delta.get("text")
                if not isinstance(text, str):
                    raise GatewayProtocolError("invalid_text_delta")
                block["text"] += text
                if text:
                    yield StreamEvent("text_delta", text=text)
            elif block["kind"] == "tool_use" and typ == "input_json_delta":
                part = delta.get("partial_json")
                if not isinstance(part, str):
                    raise GatewayProtocolError("invalid_tool_json_delta")
                block["args"] += part
            else:
                raise GatewayProtocolError("unsupported_content_delta")
        elif kind == "content_block_stop":
            index = obj.get("index")
            if index not in blocks or blocks[index]["closed"]:
                raise GatewayProtocolError("orphan_content_block_stop")
            blocks[index]["closed"] = True
        elif kind == "message_delta":
            stop = (obj.get("delta") or {}).get("stop_reason", stop)
            final_usage = obj.get("usage") or final_usage
        elif kind == "message_stop":
            done = True
            break
        else:
            raise GatewayProtocolError("unsupported_stream_event")

    if not started or not done or stop is None or not all(b["closed"] for b in blocks.values()):
        raise GatewayProtocolError("incomplete_anthropic_stream")
    reasons = {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
        "model_context_window_exceeded": "length",
        "refusal": "refusal",
    }
    if stop not in reasons:
        raise GatewayProtocolError("unsupported_anthropic_stop_reason")
    texts, calls = [], []
    for index in sorted(blocks):
        b = blocks[index]
        if b["kind"] == "text":
            texts.append(b["text"])
        else:
            arguments = b["args"] or json.dumps(b["initial_input"], ensure_ascii=False)
            calls.append(ToolCall(b["id"], b["name"], arguments))
    if (stop == "tool_use") != bool(calls) and stop not in {"max_tokens", "refusal", "model_context_window_exceeded"}:
        raise GatewayProtocolError("inconsistent_anthropic_stop_reason")
    if stop in {"max_tokens", "refusal", "model_context_window_exceeded"}:
        calls = []
    merged_usage = {**initial_usage, **final_usage}
    usage = anthropic_usage(merged_usage)
    reply = ModelReply(
        "\n".join(t for t in texts if t) or None,
        tuple(calls),
        reasons[stop],
    )
    for call in calls:
        yield StreamEvent("tool_call", tool_call=call)
    if usage.input_tokens is not None or usage.output_tokens is not None:
        yield StreamEvent("usage", usage=usage)
    yield StreamEvent(
        "done", response=GatewayResponse(reply, usage, provider, model)
    )
