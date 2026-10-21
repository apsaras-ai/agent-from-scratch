"""Chapter 7: offline ToolRuntime, MCP discovery and Agent integration.

Run: python examples/ch07_tool_runtime.py
No network, no actual file writes, no LLM API key. The MCP client is a Fake.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from mini_agent.agent_v2 import ToolCall
from mini_agent.async_agent import AsyncToolAgent
from mini_agent.demo_model import RuleBasedDemoModel
from mini_agent.mcp_tools import MCPToolSource
from mini_agent.tool_runtime import (
    AsyncToolRuntime, Risk, ToolCatalog, ToolDefinition, ToolPolicy,
)


class FakeMCPClient:
    async def list_tools(self, *, cursor=None):
        return {"tools": [{
            "name": "check", "description": "Read a simulated status",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1}},
                "required": ["name"], "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {"healthy": {"type": "boolean"}},
                "required": ["healthy"], "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True},
        }], "nextCursor": None}

    async def call_tool(self, name, arguments):
        return {"resultType": "complete", "isError": False,
                "structuredContent": {"healthy": arguments["name"] == "demo"},
                "content": []}


def arithmetic_schema():
    return {"type": "object", "properties": {
        "a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False}


async def main() -> None:
    catalog = ToolCatalog()

    async def add(a, b): return a + b
    async def multiply(a, b): return a * b

    catalog.register(ToolDefinition("add", "Add two integers", arithmetic_schema(), add))
    catalog.register(ToolDefinition("multiply", "Multiply two integers", arithmetic_schema(), multiply))
    client = FakeMCPClient()
    await MCPToolSource(client, "remote", {"check": Risk.WRITE}).register(catalog)
    runtime = AsyncToolRuntime(catalog, max_parallel=2, timeout_seconds=2.0)

    p = ToolPolicy("tenant-1", "run-1", frozenset({"add", "multiply"}))
    result = await AsyncToolAgent(RuleBasedDemoModel(), runtime).run(
        "Compute (8 + 13) * 2 using tools", p)
    print("Agent:", result.status, result.output)
    print("Model calls:", result.model_calls, "Tool calls:", result.tool_calls)

    # MCP server metadata declares readOnlyHint=true, but the TRUSTED host
    # classifies this tool as WRITE. It MUST NOT execute without permission.
    call = ToolCall("mcp-1", "remote.check", '{"name":"demo"}')
    denied = await runtime.execute_one(call, ToolPolicy(
        "tenant-1", "run-2", frozenset({"remote.check"})))
    print("MCP without approval:", denied.error)

    allowed = await runtime.execute_one(call, ToolPolicy(
        "tenant-1", "run-3", frozenset({"remote.check"}),
        approvals=frozenset({("remote.check", "mcp-1")})))
    print("MCP with approval:", allowed.value)
    print("Offline Fake MCP, no external systems touched.")


if __name__ == "__main__":
    asyncio.run(main())
