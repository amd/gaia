# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cross-process locking and atomic replacement for files under ``~/.gaia``.

The daemon, the Agent UI, the CLI and the TUI all read and write the same files.
Two rules keep them from corrupting each other:

- A writer never truncates in place. It writes a sibling temp file and renames
  it over the target, so a reader sees either the old bytes or the new ones.
- Every read-modify-write holds :func:`file_lock`, so two writers cannot each
  load the same old copy and drop the other's change.

Readers hold the lock too. On Windows a rename onto a file another process has
open fails with "access denied", and opening a file mid-rename fails the same
way, so an unlocked reader would trade a torn read for a ``PermissionError``.

Programs outside GAIA (antivirus, the search indexer) can still hold a file
open for a moment; on Windows the rename waits that out for a bounded time
(:func:`_replace`) and then fails naming the file.

The lock is an OS advisory lock on ``<name>.lock`` beside the target
(:func:`gaia.daemon.lock.try_lock`), released by the OS if the holder dies.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Union

from gaia.daemon.lock import try_lock, unlock

#: Holders keep the lock only for the I/O itself, so a 10 s wait means a stuck
#: process, not a busy one.
LOCK_TIMEOUT_SECONDS = 10.0
_POLL_SECONDS = 0.02

_held = threading.local()


class FileLockError(OSError):
    """The lock guarding a file could not be taken."""

    def __init__(self, message: str, lock_path: Path):
        super().__init__(message)
        self.lock_path = lock_path


class FileLockTimeout(FileLockError, TimeoutError):
    """Another process held a file's lock for longer than the caller would wait."""


def lock_path_for(target: Union[str, Path]) -> Path:
    """The lock file guarding *target*: ``<name>.lock`` in the same directory."""
    target = Path(target)
    return target.with_name(target.name + ".lock")


def _held_counts() -> Dict[str, int]:
    counts = getattr(_held, "counts", None)
    if counts is None:
        counts = {}
        _held.counts = counts
    return counts


@contextmanager
def file_lock(
    target: Union[str, Path], *, timeout: float = LOCK_TIMEOUT_SECONDS
) -> Iterator[Path]:
    """Hold the cross-process lock for *target* for the duration of the block.

    Re-entrant within a thread, so a locked read-modify-write can call a helper
    that takes the same lock to read. ``timeout=0`` makes a single attempt.

    Raises:
        FileLockTimeout: another process still held the lock after *timeout*.
    """
    path = lock_path_for(target)
    key = os.path.normcase(os.path.abspath(path))
    counts = _held_counts()
    if key in counts:
        counts[key] += 1
        try:
            yield path
        finally:
            counts[key] -= 1
        return

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        raise FileLockError(
            f"Cannot create the lock file {path} that guards {target}: {e}. "
            f"Check that {path.parent} is writable by this user.",
            path,
        ) from e
    deadline = time.monotonic() + timeout
    while not try_lock(fd):
        if time.monotonic() >= deadline:
            os.close(fd)
            raise FileLockTimeout(
                f"{target} is locked by another GAIA process (lock file {path}) "
                f"and was not released within {timeout:g}s. Close any stuck "
                "`gaia` command, Agent UI or TUI and retry.",
                path,
            )
        time.sleep(_POLL_SECONDS)
    counts[key] = 1
    try:
        yield path
    finally:
        del counts[key]
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            unlock(fd)
        finally:
            os.close(fd)


def write_bytes_atomic(
    target: Union[str, Path], data: bytes, mode: int = 0o600
) -> None:
    """Replace *target* with *data*; a reader never sees a partial file.

    The temp file is created at *mode* and fsynced before the rename, so a crash
    leaves either the old file or the complete new one.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        _replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _replace(tmp: str, target: Path) -> None:
    """``os.replace``, waiting out another program's brief hold on Windows.

    Antivirus scanners, the search indexer and backup tools open new files for
    a moment, and Windows refuses a rename onto an open file. That is retried
    every 20 ms for up to :data:`LOCK_TIMEOUT_SECONDS`; any other error, or a
    hold that outlasts it, raises.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            os.replace(tmp, target)
            return
        except PermissionError as e:
            if os.name != "nt":
                raise
            if time.monotonic() >= deadline:
                raise PermissionError(
                    e.errno,
                    f"Cannot replace {target}: another program has it open and "
                    f"did not let go within {LOCK_TIMEOUT_SECONDS:g}s. Close any "
                    "editor or viewer showing it and retry; if it persists, "
                    "exclude the GAIA folder from antivirus real-time scanning",
                    str(target),
                ) from e
            time.sleep(_POLL_SECONDS)


def write_text_atomic(target: Union[str, Path], text: str, mode: int = 0o600) -> None:
    """Replace *target* with UTF-8 *text*; see :func:`write_bytes_atomic`."""
    write_bytes_atomic(target, text.encode("utf-8"), mode=mode)
