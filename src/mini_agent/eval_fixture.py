"""Chapter 25: run the REAL Chapter-4 AgentKernel against a toy environment.

A deterministic fake Model emits a small subset of tool calls. The harness
checks actual fixture state and tool trace independently. Outputs, usage and
wall time are simulated; this is not a real LLM quality benchmark.
"""
from __future__ import annotations

from decimal import Decimal
import json
import re

from .agent_v2 import AgentKernel, ModelReply, Tool, ToolCall, ToolRegistry
from .agent_evaluation import (
    AdvisoryJudgment, FixtureEnvironment, PublicTask, RunTelemetry,
)
from .cost_optimization import RateCard, TokenUsage

_RATE = RateCard(Decimal("2"), Decimal("8"), Decimal("0.5"))


class ScriptedModel:
    def __init__(self, call: ToolCall | None):
        self.call = call
        self.turn = 0

    def complete(self, messages, tools):
        self.turn += 1
        if self.turn == 1 and self.call is not None:
            return ModelReply(
                tool_calls=(self.call,), finish_reason="tool_calls",
            )
        return ModelReply(content="I completed the task.", finish_reason="stop")


def _parse_action(instruction: str, variant: str, trial: int):
    """Only a toy grammar. Agents never see hidden expected_state."""
    patterns = (
        r"^Set ([a-z][a-z0-9_.-]*) to ([a-z][a-z0-9_.-]*)\.",
        r"^Update ([a-z][a-z0-9_.-]*) to ([a-z][a-z0-9_.-]*)\.",
    )
    if variant == "candidate":
        patterns += (r"^Save ([a-z][a-z0-9_.-]*) as ([a-z][a-z0-9_.-]*)\.",)
    for pat in patterns:
        match = re.search(pat, instruction)
        if match:
            if (variant == "candidate" and trial % 3 == 2
                    and instruction.startswith("Save")):
                # Simulate stochastic non-deterministic task failure.
                return None
            return "fixture.write", {
                "resource": match.group(1), "value": match.group(2)
            }
    m = re.search(r"^Read ([a-z][a-z0-9_.-]*)\.", instruction)
    if m:
        return "fixture.read", {"resource":m.group(1)}
    return None


class KernelFixtureRunner:
    def __init__(self, variant: str = "baseline"):
        if variant not in ("baseline", "candidate"):
            raise ValueError("unknown_fixture_variant")
        self.variant = variant

    def run(self, task:PublicTask, env:FixtureEnvironment,
            trial_index:int) -> RunTelemetry:
        registry = ToolRegistry()
        registry.register(Tool(
            "fixture.read","Read a synthetic local resource",
            {
                "type":"object",
                "properties":{"resource":{"type":"string"}},
                "required":["resource"],
                "additionalProperties":False,
            },
            handler=lambda resource:env.invoke("read",resource),
            read_only=True,
        ))
        registry.register(Tool(
            "fixture.write","Write a synthetic local resource",
            {
                "type":"object",
                "properties":{
                    "resource":{"type":"string"}, "value":{"type":"string"}
                },
                "required":["resource","value"],
                "additionalProperties":False,
            },
            handler=lambda resource,value:env.invoke("write",resource,value),
            read_only=False,
        ))
        action=_parse_action(task.instruction,self.variant,trial_index)
        call = None
        if action is not None:
            tool_name,args=action
            call=ToolCall("fixture-tool-1",tool_name,json.dumps(args))
        result=AgentKernel(
            ScriptedModel(call),registry,max_model_calls=3,max_tool_calls=2,
        ).run(task.instruction,allow_writes=True)
        # A real adapter MUST use trusted Provider Usage/Billing and a timer,
        # not fields emitted by the LLM. Here all values are fake fixtures.
        n=result.model_calls
        return RunTelemetry(
            final_text=result.output or "No model text",
            model_calls=n,
            usages=tuple(TokenUsage(
                300, 30, 200 if self.variant=="candidate" else 0
            ) for _ in range(n)),
            latency_ms=20 + 40*n + 10*result.tool_calls + trial_index*2,
            rate=_RATE, external_cost_usd=Decimal("0.0001")*result.tool_calls,
        )


def naive_advisory_judge(task:PublicTask, final_text:str) -> AdvisoryJudgment:
    """Purposefully misleading: completion-language != environment state."""
    return AdvisoryJudgment(
        1.0 if "completed" in final_text.lower() else 0.0,
        "self_report_language_only",
    )
