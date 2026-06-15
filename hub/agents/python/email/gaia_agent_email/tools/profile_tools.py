# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Inbox-profiling tools mixin for ``EmailTriageAgent``.

Builds a per-sender frequency profile from remembered interaction history
so the agent can tell the user which senders write most often and in what
category.

Interaction records are stored in MemoryStore as one rolling per-sender
record (entity ``email:interaction:<sender>``). Each record's ``content``
is a JSON object::

    {
        "sender":          "alice@example.com",
        "count":           7,
        "category_counts": {"urgent": 3, "actionable": 4},
        "last_ts":         "2026-06-12T14:23:00+00:00"
    }

Bounded and idempotent by design — ``_record_interaction`` always does an
upsert into the single per-sender record, so the record count is O(senders)
regardless of how many emails arrive. #1290 can reuse ``_read_interactions``
directly to build richer views.

Tools registered:

- ``profile_inbox()`` — reads all interaction records and returns a profile:
  per-sender frequency + dominant category, sorted by volume descending.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from gaia.agents.base.tools import tool
from gaia.logger import get_logger

log = get_logger(__name__)

# Stable entity prefix for per-sender interaction records.
# Entity = f"{_INTERACTION_ENTITY_PREFIX}{sender_email}"
# This prefix is intentionally namespace-safe (colon-separated, no slash)
# so that ``get_by_entity`` prefix lookups work consistently.
_INTERACTION_ENTITY_PREFIX = "email:interaction:"
_INTERACTION_DOMAIN = "email_agent_interactions"
_INTERACTION_CATEGORY = "interaction"

# Sanity ceiling on how many distinct-sender interaction records we read in
# one profiling pass. This is NOT a silent truncation cap: if a real inbox
# ever has more distinct senders than this, ``_read_interactions`` logs a
# WARNING so the coverage loss is loud (per the repo's no-silent-fallbacks
# rule) and the ceiling can be raised deliberately. 50k senders is far beyond
# any realistic single mailbox, so in practice it is never hit.
_MAX_INTERACTION_RECORDS = 50000


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _dominant_category(category_counts: Dict[str, int]) -> str:
    """Return the category with the highest count; ties broken by last alphabetically."""
    if not category_counts:
        return ""
    return max(category_counts, key=lambda cat: (category_counts[cat], cat))


class ProfileToolsMixin:
    """Mixin that registers inbox-profiling tools.

    State-free at construction time — reads ``self._memory_store`` via
    a closure captured when ``_register_profile_tools()`` is called.
    """

    def _record_interaction(self, sender: str, category: str) -> None:
        """Update the rolling interaction record for *sender*.

        - When ``_memory_store`` is None (memory disabled), silently skips.
        - Does an upsert: retrieve the single per-sender record, update JSON
          in-place, then call ``store.update(id, content=...)``. When no record
          exists yet, ``store.store(...)`` creates the initial one.
        - One record per sender — no unbounded accumulation.
        """
        store = getattr(self, "_memory_store", None)
        if store is None:
            return
        if not sender or not category:
            return

        entity = f"{_INTERACTION_ENTITY_PREFIX}{sender}"
        context = getattr(self, "_memory_context", "email")
        now = _now_iso()

        existing = store.get_by_entity(entity)
        if existing:
            row = existing[0]
            try:
                payload = json.loads(row["content"])
            except (json.JSONDecodeError, KeyError):
                payload = {
                    "sender": sender,
                    "count": 0,
                    "category_counts": {},
                    "last_ts": now,
                }
            payload["count"] = int(payload.get("count", 0)) + 1
            cats = dict(payload.get("category_counts") or {})
            cats[category] = int(cats.get(category, 0)) + 1
            payload["category_counts"] = cats
            payload["last_ts"] = now
            store.update(row["id"], content=json.dumps(payload))
        else:
            payload = {
                "sender": sender,
                "count": 1,
                "category_counts": {category: 1},
                "last_ts": now,
            }
            store.store(
                category=_INTERACTION_CATEGORY,
                content=json.dumps(payload),
                domain=_INTERACTION_DOMAIN,
                entity=entity,
                context=context,
                confidence=1.0,
                source="profile_tools",
            )

    def _read_interactions(self) -> List[Dict[str, Any]]:
        """Return all per-sender interaction records as parsed dicts.

        Each element has: ``sender``, ``count``, ``category_counts``,
        ``last_ts``. Returns an empty list when memory is disabled or no
        records exist. Designed for reuse by #1290 and other callers.

        Reads up to ``_MAX_INTERACTION_RECORDS`` distinct-sender records. That
        ceiling is far beyond any realistic mailbox; if it is ever reached we
        log a WARNING (never silently drop coverage) so it can be raised
        deliberately.
        """
        store = getattr(self, "_memory_store", None)
        if store is None:
            return []
        rows = store.get_by_category(
            _INTERACTION_CATEGORY,
            domain=_INTERACTION_DOMAIN,
            limit=_MAX_INTERACTION_RECORDS,
        )
        if len(rows) >= _MAX_INTERACTION_RECORDS:
            log.warning(
                "profile_tools: interaction-record read hit the %d-record "
                "ceiling; the inbox profile may be incomplete. Raise "
                "_MAX_INTERACTION_RECORDS to cover all senders.",
                _MAX_INTERACTION_RECORDS,
            )
        out: List[Dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["content"])
                # Ensure required keys are present
                out.append(
                    {
                        "sender": payload.get("sender", ""),
                        "count": int(payload.get("count", 0)),
                        "category_counts": dict(payload.get("category_counts") or {}),
                        "last_ts": payload.get("last_ts", ""),
                    }
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                log.warning(
                    "profile_tools: skipping malformed interaction record %s",
                    row.get("id"),
                )
        return out

    def _register_profile_tools(self) -> None:
        agent = self  # closure over the agent for live access to _memory_store

        @tool
        def profile_inbox() -> str:
            """Summarize sender frequency and typical category from interaction history.

            Reads all remembered interaction records built up as the agent
            has triaged messages. Returns a profile ranked by message volume
            (highest first), with per-sender total count and dominant
            category.

            Returns:
                JSON envelope with ``{"ok": true, "data": {"top_senders": [...],
                "total_messages": N}}`` where each top-senders element has
                ``sender``, ``count``, ``dominant_category``,
                ``category_counts``, and ``last_ts`` (ISO-8601 timestamp of
                the most recent interaction). Returns an empty profile
                (ok=True, top_senders=[]) when memory is disabled or no
                history exists.
            """
            try:
                records = agent._read_interactions()
                if not records:
                    return _envelope_ok({"top_senders": [], "total_messages": 0})
                # Sort descending by count, then alphabetically for stable output.
                sorted_records = sorted(
                    records, key=lambda r: (-r["count"], r["sender"])
                )
                top_senders = [
                    {
                        "sender": r["sender"],
                        "count": r["count"],
                        "dominant_category": _dominant_category(r["category_counts"]),
                        "category_counts": r["category_counts"],
                        "last_ts": r["last_ts"],
                    }
                    for r in sorted_records
                ]
                total = sum(r["count"] for r in records)
                return _envelope_ok(
                    {"top_senders": top_senders, "total_messages": total}
                )
            except Exception as exc:
                log.exception("profile_inbox failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
