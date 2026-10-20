"""Chapter 4: minimal synchronous Chat Completions HTTP adapter.

Target: endpoints compatible with POST /v1/chat/completions and function
tool_calls. This is NOT a universal OpenAI Responses/Anthropic adapter.
The latest APIs, model support and rate limits should be checked separately.
"""
from __future__ import annotations

import json
import math
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .agent_v2 import ModelReply, ProtocolError, ToolCall


def parse_completion(data: dict[str, Any]) -> ModelReply:
    """Turn one provider response into a typed model action proposal."""
    try:
        choice = data["choices"][0]
        message = choice["message"]
        reason = choice["finish_reason"]
        if not isinstance(message, dict) or not isinstance(reason, str):
            raise TypeError("Invalid provider response")
        if message.get("refusal"):
            return ModelReply(finish_reason="refusal")
        if reason in ("length", "content_filter"):
            return ModelReply(finish_reason=reason)
        if reason not in ("stop", "tool_calls"):
            raise ProtocolError("Unsupported finish reason")

        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise TypeError("tool_calls must be a list")
        calls: list[ToolCall] = []
        for raw in raw_calls:
            if raw["type"] != "function":
                raise TypeError("Only function tool calls are supported")
            item = raw["function"]
            if not all(
                isinstance(x, str) and x
                for x in (raw["id"], item["name"], item["arguments"])
            ):
                raise TypeError("Tool id/name/arguments must be nonempty strings")
            # Keep JSON text verbatim: Runtime will validate it. Invalid JSON
            # is a recoverable tool observation, not a parse-time execution.
            calls.append(ToolCall(raw["id"], item["name"], item["arguments"]))
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise TypeError("Only text content is supported")
        return ModelReply(content, tuple(calls), reason)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ProtocolError("Invalid Chat Completions response shape") from exc


class ChatCompletionsModel:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
        transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        if not model or not api_key:
            raise ValueError("Model name and API key are required")
        url = urlparse(base_url)
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Invalid base_url")
        if url.scheme != "https" and not (
            url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("Only HTTPS (or explicit local HTTP) is supported")
        if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive number")
        self.model = model
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.timeout = timeout
        self.transport = transport

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelReply:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools

        if self.transport is not None:
            data = self.transport(body)
        else:
            request = Request(
                self.url,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        if not isinstance(data, dict):
            raise ProtocolError("Provider response must be an object")
        return parse_completion(data)
