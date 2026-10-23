"""Chapter 18: bounded trusted-process executor, NOT an isolation sandbox.

Runs HOST-APPROVED Python fixtures in a disposable workspace. We validate
the host's script manifest, use no shell, scrub environment variables,
set POSIX rlimits in a fresh Python bootstrap, enforce a wall deadline and
bounded pipe output, kill the process group, then reclaim the work directory.

SECURITY: all processes still share the host kernel, namespaces, filesystem,
network, UID, and privileges. Python code can read host files, start network
connections, spawn detached children, etc. NEVER use this executor to run
untrusted LLM-generated code, third-party packages, or hostile tenants.
Use a properly configured VM, hardened container, or isolated runner instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
from time import monotonic
from typing import Mapping


class ProcessRunnerError(ValueError):
    """Safe error code for requests rejected before process execution."""


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class ProcessLimits:
    wall_seconds: float = 2.0
    cpu_seconds: int = 2
    memory_mb: int = 256
    max_file_bytes: int = 1_048_576
    max_output_bytes: int = 4096
    max_open_files: int = 64

    def __post_init__(self) -> None:
        if (type(self.wall_seconds) not in (float, int)
                or not math.isfinite(self.wall_seconds)
                or not 0.05 <= self.wall_seconds <= 60):
            raise ValueError("wall_seconds must be finite in [0.05, 60]")
        fields = (
            (self.cpu_seconds, 1, 60),
            (self.memory_mb, 96, 4096),
            (self.max_file_bytes, 1024, 8 * 1024 * 1024),
            (self.max_output_bytes, 128, 1024 * 1024),
            (self.max_open_files, 16, 1024),
        )
        if any(type(v) is not int or not lower <= v <= upper
               for v, lower, upper in fields):
            raise ValueError("Invalid positive integer resource limit")


@dataclass(frozen=True)
class ProcessRequest:
    job_id: str
    script_id: str
    args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not _IDENTIFIER.fullmatch(self.job_id):
            raise ProcessRunnerError("invalid_job_id")
        if not isinstance(self.script_id, str) or not _IDENTIFIER.fullmatch(self.script_id):
            raise ProcessRunnerError("invalid_script_id")
        if (not isinstance(self.args, tuple) or len(self.args) > 8
                or any(not isinstance(a, str) or len(a) > 256 or "\x00" in a
                       for a in self.args)):
            raise ProcessRunnerError("invalid_job_args")


@dataclass(frozen=True)
class ProcessResult:
    job_id: str
    status: str
    reason: str
    exit_code: int | None
    stdout: str
    stderr: str
    output_bytes: int
    elapsed_ms: float
    workspace_reclaimed: bool
    lifecycle: tuple[str, ...]
    execution_boundary: str = "host_process_rlimits_NOT_A_SANDBOX"


class TrustedScriptCatalog:
    """A trusted host owns the scripts; the model may only choose an ID.

    Script bytes are read ONCE at registration and then copied to each run's
    scratch directory, limiting accidental source changes during execution.
    This does not guarantee integrity against hostile same-UID processes.
    """

    def __init__(self, scripts: Mapping[str, str | Path], *,
                 max_script_bytes: int = 128 * 1024) -> None:
        if not scripts:
            raise ValueError("At least one trusted script required")
        self._contents: dict[str, bytes] = {}
        self._hashes: dict[str, str] = {}
        for script_id, raw_path in scripts.items():
            if not isinstance(script_id, str) or not _IDENTIFIER.fullmatch(script_id):
                raise ProcessRunnerError("invalid_script_id")
            path = Path(raw_path)
            if path.is_symlink() or not path.is_file() or path.suffix != ".py":
                raise ProcessRunnerError("trusted_script_must_be_regular_python_file")
            if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > max_script_bytes:
                raise ProcessRunnerError("trusted_script_size_or_type_rejected")
            content = path.read_bytes()
            if len(content) > max_script_bytes:
                raise ProcessRunnerError("trusted_script_size_rejected")
            self._contents[script_id] = content
            self._hashes[script_id] = sha256(content).hexdigest()

    def contents(self, script_id: str) -> bytes:
        try:
            return self._contents[script_id]
        except KeyError as exc:
            raise ProcessRunnerError("unknown_or_unapproved_script") from exc

    def digest(self, script_id: str) -> str:
        if script_id not in self._hashes:
            raise ProcessRunnerError("unknown_or_unapproved_script")
        return self._hashes[script_id]


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Best effort: same-session descendants only, not daemonized escapees."""
    if process.pid and sys.platform == "linux":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()


class TrustedProcessExecutor:
    """Synchronous host resource control for known fixture scripts.

    Do not call this 'sandbox'. Any untrusted code must go to a real isolated
    platform; even a script that exits successfully might have spawned an
    orphan in a different session or performed a host/network side effect.
    """

    def __init__(
        self, catalog: TrustedScriptCatalog, *,
        scratch_parent: str | Path | None = None,
    ) -> None:
        if sys.platform != "linux":
            raise ProcessRunnerError("linux_only_execution")
        self.catalog = catalog
        self.scratch_parent: Path | None = None
        if scratch_parent is not None:
            folder = Path(scratch_parent)
            if folder.is_symlink() or not folder.is_dir():
                raise ProcessRunnerError("invalid_scratch_parent")
            self.scratch_parent = folder.resolve()

    def run(self, request: ProcessRequest,
            limits: ProcessLimits | None = None) -> ProcessResult:
        limits = limits or ProcessLimits()
        if not isinstance(limits, ProcessLimits):
            raise ProcessRunnerError("invalid_limits")
        script_bytes = self.catalog.contents(request.script_id)
        events = ["created"]
        started = monotonic()
        status, reason = "failed", "unknown"
        output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        exit_code: int | None = None
        captured = 0
        pathname: Path | None = None
        parent = str(self.scratch_parent) if self.scratch_parent else None

        with tempfile.TemporaryDirectory(prefix="ch18-process-", dir=parent) as scratch:
            pathname = Path(scratch)
            events.append("allocated")
            script = pathname / "trusted_task.py"
            script.write_bytes(script_bytes)
            script.chmod(0o600)
            events.append("prepared")
            environment = {
                "PATH": os.defpath,
                "HOME": scratch,
                "TMPDIR": scratch,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "SANDBOX_JOB_ID": request.job_id,
            }
            entry = Path(__file__).with_name("_resource_child.py")
            command = [
                sys.executable, "-I", "-S", str(entry),
                str(limits.cpu_seconds),
                str(limits.memory_mb * 1024 * 1024),
                str(limits.max_file_bytes),
                str(limits.max_open_files),
                str(script), *request.args,
            ]
            try:
                child = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=scratch, env=environment,
                    close_fds=True, start_new_session=True,
                )
            except OSError as exc:
                raise ProcessRunnerError("process_launch_failed") from exc

            events.append("running")
            selector = selectors.DefaultSelector()
            deadline = monotonic() + limits.wall_seconds
            try:
                assert child.stdout is not None and child.stderr is not None
                for stream, name in ((child.stdout, "stdout"), (child.stderr, "stderr")):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, name)
                while selector.get_map() or child.poll() is None:
                    if monotonic() >= deadline:
                        status, reason = "timed_out", "wall_deadline_exceeded"
                        break
                    ready = selector.select(timeout=min(0.05, max(0.0, deadline - monotonic())))
                    for key, _ in ready:
                        stream, name = key.fileobj, key.data
                        try:
                            chunk = os.read(stream.fileno(), 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(stream)
                            continue
                        remaining = limits.max_output_bytes - captured
                        output[name].extend(chunk[:remaining])
                        captured += min(len(chunk), remaining)
                        if len(chunk) > remaining:
                            status, reason = "output_limited", "combined_output_limit_exceeded"
                            break
                    if status == "output_limited":
                        break
                if status not in ("timed_out", "output_limited"):
                    exit_code = child.poll()
                    status = "completed" if exit_code == 0 else "failed"
                    reason = "exit_zero" if exit_code == 0 else "nonzero_exit_or_signal"
            finally:
                # Always signal the original process group, including on
                # successful completion, to clean up known same-group orphans.
                _kill_process_group(child)
                try:
                    child.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=1.0)
                if exit_code is None:
                    exit_code = child.returncode
                selector.close()
                if child.stdout is not None:
                    child.stdout.close()
                if child.stderr is not None:
                    child.stderr.close()
            events.append(status)
        events.append("reclaimed")
        assert pathname is not None
        return ProcessResult(
            request.job_id, status, reason, exit_code,
            output["stdout"].decode("utf-8", errors="replace"),
            output["stderr"].decode("utf-8", errors="replace"),
            captured, round((monotonic() - started) * 1000, 2),
            not pathname.exists(), tuple(events),
        )
