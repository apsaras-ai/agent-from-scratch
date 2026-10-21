"""Chapter 6: opt-in live provider demo (one small read-only arithmetic task).

No network request unless credentials/model names are explicitly configured.
This uses native provider APIs, not a universal API compatibility layer.

OpenAI Chat Completions:
  export AGENT_PROVIDER=openai
  export OPENAI_API_KEY="..."
  export AGENT_MODEL="your-chat-completions-model"

Anthropic Messages:
  export AGENT_PROVIDER=anthropic
  export ANTHROPIC_API_KEY="..."
  export AGENT_MODEL="your-messages-model"

  python examples/ch06_live_gateway.py

CAUTION: real provider calls may incur charges; do not commit secrets.
"""
from __future__ import annotations

import os

from mini_agent.agent_v2 import AgentKernel
from mini_agent.demo_tools import arithmetic_tools
from mini_agent.model_gateway import (
    AnthropicMessagesBackend, GatewayModel, ModelGateway, RetryPolicy,
    OpenAIChatBackend,
)


def main() -> None:
    provider = os.environ.get("AGENT_PROVIDER", "openai").lower()
    model_name = os.environ.get("AGENT_MODEL")
    if not model_name:
        raise SystemExit("Set AGENT_MODEL to a provider-supported model name.")

    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise SystemExit("Set OPENAI_API_KEY.")
        backend = OpenAIChatBackend(name="chosen", model=model_name, api_key=key)
    elif provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("Set ANTHROPIC_API_KEY.")
        backend = AnthropicMessagesBackend(
            name="chosen", model=model_name, api_key=key,
            max_tokens=512,
        )
    else:
        raise SystemExit("AGENT_PROVIDER must be openai or anthropic")

    gw = ModelGateway(
        {"chosen": backend}, {"default": ("chosen",)},
        retry=RetryPolicy(max_attempts=1),
    )
    model = GatewayModel(gw)
    result = AgentKernel(
        model, arithmetic_tools(), max_model_calls=5, max_tool_calls=4,
    ).run(
        "Use the add tool to compute 8+13 and return a short final answer.",
        system="You are a math assistant. Use only the registered read-only tools.",
    )
    print("Status:", result.status, "reason:", result.reason)
    print("Model text (NOT independently verified):", result.output)
    print("Model calls:", result.model_calls, "tool calls:", result.tool_calls)
    print("Provider usage records:", [r.usage for r in model.history])
    if result.status != "completed":
        raise SystemExit("Model did not finish successfully.")


if __name__ == "__main__":
    main()
