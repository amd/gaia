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


def _identity(pid: int) -> Optional[float]:
    """The process's start time, so a reused pid isn't taken for the daemon."""
    try:
        return psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
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

    Raises:
        ValueError: ``GAIA_DAEMON_PID`` is set but is not a pid.
    """
    raw = (env if env is not None else os.environ).get(DAEMON_PID_ENV_VAR)
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError as e:
        raise ValueError(
            f"{DAEMON_PID_ENV_VAR}={raw!r} is not a process id; the GAIA daemon "
            "sets it when it starts this agent, so unset it to run standalone."
        ) from e
    born = _identity(pid)

    def _watch() -> None:
        while True:
            if born is None or _identity(pid) != born:
                logger.warning(
                    "The GAIA daemon (pid %s) that started this agent is gone; "
                    "shutting down so nothing is left running without it.",
                    pid,
                )
                on_gone()
                return
            stop.wait(poll_s)
            if stop.is_set():
                return

    stop = threading.Event()
    thread = threading.Thread(target=_watch, name="gaia-daemon-watch", daemon=True)
    thread.stop = stop  # type: ignore[attr-defined]
    thread.start()
    return thread
