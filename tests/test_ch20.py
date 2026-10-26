"""Chapter 20: durable scheduling, fairness, lease fencing, and failure tests.

No real network, shell, sandbox, remote queue, worker lease server or tool
side effects; only SQLite and trusted async Python fixture handlers.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from mini_agent.sandbox_reservations import HostCapacity, SandboxAllocation
from mini_agent.runtime_scheduler import (
    AdmissionRejected, AsyncWorkerPool, Effect, InvalidTransition,
    JobSpec, LeaseFenced, SchedulerError, SchedulerLimits, SQLiteTaskScheduler,
    WorkerIdentity, WorkerLease,
)


class SchedulerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "queue.sqlite"
        self.now = [1000.0]
        self.capacity = HostCapacity(8000, 16_384, 16_384)
        self.limits = SchedulerLimits(
            max_live_jobs=50, max_live_per_tenant=20, max_running=8,
            max_running_per_tenant=4, lease_seconds=10,
            base_retry_seconds=2, max_retry_seconds=8,
        )
        self.accept = lambda job, result: result == {
            "answer": job.payload["expected"]
        }
        self.queue = self.open_queue()
        self.worker = WorkerIdentity("worker", frozenset({"alpha", "beta"}))
        self.allocation = SandboxAllocation(
            1000, 512, 128, 256, ttl_seconds=60
        )

    def tearDown(self):
        self.temp.cleanup()

    def open_queue(self):
        return SQLiteTaskScheduler(
            self.path, capacity=self.capacity, limits=self.limits,
            verifiers={"fixture.echo": self.accept},
            clock=lambda: self.now[0],
        )

    def job(self, task_id, *, tenant="alpha", run="run",
            expected="ok", effect=Effect.READ_ONLY, idem="",
            priority=0, attempts=3, allocation=None):
        return JobSpec(
            task_id, tenant, run, "fixture.echo", {"expected": expected},
            allocation or self.allocation, effect=effect,
            idempotency_key=idem, priority=priority, max_attempts=attempts,
        )

    def claim(self):
        return self.queue.claim(self.worker)

    def test_queue_persists_across_scheduler_reinstantiation(self):
        self.queue.submit(self.job("task-a", idem="same-request"))
        new = self.open_queue()
        self.assertEqual(new.get("task-a").state, "queued")
        self.assertEqual(new.get("task-a").tenant_id, "alpha")
        self.assertEqual(len(new.events("task-a")), 1)
        self.assertEqual(new.events("task-a")[0].kind, "queued")

    def test_exact_idempotent_submit_replay(self):
        spec = self.job("task-a", idem="request-1")
        a = self.queue.submit(spec)
        b = self.queue.submit(spec)
        self.assertEqual(a.task_id, b.task_id)
        self.assertEqual(len(self.queue.events("task-a")), 1)
        with self.assertRaises(AdmissionRejected):
            self.queue.submit(replace(spec, payload={"expected": "different"}))
        with self.assertRaises(AdmissionRejected):
            self.queue.submit(replace(spec, task_id="different-task"))
        with self.assertRaises(AdmissionRejected):
            self.queue.submit(replace(spec, idempotency_key="different-key"))

    def test_claim_and_host_verified_completion(self):
        self.queue.submit(self.job("task-a"))
        lease = self.claim()
        self.assertEqual((lease.attempt, lease.generation), (1, 1))
        self.assertEqual(lease.kind, "fixture.echo")
        self.assertEqual(self.queue.usage()["memory_mb"], 640)
        done = self.queue.complete(lease, {"answer": "ok"})
        self.assertEqual(done.state, "verified")
        self.assertEqual(done.result, {"answer": "ok"})
        self.assertEqual(done.evidence, "host_verified")
        self.assertEqual(self.queue.usage()["active_reservations"], 0)
        self.assertEqual(
            [e.kind for e in self.queue.events("task-a")],
            ["queued", "claimed", "verified"],
        )

    def test_self_reported_done_does_not_pass_host_verifier(self):
        self.queue.submit(self.job("task-a", expected="confirmed"))
        lease = self.claim()
        record = self.queue.complete(lease, {"answer": "model_says_done"})
        self.assertEqual(record.state, "failed")
        self.assertEqual(record.error_code, "verification_failed")
        self.assertIsNone(record.evidence)

    def test_expiration_needs_host_reap_before_capacity_release(self):
        constrained = SQLiteTaskScheduler(
            self.path, capacity=HostCapacity(1000, 640, 256),
            limits=self.limits,
            verifiers={"fixture.echo": self.accept},
            clock=lambda: self.now[0],
        )
        constrained.submit(self.job("task-a"))
        constrained.submit(self.job("task-b"))
        first = constrained.claim(self.worker)
        self.assertIsNotNone(first)
        self.assertIsNone(constrained.claim(self.worker))
        self.now[0] = 1010.0
        self.assertEqual(constrained.expire_leases(), ("task-a",))
        self.assertEqual(constrained.get("task-a").state, "reap_required")
        self.assertEqual(constrained.usage()["memory_mb"], 640)
        self.assertIsNone(constrained.claim(self.worker))
        with self.assertRaises(LeaseFenced):
            constrained.complete(first, {"answer": "ok"})
        with self.assertRaises(InvalidTransition):
            constrained.confirm_reaped("task-a", 1, cleanup_confirmed=False)
        retry = constrained.confirm_reaped("task-a", 1, cleanup_confirmed=True)
        self.assertEqual(retry.state, "queued")
        self.assertEqual(constrained.usage()["memory_mb"], 0)
        # task-a is delayed by backoff; b is ready immediately.
        self.assertEqual(constrained.claim(self.worker).task_id, "task-b")

    def test_stale_generation_and_stale_worker_cannot_heartbeat_or_complete(self):
        self.queue.submit(self.job("task-a"))
        first = self.claim()
        self.now[0] = 1010.0
        self.queue.expire_leases()
        self.queue.confirm_reaped("task-a", 1, cleanup_confirmed=True)
        self.now[0] = 1012.0
        second = self.claim()
        self.assertEqual((second.attempt, second.generation), (2, 2))
        self.assertNotEqual(first.token, second.token)
        self.assertFalse(self.queue.heartbeat(first))
        with self.assertRaises(LeaseFenced):
            self.queue.fail(first)
        with self.assertRaises(LeaseFenced):
            self.queue.complete(replace(second, token="forged"), {"answer": "ok"})
        self.assertEqual(
            self.queue.complete(second, {"answer": "ok"}).state, "verified",
        )

    def test_heartbeat_extends_lease_and_replay_does_not(self):
        self.queue.submit(self.job("task-a"))
        lease = self.claim()
        self.now[0] = 1005.0
        self.assertTrue(self.queue.heartbeat(lease))
        self.assertEqual(self.queue.get("task-a").lease_deadline, 1015.0)
        self.now[0] = 1011.0
        self.assertEqual(self.queue.expire_leases(), ())
        self.assertEqual(self.queue.complete(lease, {"answer": "ok"}).state, "verified")
        self.assertFalse(self.queue.heartbeat(lease))

    def test_known_readonly_failure_backoff_is_bounded(self):
        self.queue.submit(self.job("task-a", attempts=3))
        for attempt, delay in ((1, 2), (2, 4)):
            lease = self.claim()
            self.assertEqual(lease.attempt, attempt)
            result = self.queue.fail(lease, reason="temporary", retryable=True)
            self.assertEqual(result.state, "queued")
            self.assertEqual(result.next_ready_at, self.now[0] + delay)
            self.assertIsNone(self.claim())
            self.now[0] += delay
        third = self.claim()
        self.assertEqual(third.attempt, 3)
        result = self.queue.fail(third, reason="still_failing", retryable=True)
        self.assertEqual(result.state, "failed")
        self.assertIsNone(self.claim())

    def test_known_nonretryable_error_terminal(self):
        self.queue.submit(self.job("task-a"))
        lease = self.claim()
        state = self.queue.fail(lease, reason="invalid_input", retryable=False)
        self.assertEqual(state.state, "failed")
        self.assertEqual(state.attempts, 1)

    def test_non_idempotent_expiry_never_automatically_requeues(self):
        self.queue.submit(self.job("task-a", effect=Effect.NON_IDEMPOTENT))
        lease = self.claim()
        self.now[0] = 1010
        self.queue.expire_leases()
        state = self.queue.confirm_reaped("task-a", 1, cleanup_confirmed=True)
        self.assertEqual(state.state, "needs_reconciliation")
        self.assertEqual(self.queue.usage()["active_reservations"], 0)
        self.now[0] = 9000
        self.assertIsNone(self.claim())
        with self.assertRaises(LeaseFenced):
            self.queue.complete(lease, {"answer": "ok"})

    def test_non_idempotent_failure_requires_reconciliation(self):
        self.queue.submit(self.job("task-a", effect=Effect.NON_IDEMPOTENT))
        lease = self.claim()
        self.assertEqual(self.queue.fail(lease).state, "needs_reconciliation")
        self.assertEqual(self.queue.get("task-a").attempts, 1)
        self.assertIsNone(self.claim())

    def test_host_reconciles_committed_effect_with_independent_verifier(self):
        self.queue.submit(self.job("task-a", effect=Effect.NON_IDEMPOTENT))
        lease = self.claim()
        self.queue.fail(lease)
        with self.assertRaises(InvalidTransition):
            self.queue.reconcile("task-a", 1, decision="unknown", evidence="host-read")
        done = self.queue.reconcile(
            "task-a", 1, decision="committed",
            evidence="trusted_external_record_id", result={"answer": "ok"},
        )
        self.assertEqual(done.state, "verified")
        self.assertEqual(done.evidence, "trusted_external_record_id")
        with self.assertRaises(InvalidTransition):
            self.queue.reconcile("task-a", 1, decision="committed",
                                 evidence="repeat", result={"answer": "ok"})

    def test_host_reconciliation_rejects_unverified_false_success(self):
        self.queue.submit(self.job("task-a", effect=Effect.NON_IDEMPOTENT,
                                   expected="true"))
        lease = self.claim()
        self.queue.fail(lease)
        final = self.queue.reconcile(
            "task-a", 1, decision="committed", evidence="host_checked",
            result={"answer": "model_done"},
        )
        self.assertEqual(final.state, "failed")
        self.assertEqual(final.error_code, "reconciliation_verification_failed")

    def test_host_reconciles_not_applied_then_retries(self):
        self.queue.submit(self.job("task-a", effect=Effect.NON_IDEMPOTENT))
        lease = self.claim()
        self.queue.fail(lease)
        later = self.queue.reconcile(
            "task-a", 1, decision="not_applied", evidence="host_no_external_record",
        )
        self.assertEqual(later.state, "queued")
        self.assertIsNone(self.claim())
        self.now[0] += 2
        second = self.claim()
        self.assertEqual(second.attempt, 2)

    def test_ready_cancel_is_terminal_without_worker(self):
        self.queue.submit(self.job("task-a"))
        state = self.queue.request_cancel("task-a")
        self.assertEqual(state.state, "cancelled")
        self.assertEqual(self.queue.request_cancel("task-a").state, "cancelled")
        self.assertIsNone(self.claim())
        self.assertEqual(self.queue.usage()["active_reservations"], 0)

    def test_inflight_cancel_waits_for_worker_ack(self):
        self.queue.submit(self.job("task-a"))
        lease = self.claim()
        pending = self.queue.request_cancel("task-a")
        self.assertEqual(pending.state, "running")
        self.assertTrue(pending.cancel_requested)
        self.assertEqual(self.queue.usage()["active_reservations"], 1)
        self.assertFalse(self.queue.heartbeat(lease))
        with self.assertRaises(LeaseFenced):
            self.queue.complete(lease, {"answer": "ok"})
        self.assertEqual(self.queue.ack_cancel(lease).state, "cancelled")

    def test_non_idempotent_cancel_ack_still_reconciles(self):
        self.queue.submit(self.job("task-a", effect=Effect.NON_IDEMPOTENT))
        lease = self.claim()
        self.queue.request_cancel("task-a")
        state = self.queue.ack_cancel(lease)
        self.assertEqual(state.state, "needs_reconciliation")
        self.assertEqual(
            self.queue.reconcile("task-a", 1, decision="not_applied",
                                 evidence="checked_no_commit").state,
            "cancelled",
        )

    def test_cancellation_during_lease_expiry_not_claimed(self):
        self.queue.submit(self.job("task-a"))
        lease = self.claim()
        self.queue.request_cancel("task-a")
        self.now[0] = 1010.0
        self.assertEqual(self.queue.expire_leases(), ("task-a",))
        self.assertEqual(self.queue.usage()["active_reservations"], 1)
        self.assertEqual(self.queue.confirm_reaped(
            "task-a", 1, cleanup_confirmed=True).state, "cancelled")

    def test_fair_tenant_round_robin_and_intra_tenant_priority(self):
        for task, tenant, priority in (
            ("a0", "alpha", 0), ("a1", "alpha", 10),
            ("b0", "beta", 0), ("b1", "beta", 0),
        ):
            self.queue.submit(self.job(task, tenant=tenant, priority=priority))
        order = [self.claim().task_id for _ in range(4)]
        self.assertEqual(order, ["a1", "b0", "a0", "b1"])

    def test_backpressure_global_and_per_tenant(self):
        q = SQLiteTaskScheduler(
            self.path, capacity=self.capacity,
            limits=replace(self.limits, max_live_jobs=2,
                           max_live_per_tenant=1),
            verifiers={"fixture.echo": self.accept}, clock=lambda: self.now[0],
        )
        q.submit(self.job("a1", tenant="alpha"))
        with self.assertRaisesRegex(AdmissionRejected, "tenant_backpressure"):
            q.submit(self.job("a2", tenant="alpha"))
        q.submit(self.job("b1", tenant="beta"))
        with self.assertRaisesRegex(AdmissionRejected, "global_backpressure"):
            q.submit(self.job("a3", tenant="alpha"))
        q.request_cancel("a1")
        q.submit(self.job("a3", tenant="alpha"))

    def test_resource_overhead_is_counted_for_admission(self):
        q = SQLiteTaskScheduler(
            self.path, capacity=HostCapacity(1800, 1100, 300),
            limits=self.limits, verifiers={"fixture.echo": self.accept},
            clock=lambda: self.now[0],
        )
        with self.assertRaisesRegex(AdmissionRejected, "job_exceeds_host_capacity"):
            q.submit(self.job("a", allocation=SandboxAllocation(
                2000, 512, 128, 256, 10,
            )))
        with self.assertRaisesRegex(AdmissionRejected, "job_exceeds_host_capacity"):
            q.submit(self.job("b", allocation=SandboxAllocation(
                1000, 1024, 128, 256, 10,
            )))
        q.submit(self.job("c"))
        q.submit(self.job("d"))
        first = q.claim(self.worker)
        self.assertEqual(first.task_id, "c")
        self.assertIsNone(q.claim(self.worker))
        self.assertEqual(q.usage()["memory_mb"], 640)

    def test_worker_tenant_scope_blocks_cross_tenant_claim(self):
        self.queue.submit(self.job("private", tenant="beta"))
        alpha = WorkerIdentity("alpha-worker", frozenset({"alpha"}))
        self.assertIsNone(self.queue.claim(alpha))
        beta = WorkerIdentity("beta-worker", frozenset({"beta"}))
        self.assertEqual(self.queue.claim(beta).task_id, "private")

    def test_atomic_claim_from_two_separate_sqlite_schedulers(self):
        self.queue.submit(self.job("task-a"))
        first = self.open_queue()
        second = self.open_queue()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda scheduler: scheduler.claim(self.worker),
                                    (first, second)))
        self.assertEqual(sum(v is not None for v in results), 1)
        self.assertEqual(self.queue.get("task-a").attempts, 1)

    def test_stale_worker_cannot_resubmit_after_restart(self):
        self.queue.submit(self.job("task-a"))
        lease = self.claim()
        fresh = self.open_queue()
        self.assertEqual(fresh.get("task-a").state, "running")
        self.assertEqual(fresh.complete(lease, {"answer": "ok"}).state, "verified")
        with self.assertRaises(LeaseFenced):
            self.queue.complete(lease, {"answer": "ok"})

    def test_event_log_has_no_raw_payload_or_lease_token(self):
        self.queue.submit(self.job("task-a", expected="SENSITIVE_SYNTHETIC_VALUE"))
        lease = self.claim()
        self.queue.complete(lease, {"answer": "SENSITIVE_SYNTHETIC_VALUE"})
        all_events = repr(self.queue.events("task-a"))
        self.assertNotIn("SENSITIVE_SYNTHETIC_VALUE", all_events)
        self.assertNotIn(lease.token, all_events)
        self.assertIn("host_verified", all_events)

    def test_verifier_failure_and_exception_fail_closed(self):
        def explosive(job, result):
            raise RuntimeError("infrastructure unavailable")
        first = SQLiteTaskScheduler(
            self.path, capacity=self.capacity, limits=self.limits,
            verifiers={"fixture.echo": explosive}, clock=lambda: self.now[0],
        )
        first.submit(self.job("task-a"))
        lease = first.claim(self.worker)
        result = first.complete(lease, {"answer": "ok"})
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.error_code, "verification_failed")

    def test_job_spec_and_policy_validation(self):
        bad_jobs = [
            lambda: self.job("path/escape"),
            lambda: self.job("normal", effect="not_enum"),
            lambda: self.job("normal", priority=True),
            lambda: self.job("normal", attempts=0),
            lambda: self.job("normal", allocation="fake"),
            lambda: self.job("normal", idem="../bad"),
        ]
        for fn in bad_jobs:
            with self.subTest(fn=fn), self.assertRaises(SchedulerError):
                fn()
        for fn in [
            lambda: WorkerIdentity("", frozenset()),
            lambda: WorkerIdentity("worker", {"alpha"}),
            lambda: SchedulerLimits(max_running=0),
            lambda: SchedulerLimits(lease_seconds=float("nan")),
            lambda: SchedulerLimits(base_retry_seconds=10, max_retry_seconds=2),
        ]:
            with self.subTest(fn=fn), self.assertRaises(SchedulerError):
                fn()
        with self.assertRaises(SchedulerError):
            self.queue.submit(replace(self.job("task-a"), kind="unknown.kind"))

    def test_repeated_reap_confirmation_rejected(self):
        self.queue.submit(self.job("task-a"))
        lease = self.claim()
        self.now[0] = 1010
        self.queue.expire_leases()
        self.queue.confirm_reaped("task-a", 1, cleanup_confirmed=True)
        with self.assertRaises(InvalidTransition):
            self.queue.confirm_reaped("task-a", 1, cleanup_confirmed=True)

    def test_allocation_wins_over_unbounded_priority(self):
        q = SQLiteTaskScheduler(
            self.path, capacity=HostCapacity(1000, 640, 256),
            limits=self.limits, verifiers={"fixture.echo": self.accept},
            clock=lambda: self.now[0],
        )
        q.submit(self.job("a0", priority=5))
        q.submit(self.job("b0", priority=20))
        self.assertEqual(q.claim(self.worker).task_id, "b0")
        self.assertIsNone(q.claim(self.worker))


class AsyncPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "queue.db"
        self.now = [1000.0]
        self.capacity = HostCapacity(4000, 4096, 4096)
        self.allocation = SandboxAllocation(1000, 512, 128, 512, 60)
        self.queue = SQLiteTaskScheduler(
            self.path, capacity=self.capacity,
            limits=SchedulerLimits(max_running=2, max_running_per_tenant=2,
                                   base_retry_seconds=1, lease_seconds=30),
            verifiers={"echo": lambda job, result: result == job.payload["value"]},
            clock=lambda: self.now[0],
        )
        self.worker = WorkerIdentity("async-worker", frozenset({"alpha"}))

    async def asyncTearDown(self):
        self.temp.cleanup()

    def submit(self, key, value):
        self.queue.submit(JobSpec(key, "alpha", "run", "echo",
                                  {"value": value}, self.allocation))

    async def test_async_handlers_overlap_and_host_verify(self):
        active, peak = 0, 0
        async def execute(lease):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(.02)
                return lease.payload["value"]
            finally:
                active -= 1
        for key in "abcd":
            self.submit(key, key)
        pool = AsyncWorkerPool(self.queue, self.worker, {"echo": execute},
                               workers=2)
        self.assertEqual(await pool.drain_ready(), 4)
        self.assertEqual(peak, 2)
        self.assertTrue(all(self.queue.get(k).state == "verified" for k in "abcd"))
        self.assertEqual(self.queue.usage()["active_reservations"], 0)

    async def test_async_worker_failure_schedules_delayed_retry(self):
        self.submit("task-a", "correct")
        async def fail(lease):
            raise RuntimeError("unsafe_detail_suppressed")
        pool = AsyncWorkerPool(self.queue, self.worker, {"echo": fail}, workers=1)
        self.assertEqual(await pool.drain_ready(), 1)
        result = self.queue.get("task-a")
        self.assertEqual(result.state, "queued")
        self.assertEqual(result.error_code, "handler_failed")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(await pool.drain_ready(), 0)
        self.now[0] += 1
        good = AsyncWorkerPool(
            self.queue, self.worker,
            {"echo": lambda lease: asyncio.sleep(0, result=lease.payload["value"])},
            workers=1,
        )
        self.assertEqual(await good.drain_ready(), 1)
        self.assertEqual(self.queue.get("task-a").state, "verified")

    async def test_missing_registered_handler_fails_no_infinite_poll(self):
        self.submit("task-a", "correct")
        pool = AsyncWorkerPool(
            self.queue, self.worker, {"unused": lambda l: asyncio.sleep(0)}, workers=1)
        self.assertEqual(await pool.drain_ready(), 1)
        self.assertEqual(self.queue.get("task-a").state, "failed")

    async def test_async_handler_timeout_marks_retryable_failure(self):
        self.submit("task-a", "correct")
        async def long(lease):
            await asyncio.sleep(.2)
            return "correct"
        pool = AsyncWorkerPool(self.queue, self.worker, {"echo": long},
                               workers=1, timeout_seconds=.01)
        self.assertEqual(await pool.drain_ready(), 1)
        self.assertEqual(self.queue.get("task-a").state, "queued")
        self.assertEqual(self.queue.get("task-a").error_code, "handler_failed")


if __name__ == "__main__":
    unittest.main()
