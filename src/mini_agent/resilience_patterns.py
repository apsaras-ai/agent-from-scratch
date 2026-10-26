"""Chapter 21: circuit breaker, bounded jitter, and safe Saga planning.

This module only handles deterministic decisions. It DOES NOT run untrusted
tools, compensate payments, cancel emails, or authenticate host approvals.
A Saga compensation is a NEW business operation, not a SQL rollback.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
from threading import RLock
from time import monotonic
from typing import Callable


class ResilienceError(ValueError):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_seconds: float = 0.5
    cap_seconds: float = 30.0

    def __post_init__(self):
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 100:
            raise ResilienceError("invalid_retry_attempts")
        if (any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0
                for x in (self.base_seconds, self.cap_seconds))
                or self.cap_seconds < self.base_seconds):
            raise ResilienceError("invalid_retry_delays")

    def delay(self, request_key: str, failed_attempt: int) -> float | None:
        """Full jitter in [0, exponential backoff window].

        Deterministic digest enables reproducible tests. Production should
        use randomized jitter if different clients may share request keys.
        A terminal attempt has no retry delay.
        """
        if (not isinstance(request_key, str) or not request_key
                or type(failed_attempt) is not int
                or not 1 <= failed_attempt <= self.max_attempts):
            raise ResilienceError("invalid_retry_request")
        if failed_attempt >= self.max_attempts:
            return None
        window = min(self.cap_seconds, self.base_seconds *
                     2.0 ** min(failed_attempt - 1, 52))
        digest = sha256((request_key + ":" + str(failed_attempt)).encode()).digest()
        fraction = int.from_bytes(digest[:8], "big") / (2 ** 64)
        return round(window * fraction, 6)


@dataclass(frozen=True)
class Permit:
    token: int
    probe: bool


class CircuitBreaker:
    """In-process single-probe half-open circuit breaker.

    A breaker belongs to one dependency, NOT to a tenant or security
    principal. It protects load; approval and identity are separate gates.
    """

    def __init__(self, failure_threshold: int = 3,
                 cooldown_seconds: float = 10.0,
                 clock: Callable[[], float] = monotonic):
        if type(failure_threshold) is not int or failure_threshold < 1:
            raise ResilienceError("invalid_failure_threshold")
        if (type(cooldown_seconds) not in (int, float)
                or not math.isfinite(cooldown_seconds)
                or cooldown_seconds <= 0):
            raise ResilienceError("invalid_cooldown")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = float(cooldown_seconds)
        self.clock = clock
        self.state = "closed"
        self.consecutive_failures = 0
        self.next_probe_at = 0.0
        self._next_token = 0
        self._inflight: dict[int, bool] = {}
        self._lock = RLock()

    def acquire(self) -> Permit | None:
        with self._lock:
            now = float(self.clock())
            if not math.isfinite(now):
                raise ResilienceError("invalid_circuit_clock")
            if self.state == "open":
                if now < self.next_probe_at:
                    return None
                self.state = "half_open"
            if self.state == "half_open" and self._inflight:
                return None
            self._next_token += 1
            probe = self.state == "half_open"
            self._inflight[self._next_token] = probe
            return Permit(self._next_token, probe)

    def release(self, permit: Permit) -> None:
        """No request was attempted; free the half-open probe slot."""
        with self._lock:
            if not isinstance(permit, Permit) or permit.token not in self._inflight:
                raise ResilienceError("invalid_breaker_permit")
            del self._inflight[permit.token]

    def record(self, permit: Permit, *, succeeded: bool) -> str:
        if type(succeeded) is not bool:
            raise ResilienceError("invalid_breaker_outcome")
        with self._lock:
            if (not isinstance(permit, Permit)
                    or permit.token not in self._inflight
                    or self._inflight[permit.token] != permit.probe):
                raise ResilienceError("invalid_breaker_permit")
            del self._inflight[permit.token]
            now = float(self.clock())
            if not math.isfinite(now):
                raise ResilienceError("invalid_circuit_clock")
            if succeeded:
                if permit.probe or self.state == "half_open":
                    self.state = "closed"
                self.consecutive_failures = 0
            elif permit.probe or self.state == "half_open":
                self.state = "open"
                self.consecutive_failures = self.failure_threshold
                self.next_probe_at = now + self.cooldown_seconds
            else:
                self.consecutive_failures += 1
                if self.consecutive_failures >= self.failure_threshold:
                    self.state = "open"
                    self.next_probe_at = now + self.cooldown_seconds
            return self.state


@dataclass(frozen=True)
class SagaStep:
    step_id: str
    action: str
    compensating_action: str | None = None


@dataclass(frozen=True)
class CompensationPlan:
    status: str
    actions: tuple[str, ...]
    explanation: str


def plan_compensation(steps: tuple[SagaStep, ...],
                      actual_states: dict[str, str]) -> CompensationPlan:
    """Plan, but DO NOT execute, reverse-order compensations.

    States must be host-verified: not_started, committed, compensated, unknown.
    If ANY step's real external state is unknown, no automatic compensation
    is attempted. An irreversible committed step also requires human review.
    """
    ids = [s.step_id for s in steps]
    if not steps or len(ids) != len(set(ids)) or any(not x for x in ids):
        raise ResilienceError("invalid_saga")
    if set(actual_states) != set(ids) or any(
        value not in {"not_started", "committed", "compensated", "unknown"}
        for value in actual_states.values()
    ):
        raise ResilienceError("unverified_or_incomplete_saga_state")
    if "unknown" in actual_states.values():
        return CompensationPlan("blocked_unknown", (),
                                "reconcile_external_effects_before_compensation")
    committed = [s for s in steps if actual_states[s.step_id] == "committed"]
    if any(not s.compensating_action for s in committed):
        return CompensationPlan("manual_review_required", (),
                                "irreversible_committed_step")
    return CompensationPlan("proposed", tuple(
        s.compensating_action for s in reversed(committed)
        if s.compensating_action is not None
    ), "host_must_approve_each_compensation_and_verify_effect")
