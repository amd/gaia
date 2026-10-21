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
Waiters queue for it in arrival order through ``<name>.lock.q<number>`` entry
files, so a later waiter cannot take it ahead of an earlier one.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

from gaia.daemon.lock import try_lock, unlock
from gaia.logger import get_logger

if os.name != "nt":
    import fcntl

log = get_logger(__name__)

#: Holders keep the lock only for the I/O itself, so a 10 s wait means a stuck
#: process or a long queue, not one busy holder.
LOCK_TIMEOUT_SECONDS = 10.0
#: How long one rename waits out another program's hold. A lock holder does at
#: most three renames (a skill swap), so its worst case stays under
#: LOCK_TIMEOUT_SECONDS and a waiter never gives up on a holder that is merely
#: waiting out an antivirus scan.
REPLACE_RETRY_SECONDS = 3.0
_POLL_SECONDS = 0.02
#: A waiter checking for dead queue entries holds a new entry's lock for an
#: instant; the entry's owner retries this long before giving up.
_ENTRY_LOCK_SECONDS = 1.0

_held = threading.local()


class FileLockError(OSError):
    """The lock guarding a file could not be taken.

    ``dir_unwritable`` is True when the lock could not be created because this
    user cannot create files in its directory at all.
    """

    def __init__(self, message: str, lock_path: Path, *, dir_unwritable: bool = False):
        super().__init__(message)
        self.lock_path = lock_path
        self.dir_unwritable = dir_unwritable


class FileLockTimeout(FileLockError, TimeoutError):
    """Another process held a file's lock for longer than the caller would wait.

    ``ahead`` counts the other GAIA operations queued in front of this one when
    it gave up, the holder included.
    """

    def __init__(self, message: str, lock_path: Path, *, ahead: int = 0):
        super().__init__(message, lock_path)
        self.ahead = ahead


def lock_dir_unwritable(error: FileLockError) -> bool:
    """True when *error* means only that the lock file's directory is read-only.

    No process can rename a new file into such a directory, so a caller that
    only reads may then read without the lock and still never see a partial file.
    """
    return error.dir_unwritable


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


def _can_create_in(directory: Path) -> bool:
    """Whether this user can create a file in *directory*, found by trying one.

    ``os.access`` ignores Windows ACLs, so only a real create answers this.
    Not ``tempfile.mkstemp``: on Windows it retries a refused create 10,000
    times whenever ``os.access`` calls the folder writable.
    """
    probe = directory / f".gaia-write-probe-{uuid.uuid4().hex}"
    try:
        fd = os.open(str(probe), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    except (FileNotFoundError, PermissionError):
        return False
    except OSError as e:
        if e.errno == errno.EROFS:
            return False
        raise
    os.close(fd)
    try:
        os.unlink(probe)
    except OSError as e:
        log.warning("Could not remove the write probe %s: %s", probe, e)
    return True


def _create_error(
    path: Path, target: Union[str, Path], error: OSError
) -> FileLockError:
    """The FileLockError for failing to create or open the lock file *path*."""
    unwritable = error.errno == errno.EROFS or (
        isinstance(error, PermissionError) and not _can_create_in(path.parent)
    )
    if unwritable:
        advice = (
            f"This user cannot create files in {path.parent}; fix its "
            "permissions or point GAIA at a folder this user can write."
        )
    else:
        advice = f"Check that {path} can be opened for writing by this user."
    return FileLockError(
        f"Cannot create the lock file {path} that guards {target}: {error}. {advice}",
        path,
        dir_unwritable=unwritable,
    )


# A waiter joins the queue before it touches the lock: it creates
# ``<name>.lock.q<number>``, numbered after every existing entry, and holds an
# OS lock on it until it is done. It takes the main lock only once no live entry
# with a smaller number is left. An entry whose lock can be taken belongs to a
# process that died, and the next waiter to see it deletes it.

#: Two spellings of one name are one file here, so their entries share a queue.
_NAMES_IGNORE_CASE = os.name == "nt" or sys.platform == "darwin"
#: Taken names (existing, or on Windows still being deleted) skipped per join.
_MAX_NAME_SKIPS = 1000


def _queue_entries(path: Path) -> List[Tuple[int, Path]]:
    pattern = re.compile(
        re.escape(path.name) + r"\.q(\d{10})$",
        re.IGNORECASE if _NAMES_IGNORE_CASE else 0,
    )
    entries = []
    try:
        with os.scandir(path.parent) as listing:
            for item in listing:
                match = pattern.match(item.name)
                if match:
                    entries.append((int(match.group(1)), path.parent / item.name))
    except OSError as e:
        raise FileLockError(
            f"Cannot list {path.parent} to queue for the lock {path}: {e}. "
            "Check that this user can read that folder.",
            path,
        ) from e
    return entries


def _release_fd(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        unlock(fd)
    finally:
        os.close(fd)


def _discard_entry(entry: Path) -> None:
    """Delete a queue entry nobody holds; another waiter may be doing the same."""
    try:
        os.unlink(entry)
    except FileNotFoundError:
        pass
    except PermissionError as e:
        # Windows: another waiter has it open to check it. It is unlocked, so
        # the next waiter to look deletes it.
        log.debug("Left queue entry %s for the next waiter: %s", entry, e)


def _release_and_discard(fd: int, entry: Path) -> None:
    """Release the lock *fd* holds on *entry* and delete *entry*.

    POSIX deletes first, while still locked, so no waiter can create a new entry
    under the same name in between. Windows cannot delete an open file, so it
    releases first; an open handle there also protects a newer entry's name.
    """
    if os.name != "nt":
        _discard_entry(entry)
    try:
        _release_fd(fd)
    finally:
        if os.name == "nt":
            _discard_entry(entry)


def _probe_lock(fd: int) -> Optional[bool]:
    """Take *fd*'s lock if free: True taken, False held elsewhere, None unknown."""
    if os.name == "nt":
        return try_lock(fd)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    except OSError:
        # NFS emulates flock with fcntl locks, which refuse a read-only fd.
        return None


def _open_entry(entry: Path) -> int:
    try:
        return os.open(str(entry), os.O_RDWR)
    except PermissionError:
        # Another user's entry: readable, so its lock can still be tested.
        return os.open(str(entry), os.O_RDONLY)


def _entry_alive(entry: Path) -> Optional[bool]:
    """Whether *entry*'s owner still waits for, or holds, the lock.

    None when this user cannot tell.
    """
    try:
        fd = _open_entry(entry)
    except FileNotFoundError:
        return False
    except PermissionError:
        # Windows refuses to open a file that is being deleted.
        return False if os.name == "nt" else None
    taken = _probe_lock(fd)
    if not taken:
        os.close(fd)
        return None if taken is None else True
    _release_and_discard(fd, entry)
    return False


def _same_file(fd: int, path: Path) -> bool:
    try:
        on_disk = os.stat(path)
    except FileNotFoundError:
        return False
    held = os.fstat(fd)
    return (held.st_dev, held.st_ino) == (on_disk.st_dev, on_disk.st_ino)


class _QueueEntry:
    def __init__(self, number: int, path: Path, fd: int):
        self.number = number
        self.path = path
        self.fd = fd

    def leave(self) -> None:
        _release_and_discard(self.fd, self.path)


def _create_entry(path: Path, target: Union[str, Path]) -> Optional[_QueueEntry]:
    """Create this waiter's entry, numbered after every existing one, unlocked.

    None when this user cannot create files beside the lock.
    """
    number = max((n for n, _ in _queue_entries(path)), default=-1) + 1
    for _ in range(_MAX_NAME_SKIPS):
        entry = path.with_name(f"{path.name}.q{number:010d}")
        try:
            # Readable by all, so any user can tell whether its owner is alive.
            fd = os.open(str(entry), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            number += 1
            continue
        except OSError as e:
            error = _create_error(entry, target, e)
            if error.dir_unwritable:
                return None
            if os.name == "nt" and isinstance(e, PermissionError):
                # The folder takes new files, so this name is still being
                # deleted by a waiter that just left.
                number += 1
                continue
            raise error from e
        return _QueueEntry(number, entry, fd)
    raise FileLockError(
        f"Could not create a queue entry for the lock {path} on {target}: "
        f"{_MAX_NAME_SKIPS} names in a row were refused. Check that "
        f"this user can create files in {path.parent}.",
        path,
    )


def _join_queue(path: Path, target: Union[str, Path]) -> Optional[_QueueEntry]:
    """Create and lock this waiter's entry, numbered after every existing one.

    None when this user cannot create files beside the lock, which it could
    open: such a waiter waits behind every queued one instead.
    """
    while True:
        entry = _create_entry(path, target)
        if entry is None:
            log.debug("Waiting on %s without a queue entry: folder is read-only", path)
            return None
        deadline = time.monotonic() + _ENTRY_LOCK_SECONDS
        while not try_lock(entry.fd):
            if time.monotonic() >= deadline:
                ours = _same_file(entry.fd, entry.path)
                os.close(entry.fd)
                if ours:
                    _discard_entry(entry.path)
                raise FileLockTimeout(
                    f"Could not lock this waiter's queue entry {entry.path} for "
                    f"{target}: another process held it for over "
                    f"{_ENTRY_LOCK_SECONDS:g}s. Retry once other GAIA operations "
                    "finish.",
                    path,
                )
            time.sleep(0.001)
        if _same_file(entry.fd, entry.path):
            return entry
        # POSIX: a waiter took it for dead and deleted it before we locked it.
        _release_fd(entry.fd)


def _ahead(path: Path, entry: Optional[_QueueEntry]) -> Tuple[int, List[Path]]:
    """Live entries queued before *entry* (all of them when None), and the
    ones this user could not open to check."""
    count, unknown = 0, []
    for number, other in _queue_entries(path):
        if entry is not None and number >= entry.number:
            continue
        alive = _entry_alive(other)
        if alive is None:
            unknown.append(other)
        if alive is not False:
            count += 1
    return count, unknown


def _timeout_message(
    target: Union[str, Path],
    path: Path,
    timeout: float,
    ahead: int,
    unknown: List[Path],
) -> str:
    waited = (
        f"Gave up after {timeout:g}s waiting for the lock on {target} "
        f"(lock file {path})"
    )
    if ahead == 0:
        return (
            f"{waited}: a process outside GAIA's wait queue holds it, such as an "
            "older GAIA version. Close other `gaia` commands, the Agent UI or "
            "TUI and retry."
        )
    if ahead == 1:
        message = (
            f"{waited}: 1 other GAIA operation was ahead of this one. "
            "Retry once it finishes."
        )
    else:
        message = (
            f"{waited}: {ahead} other GAIA operations were ahead of this one. "
            "Retry once they finish."
        )
    if unknown:
        message += (
            f" This user cannot check {len(unknown)} of those queue entries "
            f"({', '.join(str(p) for p in unknown)}) to tell whether their "
            "owner is still running; if no other GAIA process is, delete them."
        )
    return message


class HeldFileLock:
    """A taken file lock that :meth:`release` frees from any thread.

    For a lock that outlives the block that took it, such as an install slot
    claimed by a request and released by its background worker. It is not
    re-entrant: taking the same lock again, even in this thread, waits on it.
    """

    def __init__(self, path: Path, fd: int, entry: Optional[_QueueEntry]):
        self.path = path
        self._fd: Optional[int] = fd
        self._entry = entry
        self._release_lock = threading.Lock()

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        """Release the lock; a second call does nothing."""
        with self._release_lock:
            fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _release_fd(fd)
        finally:
            if self._entry is not None:
                self._entry.leave()


def acquire_file_lock(
    target: Union[str, Path], *, timeout: Optional[float] = None
) -> HeldFileLock:
    """Take the cross-process lock for *target*, waiting in arrival order.

    ``timeout`` defaults to :data:`LOCK_TIMEOUT_SECONDS`; ``timeout=0`` makes a
    single attempt. Use :func:`file_lock` unless the lock must outlive the
    block that takes it.

    Raises:
        FileLockTimeout: the lock was still taken after *timeout*.
        FileLockError: the lock file could not be created.
    """
    path = lock_path_for(target)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        raise _create_error(path, target, e) from e
    if timeout is None:
        timeout = LOCK_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout
    try:
        entry = _join_queue(path, target)
    except BaseException:
        os.close(fd)
        raise
    try:
        while True:
            ahead, unknown = _ahead(path, entry)
            if ahead == 0 and try_lock(fd):
                return HeldFileLock(path, fd, entry)
            if time.monotonic() >= deadline:
                raise FileLockTimeout(
                    _timeout_message(target, path, timeout, ahead, unknown),
                    path,
                    ahead=ahead,
                )
            time.sleep(_POLL_SECONDS)
    except BaseException:
        try:
            os.close(fd)
        finally:
            if entry is not None:
                entry.leave()
        raise


@contextmanager
def file_lock(
    target: Union[str, Path], *, timeout: Optional[float] = None
) -> Iterator[Path]:
    """Hold the cross-process lock for *target* for the duration of the block.

    Waiters, in this process and every other, get the lock in the order they
    asked for it. Re-entrant within a thread, so a locked read-modify-write can
    call a helper that takes the same lock to read. ``timeout`` defaults to
    :data:`LOCK_TIMEOUT_SECONDS`; ``timeout=0`` makes a single attempt.

    Raises:
        FileLockTimeout: the lock was still taken after *timeout*.
        FileLockError: the lock file could not be created.
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

    held = acquire_file_lock(target, timeout=timeout)
    counts[key] = 1
    try:
        yield path
    finally:
        del counts[key]
        held.release()


def write_bytes_atomic(
    target: Union[str, Path], data: bytes, mode: int = 0o600
) -> None:
    """Replace *target* with *data*; a reader never sees a partial file.

    The temp file is created at *mode* and fsynced before the rename, so a crash
    leaves either the old file or the complete new one.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Not tempfile.mkstemp: on Windows it retries a refused create up to
    # 10,000 times, which turns an ACL-denied folder into a long hang.
    tmp = target.parent / f".{target.name}.{uuid.uuid4().hex[:12]}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(tmp), flags, 0o600)
    except PermissionError as e:
        raise PermissionError(
            e.errno,
            f"Cannot write {target}: this user cannot create files in "
            f"{target.parent}. Fix its permissions or point GAIA at a folder "
            "this user can write",
            str(tmp),
        ) from e
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
