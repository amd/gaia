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
- ``remove_priority_sender(email)`` — undo a single priority-sender flag
- ``set_low_priority_sender(email)`` — flag a sender as always low-priority
- ``remove_low_priority_sender(email)`` — undo a single low-priority flag
- ``set_category_default(category, action)`` — per-category default action
- ``remove_category_default(category)`` — clear a category's default,
  reverting it to the implicit ``keep``
- ``get_preferences()`` — read back everything currently stored
- ``clear_session_preferences()`` — wipe preferences (in-process and persisted)

The set/remove pairs mutate the same ``_session_preferences`` state that
``triage_inbox`` and ``pre_scan_inbox`` consult (see ``read_tools.py``).
``get_preferences`` is a pure read-back for the conversation and plays no
role in triage itself.

#2520: no removal tool existed at all — asking to remove a single
preference either no-op'd while the agent claimed success, or triggered
the *set* tool instead. Every removal tool below reports its outcome via
an explicit ``removed`` field rather than relying on ``ok: true`` alone,
so the agent-loop model has an unambiguous signal to narrate from and
cannot claim a mutation that did not happen (see each tool's docstring).
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

# Legacy entity key: versions <= v0.5.0 stored the preferences snapshot in the
# embedding-backed MemoryStore under this key. On the first load after upgrade,
# ``_load_persisted_preferences`` reads it once (when ``state.db`` has no row)
# and writes it through to ``state.db`` so nothing is silently dropped (#2427).
_LEGACY_PREF_ENTITY = "email:preferences"

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
            # One-time upgrade path: no state.db row yet, but a pre-v0.5.1 build
            # may have persisted preferences to the MemoryStore. Migrate them so
            # they are not silently dropped on upgrade.
            data = self._migrate_legacy_preferences()
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

    def _migrate_legacy_preferences(self) -> Optional[Dict[str, Any]]:
        """Seed from the legacy MemoryStore preferences record, once.

        Versions <= v0.5.0 stored the preferences snapshot in the embedding-backed
        MemoryStore under ``_LEGACY_PREF_ENTITY``. When ``state.db`` has no row
        (fresh after upgrade) and that legacy record exists, read it, write it
        through to ``state.db`` so future loads use the state.db fast path, and
        return the snapshot. Returns ``None`` when there is nothing to migrate.

        A corrupt legacy record is treated as absent (logged) rather than
        crashing startup — a fail-soft read, not a silent write fallback.
        """
        store = getattr(self, "_memory_store", None)
        if store is None or not hasattr(store, "get_by_entity"):
            return None
        existing = store.get_by_entity(_LEGACY_PREF_ENTITY)
        if not existing:
            return None
        try:
            data = json.loads(existing[0]["content"])
        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
            log.warning(
                "preference_tools: legacy MemoryStore preferences record is "
                "unreadable; starting with empty defaults"
            )
            return None
        snapshot = {
            "priority_senders": sorted(data.get("priority_senders") or []),
            "low_priority_senders": sorted(data.get("low_priority_senders") or []),
            "category_defaults": dict(data.get("category_defaults") or {}),
        }
        _save_preferences_to_db(self, snapshot)
        log.info(
            "preference_tools: migrated %d priority / %d low-priority sender(s) "
            "and %d category default(s) from the legacy MemoryStore record to "
            "state.db (#2427)",
            len(snapshot["priority_senders"]),
            len(snapshot["low_priority_senders"]),
            len(snapshot["category_defaults"]),
        )
        return snapshot

    def _register_preference_tools(self) -> None:
        agent = self  # captured for live access to ``_session_preferences``

        @tool
        def set_priority_sender(email: str) -> str:
            """Mark a sender as high-priority (#2632: never forces urgency).

            Senders flagged here are tagged ``preference_applied:
            "priority_sender"`` in ``triage_inbox`` / ``pre_scan_inbox``
            output so they can be surfaced or ordered ahead of other mail —
            but the category (urgent, needs_response, ...) is still decided
            entirely by the message content; this tool never overrides it.
            "I care about this sender" is not "their mail is urgent" — a
            newsletter from a priority sender still classifies as whatever
            its content says. Useful for calling out high-signal senders
            the heuristic can't recognize on its own (e.g.
            ``boss@company.com``).

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
        def remove_priority_sender(email: str) -> str:
            """Remove a sender from the priority-sender list.

            Reverses ``set_priority_sender`` for one address. Only touches
            ``priority_senders`` — never ``low_priority_senders``, even if
            the address happens to be flagged there. Unlike the set tools,
            which deliberately cross-clear the opposite set, removal has no
            such side effect: it undoes exactly one flag and nothing else.

            The ``removed`` field is the only outcome to trust: ``true``
            means the address WAS a priority sender and now isn't. ``false``
            means it was never flagged as priority, so nothing changed — the
            call still succeeds (``ok: true``), but there is nothing to
            report success ABOUT, and no ``persisted``/``persistence`` fields
            are present since nothing was written. Never tell the user a
            sender was removed when ``removed`` is ``false`` — say plainly
            that it was not set as a priority sender.

            When ``removed`` is ``true`` the result reports persistence the
            same way ``set_priority_sender`` does: ``persisted: true`` means
            the removal is durable; ``persisted: false`` (incognito, or
            persistent storage unavailable — see ``note``) means it applies
            to THIS SESSION ONLY.

            Args:
                email: A bare email address, e.g. ``alice@example.com``.
                    Headers like ``"Alice <alice@example.com>"`` are
                    rejected; pass the bare address only.
            """
            try:
                normalized = _normalize_email(email)
                if not normalized or "@" not in normalized:
                    return _envelope_err(
                        "remove_priority_sender: email must be a bare address "
                        f"like 'alice@example.com' (got: {email!r})"
                    )
                prefs = agent._session_preferences
                _validate_session_preferences(prefs)
                if normalized not in prefs["priority_senders"]:
                    return _envelope_ok(
                        {
                            "removed": False,
                            "message": (
                                f"{normalized} was not set as a priority "
                                "sender — nothing to remove."
                            ),
                            "preferences": _snapshot(prefs),
                        }
                    )
                prefs["priority_senders"].discard(normalized)
                status = _persist_preferences(agent)
                return _envelope_ok(
                    {
                        "removed": True,
                        "message": f"{normalized} is no longer a priority sender.",
                        "preferences": _snapshot(prefs),
                        **_persistence_fields(status),
                    }
                )
            except Exception as exc:
                log.exception("remove_priority_sender failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def set_low_priority_sender(email: str) -> str:
            """Mark a sender as low-priority (#2666: never forces PROMOTIONAL).

            Senders flagged here are tagged ``preference_applied:
            "low_priority_sender"`` in ``triage_inbox`` / ``pre_scan_inbox``
            output so they can be de-prioritized behind other mail — but
            the category (urgent, needs_response, ...) is still decided
            entirely by the message content; this tool never overrides it.
            "I don't care about most of this sender's mail" is not "none
            of their mail is ever urgent" — a genuinely urgent message
            from a low-priority sender still classifies as whatever its
            content says. Useful for newsletters or bot accounts the
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
        def remove_low_priority_sender(email: str) -> str:
            """Remove a sender from the low-priority list.

            Reverses ``set_low_priority_sender`` for one address. Only
            touches ``low_priority_senders`` — never ``priority_senders``,
            even if the address happens to be flagged there. Unlike the set
            tools, which deliberately cross-clear the opposite set, removal
            has no such side effect: it undoes exactly one flag and nothing
            else.

            The ``removed`` field is the only outcome to trust: ``true``
            means the address WAS low-priority and now isn't. ``false``
            means it was never flagged low-priority, so nothing changed —
            the call still succeeds (``ok: true``), but there is nothing to
            report success ABOUT, and no ``persisted``/``persistence`` fields
            are present since nothing was written. Never tell the user a
            sender was removed when ``removed`` is ``false`` — say plainly
            that it was not set as a low-priority sender.

            When ``removed`` is ``true`` the result reports persistence the
            same way ``set_low_priority_sender`` does: ``persisted: true``
            means the removal is durable; ``persisted: false`` (incognito, or
            persistent storage unavailable — see ``note``) means it applies
            to THIS SESSION ONLY.

            Args:
                email: A bare email address, e.g.
                    ``newsletter@stripe.com``.
            """
            try:
                normalized = _normalize_email(email)
                if not normalized or "@" not in normalized:
                    return _envelope_err(
                        "remove_low_priority_sender: email must be a bare "
                        f"address like 'a@b.com' (got: {email!r})"
                    )
                prefs = agent._session_preferences
                _validate_session_preferences(prefs)
                if normalized not in prefs["low_priority_senders"]:
                    return _envelope_ok(
                        {
                            "removed": False,
                            "message": (
                                f"{normalized} was not set as a low-priority "
                                "sender — nothing to remove."
                            ),
                            "preferences": _snapshot(prefs),
                        }
                    )
                prefs["low_priority_senders"].discard(normalized)
                status = _persist_preferences(agent)
                return _envelope_ok(
                    {
                        "removed": True,
                        "message": (
                            f"{normalized} is no longer a low-priority sender."
                        ),
                        "preferences": _snapshot(prefs),
                        **_persistence_fields(status),
                    }
                )
            except Exception as exc:
                log.exception(
                    "remove_low_priority_sender failed: %s", type(exc).__name__
                )
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
        def remove_category_default(category: str) -> str:
            """Clear a category's default action, reverting it to the
            implicit ``keep``.

            Equivalent to ``set_category_default(category, "keep")``, but
            named to match ``remove_priority_sender`` /
            ``remove_low_priority_sender`` so the model has an obvious
            removal verb instead of having to know that ``"keep"`` secretly
            undoes an ``"archive"`` override.

            The ``removed`` field is the only outcome to trust: ``true``
            means the category HAD an ``archive`` override that is now
            cleared. ``false`` means the category was already at the
            implicit ``keep`` default, so nothing changed — no
            ``persisted``/``persistence`` fields are present since nothing
            was written. Never tell the user a default was removed when
            ``removed`` is ``false``.

            When ``removed`` is ``true`` the result reports persistence the
            same way ``set_category_default`` does: ``persisted: true``
            means the removal is durable; ``persisted: false`` (incognito,
            or persistent storage unavailable — see ``note``) means it
            applies to THIS SESSION ONLY.

            Args:
                category: One of ``"FYI"`` or ``"PROMOTIONAL"``.
            """
            try:
                cat = (category or "").strip().upper()
                if cat not in _CATEGORIES_WITH_DEFAULTS:
                    return _envelope_err(
                        "remove_category_default: category must be one of "
                        f"{list(_CATEGORIES_WITH_DEFAULTS)} (got: {category!r})"
                    )
                prefs = agent._session_preferences
                _validate_session_preferences(prefs)
                if cat not in prefs["category_defaults"]:
                    return _envelope_ok(
                        {
                            "removed": False,
                            "message": (
                                f"{cat} has no default action set — nothing "
                                "to remove."
                            ),
                            "preferences": _snapshot(prefs),
                        }
                    )
                prefs["category_defaults"].pop(cat, None)
                status = _persist_preferences(agent)
                return _envelope_ok(
                    {
                        "removed": True,
                        "message": f"{cat} no longer has a default action.",
                        "preferences": _snapshot(prefs),
                        **_persistence_fields(status),
                    }
                )
            except Exception as exc:
                log.exception("remove_category_default failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def get_preferences() -> str:
            """Return everything currently saved: priority senders,
            low-priority senders, and category default actions.

            The read-back counterpart to the set/remove tools above — the
            only way to confirm from the conversation what is actually
            stored, including whether a removal really took effect. Call
            this before telling a user what is or isn't configured, rather
            than guessing from conversation history.
            """
            try:
                prefs = agent._session_preferences
                _validate_session_preferences(prefs)
                return _envelope_ok({"preferences": _snapshot(prefs)})
            except Exception as exc:
                log.exception("get_preferences failed: %s", type(exc).__name__)
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
