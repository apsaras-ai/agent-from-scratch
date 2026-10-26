# Chapter 20 — Agent Scheduler production readiness checklist

> Status: **architecture reference, NOT a deployment recipe**. The Chapter 20
> SQLite implementation demonstrates durable claims and coordinated bookkeeping
> over one database file. It does not authenticate remote workers, create actual
> sandbox resources, enforce cross-machine fencing, deliver exactly-once effects
> or run a production-grade Queue/Worker fleet.

## 1. State ownership and Task Identity

- [ ] Map User Task, Run, Workflow Step, Activity Job, Attempt, Worker Identity and Sandbox ID.
- [ ] Define which service owns each field in the Run and Queue state machine.
- [ ] All accepted writes include tenant, run, activity identity and idempotency key.
- [ ] Duplicate submissions with identical keys and content resolve to the existing job.
- [ ] Duplicate keys with conflicting content are rejected and audited.
- [ ] Separate "queued", "running", "reap_required", "needs_reconciliation",
      "cancelled", "failed", "verified".
- [ ] A model statement like "done" cannot set the verified state directly.
- [ ] Cancellation is a request until the real workload is stopped and accepted.
- [ ] External effects use an independent operation ledger, not only queue status.

## 2. Atomic claims and multi-host concurrency

- [ ] Test two independent clients competing for the same queued task.
- [ ] Claim selection, allocation reservation, lease generation, audit event and
      status update occur in a deliberate transaction boundary.
- [ ] Stale generation/token/worker results are rejected after a new attempt.
- [ ] The resource actually being written also enforces fencing or
      operation idempotency; queue fencing alone cannot stop a stale worker.
- [ ] Job lease owner is authenticated using workload identity, not model text.
- [ ] Queue storage has expected write-throughput, transaction conflict behavior,
      schema migration, backup/restore and high-availability behavior.
- [ ] PostgreSQL FOR UPDATE SKIP LOCKED is understood as a queue-consumer
      technique, not a general consistent snapshot mechanism.

## 3. Resource admission and backpressure

- [ ] Host allocatable CPU/Mem/Disk explicitly exclude system overhead.
- [ ] Each worker reservation includes guest memory and runtime overhead.
- [ ] Tenant quotas, global task count and Worker concurrent slots are bounded.
- [ ] Task submissions exceeding live-job quota receive a real admission denial.
- [ ] Jobs individually exceeding HostCapacity are refused, not accepted forever.
- [ ] Running AND Reap-Required workloads remain in resource reservations.
- [ ] Unresolved external-side-effect jobs remain live business obligations.
- [ ] Quota includes token, provider API rate, wall time, disk I/O and tool budget.
- [ ] Retrying clients use stable idempotency keys and bounded backoff with jitter.
- [ ] Deadlines expire queued tasks with clear user-visible consequences.
- [ ] Submit Accepted and Resource Ready are distinct public states.

## 4. Tenant fairness and scheduling policy

- [ ] Fairness objective is documented: claim count, CPU time, value, token cost or DRF.
- [ ] Priority only applies within the chosen fairness policy; no silent tenant starvation.
- [ ] Workloads declare resource shape, estimated time and trusted effect category.
- [ ] Different pools exist for long Coding tasks, Browser sessions, lightweight
      model I/O and expensive GPU operations where useful.
- [ ] Simulated burst load tests cover tenant A sending far more work than B.
- [ ] Fairness includes waiting-time and actual-resource distribution, not only
      the number of claimed jobs.
- [ ] Metrics include queue-depth/age, p95/p99 scheduling delay and
      cost per independently verified task.

## 5. Lease, heartbeat and fencing

- [ ] Lease token is never sent into model prompts, logs or untrusted artifacts.
- [ ] Generation monotonically increases for each successful re-claim.
- [ ] Heartbeat is a host-authenticated request and fails on expired/cancelled lease.
- [ ] Independent supervisor runs expiry scanning and cleanup service.
- [ ] Expiry does NOT release CPU/memory/disk until real sandbox cleanup confirmed.
- [ ] Failed cleanup remains reap_required, alerting and consuming capacity.
- [ ] Lease/Worker clock comes from a stable authoritative source.
- [ ] Worker heartbeat loss and Host network partition are tested independently.
- [ ] Zombie worker still attempting external writes is rejected at effect boundary.
- [ ] Failed/expired Resource and Task events are kept in a durable, redacted log.

## 6. Retry and non-idempotent effects

- [ ] Effect classification (READ_ONLY / IDEMPOTENT / NON_IDEMPOTENT)
      comes from trusted Tool Registry and is reviewed for each tool.
- [ ] READ_ONLY and IDEMPOTENT have max-attempts, retry delay and jitter.
- [ ] Timeout/connection reset does not prove an external write failed.
- [ ] Non-idempotent ambiguous outcomes go to needs_reconciliation by default.
- [ ] Reconciliation checks authoritative external service state and
      records evidence/receipt with identity and approved scope.
- [ ] "not_applied" proof allows retry only within the existing attempt/approval budget.
- [ ] "committed" proof must still pass trusted independent business verification.
- [ ] No blind duplicate email, payment, deployment or publication on retry.
- [ ] Worker/server restarts and delayed ACKs do not duplicate externally committed work.
- [ ] Reconciliation does not expose a public API that accepts arbitrary
      untrusted "committed" flags or forged receipt text.

## 7. Running cancellation and sandbox lifecycle

- [ ] Queued task cancellation prevents future Claim.
- [ ] Running task cancellation is recorded as pending and does not release memory.
- [ ] Worker sees cancellation through authenticated control channel.
- [ ] Host can terminate entire sandbox/process tree, not only a Python coroutine.
- [ ] Cleanup receipt confirms process/VM, network, mounts, workspace and
      credentials have been retired.
- [ ] Non-idempotent external effects remain unresolved after a cancellation ACK.
- [ ] A long-running task may need cooperative cancellation and a stronger
      hard timeout from the trusted sandbox platform.
- [ ] Reaper failure and repeated cleanup commands are handled idempotently.

## 8. Deployment platform architecture

```text
Trusted API / Identity
        ↓
Admission + Tenant Quota
        ↓
Durable Task / Run / Event Store
        ↓
Fair Scheduler + Lease Registry
        ↓
Worker Identity + Sandbox Controller
        ↓
Hardened OCI / gVisor / Firecracker
        ↓
Host Tool Proxy (Chapter 19 Security PEP)
        ↓
Verified Effect Receipt / Scoped Artifact Store
        ↓
Durable Run State + Telemetry + Reconciliation
```

**No raw Docker socket, cloud admin credential or host filesystem is handed to
the untrusted worker.** The model only proposes a tool request; the host decides.

## 9. Observability and failure-injection matrix

| Injection | Correct outcome |
| --- | --- |
| Two workers claim same task | One valid lease, one no-op |
| Lease expires while worker runs | Reap required; capacity still charged |
| Old ACK after retry begins | Fencing rejected |
| Worker heartbeat fails | Expiration path, not fake Verified |
| Read-only handler transient error | Retry after bounded backoff |
| Non-idempotent handler reset | Unknown external effect; no automatic retry |
| User cancels queued job | No future Claim |
| User cancels running job | Pending until ACK/reap; verified effect still checked |
| Database restart | Claims/history persisted |
| Sandbox cleanup stuck | Capacity still charged; operational alert |
| API burst | Backpressure, not unbounded memory |
| Tenant A dominates queue | Tenant B retains fair service opportunity |
| Verifier unavailable | Never mark Verified |
| Operation already committed but ACK lost | Query/reconcile, don't double-submit |

## 10. Key production metrics

```text
agent_queue_live_jobs{tenant,kind}
agent_queue_ready_depth{tenant,kind}
agent_queue_wait_seconds_bucket{pool,tenant_class}
agent_workers_busy{pool}
agent_lease_expired_total{pool,reason}
agent_reap_required_seconds{host,pool}
agent_reserved_cpu_millis{pool}
agent_reserved_memory_bytes{pool}
agent_retry_total{kind,reason}
agent_reconciliation_pending{kind}
agent_cancel_to_destroy_seconds{pool}
agent_admission_rejections_total{tenant_class,reason}
agent_verified_task_success_total{kind}
agent_task_cost_per_verified_task{kind}
```

Avoid task IDs, email addresses, raw user data and secrets as unbounded Prometheus labels. Use authorized logs/traces for per-run diagnostics.

## 11. What Chapter 20 does *not* implement

- Real Worker identity authentication / mTLS;
- Real VM/container process termination and cleanup receipts;
- Automatic recurring Heartbeat/Expiry/Reaper Scheduler;
- Distributed consensus, leader election or multi-region operation;
- Hardware GPU placement, network quota, PIDs/IO cgroup enforcement;
- Atomic external payment/email effect + database queue completion;
- Real Idempotency Key support by third-party services;
- Durable human approvals, per-tenant credential vault;
- Production fairness guarantees, SLA-based scheduling or weighted DRF;
- Token/provider billing, safe redaction and genuine security isolation.

All of these require their own implementation and authorized integration tests.

## References

[Temporal Tasks](https://docs.temporal.io/tasks) ·
[Temporal Activity Failure Detection](https://docs.temporal.io/encyclopedia/detecting-activity-failures) ·
[PostgreSQL SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html) ·
[AWS SQS Visibility Timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html) ·
[Celery Tasks and Retry](https://docs.celeryq.dev/en/stable/userguide/tasks.html) ·
[Python asyncio](https://docs.python.org/3.11/library/asyncio-task.html).
