# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Preference-removal tests for EmailTriageAgent (#2520).

test_email_preferences_persist.py already covers the SET tools
(set_priority_sender, set_low_priority_sender, set_category_default) and
proves they persist correctly. This file covers the other half that was
entirely missing: REMOVING an individual preference.

#2520: no removal tool existed at all. Asking the agent to "remove a
low-priority sender" either did nothing while claiming success, or called
the *set* tool instead and reported success at ADDING when the user asked
to REMOVE — verified by diffing the agent's own state.db before/after. The
fix adds four new tools (remove_priority_sender, remove_low_priority_sender,
remove_category_default, get_preferences), and each mutating tool must be
honest: ``removed`` is the discriminator the caller/model must key off, and
a ``removed: false`` result must never carry a truthy persistence claim,
because nothing was written.

Acceptance criteria covered:
- A removal that actually removes something is written through to the REAL
  store (state.db), not just the in-process dict, and reports
  ``persisted: true``.
- A removal of something that was never set reports ``removed: false`` and
  carries NO persistence fields at all (nothing was written, so there is
  nothing to report persistence about).
- ``remove_low_priority_sender`` never touches ``priority_senders`` and
  vice versa — there is no analogous side effect to the SET tools'
  deliberate cross-clear-on-set.
- ``remove_category_default`` mirrors the same removed/not-removed
  truthfulness for the FYI/PROMOTIONAL category-default overrides, and
  rejects a category outside ``_CATEGORIES_WITH_DEFAULTS``.
- Invalid email input (empty, no ``@``, bracketed header form) is rejected
  the same way the SET tools reject it.
- ``get_preferences`` is a true read-only mirror of what the SET tools
  stored — the verification path #2520 says does not currently exist
  ("there is no way to discover the discrepancy from the chat").
- Incognito removal is honest session-only, mirroring ``TestIncognitoGate``
  for the SET tools.

Embedder is mocked out (same pattern as test_email_preferences_persist.py)
so these tests run hermetically without Lemonade.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path / import bootstrap
# ---------------------------------------------------------------------------

# parents[0] = tests/,  [1] = email/,  [2] = python/,  [3] = agents/,
# [4] = hub/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal fake backends
# ---------------------------------------------------------------------------


class _MinimalMailBackend:
    pass


class _MinimalCalendarBackend:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 768


def _fake_embed(text: str) -> np.ndarray:
    """Deterministic unit vector — keeps FAISS happy."""
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _build_agent(tmp_path: Path) -> EmailTriageAgent:
    """Build EmailTriageAgent with injected fakes and tmp db paths.

    Mocks the Lemonade embedding endpoint so init_memory succeeds without a
    running Lemonade server (FTS5-only store/search path). Hermetic: uses
    explicit db_path/memory_db_path under tmp_path, so it never touches the
    real ~/.gaia store.
    """
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )

    with (
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
        patch(
            "gaia.agents.base.memory.MemoryMixin._get_embedder",
            return_value=MagicMock(),
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._embed_text",
            side_effect=_fake_embed,
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._backfill_embeddings",
            return_value=0,
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index",
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin.init_system_context",
        ),
    ):
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


def _read_persisted_snapshot(tmp_path: Path) -> dict | None:
    """Read the persisted preferences snapshot directly from state.db,
    bypassing any in-memory agent state entirely.

    Returns None if no row has ever been written. This is the direct-sqlite
    read-back pattern from test_no_duplicate_state_db_records in
    test_email_preferences_persist.py, used here to prove a removal (or a
    truthful non-removal) reached the REAL store, not just
    ``_session_preferences`` — an in-memory-only assertion previously
    missed a real persistence bug (the #2427 ``_migrate_legacy_preferences``
    regression), so removal must be pinned the same way.
    """
    conn = sqlite3.connect(tmp_path / "state.db")
    try:
        row = conn.execute(
            "SELECT value FROM email_preferences WHERE key = 'session_preferences'"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(row[0])


def _assert_snapshot_shape(snapshot: dict) -> None:
    """Pin the exact shape ``_snapshot()`` produces: sorted lists + a dict."""
    assert set(snapshot.keys()) == {
        "priority_senders",
        "low_priority_senders",
        "category_defaults",
    }, f"unexpected preferences snapshot shape: {snapshot}"
    assert isinstance(snapshot["priority_senders"], list)
    assert snapshot["priority_senders"] == sorted(snapshot["priority_senders"])
    assert isinstance(snapshot["low_priority_senders"], list)
    assert snapshot["low_priority_senders"] == sorted(snapshot["low_priority_senders"])
    assert isinstance(snapshot["category_defaults"], dict)


def _invoke_set_priority_sender(email: str) -> dict:
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("set_priority_sender")
    assert entry is not None, "set_priority_sender not registered"
    result = entry["function"](email)
    return json.loads(result)


def _invoke_set_low_priority_sender(email: str) -> dict:
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("set_low_priority_sender")
    assert entry is not None, "set_low_priority_sender not registered"
    result = entry["function"](email)
    return json.loads(result)


def _invoke_set_category_default(category: str, action: str) -> dict:
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("set_category_default")
    assert entry is not None, "set_category_default not registered"
    result = entry["function"](category, action)
    return json.loads(result)


def _invoke_remove_priority_sender(email: str) -> dict:
    """Call the remove_priority_sender tool directly via the tool registry.

    #2520: this tool does not exist yet — the lookup is expected to fail
    with "remove_priority_sender not registered" until it is added.
    """
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("remove_priority_sender")
    assert entry is not None, "remove_priority_sender not registered"
    result = entry["function"](email)
    return json.loads(result)


def _invoke_remove_low_priority_sender(email: str) -> dict:
    """Call the remove_low_priority_sender tool directly via the tool
    registry.

    #2520: this tool does not exist yet — the lookup is expected to fail
    with "remove_low_priority_sender not registered" until it is added.
    """
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("remove_low_priority_sender")
    assert entry is not None, "remove_low_priority_sender not registered"
    result = entry["function"](email)
    return json.loads(result)


def _invoke_remove_category_default(category: str) -> dict:
    """Call the remove_category_default tool directly via the tool
    registry.

    #2520: this tool does not exist yet — the lookup is expected to fail
    with "remove_category_default not registered" until it is added.
    """
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("remove_category_default")
    assert entry is not None, "remove_category_default not registered"
    result = entry["function"](category)
    return json.loads(result)


def _invoke_get_preferences() -> dict:
    """Call the get_preferences tool directly via the tool registry.

    #2520 / #2519: this tool does not exist yet — the lookup is expected to
    fail with "get_preferences not registered" until it is added.
    """
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("get_preferences")
    assert entry is not None, "get_preferences not registered"
    result = entry["function"]()
    return json.loads(result)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRemovalPersistsToRealStore:
    """AC: removing a preference removes it from the REAL persisted store,
    not just the in-process dict. #2520 was verified by diffing the agent's
    own state.db; an in-memory-only assertion would have missed the
    analogous #2427 persistence regression, so every removal test here also
    reads state.db directly."""

    def test_remove_low_priority_sender_persists_to_state_db(self, tmp_path):
        """Set a low-priority sender, remove it, and confirm both the
        tool's own JSON result AND a direct read of state.db agree it is
        gone — not merely that _session_preferences was mutated in
        process."""
        agent_a = _build_agent(tmp_path)
        try:
            set_result = _invoke_set_low_priority_sender("newsletters@techcrunch.com")
            assert set_result["ok"] is True, f"precondition failed: {set_result}"
            assert (
                "newsletters@techcrunch.com"
                in agent_a._session_preferences["low_priority_senders"]
            )

            remove_result = _invoke_remove_low_priority_sender(
                "newsletters@techcrunch.com"
            )
            assert remove_result["ok"] is True, f"remove failed: {remove_result}"
            data = remove_result["data"]
            assert data["removed"] is True, f"expected removed: True, got {data}"
            assert isinstance(data.get("message"), str) and data["message"]
            _assert_snapshot_shape(data["preferences"])
            assert (
                data["persisted"] is True
            ), f"a non-incognito removal must report persisted: True: {data}"
            assert data["persistence"] == "persisted"
            assert "note" not in data

            # In-process state reflects the removal immediately.
            assert (
                "newsletters@techcrunch.com"
                not in agent_a._session_preferences["low_priority_senders"]
            )
        finally:
            agent_a.close_db()

        # Direct read of the durable store, bypassing any in-memory agent
        # state entirely — the assertion that would have caught #2427 had
        # it applied to removal.
        stored = _read_persisted_snapshot(tmp_path)
        assert stored is not None, "expected a persisted preferences row"
        assert (
            "newsletters@techcrunch.com" not in stored["low_priority_senders"]
        ), f"removal was not written through to state.db: {stored}"

        # And a freshly constructed agent B (same tmp_path) must not see it.
        agent_b = _build_agent(tmp_path)
        try:
            assert (
                "newsletters@techcrunch.com"
                not in agent_b._session_preferences["low_priority_senders"]
            ), (
                "removed sender reappeared in a fresh session: "
                f"{agent_b._session_preferences['low_priority_senders']}"
            )
        finally:
            agent_b.close_db()

    def test_remove_priority_sender_persists_to_state_db(self, tmp_path):
        """Mirror of the above for remove_priority_sender."""
        agent_a = _build_agent(tmp_path)
        try:
            set_result = _invoke_set_priority_sender("boss@company.com")
            assert set_result["ok"] is True, f"precondition failed: {set_result}"

            remove_result = _invoke_remove_priority_sender("boss@company.com")
            assert remove_result["ok"] is True, f"remove failed: {remove_result}"
            data = remove_result["data"]
            assert data["removed"] is True, f"expected removed: True, got {data}"
            assert isinstance(data.get("message"), str) and data["message"]
            _assert_snapshot_shape(data["preferences"])
            assert data["persisted"] is True
            assert data["persistence"] == "persisted"
            assert "note" not in data

            assert (
                "boss@company.com"
                not in agent_a._session_preferences["priority_senders"]
            )
        finally:
            agent_a.close_db()

        stored = _read_persisted_snapshot(tmp_path)
        assert stored is not None
        assert (
            "boss@company.com" not in stored["priority_senders"]
        ), f"removal was not written through to state.db: {stored}"

        agent_b = _build_agent(tmp_path)
        try:
            assert (
                "boss@company.com"
                not in agent_b._session_preferences["priority_senders"]
            )
        finally:
            agent_b.close_db()


class TestRemovalOfNeverSetAddressIsTruthful:
    """AC: removing an address that was never a preference must say so
    honestly (removed: false), not claim a success that did not happen —
    the exact false-positive #2520 documents ("I have successfully removed
    ..." while the store was actually unchanged)."""

    def test_remove_low_priority_sender_never_set(self, tmp_path):
        agent = _build_agent(tmp_path)
        try:
            # Establish some unrelated persisted state first, so "genuinely
            # unchanged" below is a real assertion, not trivially true on an
            # empty store.
            pre = _invoke_set_priority_sender("boss@company.com")
            assert pre["ok"] is True, f"precondition failed: {pre}"
            before = _read_persisted_snapshot(tmp_path)
            assert before is not None

            result = _invoke_remove_low_priority_sender("ghost@example.com")
            assert result["ok"] is True, f"call itself should succeed: {result}"
            data = result["data"]
            assert data["removed"] is False, (
                "removing an address that was never set must report "
                f"removed: False, not claim success. Got: {data}"
            )
            assert isinstance(data.get("message"), str) and data["message"]
            _assert_snapshot_shape(data["preferences"])
            assert "persisted" not in data, (
                "a no-op removal must never report persistence fields at "
                f"all (nothing was written): {data}"
            )
            assert "persistence" not in data
            assert (
                "ghost@example.com"
                not in agent._session_preferences["low_priority_senders"]
            )
        finally:
            agent.close_db()

        after = _read_persisted_snapshot(tmp_path)
        assert after == before, (
            "a no-op removal must not touch the persisted store at all: "
            f"before={before}, after={after}"
        )

    def test_remove_priority_sender_never_set(self, tmp_path):
        agent = _build_agent(tmp_path)
        try:
            pre = _invoke_set_low_priority_sender("newsletter@example.com")
            assert pre["ok"] is True, f"precondition failed: {pre}"
            before = _read_persisted_snapshot(tmp_path)
            assert before is not None

            result = _invoke_remove_priority_sender("ghost@example.com")
            assert result["ok"] is True, f"call itself should succeed: {result}"
            data = result["data"]
            assert data["removed"] is False, (
                "removing an address that was never set must report "
                f"removed: False, not claim success. Got: {data}"
            )
            assert isinstance(data.get("message"), str) and data["message"]
            _assert_snapshot_shape(data["preferences"])
            assert "persisted" not in data, (
                "a no-op removal must never report persistence fields at "
                f"all (nothing was written): {data}"
            )
            assert "persistence" not in data
            assert (
                "ghost@example.com"
                not in agent._session_preferences["priority_senders"]
            )
        finally:
            agent.close_db()

        after = _read_persisted_snapshot(tmp_path)
        assert after == before, (
            "a no-op removal must not touch the persisted store at all: "
            f"before={before}, after={after}"
        )


class TestRemovalDoesNotCrossContaminateSets:
    """Regression guard: the SET tools deliberately cross-clear the opposite
    set as a side effect (set_priority_sender discards the address from
    low_priority_senders, and vice versa) — but the REMOVE tools must have
    NO analogous side effect. Each remove tool may only ever touch its own
    target set."""

    def test_remove_low_priority_sender_does_not_add_to_priority(self, tmp_path):
        """Removing a low-priority sender must not promote it to priority —
        it ends up in NEITHER set."""
        agent = _build_agent(tmp_path)
        try:
            _invoke_set_low_priority_sender("newsletter@example.com")
            result = _invoke_remove_low_priority_sender("newsletter@example.com")
            assert result["data"]["removed"] is True

            prefs = agent._session_preferences
            assert "newsletter@example.com" not in prefs["low_priority_senders"]
            assert "newsletter@example.com" not in prefs["priority_senders"], (
                "removing a low-priority sender must not silently promote it "
                f"to priority_senders: {prefs}"
            )
        finally:
            agent.close_db()

    def test_remove_priority_sender_does_not_add_to_low_priority(self, tmp_path):
        """Mirror: removing a priority sender must not demote it to
        low-priority — it ends up in NEITHER set."""
        agent = _build_agent(tmp_path)
        try:
            _invoke_set_priority_sender("boss@example.com")
            result = _invoke_remove_priority_sender("boss@example.com")
            assert result["data"]["removed"] is True

            prefs = agent._session_preferences
            assert "boss@example.com" not in prefs["priority_senders"]
            assert "boss@example.com" not in prefs["low_priority_senders"], (
                "removing a priority sender must not silently demote it to "
                f"low_priority_senders: {prefs}"
            )
        finally:
            agent.close_db()

    def test_remove_low_priority_sender_leaves_priority_set_untouched(self, tmp_path):
        """The address is HIGH priority (never low-priority). Calling
        remove_low_priority_sender on it must not touch priority_senders at
        all, and must truthfully report removed: false (it was never in
        low_priority_senders)."""
        agent = _build_agent(tmp_path)
        try:
            set_result = _invoke_set_priority_sender("vip@example.com")
            assert set_result["ok"] is True

            remove_result = _invoke_remove_low_priority_sender("vip@example.com")
            assert remove_result["ok"] is True
            data = remove_result["data"]
            assert data["removed"] is False, (
                "vip@example.com was never in low_priority_senders, so "
                f"removed must be False: {data}"
            )
            assert "persisted" not in data
            assert "persistence" not in data

            prefs = agent._session_preferences
            assert "vip@example.com" in prefs["priority_senders"], (
                "remove_low_priority_sender must not touch priority_senders "
                f"at all, even for an address present there: {prefs}"
            )
            assert "vip@example.com" not in prefs["low_priority_senders"]
        finally:
            agent.close_db()

    def test_remove_priority_sender_leaves_low_priority_set_untouched(self, tmp_path):
        """Mirror: the address is LOW priority. remove_priority_sender on it
        must not touch low_priority_senders, and reports removed: false."""
        agent = _build_agent(tmp_path)
        try:
            set_result = _invoke_set_low_priority_sender("bot@example.com")
            assert set_result["ok"] is True

            remove_result = _invoke_remove_priority_sender("bot@example.com")
            assert remove_result["ok"] is True
            data = remove_result["data"]
            assert data["removed"] is False, (
                "bot@example.com was never in priority_senders, so removed "
                f"must be False: {data}"
            )
            assert "persisted" not in data
            assert "persistence" not in data

            prefs = agent._session_preferences
            assert "bot@example.com" in prefs["low_priority_senders"], (
                "remove_priority_sender must not touch low_priority_senders "
                f"at all: {prefs}"
            )
            assert "bot@example.com" not in prefs["priority_senders"]
        finally:
            agent.close_db()


class TestRemoveCategoryDefault:
    """AC: remove_category_default is truthful about whether an override
    existed, mirroring the removed/persisted contract of the sender-removal
    tools."""

    def test_remove_active_override_persists(self, tmp_path):
        agent_a = _build_agent(tmp_path)
        try:
            set_result = _invoke_set_category_default("FYI", "archive")
            assert set_result["ok"] is True, f"precondition failed: {set_result}"

            remove_result = _invoke_remove_category_default("FYI")
            assert remove_result["ok"] is True, f"remove failed: {remove_result}"
            data = remove_result["data"]
            assert data["removed"] is True, f"expected removed: True: {data}"
            assert isinstance(data.get("message"), str) and data["message"]
            _assert_snapshot_shape(data["preferences"])
            assert data["persisted"] is True
            assert data["persistence"] == "persisted"
            assert "note" not in data
            assert "FYI" not in agent_a._session_preferences["category_defaults"]
        finally:
            agent_a.close_db()

        stored = _read_persisted_snapshot(tmp_path)
        assert stored is not None
        assert (
            "FYI" not in stored["category_defaults"]
        ), f"category default removal was not persisted: {stored}"

        agent_b = _build_agent(tmp_path)
        try:
            assert "FYI" not in agent_b._session_preferences["category_defaults"]
        finally:
            agent_b.close_db()

    def test_remove_same_category_twice_is_truthful_on_second_call(self, tmp_path):
        """Removing an already-removed override a second time must not
        claim success again."""
        agent = _build_agent(tmp_path)
        try:
            _invoke_set_category_default("FYI", "archive")
            first = _invoke_remove_category_default("FYI")
            assert first["data"]["removed"] is True

            second = _invoke_remove_category_default("FYI")
            assert second["ok"] is True
            data = second["data"]
            assert data["removed"] is False, (
                "removing an already-removed category default a second "
                f"time must be truthful (removed: False): {data}"
            )
            assert "persisted" not in data
            assert "persistence" not in data
        finally:
            agent.close_db()

    def test_remove_category_default_never_set(self, tmp_path):
        """A category that was never set to 'archive' at all (fresh agent,
        implicit 'keep' default) -> removed: False."""
        agent = _build_agent(tmp_path)
        try:
            assert "FYI" not in agent._session_preferences["category_defaults"]
            result = _invoke_remove_category_default("FYI")
            assert result["ok"] is True
            data = result["data"]
            assert (
                data["removed"] is False
            ), f"category never set to archive must be removed: False: {data}"
            assert isinstance(data.get("message"), str) and data["message"]
            _assert_snapshot_shape(data["preferences"])
            assert "persisted" not in data
            assert "persistence" not in data
        finally:
            agent.close_db()

        stored = _read_persisted_snapshot(tmp_path)
        assert (
            stored is None
        ), f"a no-op removal must not create a persisted row: {stored}"

    def test_remove_category_default_invalid_category_is_rejected(self, tmp_path):
        """category must be one of _CATEGORIES_WITH_DEFAULTS, same
        validation the set tool applies."""
        agent = _build_agent(tmp_path)
        try:
            for bogus in ("URGENT", "BOGUS", "", "NEEDS_RESPONSE"):
                result = _invoke_remove_category_default(bogus)
                assert result["ok"] is False, (
                    f"expected an error envelope for category={bogus!r}, "
                    f"got: {result}"
                )
        finally:
            agent.close_db()


class TestRemovalInvalidEmailInput:
    """The remove tools reject invalid email input identically to the SET
    tools — empty, no '@', and bracketed header-style values are all
    rejected with an error envelope, never treated as a valid target."""

    @pytest.mark.parametrize(
        "bad_email",
        [
            "",
            "not-an-email",
            "Alice <alice@example.com>",
        ],
    )
    def test_remove_priority_sender_rejects_invalid_email(self, tmp_path, bad_email):
        agent = _build_agent(tmp_path)
        try:
            result = _invoke_remove_priority_sender(bad_email)
            assert result["ok"] is False, (
                f"expected an error envelope for email={bad_email!r}, " f"got: {result}"
            )
        finally:
            agent.close_db()

    @pytest.mark.parametrize(
        "bad_email",
        [
            "",
            "not-an-email",
            "Alice <alice@example.com>",
        ],
    )
    def test_remove_low_priority_sender_rejects_invalid_email(
        self, tmp_path, bad_email
    ):
        agent = _build_agent(tmp_path)
        try:
            result = _invoke_remove_low_priority_sender(bad_email)
            assert result["ok"] is False, (
                f"expected an error envelope for email={bad_email!r}, " f"got: {result}"
            )
        finally:
            agent.close_db()


class TestGetPreferences:
    """AC: get_preferences is a read-only tool that reflects exactly what
    the SET tools have stored — #2520/#2519 note the agent currently claims
    to have no such tool at all, which is part of why a removal failure is
    undiscoverable from the chat."""

    def test_get_preferences_reflects_all_three_collections(self, tmp_path):
        agent = _build_agent(tmp_path)
        try:
            assert _invoke_set_priority_sender("boss@company.com")["ok"] is True
            assert (
                _invoke_set_low_priority_sender("newsletter@example.com")["ok"] is True
            )
            assert _invoke_set_category_default("FYI", "archive")["ok"] is True

            result = _invoke_get_preferences()
            assert result["ok"] is True, f"get_preferences failed: {result}"
            assert set(result["data"].keys()) == {"preferences"}, (
                "get_preferences data envelope must contain exactly "
                f"'preferences': {result['data']}"
            )
            snapshot = result["data"]["preferences"]
            _assert_snapshot_shape(snapshot)
            assert snapshot["priority_senders"] == ["boss@company.com"]
            assert snapshot["low_priority_senders"] == ["newsletter@example.com"]
            assert snapshot["category_defaults"] == {"FYI": "archive"}
        finally:
            agent.close_db()

    def test_get_preferences_empty_when_nothing_set(self, tmp_path):
        """A fresh agent with nothing set returns empty collections, not an
        error."""
        agent = _build_agent(tmp_path)
        try:
            result = _invoke_get_preferences()
            assert result["ok"] is True, f"get_preferences failed: {result}"
            snapshot = result["data"]["preferences"]
            _assert_snapshot_shape(snapshot)
            assert snapshot["priority_senders"] == []
            assert snapshot["low_priority_senders"] == []
            assert snapshot["category_defaults"] == {}
        finally:
            agent.close_db()

    def test_get_preferences_verifies_a_removal_took_effect(self, tmp_path):
        """The exact verification loop #2520 says is currently impossible
        ("there is no way to discover the discrepancy from the chat"): read
        preferences, remove one, read again, confirm it is gone."""
        agent = _build_agent(tmp_path)
        try:
            _invoke_set_low_priority_sender("newsletters@techcrunch.com")
            before = _invoke_get_preferences()["data"]["preferences"]
            assert "newsletters@techcrunch.com" in before["low_priority_senders"]

            remove_result = _invoke_remove_low_priority_sender(
                "newsletters@techcrunch.com"
            )
            assert remove_result["data"]["removed"] is True

            after = _invoke_get_preferences()["data"]["preferences"]
            assert (
                "newsletters@techcrunch.com" not in after["low_priority_senders"]
            ), f"get_preferences still shows the removed sender: {after}"
        finally:
            agent.close_db()


class TestIncognitoRemovalGate:
    """Mirrors TestIncognitoGate for the SET tools: a removal performed
    while incognito must NOT reach the durable store, even though the
    in-process mutation happens and is reported truthfully."""

    def test_incognito_removal_not_persisted(self, tmp_path):
        """Persist a priority sender normally, then remove it from a
        SEPARATE incognito session. The removal must apply in-process
        (removed: true) but must be reported as session-only
        (persisted: false, with a note), and a fresh non-incognito session
        afterward must still see the sender — the incognito removal never
        reached the durable store."""
        # Session A — normal (non-incognito): persist the sender for real.
        agent_a = _build_agent(tmp_path)
        try:
            set_result = _invoke_set_priority_sender("boss@company.com")
            assert set_result["ok"] is True
            assert set_result["data"]["persisted"] is True
        finally:
            agent_a.close_db()

        # Session B — incognito: remove it. In-process mutation happens, but
        # persistence must be suppressed.
        agent_b = _build_agent(tmp_path)
        try:
            agent_b._incognito = True
            remove_result = _invoke_remove_priority_sender("boss@company.com")
            assert remove_result["ok"] is True, f"remove failed: {remove_result}"
            data = remove_result["data"]
            assert data["removed"] is True, (
                "the in-process removal must still happen under incognito: " f"{data}"
            )
            assert (
                "boss@company.com"
                not in agent_b._session_preferences["priority_senders"]
            )
            assert (
                data["persisted"] is False
            ), f"incognito removal must NOT claim a durable save: {data}"
            assert data["persistence"] == "incognito"
            assert "SESSION ONLY" in data["note"].upper()
        finally:
            agent_b.close_db()

        # Session C — fresh, non-incognito: the durable store must be
        # untouched by session B's incognito removal.
        agent_c = _build_agent(tmp_path)
        try:
            assert (
                "boss@company.com" in agent_c._session_preferences["priority_senders"]
            ), (
                "an incognito removal must not reach the durable store — the "
                "sender set in session A must still be present in a fresh "
                "non-incognito session. Got: "
                f"{agent_c._session_preferences['priority_senders']}"
            )
        finally:
            agent_c.close_db()

        stored = _read_persisted_snapshot(tmp_path)
        assert stored is not None
        assert (
            "boss@company.com" in stored["priority_senders"]
        ), f"incognito removal leaked into state.db: {stored}"
