# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Builds the read-only snapshot the TUI's ``/memory`` view renders.

The agent process already holds an open ``MemoryStore`` (via ``MemoryMixin``),
so the TUI asks THIS process for a dump instead of opening ~/.gaia/memory.db a
second time from Go, which would race the agent's own writes and duplicate the
schema/dedup logic that already lives in ``memory_store.py``.

``build_memory_dump`` is a pure function of an agent object so it can be unit
tested against a real ``MemoryStore`` (temp-dir DB) without spinning up the
stdio transport at all — see ``tests/test_memory_dump.py``.
"""

from __future__ import annotations

from typing import Any, Dict

#: Sentinel the TUI sends on stdin instead of a real question. A leading and
#: trailing NUL byte can never come from a human typing at a keyboard, so this
#: can never collide with real chat input and needs no escaping on either side
#: of the pipe. Must match ``memoryDumpQuery`` in
#: tui/internal/client/memory.go — the wire text is the whole contract.
MEMORY_DUMP_QUERY = "\x00gaia:memory_dump\x00"

#: Hard cap on rows returned in one dump. The pipe, the terminal, and a
#: reader's patience are all bounded; ``total`` in the payload (from
#: MemoryStore.get_all_knowledge's own count) tells the caller whether this is
#: everything or a page of it, so the cap is never a silent truncation.
MAX_MEMORY_DUMP_ITEMS = 300


def build_memory_dump(agent: Any) -> Dict[str, Any]:
    """Return the JSON-able payload for one ``/memory`` snapshot.

    Never raises for "memory is unavailable this session" — that is a normal,
    expected state (no Lemonade, embedding model not pulled, disabled via env)
    and is reported as ``{"available": False, "reason": ...}`` so the caller
    can show the real cause instead of an empty list that reads as "you have
    no memories" (CLAUDE.md: no silent fallbacks). Only a genuine store-level
    failure (e.g. a corrupt DB) propagates to the caller.

    Args:
        agent: A ``MemoryMixin``-composed agent (has ``.memory_store`` and
            ``.memory_unavailable_message()``). Duck-typed rather than
            type-hinted against the mixin so this module has zero import
            dependency on the agent package hierarchy.
    """
    store = getattr(agent, "memory_store", None)
    if store is None:
        reason = None
        unavailable_fn = getattr(agent, "memory_unavailable_message", None)
        if callable(unavailable_fn):
            reason = unavailable_fn()
        return {
            "available": False,
            "reason": reason or "Memory is unavailable for this session.",
        }

    stats = store.get_stats()
    knowledge = stats["knowledge"]
    page = store.get_all_knowledge(
        sort_by="updated_at",
        order="desc",
        limit=MAX_MEMORY_DUMP_ITEMS,
    )
    contexts = store.get_contexts()

    return {
        "available": True,
        "stats": {
            "total_knowledge": knowledge["total"],
            "by_category": knowledge["by_category"],
            "by_context": knowledge["by_context"],
            "sensitive_count": knowledge["sensitive_count"],
            "entity_count": knowledge["entity_count"],
            "avg_confidence": knowledge["avg_confidence"],
        },
        "contexts": [{"context": c["context"], "count": c["count"]} for c in contexts],
        "shown": len(page["items"]),
        "total": page["total"],
        "items": [
            {
                "id": item["id"],
                "category": item["category"],
                "content": item["content"],
                "entity": item.get("entity"),
                "context": item.get("context"),
                "confidence": item.get("confidence"),
                "sensitive": bool(item.get("sensitive")),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "last_used": item.get("last_used"),
            }
            for item in page["items"]
        ],
    }
