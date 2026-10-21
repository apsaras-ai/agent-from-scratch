"""Optional MCP Python SDK 2.x integration: DISCOVER only from loopback.

First start a TRUSTED MCP Streamable HTTP server on localhost:8000/mcp,
then: pip install -e ".[mcp]"
      MCP_TOOL_ALLOWLIST="echo,add" python examples/ch07_mcp_client_v2.py

Do not point this demo at unknown MCP servers. The demo discovers selected
tools but does not automatically execute them; for calls use trusted
ToolPolicy approvals and AsyncToolRuntime.
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse

from mini_agent.mcp_tools import MCPToolSource
from mini_agent.tool_runtime import Risk, ToolCatalog


async def main() -> None:
    url = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("This tutorial only connects to loopback HTTP MCP servers.")
    allowed = {name.strip(): Risk.READ
               for name in os.environ.get("MCP_TOOL_ALLOWLIST", "").split(",")
               if name.strip()}
    if not allowed:
        raise SystemExit("Set MCP_TOOL_ALLOWLIST to explicit trusted remote tool names.")

    try:
        from mcp import Client
    except ImportError as exc:
        raise SystemExit('Install optional SDK: pip install -e ".[mcp]"') from exc

    async with Client(url) as client:
        catalog = ToolCatalog()
        names = await MCPToolSource(client, "local", allowed).register(catalog)
        print("Approved tools discovered:", names)
        print("Tool schemas are loaded, but no tools have been called.")
        print("MCP annotations do NOT authorize real-world side effects.")


if __name__ == "__main__":
    asyncio.run(main())
