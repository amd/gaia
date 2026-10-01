# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Background processes: commands that keep running while the agent works.

The runtime behind ``start_process`` and its companion tools. A command line
that has already cleared every ``run_shell_command`` check
(:meth:`ShellToolsMixin._prepare_shell_line`) is launched here and supervised
on its own thread; the agent returns at once and checks on it later.

Three guarantees, each with its own mechanism:

* **Output is bounded.** Everything the process prints is spilled to a log
  file that rotates at half its cap, so a chatty server keeps its newest
  output and never fills the disk (:class:`OutputLog`).
* **Stop reaches the whole tree.** Windows puts every process in a job object;
  POSIX gives each its own process group. A stop kills descendants the shell
  spawned, not just the shell.
* **Nothing outlives the agent.** A clean exit stops every process
  (``atexit`` and the agent's own teardown). A hard death is covered by the
  OS: the job object is created kill-on-close, so Windows kills its members
  when the agent's handle dies; on POSIX a guardian process watches a pipe
  from the agent and kills the registered groups when that pipe closes.
"""

from __future__ import annotations

import atexit
import itertools
import os
import shutil
import signal
import subprocess
import threading
import time
import weakref
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

#: Most processes one agent may have running at once.
MAX_RUNNING = 8
#: Finished processes kept for inspection; the oldest is dropped past this.
MAX_RETAINED = 32
#: Disk kept per process. The log rotates at half, so between half and all of
#: this is the newest output.
LOG_CAP_BYTES = 4 * 1024 * 1024
#: How long a stop waits for SIGTERM before SIGKILL (POSIX).
STOP_GRACE_SECONDS = 3.0
#: Supervisor poll slice: how late a stop or an exit is noticed.
_POLL_SECONDS = 0.1
#: How long a finished line waits for its pipe reader to drain.
_DRAIN_SECONDS = 2.0

_COMMAND_NOT_FOUND = 127

#: Process states.
RUNNING = "running"
EXITED = "exited"
STOPPED = "stopped"
FAILED = "failed"


class ProcessStartError(RuntimeError):
    """A process could not be launched, or could not be placed under control."""


# ---------------------------------------------------------------------------
# Bounded output
# ---------------------------------------------------------------------------


class OutputLog:
    """A process's combined output on disk, capped at ``cap_bytes``.

    Offsets are absolute: byte ``n`` is the ``n``-th byte the process ever
    printed, so a reader's position stays meaningful across rotations. Bytes
    older than :attr:`earliest_offset` were discarded to stay under the cap.
    """

    def __init__(self, path: Path, cap_bytes: int = LOG_CAP_BYTES):
        self.path = Path(path)
        self._previous = self.path.with_name(self.path.name + ".1")
        self._half = max(cap_bytes // 2, 1)
        self._lock = threading.Lock()
        self._fh = open(self.path, "wb")  # pylint: disable=consider-using-with
        self._current_size = 0
        self._previous_size = 0
        self._dropped = 0
        #: Bytes thrown away, not kept, because the log could not rotate.
        self.discarded_bytes = 0
        #: Why the log stopped rotating, for the status the model reads.
        self.problem = ""

    @property
    def earliest_offset(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._dropped + self._previous_size + self._current_size

    def write(self, data: bytes) -> None:
        with self._lock:
            if self._fh is None:
                return
            if self._current_size >= self._half:
                self._rotate()
            if self._current_size >= 2 * self._half:
                # Could not rotate and already at the cap: keep the cap.
                self.discarded_bytes += len(data)
                return
            self._fh.write(data)
            self._fh.flush()
            self._current_size += len(data)

    def _rotate(self) -> None:
        self._fh.close()
        try:
            os.replace(self.path, self._previous)
        except OSError as exc:
            # Windows refuses while anything else holds the file open (a
            # reader, an antivirus scan). Keep appending; retry next write.
            self._fh = open(self.path, "ab")  # pylint: disable=consider-using-with
            if not self.problem:
                logger.warning("Could not rotate %s: %s", self.path, exc)
            self.problem = (
                f"The log could not rotate ({exc}); output past "
                f"{2 * self._half} bytes is being discarded until it can."
            )
            return
        self._dropped += self._previous_size
        self._previous_size = self._current_size
        self._current_size = 0
        self._fh = open(self.path, "wb")  # pylint: disable=consider-using-with
        self.problem = ""

    def read(self, offset: int, limit: int) -> bytes:
        """Up to *limit* bytes from absolute *offset* (clamped to what is kept)."""
        with self._lock:
            start = self._dropped
            end = start + self._previous_size + self._current_size
            offset = min(max(offset, start), end)
            want = min(limit, end - offset)
            chunks = []
            relative = offset - start
            for path, size in (
                (self._previous, self._previous_size),
                (self.path, self._current_size),
            ):
                if want <= 0:
                    break
                if relative >= size:
                    relative -= size
                    continue
                with open(path, "rb") as handle:
                    handle.seek(relative)
                    chunk = handle.read(min(want, size - relative))
                chunks.append(chunk)
                want -= len(chunk)
                relative = 0
            return b"".join(chunks)

    def close(self, delete: bool = False) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
            if delete:
                for path in (self.path, self._previous):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass


def complete_utf8(data: bytes) -> bytes:
    """*data* without a trailing, partially-read UTF-8 character."""
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte < 0x80:
            return data
        if byte >= 0xC0:
            need = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
            return data if back >= need else data[:-back]
    return data


def terminal_view(text: str) -> str:
    """*text* as a terminal would show it: a ``\\r`` progress bar keeps its last frame."""
    lines = []
    for line in text.split("\n"):
        stripped = line.rstrip("\r")
        lines.append(stripped.rsplit("\r", 1)[-1])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Process-tree containment
# ---------------------------------------------------------------------------


class _WindowsJob:
    """A kill-on-close job object holding one background process's tree.

    Every process is created suspended, placed in the job, then resumed, so a
    child cannot spawn a grandchild outside the job first. Closing the job's
    last handle — including the OS doing it when the agent dies — kills every
    member, which is what makes orphan cleanup survive a hard kill.
    """

    _CREATE_SUSPENDED = 0x00000004
    _CREATE_NEW_PROCESS_GROUP = 0x00000200
    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class _BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        self._kernel32 = kernel32
        self._ntdll = ntdll

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ProcessStartError(
                f"Could not create a Windows job object to contain the process "
                f"(error {ctypes.get_last_error()}), so it was not started."
            )
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ProcessStartError(
                f"Could not make the job object kill its processes on close "
                f"(error {error}), so the process was not started."
            )
        self._handle = handle

    #: A Ctrl+C at the agent's console must not reach background work on
    #: Windows any more than it does on POSIX, where each has its own session.
    creationflags = _CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP

    def adopt(self, process: subprocess.Popen) -> None:
        """Put a suspended *process* in the job, then let it run."""
        if not self._kernel32.AssignProcessToJobObject(
            self._handle, int(process._handle)  # pylint: disable=protected-access
        ):
            error = self._ctypes.get_last_error()
            process.kill()
            process.wait()
            raise ProcessStartError(
                f"Could not place pid {process.pid} in its job object (error "
                f"{error}); it was killed before running, because a process "
                "outside the job could outlive the agent."
            )
        status = self._ntdll.NtResumeProcess(
            int(process._handle)  # pylint: disable=protected-access
        )
        if status != 0:
            self.terminate()
            raise ProcessStartError(
                f"Could not resume pid {process.pid} after creating it "
                f"(NTSTATUS {status & 0xFFFFFFFF:#x}); it was killed."
            )

    def terminate(self) -> Optional[str]:
        """Kill every member; the reason it could not, or None."""
        if self._handle and not self._kernel32.TerminateJobObject(self._handle, 1):
            return (
                f"TerminateJobObject failed (error {self._ctypes.get_last_error()}); "
                "its processes die when the session closes."
            )
        return None

    def close(self) -> None:
        """Release the job; kill-on-close takes any member still running."""
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


#: Run by ``/bin/sh``, not Python: the flagship ships as a frozen binary,
#: where ``sys.executable -c`` does not exist. It outlives a dead agent by
#: design, so it depends on nothing that agent had.
_GUARDIAN_SCRIPT = r"""
trap '' INT HUP TERM
groups=""
while IFS= read -r line; do
  op=${line%% *}
  g=${line#* }
  case "$g" in ''|*[!0-9]*) continue ;; esac
  if [ "$op" = "+" ]; then
    groups="$groups $g"
  elif [ "$op" = "-" ]; then
    kept=""
    for x in $groups; do [ "$x" = "$g" ] || kept="$kept $x"; done
    groups=$kept
  fi
done
for g in $groups; do kill -9 "-$g" 2>/dev/null; done
"""

#: How often registered groups are checked for emptiness, so a group whose
#: last member exited is unregistered before its id can be reused.
_PRUNE_SECONDS = 5.0


class _PosixGuardian:
    """One watcher process per agent process, killing its groups if the agent dies.

    It reads ``+pgid`` / ``-pgid`` lines on a pipe only this process holds the
    write end of. When that pipe closes — a clean exit, SIGTERM, SIGKILL, OOM —
    it SIGKILLs every group still registered. A pgid cannot be handed to a new
    group while ours still has members, so only an emptied group could be
    reused; groups are unregistered once they are seen empty.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._groups: set = set()
        self._pruner: Optional[threading.Thread] = None

    def _prune_forever(self) -> None:
        while True:
            time.sleep(_PRUNE_SECONDS)
            with self._lock:
                empty = [g for g in self._groups if not _group_alive(g)]
            for pgid in empty:
                self.unregister(pgid)

    def _ensure(self) -> subprocess.Popen:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        if self._proc is not None:
            logger.warning(
                "Background-process guardian exited (code %s); starting a new "
                "one and re-registering %d group(s).",
                self._proc.returncode,
                len(self._groups),
            )
        self._proc = subprocess.Popen(  # pylint: disable=consider-using-with
            ["/bin/sh", "-c", _GUARDIAN_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        for pgid in self._groups:
            self._send(f"+ {pgid}")
        if self._pruner is None:
            self._pruner = threading.Thread(
                target=self._prune_forever, name="gaia-bg-guardian-prune", daemon=True
            )
            self._pruner.start()
        return self._proc

    def _send(self, line: str) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write((line + "\n").encode())
        self._proc.stdin.flush()

    def register(self, pgid: int) -> None:
        with self._lock:
            self._groups.add(pgid)
            try:
                self._ensure()
                self._send(f"+ {pgid}")
            except (OSError, ValueError) as exc:
                self._groups.discard(pgid)
                raise ProcessStartError(
                    f"Could not register process group {pgid} with the "
                    f"background-process guardian ({exc}), so nothing would "
                    "stop it if the agent died."
                ) from exc

    def unregister(self, pgid: int) -> None:
        with self._lock:
            if pgid not in self._groups:
                return
            self._groups.discard(pgid)
            if self._proc is None or self._proc.poll() is not None:
                return
            try:
                self._send(f"- {pgid}")
            except (OSError, ValueError) as exc:
                logger.warning("Could not unregister group %s: %s", pgid, exc)


_GUARDIAN = _PosixGuardian()

#: POSIX only; Windows has no SIGKILL, and its job object does the killing.
_SIGKILL = getattr(signal, "SIGKILL", None)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


# ---------------------------------------------------------------------------
# One background process
# ---------------------------------------------------------------------------


def _spawn(
    step, cwd: str, out_fd: int, adopt, granted: frozenset, bypass: bool
) -> List[subprocess.Popen]:
    """Launch one validated step with its output on *out_fd*; *adopt* each process.

    How the step runs — argv or shell, one process or a chain — is
    ``_launch_for``'s decision, the same one ``run_shell_command`` follows, so
    a skill-granted CLI is never handed to a shell here either. stdin is
    DEVNULL for the reason it is there: nobody is at the other end.
    """
    from gaia.agents.tools.shell_tools import (
        _captured_stderr,
        _launch_for,
        _segment_env,
    )

    launch = _launch_for(step, granted, bypass)
    posix = os.name != "nt"
    flags = 0 if posix else _WindowsJob.creationflags
    envs = step.envs or ()
    modes = step.stderr_modes or ()
    if not launch.chained:
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            launch.argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=out_fd,
            stderr=_captured_stderr(launch.stderr_mode, out_fd),
            env=_segment_env(envs[0] if envs else {}),
            shell=launch.shell,  # nosec B602 - same validated launch as run_shell_command
            start_new_session=posix,
            creationflags=flags,
        )
        adopt(process)
        return [process]

    procs: List[subprocess.Popen] = []
    upstream = None
    try:
        for index, argv in enumerate(step.segments):
            last = index == len(step.segments) - 1
            process = subprocess.Popen(  # pylint: disable=consider-using-with
                argv,
                cwd=cwd,
                stdin=subprocess.DEVNULL if upstream is None else upstream,
                stdout=out_fd if last else subprocess.PIPE,
                stderr=_captured_stderr(
                    modes[index] if index < len(modes) else "", out_fd
                ),
                env=_segment_env(envs[index] if index < len(envs) else {}),
                start_new_session=posix,
                creationflags=flags,
            )
            if upstream is not None:
                # Only the child may hold the read end, or an early-exiting
                # reader never delivers SIGPIPE to the writer.
                upstream.close()
            procs.append(process)
            adopt(process)
            upstream = None if last else process.stdout
    except BaseException:
        for process in procs:
            if process.poll() is None:
                process.kill()
                process.wait()
        raise
    return procs


@dataclass
class StepRecord:
    command: str
    return_code: Optional[int] = None


class BackgroundProcess:
    """One command line running (or finished) in the background.

    Built from a cleared ``ShellLinePlan``; :meth:`start` launches the line's
    steps in order on a supervisor thread, honouring ``&&``/``||``/``;``
    exactly as ``run_shell_command`` would.
    """

    def __init__(
        self, process_id: str, name: str, command: str, plan: Any, log: OutputLog
    ):
        self.process_id = process_id
        self.name = name
        self.command = command
        self.plan = plan
        self.log = log
        self.started_at = time.time()
        self.started_mono = time.monotonic()
        self.ended_mono: Optional[float] = None
        self.state = RUNNING
        self.exit_code: Optional[int] = None
        self.has_errors = False
        self.error = ""
        self.spawned = False
        self.steps: List[StepRecord] = []
        self.pid: Optional[int] = None
        #: Absolute log offset the model has seen up to.
        self.seen_offset = 0
        #: The model has been told this process finished.
        self.end_reported = False
        #: Why a stop could not kill the tree, if it could not.
        self.stop_error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._finished = threading.Event()
        self._job = _WindowsJob() if os.name == "nt" else None
        self._groups: List[int] = []
        self._procs: List[subprocess.Popen] = []
        self._pumps: List[threading.Thread] = []
        self._thread = threading.Thread(
            target=self._supervise,
            name=f"gaia-bg-{self.process_id}",
            daemon=True,
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    @property
    def finished(self) -> bool:
        return self._finished.is_set()

    def wait(self, timeout: float) -> bool:
        return self._finished.wait(timeout)

    @property
    def runtime_seconds(self) -> float:
        end = self.ended_mono if self.ended_mono is not None else time.monotonic()
        return round(end - self.started_mono, 1)

    @property
    def segments(self) -> list:
        return [seg for step in self.plan.steps for seg in step.segments]

    def _supervise(self) -> None:
        from gaia.agents.tools.shell_tools import _connector_runs

        last_code = 0
        unhandled = False
        try:
            for step, cwd in zip(self.plan.steps, self.plan.step_cwds):
                if self._stop.is_set():
                    break
                if not _connector_runs(step.connector, last_code):
                    continue
                if step.connector == "||":
                    unhandled = False
                if step.is_cd:
                    last_code = 0
                    self.steps.append(StepRecord(step.text, 0))
                    continue
                try:
                    code = self._run_step(step, cwd)
                except FileNotFoundError as exc:
                    self.log.write(
                        f"{step.text}: {exc.strerror or exc}\n".encode("utf-8")
                    )
                    code = _COMMAND_NOT_FOUND
                if code is None:
                    break
                self.steps.append(StepRecord(step.text, code))
                last_code = code
                unhandled = unhandled or code != 0
            with self._lock:
                if self._stop.is_set():
                    self.state = STOPPED
                else:
                    self.state = EXITED
                    self.exit_code = last_code
                self.has_errors = self.state == STOPPED or last_code != 0 or unhandled
        except Exception as exc:  # noqa: BLE001 — reported as the process's state
            logger.exception("Background process %s failed", self.process_id)
            with self._lock:
                self.state = FAILED
                self.has_errors = True
                self.error = f"{type(exc).__name__}: {exc}"
            self.log.write(f"\n[gaia] {self.error}\n".encode("utf-8"))
            self.begin_stop()
        finally:
            self.ended_mono = time.monotonic()
            self._finished.set()

    def _run_step(self, step, cwd: str) -> Optional[int]:
        """Launch *step*, wait for it, and return its pipefail code (None if stopped)."""
        read_fd, write_fd = os.pipe()
        try:
            with self._lock:
                if self._stop.is_set():
                    os.close(read_fd)
                    return None
                procs = _spawn(
                    step,
                    cwd,
                    write_fd,
                    self._adopt,
                    self.plan.granted,
                    self.plan.bypass,
                )
                self._procs = procs
                self.spawned = True
                if self.pid is None:
                    self.pid = procs[0].pid
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        pump = threading.Thread(
            target=self._pump,
            args=(read_fd,),
            name=f"gaia-bg-{self.process_id}-out",
            daemon=True,
        )
        pump.start()
        self._pumps.append(pump)
        while any(proc.poll() is None for proc in procs):
            if self._stop.wait(_POLL_SECONDS):
                return None
        pump.join(_DRAIN_SECONDS)
        self._release_empty_groups()
        from gaia.agents.tools.shell_tools import _pipefail_code

        return _pipefail_code([proc.returncode for proc in procs])

    def _adopt(self, process: subprocess.Popen) -> None:
        """Put a freshly spawned process under this background process's control."""
        if self._job is not None:
            self._job.adopt(process)
            return
        try:
            _GUARDIAN.register(process.pid)
        except ProcessStartError:
            _signal_group(process.pid, _SIGKILL)
            process.wait()
            raise
        self._groups.append(process.pid)

    def _pump(self, read_fd: int) -> None:
        """Drain the pipe to EOF whatever the log does, so the child never blocks on it."""
        try:
            while True:
                try:
                    data = os.read(read_fd, 65536)
                except OSError as exc:
                    logger.warning(
                        "Output reader for %s ended: %s", self.process_id, exc
                    )
                    break
                if not data:
                    break
                try:
                    self.log.write(data)
                except (OSError, ValueError) as exc:
                    self.log.discarded_bytes += len(data)
                    self.log.problem = f"Output could not be written to the log: {exc}"
        finally:
            os.close(read_fd)

    def _release_empty_groups(self) -> None:
        for pgid in list(self._groups):
            if not _group_alive(pgid):
                _GUARDIAN.unregister(pgid)
                self._groups.remove(pgid)

    def begin_stop(self) -> None:
        """Ask the whole tree to stop, without waiting; :meth:`finish_stop` waits."""
        with self._lock:
            self._stop.set()
        if self._job is not None:
            self.stop_error = self._job.terminate() or ""
            return
        self._release_empty_groups()
        for pgid in self._groups:
            _signal_group(pgid, signal.SIGTERM)

    def finish_stop(self) -> None:
        """Wait out the grace period, SIGKILL what is left, and join the threads."""
        if self._job is None:
            groups = list(self._groups)
            deadline = time.monotonic() + STOP_GRACE_SECONDS
            while time.monotonic() < deadline:
                # Reap our own children: a zombie leader still counts as a live group.
                for proc in self._procs:
                    proc.poll()
                if not any(_group_alive(g) for g in groups):
                    break
                time.sleep(_POLL_SECONDS)
            for pgid in groups:
                _signal_group(pgid, _SIGKILL)
        self._thread.join(STOP_GRACE_SECONDS + 5)
        for proc in self._procs:
            if proc.poll() is None:
                proc.kill()
        for pump in self._pumps:
            pump.join(_DRAIN_SECONDS)
        if self._thread.is_alive():
            logger.warning(
                "Background process %s did not finish after its stop.",
                self.process_id,
            )

    def stop(self) -> None:
        """Kill the whole tree, including anything a finished line left behind."""
        self.begin_stop()
        self.finish_stop()

    def dispose(self, delete_log: bool, stopped: bool = False) -> None:
        if not stopped:
            self.stop()
        for pgid in self._groups:
            _GUARDIAN.unregister(pgid)
        self._groups.clear()
        if self._job is not None:
            self._job.close()
        self.log.close(delete=delete_log)


# ---------------------------------------------------------------------------
# One agent's processes
# ---------------------------------------------------------------------------

_LIVE_TABLES: "weakref.WeakSet[ProcessTable]" = weakref.WeakSet()


def _stop_everything_at_exit() -> None:
    for table in list(_LIVE_TABLES):
        table.close()


atexit.register(_stop_everything_at_exit)


class ProcessLimitError(RuntimeError):
    """Starting another process would exceed :data:`MAX_RUNNING`."""


class ProcessTable:
    """The background processes one agent started, across all its turns."""

    def __init__(self, log_dir: Path, owns_log_dir: bool):
        self.log_dir = Path(log_dir)
        self._owns_log_dir = owns_log_dir
        self._lock = threading.Lock()
        self._processes: Dict[str, BackgroundProcess] = {}
        self._ids = itertools.count(1)
        self._closed = False
        _LIVE_TABLES.add(self)

    def running(self) -> List[BackgroundProcess]:
        with self._lock:
            return [p for p in self._processes.values() if not p.finished]

    def all(self) -> List[BackgroundProcess]:
        with self._lock:
            return list(self._processes.values())

    def get(self, process_id: str) -> Optional[BackgroundProcess]:
        with self._lock:
            return self._processes.get((process_id or "").strip())

    def start(self, command: str, plan: Any, name: str) -> BackgroundProcess:
        with self._lock:
            if self._closed:
                raise ProcessStartError("This agent's process table is closed.")
            running = [p for p in self._processes.values() if not p.finished]
            if len(running) >= MAX_RUNNING:
                raise ProcessLimitError(
                    f"{len(running)} background processes are already running "
                    f"(the limit is {MAX_RUNNING}): "
                    + ", ".join(f"{p.process_id} ({p.command})" for p in running)
                )
            self._evict_finished_locked()
            process_id = f"p{next(self._ids)}"
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log = OutputLog(self.log_dir / f"{process_id}.log")
            try:
                process = BackgroundProcess(
                    process_id=process_id,
                    name=name,
                    command=command,
                    plan=plan,
                    log=log,
                )
            except BaseException:
                log.close(delete=True)
                raise
            self._processes[process_id] = process
            # Under the lock, so a concurrent close() never sees it unstarted.
            process.start()
        return process

    def _evict_finished_locked(self) -> None:
        while len(self._processes) >= MAX_RETAINED:
            oldest = next((p for p in self._processes.values() if p.finished), None)
            if oldest is None:
                return
            del self._processes[oldest.process_id]
            oldest.dispose(delete_log=True)

    def take_unreported_finishes(self) -> List[BackgroundProcess]:
        """Finished processes the model has not been told about, marked told."""
        with self._lock:
            done = [
                p for p in self._processes.values() if p.finished and not p.end_reported
            ]
            for process in done:
                process.end_reported = True
            return done

    def close(self) -> None:
        """Stop every process and delete the logs. Safe to call more than once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            processes = list(self._processes.values())
            self._processes.clear()
        # Signal every tree first, then wait once, not once per process.
        for process in processes:
            process.begin_stop()
        for process in processes:
            try:
                process.finish_stop()
                process.dispose(delete_log=True, stopped=True)
            except Exception as exc:  # noqa: BLE001 — keep stopping the rest
                logger.error(
                    "Could not stop background process %s (%s): %s",
                    process.process_id,
                    process.command,
                    exc,
                )
        if self._owns_log_dir:
            try:
                shutil.rmtree(self.log_dir)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(
                    "Could not remove background-process logs at %s: %s",
                    self.log_dir,
                    exc,
                )
        _LIVE_TABLES.discard(self)


def started_at_iso(process: BackgroundProcess) -> str:
    return (
        datetime.fromtimestamp(process.started_at)
        .astimezone()
        .isoformat(timespec="seconds")
    )
