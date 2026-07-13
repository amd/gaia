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
regardless of how many emails arrive.

Reply behavior is tracked separately in reply records (entity
``email:reply:<sender>``). ``_evaluate_promotions()`` reads these to identify
senders with consistently fast replies and returns them for promotion to
priority senders. Called on-demand at triage time — no background thread.

Tools registered:

- ``profile_inbox()`` — reads all interaction records and returns a profile:
  per-sender frequency + dominant category, sorted by volume descending.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List

from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok
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

# Stable entity prefix for per-sender REPLY behavior records.
# Entity = f"{_REPLY_ENTITY_PREFIX}{sender_email}"
_REPLY_ENTITY_PREFIX = "email:reply:"
_REPLY_DOMAIN = "email_agent_replies"
_REPLY_CATEGORY = "reply_behavior"

# Promotion thresholds for behavioral learning.
# A sender qualifies for promotion when they have at least
# REPLY_PROMOTION_MIN_REPLIES replies recorded AND the median reply
# latency is <= REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS.
# Named as module-level constants so callers can inspect and tests can import.
REPLY_PROMOTION_MIN_REPLIES: int = 3
REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS: float = 300.0  # 5 minutes

# Rolling window cap on reply latencies stored per sender. Prevents a single
# per-sender record from growing without bound for very active correspondents.
# Only the most recent _REPLY_LATENCY_WINDOW samples are kept; the median
# is computed over this window. Far beyond any realistic weekly reply volume.
_REPLY_LATENCY_WINDOW: int = 100

# Ceiling on how many distinct-sender reply records we read in one promotion
# pass. Mirrors _MAX_INTERACTION_RECORDS: not silent — if the ceiling is hit
# we log a WARNING so coverage loss is explicit (per the repo's
# no-silent-fallbacks rule) and the value can be raised deliberately.
# 50k senders is far beyond any realistic mailbox.
_MAX_REPLY_RECORDS: int = 50000

# Sanity ceiling on how many distinct-sender interaction records we read in
# one profiling pass. This is NOT a silent truncation cap: if a real inbox
# ever has more distinct senders than this, ``_read_interactions`` logs a
# WARNING so the coverage loss is loud (per the repo's no-silent-fallbacks
# rule) and the ceiling can be raised deliberately. 50k senders is far beyond
# any realistic single mailbox, so in practice it is never hit.
_MAX_INTERACTION_RECORDS = 50000


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _dominant_category(category_counts: Dict[str, int]) -> str:
    """Return the category with the highest count (ties: lexicographic order)."""
    if not category_counts:
        return ""
    return max(category_counts, key=lambda cat: (category_counts[cat], cat))


class ProfileToolsMixin:
    """Mixin that registers inbox-profiling tools and behavioral-learning helpers.

    State-free at construction time — reads ``self._memory_store`` via
    a closure captured when ``_register_profile_tools()`` is called.

    Behavioral learning (added in #1290):
    - ``_record_reply_interaction`` stores reply latency data per sender.
    - ``_evaluate_promotions`` reads the reply data and returns senders whose
      median reply latency qualifies them for priority promotion.
    Both methods are memory-guarded: they skip when ``_memory_store is None``
    (memory never initialized) or ``_incognito`` (runtime toggle off, #1666).
    Promotion is NEVER triggered on a background thread; it happens only when
    ``_evaluate_promotions`` is called explicitly — currently from
    ``_triage_all_backends`` in ``agent.py``.
    """

    def _record_reply_interaction(self, sender: str, *, latency_seconds: float) -> None:
        """Append a reply latency data point for *sender*.

        Keeps a single rolling record per sender (upsert — never unbounded).
        Silently skips when memory is off (``_memory_store is None`` or
        ``_incognito``, #1666) or when *sender* is empty.

        The record's ``content`` JSON::

            {
                "sender":                   "alice@example.com",
                "reply_latencies_seconds":  [30.0, 45.0, 20.0],
                "last_ts":                  "2026-06-12T14:23:00+00:00"
            }
        """
        store = getattr(self, "_memory_store", None)
        # Incognito (#1666): the runtime memory toggle suppresses behavioral
        # learning writes, not just the base MemoryMixin writes.
        if store is None or getattr(self, "_incognito", False):
            return
        if not sender:
            return
        # Negative latency indicates a clock-skew anomaly; discard rather than
        # let it skew the median toward zero and trigger spurious promotions.
        if latency_seconds < 0:
            return

        entity = f"{_REPLY_ENTITY_PREFIX}{sender}"
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
                    "reply_latencies_seconds": [],
                    "last_ts": now,
                }
            latencies = list(payload.get("reply_latencies_seconds") or [])
            latencies.append(float(latency_seconds))
            # Keep only the most recent _REPLY_LATENCY_WINDOW entries so the
            # record stays bounded for very active correspondents.
            latencies = latencies[-_REPLY_LATENCY_WINDOW:]
            payload["reply_latencies_seconds"] = latencies
            payload["last_ts"] = now
            store.update(row["id"], content=json.dumps(payload))
        else:
            payload = {
                "sender": sender,
                "reply_latencies_seconds": [float(latency_seconds)],
                "last_ts": now,
            }
            store.store(
                category=_REPLY_CATEGORY,
                content=json.dumps(payload),
                domain=_REPLY_DOMAIN,
                entity=entity,
                context=context,
                confidence=1.0,
                source="profile_tools",
            )

    def _evaluate_promotions(self) -> List[str]:
        """Return senders that qualify for priority promotion based on reply behavior.

        A sender qualifies when:
        - they have at least ``REPLY_PROMOTION_MIN_REPLIES`` recorded replies, AND
        - the median reply latency is <= ``REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS``.

        Returns an empty list when memory is disabled or no reply data exists.
        Called on-demand at triage time — never from a background thread.
        """
        store = getattr(self, "_memory_store", None)
        # Incognito (#1666): no promotions are evaluated when memory is off, so
        # the runtime toggle also stops the read side of behavioral learning.
        if store is None or getattr(self, "_incognito", False):
            return []

        rows = store.get_by_category(
            _REPLY_CATEGORY,
            domain=_REPLY_DOMAIN,
            limit=_MAX_REPLY_RECORDS,
        )
        if len(rows) >= _MAX_REPLY_RECORDS:
            log.warning(
                "profile_tools: reply-record read hit the %d-record ceiling; "
                "some senders excluded from promotion evaluation. Raise "
                "_MAX_REPLY_RECORDS to cover all senders.",
                _MAX_REPLY_RECORDS,
            )
        qualified: List[str] = []
        for row in rows:
            try:
                payload = json.loads(row["content"])
                sender = payload.get("sender", "")
                latencies = payload.get("reply_latencies_seconds") or []
                if len(latencies) < REPLY_PROMOTION_MIN_REPLIES:
                    continue
                median_latency = statistics.median(latencies)
                if median_latency <= REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS:
                    qualified.append(sender)
            except (
                json.JSONDecodeError,
                KeyError,
                TypeError,
                statistics.StatisticsError,
            ):
                log.warning(
                    "profile_tools: skipping malformed reply record %s",
                    row.get("id"),
                )
        return qualified

    def _record_interaction(self, sender: str, category: str) -> None:
        """Update the rolling interaction record for *sender*.

        - When memory is off (``_memory_store is None`` or ``_incognito``, #1666),
          silently skips.
        - Does an upsert: retrieve the single per-sender record, update JSON
          in-place, then call ``store.update(id, content=...)``. When no record
          exists yet, ``store.store(...)`` creates the initial one.
        - One record per sender — no unbounded accumulation.
        """
        store = getattr(self, "_memory_store", None)
        # Incognito (#1666): the runtime memory toggle suppresses inbox-profiling
        # writes so personalization data stops accumulating while memory is off.
        if store is None or getattr(self, "_incognito", False):
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
        ``last_ts``. Returns an empty list when memory is off (``_memory_store
        is None`` or ``_incognito``, #1666) or no records exist. Designed for
        reuse by #1290 and other callers.

        Reads up to ``_MAX_INTERACTION_RECORDS`` distinct-sender records. That
        ceiling is far beyond any realistic mailbox; if it is ever reached we
        log a WARNING (never silently drop coverage) so it can be raised
        deliberately.
        """
        store = getattr(self, "_memory_store", None)
        # Incognito (#1666): the runtime toggle gates this read too, so a
        # profile_inbox call surfaces no stored personalization while off.
        if store is None or getattr(self, "_incognito", False):
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
