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
(:func:`replace_retrying`) and then fails naming the file.

The lock is an OS advisory lock on ``<name>.lock`` beside the target
(:func:`gaia.daemon.lock.try_lock`), released by the OS if the holder dies.
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional, Union

from gaia.daemon.lock import try_lock, unlock

#: Holders keep the lock only for the I/O itself, so a 10 s wait means a stuck
#: process, not a busy one.
LOCK_TIMEOUT_SECONDS = 10.0
#: How long one rename waits out another program's hold. A lock holder does at
#: most three renames (a skill swap), so its worst case stays under
#: LOCK_TIMEOUT_SECONDS and a waiter never gives up on a holder that is merely
#: waiting out an antivirus scan.
REPLACE_RETRY_SECONDS = 3.0
_POLL_SECONDS = 0.02

_held = threading.local()


class FileLockError(OSError):
    """The lock guarding a file could not be taken."""

    def __init__(self, message: str, lock_path: Path):
        super().__init__(message)
        self.lock_path = lock_path


class FileLockTimeout(FileLockError, TimeoutError):
    """Another process held a file's lock for longer than the caller would wait."""


_WINDOWS = os.name == "nt"
# ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION.
_LOCKED_WINERRORS = frozenset({32, 33})


def held_by_another_program(error: BaseException) -> bool:
    """True when *error* may be another program holding the file, worth retrying.

    Windows reports an exclusive open, a byte-range lock and a missing read
    permission alike, as ``PermissionError`` with errno EACCES, so a caller
    cannot tell them apart; messages built on this must name both causes.
    POSIX reads are never blocked by another program, so there EACCES is only
    a permission. A ``PermissionError`` GAIA raises itself (a path-policy
    refusal) has no errno and is never a lock.
    """
    if not _WINDOWS or not isinstance(error, PermissionError):
        return False
    return (
        error.errno == errno.EACCES
        or getattr(error, "winerror", None) in _LOCKED_WINERRORS
    )


def lock_dir_unwritable(error: FileLockError) -> bool:
    """True when *error* means only that the lock file's directory is read-only.

    No process can rename a new file into such a directory, so a caller that
    only reads may then read without the lock and still never see a partial file.
    """
    cause = error.__cause__
    if isinstance(cause, OSError) and cause.errno == errno.EROFS:
        return True
    # A lock file that exists but can't be opened, in a writable directory, is
    # not this case: a writer could still replace the file there.
    return isinstance(cause, PermissionError) and not os.access(
        error.lock_path.parent, os.W_OK
    )


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
    target: Union[str, Path], *, timeout: Optional[float] = None
) -> Iterator[Path]:
    """Hold the cross-process lock for *target* for the duration of the block.

    Re-entrant within a thread, so a locked read-modify-write can call a helper
    that takes the same lock to read. ``timeout`` defaults to
    :data:`LOCK_TIMEOUT_SECONDS`; ``timeout=0`` makes a single attempt.

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
    if timeout is None:
        timeout = LOCK_TIMEOUT_SECONDS
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
        replace_retrying(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def replace_retrying(src: Union[str, Path], target: Union[str, Path]) -> None:
    """``os.replace``, waiting out another program's brief hold on Windows.

    Antivirus scanners, the search indexer and backup tools open new files for
    a moment, and Windows refuses a rename onto (or of) an open file. That is
    retried every 20 ms for up to :data:`REPLACE_RETRY_SECONDS`. A read-only
    file, any other error, or a hold that outlasts the budget raises.
    """
    target = Path(target)
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    while True:
        try:
            os.replace(src, target)
            return
        except PermissionError as e:
            if os.name != "nt":
                raise
            for path in (Path(src), target):
                if _is_read_only(path):
                    raise PermissionError(
                        e.errno,
                        f"Cannot replace {target}: {path} is marked read-only. "
                        "Clear its read-only attribute (file Properties) and retry",
                        str(path),
                    ) from e
            if time.monotonic() >= deadline:
                raise PermissionError(
                    e.errno,
                    f"Cannot replace {target}: another program has it open and "
                    f"did not let go within {REPLACE_RETRY_SECONDS:g}s. Close any "
                    "editor or viewer showing it and retry; if it persists, "
                    "exclude the GAIA folder from antivirus real-time scanning",
                    str(target),
                ) from e
            time.sleep(_POLL_SECONDS)


def _is_read_only(path: Path) -> bool:
    try:
        attributes = getattr(os.stat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_READONLY)


def write_text_atomic(target: Union[str, Path], text: str, mode: int = 0o600) -> None:
    """Replace *target* with UTF-8 *text*; see :func:`write_bytes_atomic`."""
    write_bytes_atomic(target, text.encode("utf-8"), mode=mode)
