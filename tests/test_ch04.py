"""Chapter 4: deterministic protocol, loop, permission, and budget tests."""
from __future__ import annotations

import json
import unittest

from mini_agent.demo_model import RuleBasedDemoModel
from mini_agent.agent_v2 import (
    AgentKernel, ModelReply, Tool, ToolCall, ToolRegistry, validate_arguments,
)
from mini_agent.chat_adapter import ChatCompletionsModel, ProtocolError, parse_completion
from mini_agent.demo_tools import arithmetic_tools


class SequenceModel:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.seen: list[list[dict]] = []

    def complete(self, messages, tools):
        self.seen.append(messages)
        return next(self.replies)


def math_call(id="call_1", args='{"a":2,"b":3}', name="add"):
    return ToolCall(id, name, args)


def roundtrip_reply(*calls):
    return ModelReply(tool_calls=tuple(calls), finish_reason="tool_calls")


class AgentKernelTests(unittest.TestCase):
    def test_offline_two_step_demo(self):
        result = AgentKernel(RuleBasedDemoModel(), arithmetic_tools()).run("Calculate")
        self.assertEqual((result.status, result.output), ("completed", "Answer: 42"))
        self.assertEqual((result.model_calls, result.tool_calls), (3, 2))
        self.assertEqual([m["role"] for m in result.messages],
                         ["system", "user", "assistant", "tool",
                          "assistant", "tool", "assistant"])
        self.assertEqual(
            [json.loads(m["content"])["result"] for m in result.messages
             if m["role"] == "tool"], [21, 42]
        )

    def test_plain_text_without_tools(self):
        model = SequenceModel(ModelReply(content="hello"))
        result = AgentKernel(model, arithmetic_tools()).run("Say hi")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output, "hello")
        self.assertEqual((result.model_calls, result.tool_calls), (1, 0))
        self.assertEqual(len(model.seen), 1)

    def test_duplicate_call_ids_fail_without_executing(self):
        model = SequenceModel(roundtrip_reply(math_call("same"), math_call("same")))
        result = AgentKernel(model, arithmetic_tools()).run("Test duplicate IDs")
        self.assertEqual(result.reason, "duplicate_tool_call_ids")
        self.assertEqual(result.tool_calls, 0)

    def test_multiple_tool_calls_match_ids(self):
        model = SequenceModel(
            roundtrip_reply(math_call("one"), ToolCall("two", "multiply", '{"a":4,"b":5}')),
            ModelReply(content="done"),
        )
        result = AgentKernel(model, arithmetic_tools()).run("Two calls")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_calls, 2)
        tools = [m for m in result.messages if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tools], ["one", "two"])
        self.assertEqual([json.loads(m["content"])["result"] for m in tools], [5, 20])

    def test_unknown_tool_observation_and_recovery(self):
        model = SequenceModel(roundtrip_reply(math_call(name="unregistered")),
                              ModelReply(content="I cannot use that tool"))
        result = AgentKernel(model, arithmetic_tools()).run("Unknown")
        self.assertEqual(result.status, "completed")
        tool_result = json.loads(result.messages[-2]["content"])
        self.assertEqual(tool_result["error"], "unknown_tool")
        self.assertFalse(tool_result["ok"])

    def test_invalid_json_and_bad_schema_and_bool(self):
        invalids = (
            ("{", "invalid_json"),
            ('{"a":2}', "missing_required:b"),
            ('{"a":2,"b":3,"c":4}', "unexpected_properties:c"),
            ('{"a":true,"b":3}', "wrong_type:a"),
            ('[1,2]', "arguments_must_be_an_object"),
        )
        for payload, expected in invalids:
            with self.subTest(payload=payload):
                model = SequenceModel(roundtrip_reply(math_call(args=payload)),
                                      ModelReply(content="recovered"))
                result = AgentKernel(model, arithmetic_tools()).run("Bad arguments")
                self.assertEqual(result.status, "completed")
                self.assertEqual(json.loads(result.messages[-2]["content"])["error"], expected)

    def test_write_requires_explicit_permission(self):
        called: list[str] = []
        tools = ToolRegistry()
        tools.register(Tool(
            "write_note", "Write a note.",
            {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"], "additionalProperties": False},
            lambda text: called.append(text), read_only=False,
        ))
        model = SequenceModel(
            roundtrip_reply(ToolCall("write1", "write_note", '{"text":"example"}')),
            ModelReply(content="done"),
        )
        denied = AgentKernel(model, tools).run("Write")
        self.assertFalse(json.loads(denied.messages[-2]["content"])["ok"])
        self.assertEqual(json.loads(denied.messages[-2]["content"])["error"],
                         "write_requires_approval")
        self.assertEqual(called, [])
        allowed = AgentKernel(SequenceModel(
            roundtrip_reply(ToolCall("write2", "write_note", '{"text":"example"}')),
            ModelReply(content="done")), tools).run("Write", allow_writes=True)
        self.assertTrue(json.loads(allowed.messages[-2]["content"])["ok"])
        self.assertEqual(called, ["example"])

    def test_tool_exception_is_structured(self):
        tools = ToolRegistry()
        tools.register(Tool(
            "explode", "An example of tool failure.",
            {"type": "object", "properties": {}, "required": [],
             "additionalProperties": False},
            lambda: 1 / 0,
        ))
        result = AgentKernel(
            SequenceModel(roundtrip_reply(ToolCall("x", "explode", "{}")),
                          ModelReply(content="Handled")),
            tools,
        ).run("Error")
        obs = json.loads(result.messages[-2]["content"])
        self.assertEqual((obs["error"], obs["error_type"]),
                         ("tool_execution_failed", "ZeroDivisionError"))
        self.assertEqual(result.status, "completed")

    def test_model_error_does_not_run_tools(self):
        result = AgentKernel(SequenceModel(), arithmetic_tools()).run("No reply")
        self.assertEqual(result.status, "halted")
        self.assertEqual(result.reason, "model_error:StopIteration")
        self.assertEqual(result.tool_calls, 0)

    def test_budgets_prevent_unobserved_execution(self):
        model = SequenceModel(roundtrip_reply(math_call()))
        result = AgentKernel(model, arithmetic_tools(), max_model_calls=1).run("Budget")
        self.assertEqual((result.reason, result.tool_calls), ("max_model_calls", 0))
        result = AgentKernel(
            SequenceModel(roundtrip_reply(math_call(), math_call(id="b"))),
            arithmetic_tools(), max_tool_calls=1,
        ).run("Budget")
        self.assertEqual((result.reason, result.tool_calls), ("max_tool_calls", 0))

    def test_incomplete_reply_is_not_final(self):
        for reason in ("length", "content_filter", "refusal"):
            with self.subTest(reason=reason):
                result = AgentKernel(
                    SequenceModel(ModelReply(content="partial", finish_reason=reason)),
                    arithmetic_tools()
                ).run("Do not trust partial")
                self.assertEqual(result.status, "halted")
                self.assertEqual(result.reason, "model_" + reason)
                self.assertIsNone(result.output)

    def test_final_and_tool_call_protocol_errors(self):
        invalids = (
            (ModelReply(content=None), "missing_final_content"),
            (ModelReply(finish_reason="tool_calls"), "missing_tool_calls"),
            (ModelReply(tool_calls=(math_call(),)), "invalid_model_reply"),
            (ModelReply(content="text", finish_reason="other"), "unsupported_finish_reason"),
        )
        for reply, reason in invalids:
            with self.subTest(reason=reason):
                result = AgentKernel(SequenceModel(reply), arithmetic_tools()).run("Invalid")
                self.assertEqual(result.reason, reason)

    def test_truncated_observation_is_valid_json(self):
        tools = ToolRegistry()
        tools.register(Tool(
            "read", "Return a large value.",
            {"type": "object", "properties": {}, "required": [],
             "additionalProperties": False},
            lambda: "x" * 2000,
        ))
        model = SequenceModel(
            roundtrip_reply(ToolCall("id", "read", "{}")), ModelReply(content="done"),
        )
        result = AgentKernel(model, tools, max_observation_chars=300).run("Read")
        obs = json.loads(result.messages[-2]["content"])
        self.assertTrue(obs["truncated"])
        self.assertLessEqual(len(result.messages[-2]["content"]), 300)

    def test_schema_validation_of_registration(self):
        reg = ToolRegistry()
        bad = Tool("bad", "bad schema", {"type": "object", "properties": {},
                                        "required": []}, lambda: "x")
        with self.assertRaisesRegex(ValueError, "additionalProperties"):
            reg.register(bad)
        add_schema = arithmetic_tools().descriptions()[0]["function"]["parameters"]
        with self.assertRaisesRegex(ValueError, "wrong_type"):
            validate_arguments('{"a":true,"b":3}', add_schema)

    def test_registry_schema_is_copy(self):
        tools = arithmetic_tools()
        desc = tools.descriptions()
        desc[0]["function"]["parameters"]["properties"].clear()
        self.assertIn("a", tools.descriptions()[0]["function"]["parameters"]["properties"])


class ChatAdapterTests(unittest.TestCase):
    @staticmethod
    def _response(calls=None, content=None, reason="tool_calls"):
        return {
            "choices": [{
                "finish_reason": reason,
                "message": {"role": "assistant", "content": content,
                            "tool_calls": calls or []},
            }]
        }

    def test_wire_roundtrip_using_fake_transport(self):
        captured = []
        first = self._response(
            [{"id": "tool-1", "type": "function",
              "function": {"name": "add", "arguments": '{"a":1,"b":2}'}}],
        )
        final = self._response(content="3", reason="stop")
        responses = iter((first, final))

        def transport(payload):
            captured.append(payload)
            return next(responses)

        model = ChatCompletionsModel(
            model="test-model", api_key="not-a-real-secret",
            transport=transport,
        )
        result = AgentKernel(model, arithmetic_tools()).run("Calculate 1+2")
        self.assertEqual(result.output, "3")
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(captured[0]["tools"][0]["function"]["name"], "add")
        self.assertEqual(captured[1]["messages"][2]["tool_calls"][0]["id"], "tool-1")
        self.assertEqual(captured[1]["messages"][3]["tool_call_id"], "tool-1")

    def test_adapter_keeps_malformed_arguments_for_validation(self):
        response = self._response(
            [{"id": "tool-1", "type": "function",
              "function": {"name": "add", "arguments": "{invalid"}}],
        )
        model = ChatCompletionsModel(
            model="test", api_key="dummy", transport=lambda _: response,
        )
        parsed = model.complete([], [])
        self.assertEqual(parsed.tool_calls[0].arguments_json, "{invalid")

    def test_adapter_handles_finish_reason(self):
        for reason in ("length", "content_filter"):
            with self.subTest(reason=reason):
                parsed = parse_completion(self._response(
                    content="partial", reason=reason,
                ))
                self.assertEqual(parsed.finish_reason, reason)

    def test_adapter_rejects_malformed_provider_responses(self):
        for payload in ({}, {"choices": []},
                        self._response(calls=[{"id": "x"}])):
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                parse_completion(payload)

    def test_transport_url_requires_https_or_loopback(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            ChatCompletionsModel(
                model="x", api_key="x", base_url="http://example.org/v1",
            )
        model = ChatCompletionsModel(
            model="x", api_key="x", base_url="http://localhost:8000/v1",
            transport=lambda _: self._response(content="ok", reason="stop"),
        )
        self.assertEqual(model.complete([], []).content, "ok")


if __name__ == "__main__":
    unittest.main()
