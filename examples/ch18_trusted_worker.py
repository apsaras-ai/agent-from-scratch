"""Trusted, deterministic workload fixtures for Chapter 18.

DO NOT accept untrusted Python or LLM-generated modifications to this file.
All operations are intentionally local and harmless; this is NOT a sandbox.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import sys
import time


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"
    if mode == "report":
        print(json.dumps({
            "job": os.environ.get("SANDBOX_JOB_ID"),
            "home": os.environ.get("HOME"),
            "cwd": str(Path.cwd()),
            "private_token_present": "CH18_PRIVATE_TEST_TOKEN" in os.environ,
            "memory_address_limit": resource.getrlimit(resource.RLIMIT_AS)[0],
            "file_size_limit": resource.getrlimit(resource.RLIMIT_FSIZE)[0],
            "open_file_limit": resource.getrlimit(resource.RLIMIT_NOFILE)[0],
            "cpu_limit": resource.getrlimit(resource.RLIMIT_CPU)[0],
        }, sort_keys=True))
    elif mode == "echo":
        print(" ".join(sys.argv[2:]))
    elif mode == "write":
        Path("result.txt").write_text("trusted fixture generated artifact", encoding="utf-8")
        print(str(Path.cwd()))
    elif mode == "exit7":
        print("synthetic controlled failure", file=sys.stderr)
        raise SystemExit(7)
    elif mode == "sleep":
        time.sleep(5)
        print("unexpected completed sleep")
    elif mode == "spam":
        sys.stdout.write("x" * 100_000)
        sys.stdout.flush()
    elif mode == "bigfile":
        with open("bigfile.bin", "wb") as file:
            file.write(b"x" * 100_000)
    else:
        raise SystemExit("unknown test fixture mode")


if __name__ == "__main__":
    main()
