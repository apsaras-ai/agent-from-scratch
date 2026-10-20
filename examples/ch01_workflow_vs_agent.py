"""Chapter 1: compare an unconditional pipeline, a conditional workflow,
and a policy-driven loop in the same deterministic environment.

This is an OFFLINE CONTROL-FLOW EXERCISE. ReactivePolicy is hand-coded;
it is not an LLM and does not demonstrate model generalization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mini_agent.core import Agent, Response, ToolCall, ToolRegistry


@dataclass
class LookupWorld:
    primary_hit: bool
    calls: list[str] = field(default_factory=list)

    def primary(self) -> str:
        self.calls.append("primary")
        return "FOUND:42" if self.primary_hit else "MISS"

    def backup(self) -> str:
        self.calls.append("backup")
        return "FOUND:42"


def unconditional_pipeline(world: LookupWorld) -> str:
    primary = world.primary()
    backup = world.backup()  # Always executed, even if primary succeeds.
    return primary if primary.startswith("FOUND:") else backup


def conditional_workflow(world: LookupWorld) -> str:
    primary = world.primary()
    if primary.startswith("FOUND:"):
        return primary
    return world.backup()


class ReactivePolicy:
    """A tiny rule-based policy standing in for a future model adapter."""

    def complete(self, messages: list[dict[str, Any]]) -> Response:
        last = messages[-1]
        if last["role"] == "user":
            return Response(tool_call=ToolCall("primary", {}, "call_primary"))

        if last["role"] != "tool":
            raise ValueError(f"Unexpected message role: {last['role']}")

        observation = last["content"]
        if observation.startswith("FOUND:"):
            return Response(text=observation)
        if observation == "MISS" and last["name"] == "primary":
            return Response(tool_call=ToolCall("backup", {}, "call_backup"))
        if observation == "MISS":
            return Response(text="NOT_FOUND")
        return Response(text=f"FAILED: {observation}")


def run_scenario(primary_hit: bool, architecture: str) -> dict[str, Any]:
    """Return observable results without printing: useful for automated tests."""
    world = LookupWorld(primary_hit)
    steps = 0

    if architecture == "pipeline":
        answer = unconditional_pipeline(world)
    elif architecture == "workflow":
        answer = conditional_workflow(world)
    elif architecture == "policy_loop":
        registry = ToolRegistry()
        registry.register("primary", world.primary)
        registry.register("backup", world.backup)
        result = Agent(ReactivePolicy(), registry, max_steps=4).run("Find the answer")
        answer = result.text
        steps = result.steps
    else:
        raise ValueError(f"Unknown architecture: {architecture}")

    return {
        "answer": answer,
        "tools": tuple(world.calls),
        "model_steps": steps,
    }


def main() -> None:
    for primary_hit in (True, False):
        case = "H: primary hit" if primary_hit else "M: primary miss"
        print(case)
        for architecture in ("pipeline", "workflow", "policy_loop"):
            result = run_scenario(primary_hit, architecture)
            print(
                f"  {architecture:<12} answer={result['answer']:<8} "
                f"tools={len(result['tools'])} "
                f"sequence={','.join(result['tools'])} "
                f"model_steps={result['model_steps']}"
            )


if __name__ == "__main__":
    main()
