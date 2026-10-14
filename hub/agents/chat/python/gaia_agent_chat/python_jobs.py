# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run a Python job for ``run_python`` / ``execute_python_file`` and stop it cleanly.

A job that runs past its timeout, or that the agent stops waiting for, is
killed with every process it started: a surviving child keeps writing, and the
user is left with a half-written file that looks finished. What the job wrote
before it was stopped is reported, never deleted — those paths may be files
the user already had.
"""

from __future__ import annotations

import ast
import ctypes
import functools
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from gaia.agents.tools.command_timeouts import terminate_process_tree

logger = logging.getLogger(__name__)

#: Long enough for a pandas pass over a few hundred MB, and under the agent's
#: 180 s default tool limit, so a job that forgets ``timeout`` fails on its own
#: clock with its own message.
DEFAULT_PYTHON_TIMEOUT = 120

#: One job may hold the turn for half an hour, the ceiling GAIA already uses
#: for its longest single tool call (a full-mailbox triage). Longer work should
#: be split into steps that each save their output.
MAX_PYTHON_TIMEOUT = 1800

#: How often a running job is checked against the agent's stop signals.
_POLL_SECONDS = 0.25

#: How long a process the script left running may hold its output open.
_LEFTOVER_GRACE_SECONDS = 2.0

#: How long a killed job's pipes may take to close.
_KILL_GRACE_SECONDS = 5.0

_SCAN_SKIP_DIRS = frozenset(
    {
        ".git",
        ".gaia",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
    }
)
_SCAN_MAX_ENTRIES = 20000
_SCAN_MAX_SECONDS = 3.0
_REPORT_MAX_FILES = 20


def resolve_python_timeout(requested: Optional[int]) -> int:
    """Seconds a job may run; *requested* None means the default.

    Raises:
        ValueError: not a positive whole number, or above ``MAX_PYTHON_TIMEOUT``.
            Refused rather than clamped, so a job is never killed at a limit
            the caller did not ask for.
    """
    if requested is None:
        return DEFAULT_PYTHON_TIMEOUT
    try:
        seconds = int(requested)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"timeout must be a whole number of seconds, got {requested!r}. "
            f"Omit it for the {DEFAULT_PYTHON_TIMEOUT}s default."
        ) from exc
    if seconds <= 0:
        raise ValueError(
            f"timeout must be a positive number of seconds, got {seconds}. "
            f"Omit it for the {DEFAULT_PYTHON_TIMEOUT}s default."
        )
    if seconds > MAX_PYTHON_TIMEOUT:
        raise ValueError(
            f"timeout of {seconds}s exceeds the {MAX_PYTHON_TIMEOUT}s ceiling for "
            "one Python run. Split the job into steps that each save their "
            "output, or process the data in chunks."
        )
    return seconds


class PythonJob:
    """The outcome of :func:`run_python_job`."""

    def __init__(
        self,
        run: subprocess.CompletedProcess,
        stopped: Optional[str],
        written: List[str],
        *,
        written_complete: bool = True,
        left_running: bool = False,
    ):
        #: ``returncode``, ``stdout`` and ``stderr`` of the job.
        self.run = run
        #: ``"timeout"`` or ``"cancelled"`` if the job was killed, else None.
        self.stopped = stopped
        #: Files created or changed while the job ran, if it was killed.
        self.written = written
        #: False when the search for *written* hit its size or time bound.
        self.written_complete = written_complete
        #: The script exited, but processes it started still held its output.
        self.left_running = left_running


class _ProcessTree:
    """Every process a job starts, killable even after the job itself exits.

    Windows has no process groups, and ``taskkill /T`` cannot reach a child
    whose parent already exited, so there the job runs in a Job Object.
    """

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc
        self._job = _assign_job_object(proc.pid) if os.name == "nt" else None

    def kill(self) -> Tuple[str, str]:
        """Kill every process in the tree; return what the job printed."""
        if os.name == "nt" and self._job is None:
            return terminate_process_tree(self._proc)
        if os.name != "nt":
            try:
                # start_new_session made the job's pid its process group id.
                os.killpg(self._proc.pid, signal.SIGKILL)  # pylint: disable=no-member
            except ProcessLookupError:
                pass  # every process in the group has already exited
        elif not _kernel32().TerminateJobObject(self._job, 1):
            logger.warning(
                "TerminateJobObject failed for pid %s: WinError %s",
                self._proc.pid,
                ctypes.get_last_error(),
            )
        if self._proc.poll() is None:
            self._proc.kill()
        return _drain(self._proc)

    def close(self) -> None:
        if self._job is not None:
            _kernel32().CloseHandle(self._job)
            self._job = None


@functools.lru_cache(maxsize=1)
def _kernel32():
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _assign_job_object(pid: int):
    """A Job Object holding *pid* and every process it starts, or None.

    None is logged: the kill then uses ``taskkill /T``
    (``terminate_process_tree``), which misses a grandchild whose parent has
    already exited.
    """
    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        logger.warning(
            "CreateJobObject failed (WinError %s); a stopped Python job's "
            "orphaned children may keep running.",
            ctypes.get_last_error(),
        )
        return None
    access = 0x0100 | 0x0001  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    handle = kernel32.OpenProcess(access, False, pid)
    assigned = bool(handle) and bool(kernel32.AssignProcessToJobObject(job, handle))
    error = ctypes.get_last_error()
    if handle:
        kernel32.CloseHandle(handle)
    if not assigned:
        kernel32.CloseHandle(job)
        logger.warning(
            "Could not put pid %s in a Job Object (WinError %s); a stopped "
            "Python job's orphaned children may keep running.",
            pid,
            error,
        )
        return None
    return job


def _drain(proc: subprocess.Popen) -> Tuple[str, str]:
    """What the killed job printed; empty if its pipes will not close."""
    try:
        return proc.communicate(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Output pipes of pid %s stayed open after the kill; its output is lost.",
            proc.pid,
        )
        return "", ""


def run_python_job(
    cmd: Sequence[str],
    *,
    cwd: str,
    env: Optional[Dict[str, str]],
    timeout: float,
    should_stop: Callable[[], bool],
    watch: Iterable[os.PathLike | str] = (),
    ignore: Iterable[os.PathLike | str] = (),
) -> PythonJob:
    """Run *cmd*; kill its whole process tree at *timeout* or on *should_stop*.

    A process the script leaves running and holding its output is killed once
    the script exits, so the run ends with the script. *watch* are folders to
    search for files the job wrote, reported only when it is killed; paths in
    *ignore* (e.g. the job's own script) are left out.

    Raises:
        OSError: *cmd* could not be started.
    """
    started_wall = time.time()
    deadline = time.monotonic() + timeout
    proc = subprocess.Popen(  # pylint: disable=consider-using-with
        list(cmd),
        cwd=cwd,
        env=env,
        # An inherited stdin leaves the child waiting on a pipe nobody writes
        # to, and the run only ends at the timeout.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # The locale codec (cp1252 on Windows) raises on one emoji in a print().
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
    )
    tree = _ProcessTree(proc)
    stopped: Optional[str] = None
    left_running = False
    exited_at: Optional[float] = None
    try:
        while True:
            remaining = deadline - time.monotonic()
            try:
                out, err = proc.communicate(
                    timeout=max(min(remaining, _POLL_SECONDS), 0)
                )
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if proc.poll() is not None:
                    # The script is done; a process it started holds the pipes.
                    exited_at = exited_at or now
                    if now - exited_at < _LEFTOVER_GRACE_SECONDS:
                        continue
                    left_running = True
                elif should_stop():
                    stopped = "cancelled"
                elif now >= deadline:
                    stopped = "timeout"
                else:
                    continue
                out, err = tree.kill()
                break
    finally:
        tree.close()
    run = subprocess.CompletedProcess(list(cmd), proc.returncode, out or "", err or "")
    written: List[str] = []
    complete = True
    if stopped:
        written, complete = files_written_since(watch, started_wall, ignore)
    return PythonJob(
        run,
        stopped,
        written,
        written_complete=complete,
        left_running=left_running,
    )


def files_written_since(
    roots: Iterable[os.PathLike | str],
    since: float,
    ignore: Iterable[os.PathLike | str] = (),
) -> Tuple[List[str], bool]:
    """Files under *roots* modified at or after *since* (epoch seconds).

    Returns ``(paths, complete)``; *complete* is False when the search hit its
    entry, time or result bound. Roots are searched in the order given.
    """
    skip = {os.path.normcase(os.path.abspath(p)) for p in ignore}
    # One second of slack for filesystems that round modification times.
    cutoff = since - 1.0
    budget_end = time.monotonic() + _SCAN_MAX_SECONDS
    seen = 0
    found: List[str] = []
    walked: set = set()
    for root in roots:
        root = os.path.abspath(root)
        if too_wide_to_scan(root) or not os.path.isdir(root):
            continue
        for current, dirs, files in os.walk(root):
            key = os.path.normcase(current)
            if key in walked:
                dirs[:] = []
                continue
            walked.add(key)
            dirs[:] = [d for d in dirs if d not in _SCAN_SKIP_DIRS]
            for name in files:
                seen += 1
                if seen > _SCAN_MAX_ENTRIES or time.monotonic() > budget_end:
                    return sorted(found), False
                path = os.path.join(current, name)
                if os.path.normcase(path) in skip:
                    continue
                try:
                    if os.stat(path).st_mtime >= cutoff:
                        found.append(path)
                except OSError:
                    continue  # deleted or locked mid-walk
                if len(found) >= _REPORT_MAX_FILES:
                    return sorted(found), False
    return sorted(found), True


def too_wide_to_scan(folder: os.PathLike | str) -> bool:
    """A drive root or the home folder: too wide to say anything useful."""
    folder = os.path.normcase(os.path.abspath(folder))
    home = os.path.normcase(os.path.abspath(os.path.expanduser("~")))
    return os.path.dirname(folder) == folder or folder == home


def folders_named_in(source: str) -> List[str]:
    """Existing folders a script names by absolute path, for the written-files scan.

    A script run from a scratch folder usually saves its deliverable through an
    absolute path in a string literal; those folders are where to look.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    folders: List[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = node.value.strip()
        if not text or len(text) > 400 or "\n" in text:
            continue
        path = os.path.normpath(os.path.expanduser(text))
        if not os.path.isabs(path):
            continue
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if too_wide_to_scan(folder) or not os.path.isdir(folder):
            continue
        if folder not in folders:
            folders.append(folder)
    return folders


def watch_folders(*candidates: Optional[os.PathLike | str]) -> List[Path]:
    """Existing folders among *candidates*, de-duplicated, in order."""
    folders: List[Path] = []
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).resolve()
        if path.is_dir() and not too_wide_to_scan(path) and path not in folders:
            folders.append(path)
    return folders


def stopped_job_result(job: PythonJob, timeout: int) -> Dict[str, object]:
    """The tool result for a job that was killed before it finished."""
    if job.stopped == "timeout":
        error = (
            f"Timed out after {timeout}s; the script and every process it "
            "started were stopped. If the job legitimately needs longer, pass "
            f"a larger timeout (max {MAX_PYTHON_TIMEOUT}), or make it do less "
            "per run."
        )
    else:
        error = (
            "Stopped before it finished; the script and every process it "
            "started were stopped."
        )
    if job.written:
        error += (
            " These files changed while it ran, so they may be incomplete — "
            "check or regenerate them before calling them done: "
            + ", ".join(job.written)
        )
    if not job.written_complete:
        error += " (Not every folder was searched; others may have changed too.)"
    return {
        "status": "error",
        "error": error,
        "has_errors": True,
        "timed_out": job.stopped == "timeout",
        "files_modified": job.written,
        "files_modified_complete": job.written_complete,
        "stdout": job.run.stdout[-4000:],
        "stderr": job.run.stderr[-2000:],
    }


def finished_job_note(job: PythonJob) -> Optional[str]:
    """A note for a run that ended normally but left processes behind."""
    if not job.left_running:
        return None
    return (
        "The script exited but left processes running that held its output; "
        "they were stopped."
    )


def stop_requested(agent: object) -> bool:
    """True once the turn was stopped or the agent gave up on this tool call."""
    from gaia.tool_cancellation import tool_cancelled

    if tool_cancelled():
        return True
    event = getattr(agent, "_cancel_event", None)
    if event is not None and event.is_set():
        return True
    console_cancelled = getattr(agent, "_console_cancelled", None)
    return bool(console_cancelled and console_cancelled())
