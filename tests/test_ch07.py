"""Chapter 7: JSON Schema, async tool policy, concurrency and MCP adapter."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import unittest

from mini_agent.agent_v2 import ToolCall
from mini_agent.async_agent import AsyncToolAgent
from mini_agent.demo_model import RuleBasedDemoModel
from mini_agent.mcp_tools import MCPToolSource
from mini_agent.tool_runtime import (
    AsyncToolRuntime, Risk, ToolCatalog, ToolDefinition, ToolExecutionError,
    ToolPolicy,
)


MATH_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "numbers": {
            "type": "array", "minItems": 2, "maxItems": 3,
            "items": {"type": "integer", "minimum": 0},
        },
        "mode": {"enum": ["sum", "max"]},
    },
    "required": ["numbers", "mode"],
    "additionalProperties": False,
}


def policy(*tools, run="r", approvals=(), destructive=False, max_calls=8):
    return ToolPolicy(
        "tenant", run, frozenset(tools), frozenset(approvals),
        destructive, max_calls,
    )


class ToolRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def compute(numbers, mode):
            return {"total": sum(numbers) if mode == "sum" else max(numbers)}
        self.catalog = ToolCatalog()
        self.catalog.register(ToolDefinition(
            "math.compute", "Compute on nonnegative integers", MATH_SCHEMA,
            compute, Risk.READ, {"type": "object", "properties": {
                "total": {"type": "integer"}}, "required": ["total"],
                "additionalProperties": False},
        ))
        self.runtime = AsyncToolRuntime(self.catalog, timeout_seconds=0.3)

    async def test_nested_json_schema_and_output(self):
        call = ToolCall("id1", "math.compute",
                        '{"numbers":[1,2,3],"mode":"sum"}')
        result = await self.runtime.execute_one(call, policy("math.compute"))
        self.assertTrue(result.ok)
        self.assertEqual(result.value, {"total": 6})
        self.assertEqual(json.loads(result.observation())["result"]["total"], 6)

    async def test_schema_invalid_nested_enum_and_additional(self):
        invalids = [
            '{"numbers":[1,-2],"mode":"sum"}',
            '{"numbers":[1,2],"mode":"multiply"}',
            '{"numbers":[1,2],"mode":"sum","command":"x"}',
            '{"numbers":[1],"mode":"sum"}',
            '{"numbers":[true,2],"mode":"sum"}',
        ]
        for idx, raw in enumerate(invalids):
            with self.subTest(raw=raw):
                res = await self.runtime.execute_one(
                    ToolCall(f"bad-{idx}", "math.compute", raw),
                    policy("math.compute"))
                self.assertEqual(res.error, "schema_validation_failed")

    async def test_duplicate_keys_nonfinite_and_nonobject(self):
        invalids = [
            ('{"numbers":[1,2],"mode":"sum","mode":"max"}', "duplicate_json_key"),
            ('{"numbers":[1,2],"mode":NaN}', "invalid_json_constant"),
            ('[1,2]', "arguments_must_be_object"),
            ("{", "invalid_json"),
        ]
        for i, (raw, code) in enumerate(invalids):
            result = await self.runtime.execute_one(
                ToolCall(f"json-{i}", "math.compute", raw),
                policy("math.compute"))
            self.assertEqual(result.error, code)

    async def test_model_tool_visibility_follows_allowlist(self):
        self.assertEqual(len(self.catalog.specs_for(policy())), 0)
        specs = self.catalog.specs_for(policy("math.compute"))
        self.assertEqual(specs[0]["function"]["name"], "math.compute")
        specs[0]["function"]["parameters"]["properties"].clear()
        self.assertIn("numbers", self.catalog.specs_for(policy("math.compute"))[0]["function"]["parameters"]["properties"])

    async def test_unknown_and_denied(self):
        denied = await self.runtime.execute_one(
            ToolCall("d1", "math.compute", "{}"), policy())
        self.assertEqual(denied.error, "tool_not_allowed")
        missing = await self.runtime.execute_one(
            ToolCall("d2", "unknown", "{}"), policy("unknown"))
        self.assertEqual(missing.error, "unknown_tool")

    async def test_write_requires_specific_approval_and_is_not_retried(self):
        changes = []
        async def write(text):
            changes.append(text)
            return {"saved": True}
        self.catalog.register(ToolDefinition(
            "report.write", "Writes a report", {
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 20}},
                "required": ["text"], "additionalProperties": False,
            }, write, Risk.WRITE,
        ))
        call = ToolCall("call-write", "report.write", '{"text":"ok"}')
        denied = await self.runtime.execute_one(call, policy("report.write", run="a"))
        self.assertEqual(denied.error, "approval_required")
        wrong_approval = await self.runtime.execute_one(
            ToolCall("call-write-2", "report.write", '{"text":"ok"}'),
            policy("report.write", run="b", approvals=[("report.write", "other")]))
        self.assertEqual(wrong_approval.error, "approval_required")
        approved = await self.runtime.execute_one(
            call, policy("report.write", run="c",
                         approvals=[("report.write", "call-write")]))
        self.assertTrue(approved.ok)
        self.assertEqual(changes, ["ok"])
        duplicate = await self.runtime.execute_one(
            call, policy("report.write", run="c",
                         approvals=[("report.write", "call-write")]))
        self.assertEqual(duplicate.error, "duplicate_call_id")
        self.assertEqual(changes, ["ok"])

    async def test_destructive_requires_two_gates(self):
        acted = []
        async def dangerous():
            acted.append("yes")
            return True
        self.catalog.register(ToolDefinition(
            "repo.delete", "Destructive example", {
                "type": "object", "properties": {},
                "additionalProperties": False,
            }, dangerous, Risk.DESTRUCTIVE,
        ))
        p = policy("repo.delete", run="d1",
                   approvals=[("repo.delete", "x")])
        denied = await self.runtime.execute_one(
            ToolCall("x", "repo.delete", "{}"), p)
        self.assertEqual(denied.error, "destructive_tool_denied")
        res = await self.runtime.execute_one(
            ToolCall("x", "repo.delete", "{}"),
            policy("repo.delete", run="d2", destructive=True,
                   approvals=[("repo.delete", "x")]))
        self.assertTrue(res.ok)
        self.assertEqual(acted, ["yes"])

    async def test_batch_duplicate_ids_and_budget_fail_before_effects(self):
        calls = [
            ToolCall("same", "math.compute", '{"numbers":[1,2],"mode":"sum"}'),
            ToolCall("same", "math.compute", '{"numbers":[3,4],"mode":"sum"}'),
        ]
        p = policy("math.compute")
        rejected = await self.runtime.execute_many(calls, p)
        self.assertTrue(all(r.error == "duplicate_or_invalid_call_id" for r in rejected))
        self.assertEqual(self.runtime._usage, {})
        too_many = await self.runtime.execute_many(
            [ToolCall("a", "math.compute", '{"numbers":[1,2],"mode":"sum"}'),
             ToolCall("b", "math.compute", '{"numbers":[3,4],"mode":"sum"}')],
            policy("math.compute", max_calls=1))
        self.assertTrue(all(r.error == "tool_budget_exhausted" for r in too_many))
        allowed = await self.runtime.execute_one(
            ToolCall("a", "math.compute", '{"numbers":[1,2],"mode":"sum"}'),
            policy("math.compute", max_calls=1))
        self.assertTrue(allowed.ok)
        denied = await self.runtime.execute_one(
            ToolCall("b", "math.compute", '{"numbers":[1,2],"mode":"sum"}'),
            policy("math.compute", max_calls=1))
        self.assertEqual(denied.error, "tool_budget_exhausted")

    async def test_async_parallel_independent_reads(self):
        active = 0
        peak = 0
        async def slow():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.04)
            finally:
                active -= 1
            return {"done": True}
        self.catalog.register(ToolDefinition(
            "slow", "Read-only async job",
            {"type": "object", "properties": {}, "additionalProperties": False},
            slow,
        ))
        result = await self.runtime.execute_many(
            [ToolCall("s1", "slow", "{}"), ToolCall("s2", "slow", "{}")],
            policy("slow"))
        self.assertEqual(peak, 2)
        self.assertTrue(all(r.ok for r in result))

    async def test_mutations_are_serialized(self):
        active = 0
        peak = 0
        async def write():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.025)
            active -= 1
            return "saved"
        self.catalog.register(ToolDefinition(
            "write", "Write example",
            {"type": "object", "properties": {}, "additionalProperties": False},
            write, Risk.WRITE,
        ))
        result = await self.runtime.execute_many(
            (ToolCall("a", "write", "{}"), ToolCall("b", "write", "{}")),
            policy("write", approvals=[("write", "a"), ("write", "b")]))
        self.assertTrue(all(r.ok for r in result))
        self.assertEqual(peak, 1)

    async def test_timeout_cannot_be_assumed_to_rollback(self):
        async def slow():
            await asyncio.sleep(1)
            return 5
        self.catalog.register(ToolDefinition(
            "slow.timeout", "Slow async",
            {"type": "object", "properties": {}, "additionalProperties": False},
            slow,
        ))
        runtime = AsyncToolRuntime(self.catalog, timeout_seconds=0.01)
        result = await runtime.execute_one(
            ToolCall("t", "slow.timeout", "{}"), policy("slow.timeout"))
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "tool_timeout_outcome_unknown")

    async def test_output_schema_failure_and_size(self):
        async def bad(): return {"total": "bad"}
        self.catalog.register(ToolDefinition(
            "bad.output", "bad output",
            {"type": "object", "properties": {}, "additionalProperties": False},
            bad, output_schema={
                "type": "object", "properties": {"total": {"type": "integer"}},
                "required": ["total"], "additionalProperties": False,
            },
        ))
        result = await self.runtime.execute_one(
            ToolCall("bad1", "bad.output", "{}"), policy("bad.output"))
        self.assertEqual(result.error, "output_schema_validation_failed")
        async def huge(): return "x" * 1000
        self.catalog.register(ToolDefinition(
            "huge", "Large result",
            {"type": "object", "properties": {}, "additionalProperties": False},
            huge,
        ))
        runtime = AsyncToolRuntime(self.catalog, max_output_bytes=100)
        result = await runtime.execute_one(
            ToolCall("huge1", "huge", "{}"), policy("huge"))
        self.assertEqual(result.error, "tool_output_too_large")

    async def test_failure_message_redacted(self):
        async def leak(): raise RuntimeError("secret-passphrase-123")
        self.catalog.register(ToolDefinition(
            "leak", "Failure example",
            {"type": "object", "properties": {}, "additionalProperties": False},
            leak,
        ))
        result = await self.runtime.execute_one(ToolCall("l", "leak", "{}"), policy("leak"))
        self.assertEqual(result.error, "tool_execution_failed")
        self.assertNotIn("secret-passphrase-123", result.observation())

    async def test_invalid_schema_and_unsafe_handler_registration(self):
        async def noop(): return None
        for schema in (
            {"type": "string"},
            {"type": "object", "properties": {"x": {"type": "madeup"}}},
            {"type": "object", "$ref": "https://example.org/schema"},
        ):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                self.catalog.register(ToolDefinition("invalid", "bad", schema, noop))
        with self.assertRaises(ValueError):
            self.catalog.register(ToolDefinition(
                "bad-sync", "sync", {"type": "object"}, lambda: None))
        with self.assertRaises(ValueError):
            self.catalog.register(ToolDefinition(
                "bad space", "name", {"type": "object"}, noop))


class FakeMCPClient:
    def __init__(self):
        self.calls = []
        self.cursors = []
        self.error = False
        self.structured = {"value": 42}

    async def list_tools(self, *, cursor=None):
        self.cursors.append(cursor)
        if cursor is None:
            return {
                "tools": [{
                    "name": "lookup", "description": "Lookup a known key",
                    "inputSchema": {
                        "type": "object", "properties": {
                            "key": {"type": "string", "minLength": 1}},
                        "required": ["key"], "additionalProperties": False,
                    },
                    "outputSchema": {
                        "type": "object", "properties": {"value": {"type": "integer"}},
                        "required": ["value"], "additionalProperties": False,
                    },
                    "annotations": {"readOnlyHint": True},  # UNTRUSTED!
                }],
                "nextCursor": "next-page",
            }
        return {
            "tools": [{"name": "hidden", "description": "Not allowed",
                       "inputSchema": {"type": "object"}}],
            "nextCursor": None,
        }

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            return {"resultType": "complete", "isError": True,
                    "content": [{"type": "text", "text": "remote error"}]}
        return {"resultType": "complete", "isError": False,
                "structuredContent": self.structured,
                "content": [{"type": "text", "text": "fallback"}]}


class MCPAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeMCPClient()
        self.catalog = ToolCatalog()
        self.source = MCPToolSource(
            self.client, "remote", {"lookup": Risk.WRITE})
        self.names = await self.source.register(self.catalog)
        self.runtime = AsyncToolRuntime(self.catalog)

    async def test_paged_explicit_discovery_and_untrusted_annotation(self):
        self.assertEqual(self.names, ("remote.lookup",))
        self.assertEqual(self.client.cursors, [None, "next-page"])
        self.assertIsNone(self.catalog.get("remote.hidden"))
        call = ToolCall("id", "remote.lookup", '{"key":"answer"}')
        denied = await self.runtime.execute_one(call, policy("remote.lookup", run="m1"))
        self.assertEqual(denied.error, "approval_required")
        self.assertEqual(self.client.calls, [])
        allowed = await self.runtime.execute_one(
            call, policy("remote.lookup", run="m2",
                         approvals=[("remote.lookup", "id")]))
        self.assertEqual(allowed.value, {"value": 42})
        self.assertEqual(self.client.calls, [("lookup", {"key": "answer"})])

    async def test_mcp_output_validation_and_is_error(self):
        self.client.structured = {"value": "wrong"}
        r = await self.runtime.execute_one(
            ToolCall("x", "remote.lookup", '{"key":"answer"}'),
            policy("remote.lookup", run="wrong",
                   approvals=[("remote.lookup", "x")]))
        self.assertEqual(r.error, "output_schema_validation_failed")
        self.client.error = True
        r = await self.runtime.execute_one(
            ToolCall("y", "remote.lookup", '{"key":"answer"}'),
            policy("remote.lookup", run="error",
                   approvals=[("remote.lookup", "y")]))
        self.assertEqual(r.error, "mcp_tool_error")

    async def test_bad_mcp_input_cannot_reach_server(self):
        r = await self.runtime.execute_one(
            ToolCall("z", "remote.lookup", '{"key":2}'),
            policy("remote.lookup", run="bad",
                   approvals=[("remote.lookup", "z")]))
        self.assertEqual(r.error, "schema_validation_failed")
        self.assertEqual(self.client.calls, [])


class AgentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ch4_model_works_with_async_tool_runtime(self):
        catalog = ToolCatalog()
        math_input = {
            "type": "object", "properties": {
                "a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"], "additionalProperties": False,
        }
        async def add(a, b): return a + b
        async def multiply(a, b): return a * b
        catalog.register(ToolDefinition("add", "add integers", math_input, add))
        catalog.register(ToolDefinition("multiply", "multiply integers", math_input, multiply))
        runtime = AsyncToolRuntime(catalog)
        result = await AsyncToolAgent(
            RuleBasedDemoModel(), runtime
        ).run("Compute (8+13)*2", policy("add", "multiply"))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output, "Answer: 42")
        self.assertEqual((result.model_calls, result.tool_calls), (3, 2))
        self.assertEqual([m["tool_call_id"] for m in result.messages
                          if m["role"] == "tool"],
                         ["call_add", "call_multiply"])


if __name__ == "__main__":
    unittest.main()
