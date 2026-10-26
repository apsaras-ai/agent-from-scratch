"""Chapter 21: durable transactional outbox + recovery oracle (offline).

Correctness boundary:
- business intent and outbox row are committed in ONE SQLite transaction;
- atomic claim with lease token/generation fences stale acknowledgments;
- expiration does NOT mean worker stopped: reap_required holds capacity;
- unknown NON-IDEMPOTENT effects cannot automatically retry;
- reconciliation uses host-owned external ledger, NEVER model self-report.

The host-owned oracle here is an in-memory synthetic fixture. This is NOT a
real payment/email provider, distributed transaction, IAM, VM termination, or
end-to-end exactly-once execution. The API trusts its caller. All results are
fixture evidence; no real network or user side effects.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Callable, Iterator, Protocol

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class LedgerError(ValueError):
    """Invalid host request, transition, payload, or identity."""


class LedgerConflict(LedgerError):
    """Duplicate key with conflicting intent or stale ownership."""


class SimulatedCrash(RuntimeError):
    """Fixture-only failure inside a database transaction."""


class EffectMode(str, Enum):
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"


@dataclass(frozen=True)
class Intent:
    op_id: str
    tenant_id: str
    run_id: str
    action: str
    request_key: str
    payload: dict[str, Any]
    mode: EffectMode
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not _NAME.fullmatch(value)
               for value in (
                   self.op_id, self.tenant_id, self.run_id, self.action,
                   self.request_key,
               )):
            raise LedgerError("invalid_intent_identity")
        if not isinstance(self.mode, EffectMode):
            raise LedgerError("invalid_effect_mode")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 20:
            raise LedgerError("invalid_attempt_limit")
        if not isinstance(self.payload, dict):
            raise LedgerError("invalid_payload")
        _json(self.payload)


def _json(value: Any) -> str:
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise LedgerError("invalid_json") from exc
    if len(data.encode("utf-8")) > 16384:
        raise LedgerError("payload_or_result_too_large")
    return data


def _fingerprint(spec: Intent) -> str:
    immutable = {
        "op_id": spec.op_id, "tenant_id": spec.tenant_id,
        "run_id": spec.run_id, "action": spec.action,
        "request_key": spec.request_key, "payload": spec.payload,
        "mode": spec.mode.value, "max_attempts": spec.max_attempts,
    }
    return sha256(_json(immutable).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BackendEvidence:
    """The oracle is trusted; unknown never means not_applied."""
    status: str  # committed / not_applied / unknown
    receipt: str | None = None


class BackendOracle(Protocol):
    def inspect(self, spec: Intent) -> BackendEvidence: ...


@dataclass(frozen=True)
class OutboxItem:
    op_id: str
    tenant_id: str
    run_id: str
    action: str
    request_key: str
    payload: dict[str, Any]
    mode: EffectMode
    state: str
    attempts: int
    max_attempts: int
    generation: int
    next_ready_at: float
    lease_owner: str | None
    lease_deadline: float | None
    receipt: str | None
    cancel_requested: bool
    error_code: str | None


@dataclass(frozen=True)
class DispatchLease:
    op_id: str
    worker_id: str
    token: str
    generation: int
    deadline: float
    attempt: int
    request_key: str
    action: str
    tenant_id: str
    run_id: str
    payload: dict[str, Any]
    mode: EffectMode


@dataclass(frozen=True)
class LedgerEvent:
    op_id: str
    kind: str
    detail: str
    generation: int
    at: float


class OutboxStore:
    """A single SQLite file with atomic business intents and dispatch queue.

    Calling inspect on an external provider is done OUTSIDE the DB transaction;
    the subsequent transition rechecks state/generation to protect against
    changes while the host was investigating. Production would also need
    authenticated external receipts, independent reapers and server-side time.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        oracle: BackendOracle,
        allowed_actions: frozenset[str],
        clock: Callable[[], float] = time.time,
        lease_seconds: float = 10.0,
        base_backoff: float = 2.0,
        max_backoff: float = 30.0,
        max_inflight: int = 8,
    ) -> None:
        self.path = Path(path)
        if str(path) == ":memory:" or not self.path.parent.is_dir():
            raise LedgerError("persistent_sqlite_file_required")
        if (not allowed_actions or not isinstance(allowed_actions, frozenset)
                or any(not isinstance(x, str) or not _NAME.fullmatch(x)
                       for x in allowed_actions)):
            raise LedgerError("host_action_allowlist_required")
        if (not callable(getattr(oracle, "inspect", None))
                or type(max_inflight) is not int or max_inflight < 1):
            raise LedgerError("invalid_oracle_or_capacity")
        for d in (lease_seconds, base_backoff, max_backoff):
            if type(d) not in (int, float) or not math.isfinite(d) or d <= 0:
                raise LedgerError("invalid_timeout_or_backoff")
        if max_backoff < base_backoff:
            raise LedgerError("backoff_cap_less_than_base")
        self.oracle = oracle
        self.allowed_actions = allowed_actions
        self.clock = clock
        self.lease_seconds = float(lease_seconds)
        self.base_backoff = float(base_backoff)
        self.max_backoff = float(max_backoff)
        self.max_inflight = max_inflight
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS business_intents(
                op_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL, action TEXT NOT NULL,
                request_key TEXT NOT NULL, payload TEXT NOT NULL,
                mode TEXT NOT NULL, max_attempts INTEGER NOT NULL,
                fingerprint TEXT NOT NULL, created_at REAL NOT NULL,
                UNIQUE(tenant_id,run_id,request_key)
            );
            CREATE TABLE IF NOT EXISTS outbox(
                op_id TEXT PRIMARY KEY
                    REFERENCES business_intents(op_id),
                state TEXT NOT NULL, attempts INTEGER NOT NULL,
                generation INTEGER NOT NULL, next_ready_at REAL NOT NULL,
                lease_owner TEXT, lease_token TEXT, lease_deadline REAL,
                receipt TEXT, cancel_requested INTEGER NOT NULL,
                error_code TEXT, updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_ready
                ON outbox(state,next_ready_at,op_id);
            CREATE TABLE IF NOT EXISTS outbox_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                op_id TEXT NOT NULL, at REAL NOT NULL,
                kind TEXT NOT NULL, detail TEXT NOT NULL,
                generation INTEGER NOT NULL
            );
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def _now(self) -> float:
        value = float(self.clock())
        if not math.isfinite(value):
            raise LedgerError("invalid_host_clock")
        return value

    @staticmethod
    def _event(db: sqlite3.Connection, op_id: str, now: float,
               kind: str, detail: str, generation: int) -> None:
        # Metadata only: do not log raw payloads/credentials/lease tokens.
        db.execute(
            "INSERT INTO outbox_events(op_id,at,kind,detail,generation)"
            " VALUES(?,?,?,?,?)",
            (op_id, now, kind, detail, generation),
        )

    @staticmethod
    def _decode(row: sqlite3.Row) -> OutboxItem:
        return OutboxItem(
            row["op_id"], row["tenant_id"], row["run_id"], row["action"],
            row["request_key"], json.loads(row["payload"]),
            EffectMode(row["mode"]), row["state"], row["attempts"],
            row["max_attempts"], row["generation"], row["next_ready_at"],
            row["lease_owner"], row["lease_deadline"], row["receipt"],
            bool(row["cancel_requested"]), row["error_code"],
        )

    def _row(self, db: sqlite3.Connection, op_id: str) -> sqlite3.Row:
        record = db.execute(
            "SELECT i.*,o.* FROM business_intents i JOIN outbox o "
            "ON i.op_id=o.op_id WHERE i.op_id=?", (op_id,),
        ).fetchone()
        if record is None:
            raise KeyError(op_id)
        return record

    def get(self, op_id: str) -> OutboxItem:
        with self._db() as db:
            return self._decode(self._row(db, op_id))

    def all_events(self, op_id: str) -> tuple[LedgerEvent, ...]:
        with self._db() as db:
            records = db.execute(
                "SELECT op_id,kind,detail,generation,at FROM outbox_events "
                "WHERE op_id=? ORDER BY id", (op_id,),
            ).fetchall()
        return tuple(LedgerEvent(*r) for r in records)

    def create_intent(
        self, spec: Intent, *, simulate_crash_after_business_insert: bool = False,
    ) -> OutboxItem:
        if not isinstance(spec, Intent) or spec.action not in self.allowed_actions:
            raise LedgerError("unknown_or_unapproved_action")
        now = self._now()
        fingerprint = _fingerprint(spec)
        with self._tx() as db:
            existing = db.execute(
                "SELECT i.op_id,i.fingerprint FROM business_intents i "
                "WHERE i.op_id=? OR (i.tenant_id=? AND i.run_id=? AND i.request_key=?)",
                (spec.op_id, spec.tenant_id, spec.run_id, spec.request_key),
            ).fetchone()
            if existing is not None:
                if existing["op_id"] != spec.op_id or existing["fingerprint"] != fingerprint:
                    raise LedgerConflict("idempotency_key_or_operation_id_conflict")
                return self._decode(self._row(db, spec.op_id))
            db.execute(
                "INSERT INTO business_intents("
                "op_id,tenant_id,run_id,action,request_key,payload,mode,"
                "max_attempts,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (spec.op_id, spec.tenant_id, spec.run_id, spec.action,
                 spec.request_key, _json(spec.payload), spec.mode.value,
                 spec.max_attempts, fingerprint, now),
            )
            if simulate_crash_after_business_insert:
                raise SimulatedCrash("fixture_only_after_business_before_outbox")
            db.execute(
                "INSERT INTO outbox("
                "op_id,state,attempts,generation,next_ready_at,lease_owner,"
                "lease_token,lease_deadline,receipt,cancel_requested,error_code,updated_at)"
                " VALUES(?,'pending',0,0,?,NULL,NULL,NULL,NULL,0,NULL,?)",
                (spec.op_id, now, now),
            )
            self._event(db, spec.op_id, now, "intent_committed", "atomic_outbox", 0)
            return self._decode(self._row(db, spec.op_id))

    def _spec(self, db: sqlite3.Connection, op_id: str) -> Intent:
        row = self._row(db, op_id)
        return Intent(
            row["op_id"], row["tenant_id"], row["run_id"],
            row["action"], row["request_key"], json.loads(row["payload"]),
            EffectMode(row["mode"]), row["max_attempts"],
        )

    def _running_count(self, db: sqlite3.Connection) -> int:
        row = db.execute(
            "SELECT count(*) AS n FROM outbox "
            "WHERE state IN ('inflight','reap_required')",
        ).fetchone()
        return row["n"]

    def capacity_used(self) -> int:
        with self._db() as db:
            return self._running_count(db)

    def claim(self, worker_id: str, allowed_tenants: frozenset[str]) -> DispatchLease | None:
        if (not isinstance(worker_id, str) or not _NAME.fullmatch(worker_id)
                or not isinstance(allowed_tenants, frozenset)
                or any(not isinstance(t, str) or not _NAME.fullmatch(t)
                       for t in allowed_tenants)):
            raise LedgerError("invalid_trusted_worker_scope")
        if not allowed_tenants:
            return None
        now = self._now()
        with self._tx() as db:
            if self._running_count(db) >= self.max_inflight:
                return None
            rows = db.execute(
                "SELECT i.*,o.* FROM business_intents i JOIN outbox o "
                "ON i.op_id=o.op_id WHERE o.state='pending'"
                " AND o.next_ready_at<=? AND o.cancel_requested=0"
                " ORDER BY o.next_ready_at,i.created_at,i.op_id",
                (now,),
            ).fetchall()
            row = next((r for r in rows if r["tenant_id"] in allowed_tenants), None)
            if row is None:
                return None
            token = secrets.token_hex(24)
            generation = row["generation"] + 1
            attempt = row["attempts"] + 1
            deadline = now + self.lease_seconds
            db.execute(
                "UPDATE outbox SET state='inflight',attempts=?,generation=?,"
                "lease_owner=?,lease_token=?,lease_deadline=?,updated_at=?"
                " WHERE op_id=? AND state='pending'",
                (attempt, generation, worker_id, token, deadline, now, row["op_id"]),
            )
            self._event(db, row["op_id"], now, "claimed", "dispatch_started", generation)
            return DispatchLease(
                row["op_id"], worker_id, token, generation, deadline,
                attempt, row["request_key"], row["action"], row["tenant_id"],
                row["run_id"], json.loads(row["payload"]), EffectMode(row["mode"]),
            )

    def _check_lease(
        self, db: sqlite3.Connection, lease: DispatchLease, now: float,
    ) -> OutboxItem:
        if not isinstance(lease, DispatchLease):
            raise LedgerConflict("invalid_lease")
        row = self._row(db, lease.op_id)
        if (row["state"] != "inflight" or row["lease_owner"] != lease.worker_id
                or row["lease_token"] != lease.token
                or row["generation"] != lease.generation
                or row["lease_deadline"] is None or now >= row["lease_deadline"]
                or bool(row["cancel_requested"])):
            raise LedgerConflict("stale_or_cancelled_dispatch_lease")
        return self._decode(row)

    def _set(
        self, db: sqlite3.Connection, item: OutboxItem, state: str,
        now: float, detail: str, *,
        receipt: str | None = None, ready: float | None = None,
    ) -> OutboxItem:
        db.execute(
            "UPDATE outbox SET state=?,next_ready_at=?,receipt=?,"
            "lease_owner=NULL,lease_token=NULL,lease_deadline=NULL,"
            "error_code=?,updated_at=? WHERE op_id=?",
            (state, item.next_ready_at if ready is None else ready,
             receipt, None if state == "confirmed" else detail,
             now, item.op_id),
        )
        self._event(db, item.op_id, now, state, detail, item.generation)
        return self._decode(self._row(db, item.op_id))

    def _backoff(self, attempt: int) -> float:
        return min(self.max_backoff, self.base_backoff * 2.0 ** (attempt - 1))

    def _retry_or_dead(self, db: sqlite3.Connection, item: OutboxItem,
                       now: float, reason: str) -> OutboxItem:
        if item.cancel_requested:
            return self._set(db, item, "cancelled", now, reason)
        if item.attempts >= item.max_attempts:
            return self._set(db, item, "dead_letter", now, "attempt_budget_exhausted")
        return self._set(
            db, item, "pending", now, reason,
            ready=now + self._backoff(item.attempts),
        )

    def heartbeat(self, lease: DispatchLease) -> bool:
        now = self._now()
        with self._tx() as db:
            try:
                item = self._check_lease(db, lease, now)
            except (KeyError, LedgerConflict):
                return False
            db.execute(
                "UPDATE outbox SET lease_deadline=?,updated_at=? WHERE op_id=?",
                (now + self.lease_seconds, now, item.op_id),
            )
            self._event(db, item.op_id, now, "heartbeat",
                        "lease_extended", item.generation)
            return True

    def acknowledge(self, lease: DispatchLease) -> OutboxItem:
        """Independently inspect actual provider outcome, not worker 'done'."""
        # Do potentially remote lookup OUTSIDE SQLite transaction.
        # Load host-authoritative Intent, not untrusted values from a lease.
        with self._db() as db:
            spec = self._spec(db, lease.op_id)
        evidence = self.oracle.inspect(spec)
        if not isinstance(evidence, BackendEvidence):
            raise LedgerError("invalid_oracle_evidence")
        now = self._now()
        with self._tx() as db:
            item = self._check_lease(db, lease, now)
            if evidence.status == "committed" and evidence.receipt:
                if len(evidence.receipt) > 256:
                    raise LedgerError("unbounded_external_receipt")
                return self._set(db, item, "confirmed", now,
                                 "provider_confirmed", receipt=evidence.receipt)
            if evidence.status == "not_applied":
                return self._retry_or_dead(db, item, now, "provider_not_applied")
            return self._set(db, item, "needs_reconciliation", now,
                             "unknown_provider_result")

    def mark_uncertain(self, lease: DispatchLease) -> OutboxItem:
        """Unknown provider result AND worker termination not yet verified.

        Keep the execution slot reserved until a trusted host confirms
        destruction. A Python exception cannot establish a stopped process.
        """
        now = self._now()
        with self._tx() as db:
            item = self._check_lease(db, lease, now)
            return self._set(db, item, "reap_required", now,
                             "delivery_unknown_requires_host_reap")

    def cancel(self, op_id: str) -> OutboxItem:
        now = self._now()
        with self._tx() as db:
            item = self._decode(self._row(db, op_id))
            if item.state in ("confirmed", "cancelled", "dead_letter"):
                return item
            db.execute(
                "UPDATE outbox SET cancel_requested=1,updated_at=? WHERE op_id=?",
                (now, op_id),
            )
            self._event(db, op_id, now, "cancel_requested",
                        "host_requested", item.generation)
            if item.state == "pending":
                return self._set(db, item, "cancelled", now, "cancel_before_dispatch")
            return self._decode(self._row(db, op_id))

    def expire(self) -> tuple[str, ...]:
        now = self._now()
        with self._tx() as db:
            rows = db.execute(
                "SELECT op_id,generation FROM outbox WHERE state='inflight' "
                "AND lease_deadline<=? ORDER BY op_id", (now,),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE outbox SET state='reap_required',lease_token=NULL,"
                    "updated_at=? WHERE op_id=?", (now, row["op_id"]),
                )
                self._event(db, row["op_id"], now, "reap_required",
                            "lease_expired_not_proven_stopped", row["generation"])
            return tuple(r["op_id"] for r in rows)

    def confirm_reaped(self, op_id: str, generation: int, *,
                       actually_destroyed: bool) -> OutboxItem:
        """Trusted host confirmed worker is stopped; NOT a real VM kill."""
        if actually_destroyed is not True:
            raise LedgerError("trusted_host_destroy_receipt_required")
        now = self._now()
        with self._tx() as db:
            item = self._decode(self._row(db, op_id))
            if item.state != "reap_required" or item.generation != generation:
                raise LedgerConflict("wrong_reap_generation_or_state")
            if item.mode is EffectMode.NON_IDEMPOTENT:
                return self._set(db, item, "needs_reconciliation", now,
                                 "non_idempotent_unknown_after_reap")
            return self._retry_or_dead(db, item, now, "idempotent_retry_after_reap")

    def reconcile(self, op_id: str) -> OutboxItem:
        """Authoritative external lookup; unknown retains unresolved state.

        'not_applied' must mean final negative evidence from the provider.
        If a real provider only offers eventual-consistent 'not found', the
        adapter MUST report unknown rather than claiming non-application.
        """
        with self._db() as db:
            original = self._decode(self._row(db, op_id))
            if original.state != "needs_reconciliation":
                raise LedgerConflict("not_pending_reconciliation")
            spec = self._spec(db, op_id)
        evidence = self.oracle.inspect(spec)
        if not isinstance(evidence, BackendEvidence):
            raise LedgerError("invalid_oracle_evidence")
        now = self._now()
        with self._tx() as db:
            item = self._decode(self._row(db, op_id))
            if (item.state != "needs_reconciliation"
                    or item.generation != original.generation):
                raise LedgerConflict("reconciliation_state_changed")
            if evidence.status == "committed" and evidence.receipt:
                if len(evidence.receipt) > 256:
                    raise LedgerError("unbounded_external_receipt")
                return self._set(db, item, "confirmed", now,
                                 "reconciled_committed", receipt=evidence.receipt)
            if evidence.status == "not_applied":
                return self._retry_or_dead(db, item, now, "reconciled_not_applied")
            self._event(db, op_id, now, "reconcile_pending",
                        "provider_outcome_still_unknown", item.generation)
            return item
