"""Chapter 8: a deterministic Context Engineering teaching layer.

Key invariants:
- A system instruction and first user task are pinned and never silently cut.
- assistant.tool_calls + matching tool results form an ATOMIC unit.
- Count tool specifications as part of the input request.
- Token counts are explicitly APPROXIMATE; no provider context guarantee.
- Clipped results and omitted units may be restored from an IN-MEMORY archive.
- Archive markers are NOT factual summaries; never invent test outcomes.
- The full execution transcript is never changed by context selection.

This is not a durable artifact store, semantic retrieval service, tokenizer,
prompt-injection defense, or a true long-context LLM evaluation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping, Protocol

from .agent_v2 import ModelReply


class ContextError(ValueError):
    """Invalid transcript or context configuration."""


class ContextOverflow(ContextError):
    """Pinned instructions/task and tool schema exceed the planning budget."""


class TokenCounter(Protocol):
    def count(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int: ...


class HeuristicTokenCounter:
    """Deterministic length proxy: ~4 ASCII chars/token and 1 non-ASCII char/token.

    This intentionally includes compact serialized roles, IDs and tool schemas,
    but does NOT match any vendor tokenizer or byte-level API accounting.
    """

    def count(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
        serialized = json.dumps(
            {"messages": messages, "tools": tools},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        ascii_chars = sum(ord(ch) < 128 for ch in serialized)
        return math.ceil(ascii_chars / 4) + len(serialized) - ascii_chars


@dataclass(frozen=True)
class ContextBudget:
    window_tokens: int
    output_reserve: int
    provider_overhead: int = 0
    safety_margin: int = 64

    def __post_init__(self) -> None:
        values = (self.window_tokens, self.output_reserve,
                  self.provider_overhead, self.safety_margin)
        if any(type(x) is not int or x < 0 for x in values):
            raise ValueError("Budget fields must be nonnegative integers")
        if self.window_tokens <= 0 or self.input_limit <= 0:
            raise ValueError("Output/overhead/margins must leave input capacity")

    @property
    def input_limit(self) -> int:
        return (self.window_tokens - self.output_reserve
                - self.provider_overhead - self.safety_margin)


@dataclass(frozen=True)
class ContextUnit:
    unit_id: str
    kind: str
    messages: tuple[dict[str, Any], ...]
    pinned: bool = False
    importance: int = 0

    def __post_init__(self) -> None:
        if not self.unit_id or not self.messages:
            raise ContextError("A context unit requires an ID and messages")
        if type(self.importance) is not int or self.importance < 0:
            raise ContextError("importance must be a nonnegative integer")


class InMemoryContextArchive:
    """Content-addressed, scope-local teaching storage; no disk persistence."""

    def __init__(self) -> None:
        self._objects: dict[str, Any] = {}

    def put(self, payload: Any) -> str:
        snapshot = deepcopy(payload)
        raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
        artifact_id = "sha256:" + sha256(raw.encode("utf-8")).hexdigest()
        self._objects[artifact_id] = snapshot
        return artifact_id

    def get(self, artifact_id: str) -> Any:
        if artifact_id not in self._objects:
            raise KeyError("Archive reference not found")
        return deepcopy(self._objects[artifact_id])


def _call_ids(message: dict[str, Any]) -> list[str]:
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        raise ContextError("Expected a nonempty tool_calls list")
    ids: list[str] = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
            raise ContextError("Tool call is missing an ID")
        ids.append(call["id"])
    if len(set(ids)) != len(ids):
        raise ContextError("Duplicate tool call IDs in one assistant message")
    return ids


def units_from_transcript(
    messages: list[dict[str, Any]],
    *,
    priority_by_tool_call: Mapping[str, int] | None = None,
) -> tuple[ContextUnit, ...]:
    """Group full tool rounds. Reject orphan/incomplete tool results.

    Importance overrides must be supplied by TRUSTED application logic,
    not read from untrusted tool text or suggested by an LLM.
    """
    priorities = priority_by_tool_call or {}
    result: list[ContextUnit] = []
    index = 0
    first_user = True
    while index < len(messages):
        item = messages[index]
        role = item.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise ContextError(f"Unsupported role at index {index}")
        if role == "tool":
            raise ContextError("Orphan tool result")
        if role == "assistant" and item.get("tool_calls"):
            ids = _call_ids(item)
            group = [deepcopy(item)]
            observed: set[str] = set()
            end = index + 1
            while end < len(messages) and messages[end].get("role") == "tool":
                tool_id = messages[end].get("tool_call_id")
                if tool_id not in ids or tool_id in observed:
                    raise ContextError("Mismatched or duplicated tool result")
                observed.add(tool_id)
                group.append(deepcopy(messages[end]))
                end += 1
            if observed != set(ids):
                raise ContextError("Unresolved tool calls: partial transcript")
            importance = max((priorities.get(cid, 0) for cid in ids), default=0)
            result.append(ContextUnit(
                f"turn-{index}", "tool_exchange", tuple(group), importance=importance,
            ))
            index = end
            continue
        if role == "assistant" and "tool_calls" in item and not item["tool_calls"]:
            raise ContextError("Empty assistant tool_calls cannot be compacted")
        pinned = role == "system" or (role == "user" and first_user)
        if role == "user":
            first_user = False
        kind = "instruction" if role == "system" else ("task" if pinned else "message")
        result.append(ContextUnit(f"turn-{index}", kind, (deepcopy(item),), pinned=pinned))
        index += 1
    # Preserve the latest user instruction/question even in a multi-turn chat.
    user_units = [i for i, unit in enumerate(result)
                  if unit.messages[0].get("role") == "user"]
    if user_units:
        i = user_units[-1]
        result[i] = replace(result[i], pinned=True,
                            kind="task" if result[i].pinned else "current_user")
    return tuple(result)


def clip_tool_outputs(
    units: tuple[ContextUnit, ...],
    archive: InMemoryContextArchive,
    *,
    max_chars: int = 512,
) -> tuple[ContextUnit, ...]:
    """Clip individual tool observations without unpairing Tool Call IDs.

    Archived original observation is available to a TRUSTED retrieval layer.
    The excerpt is *untrusted data*. It is never promoted to a system message.
    """
    if type(max_chars) is not int or max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    processed: list[ContextUnit] = []
    for unit in units:
        if unit.kind != "tool_exchange":
            processed.append(unit)
            continue
        messages = deepcopy(list(unit.messages))
        for msg in messages:
            if msg["role"] != "tool":
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                raise ContextError("Only text tool results are supported")
            if len(content) <= max_chars:
                continue
            artifact = archive.put({
                "tool_call_id": msg.get("tool_call_id"),
                "content": content,
            })
            try:
                original = json.loads(content)
                ok = original.get("ok") if isinstance(original, dict) else None
            except ValueError:
                ok = None
            clipped = {
                "truncated": True,
                "ok": ok if type(ok) is bool else None,
                "artifact_id": artifact,
                "note": "Tool observation is incomplete; retrieve artifact before using omitted details.",
                "excerpt": content[: max_chars - 225],
            }
            serialized = json.dumps(clipped, ensure_ascii=False, separators=(",", ":"))
            # Never exceed requested char limit even if a nonstandard message
            # has unusual overhead; decrease the excerpt as necessary.
            while len(serialized) > max_chars and clipped["excerpt"]:
                clipped["excerpt"] = clipped["excerpt"][:-1]
                serialized = json.dumps(clipped, ensure_ascii=False, separators=(",", ":"))
            if len(serialized) > max_chars:
                raise ContextError("Truncation envelope exceeds maximum")
            msg["content"] = serialized
        processed.append(replace(unit, messages=tuple(messages)))
    return tuple(processed)


@dataclass(frozen=True)
class ContextPlan:
    messages: tuple[dict[str, Any], ...]
    selected_ids: tuple[str, ...]
    omitted_ids: tuple[str, ...]
    estimated_input_tokens: int
    input_limit: int
    strategy: str
    archive_id: str | None = None

    @property
    def proxy_fill_ratio(self) -> float:
        return self.estimated_input_tokens / self.input_limit


_WORD = re.compile(r"[a-z0-9_]+", flags=re.IGNORECASE)


class ContextManager:
    def __init__(
        self,
        budget: ContextBudget,
        *,
        counter: TokenCounter | None = None,
        archive: InMemoryContextArchive | None = None,
    ) -> None:
        self.budget = budget
        self.counter = counter or HeuristicTokenCounter()
        self.archive = archive or InMemoryContextArchive()

    def build(
        self,
        units: tuple[ContextUnit, ...],
        tools: list[dict[str, Any]],
        *,
        strategy: str = "recent",
        query: str = "",
    ) -> ContextPlan:
        """Plan a bounded *model view*, never mutate execution history.

        'recent' takes newest eligible units first;
        'priority' prefers TRUSTED importance and basic lexical overlap;
        'compact' is 'priority' plus an explicit archive checkpoint.
        """
        if strategy not in {"recent", "priority", "compact"}:
            raise ValueError("Unknown context strategy")
        if len({x.unit_id for x in units}) != len(units):
            raise ContextError("Duplicate context unit identifiers")

        def flatten(selected: set[str], checkpoint: dict[str, Any] | None = None):
            result: list[dict[str, Any]] = []
            inserted = False
            for unit in units:
                if checkpoint is not None and not inserted and not unit.pinned:
                    result.append(deepcopy(checkpoint))
                    inserted = True
                if unit.unit_id in selected:
                    result.extend(deepcopy(list(unit.messages)))
            if checkpoint is not None and not inserted:
                result.append(deepcopy(checkpoint))
            return result

        def count(selected: set[str], checkpoint: dict[str, Any] | None = None):
            return self.counter.count(flatten(selected, checkpoint), tools)

        pinned = {unit.unit_id for unit in units if unit.pinned}
        if count(pinned) > self.budget.input_limit:
            raise ContextOverflow(
                "Pinned system/task and tool definitions exceed input budget"
            )

        candidates = [(i, x) for i, x in enumerate(units) if not x.pinned]
        query_terms = set(_WORD.findall(query.lower()))

        def score(entry):
            i, unit = entry
            content = json.dumps(list(unit.messages), ensure_ascii=False).lower()
            overlap = len(query_terms.intersection(_WORD.findall(content)))
            # Importance is supplied externally by trusted project logic.
            return (unit.importance, overlap, i)

        ordered = (sorted(candidates, reverse=True)
                   if strategy == "recent"
                   else sorted(candidates, key=score, reverse=True))
        selected = set(pinned)
        for _, unit in ordered:
            tentative = selected | {unit.unit_id}
            if count(tentative) <= self.budget.input_limit:
                selected = tentative

        checkpoint: dict[str, Any] | None = None
        archive_id: str | None = None
        if strategy == "compact" and any(x.unit_id not in selected for x in units):
            # Reserve enough space for a fixed-size checkpoint, evicting
            # least-valuable unpinned units if necessary. The message never
            # claims to summarize facts that were not reliably extracted.
            for _ in range(len(candidates) + 1):
                omitted = [x for x in units if x.unit_id not in selected]
                manifest = [{"unit_id": x.unit_id, "kind": x.kind,
                             "messages": list(x.messages)} for x in omitted]
                ref = self.archive.put(manifest)
                checkpoint = {
                    "role": "assistant",
                    "content": (
                        "[Context checkpoint: earlier messages are archived, NOT summarized. "
                        f"Archive ID={ref}. Omitted units={len(omitted)}. "
                        "Retrieve original evidence before making claims based on omitted content.]"
                    ),
                }
                if count(selected, checkpoint) <= self.budget.input_limit:
                    archive_id = ref
                    break
                eligible = [x for x in selected if x not in pinned]
                if not eligible:
                    # A checkpoint is optional; preserve pinned material.
                    checkpoint = None
                    break
                discard = min(
                    eligible,
                    key=lambda uid: score(next(
                        e for e in candidates if e[1].unit_id == uid
                    )),
                )
                # An optional archive marker must not evict explicitly
                # verified/high-priority evidence just to advertise itself.
                lowest = next(u for u in units if u.unit_id == discard)
                if lowest.importance > 0:
                    checkpoint = None
                    break
                selected.remove(discard)

        messages = flatten(selected, checkpoint)
        estimated = self.counter.count(messages, tools)
        if estimated > self.budget.input_limit:
            raise ContextOverflow("Context planning exceeded configured proxy budget")
        return ContextPlan(
            messages=tuple(messages),
            selected_ids=tuple(x.unit_id for x in units if x.unit_id in selected),
            omitted_ids=tuple(x.unit_id for x in units if x.unit_id not in selected),
            estimated_input_tokens=estimated,
            input_limit=self.budget.input_limit,
            strategy=strategy,
            archive_id=archive_id,
        )


class ContextManagedModel:
    """Chapter-4 Model-compatible wrapper. Records model-view plans for audit.

    It MUST NOT be used to claim a provider token cap is guaranteed; this
    counter is a rough proxy. Real deployments need model-specific token
    accounting, a capability registry, and upstream 400/context retry logic.
    """

    def __init__(
        self, model: Any, manager: ContextManager, *,
        strategy: str = "recent",
        priority_by_tool_call: Mapping[str, int] | None = None,
        tool_result_max_chars: int = 512,
    ) -> None:
        self.model = model
        self.manager = manager
        self.strategy = strategy
        self.priority_by_tool_call = dict(priority_by_tool_call or {})
        self.tool_result_max_chars = tool_result_max_chars
        self.plans: list[ContextPlan] = []

    def complete(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]]) -> ModelReply:
        units = units_from_transcript(
            messages, priority_by_tool_call=self.priority_by_tool_call,
        )
        clipped = clip_tool_outputs(
            units, self.manager.archive, max_chars=self.tool_result_max_chars,
        )
        task_query = next((m.get("content", "") for m in messages
                           if m.get("role") == "user"), "")
        plan = self.manager.build(clipped, tools,
                                  strategy=self.strategy, query=str(task_query))
        self.plans.append(plan)
        # Do not let provider adapter mutate the archived execution history.
        return self.model.complete(deepcopy(list(plan.messages)), deepcopy(tools))
