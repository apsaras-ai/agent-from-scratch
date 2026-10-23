"""Trusted POSIX rlimit bootstrap used by Chapter 18, not a sandbox.

This file runs in a new subprocess and never imports arbitrary projects.
Restrictions are set BEFORE execing the explicitly registered Python file.
"""
from __future__ import annotations

import os
import resource
import sys


def main() -> None:
    if len(sys.argv) < 6:
        raise SystemExit("invalid bootstrap arguments")
    cpu, address_bytes, fsize, nofiles = (int(v) for v in sys.argv[1:5])
    script = sys.argv[5]
    if min(cpu, address_bytes, fsize, nofiles) < 1:
        raise SystemExit("invalid resource limit")
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_AS, (address_bytes, address_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    resource.setrlimit(resource.RLIMIT_NOFILE, (nofiles, nofiles))
    # Executed source shares host namespaces, UID, mounts and network.
    os.execv(sys.executable, [sys.executable, "-I", "-S", script, *sys.argv[6:]])


if __name__ == "__main__":
    main()
