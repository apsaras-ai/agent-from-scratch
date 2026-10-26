"""Chapter 22: privacy-first, bounded Agent tracing (standard-library lab).

Trace/span IDs and operation names align with W3C Trace Context and *selected*
OpenTelemetry GenAI semantic convention names. This is NOT the OTel SDK,
OTLP exporter, distributed tracing backend, or an authenticated trace service.

Strict, explicit metadata allowlist: no prompts, responses, tool arguments,
file paths, email addresses, user IDs, authentication headers or credentials.
No raw exceptions in traces. Prices must be explicitly supplied; a missing
rate card is 'unknown', not zero. All timings/usage refer to this fixture.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import re
import secrets
from threading import RLock
import time
from typing import Any, Callable, Iterator, Mapping


class TraceError(ValueError):
    """Trace schema, privacy policy, resource limit or state violation."""


_ID32 = re.compile(r"^[0-9a-f]{32}$")
_ID16 = re.compile(r"^[0-9a-f]{16}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,95}$")
_OPERATIONS = frozenset({
    "invoke_agent", "invoke_workflow", "plan", "chat", "execute_tool",
    "verify", "reconcile", "queue_wait", "checkpoint",
})
# Values and keys that may identify user content are intentionally excluded.
_LABEL_ATTRIBUTES = frozenset({
    "gen_ai.provider.name", "gen_ai.request.model", "gen_ai.tool.name",
    "agent.task.kind", "agent.workflow.phase", "agent.retry.reason",
    "agent.reconciliation.state", "agent.verification.reason",
})
_INTEGER_ATTRIBUTES = frozenset({
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
    "agent.retry.attempt",
})
_BOOL_ATTRIBUTES = frozenset({"agent.verification.passed"})
_SAFE_ERROR_CODES = frozenset({
    "unhandled_exception", "operation_cancelled",
    "tool_rejected_or_failed", "tool_denied", "tool_timeout",
    "tool_execution_failed", "model_transport_failure",
    "model_protocol_invalid", "provider_rate_limited",
    "resource_exhausted", "worker_failed", "fixture_error",
    "verification_failed", "provider_timeout",
})
_EVENT_NAMES = frozenset({
    "worker_claimed", "approval_required", "approval_granted",
    "retry_scheduled", "tool_denied", "checkpoint_saved",
    "reconciliation_pending", "reconciliation_confirmed",
    "verification_failed", "verification_passed",
})


def _safe_labels(attributes: Mapping[str, Any]) -> dict[str, str | int | bool]:
    if not isinstance(attributes, Mapping) or len(attributes) > 12:
        raise TraceError("invalid_attribute_mapping")
    cleaned: dict[str, str | int | bool] = {}
    for key, val in attributes.items():
        if key in _LABEL_ATTRIBUTES:
            if not isinstance(val, str) or not _LABEL.fullmatch(val):
                raise TraceError("unsafe_attribute_value")
        elif key in _INTEGER_ATTRIBUTES:
            if type(val) is not int or val < 0 or val > 10**12:
                raise TraceError("invalid_integer_attribute")
        elif key in _BOOL_ATTRIBUTES:
            if type(val) is not bool:
                raise TraceError("invalid_boolean_attribute")
        else:
            raise TraceError("unapproved_attribute:" + str(key)[:32])
        cleaned[key] = val
    return cleaned


def _id(size: int) -> str:
    identifier = secrets.token_hex(size)
    while not any(x != "0" for x in identifier):
        identifier = secrets.token_hex(size)
    return identifier


def parse_traceparent(header: str) -> tuple[str, str, bool]:
    """Strict W3C version-00 parsing. Invalid trace context never grants trust.

    Real interoperability must support future W3C versions, baggage/tracestate
    policies, span links, sampling and explicit cross-service propagation.
    """
    if not isinstance(header, str):
        raise TraceError("invalid_traceparent")
    pieces = header.split("-")
    if (len(pieces) != 4 or pieces[0] != "00"
            or not _ID32.fullmatch(pieces[1])
            or not _ID16.fullmatch(pieces[2])
            or pieces[1] == "0" * 32 or pieces[2] == "0" * 16
            or pieces[3] not in ("00", "01")):
        raise TraceError("invalid_traceparent")
    return pieces[1], pieces[2], pieces[3] == "01"


def format_traceparent(trace_id: str, span_id: str, *, sampled: bool = True) -> str:
    if (not isinstance(trace_id, str) or not _ID32.fullmatch(trace_id)
            or trace_id == "0" * 32 or not isinstance(span_id, str)
            or not _ID16.fullmatch(span_id) or span_id == "0" * 16
            or type(sampled) is not bool):
        raise TraceError("invalid_trace_identity")
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}"


@dataclass(frozen=True)
class RateCard:
    provider: str
    model: str
    version: str
    input_usd_per_million: str
    output_usd_per_million: str

    def __post_init__(self) -> None:
        if any(not isinstance(x, str) or not _LABEL.fullmatch(x) for x in (
            self.provider, self.model, self.version,
        )):
            raise TraceError("invalid_rate_card_identity")
        for x in (self.input_usd_per_million, self.output_usd_per_million):
            if not isinstance(x, str) or not re.fullmatch(r"\d+(?:\.\d{1,8})?", x):
                raise TraceError("invalid_rate_card_price")
            if not Decimal(x).is_finite():
                raise TraceError("invalid_rate_card_price")

    def estimated_usd(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * Decimal(self.input_usd_per_million)
            + Decimal(output_tokens) * Decimal(self.output_usd_per_million)
        ) / Decimal(1_000_000)


@dataclass(frozen=True)
class SpanEvent:
    name: str
    timestamp_ns: int
    attributes: dict[str, Any]


@dataclass(frozen=True)
class SpanSnapshot:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    operation: str
    started_unix_ns: int
    ended_unix_ns: int
    duration_ns: int
    status: str
    error_code: str | None
    attributes: dict[str, Any]
    events: tuple[SpanEvent, ...]
    price_version: str | None = None
    estimated_usd: str | None = None


@dataclass
class _MutableSpan:
    span_id: str
    parent_span_id: str | None
    operation: str
    started_unix_ns: int
    started_mono_ns: int
    ended_unix_ns: int | None = None
    duration_ns: int | None = None
    status: str = "running"
    error_code: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    price_version: str | None = None
    estimated_usd: str | None = None
    usage_recorded: bool = False


class SpanHandle:
    def __init__(self, tracer: TraceRecorder, span_id: str):
        self._tracer = tracer
        self.span_id = span_id

    @property
    def traceparent(self) -> str:
        return format_traceparent(self._tracer.trace_id, self.span_id)

    def _live(self) -> _MutableSpan:
        span = self._tracer._spans[self.span_id]
        if span.status != "running":
            raise TraceError("span_already_closed")
        return span

    def add_event(self, name: str, *,
                  attributes: Mapping[str, Any] | None = None) -> None:
        if name not in _EVENT_NAMES:
            raise TraceError("unapproved_event_name")
        attrs = _safe_labels(attributes or {})
        with self._tracer._lock:
            span = self._live()
            if len(span.events) >= self._tracer.max_events_per_span:
                raise TraceError("event_budget_exceeded")
            timestamp = self._tracer._wall_ns()
            span.events.append(SpanEvent(name, timestamp, attrs))

    def record_usage(
        self, *, provider: str, model: str,
        input_tokens: int, output_tokens: int,
        rate_card: RateCard | None = None,
    ) -> None:
        labels = _safe_labels({
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
        })
        if rate_card is not None:
            if not isinstance(rate_card, RateCard):
                raise TraceError("invalid_rate_card")
            if rate_card.provider != provider or rate_card.model != model:
                raise TraceError("price_rate_card_model_mismatch")
        with self._tracer._lock:
            span = self._live()
            if span.operation != "chat" or span.usage_recorded:
                raise TraceError("usage_only_once_on_chat_span")
            span.attributes.update(labels)
            span.usage_recorded = True
            if rate_card is not None:
                span.price_version = rate_card.version
                span.estimated_usd = format(
                    rate_card.estimated_usd(input_tokens, output_tokens),
                    "f",
                )

    def verify(self, *, passed: bool, reason: str) -> None:
        labels = _safe_labels({
            "agent.verification.passed": passed,
            "agent.verification.reason": reason,
        })
        with self._tracer._lock:
            span = self._live()
            if span.operation != "verify":
                raise TraceError("verify_only_on_verify_span")
            if "agent.verification.passed" in span.attributes:
                raise TraceError("verification_already_recorded")
            span.attributes.update(labels)
            self.add_event("verification_passed" if passed else "verification_failed")

    def fail(self, code: str) -> None:
        if not isinstance(code, str) or code not in _SAFE_ERROR_CODES:
            raise TraceError("unapproved_error_code")
        with self._tracer._lock:
            span = self._live()
            span.status = "error"
            span.error_code = code


class TraceRecorder:
    """Per-run privacy-first spans. ContextVar preserves async child ancestry.

    This class stores spans in memory only and does not auto-instrument any
    framework or transmit telemetry. Callers may export ONLY sanitized fields.
    """

    def __init__(
        self, *, max_spans: int = 256, max_events_per_span: int = 16,
        wall_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if (type(max_spans) is not int or not 1 <= max_spans <= 5000
                or type(max_events_per_span) is not int
                or not 1 <= max_events_per_span <= 128):
            raise TraceError("invalid_trace_capacity")
        self.trace_id = _id(16)
        self.max_spans = max_spans
        self.max_events_per_span = max_events_per_span
        self._wall_ns = wall_ns
        self._monotonic_ns = monotonic_ns
        self._spans: dict[str, _MutableSpan] = {}
        self._current: ContextVar[str | None] = ContextVar(
            "mini_agent_trace_current", default=None
        )
        self._lock = RLock()

    @contextmanager
    def span(
        self, operation: str, *,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[SpanHandle]:
        if operation not in _OPERATIONS:
            raise TraceError("unknown_span_operation")
        clean = _safe_labels(attributes or {})
        with self._lock:
            if len(self._spans) >= self.max_spans:
                raise TraceError("span_budget_exceeded")
            parent_id = self._current.get()
            if parent_id is None and self._spans:
                raise TraceError("multiple_trace_roots")
            if parent_id is not None and parent_id not in self._spans:
                raise TraceError("missing_parent_span")
            identifier = _id(8)
            while identifier in self._spans:
                identifier = _id(8)
            record = _MutableSpan(
                identifier, parent_id, operation,
                self._wall_ns(), self._monotonic_ns(),
                attributes=clean,
            )
            self._spans[identifier] = record
        token = self._current.set(identifier)
        handle = SpanHandle(self, identifier)
        try:
            yield handle
        except BaseException as exc:
            with self._lock:
                if record.status == "running":
                    record.status = "cancelled" if isinstance(
                        exc, (KeyboardInterrupt, SystemExit)
                    ) or type(exc).__name__ == "CancelledError" else "error"
                    # Intentionally no exception class/name/message/stack in logs:
                    # arbitrary exception details can contain private content.
                    record.error_code = "operation_cancelled" if record.status == "cancelled" else "unhandled_exception"
            raise
        finally:
            with self._lock:
                duration = self._monotonic_ns() - record.started_mono_ns
                record.duration_ns = max(0, duration)
                record.ended_unix_ns = max(record.started_unix_ns, self._wall_ns())
                if record.status == "running":
                    record.status = "ok"
            self._current.reset(token)

    def snapshots(self) -> tuple[SpanSnapshot, ...]:
        with self._lock:
            result = []
            for value in self._spans.values():
                if value.ended_unix_ns is None or value.duration_ns is None:
                    raise TraceError("cannot_export_active_spans")
                result.append(SpanSnapshot(
                    self.trace_id, value.span_id, value.parent_span_id,
                    value.operation, value.started_unix_ns,
                    value.ended_unix_ns, value.duration_ns,
                    value.status, value.error_code,
                    dict(value.attributes), tuple(value.events),
                    value.price_version, value.estimated_usd,
                ))
            return tuple(result)
