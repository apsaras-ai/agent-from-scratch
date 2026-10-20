"""Chapter 4 offline Agent Loop: (8 + 13) * 2 using two tool calls.

The RuleBasedDemoModel is deterministic, not an LLM. It demonstrates an
observation-driven loop, NOT model reasoning quality. For actual inference
use examples/ch04_live_agent.py with your own credentials.
"""
from __future__ import annotations

from mini_agent.agent_v2 import AgentKernel
from mini_agent.demo_tools import arithmetic_tools
from mini_agent.demo_model import RuleBasedDemoModel


def main() -> None:
    agent = AgentKernel(RuleBasedDemoModel(), arithmetic_tools())
    result = agent.run("Calculate (8 + 13) * 2 using tools.")
    print("Status:", result.status)
    print("Answer:", result.output)
    print("Model calls:", result.model_calls)
    print("Tool calls:", result.tool_calls)
    for event in result.events:
        print("Event:", event.kind, event.detail)


if __name__ == "__main__":
    main()
