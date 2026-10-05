# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Size-capped log files that several GAIA processes can write at once.

The CLI, the Agent UI server, the daemon and its sidecars all append to the same
``gaia.log``. Every write and every rotation happens under an inter-process lock
on ``<log>.lock``, so two processes never rotate at once, and a process whose
handle still points at a rotated-away file reopens the live path before writing.

Windows refuses to rename a file another process holds open, which is the normal
state of a shared log. Rotation then copies the live file to ``<log>.1`` and
truncates it in place. That is lossless because every GAIA writer takes the same
lock before it writes, and the other writers' append-mode handles keep writing at
the new end of file.

The cap is measured in bytes on disk: a record is encoded, line ending included,
before it is weighed against the room left. No file in the family exceeds the cap,
except that a single record larger than the whole cap is written alone to an empty
file rather than dropped.

Stdlib-only: ``gaia.logger`` imports this before anything else in GAIA exists.
"""

import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

#: Size cap per log file, in MB.
MAX_MB_ENV = "GAIA_LOG_MAX_MB"
#: Rotated files kept beside the live one (``gaia.log.1`` ... ``gaia.log.N``).
BACKUPS_ENV = "GAIA_LOG_BACKUPS"

DEFAULT_MAX_MB = 10
DEFAULT_BACKUPS = 3

# Copying a file this many times the cap under the lock would stall every writer.
_COPY_LIMIT_FACTOR = 2
_LOCK_TIMEOUT_S = 5.0
_RETRY_AFTER_S = 30.0
# What "\n" becomes on disk: "\r\n" on Windows, so a record weighs more than its length.
_LINESEP = os.linesep


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise ValueError(
            f"{name}={raw!r} is not a positive whole number. Set it to an integer "
            f">= 1 (default {default}), or unset it."
        )
    return value


def log_limits() -> Tuple[int, int]:
    """``(max_bytes, backup_count)`` from ``GAIA_LOG_MAX_MB`` / ``GAIA_LOG_BACKUPS``."""
    max_mb = _positive_int_env(MAX_MB_ENV, DEFAULT_MAX_MB)
    return max_mb * 1024 * 1024, _positive_int_env(BACKUPS_ENV, DEFAULT_BACKUPS)


def log_family(path) -> List[Path]:
    """The live log and its existing rotated files, newest first."""
    path = Path(path)
    family = [path] if path.is_file() else []
    return family + [entry for _, entry in sorted(_numbered_backups(path))]


def shift_backups(path, backup_count: int) -> None:
    """Rename ``path`` to ``path.1``, moving older backups up and dropping the oldest.

    Finds which backups exist with one directory scan rather than probing
    every number up to ``backup_count``: at a large backup_count that probe
    is slow enough on its own to blow another writer's lock-acquire timeout.

    Raises:
        OSError: a rename failed, typically because another process holds the
            file open on Windows.
    """
    path = Path(path)
    base = str(path)
    existing = {n for n, _ in _numbered_backups(path)}
    for i in range(backup_count - 1, 0, -1):
        if i in existing:
            os.replace(f"{base}.{i}", f"{base}.{i + 1}")
    os.replace(base, f"{base}.1")


def _numbered_backups(path: Path) -> List[Tuple[int, Path]]:
    """``(n, entry)`` for every ``path.<n>`` beside ``path``, unsorted."""
    pattern = re.compile(re.escape(path.name) + r"\.(\d+)$")
    backups = []
    try:
        for entry in path.parent.iterdir():
            match = pattern.match(entry.name)
            if match and entry.is_file():
                backups.append((int(match.group(1)), entry))
    except FileNotFoundError:
        pass
    return backups


def rotate_if_oversized(path, max_bytes: Optional[int] = None) -> bool:
    """Rotate a log some subprocess appends to, before it is reopened.

    For logs GAIA hands to a child as stdout, which no handler can rotate while
    the child runs: bounding them at every (re)start keeps them from growing
    across restarts.

    Raises:
        OSError: the file is over the cap and could not be renamed.
    """
    limit, backups = log_limits()
    max_bytes = limit if max_bytes is None else max_bytes
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        return False
    if size < max_bytes:
        return False
    shift_backups(path, backups)
    return True


class _InterProcessLock:
    """An exclusive lock on a sidecar ``.lock`` file, shared by every GAIA process.

    Two handles opened by the same process exclude each other too (``flock`` and
    ``msvcrt.locking`` both lock per open file, not per process).
    """

    def __init__(self, path: str):
        self._path = path
        self._fd: Optional[int] = None
        self._pid: Optional[int] = None

    def _ensure_open(self) -> int:
        # A forked child shares the parent's open file, and with it the lock.
        if self._fd is None or self._pid != os.getpid():
            self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
            self._pid = os.getpid()
        return self._fd

    def acquire(self, timeout: float = _LOCK_TIMEOUT_S) -> bool:
        fd = self._ensure_open()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.005)

    def release(self) -> None:
        if self._fd is None:
            return
        if sys.platform == "win32":
            import msvcrt

            os.lseek(self._fd, 0, os.SEEK_SET)
            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)

    def close(self) -> None:
        if self._fd is not None and self._pid == os.getpid():
            os.close(self._fd)
        self._fd = None


class SharedRotatingFileHandler(logging.FileHandler):
    """A ``FileHandler`` that caps its file at ``max_bytes`` x ``backup_count + 1``.

    Safe when several processes (and several handlers in one process) write the
    same path. A log already over the cap when the handler starts is rotated
    aside to ``<log>.1`` rather than deleted, so it stays available for a bug
    report until it ages out. A single record larger than ``max_bytes`` gets a
    file to itself.

    When a rotation cannot happen at all, the handler says so once on stderr and
    stops writing to the file while it is over the cap, retrying every 30 s. The
    cap holds; records logged meanwhile still reach the console handler.
    """

    def __init__(
        self,
        filename,
        max_bytes: Optional[int] = None,
        backup_count: Optional[int] = None,
        encoding: str = "utf-8",
    ):
        env_max, env_backups = log_limits()
        self.max_bytes = env_max if max_bytes is None else max_bytes
        self.backup_count = env_backups if backup_count is None else backup_count
        if self.max_bytes < 1 or self.backup_count < 1:
            raise ValueError("max_bytes and backup_count must both be >= 1")
        super().__init__(filename, mode="a", encoding=encoding, delay=True)
        self._ipc_lock = _InterProcessLock(self.baseFilename + ".lock")
        self._announced = set()
        self._paused_until = 0.0
        if not self._ipc_lock.acquire():
            self._announce(
                "lock",
                f"timed out waiting for {self.baseFilename}.lock; another GAIA "
                "process is holding it. Writing without rotation until it frees.",
            )
            self.stream = self._open()
            return
        try:
            size = self._path_size()
            if size is not None and size >= self.max_bytes:
                self._rollover(size, startup=True)
            if self.stream is None and not self._paused():
                self.stream = self._open()
        finally:
            self._ipc_lock.release()

    def _announce(self, key: str, message: str) -> None:
        if key in self._announced:
            return
        self._announced.add(key)
        print(f"[gaia] Log rotation: {message}", file=sys.stderr)

    def _paused(self) -> bool:
        return time.monotonic() < self._paused_until

    def _path_size(self) -> Optional[int]:
        try:
            return os.path.getsize(self.baseFilename)
        except FileNotFoundError:
            return None

    def _open(self):
        # Binary append: the bytes written are the bytes weighed against the cap.
        return open(self.baseFilename, "ab")

    def _encode(self, msg: str) -> bytes:
        if _LINESEP != "\n":
            msg = msg.replace("\n", _LINESEP)
        return msg.encode(self.encoding or "utf-8", self.errors or "strict")

    def _fits(self, size: int, nbytes: int) -> bool:
        return not size or size + nbytes <= self.max_bytes

    def _close_stream(self) -> None:
        if self.stream is not None:
            try:
                self.stream.flush()
            finally:
                self.stream.close()
                self.stream = None

    def _follow_live_file(self) -> None:
        """Point the stream at the current file at ``baseFilename``.

        Another process may have rotated it away (our handle then points at
        ``<log>.1``) or a user may have deleted it.
        """
        if self.stream is None:
            return
        try:
            live = os.stat(self.baseFilename)
        except FileNotFoundError:
            live = None
        mine = os.fstat(self.stream.fileno())
        if live is None or (live.st_dev, live.st_ino) != (mine.st_dev, mine.st_ino):
            self._close_stream()

    def _rollover(self, size: int, startup: bool = False) -> None:
        """Rotate the live file. Caller holds the inter-process lock."""
        self._close_stream()
        try:
            shift_backups(self.baseFilename, self.backup_count)
            rotated = True
        except OSError as rename_err:
            rotated = self._copy_truncate(size, rename_err)
        if not rotated:
            return
        self._paused_until = 0.0
        if startup:
            self._announce(
                "startup",
                f"{self.baseFilename} was {size / 1024 / 1024:.0f} MB, so it was "
                f"moved to {self.baseFilename}.1 (kept for bug reports). The log "
                f"is now capped at {self.max_bytes // (1024 * 1024)} MB x "
                f"{self.backup_count} backups.",
            )

    def _copy_truncate(self, size: int, rename_err: OSError) -> bool:
        if size > _COPY_LIMIT_FACTOR * self.max_bytes:
            return self._give_up(rename_err)
        try:
            shutil.copyfile(self.baseFilename, self.baseFilename + ".1")
            with open(self.baseFilename, "r+b") as live:
                live.truncate(0)
        except OSError as copy_err:
            return self._give_up(copy_err)
        self._announce(
            "copy",
            f"could not rename {self.baseFilename} ({rename_err}), most likely "
            "because another process has it open. Rotating by copy + truncate "
            "instead; no lines are lost.",
        )
        return True

    def _give_up(self, err: OSError) -> bool:
        self._paused_until = time.monotonic() + _RETRY_AFTER_S
        self._announce(
            "stuck",
            f"cannot rotate {self.baseFilename} ({err}). File logging is paused "
            f"while it is over {self.max_bytes // (1024 * 1024)} MB and retried "
            f"every {_RETRY_AFTER_S:.0f} s. Close whatever holds the file open, or "
            "move it aside yourself.",
        )
        return False

    def emit(self, record):
        try:
            data = self._encode(self.format(record) + self.terminator)
            if not self._ipc_lock.acquire():
                self._announce(
                    "lock",
                    f"timed out waiting for {self.baseFilename}.lock; another "
                    "GAIA process is holding it. Writing without rotation until "
                    "it frees.",
                )
                self._write_unlocked(data)
                return
            try:
                self._follow_live_file()
                size = self._path_size() or 0
                if not self._fits(size, len(data)):
                    if self._paused():
                        return
                    self._rollover(size)
                    if self._paused():
                        return
                if self.stream is None:
                    self.stream = self._open()
                self.stream.write(data)
                self.stream.flush()
            finally:
                self._ipc_lock.release()
        except RecursionError:
            raise
        except Exception:  # pylint: disable=broad-except
            # logging.Handler's contract: report through handleError, never raise.
            self.handleError(record)

    def _write_unlocked(self, data: bytes) -> None:
        # Without the lock no rotation is safe, so the cap is enforced by not writing.
        if not self._fits(self._path_size() or 0, len(data)):
            return
        if self.stream is None:
            self.stream = self._open()
        self.stream.write(data)
        self.stream.flush()

    def close(self):
        self.acquire()
        try:
            super().close()
            self._ipc_lock.close()
        finally:
            self.release()
