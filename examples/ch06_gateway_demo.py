"""Chapter 6: two native protocols, gateway routing and streamed tool calls.

This example uses local deterministic FIXTURES, not real models.
Run: python examples/ch06_gateway_demo.py
"""
from __future__ import annotations

from collections import deque
import json

from mini_agent.agent_v2 import AgentKernel
from mini_agent.demo_tools import arithmetic_tools
from mini_agent.model_gateway import (
    AnthropicMessagesBackend, GatewayModel, ModelGateway,
    OpenAIChatBackend, RetryPolicy,
)


def chat_tool():
    return {
        "choices": [{"index": 0, "finish_reason": "tool_calls",
                     "message": {"role": "assistant", "content": None,
                                 "tool_calls": [{
                                     "id": "call_add", "type": "function",
                                     "function": {"name": "add", "arguments": '{"a":8,"b":13}'},
                                 }]}}],
        "usage": {"prompt_tokens": 35, "completion_tokens": 15,
                  "prompt_tokens_details": {"cached_tokens": 10}},
    }


def chat_final():
    return {
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "21", "tool_calls": []}}],
        "usage": {"prompt_tokens": 44, "completion_tokens": 4},
    }


def anthro_final():
    return {
        "type": "message", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "Claude-style protocol reply"}],
        "usage": {"input_tokens": 14, "cache_read_input_tokens": 5, "output_tokens": 6},
    }


def main() -> None:
    chat_responses = deque([chat_tool(), chat_final()])
    openai = OpenAIChatBackend(
        name="chat", model="demo-chat", api_key="fake",
        transport=lambda body: chat_responses.popleft(),
    )
    anthropic = AnthropicMessagesBackend(
        name="claude", model="demo-messages", api_key="fake",
        transport=lambda body: anthro_final(),
    )
    gateway = ModelGateway(
        {"chat": openai, "claude": anthropic},
        {"default": ("chat", "claude"), "research": ("claude",)},
        retry=RetryPolicy(max_attempts=1),
    )
    model = GatewayModel(gateway)
    run = AgentKernel(model, arithmetic_tools()).run("Calculate 8+13 using add")
    print("Chat Agent:", run.status, run.output, "tool_calls=", run.tool_calls)
    print("Gateway calls:", len(model.history))
    print("Input tokens reported:", sum(r.usage.input_tokens or 0 for r in model.history))
    print("Cached input (included):", model.history[0].usage.cached_read_input_tokens)

    other = gateway.complete([{"role": "user", "content": "Hello"}], [], route="research")
    print("Other provider:", other.provider, "answer=", other.reply.content)
    print("Other input tokens including cache:", other.usage.input_tokens)

    def fake_stream(body):
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "Calculating "}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": 0, "id": "id-1", "type": "function",
                "function": {"name": "add", "arguments": '{"a":8,'}
            }]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": 0, "function": {"arguments": '"b":13}'}
            }]}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 8}},
        ]
        for chunk in chunks:
            yield "data: " + json.dumps(chunk) + "\n"
            yield "\n"
        yield "data: [DONE]\n"
        yield "\n"

    streaming = OpenAIChatBackend(
        name="stream-demo", model="demo-chat", api_key="fake",
        stream_transport=fake_stream,
    )
    events = list(streaming.stream(
        [{"role": "user", "content": "Compute 8 + 13"}],
        arithmetic_tools().descriptions(),
    ))
    print("Streaming event kinds:", [event.kind for event in events])
    print("Final tool arguments:",
          events[-1].response.reply.tool_calls[0].arguments_json)
    print("No network requests were made.")


if __name__ == "__main__":
    main()
