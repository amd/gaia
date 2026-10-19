# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Copies of the files a turn changes, so the turn can be undone.

Every agent tool that writes, edits, creates or deletes a file calls
:func:`before_change` first. While a turn is being recorded
(:func:`recording_turn`), the first call for each path saves the file as it
was — or notes that it did not exist — under
``<gaia_home>/snapshots/<session>/<turn>/``. When the turn ends, the state it
left each file in is saved too, so a later undo can tell whether someone has
changed the file since.

What a turn cannot account for — a shell command or a Python snippet that may
have written files on its own — is recorded with :func:`note_untracked`, and
the turn is reported as "undo may be incomplete".

A restore is all or nothing: every file is checked before any is touched, and
a write that fails part-way puts back the files already restored.
"""

from __future__ import annotations

import contextlib
import contextvars
import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

MANIFEST = "manifest.json"
MANIFEST_VERSION = 1

#: Default retention; the user changes both in Settings (``gaia config``).
DEFAULT_RETENTION_DAYS = 7
DEFAULT_MAX_MB = 500

#: A review diff longer than this is cut, and says so.
MAX_DIFF_CHARS = 200_000

#: Files larger than this are not read for a review diff.
MAX_DIFF_BYTES = 2 * 1024 * 1024

#: A turn's end state is copied only up to this size; larger files keep a checksum.
MAX_AFTER_COPY_BYTES = 8 * 1024 * 1024

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class SnapshotError(RuntimeError):
    """A snapshot could not be taken, read, or restored exactly."""


class SnapshotNotFound(SnapshotError):
    """No saved changes exist for that turn (never recorded, or expired)."""


class RestoreConflict(SnapshotError):
    """A file changed after the turn, so undoing it would lose that change."""

    def __init__(self, message: str, conflicts: List[Dict[str, str]]):
        super().__init__(message)
        self.conflicts = conflicts


def snapshots_root() -> Path:
    """Where every session's snapshots live: ``<gaia_home>/snapshots``."""
    from gaia.config import gaia_home

    return gaia_home() / "snapshots"


def _check_id(kind: str, value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.match(value) or value in (".", ".."):
        raise SnapshotError(f"Invalid {kind} {value!r}.")
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _missing_parents(path: Path) -> List[str]:
    """Ancestors of *path* that do not exist yet, outermost first."""
    missing: List[str] = []
    parent = path.parent
    while parent != parent.parent and not parent.exists():
        missing.append(str(parent))
        parent = parent.parent
    return list(reversed(missing))


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

_ACTIVE: contextvars.ContextVar[Optional["TurnRecorder"]] = contextvars.ContextVar(
    "gaia_file_snapshot_turn", default=None
)


class TurnRecorder:
    """Records the prior state of every file one turn changes."""

    def __init__(self, session_id: str, turn_id: str, root: Path):
        self.session_id = _check_id("session id", session_id)
        self.turn_id = _check_id("turn id", turn_id)
        self.dir = root / session_id / turn_id
        self._lock = threading.Lock()
        self._manifest: Dict[str, Any] = {
            "version": MANIFEST_VERSION,
            "session_id": session_id,
            "turn_id": turn_id,
            "message_id": None,
            "started_at": _now_iso(),
            "finished_at": None,
            "restored_at": None,
            "files": [],
            "untracked": [],
        }
        self._index: Dict[str, Dict[str, Any]] = {}
        self._closed = False

    @property
    def has_changes(self) -> bool:
        return bool(self._manifest["files"] or self._manifest["untracked"])

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(self.dir / MANIFEST, self._manifest)

    def capture(self, path: os.PathLike | str, tool: Optional[str] = None) -> None:
        """Save *path* as it is now, unless this turn already saved it."""
        # realpath: one file reached through a link or a short name is one entry.
        target = Path(os.path.realpath(os.fspath(path)))
        key = os.path.normcase(str(target))
        with self._lock:
            if self._closed:
                self._note_late("late_write", str(target))
                return
            if key in self._index:
                return
            entry: Dict[str, Any] = {
                "path": str(target),
                "tool": tool,
                "existed": False,
                "before_blob": None,
                "before_sha256": None,
                "before_size": None,
                "mode": None,
                "created_dirs": [],
                "after_exists": None,
                "after_blob": None,
                "after_sha256": None,
            }
            try:
                if target.is_dir():
                    raise SnapshotError(
                        f"{target} is a folder; only files can be saved for undo."
                    )
                if target.exists():
                    blob = f"{len(self._manifest['files']):04d}.before"
                    (self.dir / "blobs").mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(target, self.dir / "blobs" / blob)
                    entry.update(
                        existed=True,
                        before_blob=blob,
                        before_sha256=_sha256_file(self.dir / "blobs" / blob),
                        before_size=target.stat().st_size,
                        mode=stat.S_IMODE(target.stat().st_mode),
                    )
                else:
                    entry["created_dirs"] = _missing_parents(target)
                self._manifest["files"].append(entry)
                self._index[key] = entry
                self._save()
            except OSError as e:
                raise SnapshotError(
                    f"Could not save a copy of {target} before changing it, so "
                    f"it was left unchanged: {e}. Check free space and "
                    f"permissions under {self.dir.parent.parent}."
                ) from e

    def note_untracked(
        self, kind: str, detail: str, paths: Optional[List[str]] = None
    ) -> None:
        """Record work that may have changed files without a copy being saved."""
        with self._lock:
            if self._closed:
                self._note_late(kind, detail)
                return
            self._manifest["untracked"].append(
                {"kind": kind, "detail": detail[:500], "paths": list(paths or [])}
            )
            self._save()

    def _note_late(self, kind: str, detail: str) -> None:
        """A tool abandoned at its time limit wrote after the turn ended.

        Edits the manifest on disk, never the in-memory copy, so an undo that
        already happened is not forgotten.
        """
        path = self.dir / MANIFEST
        if not path.is_file():
            return
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["untracked"].append(
            {
                "kind": kind,
                "detail": f"after the turn ended: {detail[:480]}",
                "paths": [],
            }
        )
        _write_json_atomic(path, manifest)

    def finish(self) -> None:
        """Save the state the turn left each file in; drop an empty turn."""
        with self._lock:
            self._closed = True
            if not self.has_changes:
                return
            (self.dir / "blobs").mkdir(parents=True, exist_ok=True)
            for i, entry in enumerate(self._manifest["files"]):
                target = Path(entry["path"])
                if target.is_file():
                    size = target.stat().st_size
                    blob = None
                    if size <= MAX_AFTER_COPY_BYTES:
                        blob = f"{i:04d}.after"
                        shutil.copyfile(target, self.dir / "blobs" / blob)
                    entry.update(
                        after_exists=True,
                        after_blob=blob,
                        after_size=size,
                        after_sha256=_sha256_file(
                            self.dir / "blobs" / blob if blob else target
                        ),
                    )
                else:
                    entry.update(
                        after_exists=False,
                        after_blob=None,
                        after_size=None,
                        after_sha256=None,
                    )
            self._manifest["finished_at"] = _now_iso()
            self._save()

    def link_message(self, message_id: int) -> None:
        """Tie this turn to the assistant message that answered it."""
        with self._lock:
            if not self.has_changes:
                return
            self._manifest["message_id"] = message_id
            path = self.dir / MANIFEST
            if self._closed and path.is_file():
                # Re-read: a tool abandoned at its time limit may have noted a late write.
                manifest = json.loads(path.read_text(encoding="utf-8"))
                manifest["message_id"] = message_id
                _write_json_atomic(path, manifest)
                return
            self._save()


@contextlib.contextmanager
def recording_turn(
    session_id: str,
    turn_id: Optional[str] = None,
    root: Optional[Path] = None,
) -> Iterator[TurnRecorder]:
    """Record every file change made inside this block as one undoable turn.

    Tools reach the recorder through a context variable, so a tool body run on
    a worker thread sees it only when that thread copies this context (the
    agent's tool runner does).
    """
    turn_id = turn_id or f"{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    recorder = TurnRecorder(session_id, turn_id, root or snapshots_root())
    token = _ACTIVE.set(recorder)
    try:
        yield recorder
    finally:
        _ACTIVE.reset(token)
        try:
            recorder.finish()
        except OSError as e:
            raise SnapshotError(
                f"Could not save the end state of turn {turn_id}: {e}. Undo for "
                "this turn may refuse; check free space under "
                f"{recorder.dir.parent.parent}."
            ) from e


def active_recorder() -> Optional[TurnRecorder]:
    return _ACTIVE.get()


def before_change(path: os.PathLike | str, tool: Optional[str] = None) -> None:
    """Call before writing, replacing, creating or deleting *path*.

    Outside a recorded turn this does nothing. Inside one it raises
    :class:`SnapshotError` when the copy cannot be saved; the caller must
    then not make the change.
    """
    recorder = _ACTIVE.get()
    if recorder is not None:
        recorder.capture(path, tool)


def note_untracked(kind: str, detail: str, paths: Optional[List[str]] = None) -> None:
    """Mark the current turn's undo as possibly incomplete."""
    recorder = _ACTIVE.get()
    if recorder is not None:
        recorder.note_untracked(kind, detail, paths)


# ---------------------------------------------------------------------------
# Reading, reviewing, restoring
# ---------------------------------------------------------------------------


@dataclass
class _Turn:
    dir: Path
    manifest: Dict[str, Any]


def _turn_dir(session_id: str, turn_id: str, root: Optional[Path]) -> Path:
    return (
        (root or snapshots_root())
        / _check_id("session id", session_id)
        / _check_id("turn id", turn_id)
    )


def _load(session_id: str, turn_id: str, root: Optional[Path] = None) -> _Turn:
    turn_dir = _turn_dir(session_id, turn_id, root)
    path = turn_dir / MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SnapshotNotFound(
            f"No saved changes for turn {turn_id} in session {session_id}; "
            "it may have been removed by the retention limit in Settings."
        ) from e
    except (OSError, json.JSONDecodeError) as e:
        raise SnapshotError(f"Cannot read {path}: {e}") from e
    if manifest.get("version") != MANIFEST_VERSION:
        raise SnapshotError(
            f"{path} was written by a different GAIA version "
            f"(format {manifest.get('version')!r}); it cannot be restored here."
        )
    return _Turn(turn_dir, manifest)


def _change_kind(entry: Dict[str, Any]) -> str:
    after = entry.get("after_exists")
    if after is None:
        return "pending"
    if entry["existed"] and after:
        return (
            "unchanged" if entry["before_sha256"] == entry["after_sha256"] else "edited"
        )
    if entry["existed"]:
        return "deleted"
    return "created" if after else "unchanged"


def _summary(turn: _Turn) -> Dict[str, Any]:
    m = turn.manifest
    files = [
        {"path": e["path"], "change": _change_kind(e), "tool": e.get("tool")}
        for e in m["files"]
    ]
    changed = [f for f in files if f["change"] != "unchanged"]
    return {
        "turn_id": m["turn_id"],
        "session_id": m["session_id"],
        "message_id": m.get("message_id"),
        "started_at": m["started_at"],
        "finished_at": m.get("finished_at"),
        "restored_at": m.get("restored_at"),
        "files": changed,
        "untracked": m.get("untracked", []),
        "incomplete": bool(m.get("untracked")),
    }


def list_turns(session_id: str, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every recorded turn in *session_id*, oldest first."""
    session_dir = (root or snapshots_root()) / _check_id("session id", session_id)
    if not session_dir.is_dir():
        return []
    turns = []
    for turn_dir in sorted(session_dir.iterdir()):
        if not (turn_dir / MANIFEST).is_file():
            continue
        turns.append(_summary(_load(session_id, turn_dir.name, root)))
    return turns


def _read_blob(turn: _Turn, blob: Optional[str]) -> bytes:
    if blob is None:
        return b""
    return (turn.dir / "blobs" / blob).read_bytes()


def _as_text(data: bytes) -> Optional[str]:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def diff_turn(
    session_id: str, turn_id: str, root: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """The change the turn made to each file, as a unified diff."""
    turn = _load(session_id, turn_id, root)
    out = []
    for entry in turn.manifest["files"]:
        kind = _change_kind(entry)
        if kind == "unchanged":
            continue
        item: Dict[str, Any] = {
            "path": entry["path"],
            "change": kind,
            "binary": False,
            "too_large": False,
            "truncated": False,
            "unified": "",
            "before_size": entry.get("before_size") or 0,
            "after_size": entry.get("after_size") or 0,
        }
        uncopied = entry.get("after_exists") and not entry.get("after_blob")
        if uncopied or max(item["before_size"], item["after_size"]) > MAX_DIFF_BYTES:
            item["too_large"] = True
            out.append(item)
            continue
        before = _read_blob(turn, entry.get("before_blob"))
        after = _read_blob(turn, entry.get("after_blob"))
        before_text, after_text = _as_text(before), _as_text(after)
        if before_text is None or after_text is None:
            item["binary"] = True
        else:
            name = Path(entry["path"]).name
            unified = "".join(
                difflib.unified_diff(
                    before_text.splitlines(keepends=True),
                    after_text.splitlines(keepends=True),
                    fromfile=f"a/{name}" if entry["existed"] else "/dev/null",
                    tofile=f"b/{name}" if entry.get("after_exists") else "/dev/null",
                )
            )
            if len(unified) > MAX_DIFF_CHARS:
                unified = unified[:MAX_DIFF_CHARS]
                item["truncated"] = True
            item["unified"] = unified
        out.append(item)
    return out


def _current_matches_after(entry: Dict[str, Any]) -> Optional[str]:
    """Why the file is no longer as the turn left it, or None when it is."""
    target = Path(entry["path"])
    if entry.get("after_exists"):
        if not target.is_file():
            return "deleted after this turn"
        if _sha256_file(target) != entry["after_sha256"]:
            return "changed after this turn"
    elif target.exists():
        return "created again after this turn"
    return None


def _restore_file(target: Path, source: Path, mode: Optional[int]) -> None:
    """Replace *target* with a copy of *source* in one atomic rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.gaia-undo-{uuid.uuid4().hex[:8]}")
    try:
        shutil.copyfile(source, tmp)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def restore_turn(
    session_id: str,
    turn_id: str,
    force: bool = False,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Put every file the turn changed back as it was before the turn.

    Raises :class:`RestoreConflict` when a file changed after the turn (pass
    ``force`` to overwrite those changes) and :class:`SnapshotError` when the
    saved copies cannot reproduce the old files exactly. Nothing on disk is
    changed in either case.
    """
    turn = _load(session_id, turn_id, root)
    m = turn.manifest
    if m.get("restored_at"):
        raise SnapshotError(f"This turn was already undone at {m['restored_at']}.")
    if not m.get("finished_at"):
        raise SnapshotError(
            "This turn did not finish (GAIA stopped while it was running), so "
            "GAIA cannot tell which file states are the turn's own. Restore the "
            "files by hand from the copies in " + str(turn.dir / "blobs") + "."
        )

    entries = [e for e in m["files"] if _change_kind(e) != "unchanged"]

    # Every copy must be intact before any file is touched.
    for entry in entries:
        if not entry["existed"]:
            continue
        blob = turn.dir / "blobs" / entry["before_blob"]
        try:
            intact = _sha256_file(blob) == entry["before_sha256"]
        except OSError as e:
            raise SnapshotError(
                f"The saved copy of {entry['path']} is missing ({e}); nothing "
                "was restored."
            ) from e
        if not intact:
            raise SnapshotError(
                f"The saved copy of {entry['path']} is damaged (checksum "
                "mismatch); nothing was restored."
            )

    if not force:
        conflicts = []
        for entry in entries:
            reason = _current_matches_after(entry)
            if reason:
                conflicts.append({"path": entry["path"], "reason": reason})
        if conflicts:
            names = ", ".join(c["path"] for c in conflicts[:5])
            more = f" and {len(conflicts) - 5} more" if len(conflicts) > 5 else ""
            raise RestoreConflict(
                f"{len(conflicts)} file(s) changed after this turn: {names}{more}. "
                "Undoing it would overwrite those changes. Nothing was restored.",
                conflicts,
            )

    # Copy what is on disk now, so a failure part-way can be rolled back.
    rollback_dir = turn.dir / "rollback"
    shutil.rmtree(rollback_dir, ignore_errors=True)
    rollback_dir.mkdir(parents=True)
    rollback: List[tuple] = []
    try:
        for i, entry in enumerate(entries):
            target = Path(entry["path"])
            saved = None
            if target.is_file():
                saved = rollback_dir / f"{i:04d}"
                shutil.copyfile(target, saved)
            rollback.append((target, saved, entry))
    except OSError as e:
        shutil.rmtree(rollback_dir, ignore_errors=True)
        raise SnapshotError(
            f"Could not keep a safety copy of {target} before undoing ({e}); "
            "nothing was restored."
        ) from e

    done: List[tuple] = []
    failing: Optional[Path] = None
    try:
        for target, saved, entry in rollback:
            failing = target
            # Counted as done before the write, so a failed write is rolled back too.
            done.append((target, saved, entry))
            if entry["existed"]:
                _restore_file(
                    target, turn.dir / "blobs" / entry["before_blob"], entry.get("mode")
                )
                if _sha256_file(target) != entry["before_sha256"]:
                    raise SnapshotError(
                        f"{target} does not match its saved copy after writing it"
                    )
            elif target.exists():
                target.unlink()
    except (OSError, SnapshotError) as e:
        failed_back = []
        for target, saved, entry in reversed(done):
            try:
                if saved is None:
                    target.unlink(missing_ok=True)
                else:
                    _restore_file(target, saved, entry.get("mode"))
            except OSError as back_err:
                failed_back.append(f"{target} ({back_err})")
        if failed_back:
            raise SnapshotError(
                f"Undo failed on {failing}: {e}. Putting the files back as they "
                "were before the undo also failed for: "
                + "; ".join(failed_back)
                + f". Copies of them are in {rollback_dir}."
            ) from e
        shutil.rmtree(rollback_dir, ignore_errors=True)
        raise SnapshotError(
            f"Undo failed on {failing}: {e}. Every file was put back as it was, "
            "so nothing changed."
        ) from e
    shutil.rmtree(rollback_dir, ignore_errors=True)

    kept_dirs = []
    created = {d for e in entries for d in (e.get("created_dirs") or [])}
    for folder in sorted(created, key=lambda d: len(Path(d).parts), reverse=True):
        path = Path(folder)
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            kept_dirs.append(folder)

    m["restored_at"] = _now_iso()
    _write_json_atomic(turn.dir / MANIFEST, m)
    return {
        "turn_id": turn_id,
        "restored": [e["path"] for e in entries if e["existed"]],
        "removed": [e["path"] for e in entries if not e["existed"]],
        "kept_folders": sorted(set(kept_dirs)),
        "untracked": m.get("untracked", []),
        "incomplete": bool(m.get("untracked")),
    }


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def usage(root: Optional[Path] = None) -> Dict[str, Any]:
    base = root or snapshots_root()
    turns = 0
    if base.is_dir():
        for session_dir in base.iterdir():
            if session_dir.is_dir():
                turns += sum(1 for t in session_dir.iterdir() if t.is_dir())
    return {
        "path": str(base),
        "bytes": _dir_size(base) if base.is_dir() else 0,
        "turns": turns,
    }


def _all_turn_dirs(base: Path) -> List[tuple]:
    out = []
    for session_dir in base.iterdir():
        if not session_dir.is_dir():
            continue
        for turn_dir in session_dir.iterdir():
            manifest = turn_dir / MANIFEST
            if manifest.is_file():
                out.append((manifest.stat().st_mtime, turn_dir))
    return sorted(out)


def _finished(turn_dir: Path) -> bool:
    try:
        manifest = json.loads((turn_dir / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SnapshotError(f"Cannot read {turn_dir / MANIFEST}: {e}") from e
    return bool(manifest.get("finished_at"))


def prune(
    retention_days: int,
    max_bytes: int,
    root: Optional[Path] = None,
    keep: Optional[Path] = None,
) -> Dict[str, Any]:
    """Drop turns older than *retention_days*, then the oldest past *max_bytes*.

    *keep* (default: the newest turn) is never dropped by the size limit, so
    the latest turn stays undoable even when it alone is over the limit. Nor is
    a turn still running in another chat; a crashed one ages out by time.
    """
    base = root or snapshots_root()
    if not base.is_dir():
        return {"removed": 0, "bytes": 0}
    cutoff = time.time() - retention_days * 86400
    removed = 0
    turns = _all_turn_dirs(base)
    if keep is None and turns:
        keep = turns[-1][1]
    sizes = {d: _dir_size(d) for _, d in turns}
    total = sum(sizes.values())
    for mtime, turn_dir in turns:
        expired = mtime < cutoff
        over = total > max_bytes and turn_dir != keep and _finished(turn_dir)
        if not (expired or over):
            continue
        shutil.rmtree(turn_dir)
        total -= sizes[turn_dir]
        removed += 1
    for session_dir in base.iterdir():
        if session_dir.is_dir() and not any(session_dir.iterdir()):
            session_dir.rmdir()
    return {"removed": removed, "bytes": total}


def delete_session(session_id: str, root: Optional[Path] = None) -> None:
    """Remove every saved copy for a deleted chat."""
    session_dir = (root or snapshots_root()) / _check_id("session id", session_id)
    if session_dir.is_dir():
        shutil.rmtree(session_dir)
