"""Chapter 18: sandbox capacity admission and lease lifecycle model.

Logical reservations only; this DOES NOT create/terminate actual VMs, attach
cgroups or prevent a workload from running after its TTL. A trusted controller
must really destroy the VM/process, confirm cleanup, and only THEN release
resources. An expired reservation remains counted until confirmed destroyed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from time import monotonic
from typing import Callable


class AdmissionError(ValueError):
    """Resource or lifecycle admission failure."""


@dataclass(frozen=True)
class HostCapacity:
    cpu_millis: int
    memory_mb: int
    disk_mb: int

    def __post_init__(self) -> None:
        if any(type(v) is not int or v < 1
               for v in (self.cpu_millis, self.memory_mb, self.disk_mb)):
            raise AdmissionError("capacity_must_be_positive")


@dataclass(frozen=True)
class SandboxAllocation:
    cpu_millis: int
    guest_memory_mb: int
    runtime_overhead_mb: int
    workspace_disk_mb: int
    ttl_seconds: float

    def __post_init__(self) -> None:
        if any(type(v) is not int or v < 1
               for v in (self.cpu_millis, self.guest_memory_mb,
                         self.workspace_disk_mb)):
            raise AdmissionError("invalid_positive_allocation")
        if type(self.runtime_overhead_mb) is not int or self.runtime_overhead_mb < 0:
            raise AdmissionError("invalid_overhead")
        if (type(self.ttl_seconds) not in (int, float)
                or not math.isfinite(self.ttl_seconds)
                or self.ttl_seconds <= 0):
            raise AdmissionError("invalid_ttl")

    @property
    def total_memory_mb(self) -> int:
        return self.guest_memory_mb + self.runtime_overhead_mb


@dataclass(frozen=True)
class SandboxLease:
    run_id: str
    tenant_id: str
    allocation: SandboxAllocation
    expires_at: float
    state: str
    generation: int = 1


@dataclass(frozen=True)
class Usage:
    cpu_millis: int
    memory_mb: int
    disk_mb: int


class ReservationController:
    def __init__(self, capacity: HostCapacity, *,
                 clock: Callable[[], float] = monotonic) -> None:
        self.capacity = capacity
        self.clock = clock
        self._leases: dict[str, SandboxLease] = {}

    def get(self, run_id: str) -> SandboxLease:
        try:
            return self._leases[run_id]
        except KeyError as exc:
            raise AdmissionError("unknown_run") from exc

    def usage(self) -> Usage:
        active = [lease.allocation for lease in self._leases.values()
                  if lease.state != "destroyed"]
        return Usage(
            sum(x.cpu_millis for x in active),
            sum(x.total_memory_mb for x in active),
            sum(x.workspace_disk_mb for x in active),
        )

    def reserve(self, run_id: str, tenant_id: str,
                allocation: SandboxAllocation) -> SandboxLease:
        if (not isinstance(run_id, str) or not run_id.strip()
                or not isinstance(tenant_id, str) or not tenant_id.strip()):
            raise AdmissionError("invalid_run_or_tenant")
        if not isinstance(allocation, SandboxAllocation):
            raise AdmissionError("invalid_allocation")
        if run_id in self._leases:
            raise AdmissionError("run_id_already_used")
        u = self.usage()
        if (u.cpu_millis + allocation.cpu_millis > self.capacity.cpu_millis
                or u.memory_mb + allocation.total_memory_mb > self.capacity.memory_mb
                or u.disk_mb + allocation.workspace_disk_mb > self.capacity.disk_mb):
            raise AdmissionError("resource_capacity_exhausted")
        lease = SandboxLease(run_id, tenant_id, allocation,
                             self.clock() + allocation.ttl_seconds,
                             "reserved")
        self._leases[run_id] = lease
        return lease

    def mark_running(self, run_id: str) -> SandboxLease:
        lease = self.get(run_id)
        if lease.state != "reserved":
            raise AdmissionError("not_reserved")
        if self.clock() >= lease.expires_at:
            raise AdmissionError("lease_expired_before_start")
        lease = replace(lease, state="running")
        self._leases[run_id] = lease
        return lease

    def scan_expired(self) -> tuple[str, ...]:
        """Record cleanup REQUIRED, but do not release capacity prematurely."""
        expired = []
        for run_id, lease in list(self._leases.items()):
            if lease.state in {"reserved", "running"} and self.clock() >= lease.expires_at:
                self._leases[run_id] = replace(lease, state="reap_required")
                expired.append(run_id)
        return tuple(expired)

    def mark_reap_required(self, run_id: str) -> SandboxLease:
        lease = self.get(run_id)
        if lease.state in {"destroyed", "reap_required"}:
            raise AdmissionError("invalid_reap_transition")
        lease = replace(lease, state="reap_required")
        self._leases[run_id] = lease
        return lease

    def confirm_destroyed(self, run_id: str, *, cleanup_confirmed: bool) -> SandboxLease:
        """Trusted operator confirms underlying execution context is gone."""
        if cleanup_confirmed is not True:
            raise AdmissionError("cleanup_confirmation_required")
        lease = self.get(run_id)
        if lease.state != "reap_required":
            raise AdmissionError("reap_not_requested")
        lease = replace(lease, state="destroyed")
        self._leases[run_id] = lease
        return lease
