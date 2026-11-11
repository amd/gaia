# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Session-scoped exact tool output with bounded, explicit continuation reads."""

import hashlib
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Optional, Tuple
from uuid import uuid4

from gaia.logger import get_logger

logger = get_logger(__name__)

#: Most characters one ``read`` returns; a longer span continues at ``next_offset``.
PAGE_CHARS = 8000

#: Serializes archive writes: Windows refuses to replace a file another chat reads.
_WRITE_LOCK = threading.Lock()

#: The only names ``archive_dir`` ever serves: never a path, never another file.
_RESULT_HANDLE = re.compile(r"output_[0-9a-f]{32}")


#: Run folders untouched this long are removed when an agent with eviction starts.
RUN_RETENTION_DAYS = 7


def sweep_old_runs(runs_dir: Path, max_age_days: int = RUN_RETENTION_DAYS) -> int:
    """Delete run folders whose newest file is older than *max_age_days*; return how many."""
    if not runs_dir.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for run in runs_dir.iterdir():
        if not run.is_dir():
            continue
        newest = max(
            (p.stat().st_mtime for p in run.rglob("*")), default=run.stat().st_mtime
        )
        if newest >= cutoff:
            continue
        try:
            shutil.rmtree(run)
        except OSError as e:
            logger.warning("Could not remove old run archive %s: %s", run, e)
            continue
        removed += 1
    if removed:
        logger.info(
            "Removed %d run archive(s) older than %d days", removed, max_age_days
        )
    return removed


def _encode(text: str) -> bytes:
    # Leniently decoded binary output carries lone surrogates; keep them exactly.
    return text.encode("utf-8", "surrogatepass")


def _result_handle(data: bytes) -> str:
    return "output_" + hashlib.sha256(data).hexdigest()[:32]


class ArtifactStore:
    """Retain exact text for one agent; never resolve another session's handle."""

    def __init__(
        self, max_bytes=64 * 1024 * 1024, ttl=3600, archive_dir: Optional[Path] = None
    ):
        self.max_bytes = max_bytes
        self.ttl = ttl
        #: Where ``put_result`` keeps whole results on disk; ``None`` keeps them in memory.
        self.archive_dir = Path(archive_dir) if archive_dir is not None else None
        self._items = {}
        #: handle -> [(offset, length), ...] for index entries 1..n.
        self._indexes = {}
        #: Handles holding one whole tool result, which a bare ``read`` pages from 0.
        self._whole = set()
        #: Handles this store has written to ``archive_dir``, verified then.
        self._written = set()
        self._lock = threading.Lock()

    def put(self, text: str) -> str:
        size = len(text.encode("utf-8"))
        with self._lock:
            now = time.monotonic()
            self._items = {
                k: v for k, v in self._items.items() if now - v[0] < self.ttl
            }
            self._indexes = {k: v for k, v in self._indexes.items() if k in self._items}
            if size + sum(v[2] for v in self._items.values()) > self.max_bytes:
                raise ValueError(
                    "Tool output archive is full (64 MiB default); request smaller output or start a new session."
                )
            handle = "output_" + uuid4().hex
            self._items[handle] = (now, text, size)
            return handle

    def put_result(self, text: str) -> str:
        """Archive a whole tool result put away from the context.

        The handle is a hash of the text, so the same result always gets the
        same handle and a byte-identical stub, in any process; that keeps the
        prompt cache. With ``archive_dir`` the text is also written there, so
        the handle outlives ``ttl`` and the process.
        """
        data = _encode(text)
        handle = _result_handle(data)
        if self.archive_dir is not None:
            self._write(handle, data)
        with self._lock:
            now = time.monotonic()
            item = self._items.get(handle)
            if item is None:
                self._items = {
                    k: v for k, v in self._items.items() if now - v[0] < self.ttl
                }
                used = sum(v[2] for v in self._items.values())
                if self.archive_dir is None and len(data) + used > self.max_bytes:
                    raise ValueError(
                        "Tool output archive is full (64 MiB default); request smaller output or start a new session."
                    )
            self._items[handle] = (now, text, len(data))
            self._indexes[handle] = [(0, len(text))]
            self._whole.add(handle)
            self._shed(keep=handle)
        return handle

    def _write(self, handle: str, data: bytes) -> None:
        """Put *data* at ``archive_dir/<handle>.txt``, replacing a file that differs."""
        path = self.archive_dir / f"{handle}.txt"
        try:
            with _WRITE_LOCK:
                if handle in self._written and path.is_file():
                    return
                if path.is_file() and path.read_bytes() == data:
                    self._written.add(handle)
                    return
                self.archive_dir.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f"{handle}.{uuid4().hex}.tmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
                self._written.add(handle)
        except OSError as e:
            raise ValueError(
                f"Cannot archive tool output under {self.archive_dir}: {e}. Free disk "
                "space or make the folder writable; the result stays in the prompt."
            ) from e

    def _shed(self, keep: str) -> None:
        """Drop the oldest in-memory copies of results the disk holds. Lock held."""
        if self.archive_dir is None:
            return
        used = sum(v[2] for v in self._items.values())
        for handle, item in sorted(self._items.items(), key=lambda kv: kv[1][0]):
            if used <= self.max_bytes:
                return
            if handle == keep or handle not in self._whole:
                continue
            del self._items[handle]
            used -= item[2]

    def _revive(self, handle) -> None:
        """Reload a whole result from ``archive_dir`` when memory has let it go. Lock held."""
        if self.archive_dir is None or not isinstance(handle, str):
            return
        item = self._items.get(handle)
        if item is not None and time.monotonic() - item[0] < self.ttl:
            return
        if not _RESULT_HANDLE.fullmatch(handle):
            return
        path = self.archive_dir / f"{handle}.txt"
        if not path.is_file():
            return
        with _WRITE_LOCK:
            data = path.read_bytes()
        try:
            text = data.decode("utf-8", "surrogatepass")
        except UnicodeDecodeError:
            text = None
        if text is None or _result_handle(data) != handle:
            raise ValueError(
                f"The archived output {handle} at {path} does not match its handle: "
                "the file was modified or corrupted. Re-run the tool for a fresh copy."
            )
        self._items[handle] = (time.monotonic(), text, len(data))
        self._indexes[handle] = [(0, len(text))]
        self._whole.add(handle)
        self._shed(keep=handle)

    def touch(self, handle: str) -> None:
        """Restart a live handle's clock: a new stub now depends on it."""
        with self._lock:
            self._revive(handle)
            item = self._items.get(handle)
            if item is None or time.monotonic() - item[0] >= self.ttl:
                raise ValueError(f"Unknown or expired output handle {handle}.")
            self._items[handle] = (time.monotonic(), item[1], item[2])

    def set_index(self, handle: str, entries) -> None:
        """Record a handle's index so ``read(entry=n)`` returns entry ``n``."""
        with self._lock:
            if handle not in self._items:
                raise ValueError(f"Unknown output handle {handle}; cannot index it.")
            self._indexes[handle] = [(e["offset"], e["length"]) for e in entries]

    def has(self, handle) -> bool:
        """Whether ``handle`` names live output in this store."""
        with self._lock:
            try:
                self._revive(handle)
            except ValueError as e:
                logger.warning("%s", e)
                return False
            item = self._items.get(handle) if isinstance(handle, str) else None
            return item is not None and time.monotonic() - item[0] < self.ttl

    def text(self, handle: str) -> str:
        """The whole archived text, for indexing it; the model pages via ``read``."""
        with self._lock:
            self._revive(handle)
            item = self._items.get(handle)
            if item is None or time.monotonic() - item[0] >= self.ttl:
                raise ValueError(f"Unknown or expired output handle {handle}.")
            return item[1]

    def read(
        self,
        handle: str,
        offset: int = 0,
        limit: Optional[int] = None,
        entry: Optional[int] = None,
    ) -> dict:
        """A page of archived text: index entry ``entry``, or ``offset``/``limit``.

        ``limit`` defaults to the entry's length, or 2000 without an entry. A
        page holds at most ``PAGE_CHARS``; ``remaining`` and ``next_offset``
        say where the rest of the requested span continues.
        """
        with self._lock:
            self._revive(handle)
        if entry is None and not offset and limit is None and handle in self._whole:
            # Small models drop the stub's entry=1; a whole result still comes back.
            entry = 1
        if entry is not None:
            if offset:
                raise ValueError("Pass entry or offset, not both.")
            offset, length = self._entry_span(handle, entry)
            limit = length if limit is None else limit
        elif limit is None:
            limit = 2000
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
        ):
            raise ValueError(
                "offset must be a nonnegative character index; limit must be a "
                "positive character count"
            )
        with self._lock:
            item = self._items.get(handle)
            if item is None:
                raise ValueError(
                    f"Unknown output handle {handle}; handles belong to the producing agent session."
                )
            created, text, _ = item
            if time.monotonic() - created >= self.ttl:
                del self._items[handle]
                raise ValueError(
                    f"Expired output handle {handle}; rerun the source tool to refresh it."
                )
            if offset > len(text):
                raise ValueError(f"offset exceeds output length {len(text)}")
            end = min(len(text), offset + min(limit, PAGE_CHARS))
            page = {
                "artifact": handle,
                "content": text[offset:end],
                "offset": offset,
                "next_offset": end if end < len(text) else None,
                "total_chars": len(text),
            }
            span_end = min(len(text), offset + limit)
            if end < span_end:
                page["remaining"] = span_end - end
            if entry is not None:
                page["entry"] = entry
            return page

    def _entry_span(self, handle: str, entry) -> Tuple[int, int]:
        if not isinstance(entry, int) or isinstance(entry, bool):
            raise ValueError(f"entry must be an index entry number, got {entry!r}.")
        with self._lock:
            self._revive(handle)
            if handle not in self._items:
                raise ValueError(
                    f"Unknown output handle {handle}; handles belong to the producing agent session."
                )
            spans = self._indexes.get(handle)
        if spans is None:
            raise ValueError(
                f"Output {handle} has no index; read it with offset and limit."
            )
        if not 1 <= entry <= len(spans):
            listed = f"entries 1-{len(spans)}" if spans else "no entries"
            raise ValueError(
                f"Entry {entry} is not in the index of {handle}, which lists "
                f"{listed}; use an n from that result's index."
            )
        return spans[entry - 1]


def store_for(owner) -> ArtifactStore:
    # Agent initialization creates this before any parallel tool dispatch.
    store = getattr(owner, "_output_artifacts", None)
    if store is None:
        store = ArtifactStore()
        owner._output_artifacts = store
    return store


def retain_excerpt(owner, text: str, budget: int) -> str:
    """Keep a tool's existing text contract while making every omitted byte accessible."""
    import json

    from gaia.agents.base.tool_output import elide_text

    if len(text) <= budget:
        return text
    handle = store_for(owner).put(text)
    metadata = {
        "artifact": handle,
        "continuation": "read_tool_output",
        "offset_unit": "characters",
    }
    reserve = len(json.dumps(metadata)) + 2
    excerpt = elide_text(text, max(200, budget - reserve))
    excerpt.update(metadata)
    return json.dumps(excerpt, ensure_ascii=False)


def read_text_page(path, offset=0, limit=8000, encoding="utf-8"):
    """Read a bounded character page after the caller has checked path permissions."""
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 8000
    ):
        raise ValueError(
            "offset must be nonnegative and limit must be 1..8000 characters"
        )
    with open(path, encoding=encoding) as stream:
        remaining = offset
        while remaining:
            chunk = stream.read(min(remaining, 65536))
            if not chunk:
                raise ValueError("offset exceeds file length")
            remaining -= len(chunk)
        chunk = stream.read(limit + 1)
    return {
        "content": chunk[:limit],
        "offset": offset,
        "next_offset": offset + limit if len(chunk) > limit else None,
        "offset_unit": "characters",
    }
