"""Run without a model API, showing a full tool-call round trip."""
from mini_agent.core import Agent, Response, ScriptedModel, ToolCall, ToolRegistry


def main() -> None:
    tools = ToolRegistry()
    tools.register("add", lambda a, b: a + b)
    model = ScriptedModel([
        Response(tool_call=ToolCall("add", {"a": 2, "b": 3}, "call_1")),
        Response(text="2 + 3 = 5"),
    ])
    result = Agent(model, tools).run("Calculate 2 + 3")
    print(f"Agent: {result.text}")
    print(f"Steps: {result.steps}")


if __name__ == "__main__":
    main()
