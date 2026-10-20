"""Read-only arithmetic tools shared by chapter 4 examples."""
from __future__ import annotations

from .agent_v2 import Tool, ToolRegistry


def arithmetic_tools() -> ToolRegistry:
    tools = ToolRegistry()
    two_ints = {
        "type": "object",
        "properties": {
            "a": {"type": "integer", "description": "First integer"},
            "b": {"type": "integer", "description": "Second integer"},
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    tools.register(Tool("add", "Add two integers.", two_ints, lambda a, b: a + b))
    tools.register(Tool("multiply", "Multiply two integers.", two_ints, lambda a, b: a * b))
    return tools
