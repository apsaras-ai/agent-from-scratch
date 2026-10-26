"""Chapter 22: bounded, content-free JSONL trace export and OFFLINE playback.

Each line is hash-chained for accidental/tamper detection, but NOT signed.
A malicious writer could replace the entire chain: never treat it as evidence
of provenance, identity, tool authorization, true model output or task success.

Playback DOES NOT invoke any model, handler, HTTP request or tool. It merely
validates, summarizes and diagnoses the recorded metadata. Real OTLP exporter
and long-term authenticated telemetry storage are future work.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable

from .observability import (
    SpanSnapshot, SpanEvent, TraceRecorder, TraceError,
    _ID16, _ID32, _OPERATIONS, _EVENT_NAMES, _LABEL, _safe_labels,
)


class TraceIntegrityError(TraceError):
    """Malformed, redaction-violating or inconsistent replay data."""


@dataclass(frozen=True)
class TraceSummary:
    trace_id: str
    span_count: int
    failed_spans: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_usd: str | None
    priced_model_calls: int
    unpriced_model_calls: int
    verified: str  # passed / failed / unknown
    primary_failure: str | None
    slowest_child: str | None
    slowest_child_ms: float | None


def _canonical(obj: Any) -> str:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    )


def _validate_snapshot(s: SpanSnapshot) -> None:
    if not isinstance(s, SpanSnapshot):
        raise TraceIntegrityError("invalid_span_object")
    if (not _ID32.fullmatch(s.trace_id) or s.trace_id == "0" * 32
            or not _ID16.fullmatch(s.span_id) or s.span_id == "0" * 16):
        raise TraceIntegrityError("invalid_span_ids")
    if (s.parent_span_id is not None and
            (not _ID16.fullmatch(s.parent_span_id)
             or s.parent_span_id == "0" * 16)):
        raise TraceIntegrityError("invalid_parent_id")
    if s.operation not in _OPERATIONS:
        raise TraceIntegrityError("unknown_operation")
    if s.status not in ("ok", "error", "cancelled"):
        raise TraceIntegrityError("invalid_span_status")
    if (any(type(x) is not int or x < 0 for x in (
            s.started_unix_ns, s.ended_unix_ns, s.duration_ns))
            or s.ended_unix_ns < s.started_unix_ns):
        raise TraceIntegrityError("invalid_span_timestamps")
    if ((s.status == "ok" and s.error_code is not None)
            or (s.status != "ok" and (
                not isinstance(s.error_code, str)
                or not _LABEL.fullmatch(s.error_code)))):
        raise TraceIntegrityError("invalid_error_code")
    _safe_labels(s.attributes)
    if not isinstance(s.events, tuple) or len(s.events) > 128:
        raise TraceIntegrityError("invalid_events")
    for ev in s.events:
        if (not isinstance(ev, SpanEvent) or ev.name not in _EVENT_NAMES
                or type(ev.timestamp_ns) is not int or ev.timestamp_ns < 0):
            raise TraceIntegrityError("invalid_event")
        _safe_labels(ev.attributes)
    if s.estimated_usd is not None:
        if (not isinstance(s.estimated_usd, str)
                or not re.fullmatch(r"\d+(?:\.\d{1,16})?", s.estimated_usd)
                or not Decimal(s.estimated_usd).is_finite()):
            raise TraceIntegrityError("invalid_cost")
        if (s.operation != "chat" or s.price_version is None):
            raise TraceIntegrityError("cost_not_on_priced_chat")
    if (s.price_version is not None and
            (not isinstance(s.price_version, str)
             or not _LABEL.fullmatch(s.price_version))):
        raise TraceIntegrityError("invalid_price_version")


def _to_record(s: SpanSnapshot) -> dict[str, Any]:
    _validate_snapshot(s)
    return {
        "trace_id": s.trace_id,
        "span_id": s.span_id,
        "parent_span_id": s.parent_span_id,
        "operation": s.operation,
        "started_unix_ns": s.started_unix_ns,
        "ended_unix_ns": s.ended_unix_ns,
        "duration_ns": s.duration_ns,
        "status": s.status,
        "error_code": s.error_code,
        "attributes": s.attributes,
        "events": [
            {"name": e.name, "timestamp_ns": e.timestamp_ns,
             "attributes": e.attributes} for e in s.events
        ],
        "price_version": s.price_version,
        "estimated_usd": s.estimated_usd,
    }


def write_trace_jsonl(recorder: TraceRecorder, path: str | Path) -> Path:
    """Atomically replace an EXPORT FILE with an integrity-checked trace.

    The file is chmod 0600 on POSIX. This cannot protect against a malicious
    writer with access to the same filesystem. No raw prompt payload stored.
    """
    if not isinstance(recorder, TraceRecorder):
        raise TraceError("trace_recorder_required")
    spans = recorder.snapshots()
    if not spans:
        raise TraceError("empty_trace")
    target = Path(path)
    if not target.parent.is_dir() or target.is_symlink():
        raise TraceError("invalid_export_path")
    entries = []
    previous = "0" * 64
    for index, span in enumerate(spans, 1):
        body = {"schema_version": 1, "sequence": index,
                "record": _to_record(span)}
        digest = sha256((previous + _canonical(body)).encode("utf-8")).hexdigest()
        entries.append(_canonical({
            **body, "previous_hash": previous, "entry_hash": digest,
        }))
        previous = digest
    payload = "\n".join(entries) + "\n"
    if len(payload.encode("utf-8")) > 1_048_576:
        raise TraceError("export_size_limit")
    temp: str | None = None
    try:
        fd, temp = tempfile.mkstemp(prefix=".trace-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
            temp = None
        finally:
            if temp is not None:
                Path(temp).unlink(missing_ok=True)
    except OSError as exc:
        raise TraceError("trace_export_io_error") from exc
    return target


def _duplicate_free(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise TraceIntegrityError("duplicate_json_key")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise TraceIntegrityError("nonfinite_json_constant")


def read_trace_jsonl(path: str | Path, *, max_bytes: int = 1_048_576,
                     max_spans: int = 5000) -> tuple[SpanSnapshot, ...]:
    target = Path(path)
    if (type(max_bytes) is not int or max_bytes < 128
            or type(max_spans) is not int or max_spans < 1):
        raise TraceIntegrityError("invalid_replay_limits")
    if target.is_symlink() or not target.is_file() or target.stat().st_size > max_bytes:
        raise TraceIntegrityError("invalid_trace_file_or_size")
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TraceIntegrityError("trace_read_failure") from exc
    lines = text.splitlines()
    if not lines or len(lines) > max_spans:
        raise TraceIntegrityError("invalid_trace_line_count")
    prev = "0" * 64
    observed: set[str] = set()
    trace_id: str | None = None
    roots = 0
    spans = []
    keys = {
        "trace_id", "span_id", "parent_span_id", "operation",
        "started_unix_ns", "ended_unix_ns", "duration_ns",
        "status", "error_code", "attributes", "events",
        "price_version", "estimated_usd",
    }
    for index, line in enumerate(lines, 1):
        try:
            data = json.loads(line, object_pairs_hook=_duplicate_free,
                              parse_constant=_reject_nonfinite)
        except (ValueError, RecursionError, TypeError) as exc:
            raise TraceIntegrityError("invalid_trace_json") from exc
        if (not isinstance(data, dict) or set(data) != {
            "schema_version", "sequence", "record",
            "previous_hash", "entry_hash",
        } or data["schema_version"] != 1
                or type(data["sequence"]) is not int
                or data["sequence"] != index
                or data["previous_hash"] != prev
                or not isinstance(data["record"], dict)
                or set(data["record"]) != keys):
            raise TraceIntegrityError("invalid_record_shape_or_sequence")
        body = {"schema_version": 1, "sequence": index, "record": data["record"]}
        expected = sha256((prev + _canonical(body)).encode("utf-8")).hexdigest()
        if data["entry_hash"] != expected:
            raise TraceIntegrityError("invalid_hash_chain")
        prev = expected
        record = data["record"]
        try:
            events = tuple(
                SpanEvent(e["name"], e["timestamp_ns"], e["attributes"])
                for e in record["events"]
            )
            span = SpanSnapshot(
                record["trace_id"], record["span_id"],
                record["parent_span_id"], record["operation"],
                record["started_unix_ns"], record["ended_unix_ns"],
                record["duration_ns"], record["status"], record["error_code"],
                record["attributes"], events,
                record["price_version"], record["estimated_usd"],
            )
            _validate_snapshot(span)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise TraceIntegrityError("invalid_span_content") from exc
        if trace_id is None:
            trace_id = span.trace_id
        if span.trace_id != trace_id or span.span_id in observed:
            raise TraceIntegrityError("mixed_trace_or_duplicate_span")
        if span.parent_span_id is None:
            roots += 1
        elif span.parent_span_id not in observed:
            raise TraceIntegrityError("parent_missing_or_out_of_order")
        observed.add(span.span_id)
        spans.append(span)
    if roots != 1:
        raise TraceIntegrityError("expected_one_root_span")
    return tuple(spans)


def summarize_trace(spans: Iterable[SpanSnapshot]) -> TraceSummary:
    spans = tuple(spans)
    if not spans:
        raise TraceIntegrityError("no_spans_to_summarize")
    seen: set[str] = set()
    trace_id = spans[0].trace_id
    for span in spans:
        _validate_snapshot(span)
        if span.trace_id != trace_id or span.span_id in seen:
            raise TraceIntegrityError("mixed_or_duplicate_trace")
        seen.add(span.span_id)
    models = [s for s in spans if s.operation == "chat"]
    priced = [s for s in models if s.estimated_usd is not None]
    unpriced = [s for s in models if s.estimated_usd is None]
    cost = (format(sum((Decimal(s.estimated_usd) for s in priced),
                      start=Decimal(0)), "f")
            if not unpriced else None)
    # Do NOT synthesize zero usage for providers that omit usage.
    # An aggregate is unknown if any model call has missing counts.
    token_input = (None if any("gen_ai.usage.input_tokens" not in s.attributes
                              for s in models)
                   else sum(s.attributes["gen_ai.usage.input_tokens"]
                            for s in models))
    token_output = (None if any("gen_ai.usage.output_tokens" not in s.attributes
                               for s in models)
                    else sum(s.attributes["gen_ai.usage.output_tokens"]
                             for s in models))
    # A successful tool or model span is NOT a verified user task. Only
    # dedicated verifier spans can report acceptance by a trusted host.
    checks = [s for s in spans if s.operation == "verify" and
              "agent.verification.passed" in s.attributes]
    verified = (
        "unknown" if not checks else
        "passed" if checks[-1].attributes["agent.verification.passed"]
        else "failed"
    )
    failures = [s for s in spans if s.status in ("error", "cancelled")]
    children = [s for s in spans if s.parent_span_id is not None]
    bottleneck = max(children, key=lambda x: x.duration_ns) if children else None
    return TraceSummary(
        trace_id, len(spans), len(failures),
        token_input, token_output, cost, len(priced), len(unpriced),
        verified, (failures[0].operation + ":" + (failures[0].error_code or "unknown"))
                  if failures else None,
        bottleneck.operation if bottleneck else None,
        round(bottleneck.duration_ns / 1_000_000, 3) if bottleneck else None,
    )


def replay_diagnostic(path: str | Path) -> TraceSummary:
    """Read-only TRACE playback. Never reproduce external side effects."""
    return summarize_trace(read_trace_jsonl(path))
