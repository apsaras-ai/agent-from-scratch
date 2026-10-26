"""Chapter 20: SQLite scheduler, async workers, fair claims, lease reaping.

All work is deterministic, safe and offline. The handlers have no external
side effects; this example does not deploy real sandbox or distributed workers.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile

from mini_agent.runtime_scheduler import (
    AsyncWorkerPool, Effect, JobSpec, SchedulerLimits, SQLiteTaskScheduler,
    WorkerIdentity,
)
from mini_agent.sandbox_reservations import HostCapacity, SandboxAllocation


def make_task(key, tenant, *, effect=Effect.READ_ONLY):
    return JobSpec(
        task_id=key, tenant_id=tenant, run_id="demo",
        kind="fixture.echo", payload={"answer": key.upper()},
        allocation=SandboxAllocation(
            cpu_millis=1000, guest_memory_mb=512,
            runtime_overhead_mb=128, workspace_disk_mb=256, ttl_seconds=60,
        ),
        effect=effect, max_attempts=3,
    )


async def main():
    now = [1000.0]
    with tempfile.TemporaryDirectory() as scratch:
        db = Path(scratch) / "tasks.sqlite"
        capacity = HostCapacity(4000, 4096, 4096)
        limits = SchedulerLimits(
            max_live_jobs=10, max_running=2, max_running_per_tenant=2,
            lease_seconds=10, base_retry_seconds=2,
        )
        verifier = lambda job, result: result == job.payload["answer"]
        queue = SQLiteTaskScheduler(
            db, capacity=capacity, limits=limits,
            verifiers={"fixture.echo": verifier}, clock=lambda: now[0],
        )
        for item, tenant in (
            ("a1", "tenant-a"), ("a2", "tenant-a"),
            ("b1", "tenant-b"), ("b2", "tenant-b"),
        ):
            queue.submit(make_task(item, tenant))
        permit = WorkerIdentity(
            "fixture-worker", frozenset({"tenant-a", "tenant-b"}),
        )
        active, peak = 0, 0

        async def worker_handler(lease):
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            try:
                await asyncio.sleep(.02)
                return lease.payload["answer"]
            finally:
                active -= 1

        pool = AsyncWorkerPool(
            queue, permit, {"fixture.echo": worker_handler}, workers=2,
        )
        count = await pool.drain_ready()
        print("Processed tasks:", count)
        print("Peak active handlers:", peak)
        print("Verified results:", [
            queue.get(x).state for x in ("a1", "a2", "b1", "b2")
        ])
        print("Capacity after verified completion:", queue.usage()["memory_mb"])

        # A separate example: non-idempotent external effect with unknown
        # result. The Host first reaps the execution context, then checks the
        # external ledger. No automated duplicate request is made.
        queue.submit(make_task("send1", "tenant-a",
                               effect=Effect.NON_IDEMPOTENT))
        lease = queue.claim(permit)
        print("Effectful lease attempt:", lease.attempt, "generation:", lease.generation)
        now[0] = 1011.0
        print("Expired leases:", queue.expire_leases())
        print("Capacity still reserved:", queue.usage()["memory_mb"])
        # A real Host MUST verify actual worker/VM destruction. This teaching
        # call is a deterministic fake operator acknowledgment.
        queue.confirm_reaped(lease.task_id, lease.generation,
                             cleanup_confirmed=True)
        print("After fake reaper:", queue.get("send1").state)
        print("No automatic retry:", queue.claim(permit) is None)
        queue.reconcile(
            "send1", 1, decision="committed",
            evidence="synthetic_external_record_checked",
            result="SEND1",
        )
        print("After independent host reconciliation:", queue.get("send1").state)
        print("Audit kinds:", [e.kind for e in queue.events("send1")])
        assert queue.get("send1").state == "verified"
        print("No real external side effects or sandbox operations occurred.")


if __name__ == "__main__":
    asyncio.run(main())
