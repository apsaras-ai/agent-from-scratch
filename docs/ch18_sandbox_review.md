# Chapter 18 — Sandbox isolation and production-readiness review

> **Status: reference checklist, NOT a production deployment recipe.**  
> The Python Chapter-18 Process Executor does **not** implement these isolation guarantees. They must be implemented and verified by a trusted container/VM runtime and reviewed for the actual environment, kernel, image, workload and threat model.

## 1. Trust boundaries

```text
User/Model Proposal
   |
   v
Trusted Agent API ── Identity / Policy / Budget
   |
   v
Sandbox Control Plane ── Admission, placement, lease and audit
   |
   v
Host Agent (privileged, restricted API)
   |
   v
gVisor / Firecracker / hardened OCI runtime
   |
   v
Untrusted task (no host credentials)
   |
   v
Scoped Artifact & Egress gateways ── independent verifier
```

The model never obtains a raw Docker socket, Kubernetes cluster-admin token, host filesystem mount, Firecracker management socket or cloud provider metadata credentials. The worker sees only the task-specific scope.

## 2. Suggested API contract (design target only)

```json
{
  "create_request": {
    "tenant_id": "tenant-demo",
    "run_id": "run-123",
    "isolation_profile": "untrusted-python-v1",
    "image_digest": "sha256:<signed approved image digest>",
    "limits": {
      "cpu_millis": 1000,
      "guest_memory_mb": 512,
      "runtime_overhead_mb": 128,
      "workspace_disk_mb": 512,
      "pids_max": 64,
      "max_duration_seconds": 1200
    },
    "network_profile": "deny-by-default",
    "credentials_profile": "no-host-credentials"
  }
}
```

Do not allow the model to set `isolation_profile`, `image_digest`, `network_profile` or `credentials_profile` freely. The trusted policy layer validates them against the tenant and task.

```text
SandboxLease:
  run_id
  tenant_id
  sandbox_id
  isolation_backend
  allocation
  deadline
  generation
  state

ExecutionReceipt:
  sandbox_id
  task_id
  exit_status
  stdout_ref / stderr_ref
  artifact_refs
  resource_usage
  verified_by_host
  timestamps

TerminationReceipt:
  sandbox_id
  stop_command_accepted
  guest_processes_gone
  host_vmm_process_gone
  network_devices_detached
  mounts_released
  workspace_deleted_or_quarantined
  credentials_revoked
  host_verified
```

`TerminationReceipt.host_verified` must be derived from actual runtime state, not from a Worker self-report. In a real multi-host system receipts require authentication and durable event linkage.

## 3. Isolation acceptance matrix

| Control | Required evidence | Negative test |
| --- | --- | --- |
| Distinct guest/user identity | UID map or VM process identities | Guest cannot gain host administrator capabilities |
| PID isolation/reaping | PID namespace/cgroup membership | Terminating task kills its whole sandbox process tree |
| RootFS protection | Read-only approved base image digest | Guest cannot modify image/host files |
| Workspace separation | Distinct mounts / storage credentials | Tenant A cannot read Tenant B workspace |
| Network isolation | Effective namespace policy and Egress gateway | Guest cannot reach metadata/internal hosts |
| Tool authorization | Signed/host-approved task and tool policy | Prompt cannot authorize additional tool names |
| Resource limits | cgroup v2 or VM quota + host metrics | CPU/Memory/PIDs/IO limit tests in disposable environment |
| Deadline | Host-level kill + reaper/VM destroy | Guest cannot continue after verified destroy |
| Log/output bound | Streaming log size and retention policy | Output flood does not exhaust Agent API memory |
| Artifact publication | Host-side validation and tenant ACL | Unapproved file cannot be published |
| Secrets | Short-lived scoped token broker | Guest cannot read host/other tenant tokens |
| Snapshot hygiene | Template signatures, encryption, clone identity rotation | Restored tenant image has no prior tenant secrets |

Tests that probe cross-tenant isolation or security bypass must be performed only against a **dedicated, authorized environment** with a cleanup plan, not against shared CI/production hosts.

## 4. Resource sizing and reservation safety

For an available host budget:

```text
H_cpu = allocatable millicores
H_mem = allocatable MiB after host reserved headroom
H_disk = allocatable MiB after image/cache headroom
```

Each sandbox has:

```text
S_cpu = reserved millicores
S_mem = guest memory + VMM/runtime overhead
S_disk = workspace + expected artifact/log overhead
```

The conservative admission bound is:

\[
N \le \min\left(
\left\lfloor\frac{H_{\mathrm{cpu}}}{S_{\mathrm{cpu}}}\right\rfloor,
\left\lfloor\frac{H_{\mathrm{mem}}}{S_{\mathrm{mem}}}\right\rfloor,
\left\lfloor\frac{H_{\mathrm{disk}}}{S_{\mathrm{disk}}}\right\rfloor
\right).
\]

Track **reserved** and **actual** usage separately. Do not release capacity on TTL expiration alone: reaping must complete first. Do not derive CPU concurrency solely from average observed task usage.

## 5. MicroVM / Snapshot / Warm Pool review

Check against the exact Firecracker version and host CPU/kernel profile:

- KVM support and least-privilege Jailer configuration;
- fixed Guest Kernel/RootFS image digests and security update process;
- private vsock/management socket exposure;
- independent TAP/NetNS and Egress policy per workload;
- snapshot state version, CPU compatibility and disk file lifecycle;
- snapshot file authentication, encryption and tenant-aware retention;
- regenerated identities, entropy, tokens and machine-specific state after clone;
- tenant-specific writable layers and no stale browser/login state;
- cleanup and reaper idempotency under coordinator crash;
- warm-pool cold miss, eviction and cost accounting.

Snapshot restoration can have significant pitfalls: memory contains application state, while disks are separately managed; network/vsock sessions may not survive unchanged. Do not share tenant-containing warm snapshots across tenants.

## 6. Metrics and SLOs

Record, at minimum:

- `sandbox_acquire_seconds` (P50/P95/P99) split cold/warm/snapshot;
- `sandbox_ready_failures_total` by backend/image/host;
- `sandbox_running`, `sandbox_reserved_cpu_millis`, `sandbox_reserved_memory_bytes`;
- `sandbox_cpu_throttled_seconds_total`, `sandbox_memory_oom_total`, `sandbox_pids_limit_hits_total`;
- `sandbox_process_timeouts_total`, `sandbox_output_limit_hits_total`;
- `sandbox_reap_requested_total`, `sandbox_destroy_verified_total`;
- `sandbox_reap_latency_seconds` and `sandbox_orphans_total`;
- `sandbox_artifact_bytes` and `sandbox_snapshot_bytes`;
- `cost_per_independently_verified_task`.

Use Run ID + Sandbox ID + Tenant ID in authenticated internal trace data; redact credentials and content, avoid storing personally sensitive data in metrics label values.

## 7. Reliability failure windows

| Failure window | Do not assume | Required next step |
| --- | --- | --- |
| Create request timed out | No VM exists | Query by idempotency key / stable sandbox ID |
| Start accepted, worker crashed | No side effect occurred | Inspect VM and external result |
| TTL expired | Workload terminated | Request reap, confirm process/VM gone |
| VM stopped but mount persists | All data erased | Verify disk/mount deletion or quarantine |
| Snapshot restored | All sessions are valid/unique | Reinitialize identities and connections |
| Run result is "success" | User task passed | Run independent verifier |
| Control Plane restart | Worker leases still current | Reconcile durable leases with actual hosts |

## 8. Graduation criteria for the book implementation

The Chapter-18 Python code is an educational **resource-control experiment**. Before using any similar code with unknown input, implement or integrate:

1. a real isolation backend with explicit security configuration;
2. a host-side control plane with durable lease/identity/approval checks;
3. cgroup/VM-level CPU, memory, PID, disk and I/O limits;
4. deny-by-default network and no host credentials in guest;
5. crash-safe termination and resource reclamation;
6. an external verifier and complete security/reliability acceptance suite.

Further reading: [Firecracker Production Host Setup](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md), [Firecracker Snapshot Support](https://github.com/firecracker-microvm/firecracker/blob/main/docs/snapshotting/snapshot-support.md), [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html), [gVisor](https://gvisor.dev/docs/), [Rootless Docker](https://docs.docker.com/engine/security/rootless/).
