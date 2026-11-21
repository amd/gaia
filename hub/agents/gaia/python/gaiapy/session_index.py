# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The listing a host reads to show and reopen past stdio sessions.

The transcript a session replays to the model is ``<id>.sqlite3``
(:class:`gaia.agents.base.history.SessionHistory`). A host that wants to list
sessions or redraw one cannot read that without a SQLite driver, and cannot
ask this process about a session it is not running. So, beside it, this
writes two plain files:

* ``<id>.json`` — title, kind, status, timestamps and the owning pid,
  replaced atomically on every change.
* ``<id>.jsonl`` — one line per finished turn: what the user asked and what
  came back, for the host to redraw on resume.

Written only when the host passes ``--session-kind``: a launcher that never
lists sessions (the messaging bridge, a one-shot) leaves no index behind.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

#: Version of the ``<id>.json`` layout. The Go reader refuses any other.
INDEX_VERSION = 1

STATUS_RUNNING = "running"
#: Parked on a permission prompt: nothing moves until the user answers.
STATUS_WAITING = "waiting"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUSES = (STATUS_RUNNING, STATUS_WAITING, STATUS_DONE, STATUS_FAILED)

#: An interactive chat. Every other kind (``eval``, ``scheduled`` …) is
#: hidden from a host's default list.
KIND_CHAT = "chat"
_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

#: Longest title kept; the first line of the first question, trimmed.
TITLE_MAX = 80


def check_kind(kind: str) -> str:
    """Return *kind* if it is a usable session kind, else raise naming the rule."""
    if not _KIND_RE.match(kind or ""):
        raise ValueError(
            f"invalid --session-kind {kind!r}: use a short lowercase word such as "
            f"'{KIND_CHAT}', 'eval' or 'scheduled'"
        )
    return kind


def title_from(query: str) -> str:
    """The first non-blank line of *query*, cut to :data:`TITLE_MAX`."""
    for line in str(query).splitlines():
        line = " ".join(line.split())
        if line:
            return line if len(line) <= TITLE_MAX else line[: TITLE_MAX - 1] + "…"
    return ""


#: How long a write or the session lock waits out a host's brief hold: on
#: Windows a host reading ``<id>.json`` makes ``os.replace`` fail, and a host
#: probing ``<id>.lock`` holds it for an instant.
REPLACE_ATTEMPTS = 10
REPLACE_PAUSE_S = 0.02


def _replace(src: str, dst: Path) -> None:
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_PAUSE_S)


def _write_private(path: Path, data: str) -> None:
    """Replace *path* with *data* atomically; the file is readable by its owner only."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


#: A listed session's file stem: what the TUI mints and is willing to reopen.
_ID_RE = re.compile(r"^[0-9a-f]{8,64}$")


def _hold_lock(path: Path):
    """Take an exclusive lock on *path* for this process's life, or None if held.

    The lock, not the pid, is what tells a host the session is open: it dies
    with the process however that ends, and a reused pid cannot fake it. One
    byte at offset 0, the range the TUI's own try-lock tests.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+b")  # noqa: SIM115 - held open until the process exits
    # A host checking whether the session is open takes and drops this lock;
    # waiting out that instant keeps the probe from turning a free session away.
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if attempt == REPLACE_ATTEMPTS - 1:
                f.close()
                return None
            time.sleep(REPLACE_PAUSE_S)
    return None


class SessionOpenElsewhere(RuntimeError):
    """Another live process already holds this session."""


class SessionIndex:
    """Keeps ``<id>.json`` and ``<id>.jsonl`` current for one session.

    Reopening an existing session keeps its title, kind and creation time, and
    claims it at once so no other window can open it too.
    """

    def __init__(self, history_path: str, kind: str) -> None:
        history = Path(history_path)
        self.session_id = history.stem
        if not _ID_RE.match(self.session_id):
            raise ValueError(
                f"--session-kind needs a --history-file named <id>.sqlite3 with a "
                f"hex id, so a host can list and reopen it; got {history.name!r}"
            )
        check_kind(kind)
        self.meta_path = history.with_suffix(".json")
        self.transcript_path = history.with_suffix(".jsonl")
        self._lock = threading.Lock()
        self._lock_file = _hold_lock(history.with_suffix(".lock"))
        if self._lock_file is None:
            raise SessionOpenElsewhere(
                f"session {self.session_id} is open in another GAIA window. "
                "Close it there first, then reopen it here."
            )
        now = time.time()
        self._meta: Dict[str, Any] = {
            "version": INDEX_VERSION,
            "id": self.session_id,
            "title": "",
            "kind": kind,
            "status": STATUS_DONE,
            "created_at": now,
            "updated_at": now,
            "pid": os.getpid(),
            "turns": 0,
        }
        self._query = ""
        self._in_turn = False
        if self.meta_path.exists():
            try:
                saved = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._release()
                raise RuntimeError(
                    f"cannot reopen session {self.session_id}: its index "
                    f"{self.meta_path} is unreadable ({exc}). Delete it to start "
                    "the session's listing over; the transcript itself is untouched."
                ) from exc
            for key in ("title", "kind", "created_at", "turns"):
                if key in saved:
                    self._meta[key] = saved[key]
            # A turn still marked in progress died with its process.
            status = saved.get("status")
            self._save(status=STATUS_DONE if status == STATUS_DONE else STATUS_FAILED)

    @property
    def meta(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._meta)

    def _save(self, pid: Optional[int] = None, **changes: Any) -> None:
        self._meta.update(
            changes, updated_at=time.time(), pid=os.getpid() if pid is None else pid
        )
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)
        _write_private(self.meta_path, json.dumps(self._meta, ensure_ascii=False))

    def _release(self) -> None:
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def turn_started(self, query: str) -> None:
        """A real question is about to run."""
        with self._lock:
            self._query = str(query)
            self._in_turn = True
            changes: Dict[str, Any] = {"status": STATUS_RUNNING}
            if not self._meta["title"]:
                changes["title"] = title_from(query)
            self._save(**changes)

    def observe(self, event: Dict[str, Any]) -> None:
        """Track the turn's state from one event it is about to write."""
        kind = event.get("type")
        with self._lock:
            if not self._in_turn:
                return
            if kind == "needs_confirmation":
                self._save(status=STATUS_WAITING)
            elif kind == "final":
                self._finish(STATUS_DONE, {"answer": str(event.get("answer") or "")})
            elif kind == "error":
                self._finish(STATUS_FAILED, {"error": str(event.get("detail") or "")})
            elif self._meta["status"] == STATUS_WAITING:
                self._save(status=STATUS_RUNNING)

    def _finish(self, status: str, outcome: Dict[str, Any]) -> None:
        self._in_turn = False
        line = json.dumps({"user": self._query, **outcome}, ensure_ascii=False)
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self.transcript_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        self._save(status=status, turns=int(self._meta["turns"]) + 1)

    def cleared(self) -> None:
        """``/clear`` emptied the conversation: so is its listing."""
        with self._lock:
            self._in_turn = False
            _write_private(self.transcript_path, "")
            self._save(title="", turns=0, status=STATUS_DONE)

    def closed(self) -> None:
        """The process is exiting: give the session up.

        The pid is cleared for readers that show it; the lock is what marks
        the session closed. Nothing is written for a session with no turns.
        """
        with self._lock:
            try:
                if self.meta_path.exists():
                    self._save(pid=0)
            finally:
                self._release()


def report_failure(index: SessionIndex, exc: OSError, out=None) -> None:
    """Say a listing write failed: in the log, and on the wire when there is one.

    The listing is a convenience for hosts: failing to write it never costs
    the user a turn, and never goes unsaid.
    """
    logger.error("Could not update session listing %s: %s", index.meta_path, exc)
    if out is not None:
        notice = {
            "type": "status",
            "message": f"Could not save this session's listing ({exc}); "
            "/sessions may show it out of date.",
        }
        out.write(json.dumps(notice, ensure_ascii=False) + "\n")
        out.flush()


def guarded(index: SessionIndex, action, out=None) -> None:
    """Run one listing update, reporting an OS failure instead of raising it."""
    try:
        action()
    except OSError as exc:
        report_failure(index, exc, out)


class IndexedOut:
    """A stdout stand-in that shows each outgoing event to a :class:`SessionIndex`.

    The index is updated before the line is written, so a host that has
    already gone still leaves the turn's outcome on disk. A listing that
    cannot be saved is reported, never by losing the event it was tracking:
    a lost terminal event hangs the host.
    """

    def __init__(self, out, index: SessionIndex) -> None:
        self._out = out
        self._index = index

    def write(self, data: str) -> int:
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                logger.warning("Session listing skipped a non-JSON line on the wire")
                continue
            guarded(self._index, partial(self._index.observe, event), self._out)
        return self._out.write(data)

    def flush(self) -> None:
        self._out.flush()


def open_index(history_path: str, kind: Optional[str]) -> Optional[SessionIndex]:
    """The index for this launch, or None when the host asked for none."""
    return SessionIndex(history_path, kind) if kind else None
