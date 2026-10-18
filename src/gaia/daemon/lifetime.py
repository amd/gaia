# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""When a running daemon should exit on its own.

A daemon whose home folder or ``instance.json`` is gone can no longer be found
by any client, so nothing will ever ask it to stop: it would poll forever
against state that no longer exists. :class:`LifetimeWatch` notices that and
asks the server to shut down through its normal, graceful path.

``GAIA_DAEMON_EXIT_WITH_PID`` additionally ties the daemon to one process. It is
opt-in, set by the test suite so a daemon a test started cannot outlive a test
run that was killed before its teardown ran. Production never sets it.

A daemon for any home but ``~/.gaia`` (a scratch ``GAIA_HOME``, an eval or demo
home) also exits after :data:`DEFAULT_IDLE_EXIT_MINUTES` with no client, no busy
sidecar and no pending scheduled job; the next client starts a fresh one. The
default home's daemon is the machine's always-on one and never idles out.
``GAIA_DAEMON_IDLE_EXIT_MINUTES`` changes the limit; ``0`` turns it off.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Optional

from gaia.daemon import paths
from gaia.daemon.errors import DaemonStartError
from gaia.daemon.instance import pid_alive
from gaia.logger import get_logger

logger = get_logger(__name__)

EXIT_WITH_PID_ENV = "GAIA_DAEMON_EXIT_WITH_PID"
IDLE_EXIT_ENV = "GAIA_DAEMON_IDLE_EXIT_MINUTES"
DEFAULT_IDLE_EXIT_MINUTES = 120

#: Seconds between checks. Each one is two stats (and a pid check when tied).
CHECK_SECONDS = 5.0


def exit_with_pid_from_env() -> Optional[int]:
    """The pid named by ``GAIA_DAEMON_EXIT_WITH_PID``, or None when unset.

    Raises:
        DaemonStartError: The variable is set to something that is not a pid.
    """
    raw = os.environ.get(EXIT_WITH_PID_ENV, "").strip()
    if not raw:
        return None
    message = (
        f"{EXIT_WITH_PID_ENV}={raw!r} is not a process id. Unset it, or set it "
        "to the pid of the process the daemon should exit with."
    )
    try:
        pid = int(raw)
    except ValueError as e:
        raise DaemonStartError(message) from e
    if pid <= 0:
        raise DaemonStartError(message)
    return pid


def idle_exit_seconds_from_env() -> Optional[float]:
    """The idle limit for a non-default home, or None when it is turned off.

    Raises:
        DaemonStartError: ``GAIA_DAEMON_IDLE_EXIT_MINUTES`` is not a whole number
            of minutes, or is negative.
    """
    raw = os.environ.get(IDLE_EXIT_ENV, "").strip()
    if not raw:
        return DEFAULT_IDLE_EXIT_MINUTES * 60.0
    try:
        minutes = int(raw)
    except ValueError as e:
        raise DaemonStartError(
            f"{IDLE_EXIT_ENV}={raw!r} is not a whole number of minutes. Unset it "
            f"for the default ({DEFAULT_IDLE_EXIT_MINUTES}), or set 0 to never idle out."
        ) from e
    if minutes < 0:
        raise DaemonStartError(
            f"{IDLE_EXIT_ENV}={raw!r} is negative. Use 0 to never idle out."
        )
    return minutes * 60.0 if minutes else None


def is_default_home() -> bool:
    """True for the machine's own daemon home, ``~/.gaia/host``."""
    return paths.host_dir().resolve() == (Path.home() / ".gaia" / "host").resolve()


def exit_reason(
    exit_with_pid: Optional[int] = None,
    idle_seconds: Optional[float] = None,
    idle_limit: Optional[float] = None,
) -> Optional[str]:
    """Why this daemon should exit now, or None while it should keep running.

    *idle_seconds* is how long nothing has used the daemon (None while
    something does); it counts against *idle_limit* only outside the default
    home.
    """
    home = paths.host_dir()
    if not home.is_dir():
        return f"its home {home} no longer exists"
    # On Windows a deleted home can linger, held open by the daemon's own log.
    state = paths.instance_path()
    if not state.is_file():
        return f"its state file {state} no longer exists"
    if exit_with_pid is not None and not pid_alive(exit_with_pid):
        return f"process {exit_with_pid} ({EXIT_WITH_PID_ENV}) has exited"
    if (
        idle_limit is not None
        and idle_seconds is not None
        and idle_seconds >= idle_limit
        and not is_default_home()
    ):
        return (
            f"nothing has used it for {idle_seconds / 60:.0f} minutes "
            f"({IDLE_EXIT_ENV}={idle_limit / 60:g})"
        )
    return None


class LifetimeWatch:
    """Calls *on_exit* once, from a background thread, when :func:`exit_reason`
    says the daemon should stop.

    Args:
        on_exit: Starts the daemon's graceful shutdown. Receives the reason.
        exit_with_pid: Also exit once this process is gone.
        idle: Seconds since anything last used the daemon, or None while
            something does.
        idle_limit: Exit once *idle* reaches this, outside the default home.
        interval: Seconds between checks.
    """

    def __init__(
        self,
        on_exit: Callable[[str], None],
        *,
        exit_with_pid: Optional[int] = None,
        idle: Optional[Callable[[], Optional[float]]] = None,
        idle_limit: Optional[float] = None,
        interval: float = CHECK_SECONDS,
    ) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be > 0, got {interval}")
        self._on_exit = on_exit
        self._exit_with_pid = exit_with_pid
        self._idle = idle
        self._idle_limit = idle_limit
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start watching. Idempotent while running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="gaia-daemon-lifetime", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop watching. Safe before :meth:`start` and from ``on_exit``."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._interval + 1)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            idle = self._idle() if self._idle is not None else None
            reason = exit_reason(self._exit_with_pid, idle, self._idle_limit)
            if reason is None:
                continue
            logger.warning(
                "daemon: shutting down because %s (daemon home %s)",
                reason,
                paths.host_dir(),
            )
            self._on_exit(reason)
            return


__all__ = [
    "CHECK_SECONDS",
    "DEFAULT_IDLE_EXIT_MINUTES",
    "EXIT_WITH_PID_ENV",
    "IDLE_EXIT_ENV",
    "LifetimeWatch",
    "exit_reason",
    "exit_with_pid_from_env",
    "idle_exit_seconds_from_env",
    "is_default_home",
]
