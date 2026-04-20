# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CLIToolsMixin — foreground/background subprocess execution.

Implements the four CLI tools from §15.2 of docs/plans/coder-agent.mdx plus
the static-denylist layer of the §6.8 guardrail stack. The denylist is
intentionally permissive for v1 (literal-substring match) — §15.8 will harden
it with argv-aware matching and a richer exception taxonomy.
"""

from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from typing import Dict, List, Optional, TypedDict

from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


class CLIResult(TypedDict):
    """Result of :meth:`run_cli_command`."""

    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    pid: Optional[int]


class StopResult(TypedDict):
    """Result of :meth:`stop_process`."""

    pid: int
    stopped: bool
    signal_sent: str


class ProcessInfo(TypedDict):
    """One row from :meth:`list_processes`."""

    pid: int
    command: str
    cwd: Optional[str]
    started_at: float
    running: bool


# ---------------------------------------------------------------------------
# Guardrail — static denylist (§6.8 layer 1)
# ---------------------------------------------------------------------------


class ShellDeniedError(Exception):
    """Raised when a command is blocked by the static denylist.

    Part of the ad-hoc exception taxonomy until §15.8 formalises it into a
    shared module. Matches by literal substring against the fully-rendered
    command string for v1; will be tightened in §15.8.
    """


_SHELL_DENYLIST: tuple = (
    "rm -rf /",
    "sudo",
    "chmod 777",
    "curl | bash",
    "git push origin main",
)


def _check_denylist(command: List[str]) -> None:
    """Raise ``ShellDeniedError`` if the rendered command trips the denylist."""
    rendered = " ".join(shlex.quote(part) for part in command)
    # Also compare the raw joined form so "sudo" blocks even if quoted.
    raw = " ".join(command)
    for banned in _SHELL_DENYLIST:
        if banned in rendered or banned in raw:
            raise ShellDeniedError(
                f"command blocked by static denylist ({banned!r}): {raw!r}"
            )


# ---------------------------------------------------------------------------
# Background-process registry
# ---------------------------------------------------------------------------


class _BackgroundEntry(TypedDict):
    process: subprocess.Popen
    command: str
    cwd: Optional[str]
    started_at: float
    stdout_buf: List[str]
    stderr_buf: List[str]
    reader_threads: List[threading.Thread]


_PROCESS_REGISTRY: Dict[int, _BackgroundEntry] = {}
_REGISTRY_LOCK = threading.Lock()


def _spawn_reader(
    stream,
    buf: List[str],
    *,
    max_lines: int = 10_000,
) -> threading.Thread:
    """Drain ``stream`` line-by-line into ``buf`` (capped at ``max_lines``)."""

    def _run() -> None:
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                buf.append(line)
                if len(buf) > max_lines:
                    # Trim from the head so ``tail_lines`` stays meaningful.
                    del buf[: len(buf) - max_lines]
        except ValueError:
            # Stream closed mid-read.
            pass
        finally:
            try:
                stream.close()
            except OSError as close_err:
                # Torn-down pipe on subprocess termination — real but not
                # recoverable here. Log with context per CLAUDE.md fail-loudly
                # rule; do not silently swallow.
                logger.debug("reader thread: stream close failed (%s)", close_err)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class CLIToolsMixin:
    """Mixin providing the four CLI tools in §15.2 of the coder plan."""

    def register_cli_tools(self) -> None:
        """Register ``run_cli_command`` / ``stop_process`` / ``list_processes`` /
        ``get_process_logs``."""

        @tool
        def run_cli_command(
            command: List[str],
            cwd: Optional[str] = None,
            timeout_s: int = 120,
            background: bool = False,
            env: Optional[Dict[str, str]] = None,
        ) -> CLIResult:
            """Run ``command`` synchronously (default) or in the background.

            Raises:
                ShellDeniedError: if the command matches ``_SHELL_DENYLIST``.
                subprocess.TimeoutExpired: if a foreground command exceeds
                    ``timeout_s``.
            """
            if not command:
                raise ValueError("run_cli_command: command must be non-empty")
            _check_denylist(command)

            merged_env = os.environ.copy()
            if env:
                merged_env.update(env)

            if background:
                proc = subprocess.Popen(  # pylint: disable=consider-using-with
                    command,
                    cwd=cwd,
                    env=merged_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                )
                stdout_buf: List[str] = []
                stderr_buf: List[str] = []
                readers = [
                    _spawn_reader(proc.stdout, stdout_buf),
                    _spawn_reader(proc.stderr, stderr_buf),
                ]
                with _REGISTRY_LOCK:
                    _PROCESS_REGISTRY[proc.pid] = {
                        "process": proc,
                        "command": " ".join(command),
                        "cwd": cwd,
                        "started_at": time.time(),
                        "stdout_buf": stdout_buf,
                        "stderr_buf": stderr_buf,
                        "reader_threads": readers,
                    }
                return {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "duration_ms": 0,
                    "pid": proc.pid,
                }

            start = time.monotonic()
            completed = subprocess.run(  # pylint: disable=subprocess-run-check
                command,
                cwd=cwd,
                env=merged_env,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "duration_ms": duration_ms,
                "pid": None,
            }

        @tool
        def stop_process(pid: int, force: bool = False) -> StopResult:
            """Stop a tracked background process.

            ``force=False`` sends SIGINT first; if still running after 10s,
            escalates to SIGTERM. ``force=True`` sends SIGKILL directly.

            Raises:
                ValueError: if ``pid`` is not in ``_PROCESS_REGISTRY``.
            """
            with _REGISTRY_LOCK:
                entry = _PROCESS_REGISTRY.get(pid)
            if entry is None:
                raise ValueError(f"stop_process: unknown pid {pid}")
            proc = entry["process"]
            sig_name: str
            if force:
                proc.send_signal(signal.SIGKILL)
                sig_name = "SIGKILL"
            else:
                proc.send_signal(signal.SIGINT)
                sig_name = "SIGINT"
                # Give the process 10s to respond to SIGINT; then SIGTERM.
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.send_signal(signal.SIGTERM)
                    sig_name = "SIGTERM"
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Last-resort kill so we never leak children.
                proc.kill()
                proc.wait(timeout=5)
            with _REGISTRY_LOCK:
                _PROCESS_REGISTRY.pop(pid, None)
            return {"pid": pid, "stopped": True, "signal_sent": sig_name}

        @tool
        def list_processes() -> List[ProcessInfo]:
            """Return a snapshot of tracked background processes."""
            now_running: List[ProcessInfo] = []
            with _REGISTRY_LOCK:
                entries = list(_PROCESS_REGISTRY.items())
            for pid, entry in entries:
                proc = entry["process"]
                now_running.append(
                    {
                        "pid": pid,
                        "command": entry["command"],
                        "cwd": entry["cwd"],
                        "started_at": entry["started_at"],
                        "running": proc.poll() is None,
                    }
                )
            return now_running

        @tool
        def get_process_logs(pid: int, tail_lines: int = 100) -> str:
            """Return the last ``tail_lines`` of captured stdout+stderr for ``pid``.

            Raises:
                ValueError: if ``pid`` is not in ``_PROCESS_REGISTRY``.
            """
            with _REGISTRY_LOCK:
                entry = _PROCESS_REGISTRY.get(pid)
            if entry is None:
                raise ValueError(f"get_process_logs: unknown pid {pid}")
            # Interleave the two buffers in arrival order is non-trivial without
            # timestamps; for v1 we concatenate stdout then stderr, preserving
            # line order within each. §15.8 may introduce timestamped capture.
            combined = entry["stdout_buf"] + entry["stderr_buf"]
            tail = combined[-tail_lines:]
            return "".join(tail)
