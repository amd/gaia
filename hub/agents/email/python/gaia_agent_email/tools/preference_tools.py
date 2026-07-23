# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Persistent preference tools mixin for ``EmailTriageAgent``.

These tools mutate ``self._session_preferences`` on the agent instance and
persist the current snapshot to the agent's ``state.db`` (the same durable
SQLite store the trust ledger uses) so that preferences survive across
restarts.  On agent construction, ``_load_persisted_preferences`` seeds
``_session_preferences`` from the stored snapshot.

Preferences are structured key/values, so they live in ``state.db`` and do
NOT depend on the embedding model or the embedding-backed MemoryStore — they
persist even when memory v2 is unavailable (e.g. the embedding model 404s
from Lemonade). Persistence is skipped only in incognito mode (deliberate,
privacy) or when the ``state.db`` handle is not ready (degraded); both are
genuine session-only states, and the tools report them honestly via a
``persisted`` flag rather than claiming a durable save.

Tools registered:

- ``set_priority_sender(email)`` — flag a sender as always urgent
- ``set_low_priority_sender(email)`` — flag a sender as always low-priority
- ``set_category_default(category, action)`` — per-category default action
- ``clear_session_preferences()`` — wipe preferences (in-process and persisted)

The first three tools are consulted by ``triage_inbox`` and
``pre_scan_inbox`` (see ``read_tools.py``).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from gaia_agent_email.tools.envelope import _envelope_err, _envelope_ok
from gaia_agent_email.tools.triage_heuristics import (
    CATEGORY_FYI,
    CATEGORY_PROMOTIONAL,
)

from gaia.agents.base.tools import tool
from gaia.logger import get_logger

log = get_logger(__name__)

# Single-row key under which the JSON preferences snapshot is stored in the
# ``email_preferences`` table. One fixed key means the upsert always touches at
# most one row, so the record count stays at one.
_PREF_STATE_KEY = "session_preferences"

# state.db schema for preferences. Mirrors the trust ledger's storage choice:
# structured operational state lives in ``state.db`` via ``DatabaseMixin``, not
# in the embedding-backed MemoryStore — so preferences persist without the
# embedding model. A single JSON blob keeps the round-trip identical to the
# prior ``_snapshot`` serialization; the read path consumes the whole snapshot.
EMAIL_PREFERENCES_DDL = """
CREATE TABLE IF NOT EXISTS email_preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

# Persistence outcome, surfaced to the caller so the assistant can be honest
# about whether a rule is durable or session-only.
_PERSIST_OK = "persisted"  # written durably to state.db
_PERSIST_INCOGNITO = "incognito"  # deliberate session-only (privacy, #1666)
_PERSIST_UNAVAILABLE = "unavailable"  # state.db handle not ready (degraded)

# Human-readable note the tools attach when a rule could NOT be persisted, so
# the LLM tells the user it is session-only instead of claiming "going forward".
_SESSION_ONLY_NOTE = {
    _PERSIST_INCOGNITO: (
        "Incognito mode is on, so this rule applies for THIS SESSION ONLY "
        "and was not saved."
    ),
    _PERSIST_UNAVAILABLE: (
        "Persistent storage is unavailable, so this rule applies for THIS "
        "SESSION ONLY and was not saved."
    ),
}


def init_preferences_schema(db: Any) -> None:
    """Create the single-row ``email_preferences`` table if absent. Idempotent.

    Called from ``EmailTriageAgent.__init__`` alongside the other
    ``init_schema`` calls (action/schedule/task/trust), before ``init_memory``
    and ``_load_persisted_preferences``.
    """
    db.execute(EMAIL_PREFERENCES_DDL)


def _save_preferences_to_db(
    db: Any, snapshot: Dict[str, Any], now: Optional[float] = None
) -> None:
    """Upsert the one preferences row (JSON blob) into ``state.db``.

    Wrapped in a transaction so the write commits — ``db.query()`` alone does
    not (matches ``trust.record_outcome``). The atomic ``ON CONFLICT`` upsert
    keeps the row count at one even if a scheduler-built agent and the live
    session agent write concurrently to the shared on-disk DB.
    """
    ts = time.time() if now is None else now
    content = json.dumps(snapshot)
    with db.transaction():
        db.query(
            "INSERT INTO email_preferences (key, value, updated_at) "
            "VALUES (:k, :v, :ts) "
            "ON CONFLICT(key) DO UPDATE SET value = :v, updated_at = :ts",
            {"k": _PREF_STATE_KEY, "v": content, "ts": ts},
        )


def _load_preferences_from_db(db: Any) -> Optional[Dict[str, Any]]:
    """Return the persisted snapshot dict, or None if absent/corrupt.

    A corrupt row is tolerated (logged, treated as absent) rather than crashing
    agent startup — it is a local cache read, and the empty defaults are a safe
    starting point. This is fail-soft on a read, not a silent write fallback.
    """
    row = db.query(
        "SELECT value FROM email_preferences WHERE key = :k",
        {"k": _PREF_STATE_KEY},
        one=True,
    )
    if not row:
        return None
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        log.warning(
            "preference_tools: failed to parse persisted preferences from "
            "state.db; starting with empty defaults"
        )
        return None


# Categories that accept a session-level default action. Keep this set
# small on purpose — defaulting "urgent" or "actionable" to "archive"
# would silently drop important mail.
_CATEGORIES_WITH_DEFAULTS = (CATEGORY_FYI, CATEGORY_PROMOTIONAL)
_VALID_ACTIONS = ("archive", "keep")


def _normalize_email(value: str) -> str:
    """Lowercase + strip an email-like value; reject bracketed forms.

    The user can say "Treat alice@example.com as urgent" without quoting,
    and the LLM will pass the bare address through. Headers with angle
    brackets ("Alice <alice@example.com>") are explicitly rejected by
    returning an empty string — the caller treats that as a validation
    failure. This keeps the LLM from sneaking a full From-header value
    into the preference store, which would never match
    ``extract_sender_email`` lookups during triage anyway.
    """
    if not value:
        return ""
    cleaned = value.strip()
    if "<" in cleaned or ">" in cleaned:
        return ""
    return cleaned.lower()


def _validate_session_preferences(prefs: Dict[str, Any]) -> None:
    """Backstop: ensure the in-process state stays well-formed."""
    if not isinstance(prefs.get("priority_senders"), set):
        prefs["priority_senders"] = set(prefs.get("priority_senders") or [])
    if not isinstance(prefs.get("low_priority_senders"), set):
        prefs["low_priority_senders"] = set(prefs.get("low_priority_senders") or [])
    if not isinstance(prefs.get("category_defaults"), dict):
        prefs["category_defaults"] = dict(prefs.get("category_defaults") or {})


def init_session_preferences() -> Dict[str, Any]:
    """Return a fresh, empty preference state.

    Called from ``EmailTriageAgent.__init__`` so the schema lives in one
    place. Sets are used for sender-membership lookups (O(1)); the
    category-defaults dict is keyed by category name.
    """
    return {
        "priority_senders": set(),
        "low_priority_senders": set(),
        "category_defaults": {},
    }


def _snapshot(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-serializable view of session preferences."""
    return {
        "priority_senders": sorted(prefs.get("priority_senders") or []),
        "low_priority_senders": sorted(prefs.get("low_priority_senders") or []),
        "category_defaults": dict(prefs.get("category_defaults") or {}),
    }


def _persist_preferences(agent: Any) -> str:
    """Write the current snapshot to ``state.db``; return a ``_PERSIST_*`` status.

    Preferences are structured key/values stored in the agent's ``state.db``
    (like the trust ledger), so persistence does NOT depend on the embedding
    model or MemoryStore — they survive even when the embedder is absent. The
    write is skipped only in two genuine session-only states, each reported to
    the caller so it is surfaced honestly rather than as a durable save:

    - ``_PERSIST_UNAVAILABLE`` — the ``state.db`` handle is not ready
      (``db_ready`` is False, or ``_session_preferences`` is unset).
    - ``_PERSIST_INCOGNITO`` — a *deliberate* incognito session: ``_incognito``
      is True AND a real ``_memory_store`` exists (the #1666 runtime privacy
      toggle, or ``config.memory_enabled=False``). Such a session must not
      write to persistent storage.

    Crucially, ``_memory_store is None`` is NOT treated as incognito even though
    ``memory.py`` flips ``_incognito`` True when it tears memory down: that
    happens on *involuntary* degradation (embedding model absent /
    ``GAIA_MEMORY_DISABLED=1``), which is the very case #2427 is about. There
    the preference still persists to state.db — the same store the trust/action
    ledgers already write to in that state.

    Otherwise the snapshot is upserted and ``_PERSIST_OK`` is returned.
    """
    if not getattr(agent, "db_ready", False):
        return _PERSIST_UNAVAILABLE

    store = getattr(agent, "_memory_store", None)
    if getattr(agent, "_incognito", False) and store is not None:
        return _PERSIST_INCOGNITO

    prefs = getattr(agent, "_session_preferences", None)
    if prefs is None:
        return _PERSIST_UNAVAILABLE

    _save_preferences_to_db(agent, _snapshot(prefs))
    return _PERSIST_OK


def _persistence_fields(status: str) -> Dict[str, Any]:
    """Envelope fields describing whether a preference write was durable.

    ``persisted`` is the boolean the assistant keys off: True → the rule is
    saved and honored in future sessions; False → session-only, and ``note``
    explains why so the assistant never claims the rule applies "going forward".
    """
    fields: Dict[str, Any] = {
        "persisted": status == _PERSIST_OK,
        "persistence": status,
    }
    if status != _PERSIST_OK:
        fields["note"] = _SESSION_ONLY_NOTE[status]
    return fields


class PreferenceToolsMixin:
    """Mixin that registers session-preference tools.

    Like the other email-agent mixins, this is state-free at construction
    time and reads ``self._session_preferences`` (set by the agent class)
    via a closure over the agent instance.
    """

    def _load_persisted_preferences(self) -> None:
        """Seed ``_session_preferences`` from the ``state.db`` snapshot.

        Called from ``EmailTriageAgent.__init__`` after ``init_db()`` /
        ``init_preferences_schema()`` so that preferences set in a previous
        session are immediately available — independent of the embedding model.

        When no record exists (first run or after ``clear_session_preferences``
        wiped everything), the empty default set by ``init_session_preferences()``
        is left untouched. The read is skipped when the ``state.db`` handle is
        not ready, and in a *deliberate* incognito session (``_incognito`` with a
        real ``_memory_store`` — the #1666 privacy toggle) so stored
        personalization is not read back. It mirrors ``_persist_preferences``:
        an involuntary memory-off state (``_memory_store is None``, embedder
        absent) still loads persisted preferences.
        """
        if not getattr(self, "db_ready", False):
            return
        store = getattr(self, "_memory_store", None)
        if getattr(self, "_incognito", False) and store is not None:
            return

        data = _load_preferences_from_db(self)
        if not data:
            return

        prefs = getattr(self, "_session_preferences", None)
        if prefs is None:
            return

        _validate_session_preferences(prefs)
        # lists → sets for the two sender fields
        prefs["priority_senders"] = set(data.get("priority_senders") or [])
        prefs["low_priority_senders"] = set(data.get("low_priority_senders") or [])
        prefs["category_defaults"] = dict(data.get("category_defaults") or {})

    def _register_preference_tools(self) -> None:
        agent = self  # captured for live access to ``_session_preferences``

        @tool
        def set_priority_sender(email: str) -> str:
            """Mark a sender as always urgent.

            Senders flagged here bypass the triage heuristic entirely —
            ``triage_inbox`` and ``pre_scan_inbox`` will classify their
            messages as ``urgent`` regardless of subject keywords or
            Gmail labels. Useful for high-signal senders the heuristic
            can't recognize on its own (e.g. ``boss@company.com``).

            On a normally-provisioned install this rule is saved to the
            agent's local state database and is honored in future sessions.
            The result reports the outcome: ``persisted: true`` means the
            rule is durable; ``persisted: false`` (incognito, or persistent
            storage unavailable — see ``note``) means it applies to THIS
            SESSION ONLY. When ``persisted`` is false, tell the user the rule
            is session-only and was not saved — never that it applies
            "going forward".

            Args:
                email: A bare email address, e.g. ``alice@example.com``.
                    Headers like ``"Alice <alice@example.com>"`` are
                    rejected; pass the bare address only.
            """
            try:
                normalized = _normalize_email(email)
                if not normalized or "@" not in normalized:
                    return _envelope_err(
                        "set_priority_sender: email must be a bare address "
                        f"like 'alice@example.com' (got: {email!r})"
                    )
                prefs = agent._session_preferences
                _validate_session_preferences(prefs)
                prefs["priority_senders"].add(normalized)
                # If the same sender was previously low-priority, the new
                # priority designation supersedes — silently drop the
                # contradicting flag.
                prefs["low_priority_senders"].discard(normalized)
                status = _persist_preferences(agent)
                return _envelope_ok(
                    {
                        "added": normalized,
                        "preferences": _snapshot(prefs),
                        **_persistence_fields(status),
                    }
                )
            except Exception as exc:
                log.exception("set_priority_sender failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def set_low_priority_sender(email: str) -> str:
            """Mark a sender as always low-priority.

            Senders flagged here are classified as ``low priority`` and
            surfaced in ``pre_scan_inbox``'s ``suggested_archives``
            section. Useful for newsletters or bot accounts the
            heuristic can't recognize on its own.

            On a normally-provisioned install this rule is saved to the
            agent's local state database and is honored in future sessions.
            The result reports the outcome: ``persisted: true`` means the
            rule is durable; ``persisted: false`` (incognito, or persistent
            storage unavailable — see ``note``) means it applies to THIS
            SESSION ONLY. When ``persisted`` is false, tell the user the rule
            is session-only and was not saved — never that it applies
            "going forward".

            Args:
                email: A bare email address, e.g.
                    ``newsletter@stripe.com``.
            """
            try:
                normalized = _normalize_email(email)
                if not normalized or "@" not in normalized:
                    return _envelope_err(
                        "set_low_priority_sender: email must be a bare "
                        f"address like 'a@b.com' (got: {email!r})"
                    )
                prefs = agent._session_preferences
                _validate_session_preferences(prefs)
                prefs["low_priority_senders"].add(normalized)
                # Same conflict resolution as set_priority_sender —
                # later wins.
                prefs["priority_senders"].discard(normalized)
                status = _persist_preferences(agent)
                return _envelope_ok(
                    {
                        "added": normalized,
                        "preferences": _snapshot(prefs),
                        **_persistence_fields(status),
                    }
                )
            except Exception as exc:
                log.exception("set_low_priority_sender failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def set_category_default(category: str, action: str) -> str:
            """Set a default action for a triage category.

            Currently supports two categories — ``FYI`` and
            ``PROMOTIONAL`` — with two possible actions: ``archive``
            (lift items into ``suggested_archives``) or ``keep`` (the
            default; no archive suggestion). ``URGENT`` and
            ``NEEDS_RESPONSE`` cannot be defaulted to anything other than
            ``keep``: the safety cost of silently archiving important
            mail is too high.

            On a normally-provisioned install this default is saved to the
            agent's local state database and is honored in future sessions.
            The result reports the outcome: ``persisted: true`` means it is
            durable; ``persisted: false`` (incognito, or persistent storage
            unavailable — see ``note``) means it applies to THIS SESSION ONLY.
            When ``persisted`` is false, tell the user the default is
            session-only and was not saved — never that it applies
            "going forward".

            Args:
                category: One of ``"FYI"`` or ``"PROMOTIONAL"``.
                action: One of ``"archive"`` or ``"keep"``.
            """
            try:
                # Normalize: category is UPPERCASE (schema 2.0), action is lowercase.
                cat = (category or "").strip().upper()
                act = (action or "").strip().lower()
                if cat not in _CATEGORIES_WITH_DEFAULTS:
                    return _envelope_err(
                        "set_category_default: category must be one of "
                        f"{list(_CATEGORIES_WITH_DEFAULTS)} (got: {category!r})"
                    )
                if act not in _VALID_ACTIONS:
                    return _envelope_err(
                        "set_category_default: action must be one of "
                        f"{list(_VALID_ACTIONS)} (got: {action!r})"
                    )
                prefs = agent._session_preferences
                _validate_session_preferences(prefs)
                if act == "keep":
                    # 'keep' is the implicit default — clear any prior
                    # 'archive' setting rather than persisting a no-op.
                    prefs["category_defaults"].pop(cat, None)
                else:
                    prefs["category_defaults"][cat] = act
                status = _persist_preferences(agent)
                return _envelope_ok(
                    {
                        "category": cat,
                        "action": act,
                        "preferences": _snapshot(prefs),
                        **_persistence_fields(status),
                    }
                )
            except Exception as exc:
                log.exception("set_category_default failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def clear_session_preferences() -> str:
            """Wipe preferences in-process and from persistent storage.

            Resets ``priority_senders``, ``low_priority_senders``, and
            ``category_defaults`` to empty without restarting the agent.
            On a normally-provisioned install the cleared state is also
            saved so a fresh session starts empty; the result's
            ``persisted`` flag reports whether that durable clear happened
            (``false`` in incognito or when storage is unavailable — in
            which case only the current session was cleared). Use when the
            user wants a clean slate.

            Mutates the existing dict in place rather than rebinding to
            a fresh one. Read-side tools currently look up the dict via
            ``getattr(agent, "_session_preferences", None)`` at call
            time, so a rebind would also work — but a future caller
            holding a direct reference to the dict (e.g. a memory
            adapter snapshotting state) would silently observe stale
            data after a rebind. In-place mutation keeps the contract
            stable.
            """
            try:
                prefs = agent._session_preferences
                _validate_session_preferences(prefs)
                prefs["priority_senders"].clear()
                prefs["low_priority_senders"].clear()
                prefs["category_defaults"].clear()
                status = _persist_preferences(agent)
                return _envelope_ok(
                    {
                        "cleared": True,
                        "preferences": _snapshot(prefs),
                        **_persistence_fields(status),
                    }
                )
            except Exception as exc:
                log.exception(
                    "clear_session_preferences failed: %s", type(exc).__name__
                )
                return _envelope_err(f"{type(exc).__name__}: {exc}")
