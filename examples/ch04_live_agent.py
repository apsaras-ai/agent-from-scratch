"""Chapter 4: run the same Agent Kernel with a real Chat Completions model.

Requires a trusted HTTPS endpoint supporting function tool_calls.
Never hardcode or commit API keys.

    export AGENT_API_KEY="..."
    export AGENT_MODEL="your-supported-model"
    # optional: export AGENT_BASE_URL="https://your-endpoint/v1"
    python examples/ch04_live_agent.py
"""
from __future__ import annotations

import os

from mini_agent.agent_v2 import AgentKernel
from mini_agent.chat_adapter import ChatCompletionsModel
from mini_agent.demo_tools import arithmetic_tools


def main() -> None:
    key = os.environ.get("AGENT_API_KEY")
    name = os.environ.get("AGENT_MODEL")
    if not key or not name:
        raise SystemExit("Set AGENT_API_KEY and AGENT_MODEL to run the live example.")

    model = ChatCompletionsModel(
        model=name,
        api_key=key,
        base_url=os.environ.get("AGENT_BASE_URL", "https://api.openai.com/v1"),
        timeout=30.0,
    )
    agent = AgentKernel(model, arithmetic_tools(), max_model_calls=6, max_tool_calls=4)
    result = agent.run(
        "Calculate (8 + 13) * 2. Use the add and multiply tools for arithmetic. "
        "Return a concise final answer.",
        system="You are a tool-using math assistant. Only use registered tools.",
    )
    print(f"Status: {result.status} / {result.reason}")
    print(f"Answer: {result.output}")
    print(f"Model calls: {result.model_calls}, Tool calls: {result.tool_calls}")
    if result.status != "completed":
        raise SystemExit("The model did not complete the task.")


if __name__ == "__main__":
    main()
