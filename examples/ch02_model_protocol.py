"""Chapter 2: validate tool proposals and feed results back to a model.

Fully offline. ScriptedModel is deterministic and does not implement an LLM.
"""
from mini_agent.core import Agent, Response, ScriptedModel, ToolCall, ToolRegistry


def validate_add(args: dict) -> tuple[int, int]:
    if set(args) != {"a", "b"}:
        raise ValueError("add requires exactly a and b")
    if type(args["a"]) is not int or type(args["b"]) is not int:
        raise TypeError("a and b must be integers")
    return args["a"], args["b"]


def add(a: int, b: int) -> int:
    a, b = validate_add({"a": a, "b": b})
    return a + b


def run_case(arguments: dict) -> tuple[str, str]:
    tools = ToolRegistry()
    tools.register("add", add)
    model = ScriptedModel([
        Response(tool_call=ToolCall("add", arguments, "call_1")),
        Response(text="done"),
    ])
    result = Agent(model, tools).run("Use add")
    return result.messages[-2]["content"], result.text


def main() -> None:
    valid, _ = run_case({"a": 2, "b": 3})
    invalid, _ = run_case({"a": 2})
    print("valid:", valid)
    print("invalid:", invalid)


if __name__ == "__main__":
    main()
