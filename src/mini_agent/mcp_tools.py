"""Chapter 7: adapter for the OFFICIAL MCP Python SDK v2 Client protocol.

No MCP transport implementation lives here. Callers own a connected Client
and pass it to MCPToolSource. The official SDK handles negotiation, JSON-RPC,
stdio / Streamable HTTP and cancellation. Discovered metadata/annotations are
UNTRUSTED; only explicitly approved tools are registered locally.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Mapping, Protocol

from .tool_runtime import (
    Risk, ToolCatalog, ToolDefinition, ToolExecutionError,
)


class MCPClientProtocol(Protocol):
    async def list_tools(self, *, cursor: str | None = None) -> Any: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _field(record: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(record, dict) and name in record:
            return record[name]
        if hasattr(record, name):
            return getattr(record, name)
    return default


@dataclass(frozen=True)
class MCPToolSource:
    client: MCPClientProtocol
    server_alias: str
    approved_tools: Mapping[str, Risk]
    max_tools: int = 64
    max_pages: int = 10

    async def register(self, catalog: ToolCatalog) -> tuple[str, ...]:
        if not self.server_alias or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in self.server_alias):
            raise ValueError("Unsafe MCP server alias")
        if not self.approved_tools:
            return ()
        selected: dict[str, Any] = {}
        visited_cursors: set[str] = set()
        cursor: str | None = None

        for _ in range(self.max_pages):
            # Current MCP Python SDK v2 supports list_tools(cursor=...).
            page = await self.client.list_tools(cursor=cursor)
            for item in _field(page, "tools", default=[]):
                name = _field(item, "name")
                if not isinstance(name, str):
                    raise ValueError("MCP tool must have a valid name")
                if name not in self.approved_tools:
                    continue
                if name in selected:
                    raise ValueError("Duplicate MCP tool across pages")
                selected[name] = item
                if len(selected) > self.max_tools:
                    raise ValueError("Too many allowed MCP tools")
            new_cursor = _field(page, "next_cursor", "nextCursor")
            if new_cursor is None:
                break
            if not isinstance(new_cursor, str) or not new_cursor or new_cursor in visited_cursors:
                raise ValueError("Invalid MCP discovery cursor")
            visited_cursors.add(new_cursor)
            cursor = new_cursor
        else:
            raise ValueError("MCP pagination exceeded max_pages")

        registered = []
        for remote_name, item in selected.items():
            exposed_name = f"{self.server_alias}.{remote_name}"
            input_schema = _field(item, "input_schema", "inputSchema")
            output_schema = _field(item, "output_schema", "outputSchema")
            description = _field(item, "description", default="") or ""
            # IMPORTANT: remote annotations.readOnlyHint/destructiveHint are
            # not trusted and do not choose permissions.
            risk = self.approved_tools[remote_name]

            async def remote_handler(approved_name, requires_output, **kwargs):
                try:
                    result = await self.client.call_tool(approved_name, kwargs)
                except Exception as exc:
                    raise ToolExecutionError("mcp_transport_or_protocol_error") from exc

                result_type = _field(result, "result_type", "resultType")
                if result_type not in (None, "complete"):
                    # 2026-07-28 spec includes multi-round-trip input_required.
                    raise ToolExecutionError("mcp_input_required_not_supported")
                if bool(_field(result, "is_error", "isError", default=False)):
                    raise ToolExecutionError("mcp_tool_error")

                structured = _field(result, "structured_content", "structuredContent")
                if structured is not None:
                    return structured
                if requires_output is not None:
                    raise ToolExecutionError("mcp_missing_structured_output")
                blocks = _field(result, "content", default=[]) or []
                texts = []
                for block in blocks:
                    if _field(block, "type") != "text":
                        raise ToolExecutionError("mcp_non_text_content_not_supported")
                    content_text = _field(block, "text")
                    if not isinstance(content_text, str):
                        raise ToolExecutionError("mcp_invalid_text_content")
                    texts.append(content_text)
                return "\n".join(texts)

            # Prevent handler's default _name from being treated as part of
            # JSON Schema or as a caller-supplied argument: remote_handler
            # receives **kwargs only from validated model tool data.
            catalog.register(ToolDefinition(
                name=exposed_name,
                description=str(description)[:4000],
                input_schema=input_schema,
                output_schema=output_schema,
                handler=partial(remote_handler, remote_name, output_schema),
                risk=risk,
            ))
            registered.append(exposed_name)

        # Approved tools that the server did not advertise remain unavailable.
        return tuple(registered)
