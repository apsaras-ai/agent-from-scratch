"""Chapter 6: provider protocol, stream assembly, usage, and routing tests.

Tests are entirely offline. No credentials or external model calls.
"""
from __future__ import annotations

from collections import deque
import json
import unittest
from urllib.error import HTTPError, URLError

from mini_agent.agent_v2 import AgentKernel, ModelReply
from mini_agent.demo_tools import arithmetic_tools
from mini_agent.model_gateway import (
    AnthropicMessagesBackend, GatewayModel, GatewayPermanentError,
    GatewayProtocolError, GatewayResponse, GatewayTransientError,
    GatewayUnavailable, ModelGateway, OpenAIChatBackend, RetryPolicy,
    TokenUsage, _translate_transport_exception, anthropic_usage,
    openai_usage,
)
from mini_agent.gateway_stream import consume_anthropic, consume_openai, iter_sse


def chat_response(*, text=None, tool=None, usage=None, reason=None):
    if reason is None:
        reason = "tool_calls" if tool else "stop"
    return {
        "choices": [{
            "index": 0, "finish_reason": reason,
            "message": {
                "role": "assistant",
                "content": text,
                "tool_calls": [tool] if tool else [],
            },
        }],
        "usage": usage,
    }


def anthropic_response(*, text=None, tool=None, usage=None, reason=None):
    if reason is None:
        reason = "tool_use" if tool else "end_turn"
    blocks = []
    if text is not None:
        blocks.append({"type": "text", "text": text})
    if tool is not None:
        blocks.append(tool)
    return {"type": "message", "stop_reason": reason, "content": blocks, "usage": usage}


def openai_tool(cid="c1", name="add", args='{"a":2,"b":3}'):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


def anthro_tool(cid="t1", name="add", args=None):
    return {"type": "tool_use", "id": cid, "name": name, "input": args or {"a": 2, "b": 3}}


def sse_events(*items):
    """Arguments: (event type or None, body object or string)."""
    lines = []
    for event, payload in items:
        if event is not None:
            lines.append(f"event: {event}\n")
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        lines.append(f"data: {raw}\n")
        lines.append("\n")
    return lines


def anthropic_tool_stream():
    return sse_events(
        ("message_start", {"type": "message_start", "message": {
            "usage": {"input_tokens": 9, "cache_read_input_tokens": 3}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Call "}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("content_block_start", {"type": "content_block_start", "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "add", "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"a":'}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '2,"b":3}'}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ("message_delta", {"type": "message_delta",
            "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}}),
        ("message_stop", {"type": "message_stop"}),
    )


class UsageTests(unittest.TestCase):
    def test_openai_cache_is_subset_of_input(self):
        usage = openai_usage({
            "prompt_tokens": 200, "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 80},
        })
        self.assertEqual((usage.input_tokens, usage.output_tokens), (200, 30))
        self.assertEqual(usage.cached_read_input_tokens, 80)

    def test_anthropic_cache_tokens_are_additive_to_uncached(self):
        usage = anthropic_usage({
            "input_tokens": 100, "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 20, "output_tokens": 15,
        })
        self.assertEqual((usage.input_tokens, usage.output_tokens), (200, 15))
        self.assertEqual((usage.cached_read_input_tokens, usage.cached_write_input_tokens),
                         (80, 20))

    def test_missing_usage_is_unknown_not_zero(self):
        self.assertIsNone(openai_usage(None).input_tokens)
        self.assertIsNone(anthropic_usage(None).output_tokens)

    def test_invalid_usage_rejected(self):
        for fn, payload in (
            (openai_usage, {"prompt_tokens": -1}),
            (openai_usage, {"completion_tokens": True}),
            (anthropic_usage, {"input_tokens": "100"}),
        ):
            with self.subTest(payload=payload), self.assertRaises(GatewayProtocolError):
                fn(payload)


class AnthropicProtocolTests(unittest.TestCase):
    def test_tool_roundtrip_preserves_id_and_error_flag(self):
        bodies = []
        responses = deque([
            anthropic_response(tool=anthro_tool(), usage={"input_tokens": 10, "output_tokens": 6}),
            anthropic_response(text="5", usage={"input_tokens": 20, "output_tokens": 1}),
        ])

        def transport(body):
            bodies.append(body)
            return responses.popleft()

        backend = AnthropicMessagesBackend(
            name="anthropic", model="test-model", api_key="fake",
            max_tokens=256, transport=transport,
        )
        result = AgentKernel(
            GatewayModel(ModelGateway(
                {"anthropic": backend}, {"default": ("anthropic",)}
            )),
            arithmetic_tools(),
        ).run("Add 2 and 3")
        self.assertEqual((result.status, result.output), ("completed", "5"))
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(bodies[0]["model"], "test-model")
        self.assertEqual(bodies[0]["tools"][0]["input_schema"]["type"], "object")
        self.assertEqual(bodies[0]["system"], "You are a helpful assistant.")
        blocks = bodies[1]["messages"]
        self.assertEqual([b["role"] for b in blocks], ["user", "assistant", "user"])
        self.assertEqual(blocks[1]["content"][0]["id"], "t1")
        self.assertEqual(blocks[2]["content"][0]["tool_use_id"], "t1")
        self.assertFalse(blocks[2]["content"][0]["is_error"])
        self.assertIn('"result": 5', blocks[2]["content"][0]["content"])

    def test_error_tool_result_is_marked_error(self):
        bodies = []
        backend = AnthropicMessagesBackend(
            name="x", model="m", api_key="fake",
            transport=lambda body: bodies.append(body) or anthropic_response(text="Done"),
        )
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [openai_tool()]},
            {"role": "tool", "tool_call_id": "c1",
             "content": '{"ok":false,"error":"permission"}'},
        ]
        backend.complete(messages, arithmetic_tools().descriptions())
        self.assertTrue(bodies[0]["messages"][-1]["content"][0]["is_error"])

    def test_orphan_tool_result_and_unsupported_content_rejected(self):
        backend = AnthropicMessagesBackend(
            name="x", model="m", api_key="fake",
            transport=lambda _: anthropic_response(text="ok"),
        )
        with self.assertRaises(GatewayProtocolError):
            backend.complete([{"role": "tool", "tool_call_id": "missing", "content": "x"}], [])
        thinking = anthropic_response(reason="end_turn")
        thinking["content"] = [{"type": "thinking", "thinking": "internal"}]
        backend2 = AnthropicMessagesBackend(
            name="x", model="m", api_key="fake", transport=lambda _: thinking,
        )
        with self.assertRaises(GatewayProtocolError):
            backend2.complete([{"role": "user", "content": "hi"}], [])

    def test_pause_turn_never_becomes_fake_success(self):
        backend = AnthropicMessagesBackend(
            name="x", model="m", api_key="fake",
            transport=lambda _: anthropic_response(reason="pause_turn"),
        )
        with self.assertRaisesRegex(GatewayProtocolError, "stop_reason"):
            backend.complete([{"role": "user", "content": "hi"}], [])

    def test_truncation_not_success(self):
        backend = AnthropicMessagesBackend(
            name="x", model="m", api_key="fake",
            transport=lambda _: anthropic_response(text="partial", reason="max_tokens"),
        )
        self.assertEqual(
            backend.complete([{"role": "user", "content": "hi"}], []).reply.finish_reason,
            "length",
        )


class OpenAIProtocolTests(unittest.TestCase):
    def test_gateway_adapter_preserves_native_chat_messages(self):
        bodies = []
        responses = deque([
            chat_response(tool=openai_tool(), usage={"prompt_tokens": 15, "completion_tokens": 5}),
            chat_response(text="5", usage={"prompt_tokens": 27, "completion_tokens": 2}),
        ])
        backend = OpenAIChatBackend(
            name="chat", model="example-model", api_key="fake",
            transport=lambda body: bodies.append(body) or responses.popleft(),
        )
        model = GatewayModel(ModelGateway({"chat": backend}, {"default": ("chat",)}))
        result = AgentKernel(model, arithmetic_tools()).run("Add 2 and 3")
        self.assertEqual(result.output, "5")
        self.assertEqual(len(model.history), 2)
        self.assertEqual(sum(x.usage.output_tokens for x in model.history), 7)
        self.assertEqual(bodies[1]["messages"][2]["tool_calls"][0]["id"], "c1")
        self.assertEqual(bodies[1]["messages"][3]["tool_call_id"], "c1")
        self.assertEqual([r.attempts for r in model.history], [1, 1])

    def test_chat_stream_body_requests_usage(self):
        captured = []
        backend = OpenAIChatBackend(
            name="chat", model="m", api_key="fake",
            stream_transport=lambda b: captured.append(b) or sse_events(
                (None, {"choices": [{"index": 0, "delta": {"content": "OK"},
                                      "finish_reason": "stop"}]}),
                (None, {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
                (None, "[DONE]"),
            ),
        )
        stream = backend.stream([{"role": "user", "content": "hi"}], [])
        result = list(stream)[-1].response
        self.assertTrue(captured[0]["stream"])
        self.assertTrue(captured[0]["stream_options"]["include_usage"])
        self.assertEqual(result.reply.content, "OK")
        self.assertEqual(result.usage.input_tokens, 1)


class StreamingTests(unittest.TestCase):
    def test_sse_parses_comments_and_multiline_data(self):
        lines = [": ping\n", "event: sample\n", "data: a\n", "data: b\n", "\n"]
        self.assertEqual(list(iter_sse(lines)), [("sample", "a\nb")])

    def test_openai_stream_tool_chunks_and_final_usage(self):
        lines = sse_events(
            (None, {"choices": [{"index": 0, "delta": {"content": "Checking "}}]}),
            (None, {"choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": 0, "id": "call0", "type": "function",
                "function": {"name": "add", "arguments": '{"a":'}
            }]}}]}),
            (None, {"choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": 0, "function": {"arguments": '2,"b":3}'}
            }]}}]}),
            (None, {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
            (None, {"choices": [], "usage": {
                "prompt_tokens": 12, "completion_tokens": 8,
                "prompt_tokens_details": {"cached_tokens": 5},
            }}),
            (None, "[DONE]"),
        )
        events = list(consume_openai(lines, provider="chat", model="m"))
        self.assertEqual([e.kind for e in events],
                         ["text_delta", "tool_call", "usage", "done"])
        result = events[-1].response
        self.assertEqual(result.reply.finish_reason, "tool_calls")
        self.assertEqual(result.reply.tool_calls[0].arguments_json, '{"a":2,"b":3}')
        self.assertEqual(result.usage.input_tokens, 12)
        self.assertEqual(result.usage.cached_read_input_tokens, 5)

    def test_openai_tool_stream_without_done_is_not_executable(self):
        lines = sse_events(
            (None, {"choices": [{"index": 0, "delta": {
                "tool_calls": [{"index": 0, "id": "call0",
                                "function": {"name": "add", "arguments": '{"a":2}'}}]},
                "finish_reason": "tool_calls"}]})
        )
        it = consume_openai(lines, provider="chat", model="m")
        with self.assertRaisesRegex(GatewayProtocolError, "missing_done"):
            list(it)

    def test_openai_duplicate_or_inconsistent_id(self):
        lines = sse_events(
            (None, {"choices": [{"index": 0, "delta": {
                "tool_calls": [{"index": 0, "id": "a",
                                "function": {"name": "add", "arguments": "{}"}}]}}]}),
            (None, {"choices": [{"index": 0, "delta": {
                "tool_calls": [{"index": 0, "id": "b", "function": {}}]}}]}),
            (None, "[DONE]"),
        )
        with self.assertRaisesRegex(GatewayProtocolError, "inconsistent"):
            list(consume_openai(lines, provider="chat", model="m"))

    def test_anthropic_stream_assembles_tool_json_and_usage(self):
        events = list(consume_anthropic(
            anthropic_tool_stream(), provider="anthropic", model="m"
        ))
        self.assertEqual([e.kind for e in events],
                         ["text_delta", "tool_call", "usage", "done"])
        result = events[-1].response
        self.assertEqual(result.reply.content, "Call ")
        self.assertEqual(result.reply.finish_reason, "tool_calls")
        self.assertEqual(
            json.loads(result.reply.tool_calls[0].arguments_json),
            {"a": 2, "b": 3},
        )
        self.assertEqual((result.usage.input_tokens, result.usage.output_tokens), (12, 7))

    def test_anthropic_stream_incomplete_tool_never_emitted(self):
        lines = anthropic_tool_stream()[:-3]  # truncate before message_stop
        with self.assertRaises(GatewayProtocolError):
            list(consume_anthropic(lines, provider="anthropic", model="m"))

    def test_anthropic_non_json_tool_fragments_not_executed_or_silently_fixed(self):
        lines = sse_events(
            ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
               "content_block": {"type": "tool_use", "id": "x", "name": "add", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
               "delta": {"type": "input_json_delta", "partial_json": '{"a":'}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                               "usage": {"output_tokens": 2}}),
            ("message_stop", {"type": "message_stop"}),
        )
        result = list(consume_anthropic(lines, provider="anthropic", model="m"))[-1].response
        self.assertEqual(result.reply.tool_calls[0].arguments_json, '{"a":')
        # The Chapter 4 ToolRegistry will reject this as invalid_json.


class RoutingAndRetryTests(unittest.TestCase):
    class Stub:
        def __init__(self, name, responses):
            self.name = name
            self.replies = iter(responses)
            self.count = 0
            self.stream_count = 0

        def complete(self, messages, tools):
            self.count += 1
            response = next(self.replies)
            if isinstance(response, Exception):
                raise response
            return GatewayResponse(ModelReply(content=response), TokenUsage(),
                                   self.name, self.name)

        def stream(self, messages, tools):
            self.stream_count += 1
            return iter([])

    def test_retry_then_success_without_fallback(self):
        primary = self.Stub("primary", [
            GatewayTransientError("too_many_requests", http_status=429), "OK",
        ])
        secondary = self.Stub("backup", ["unused"])
        slept = []
        gateway = ModelGateway(
            {"primary": primary, "backup": secondary},
            {"default": ("primary", "backup")},
            retry=RetryPolicy(max_attempts=2, base_delay=0.05, max_delay=1.0),
            sleep=slept.append,
        )
        result = gateway.complete([], [])
        self.assertEqual((result.provider, result.attempts), ("primary", 2))
        self.assertEqual(slept, [0.05])
        self.assertEqual(secondary.count, 0)

    def test_fallback_after_exhausting_transient_failures(self):
        first = self.Stub("first", [GatewayTransientError("down")] * 2)
        second = self.Stub("second", ["Recovered"])
        gateway = ModelGateway(
            {"a": first, "b": second}, {"default": ("a", "b")},
            retry=RetryPolicy(max_attempts=2, base_delay=0), sleep=lambda _: None,
        )
        result = gateway.complete([], [])
        self.assertEqual((result.provider, result.attempts), ("second", 3))
        self.assertEqual(result.reply.content, "Recovered")
        self.assertEqual((first.count, second.count), (2, 1))

    def test_permanent_auth_error_does_not_fallback(self):
        primary = self.Stub("a", [GatewayPermanentError("401")])
        backup = self.Stub("b", ["should not be used"])
        gw = ModelGateway({"a": primary, "b": backup}, {"default": ("a", "b")})
        with self.assertRaises(GatewayPermanentError):
            gw.complete([], [])
        self.assertEqual(backup.count, 0)

    def test_unavailable_after_all_fail(self):
        primary = self.Stub("a", [GatewayTransientError("down")])
        gw = ModelGateway({"a": primary}, {"default": ("a",)},
                          retry=RetryPolicy(max_attempts=1), sleep=lambda _: None)
        with self.assertRaises(GatewayUnavailable):
            gw.complete([], [])

    def test_static_route_selection_not_auto_model_quality(self):
        fast = self.Stub("fast", ["quick"])
        deep = self.Stub("deep", ["deep"])
        gw = ModelGateway(
            {"fast": fast, "deep": deep},
            {"default": ("fast",), "analysis": ("deep",)},
        )
        self.assertEqual(gw.complete([], [], route="analysis").reply.content, "deep")
        self.assertEqual(fast.count, 0)

    def test_stream_uses_primary_only_without_fallback(self):
        primary = self.Stub("a", [])
        backup = self.Stub("b", [])
        gw = ModelGateway(
            {"a": primary, "b": backup},
            {"default": ("a", "b")},
        )
        list(gw.stream([], []))
        self.assertEqual((primary.stream_count, backup.stream_count), (1, 0))

    def test_retry_classification(self):
        for status in (429, 502, 503):
            err = _translate_transport_exception(
                HTTPError("https://example.org", status, "no", {}, None)
            )
            self.assertIsInstance(err, GatewayTransientError)
        self.assertIsInstance(
            _translate_transport_exception(HTTPError(
                "https://example.org", 401, "no", {}, None,
            )),
            GatewayPermanentError,
        )
        self.assertIsInstance(
            _translate_transport_exception(URLError("network")),
            GatewayTransientError,
        )

    def test_bad_route_config(self):
        with self.assertRaises(ValueError):
            ModelGateway({"a": self.Stub("a", [])}, {"default": ("missing",)})
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)

    def test_credentials_do_not_enter_requests(self):
        backend = AnthropicMessagesBackend(
            name="x", model="m", api_key="secret-credential",
            transport=lambda payload: anthropic_response(text="OK"),
        )
        result = backend.complete([{"role": "user", "content": "hi"}], [])
        self.assertEqual(result.reply.content, "OK")
        self.assertNotIn("secret-credential", repr(result))
        self.assertNotIn("secret-credential", repr(backend._body(
            [{"role": "user", "content": "hi"}], []
        )))


if __name__ == "__main__":
    unittest.main()
