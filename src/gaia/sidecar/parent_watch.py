# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stop a sidecar whose daemon died without stopping it.

The daemon tree-kills its sidecars on a clean shutdown and reaps stale ones
when the next daemon starts. A daemon that was killed outright, with no daemon
after it, left its sidecar running indefinitely, holding memory, a port and
any MCP servers it started. The daemon passes its pid in ``GAIA_DAEMON_PID``.
The sidecar polls it and shuts itself down through its normal SIGINT path, so
its own cleanup still runs.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from typing import Callable, Mapping, Optional

import psutil

from gaia.daemon.constants import DAEMON_PID_ENV_VAR

logger = logging.getLogger(__name__)

#: Seconds between checks that the daemon is still alive.
POLL_S = 5.0
#: Seconds the graceful shutdown gets before the process exits outright.
GRACE_S = 30.0

_lock = threading.Lock()
_watcher: Optional[threading.Thread] = None
_stopping: Optional[threading.Event] = None


def _identity(pid: int) -> Optional[float]:
    """The process's start time, so a reused pid isn't taken for the daemon.

    None when no such process exists, or it is a zombie: a killed daemon whose
    parent never reaped it still has a start time on Linux and macOS.
    ``psutil.AccessDenied`` propagates: a process we may not inspect is not a
    process that is gone.
    """
    try:
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return None
        return proc.create_time()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def _shut_down() -> None:
    """SIGINT ourselves, as Ctrl+C would; exit outright if that hasn't ended us."""
    signal.raise_signal(signal.SIGINT)
    timer = threading.Timer(GRACE_S, lambda: os._exit(1))
    timer.daemon = True
    timer.start()


def start(
    env: Optional[Mapping[str, str]] = None,
    poll_s: float = POLL_S,
    on_gone: Callable[[], None] = _shut_down,
) -> Optional[threading.Thread]:
    """Watch the daemon named in *env*; the thread, or None when there is none.

    Starts one watcher per process: a later call returns the running one. With
    no *env* it reads ``os.environ`` and removes the variable, so processes the
    agent starts don't tie themselves to the daemon too.

    Raises:
        ValueError: ``GAIA_DAEMON_PID`` is set but is not a pid, or the
            daemon's process can't be inspected.
    """
    global _watcher, _stopping
    if env is None:
        # Read once and drop it: what the agent launches must not inherit the tie.
        raw = os.environ.pop(DAEMON_PID_ENV_VAR, None)
    else:
        raw = env.get(DAEMON_PID_ENV_VAR)
    if not raw:
        return _watcher if _watcher is not None and _watcher.is_alive() else None
    try:
        pid = int(raw)
    except ValueError as e:
        raise ValueError(
            f"{DAEMON_PID_ENV_VAR}={raw!r} is not a process id; the GAIA daemon "
            "sets it when it starts this agent, so unset it to run standalone."
        ) from e
    with _lock:
        if _watcher is not None and _watcher.is_alive():
            return _watcher
        try:
            born = _identity(pid)
        except psutil.AccessDenied as e:
            raise ValueError(
                f"cannot watch the GAIA daemon (pid {pid}): this agent may not "
                "inspect its process. Run the agent as the same user as the daemon."
            ) from e
        if born is None:
            # Before the server is up a SIGINT would surface as a bare traceback.
            raise ValueError(
                f"the GAIA daemon (pid {pid}) that started this agent is already "
                "gone, so the agent is not starting. Start GAIA again."
            )
        stopping = threading.Event()

        def _watch() -> None:
            while not stopping.wait(poll_s):
                try:
                    gone = _identity(pid) != born
                except psutil.AccessDenied:
                    gone = False  # it exists; we just may not inspect it now
                if gone:
                    logger.warning(
                        "The GAIA daemon (pid %s) that started this agent is gone; "
                        "shutting down so nothing is left running without it.",
                        pid,
                    )
                    on_gone()
                    return

        _stopping = stopping
        _watcher = threading.Thread(
            target=_watch, name="gaia-daemon-watch", daemon=True
        )
        _watcher.start()
        return _watcher


def stop(timeout: float = 5.0) -> None:
    """Stop the running watcher, if any; the next :func:`start` begins afresh."""
    global _watcher, _stopping
    with _lock:
        watcher, stopping = _watcher, _stopping
        _watcher = _stopping = None
    if stopping is not None:
        stopping.set()
    if watcher is not None:
        watcher.join(timeout)
