"""Minimal Chat Completions tool-calling demo (requires model credentials).

Only for a trusted OpenAI-compatible endpoint. Do not send untrusted secrets.
"""
from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen

from mini_agent.core import Agent, Response, ToolCall, ToolRegistry


class ChatCompletionsModel:
    def __init__(self) -> None:
        self.api_key = os.environ["AGENT_API_KEY"]
        self.model = os.environ["AGENT_MODEL"]
        self.url = os.environ.get("AGENT_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"

    def complete(self, messages: list[dict]) -> Response:
        wire_messages: list[dict] = []
        for message in messages:
            if "tool_call" in message:
                call = message["tool_call"]
                wire_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": call["call_id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"]),
                        },
                    }],
                })
            elif message["role"] == "tool":
                wire_messages.append({
                    "role": "tool",
                    "tool_call_id": message["call_id"],
                    "content": message["content"],
                })
            else:
                wire_messages.append(message)
        body = {
            "model": self.model,
            "messages": wire_messages,
            "tools": [{
                "type": "function",
                "function": {
                    "name": "add",
                    "description": "Add two integers.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "integer"},
                            "b": {"type": "integer"},
                        },
                        "required": ["a", "b"],
                        "additionalProperties": False,
                    },
                },
            }],
        }
        request = Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            data = json.load(response)
        message = data["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if len(calls) > 1:
            raise ValueError("This teaching adapter only supports one tool call per turn")
        if calls:
            call = calls[0]
            return Response(tool_call=ToolCall(
                name=call["function"]["name"],
                arguments=json.loads(call["function"]["arguments"]),
                call_id=call["id"],
            ))
        return Response(text=message.get("content") or "")


def main() -> None:
    tools = ToolRegistry()
    tools.register("add", lambda a, b: a + b)
    result = Agent(ChatCompletionsModel(), tools).run("What is 2 + 3? Use the add tool.")
    print(result.text)


if __name__ == "__main__":
    main()
