# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run a child process under a time cap, and kill everything it started.

``subprocess.run(timeout=...)`` kills only the direct child: a grandchild
keeps running, keeps files open (so its folder can't be deleted on Windows),
and keeps any inherited output pipe open, so reading it never ends. Here the
child gets its own process group or session, writes to files rather than
pipes, and on a timeout or an interrupt its whole tree is killed.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import psutil

from gaia.logger import get_logger

log = get_logger(__name__)

#: How often a wait wakes, so Ctrl+C is handled while a child runs.
_POLL_S = 0.5
#: How often the child's descendants are listed; each listing walks every process.
_SAMPLE_S = 2.0
#: How long killed descendants get to exit before they are reported.
_REAP_S = 5


class Stopped(Exception):
    """Another thread asked a running :func:`launch` to stop; its tree is dead."""


def popen_kwargs() -> Dict[str, Any]:
    """Start the child as the head of its own group, apart from this console's Ctrl+C."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _sample(proc: subprocess.Popen, seen: Dict[int, psutil.Process]) -> None:
    """Add *proc*'s descendants now running to *seen*."""
    try:
        for child in psutil.Process(proc.pid).children(recursive=True):
            seen.setdefault(child.pid, child)
    except psutil.Error:
        pass  # exited since: what was seen before is still killed


Spare = Callable[[], Iterable[int]]


def _unspared(
    seen: Dict[int, psutil.Process], spare: Optional[Spare]
) -> Tuple[Dict[int, psutil.Process], bool]:
    """*seen* less what *spare* names, its descendants and its launcher.

    The flag says whether anything in *seen* was spared.
    """
    if spare is None:
        return seen, False
    spared = set()
    for pid in spare():
        try:
            if pid <= 0:
                raise ValueError(f"pid {pid}")
            root = psutil.Process(pid)
            spared.add(pid)
            spared.update(p.pid for p in root.children(recursive=True))
            parent = root.parent()
            # A Windows venv's python.exe runs the real one with the same
            # arguments, and killing it kills that child.
            if parent is not None and parent.cmdline()[1:] == root.cmdline()[1:]:
                spared.add(parent.pid)
        except psutil.Error:
            continue  # gone, or not ours to read: nothing more to spare
        except (ValueError, OverflowError, TypeError) as e:
            log.warning("not sparing %r, not a process id: %s", pid, e)
    kept = {pid: p for pid, p in seen.items() if pid not in spared}
    return kept, len(kept) < len(seen)


def _kill_seen(owner: int, seen: Dict[int, psutil.Process]) -> None:
    for child in seen.values():
        with contextlib.suppress(psutil.Error):
            child.kill()  # psutil checks the pid was not reused
    _, alive = psutil.wait_procs(list(seen.values()), timeout=_REAP_S)
    for child in alive:
        log.error(
            "process %s, started by pid %s, survived a kill; end it by hand",
            child.pid,
            owner,
        )


def kill_tree(
    proc: subprocess.Popen,
    seen: Optional[Dict[int, psutil.Process]] = None,
    spare: Optional[Spare] = None,
) -> None:
    """Kill *proc* and every process it started, then reap *proc*.

    Descendants are listed while *proc* is alive, plus any in *seen* from
    earlier samples: on POSIX one that started its own session (as the shell
    tool's commands do) is outside *proc*'s group, and on Windows one whose
    parent already exited is out of ``taskkill /T``'s reach. A process *spare*
    names lives on, with its descendants and its launcher.
    """
    seen = dict(seen or {})
    running = proc.poll() is None
    if running:
        _sample(proc, seen)
    seen, spared = _unspared(seen, spare)
    # taskkill /T would reach a spared process too; then only psutil kills.
    if running and os.name == "nt" and not spared:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                timeout=_REAP_S * 6,
            )
        except subprocess.TimeoutExpired:
            log.error("taskkill of pid %s hung; killing what was seen", proc.pid)
    if os.name != "nt":
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)  # pylint: disable=no-member
    with contextlib.suppress(OSError):
        proc.kill()
    proc.wait()
    _kill_seen(proc.pid, seen)


def launch(
    cmd: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout_s: float,
    stdout_path: Path,
    stderr_path: Path,
    stdin_path: Optional[Path] = None,
    stop: Optional[threading.Event] = None,
    kill_leftovers: bool = False,
    spare: Optional[Spare] = None,
) -> Tuple[Optional[int], bool]:
    """Run *cmd* to completion or the cap; return ``(exit code, timed out)``.

    Output goes to files, so a run killed at the cap keeps everything it wrote
    and a descendant holding the output can't stall the return. On the cap, or
    any exception here (Ctrl+C included), the whole tree is killed. A child
    that exits on its own returns at once; what it left running is its own,
    unless *kill_leftovers*, which kills the descendants seen while it ran.
    Setting *stop* from another thread kills the tree and raises
    :class:`Stopped` here, since only the main thread sees Ctrl+C. Every kill
    spares what *spare* names (see :func:`kill_tree`).
    """
    seen: Dict[int, psutil.Process] = {}
    with contextlib.ExitStack() as stack:
        out = stack.enter_context(open(stdout_path, "wb"))
        err = stack.enter_context(open(stderr_path, "wb"))
        stdin: Any = subprocess.DEVNULL
        if stdin_path is not None:
            stdin = stack.enter_context(open(stdin_path, "rb"))
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            list(cmd),
            cwd=str(cwd),
            env=dict(env),
            stdin=stdin,
            stdout=out,
            stderr=err,
            **popen_kwargs(),
        )
        try:
            deadline = time.monotonic() + timeout_s
            next_sample = 0.0  # the first wake lists them, then every _SAMPLE_S
            while True:
                if stop is not None and stop.is_set():
                    raise Stopped(f"stopped: {cmd[0]}")
                left = deadline - time.monotonic()
                if left <= 0:
                    kill_tree(proc, seen, spare)
                    return None, True
                try:
                    code = proc.wait(timeout=min(_POLL_S, left))
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= next_sample:
                        _sample(proc, seen)
                        next_sample = time.monotonic() + _SAMPLE_S
                    continue
                if kill_leftovers:
                    _kill_seen(proc.pid, _unspared(seen, spare)[0])
                return code, False
        except BaseException:
            kill_tree(proc, seen, spare)
            raise
