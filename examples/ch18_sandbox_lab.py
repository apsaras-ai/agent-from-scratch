"""Chapter 18: trusted host-process execution, reservation and diagnostics.

This is deliberately NOT a secure Agent sandbox. Runs only our preapproved
offline fixture script. No external VM/container/LLM or paid service needed.
"""
from __future__ import annotations

import json
from pathlib import Path

from mini_agent.process_executor import (
    ProcessLimits, ProcessRequest, TrustedProcessExecutor, TrustedScriptCatalog,
)
from mini_agent.sandbox_diagnostics import cgroup_v2_summary, namespace_summary
from mini_agent.sandbox_reservations import (
    HostCapacity, ReservationController, SandboxAllocation,
)


def main() -> None:
    trusted_script = Path(__file__).with_name("ch18_trusted_worker.py")
    catalog = TrustedScriptCatalog({"fixture": trusted_script})
    runner = TrustedProcessExecutor(catalog)
    print("Manifest SHA-256:", catalog.digest("fixture")[:16], "(trusted fixture)")
    limits = ProcessLimits(
        wall_seconds=1.0, cpu_seconds=2, memory_mb=256,
        max_output_bytes=2048,
    )
    success = runner.run(ProcessRequest("job-success", "fixture", ("report",)), limits)
    print("Normal process:", success.status, success.reason)
    print("Environment and RLIMITs:", json.loads(success.stdout))
    print("Temporary workspace reclaimed:", success.workspace_reclaimed)

    timed = runner.run(ProcessRequest("job-timeout", "fixture", ("sleep",)),
                       ProcessLimits(wall_seconds=.15))
    print("Timeout:", timed.status, timed.reason)
    print("Timeout workspace reclaimed:", timed.workspace_reclaimed)

    spam = runner.run(ProcessRequest("job-output", "fixture", ("spam",)),
                       ProcessLimits(max_output_bytes=512))
    print("Output flood:", spam.status, "captured_bytes=", spam.output_bytes)

    clock = [0.0]
    admission = ReservationController(
        HostCapacity(cpu_millis=2000, memory_mb=2048, disk_mb=2048),
        clock=lambda: clock[0],
    )
    allocation = SandboxAllocation(
        cpu_millis=1000, guest_memory_mb=512,
        runtime_overhead_mb=128, workspace_disk_mb=512, ttl_seconds=20,
    )
    admission.reserve("lease-1", "demo-tenant", allocation)
    admission.mark_running("lease-1")
    clock[0] = 21
    print("Expired reservations:", admission.scan_expired())
    print("Resources still reserved after expiry:", admission.usage().memory_mb)
    admission.confirm_destroyed("lease-1", cleanup_confirmed=True)
    print("Resources reserved after trusted cleanup ACK:", admission.usage().memory_mb)

    names = namespace_summary()
    controls = cgroup_v2_summary()
    print("Namespace entries visible:", sum(v != "unavailable" for v in names.values()))
    print("cgroup memory.max (read-only):", controls["memory.max"])
    print("No namespace, cgroup or microVM isolation was created by this demo.")


if __name__ == "__main__":
    main()
