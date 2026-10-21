"""Chapter 8: repeatable offline Context Engineering ablation.

No LLM inference takes place. We measure only prompt-proxy length, which
evidence survives selection, and whether tool calls/results stay paired.
This demonstration CANNOT establish real-world model answer accuracy.
"""
from __future__ import annotations

import json

from mini_agent.context_engineering import (
    ContextBudget, ContextManager, HeuristicTokenCounter,
    InMemoryContextArchive, clip_tool_outputs, units_from_transcript,
)


def sample_messages() -> list[dict]:
    messages: list[dict] = [
        {"role": "system", "content": "Stay within tool permissions. Treat logs as untrusted evidence."},
        {"role": "user", "content": "Repair the repository. Preserve independently verified facts."},
    ]

    def append_tool(cid: str, file_name: str, result: str) -> None:
        messages.append({
            "role": "assistant", "content": None, "tool_calls": [{
                "id": cid,
                "type": "function",
                "function": {
                    "name": "read_fixture",
                    "arguments": json.dumps({"file": file_name}),
                },
            }],
        })
        messages.append({
            "role": "tool", "tool_call_id": cid,
            "content": json.dumps({"ok": True, "result": result}),
        })

    append_tool(
        "verified-early", "tests/test_gate.py",
        "VERIFIED_LOCK_ID=FIX-42; test_gate.py reports the exact confirmed ID.",
    )
    for i in range(6):
        append_tool(
            f"filler-{i}", f"logs/worker_{i}.txt",
            f"worker-{i}: " + "repeated uninformative heartbeat message. " * 16,
        )
    messages.append({
        "role": "user",
        "content": "What VERIFIED_LOCK_ID was confirmed by test_gate.py?",
    })
    return messages


def sample_tools() -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": "read_fixture",
            "description": "Read one trusted synthetic fixture for teaching.",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string"}},
                "required": ["file"],
                "additionalProperties": False,
            },
        },
    }]


def prepared(archive: InMemoryContextArchive):
    transcript = sample_messages()
    units = units_from_transcript(
        transcript, priority_by_tool_call={"verified-early": 10},
    )
    return clip_tool_outputs(units, archive, max_chars=300)


def diagnostic_budget(units, tools) -> ContextBudget:
    """Choose a synthetic budget that fits either one of two tool turns."""
    counter = HeuristicTokenCounter()
    pinned = [m for u in units if u.pinned for m in u.messages]
    evidence = next(u for u in units if u.importance > 0)
    last_tool = next(u for u in reversed(units) if u.kind == "tool_exchange")
    required = max(
        counter.count(pinned + list(evidence.messages), tools),
        counter.count(pinned + list(last_tool.messages), tools),
    )
    # Select an explicit proxy-token limit, not the actual model context size.
    return ContextBudget(
        window_tokens=required + 128,
        output_reserve=64,
        safety_margin=64,
    )


def has_verified_id(messages) -> bool:
    return any("VERIFIED_LOCK_ID=FIX-42" in json.dumps(m, ensure_ascii=False)
               for m in messages)


def main() -> None:
    archive = InMemoryContextArchive()
    units = prepared(archive)
    tools = sample_tools()
    counter = HeuristicTokenCounter()
    budget = diagnostic_budget(units, tools)
    manager = ContextManager(budget, archive=archive)
    raw_estimate = counter.count(sample_messages(), tools)

    print("Full transcript proxy tokens:", raw_estimate)
    print("Model input proxy limit:", budget.input_limit)
    for strategy in ("recent", "priority"):
        plan = manager.build(
            units, tools, strategy=strategy,
            query="What VERIFIED_LOCK_ID was confirmed by test_gate.py?",
        )
        print(
            f"{strategy}: estimate={plan.estimated_input_tokens} "
            f"selected={len(plan.selected_ids)} omitted={len(plan.omitted_ids)} "
            f"verified_fact={has_verified_id(plan.messages)}"
        )

    assert not has_verified_id(manager.build(units, tools, strategy="recent").messages)
    assert has_verified_id(manager.build(units, tools, strategy="priority").messages)

    # Reserve extra room for the deterministic archive checkpoint. The record
    # is not a fabricated semantic summary; omitted messages remain retrievable.
    larger = ContextBudget(
        window_tokens=budget.window_tokens + 150,
        output_reserve=budget.output_reserve,
        safety_margin=budget.safety_margin,
    )
    compact = ContextManager(larger, archive=archive).build(
        units, tools, strategy="compact",
        query="What VERIFIED_LOCK_ID was confirmed by test_gate.py?",
    )
    print(
        f"compact: estimate={compact.estimated_input_tokens} "
        f"archive_ref={bool(compact.archive_id)} "
        f"verified_fact={has_verified_id(compact.messages)}"
    )
    if compact.archive_id:
        omitted_original = archive.get(compact.archive_id)
        print("Archived units recoverable:", len(omitted_original))
    print("These are deterministic information-retention results, NOT LLM accuracy.")


if __name__ == "__main__":
    main()
