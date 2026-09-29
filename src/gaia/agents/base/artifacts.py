# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Session-scoped exact tool output with bounded, explicit continuation reads."""

import threading
import time
from typing import Optional, Tuple
from uuid import uuid4

#: Most characters one ``read`` returns; a longer span continues at ``next_offset``.
PAGE_CHARS = 8000


class ArtifactStore:
    """Retain exact text for one agent; never resolve another session's handle."""

    def __init__(self, max_bytes=64 * 1024 * 1024, ttl=3600):
        self.max_bytes = max_bytes
        self.ttl = ttl
        self._items = {}
        #: handle -> [(offset, length), ...] for index entries 1..n.
        self._indexes = {}
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

    def set_index(self, handle: str, entries) -> None:
        """Record a handle's index so ``read(entry=n)`` returns entry ``n``."""
        with self._lock:
            if handle not in self._items:
                raise ValueError(f"Unknown output handle {handle}; cannot index it.")
            self._indexes[handle] = [(e["offset"], e["length"]) for e in entries]

    def has(self, handle) -> bool:
        """Whether ``handle`` names live output in this store."""
        with self._lock:
            item = self._items.get(handle) if isinstance(handle, str) else None
            return item is not None and time.monotonic() - item[0] < self.ttl

    def text(self, handle: str) -> str:
        """The whole archived text, for indexing it; the model pages via ``read``."""
        with self._lock:
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
