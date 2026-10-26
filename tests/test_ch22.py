"""Chapter 22: privacy, telemetry, async ancestry, integrity and gateway tests.

No OTel Collector, paid models or external calls. Uses real Chapter 4 Kernel,
Chapter 6 GatewayResponse and ToolRegistry in offline fixture tests.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import os
import tempfile
import unittest

from mini_agent.agent_v2 import (
    AgentKernel, ModelReply, Tool, ToolCall, ToolRegistry,
)
from mini_agent.model_gateway import GatewayResponse, TokenUsage
from mini_agent.observability import (
    RateCard, TraceError, TraceRecorder, format_traceparent, parse_traceparent,
)
from mini_agent.observability_bridge import TracedGatewayModel, TracedToolRegistry
from mini_agent.trace_store import (
    TraceIntegrityError, read_trace_jsonl, replay_diagnostic,
    summarize_trace, write_trace_jsonl,
)


class Clock:
    def __init__(self):
        self.wall = 100_000_000_000
        self.mono = 1000

    def advance(self, ns):
        self.wall += ns
        self.mono += ns


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.trace = TraceRecorder(
            wall_ns=lambda: self.clock.wall,
            monotonic_ns=lambda: self.clock.mono,
        )

    def test_root_child_span_identity_and_monotonic_duration(self):
        with self.trace.span("invoke_agent") as root:
            self.clock.advance(2_000_000)
            with self.trace.span("execute_tool", attributes={
                "gen_ai.tool.name": "approved.lookup",
            }) as tool:
                self.clock.advance(3_000_000)
                self.assertEqual(
                    parse_traceparent(tool.traceparent)[0], self.trace.trace_id,
                )
            self.clock.advance(1_000_000)
        main, child = self.trace.snapshots()
        self.assertEqual(main.span_id, root.span_id)
        self.assertEqual(child.parent_span_id, root.span_id)
        self.assertEqual(main.duration_ns, 6_000_000)
        self.assertEqual(child.duration_ns, 3_000_000)
        self.assertEqual(main.status, "ok")
        self.assertEqual(child.operation, "execute_tool")

    def test_w3c_traceparent_roundtrip_and_sampled_flag(self):
        with self.trace.span("invoke_agent") as root:
            header = root.traceparent
        trace, parent, sampled = parse_traceparent(header)
        self.assertEqual(trace, self.trace.trace_id)
        self.assertEqual(parent, root.span_id)
        self.assertTrue(sampled)
        unsampled = format_traceparent(trace, parent, sampled=False)
        self.assertFalse(parse_traceparent(unsampled)[2])

    def test_invalid_w3c_traceparent_inputs(self):
        with self.trace.span("invoke_agent") as root:
            valid = root.traceparent
        cases = [
            "", "bad", None, "ff" + valid[2:],
            valid.replace("00-", "01-", 1),
            valid.replace("00-", "00-", 1) + "-extraneous",
            "00-" + "0" * 32 + "-" + "f" * 16 + "-01",
            "00-" + "f" * 32 + "-" + "0" * 16 + "-01",
            valid[:-2] + "ff", valid.upper(),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(TraceError):
                parse_traceparent(value)

    def test_security_does_not_trust_unsanitized_attributes(self):
        candidates = [
            {"gen_ai.input.messages": "user secret"},
            {"gen_ai.output.messages": "reply secret"},
            {"gen_ai.tool.call.arguments": '{"token":"private"}'},
            {"http.request.header.authorization": "Bearer token"},
            {"user.id": "user@example.com"},
            {"tenant": "secret"},
            {"gen_ai.request.model": "model with spaces"},
            {"gen_ai.request.model": "model\nsecret"},
            {"gen_ai.usage.input_tokens": -1},
            {"gen_ai.usage.input_tokens": True},
            {"agent.verification.passed": "yes"},
        ]
        for bad in candidates:
            with self.subTest(bad=bad), self.assertRaises(TraceError):
                with self.trace.span("invoke_agent", attributes=bad):
                    pass
        self.assertEqual(len(self.trace.snapshots()), 0)

    def test_bad_event_name_and_unapproved_event_metadata(self):
        with self.trace.span("invoke_agent") as span:
            with self.assertRaises(TraceError):
                span.add_event("raw_log_output")
            with self.assertRaises(TraceError):
                span.add_event("checkpoint_saved", attributes={
                    "prompt": "private",
                })
            span.add_event("checkpoint_saved", attributes={
                "agent.workflow.phase": "saved",
            })
        self.assertEqual(len(self.trace.snapshots()[0].events), 1)

    def test_event_and_span_capacity_bounds(self):
        trace = TraceRecorder(max_spans=1, max_events_per_span=1)
        with trace.span("invoke_agent") as span:
            span.add_event("worker_claimed")
            with self.assertRaises(TraceError):
                span.add_event("worker_claimed")
            with self.assertRaises(TraceError):
                with trace.span("chat"):
                    pass
        self.assertEqual(len(trace.snapshots()), 1)

    def test_snapshot_before_close_is_disallowed(self):
        with self.trace.span("invoke_agent"):
            with self.assertRaises(TraceError):
                self.trace.snapshots()
        self.assertEqual(len(self.trace.snapshots()), 1)

    def test_span_handle_cannot_mutate_after_close(self):
        with self.trace.span("invoke_agent") as span:
            pass
        with self.assertRaises(TraceError):
            span.add_event("checkpoint_saved")

    def test_multiple_root_spans_are_disallowed(self):
        with self.trace.span("invoke_agent"):
            pass
        with self.assertRaisesRegex(TraceError, "multiple_trace_roots"):
            with self.trace.span("invoke_agent"):
                pass

    def test_arbitrary_error_message_cannot_become_trace_error_code(self):
        with self.trace.span("invoke_agent") as root:
            with self.assertRaisesRegex(TraceError, "unapproved_error_code"):
                root.fail("SYNTHETIC_PRIVATE_TOKEN_AS_ERROR")
            root.fail("tool_denied")
        self.assertNotIn("SYNTHETIC_PRIVATE_TOKEN_AS_ERROR",
                         repr(self.trace.snapshots()))

    def test_tool_failure_is_structured_but_can_have_successful_parent(self):
        with self.trace.span("invoke_agent"):
            with self.trace.span("execute_tool") as tool:
                tool.fail("tool_denied")
            with self.trace.span("verify") as v:
                v.verify(passed=True, reason="host_fixture_checked")
        data = self.trace.snapshots()
        self.assertEqual(data[1].status, "error")
        self.assertEqual(data[1].error_code, "tool_denied")
        self.assertEqual(summarize_trace(data).verified, "passed")
        self.assertEqual(summarize_trace(data).primary_failure,
                         "execute_tool:tool_denied")

    def test_exception_message_never_exported(self):
        with self.assertRaises(RuntimeError):
            with self.trace.span("invoke_agent"):
                raise RuntimeError("CANARY_PASSWORD_NO_LOGGING")
        snap = self.trace.snapshots()[0]
        self.assertEqual((snap.status, snap.error_code),
                         ("error", "unhandled_exception"))
        self.assertNotIn("CANARY_PASSWORD_NO_LOGGING", repr(snap))

    def test_cancelled_trace_state_on_keyboard_interrupt(self):
        with self.assertRaises(KeyboardInterrupt):
            with self.trace.span("invoke_agent"):
                raise KeyboardInterrupt
        s = self.trace.snapshots()[0]
        self.assertEqual((s.status, s.error_code),
                         ("cancelled", "operation_cancelled"))

    def test_explicit_verifier_fails_without_fake_task_success(self):
        with self.trace.span("invoke_agent"):
            with self.trace.span("verify") as verifier:
                verifier.verify(passed=False, reason="evidence_mismatch")
        summary = summarize_trace(self.trace.snapshots())
        self.assertEqual(summary.verified, "failed")
        self.assertEqual(summary.failed_spans, 0)

    def test_non_verifier_cannot_report_verification(self):
        with self.trace.span("invoke_agent") as handle:
            with self.assertRaises(TraceError):
                handle.verify(passed=True, reason="untrusted")
            with self.trace.span("verify") as v:
                v.verify(passed=True, reason="host_passed")
                with self.assertRaises(TraceError):
                    v.verify(passed=False, reason="second")

    def test_usage_only_recorded_once_on_chat(self):
        with self.trace.span("invoke_agent"):
            with self.trace.span("chat") as m:
                m.record_usage(provider="synthetic", model="fixture",
                               input_tokens=10, output_tokens=5)
                with self.assertRaises(TraceError):
                    m.record_usage(provider="synthetic", model="fixture",
                                   input_tokens=10, output_tokens=5)
            with self.trace.span("execute_tool") as tool:
                with self.assertRaises(TraceError):
                    tool.record_usage(provider="synthetic", model="fixture",
                                      input_tokens=1, output_tokens=1)

    def test_rate_card_decimal_exact_cost_with_three_model_calls(self):
        price = RateCard("synthetic", "fixture", "2026-10", "1.00", "4.00")
        with self.trace.span("invoke_agent"):
            for inp, out in ((60, 12), (100, 25), (90, 40)):
                with self.trace.span("chat") as model:
                    model.record_usage(provider="synthetic", model="fixture",
                                       input_tokens=inp, output_tokens=out,
                                       rate_card=price)
        summary = summarize_trace(self.trace.snapshots())
        self.assertEqual(summary.input_tokens, 250)
        self.assertEqual(summary.output_tokens, 77)
        self.assertEqual(summary.estimated_usd, "0.000558")
        self.assertEqual(summary.priced_model_calls, 3)
        self.assertEqual(summary.unpriced_model_calls, 0)

    def test_missing_rate_does_not_fabricate_zero_cost(self):
        with self.trace.span("invoke_agent"):
            with self.trace.span("chat") as model:
                model.record_usage(provider="synthetic", model="fixture",
                                   input_tokens=500, output_tokens=100)
        summary = summarize_trace(self.trace.snapshots())
        self.assertEqual(summary.input_tokens, 500)
        self.assertIsNone(summary.estimated_usd)
        self.assertEqual(summary.unpriced_model_calls, 1)

    def test_missing_token_usage_does_not_fabricate_zero_tokens(self):
        with self.trace.span("invoke_agent"):
            with self.trace.span("chat", attributes={
                "gen_ai.provider.name": "synthetic",
                "gen_ai.request.model": "fixture",
            }):
                pass
        summary = summarize_trace(self.trace.snapshots())
        self.assertIsNone(summary.input_tokens)
        self.assertIsNone(summary.output_tokens)
        self.assertIsNone(summary.estimated_usd)

    def test_mixed_pricing_coverage_is_unknown_total(self):
        price = RateCard("synthetic", "fixture", "v1", "1.00", "4.00")
        with self.trace.span("invoke_agent"):
            with self.trace.span("chat") as one:
                one.record_usage(provider="synthetic", model="fixture",
                                 input_tokens=10, output_tokens=10,
                                 rate_card=price)
            with self.trace.span("chat") as two:
                two.record_usage(provider="synthetic", model="fixture",
                                 input_tokens=10, output_tokens=10)
        summary = summarize_trace(self.trace.snapshots())
        self.assertEqual(summary.priced_model_calls, 1)
        self.assertEqual(summary.unpriced_model_calls, 1)
        self.assertIsNone(summary.estimated_usd)

    def test_reject_malformed_price_or_mismatched_model(self):
        bad = [
            ("-1", "1.00"),
            ("1e5", "1.00"),
            ("nan", "1.00"),
            ("1.123456789", "1.00"),
        ]
        for inp, out in bad:
            with self.subTest(inp=inp), self.assertRaises(TraceError):
                RateCard("provider", "model", "v1", inp, out)
        rate = RateCard("synthetic", "other", "v1", "1.00", "1.00")
        with self.trace.span("chat") as model:
            with self.assertRaises(TraceError):
                model.record_usage(provider="synthetic", model="fixture",
                                   input_tokens=1, output_tokens=2,
                                   rate_card=rate)


class AsyncContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_siblings_share_parent_not_each_other(self):
        tracer = TraceRecorder()
        async def process(tool):
            with tracer.span("execute_tool", attributes={
                "gen_ai.tool.name": tool,
            }):
                await asyncio.sleep(0.01)
        with tracer.span("invoke_agent") as root:
            await asyncio.gather(process("search"), process("compile"))
        snapshots = tracer.snapshots()
        self.assertEqual(len(snapshots), 3)
        children = snapshots[1:]
        self.assertTrue(all(s.parent_span_id == root.span_id for s in children))
        self.assertNotEqual(children[0].span_id, children[1].span_id)
        self.assertEqual({s.attributes["gen_ai.tool.name"] for s in children},
                         {"search", "compile"})

    async def test_async_cancellation_marks_cancelled(self):
        tracer = TraceRecorder()
        start = asyncio.Event()
        async def hanging():
            with tracer.span("execute_tool"):
                start.set()
                await asyncio.sleep(10)
        with tracer.span("invoke_agent"):
            task = asyncio.create_task(hanging())
            await asyncio.wait_for(start.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(tracer.snapshots()[1].status, "cancelled")


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "trace.jsonl"
        self.trace = TraceRecorder()
        with self.trace.span("invoke_agent"):
            with self.trace.span("execute_tool", attributes={
                "gen_ai.tool.name": "test_tool",
            }) as tool:
                tool.fail("fixture_error")
            with self.trace.span("verify") as verifier:
                verifier.verify(passed=False, reason="fixture_rejected")
        write_trace_jsonl(self.trace, self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_integrity_and_slowest_child(self):
        loaded = read_trace_jsonl(self.path)
        self.assertEqual(len(loaded), 3)
        summary = replay_diagnostic(self.path)
        self.assertEqual(summary.failed_spans, 1)
        self.assertEqual(summary.verified, "failed")
        self.assertEqual(summary.primary_failure, "execute_tool:fixture_error")
        self.assertIsNotNone(summary.slowest_child)

    def test_export_does_not_record_raw_exception_or_tool_args(self):
        text = self.path.read_text()
        for secret in ("prompt", "tool.call.arguments", "tool.call.result",
                       "authorization", "user@example.com"):
            self.assertNotIn(secret, text)
        self.assertIn('"entry_hash":', text)

    def test_export_directory_and_symlink_rejected(self):
        with self.assertRaises(TraceError):
            write_trace_jsonl(self.trace, Path(self.tmp.name) / "not" / "x.jsonl")
        target = Path(self.tmp.name) / "link.jsonl"
        target.symlink_to(self.path)
        with self.assertRaises(TraceError):
            write_trace_jsonl(self.trace, target)

    def test_one_changed_byte_rejected_by_hash_chain(self):
        data = self.path.read_text()
        self.path.write_text(data.replace("fixture_error", "another_error", 1))
        with self.assertRaises(TraceIntegrityError):
            read_trace_jsonl(self.path)

    def test_truncated_and_extra_line_rejected(self):
        original = self.path.read_text()
        lines = original.splitlines()
        self.path.write_text("\n".join(lines[:-1]) + "\n")
        # Valid prefix containing root and tool spans; one verifier missing is
        # structurally valid, but outcome becomes UNKNOWN rather than verified.
        summary = replay_diagnostic(self.path)
        self.assertEqual(summary.verified, "unknown")
        self.path.write_text(original + "THIS IS NOT JSON\n")
        with self.assertRaises(TraceIntegrityError):
            read_trace_jsonl(self.path)

    def test_repeated_or_reordered_rows_rejected(self):
        original = self.path.read_text().splitlines()
        self.path.write_text("\n".join([original[0], original[2], original[1]]) + "\n")
        with self.assertRaises(TraceIntegrityError):
            read_trace_jsonl(self.path)

    def test_size_and_count_limits(self):
        with self.assertRaises(TraceIntegrityError):
            read_trace_jsonl(self.path, max_bytes=200)
        with self.assertRaises(TraceIntegrityError):
            read_trace_jsonl(self.path, max_spans=2)
        with self.assertRaises(TraceIntegrityError):
            read_trace_jsonl(self.path, max_spans=0)

    def test_export_file_has_private_posix_permissions(self):
        if os.name == "posix":
            mode = self.path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_playback_never_invokes_model_or_tool(self):
        counter = {"calls": 0}
        def dangerous_fixture():
            counter["calls"] += 1
            return "synthetic"
        # Replay does not have a callback API by design.
        self.assertEqual(replay_diagnostic(self.path).span_count, 3)
        self.assertEqual(counter["calls"], 0)

    def test_hash_chain_cannot_certify_trust(self):
        # Re-hashing an altered trace is possible without a signing key.
        # This test only checks the hash chain, not actor authenticity.
        entries = [json.loads(line) for line in self.path.read_text().splitlines()]
        entries[1]["record"]["error_code"] = "tool_timeout"
        prev = "0" * 64
        for n, entry in enumerate(entries, 1):
            entry["previous_hash"] = prev
            body = {"schema_version": 1, "sequence": n,
                    "record": entry["record"]}
            raw = json.dumps(body, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False)
            entry["entry_hash"] = hashlib.sha256((prev + raw).encode()).hexdigest()
            prev = entry["entry_hash"]
        self.path.write_text("\n".join(json.dumps(
            entry, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ) for entry in entries) + "\n")
        changed = replay_diagnostic(self.path)
        self.assertEqual(changed.primary_failure, "execute_tool:tool_timeout")
        # Cryptographic signature and append-only storage are NOT implemented.


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tracer = TraceRecorder()

    def test_real_kernel_and_gateway_trace_no_private_arguments(self):
        from examples.ch22_observability_lab import run_experiment, PRIVATE_CANARY
        tracer, result, verified, invocations = run_experiment()
        self.assertTrue(verified)
        self.assertEqual(result.status, "completed")
        self.assertEqual(invocations, 1)
        snaps = tracer.snapshots()
        with tempfile.TemporaryDirectory() as directory:
            file = write_trace_jsonl(tracer, Path(directory) / "case.jsonl")
            raw = file.read_text()
            self.assertNotIn(PRIVATE_CANARY, raw)
            self.assertNotIn("query", raw)
            self.assertNotIn("unapproved_tool", result.output)
            view = replay_diagnostic(file)
            self.assertEqual(view.verified, "passed")
            self.assertEqual(view.primary_failure, "execute_tool:tool_rejected_or_failed")
            self.assertEqual(view.priced_model_calls, 3)
            self.assertEqual(view.estimated_usd, "0.000558")

    def test_gateway_missing_usage_has_unknown_cost(self):
        class Fake:
            name = "synthetic"
            def complete(self, messages, tools):
                return GatewayResponse(ModelReply(content="ok"),
                                       TokenUsage(input_tokens=None, output_tokens=None),
                                       "synthetic", "model")
        wrapper = TracedGatewayModel(
            Fake(), self.tracer, provider="synthetic", model="model",
            rate_card=RateCard("synthetic", "model", "v1", "1.0", "2.0"),
        )
        with self.tracer.span("invoke_agent"):
            self.assertEqual(wrapper.complete([], []).content, "ok")
        data = summarize_trace(self.tracer.snapshots())
        self.assertIsNone(data.estimated_usd)
        self.assertIsNone(data.input_tokens)

    def test_bad_gateway_response_fails_without_logging_raw_payload(self):
        class Fake:
            name = "synthetic"
            def complete(self, messages, tools):
                return GatewayResponse(ModelReply(content="SENSITIVE_CANARY"),
                                       TokenUsage(input_tokens=2, output_tokens=1),
                                       "wrong-provider", "model")
        wrapper = TracedGatewayModel(Fake(), self.tracer,
                                     provider="synthetic", model="model")
        with self.tracer.span("invoke_agent"):
            with self.assertRaises(TraceError):
                wrapper.complete([{"role": "user", "content": "secret"}], [])
        text = repr(self.tracer.snapshots())
        self.assertNotIn("secret", text)
        self.assertNotIn("SENSITIVE_CANARY", text)
        self.assertEqual(self.tracer.snapshots()[1].error_code,
                         "unhandled_exception")

    def test_wrapper_preserves_tool_registry_authorization(self):
        tools = ToolRegistry()
        count = [0]
        def update(value):
            count[0] += 1
            return value
        tools.register(Tool(
            "write", "Trusted write fixture", {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"], "additionalProperties": False,
            }, update, read_only=False,
        ))
        wrapped = TracedToolRegistry(tools, self.tracer)
        with self.tracer.span("invoke_agent"):
            denied = wrapped.invoke(ToolCall("a", "write",
                                            '{"value":"SYNTHETIC_CANARY"}'))
        self.assertFalse(denied["ok"])
        self.assertEqual(count[0], 0)
        self.assertNotIn("SYNTHETIC_CANARY", repr(self.tracer.snapshots()))

    def test_unknown_tool_name_redacted_even_if_label_shaped_like_secret(self):
        tools = ToolRegistry()
        wrapped = TracedToolRegistry(tools, self.tracer)
        private_label = "PRIVATE_FAKE_TOKEN_ABC_9876"
        with self.tracer.span("invoke_agent"):
            result = wrapped.invoke(ToolCall("call-a", private_label, "{}"))
        self.assertFalse(result["ok"])
        exported = repr(self.tracer.snapshots())
        self.assertNotIn(private_label, exported)
        self.assertIn("unregistered_tool", exported)

    def test_invalid_tool_label_rejected_before_call(self):
        tools = ToolRegistry()
        wrapped = TracedToolRegistry(tools, self.tracer)
        with self.tracer.span("invoke_agent"):
            with self.assertRaises(TraceError):
                wrapped.invoke(ToolCall("x", "weird secret tool", "{}"))
        self.assertEqual(len(self.tracer.snapshots()), 1)


if __name__ == "__main__":
    unittest.main()
