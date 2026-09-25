# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Session-scoped exact tool output with bounded, explicit continuation reads."""

import threading
import time
from uuid import uuid4


class ArtifactStore:
    """Retain exact text for one agent; never resolve another session's handle."""

    def __init__(self, max_bytes=64 * 1024 * 1024, ttl=3600):
        self.max_bytes = max_bytes
        self.ttl = ttl
        self._items = {}
        self._lock = threading.Lock()

    def put(self, text: str) -> str:
        size = len(text.encode("utf-8"))
        with self._lock:
            now = time.monotonic()
            self._items = {
                k: v for k, v in self._items.items() if now - v[0] < self.ttl
            }
            if size + sum(v[2] for v in self._items.values()) > self.max_bytes:
                raise ValueError(
                    "Tool output archive is full (64 MiB default); request smaller output or start a new session."
                )
            handle = "output_" + uuid4().hex
            self._items[handle] = (now, text, size)
            return handle

    def read(self, handle: str, offset: int = 0, limit: int = 2000) -> dict:
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 8000
        ):
            raise ValueError(
                "offset must be a nonnegative character index; limit must be 1..8000"
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
            end = min(len(text), offset + limit)
            return {
                "artifact": handle,
                "content": text[offset:end],
                "offset": offset,
                "next_offset": end if end < len(text) else None,
                "total_chars": len(text),
            }


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
