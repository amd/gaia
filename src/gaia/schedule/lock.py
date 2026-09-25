# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Advisory file locks for the schedule store and the schedule daemon.

Two locks live beside the store file:

- ``schedules.lock`` is held by ``gaia schedule daemon`` for its whole lifetime,
  so a second daemon on the same store fails at start instead of firing every
  job a second time.
- ``schedules.toml.lock`` is held for each read-modify-write of the store, so a
  CLI ``add`` racing the daemon's ``mark_run`` cannot drop either change.

OS advisory locks are released when the holder dies, so a crashed daemon never
strands a stale lock.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from gaia.daemon.lock import try_lock, unlock

STORE_LOCK_TIMEOUT_SECONDS = 10.0
_POLL_SECONDS = 0.05


class ScheduleLockError(RuntimeError):
    """A schedule lock is held by another process."""


def daemon_lock_path(store_path: Path) -> Path:
    return Path(store_path).with_suffix(".lock")


def store_lock_path(store_path: Path) -> Path:
    store_path = Path(store_path)
    return store_path.with_name(store_path.name + ".lock")


def _open(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)


def _release(fd: int) -> None:
    try:
        # Windows unlocks the byte at the current offset, and the pid write moved it.
        os.lseek(fd, 0, os.SEEK_SET)
        unlock(fd)
    finally:
        os.close(fd)


@contextmanager
def daemon_lock(store_path: Path) -> Iterator[None]:
    """Hold the single-instance lock for the schedule daemon on ``store_path``.

    Fails immediately (no waiting) when another daemon already holds it.
    """
    path = daemon_lock_path(store_path)
    fd = _open(path)
    if not try_lock(fd):
        holder = _read_pid(fd)
        os.close(fd)
        raise ScheduleLockError(
            f"another `gaia schedule daemon`{holder} is already running for "
            f"{store_path} (lock held on {path}). Running two would fire every "
            "schedule twice. Stop the other daemon first, then retry."
        )
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        _release(fd)


def _read_pid(fd: int) -> str:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        pid = os.read(fd, 32).decode().strip()
    except OSError:
        # Windows refuses reads of the holder's locked byte; name no pid then.
        return ""
    return f" (pid {pid})" if pid.isdigit() else ""


@contextmanager
def store_lock(
    store_path: Path, timeout: float = STORE_LOCK_TIMEOUT_SECONDS
) -> Iterator[None]:
    """Serialize one read-modify-write of the schedule store at ``store_path``."""
    path = store_lock_path(store_path)
    fd = _open(path)
    deadline = time.monotonic() + timeout
    while not try_lock(fd):
        if time.monotonic() >= deadline:
            os.close(fd)
            raise ScheduleLockError(
                f"could not lock the schedule store {store_path} within "
                f"{timeout:g}s: another process has held {path} that long. "
                "Check for a hung `gaia schedule` command and retry."
            )
        time.sleep(_POLL_SECONDS)
    try:
        yield
    finally:
        _release(fd)
