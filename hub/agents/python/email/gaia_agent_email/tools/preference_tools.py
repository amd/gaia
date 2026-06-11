# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Session-scoped preference tools mixin for ``EmailTriageAgent``.

These tools mutate ``self._session_preferences`` on the agent instance —
an in-memory dict that lives for the lifetime of the agent and is wiped
on restart. The deliberate scoping keeps the daily-driver demo focused
on proving the value before investing in a persistent memory subsystem;
once the broader memory work lands, persisting these preferences is a
direct upgrade path.

Tools registered:

- ``set_priority_sender(email)`` — flag a sender as always urgent
- ``set_low_priority_sender(email)`` — flag a sender as always low-priority
- ``set_category_default(category, action)`` — per-category default action
- ``clear_session_preferences()`` — wipe in-process preferences

The first three tools are consulted by ``triage_inbox`` and
``pre_scan_inbox`` (see ``read_tools.py``). ``clear_session_preferences``
exists so the user can reset without restarting the agent.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from gaia.agents.base.tools import tool
from gaia_agent_email.tools.triage_heuristics import (
    CATEGORY_INFORMATIONAL,
    CATEGORY_LOW_PRIORITY,
)
from gaia.logger import get_logger

log = get_logger(__name__)


# Categories that accept a session-level default action. Keep this set
# small on purpose — defaulting "urgent" or "actionable" to "archive"
# would silently drop important mail.
_CATEGORIES_WITH_DEFAULTS = (CATEGORY_INFORMATIONAL, CATEGORY_LOW_PRIORITY)
_VALID_ACTIONS = ("archive", "keep")


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


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


class PreferenceToolsMixin:
    """Mixin that registers session-preference tools.

    Like the other email-agent mixins, this is state-free at construction
    time and reads ``self._session_preferences`` (set by the agent class)
    via a closure over the agent instance.
    """

    def _register_preference_tools(self) -> None:
        agent = self  # captured for live access to ``_session_preferences``

        @tool
        def set_priority_sender(email: str) -> str:
            """Mark a sender as always urgent for this session.

            Senders flagged here bypass the triage heuristic entirely —
            ``triage_inbox`` and ``pre_scan_inbox`` will classify their
            messages as ``urgent`` regardless of subject keywords or
            Gmail labels. Useful for high-signal senders the heuristic
            can't recognize on its own (e.g. ``boss@company.com``).

            **Session-scoped — preferences are wiped on agent restart.**

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
                return _envelope_ok(
                    {
                        "added": normalized,
                        "preferences": _snapshot(prefs),
                    }
                )
            except Exception as exc:
                log.exception("set_priority_sender failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def set_low_priority_sender(email: str) -> str:
            """Mark a sender as always low-priority for this session.

            Senders flagged here are classified as ``low priority`` and
            surfaced in ``pre_scan_inbox``'s ``suggested_archives``
            section. Useful for newsletters or bot accounts the
            heuristic can't recognize on its own.

            **Session-scoped — preferences are wiped on agent restart.**

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
                return _envelope_ok(
                    {
                        "added": normalized,
                        "preferences": _snapshot(prefs),
                    }
                )
            except Exception as exc:
                log.exception("set_low_priority_sender failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def set_category_default(category: str, action: str) -> str:
            """Set a default action for a triage category.

            Currently supports two categories — ``informational`` and
            ``low priority`` — with two possible actions: ``archive``
            (lift items into ``suggested_archives``) or ``keep`` (the
            default; no archive suggestion). ``urgent`` and
            ``actionable`` cannot be defaulted to anything other than
            ``keep``: the safety cost of silently archiving important
            mail is too high.

            **Session-scoped — preferences are wiped on agent restart.**

            Args:
                category: One of ``"informational"`` or ``"low priority"``.
                action: One of ``"archive"`` or ``"keep"``.
            """
            try:
                cat = (category or "").strip().lower()
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
                return _envelope_ok(
                    {
                        "category": cat,
                        "action": act,
                        "preferences": _snapshot(prefs),
                    }
                )
            except Exception as exc:
                log.exception("set_category_default failed: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def clear_session_preferences() -> str:
            """Wipe in-process session preferences.

            Resets ``priority_senders``, ``low_priority_senders``, and
            ``category_defaults`` to empty without restarting the agent.
            Use when the user wants a fresh triage run with no overrides.

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
                return _envelope_ok(
                    {
                        "cleared": True,
                        "preferences": _snapshot(prefs),
                    }
                )
            except Exception as exc:
                log.exception(
                    "clear_session_preferences failed: %s", type(exc).__name__
                )
                return _envelope_err(f"{type(exc).__name__}: {exc}")
