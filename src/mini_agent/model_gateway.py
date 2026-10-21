"""Chapter 6: explicit, provider-neutral Model Gateway (synchronous edition).

Supports:
  * OpenAI Chat Completions compatible text + function calls;
  * Anthropic Messages text + client tool use;
  * normalized stop reasons and provider-reported usage;
  * bounded retry, static route selection, transient-error fallback;
  * a Chapter 4 AgentKernel-compatible Model wrapper.

No implicit network calls in tests. Native Responses API, async, multi-modal,
full JSON Schema, persistent budgets, and production safety are OUT OF SCOPE.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import math
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler, Request, build_opener,
)

from .agent_v2 import ModelReply, ToolCall
from .chat_adapter import parse_completion


class GatewayError(Exception):
    """All gateway failures; never contains raw provider error bodies."""


class GatewayProtocolError(GatewayError):
    """Unsupported or malformed response; do not blindly retry."""


class GatewayPermanentError(GatewayError):
    """Invalid request, authentication, authorization or incompatible API."""


class GatewayTransientError(GatewayError):
    """Transport error eligible for bounded retry and failover."""

    def __init__(self, category: str, *, http_status: int | None = None):
        super().__init__(category)
        self.category = category
        self.http_status = http_status


class GatewayUnavailable(GatewayError):
    """All eligible route backends exhausted on transient errors."""


@dataclass(frozen=True)
class TokenUsage:
    """Normalized counting semantics; missing values remain None.

    input_tokens: total reported input tokens (INCLUDING cache reads/writes,
    when Anthropic separates those fields). cached_* are INCLUDED components,
    not additive to input_tokens again. Values are provider observations,
    NOT invoices; model/tool costs need independently versioned price data.
    """
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_read_input_tokens: int | None = None
    cached_write_input_tokens: int | None = None


@dataclass(frozen=True)
class GatewayResponse:
    reply: ModelReply
    usage: TokenUsage
    provider: str
    model: str
    attempts: int = 1


class ModelBackend(Protocol):
    name: str

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> GatewayResponse: ...

    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Any: ...


JsonTransport = Callable[[dict[str, Any]], dict[str, Any]]
LineTransport = Callable[[dict[str, Any]], Any]


def _positive(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise GatewayProtocolError(f"invalid_usage:{field_name}")
    return value


def openai_usage(raw: Any) -> TokenUsage:
    if raw is None:
        return TokenUsage()
    if not isinstance(raw, dict):
        raise GatewayProtocolError("invalid_openai_usage")
    details = raw.get("prompt_tokens_details") or {}
    if not isinstance(details, dict):
        raise GatewayProtocolError("invalid_openai_usage_details")
    return TokenUsage(
        input_tokens=_positive(raw.get("prompt_tokens"), "prompt_tokens"),
        output_tokens=_positive(raw.get("completion_tokens"), "completion_tokens"),
        cached_read_input_tokens=_positive(details.get("cached_tokens"), "cached_tokens"),
    )


def anthropic_usage(raw: Any) -> TokenUsage:
    if raw is None:
        return TokenUsage()
    if not isinstance(raw, dict):
        raise GatewayProtocolError("invalid_anthropic_usage")
    base = _positive(raw.get("input_tokens"), "input_tokens")
    cache_write = _positive(raw.get("cache_creation_input_tokens"), "cache_creation_input_tokens")
    cache_read = _positive(raw.get("cache_read_input_tokens"), "cache_read_input_tokens")
    total = None if base is None else base + (cache_write or 0) + (cache_read or 0)
    return TokenUsage(
        input_tokens=total,
        output_tokens=_positive(raw.get("output_tokens"), "output_tokens"),
        cached_read_input_tokens=cache_read,
        cached_write_input_tokens=cache_write,
    )


def _validate_endpoint(base_url: str) -> str:
    if not isinstance(base_url, str):
        raise ValueError("base_url must be a string")
    url = urlparse(base_url)
    if url.query or url.fragment or url.username or url.password or not url.hostname:
        raise ValueError("Invalid base_url")
    if url.scheme != "https" and not (
        url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("Only HTTPS and local loopback HTTP are allowed")
    return base_url.rstrip("/")


def _check_config(model: str, api_key: str, timeout: float) -> None:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Nonempty model is required")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("Nonempty api_key is required")
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be positive and finite")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward API credentials to a redirect target.


def _translate_transport_exception(exc: Exception) -> GatewayError:
    if isinstance(exc, HTTPError):
        if exc.code in {408, 409, 429, 500, 502, 503, 504}:
            return GatewayTransientError("retryable_http_error", http_status=exc.code)
        return GatewayPermanentError(f"nonretryable_http_status:{exc.code}")
    if isinstance(exc, (URLError, TimeoutError, ConnectionError)):
        return GatewayTransientError("transport_unavailable")
    return GatewayPermanentError("transport_failure")


def _request_json(
    url: str, headers: dict[str, str], body: dict[str, Any], timeout: float,
) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(url, headers=headers, data=data, method="POST")
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
    except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
        raise _translate_transport_exception(exc) from exc
    if len(raw) > 8 * 1024 * 1024:
        raise GatewayProtocolError("response_size_limit")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise GatewayProtocolError("invalid_json_response") from exc
    if not isinstance(data, dict):
        raise GatewayProtocolError("response_must_be_object")
    return data


def _request_stream_lines(
    url: str, headers: dict[str, str], body: dict[str, Any], timeout: float,
):
    """Yield newline-delimited SSE lines. Closing the iterator closes HTTP.

    A read timeout covers a blocking read, NOT the total request lifetime.
    No automatic retry after any streaming response data has been observed.
    """
    request = Request(
        url, headers=headers, data=json.dumps(body, ensure_ascii=False).encode(),
        method="POST",
    )
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            while True:
                try:
                    line = response.readline(1024 * 1024 + 1)
                except (TimeoutError, ConnectionError, URLError) as exc:
                    raise _translate_transport_exception(exc) from exc
                if not line:
                    break
                if len(line) > 1024 * 1024:
                    raise GatewayProtocolError("stream_line_size_limit")
                try:
                    yield line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise GatewayProtocolError("stream_utf8_error") from exc
    except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
        raise _translate_transport_exception(exc) from exc


def _openai_reply(data: dict[str, Any]) -> ModelReply:
    try:
        return parse_completion(data)
    except (ValueError, KeyError, TypeError) as exc:
        raise GatewayProtocolError("invalid_chat_completion") from exc


def _anthropic_messages(messages: list[dict[str, Any]]):
    """Convert Chapter-4 Chat-style history to native Anthropic messages.

    Requires all tool_use IDs to have exactly one corresponding tool_result
    before the next ordinary assistant/user turn. Only text and client-side
    function tools are handled.
    """
    system_parts: list[str] = []
    result: list[dict[str, Any]] = []
    pending: set[str] = set()
    in_body = False
    for message in messages:
        role = message.get("role")
        if role == "system":
            if in_body or not isinstance(message.get("content"), str):
                raise GatewayProtocolError("invalid_system_message_position")
            system_parts.append(message["content"])
            continue
        in_body = True
        if role == "user":
            if pending:
                raise GatewayProtocolError("unresolved_tool_calls")
            if not isinstance(message.get("content"), str):
                raise GatewayProtocolError("only_text_user_messages_supported")
            result.append({"role": "user", "content": message["content"]})
        elif role == "assistant":
            if pending:
                raise GatewayProtocolError("unresolved_tool_calls")
            blocks: list[dict[str, Any]] = []
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise GatewayProtocolError("only_text_assistant_messages_supported")
            if content:
                blocks.append({"type": "text", "text": content})
            for call in message.get("tool_calls") or []:
                try:
                    cid = call["id"]
                    fn = call["function"]
                    args = json.loads(fn["arguments"])
                    if not isinstance(cid, str) or not cid or not isinstance(args, dict):
                        raise ValueError("invalid_tool_call")
                    if not isinstance(fn["name"], str) or not fn["name"] or cid in pending:
                        raise ValueError("invalid_tool_call")
                except (KeyError, ValueError, TypeError) as exc:
                    raise GatewayProtocolError("cannot_convert_tool_call") from exc
                pending.add(cid)
                blocks.append({
                    "type": "tool_use", "id": cid, "name": fn["name"], "input": args,
                })
            if not blocks:
                raise GatewayProtocolError("empty_assistant_message")
            result.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            cid = message.get("tool_call_id")
            if cid not in pending or not isinstance(message.get("content"), str):
                raise GatewayProtocolError("orphan_tool_result")
            pending.remove(cid)
            text = message["content"]
            try:
                observation = json.loads(text)
                is_error = isinstance(observation, dict) and observation.get("ok") is False
            except ValueError:
                is_error = False
            block = {
                "type": "tool_result",
                "tool_use_id": cid,
                "content": text,
                "is_error": is_error,
            }
            if result and result[-1]["role"] == "user" and isinstance(result[-1]["content"], list):
                result[-1]["content"].append(block)
            else:
                result.append({"role": "user", "content": [block]})
        else:
            raise GatewayProtocolError("unsupported_message_role")
    if pending:
        raise GatewayProtocolError("unresolved_tool_calls")
    return "\n\n".join(system_parts), result


def _anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = []
    for item in tools:
        try:
            if item["type"] != "function":
                raise ValueError("only_function_tools")
            fn = item["function"]
            if not isinstance(fn["parameters"], dict):
                raise ValueError("parameters")
            definitions.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn["parameters"],
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise GatewayProtocolError("unsupported_tool_definition") from exc
    return definitions


def _anthropic_reply(data: dict[str, Any]) -> ModelReply:
    try:
        stop = data["stop_reason"]
        blocks = data["content"]
        if not isinstance(blocks, list):
            raise TypeError("content must be a list")
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
        if stop in ("refusal", "max_tokens", "model_context_window_exceeded"):
            return ModelReply(finish_reason=reasons[stop])
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in blocks:
            if block["type"] == "text":
                if not isinstance(block["text"], str):
                    raise TypeError("text must be a string")
                texts.append(block["text"])
            elif block["type"] == "tool_use":
                if not isinstance(block["input"], dict):
                    raise TypeError("tool input must be an object")
                if not isinstance(block["id"], str) or not isinstance(block["name"], str):
                    raise TypeError("tool id/name must be strings")
                calls.append(ToolCall(
                    block["id"], block["name"],
                    json.dumps(block["input"], ensure_ascii=False),
                ))
            else:
                raise GatewayProtocolError("unsupported_anthropic_content_block")
        if (stop == "tool_use") != bool(calls):
            raise GatewayProtocolError("inconsistent_anthropic_stop_reason")
        return ModelReply("\n".join(texts) if texts else None, tuple(calls), reasons[stop])
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise GatewayProtocolError("malformed_anthropic_response") from exc


class OpenAIChatBackend:
    def __init__(
        self, *, name: str, model: str, api_key: str,
        base_url: str = "https://api.openai.com/v1", timeout: float = 30.0,
        transport: JsonTransport | None = None,
        stream_transport: LineTransport | None = None,
    ) -> None:
        _check_config(model, api_key, timeout)
        if not name:
            raise ValueError("Backend name cannot be empty")
        self.name = name
        self.model = model
        self._url = _validate_endpoint(base_url) + "/chat/completions"
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        self.timeout = timeout
        self._transport = transport
        self._stream_transport = stream_transport

    def _body(self, messages, tools, *, stream: bool = False) -> dict[str, Any]:
        body = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
        if stream:
            body.update({"stream": True, "stream_options": {"include_usage": True}})
        return body

    def complete(self, messages, tools) -> GatewayResponse:
        body = self._body(messages, tools)
        data = self._transport(body) if self._transport else _request_json(
            self._url, self._headers, body, self.timeout
        )
        if not isinstance(data, dict):
            raise GatewayProtocolError("response_must_be_object")
        return GatewayResponse(
            _openai_reply(data), openai_usage(data.get("usage")),
            self.name, self.model,
        )

    def stream(self, messages, tools):
        from .gateway_stream import consume_openai
        body = self._body(messages, tools, stream=True)
        lines = self._stream_transport(body) if self._stream_transport else _request_stream_lines(
            self._url, self._headers, body, self.timeout
        )
        return consume_openai(lines, provider=self.name, model=self.model)


class AnthropicMessagesBackend:
    def __init__(
        self, *, name: str, model: str, api_key: str,
        max_tokens: int = 1024,
        base_url: str = "https://api.anthropic.com/v1", timeout: float = 30.0,
        transport: JsonTransport | None = None,
        stream_transport: LineTransport | None = None,
    ) -> None:
        _check_config(model, api_key, timeout)
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        if not name:
            raise ValueError("Backend name cannot be empty")
        self.name = name
        self.model = model
        self.max_tokens = max_tokens
        self._url = _validate_endpoint(base_url) + "/messages"
        self._headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        self.timeout = timeout
        self._transport = transport
        self._stream_transport = stream_transport

    def _body(self, messages, tools, *, stream: bool = False) -> dict[str, Any]:
        system, converted = _anthropic_messages(messages)
        body: dict[str, Any] = {
            "model": self.model, "max_tokens": self.max_tokens,
            "messages": converted,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = _anthropic_tools(tools)
        if stream:
            body["stream"] = True
        return body

    def complete(self, messages, tools) -> GatewayResponse:
        body = self._body(messages, tools)
        data = self._transport(body) if self._transport else _request_json(
            self._url, self._headers, body, self.timeout
        )
        if not isinstance(data, dict):
            raise GatewayProtocolError("response_must_be_object")
        return GatewayResponse(
            _anthropic_reply(data), anthropic_usage(data.get("usage")),
            self.name, self.model,
        )

    def stream(self, messages, tools):
        from .gateway_stream import consume_anthropic
        body = self._body(messages, tools, stream=True)
        lines = self._stream_transport(body) if self._stream_transport else _request_stream_lines(
            self._url, self._headers, body, self.timeout
        )
        return consume_anthropic(lines, provider=self.name, model=self.model)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    base_delay: float = 0.1
    max_delay: float = 1.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if any(
            type(x) not in (int, float) or not math.isfinite(x) or x < 0
            for x in (self.base_delay, self.max_delay)
        ):
            raise ValueError("delays must be nonnegative and finite")

    def delay(self, retry_number: int) -> float:
        return min(self.max_delay, self.base_delay * 2 ** (retry_number - 1))


class ModelGateway:
    """One request at a time. Fallback only for exhausted transient failures."""

    def __init__(
        self, backends: Mapping[str, ModelBackend],
        routes: Mapping[str, tuple[str, ...]],
        *,
        retry: RetryPolicy = RetryPolicy(),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not backends or not routes:
            raise ValueError("At least one backend and route are required")
        if any(not names or any(name not in backends for name in names) for names in routes.values()):
            raise ValueError("Every route must reference known backends")
        self.backends = dict(backends)
        self.routes = {name: tuple(entries) for name, entries in routes.items()}
        self.retry = retry
        self.sleep = sleep

    def complete(self, messages, tools, *, route: str = "default") -> GatewayResponse:
        if route not in self.routes:
            raise ValueError(f"Unknown model route: {route}")
        attempts = 0
        for name in self.routes[route]:
            backend = self.backends[name]
            for attempt in range(1, self.retry.max_attempts + 1):
                attempts += 1
                try:
                    result = backend.complete(messages, tools)
                    return replace(result, attempts=attempts)
                except GatewayTransientError:
                    if attempt < self.retry.max_attempts:
                        self.sleep(self.retry.delay(attempt))
                    else:
                        break
                # Permanent and protocol failures STOP; no silent downgrade.
        raise GatewayUnavailable("all_route_backends_failed_transiently")

    def stream(self, messages, tools, *, route: str = "default"):
        """No retries/fallback after stream starts (partial output/tool risk)."""
        if route not in self.routes:
            raise ValueError(f"Unknown model route: {route}")
        return self.backends[self.routes[route][0]].stream(messages, tools)


class GatewayModel:
    """Adapter for chapter 4's Model protocol; not thread-safe.

    Usage observations are recorded per model call (where reported).
    AgentKernel's RunResult does not yet expose TokenUsage directly.
    """

    def __init__(self, gateway: ModelGateway, *, route: str = "default") -> None:
        self.gateway = gateway
        self.route = route
        self.history: list[GatewayResponse] = []

    def complete(self, messages, tools) -> ModelReply:
        result = self.gateway.complete(messages, tools, route=self.route)
        self.history.append(result)
        return result.reply
