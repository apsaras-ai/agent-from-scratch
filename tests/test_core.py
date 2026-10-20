import unittest

from mini_agent.core import Agent, Response, ScriptedModel, ToolCall, ToolRegistry


class AgentTests(unittest.TestCase):
    def test_tool_roundtrip(self):
        tools = ToolRegistry()
        tools.register("add", lambda a, b: a + b)
        model = ScriptedModel([
            Response(tool_call=ToolCall("add", {"a": 2, "b": 3})),
            Response(text="five"),
        ])
        result = Agent(model, tools).run("add")
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.messages[-2]["content"], "5")
        self.assertEqual(result.text, "five")

    def test_unknown_tool_feedback(self):
        model = ScriptedModel([
            Response(tool_call=ToolCall("nonexistent", {})),
            Response(text="failed safely"),
        ])
        result = Agent(model, ToolRegistry()).run("bad tool")
        self.assertIn("Unregistered tool", result.messages[-2]["content"])

    def test_step_limit(self):
        model = ScriptedModel([Response(tool_call=ToolCall("missing", {}))])
        with self.assertRaisesRegex(RuntimeError, "max_steps"):
            Agent(model, ToolRegistry(), max_steps=1).run("loop")

    def test_duplicate_tool(self):
        registry = ToolRegistry()
        registry.register("add", lambda: 1)
        with self.assertRaises(ValueError):
            registry.register("add", lambda: 2)


if __name__ == "__main__":
    unittest.main()
