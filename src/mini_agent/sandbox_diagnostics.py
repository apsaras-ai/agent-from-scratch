"""Chapter 18 read-only host namespace and cgroup diagnostics.

Only reports visible paths and optional cgroup limits. Observing a namespace
ID or a configured cgroup does NOT prove isolation from malicious workloads.
No mounts, namespace creation, or cgroup configuration are performed.
"""
from __future__ import annotations

from pathlib import Path


def namespace_summary(proc_root: str | Path = "/proc/self/ns") -> dict[str, str]:
    root = Path(proc_root)
    names = ("user", "pid", "mnt", "net", "ipc", "uts", "cgroup")
    result: dict[str, str] = {}
    for name in names:
        path = root / name
        try:
            result[name] = path.readlink().as_posix()
        except (OSError, ValueError):
            result[name] = "unavailable"
    return result


def cgroup_v2_summary(cgroup_root: str | Path = "/sys/fs/cgroup") -> dict[str, str]:
    root = Path(cgroup_root)
    result: dict[str, str] = {}
    for name in (
        "cgroup.controllers", "cpu.max", "memory.max", "memory.current",
        "pids.max", "pids.current",
    ):
        path = root / name
        try:
            result[name] = path.read_text(encoding="utf-8").strip()[:200]
        except OSError:
            result[name] = "unavailable"
    return result
