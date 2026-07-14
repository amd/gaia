# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cross-platform advisory file lock serializing daemon start.

Only ONE process may be in the "decide + spawn" critical section at a time, so two
concurrent ``start_or_attach()`` callers spawn exactly one daemon (the loser waits,
then attaches to the winner's instance.json). An OS advisory lock is used rather
than an ``O_EXCL`` create-lock because the OS releases it automatically when the
holder dies — a create-lock would strand a stale lock file after SIGKILL.

POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking``. Acquisition is a
bounded non-blocking retry so an abandoned attempt fails loudly with an actionable
error instead of hanging forever.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from gaia.daemon import paths
from gaia.daemon.errors import DaemonLockError

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class StartLock:
    """Context manager holding the exclusive daemon-start lock."""

    def __init__(self, timeout: float = 30.0, poll: float = 0.1):
        self._timeout = timeout
        self._poll = poll
        self._fd: Optional[int] = None
        self._path = paths.lock_path()

    def _try_acquire(self) -> bool:
        if os.name == "nt":
            try:
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def __enter__(self) -> "StartLock":
        paths.ensure_host_dir()
        # 0600: the lock file lives beside the token-bearing instance.json.
        self._fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self._timeout
        while True:
            if self._try_acquire():
                return self
            if time.monotonic() >= deadline:
                os.close(self._fd)
                self._fd = None
                raise DaemonLockError(
                    f"could not acquire the daemon start lock at {self._path} within "
                    f"{self._timeout}s — another process is starting the daemon and "
                    "did not finish. Check for a stuck `gaia daemon`/UI start, then "
                    "retry; if it persists inspect the daemon log via `gaia daemon logs`."
                )
            time.sleep(self._poll)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                try:
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
