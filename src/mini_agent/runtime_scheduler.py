"""Chapter 20: SQLite-backed teaching Agent Task Scheduler with lease fencing.

Durable queue state + event metadata, fairness, admission, versioned worker
leases, heartbeats, retries, cancellation, and a host-controlled verifier.
All operations use SQLite BEGIN IMMEDIATE, including across connections.

SECURITY AND RELIABILITY LIMITS:
- worker identity, resource cleanup confirmations and external reconciliation
  are supplied by TRUSTED CALLERS, not authenticated by this library.
- no sandbox, cgroup, VM, external API, SQL broker, or credential is created.
- a fenced worker may STILL be running: token fencing is not a process kill,
  nor does it undo an already committed external side effect.
- an expired lease remains in 'reap_required' and occupies capacity until
  actual sandbox cleanup is acknowledged by a trusted host.
- non-idempotent unknown outcomes require human/host reconciliation.
- uses an injected Unix clock for a single-node lab; distributed production
  requires trusted server time, independent host reaping and durable IAM.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Awaitable, Callable, Iterator, Mapping

from .sandbox_reservations import HostCapacity, SandboxAllocation


class SchedulerError(ValueError):
    pass


class AdmissionRejected(SchedulerError):
    pass


class LeaseFenced(SchedulerError):
    pass


class InvalidTransition(SchedulerError):
    pass


_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class Effect(str, Enum):
    READ_ONLY = "read_only"
    IDEMPOTENT = "idempotent"
    NON_IDEMPOTENT = "non_idempotent"


@dataclass(frozen=True)
class JobSpec:
    task_id: str
    tenant_id: str
    run_id: str
    kind: str
    payload: dict[str, Any]
    allocation: SandboxAllocation
    effect: Effect = Effect.READ_ONLY
    idempotency_key: str = ""
    priority: int = 0
    max_attempts: int = 3

    def __post_init__(self) -> None:
        for value in (self.task_id, self.tenant_id, self.run_id, self.kind):
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise SchedulerError("invalid_job_identity")
        if self.idempotency_key and (
            not isinstance(self.idempotency_key, str)
            or not _ID.fullmatch(self.idempotency_key)
        ):
            raise SchedulerError("invalid_idempotency_key")
        if not isinstance(self.effect, Effect):
            raise SchedulerError("invalid_effect_class")
        if (type(self.priority) is not int or self.priority < -20 or self.priority > 20
                or type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 20):
            raise SchedulerError("invalid_priority_or_attempt_limit")
        if not isinstance(self.allocation, SandboxAllocation):
            raise SchedulerError("allocation_required")
        if not isinstance(self.payload, dict):
            raise SchedulerError("payload_must_be_object")
        try:
            serial = json.dumps(self.payload, sort_keys=True, ensure_ascii=False,
                                separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise SchedulerError("payload_must_be_serializable") from exc
        if len(serial.encode("utf-8")) > 16_384:
            raise SchedulerError("payload_too_large")


@dataclass(frozen=True)
class WorkerIdentity:
    worker_id: str
    allowed_tenants: frozenset[str]

    def __post_init__(self) -> None:
        if (not isinstance(self.worker_id, str) or not _ID.fullmatch(self.worker_id)
                or not isinstance(self.allowed_tenants, frozenset)
                or not all(isinstance(x, str) and _ID.fullmatch(x)
                           for x in self.allowed_tenants)):
            raise SchedulerError("invalid_worker_identity")


@dataclass(frozen=True)
class SchedulerLimits:
    max_live_jobs: int = 64
    max_live_per_tenant: int = 32
    max_running: int = 4
    max_running_per_tenant: int = 2
    lease_seconds: float = 20.0
    base_retry_seconds: float = 2.0
    max_retry_seconds: float = 120.0

    def __post_init__(self) -> None:
        ints = (self.max_live_jobs, self.max_live_per_tenant,
                self.max_running, self.max_running_per_tenant)
        if any(type(x) is not int or x < 1 for x in ints):
            raise SchedulerError("invalid_queue_or_concurrency_limit")
        floats = (self.lease_seconds, self.base_retry_seconds,
                  self.max_retry_seconds)
        if any(type(x) not in (float, int) or not math.isfinite(x) or x <= 0
               for x in floats):
            raise SchedulerError("invalid_lease_or_retry_duration")
        if not 0.05 <= self.lease_seconds <= 3600:
            raise SchedulerError("lease_seconds_out_of_bounds")
        if self.max_retry_seconds < self.base_retry_seconds:
            raise SchedulerError("retry_ceiling_less_than_base")


@dataclass(frozen=True)
class JobRecord:
    task_id: str
    tenant_id: str
    run_id: str
    kind: str
    payload: dict[str, Any]
    effect: Effect
    state: str
    priority: int
    attempts: int
    max_attempts: int
    next_ready_at: float
    generation: int
    cancel_requested: bool
    lease_owner: str | None
    lease_deadline: float | None
    cpu_millis: int
    memory_mb: int
    disk_mb: int
    result: Any
    evidence: str | None
    error_code: str | None


@dataclass(frozen=True)
class WorkerLease:
    task_id: str
    worker_id: str
    token: str
    generation: int
    deadline: float
    attempt: int
    kind: str
    payload: dict[str, Any]
    effect: Effect


@dataclass(frozen=True)
class QueueEvent:
    task_id: str
    at: float
    kind: str
    detail: str
    generation: int


Verifier = Callable[[JobRecord, Any], bool]
Handler = Callable[[WorkerLease], Awaitable[Any]]


class SQLiteTaskScheduler:
    """SQLite serialized claim with tenant round-robin among eligible jobs.

    Per-tenant fairness is measured as least-recently-served among tenants
    currently eligible by time, capacity, worker scope, and concurrency.
    Priority orders tasks *within* a selected tenant. It is not weighted fair
    queuing, does not provide latency SLAs and can starve low-priority jobs
    within a permanently busy tenant.
    """

    _LIVE = ("queued", "running", "reap_required", "needs_reconciliation")
    _CAPACITY = ("running", "reap_required")

    def __init__(
        self,
        database: str | Path,
        *,
        capacity: HostCapacity,
        limits: SchedulerLimits = SchedulerLimits(),
        verifiers: Mapping[str, Verifier],
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(capacity, HostCapacity) or not isinstance(limits, SchedulerLimits):
            raise SchedulerError("invalid_capacity_or_limits")
        if not verifiers or any(not isinstance(k, str) or not _ID.fullmatch(k)
                                or not callable(v) for k, v in verifiers.items()):
            raise SchedulerError("host_verifiers_required")
        self.database = Path(database)
        if str(database) == ":memory:" or not self.database.parent.exists():
            raise SchedulerError("persistent_database_file_required")
        self.capacity = capacity
        self.limits = limits
        self.verifiers = dict(verifiers)
        self.clock = clock
        with self._connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, effect TEXT NOT NULL,
                idempotency_key TEXT, cpu_millis INTEGER NOT NULL,
                memory_mb INTEGER NOT NULL, disk_mb INTEGER NOT NULL,
                priority INTEGER NOT NULL, state TEXT NOT NULL,
                attempts INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
                next_ready_at REAL NOT NULL,
                lease_owner TEXT, lease_token TEXT, generation INTEGER NOT NULL,
                lease_deadline REAL, cancel_requested INTEGER NOT NULL,
                result TEXT, evidence TEXT, error_code TEXT,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(tenant_id,run_id,idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS jobs_pick
                ON jobs(state,next_ready_at,tenant_id,priority,created_at);
            CREATE INDEX IF NOT EXISTS jobs_expiry
                ON jobs(state,lease_deadline);
            CREATE TABLE IF NOT EXISTS tenant_turn (
                tenant_id TEXT PRIMARY KEY, last_turn INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scheduler_meta (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                turn INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO scheduler_meta(singleton,turn) VALUES(1,0);
            CREATE TABLE IF NOT EXISTS job_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL, at REAL NOT NULL, kind TEXT NOT NULL,
                detail TEXT NOT NULL, generation INTEGER NOT NULL
            );
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.database), timeout=5.0,
                               isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _now(self) -> float:
        current = float(self.clock())
        if not math.isfinite(current):
            raise SchedulerError("invalid_server_clock")
        return current

    @staticmethod
    def _encode(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise SchedulerError("invalid_json_payload") from exc

    @staticmethod
    def _record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            row["task_id"], row["tenant_id"], row["run_id"], row["kind"],
            json.loads(row["payload"]), Effect(row["effect"]), row["state"],
            row["priority"], row["attempts"], row["max_attempts"],
            row["next_ready_at"], row["generation"],
            bool(row["cancel_requested"]), row["lease_owner"],
            row["lease_deadline"], row["cpu_millis"], row["memory_mb"],
            row["disk_mb"],
            json.loads(row["result"]) if row["result"] is not None else None,
            row["evidence"], row["error_code"],
        )

    def _get(self, conn: sqlite3.Connection, task_id: str) -> JobRecord:
        row = conn.execute("SELECT * FROM jobs WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._record(row)

    def get(self, task_id: str) -> JobRecord:
        with self._connection() as conn:
            return self._get(conn, task_id)

    @staticmethod
    def _event(conn: sqlite3.Connection, task_id: str, at: float,
               kind: str, detail: str, generation: int) -> None:
        # Never insert payload, secrets, approval tokens or model text here.
        conn.execute(
            "INSERT INTO job_events(task_id,at,kind,detail,generation) "
            "VALUES(?,?,?,?,?)",
            (task_id, at, kind, detail, generation),
        )

    def events(self, task_id: str) -> tuple[QueueEvent, ...]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT task_id,at,kind,detail,generation FROM job_events "
                "WHERE task_id=? ORDER BY sequence", (task_id,),
            ).fetchall()
        return tuple(QueueEvent(*row) for row in rows)

    def submit(self, spec: JobSpec) -> JobRecord:
        if not isinstance(spec, JobSpec) or spec.kind not in self.verifiers:
            raise SchedulerError("unsupported_job_kind_or_spec")
        now = self._now()
        al = spec.allocation
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT * FROM jobs WHERE task_id=? OR "
                "(tenant_id=? AND run_id=? AND idempotency_key=?)",
                (spec.task_id, spec.tenant_id, spec.run_id,
                 spec.idempotency_key or None),
            ).fetchone()
            if existing is not None:
                # Identical replay of an acknowledged enqueue is idempotent.
                fields = (
                    existing["task_id"] == spec.task_id,
                    existing["tenant_id"] == spec.tenant_id,
                    existing["run_id"] == spec.run_id,
                    existing["kind"] == spec.kind,
                    existing["payload"] == self._encode(spec.payload),
                    existing["effect"] == spec.effect.value,
                    existing["idempotency_key"] == (spec.idempotency_key or None),
                    existing["priority"] == spec.priority,
                    existing["max_attempts"] == spec.max_attempts,
                    existing["cpu_millis"] == al.cpu_millis,
                    existing["memory_mb"] == al.total_memory_mb,
                    existing["disk_mb"] == al.workspace_disk_mb,
                )
                if not all(fields):
                    raise AdmissionRejected("idempotency_or_task_id_conflict")
                return self._record(existing)
            live = conn.execute(
                "SELECT tenant_id FROM jobs WHERE state IN "
                "('queued','running','reap_required','needs_reconciliation')"
            ).fetchall()
            if len(live) >= self.limits.max_live_jobs:
                raise AdmissionRejected("global_backpressure")
            if sum(row["tenant_id"] == spec.tenant_id for row in live) >= self.limits.max_live_per_tenant:
                raise AdmissionRejected("tenant_backpressure")
            if (al.cpu_millis > self.capacity.cpu_millis
                    or al.total_memory_mb > self.capacity.memory_mb
                    or al.workspace_disk_mb > self.capacity.disk_mb):
                raise AdmissionRejected("job_exceeds_host_capacity")
            conn.execute("""
                INSERT INTO jobs(
                    task_id,tenant_id,run_id,kind,payload,effect,idempotency_key,
                    cpu_millis,memory_mb,disk_mb,priority,state,attempts,
                    max_attempts,next_ready_at,lease_owner,lease_token,generation,
                    lease_deadline,cancel_requested,result,evidence,error_code,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',0,?,?,NULL,NULL,0,
                         NULL,0,NULL,NULL,NULL,?,?)
            """, (spec.task_id, spec.tenant_id, spec.run_id, spec.kind,
                  self._encode(spec.payload), spec.effect.value,
                  spec.idempotency_key or None, al.cpu_millis,
                  al.total_memory_mb, al.workspace_disk_mb, spec.priority,
                  spec.max_attempts, now, now, now))
            self._event(conn, spec.task_id, now, "queued", "accepted", 0)
            return self._get(conn, spec.task_id)

    def _used(self, conn: sqlite3.Connection) -> tuple[int, int, int, int, dict[str, int]]:
        rows = conn.execute(
            "SELECT tenant_id,cpu_millis,memory_mb,disk_mb FROM jobs "
            "WHERE state IN ('running','reap_required')"
        ).fetchall()
        tenants: dict[str, int] = {}
        for row in rows:
            tenants[row["tenant_id"]] = tenants.get(row["tenant_id"], 0) + 1
        return (len(rows), sum(r["cpu_millis"] for r in rows),
                sum(r["memory_mb"] for r in rows),
                sum(r["disk_mb"] for r in rows), tenants)

    def usage(self) -> dict[str, Any]:
        with self._connection() as conn:
            count, cpu, mem, disk, tenants = self._used(conn)
        return {"active_reservations": count, "cpu_millis": cpu,
                "memory_mb": mem, "disk_mb": disk,
                "active_by_tenant": tenants}

    def claim(self, worker: WorkerIdentity) -> WorkerLease | None:
        if not isinstance(worker, WorkerIdentity):
            raise SchedulerError("trusted_worker_identity_required")
        if not worker.allowed_tenants:
            return None
        now = self._now()
        with self._tx() as conn:
            running, cpu, memory, disk, tenants = self._used(conn)
            if running >= self.limits.max_running:
                return None
            rows = conn.execute(
                "SELECT * FROM jobs WHERE state='queued' AND next_ready_at<=? "
                "AND cancel_requested=0 ORDER BY priority DESC,created_at,task_id",
                (now,),
            ).fetchall()
            feasible = []
            for row in rows:
                if row["tenant_id"] not in worker.allowed_tenants:
                    continue
                if tenants.get(row["tenant_id"], 0) >= self.limits.max_running_per_tenant:
                    continue
                if (cpu + row["cpu_millis"] > self.capacity.cpu_millis
                        or memory + row["memory_mb"] > self.capacity.memory_mb
                        or disk + row["disk_mb"] > self.capacity.disk_mb):
                    continue
                feasible.append(row)
            if not feasible:
                return None
            tenant_candidates: dict[str, sqlite3.Row] = {}
            for row in feasible:
                tenant_candidates.setdefault(row["tenant_id"], row)
            last_rows = conn.execute(
                "SELECT tenant_id,last_turn FROM tenant_turn"
            ).fetchall()
            served = {x["tenant_id"]: x["last_turn"] for x in last_rows}
            next_tenant = min(tenant_candidates, key=lambda t: (served.get(t, 0), t))
            row = tenant_candidates[next_tenant]
            token = secrets.token_hex(24)
            generation = row["generation"] + 1
            deadline = now + self.limits.lease_seconds
            attempt = row["attempts"] + 1
            conn.execute(
                "UPDATE jobs SET state='running',attempts=?,generation=?,"
                "lease_owner=?,lease_token=?,lease_deadline=?,updated_at=? "
                "WHERE task_id=? AND state='queued'",
                (attempt, generation, worker.worker_id, token,
                 deadline, now, row["task_id"]),
            )
            turn = conn.execute(
                "SELECT turn FROM scheduler_meta WHERE singleton=1"
            ).fetchone()["turn"] + 1
            conn.execute("UPDATE scheduler_meta SET turn=? WHERE singleton=1",
                         (turn,))
            conn.execute(
                "INSERT INTO tenant_turn(tenant_id,last_turn) VALUES(?,?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET last_turn=excluded.last_turn",
                (next_tenant, turn),
            )
            self._event(conn, row["task_id"], now, "claimed",
                        "attempt_started", generation)
            return WorkerLease(
                row["task_id"], worker.worker_id, token, generation,
                deadline, attempt, row["kind"], json.loads(row["payload"]),
                Effect(row["effect"]),
            )

    def _fence(self, conn: sqlite3.Connection, lease: WorkerLease,
               now: float, *, allow_cancel: bool = False) -> JobRecord:
        if not isinstance(lease, WorkerLease):
            raise LeaseFenced("invalid_lease")
        record = self._get(conn, lease.task_id)
        raw = conn.execute(
            "SELECT lease_token FROM jobs WHERE task_id=?", (lease.task_id,)
        ).fetchone()
        if (record.state != "running" or record.generation != lease.generation
                or record.lease_owner != lease.worker_id
                or raw["lease_token"] != lease.token
                or record.lease_deadline is None
                or now >= record.lease_deadline
                or (record.cancel_requested and not allow_cancel)):
            raise LeaseFenced("stale_or_cancelled_lease")
        return record

    def heartbeat(self, lease: WorkerLease) -> bool:
        now = self._now()
        with self._tx() as conn:
            try:
                self._fence(conn, lease, now)
            except (KeyError, LeaseFenced):
                return False
            conn.execute("UPDATE jobs SET lease_deadline=?,updated_at=? WHERE task_id=?",
                         (now + self.limits.lease_seconds, now, lease.task_id))
            self._event(conn, lease.task_id, now, "heartbeat",
                        "lease_extended", lease.generation)
            return True

    def complete(self, lease: WorkerLease, result: Any) -> JobRecord:
        now = self._now()
        payload = self._encode(result)
        if len(payload.encode("utf-8")) > 16384:
            raise SchedulerError("result_too_large")
        with self._tx() as conn:
            job = self._fence(conn, lease, now)
            # Host-owned, side-effect-free verifier. Worker does not pass a
            # 'verified' flag. For real external effects, use a trusted
            # service outside SQLite transaction and fencing at the backend.
            try:
                accepted = self.verifiers[job.kind](job, result)
            except Exception:
                accepted = False
            if type(accepted) is not bool:
                accepted = False
            next_state = "verified" if accepted else "failed"
            reason = "host_verified" if accepted else "verification_failed"
            conn.execute(
                "UPDATE jobs SET state=?,result=?,evidence=?,error_code=?,"
                "lease_owner=NULL,lease_token=NULL,lease_deadline=NULL,updated_at=? "
                "WHERE task_id=?",
                (next_state, payload if accepted else None,
                 reason if accepted else None,
                 None if accepted else reason, now, job.task_id),
            )
            self._event(conn, job.task_id, now, next_state, reason,
                        job.generation)
            return self._get(conn, job.task_id)

    def _retry_delay(self, attempts: int) -> float:
        return min(self.limits.max_retry_seconds,
                   self.limits.base_retry_seconds * (2.0 ** (attempts - 1)))

    def _transition_after_failure(self, conn: sqlite3.Connection,
                                  job: JobRecord, now: float, *,
                                  reason: str, retryable: bool) -> JobRecord:
        if job.effect is Effect.NON_IDEMPOTENT:
            state = "needs_reconciliation"
            ready_at = job.next_ready_at
        elif job.cancel_requested:
            state = "cancelled"
            ready_at = job.next_ready_at
        elif retryable and job.attempts < job.max_attempts:
            state = "queued"
            ready_at = now + self._retry_delay(job.attempts)
        else:
            state = "failed"
            ready_at = job.next_ready_at
        conn.execute(
            "UPDATE jobs SET state=?,next_ready_at=?,error_code=?,"
            "lease_owner=NULL,lease_token=NULL,lease_deadline=NULL,updated_at=? "
            "WHERE task_id=?",
            (state, ready_at, reason, now, job.task_id),
        )
        self._event(conn, job.task_id, now, state, reason, job.generation)
        return self._get(conn, job.task_id)

    def fail(self, lease: WorkerLease, *, reason: str = "handler_failed",
             retryable: bool = True) -> JobRecord:
        if (not isinstance(reason, str) or not _ID.fullmatch(reason)
                or type(retryable) is not bool):
            raise SchedulerError("invalid_failure_reason")
        now = self._now()
        with self._tx() as conn:
            job = self._fence(conn, lease, now)
            return self._transition_after_failure(
                conn, job, now, reason=reason, retryable=retryable,
            )

    def request_cancel(self, task_id: str) -> JobRecord:
        now = self._now()
        with self._tx() as conn:
            job = self._get(conn, task_id)
            if job.state in ("verified", "failed", "cancelled"):
                return job
            if job.state == "queued":
                conn.execute(
                    "UPDATE jobs SET state='cancelled',cancel_requested=1,"
                    "updated_at=? WHERE task_id=?", (now, task_id),
                )
            else:
                # Running or unresolved side-effect jobs cannot be honestly
                # declared stopped by a mere API flag.
                conn.execute(
                    "UPDATE jobs SET cancel_requested=1,updated_at=? WHERE task_id=?",
                    (now, task_id),
                )
            self._event(conn, task_id, now, "cancel_requested",
                        "host_requested", job.generation)
            return self._get(conn, task_id)

    def ack_cancel(self, lease: WorkerLease) -> JobRecord:
        """Only after the TRUSTED worker reports it has stopped.

        For non-idempotent work, even an ACK cannot prove whether the
        outside-world effect committed: reconciliation remains required.
        """
        now = self._now()
        with self._tx() as conn:
            job = self._fence(conn, lease, now, allow_cancel=True)
            if not job.cancel_requested:
                raise InvalidTransition("cancellation_not_requested")
            state = ("needs_reconciliation" if job.effect is Effect.NON_IDEMPOTENT
                     else "cancelled")
            conn.execute(
                "UPDATE jobs SET state=?,lease_owner=NULL,lease_token=NULL,"
                "lease_deadline=NULL,error_code=?,updated_at=? WHERE task_id=?",
                (state, "worker_cancel_ack", now, job.task_id),
            )
            self._event(conn, job.task_id, now, state,
                        "worker_cancel_ack", job.generation)
            return self._get(conn, job.task_id)

    def expire_leases(self) -> tuple[str, ...]:
        """Fence expired worker results but KEEP host capacity reserved.

        An expired lease does not prove that its actual sandbox has stopped.
        An independent trusted reaper must confirm destruction before retry.
        """
        now = self._now()
        expired: list[str] = []
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT task_id,generation FROM jobs WHERE state='running' "
                "AND lease_deadline<=? ORDER BY task_id", (now,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE jobs SET state='reap_required',lease_token=NULL,"
                    "updated_at=? WHERE task_id=?",
                    (now, row["task_id"]),
                )
                self._event(conn, row["task_id"], now, "reap_required",
                            "lease_expired", row["generation"])
                expired.append(row["task_id"])
        return tuple(expired)

    def confirm_reaped(self, task_id: str, generation: int, *,
                       cleanup_confirmed: bool) -> JobRecord:
        """Call only after Host verified process/VM and resources are gone."""
        if cleanup_confirmed is not True:
            raise InvalidTransition("real_cleanup_confirmation_required")
        now = self._now()
        with self._tx() as conn:
            job = self._get(conn, task_id)
            if job.state != "reap_required" or job.generation != generation:
                raise InvalidTransition("invalid_reap_generation_or_state")
            return self._transition_after_failure(
                conn, job, now, reason="lease_expired_reaped", retryable=True,
            )

    def reconcile(self, task_id: str, generation: int, *, decision: str,
                  evidence: str, result: Any = None) -> JobRecord:
        """Trusted host/operator asserts external fact, not a worker guess.

        'committed': verify result with host verifier; may become verified.
        'not_applied': safe to retry (or cancel) if budget permits.
        """
        if decision not in ("committed", "not_applied"):
            raise InvalidTransition("external_outcome_must_be_determined")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 256:
            raise SchedulerError("trusted_external_evidence_required")
        now = self._now()
        with self._tx() as conn:
            job = self._get(conn, task_id)
            if job.state != "needs_reconciliation" or generation != job.generation:
                raise InvalidTransition("invalid_reconciliation_state_or_generation")
            if decision == "committed":
                serialized = self._encode(result)
                accepted = self.verifiers[job.kind](job, result)
                state = "verified" if accepted is True else "failed"
                error = None if accepted is True else "reconciliation_verification_failed"
                conn.execute(
                    "UPDATE jobs SET state=?,result=?,evidence=?,error_code=?,updated_at=? "
                    "WHERE task_id=?",
                    (state, serialized if accepted is True else None,
                     evidence if accepted is True else None, error, now, task_id),
                )
                self._event(conn, task_id, now, state,
                            "host_reconciliation", generation)
                return self._get(conn, task_id)
            if job.cancel_requested:
                state, ready = "cancelled", job.next_ready_at
            elif job.attempts >= job.max_attempts:
                state, ready = "failed", job.next_ready_at
            else:
                state, ready = "queued", now + self._retry_delay(job.attempts)
            conn.execute(
                "UPDATE jobs SET state=?,next_ready_at=?,error_code=?,updated_at=? "
                "WHERE task_id=?",
                (state, ready, "confirmed_not_applied", now, task_id),
            )
            self._event(conn, task_id, now, state, "host_reconciliation", generation)
            return self._get(conn, task_id)


class AsyncWorkerPool:
    """Runs ONLY pre-registered TRUSTED async Python handlers.

    This runner is not an isolated worker. It polls ready work until no tasks
    are immediately claimable; delayed retries require a later invocation.
    Handler cancellation (asyncio.wait_for) does not undo external effects.
    """

    def __init__(
        self, queue: SQLiteTaskScheduler, worker: WorkerIdentity,
        handlers: Mapping[str, Handler], *,
        workers: int = 2, timeout_seconds: float = 5.0,
    ) -> None:
        if (type(workers) is not int or workers < 1
                or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0):
            raise SchedulerError("invalid_async_worker_limits")
        if not handlers or any(not callable(v) for v in handlers.values()):
            raise SchedulerError("trusted_handlers_required")
        self.queue = queue
        self.worker = worker
        self.handlers = dict(handlers)
        self.workers = workers
        self.timeout_seconds = float(timeout_seconds)

    async def drain_ready(self) -> int:
        count = 0

        async def loop(worker_index: int) -> None:
            nonlocal count
            identity = WorkerIdentity(
                self.worker.worker_id + ".slot" + str(worker_index),
                self.worker.allowed_tenants,
            )
            while True:
                lease = self.queue.claim(identity)
                if lease is None:
                    return
                count += 1
                handler = self.handlers.get(lease.kind)
                if handler is None:
                    self.queue.fail(lease, reason="unregistered_trusted_handler",
                                    retryable=False)
                    continue
                try:
                    result = await asyncio.wait_for(
                        handler(lease), timeout=self.timeout_seconds,
                    )
                except asyncio.CancelledError:
                    self.queue.fail(lease, reason="handler_cancelled")
                    raise
                except Exception:
                    self.queue.fail(lease, reason="handler_failed")
                    continue
                self.queue.complete(lease, result)

        await asyncio.gather(*(loop(i) for i in range(self.workers)))
        return count
