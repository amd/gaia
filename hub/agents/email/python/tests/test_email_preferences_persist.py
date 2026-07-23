# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Cross-session preference persistence tests for EmailTriageAgent.

Acceptance criteria covered:
- AC (main): priority/low-priority sender set in one session is present in a fresh session.
- AC (main): category-default action persists across restarts.
- Test-AC: priority sender set in session A is in _session_preferences in session B.
- Test-AC: clear_session_preferences clears persistence so a new session starts empty.

#2427: preferences persist in the agent's state.db (like the trust ledger), NOT
in the embedding-backed MemoryStore, so they survive across sessions even when
memory v2 is disabled (embedding model absent). The TestMemoryDisabledFallback
and TestHonestPersistenceStatus classes cover that fix directly.

Embedder is mocked out (same pattern as test_email_memory.py) so the
memory-enabled tests run hermetically without Lemonade.
"""

from __future__ import annotations

import json
import os
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

    Mocks the Lemonade embedding endpoint so init_memory succeeds
    without a running Lemonade server (FTS5-only store/search path).
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


def _build_agent_no_memory(tmp_path: Path) -> EmailTriageAgent:
    """Build an agent with memory v2 disabled — the #2427 field scenario.

    ``GAIA_MEMORY_DISABLED=1`` forces ``_memory_store`` to None, reproducing
    the state where the embedding model 404s from Lemonade. No embedder
    patching is needed because memory init is short-circuited. Preferences
    must still persist via state.db, which is independent of the embedder.
    """
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    old = os.environ.get("GAIA_MEMORY_DISABLED")
    os.environ["GAIA_MEMORY_DISABLED"] = "1"
    try:
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            agent = EmailTriageAgent(config=cfg)
    finally:
        if old is None:
            del os.environ["GAIA_MEMORY_DISABLED"]
        else:
            os.environ["GAIA_MEMORY_DISABLED"] = old
    assert agent._memory_store is None, "expected memory disabled (_memory_store is None)"
    return agent


def _invoke_set_priority_sender(email: str) -> dict:
    """Call the set_priority_sender tool directly via the tool registry."""
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


def _invoke_clear_session_preferences() -> dict:
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("clear_session_preferences")
    assert entry is not None, "clear_session_preferences not registered"
    result = entry["function"]()
    return json.loads(result)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPrioritySenderPersistsAcrossRestart:
    """AC: priority sender set in session A is present in session B."""

    def test_priority_sender_survives_restart(self, tmp_path):
        """Set a priority sender in agent A; it must be in _session_preferences
        of a freshly-constructed agent B using the same memory.db."""
        # Session A — set sender and close
        agent_a = _build_agent(tmp_path)
        try:
            result = _invoke_set_priority_sender("boss@company.com")
            assert result["ok"] is True, f"set_priority_sender failed: {result}"
        finally:
            agent_a.close_db()

        # Session B — fresh instance, same db
        agent_b = _build_agent(tmp_path)
        try:
            assert (
                "boss@company.com" in agent_b._session_preferences["priority_senders"]
            ), (
                "priority sender not restored from memory after restart. "
                f"Got: {agent_b._session_preferences['priority_senders']}"
            )
        finally:
            agent_b.close_db()

    def test_low_priority_sender_survives_restart(self, tmp_path):
        """Set a low-priority sender in session A; it persists into session B."""
        agent_a = _build_agent(tmp_path)
        try:
            result = _invoke_set_low_priority_sender("newsletter@stripe.com")
            assert result["ok"] is True, f"set_low_priority_sender failed: {result}"
        finally:
            agent_a.close_db()

        agent_b = _build_agent(tmp_path)
        try:
            assert (
                "newsletter@stripe.com"
                in agent_b._session_preferences["low_priority_senders"]
            ), (
                "low_priority sender not restored from memory after restart. "
                f"Got: {agent_b._session_preferences['low_priority_senders']}"
            )
        finally:
            agent_b.close_db()

    def test_multiple_senders_survive_restart(self, tmp_path):
        """Multiple priority and low-priority senders all persist."""
        agent_a = _build_agent(tmp_path)
        try:
            _invoke_set_priority_sender("boss@company.com")
            _invoke_set_priority_sender("cto@company.com")
            _invoke_set_low_priority_sender("news@example.com")
        finally:
            agent_a.close_db()

        agent_b = _build_agent(tmp_path)
        try:
            assert (
                "boss@company.com" in agent_b._session_preferences["priority_senders"]
            )
            assert "cto@company.com" in agent_b._session_preferences["priority_senders"]
            assert (
                "news@example.com"
                in agent_b._session_preferences["low_priority_senders"]
            )
        finally:
            agent_b.close_db()

    def test_no_duplicate_state_db_records(self, tmp_path):
        """Writing preferences multiple times keeps exactly one state.db row.

        #2427: persistence moved from MemoryStore to the single-row
        ``email_preferences`` table in state.db. The upsert must keep the row
        count at one no matter how many times a preference is written.
        """
        agent_a = _build_agent(tmp_path)
        try:
            # Write the same sender several times
            for _ in range(5):
                _invoke_set_priority_sender("boss@company.com")
        finally:
            agent_a.close_db()

        # Re-open state.db directly and count preference rows.
        import sqlite3

        conn = sqlite3.connect(tmp_path / "state.db")
        try:
            rows = conn.execute("SELECT key, value FROM email_preferences").fetchall()
        finally:
            conn.close()
        assert (
            len(rows) == 1
        ), f"Expected exactly 1 email_preferences row, got {len(rows)}: {rows}"
        stored = json.loads(rows[0][1])
        assert "boss@company.com" in stored["priority_senders"]


class TestCategoryDefaultPersistsAcrossRestart:
    """AC: category-default action persists across restarts."""

    def test_category_default_survives_restart(self, tmp_path):
        """Set FYI→archive in session A; it's present in session B."""
        agent_a = _build_agent(tmp_path)
        try:
            result = _invoke_set_category_default("FYI", "archive")
            assert result["ok"] is True, f"set_category_default failed: {result}"
        finally:
            agent_a.close_db()

        agent_b = _build_agent(tmp_path)
        try:
            defaults = agent_b._session_preferences["category_defaults"]
            assert (
                defaults.get("FYI") == "archive"
            ), f"category_default not restored. Got: {defaults}"
        finally:
            agent_b.close_db()

    def test_category_default_keep_clears_persisted_archive(self, tmp_path):
        """Setting a category to 'keep' removes the archive preference on restart."""
        # Session A: set archive, then flip back to keep
        agent_a = _build_agent(tmp_path)
        try:
            _invoke_set_category_default("FYI", "archive")
            _invoke_set_category_default("FYI", "keep")
        finally:
            agent_a.close_db()

        agent_b = _build_agent(tmp_path)
        try:
            defaults = agent_b._session_preferences["category_defaults"]
            assert "FYI" not in defaults, (
                f"'keep' should remove FYI from category_defaults, "
                f"but got: {defaults}"
            )
        finally:
            agent_b.close_db()


class TestClearPersistenceAcrossRestart:
    """AC: clear_session_preferences clears persistence so session B starts empty."""

    def test_clear_wipes_persisted_preferences(self, tmp_path):
        """Set sender + default, then clear; session B starts empty."""
        agent_a = _build_agent(tmp_path)
        try:
            _invoke_set_priority_sender("boss@company.com")
            _invoke_set_category_default("FYI", "archive")
            result = _invoke_clear_session_preferences()
            assert result["ok"] is True, f"clear_session_preferences failed: {result}"
        finally:
            agent_a.close_db()

        agent_b = _build_agent(tmp_path)
        try:
            prefs = agent_b._session_preferences
            assert len(prefs["priority_senders"]) == 0, (
                f"priority_senders should be empty after clear+restart, "
                f"got: {prefs['priority_senders']}"
            )
            assert len(prefs["low_priority_senders"]) == 0, (
                f"low_priority_senders should be empty after clear+restart, "
                f"got: {prefs['low_priority_senders']}"
            )
            assert len(prefs["category_defaults"]) == 0, (
                f"category_defaults should be empty after clear+restart, "
                f"got: {prefs['category_defaults']}"
            )
        finally:
            agent_b.close_db()


class TestIncognitoGate:
    """When _incognito=True, preference mutations work in-process but are NOT persisted."""

    def test_incognito_preferences_not_persisted(self, tmp_path):
        """Setting a priority sender while incognito must NOT be written to the store.

        A subsequent non-incognito session must NOT see the incognito-session sender.
        """
        # Session A — incognito; set a sender
        agent_a = _build_agent(tmp_path)
        try:
            agent_a._incognito = True
            result = _invoke_set_priority_sender("secret@example.com")
            assert result["ok"] is True, f"set_priority_sender failed: {result}"
            # In-process state is mutated even in incognito
            assert (
                "secret@example.com" in agent_a._session_preferences["priority_senders"]
            )
        finally:
            agent_a.close_db()

        # Session B — non-incognito; sender must NOT be present
        agent_b = _build_agent(tmp_path)
        try:
            assert (
                "secret@example.com"
                not in agent_b._session_preferences["priority_senders"]
            ), (
                "Incognito session must not persist preferences to the memory store. "
                f"Got: {agent_b._session_preferences['priority_senders']}"
            )
        finally:
            agent_b.close_db()

    def test_non_incognito_preferences_are_persisted(self, tmp_path):
        """Sanity: a normal (non-incognito) session DOES persist the preference."""
        agent_a = _build_agent(tmp_path)
        try:
            # _incognito defaults to False; explicitly confirm
            agent_a._incognito = False
            result = _invoke_set_priority_sender("visible@example.com")
            assert result["ok"] is True, f"set_priority_sender failed: {result}"
        finally:
            agent_a.close_db()

        agent_b = _build_agent(tmp_path)
        try:
            assert (
                "visible@example.com"
                in agent_b._session_preferences["priority_senders"]
            ), (
                "Non-incognito session must persist preference to the memory store. "
                f"Got: {agent_b._session_preferences['priority_senders']}"
            )
        finally:
            agent_b.close_db()


class TestMemoryDisabledFallback:
    """#2427: with memory v2 disabled (embedding model absent) preferences
    still mutate in-session AND persist across sessions via state.db."""

    def test_preferences_work_in_session_without_memory(self, tmp_path):
        """GAIA_MEMORY_DISABLED=1 — preferences mutate in-memory but don't crash."""
        cfg = EmailAgentConfig(
            gmail_backend=_MinimalMailBackend(),
            calendar_backend=_MinimalCalendarBackend(),
            db_path=str(tmp_path / "state.db"),
            memory_db_path=str(tmp_path / "memory.db"),
            silent_mode=True,
            debug=False,
        )

        old = os.environ.get("GAIA_MEMORY_DISABLED")
        os.environ["GAIA_MEMORY_DISABLED"] = "1"
        try:
            with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
                mock_sdk.return_value = MagicMock()
                agent = EmailTriageAgent(config=cfg)
            try:
                # Memory is disabled — _memory_store should be None
                assert agent._memory_store is None

                # Preference tools should still work (in-process mutation)
                result = _invoke_set_priority_sender("boss@company.com")
                assert result["ok"] is True, f"set_priority_sender failed: {result}"
                assert (
                    "boss@company.com" in agent._session_preferences["priority_senders"]
                )
            finally:
                agent.close_db()
        finally:
            if old is None:
                del os.environ["GAIA_MEMORY_DISABLED"]
            else:
                os.environ["GAIA_MEMORY_DISABLED"] = old

    def test_priority_sender_persists_across_sessions_without_memory(self, tmp_path):
        """#2427 core (AC-1 persist branch + AC-2): with the embedding model
        absent (memory disabled), a captured priority-sender rule STILL
        persists to state.db, is restored in a fresh session, and is honored
        on re-triage — and the tool honestly reports ``persisted: true``."""
        # Session A — memory disabled; capture the rule (T21).
        agent_a = _build_agent_no_memory(tmp_path)
        try:
            result = _invoke_set_priority_sender("newsletters@techcrunch.com")
            assert result["ok"] is True, f"set_priority_sender failed: {result}"
            # Honest success: it really WAS persisted (state.db needs no embedder).
            assert result["data"]["persisted"] is True, (
                "with memory disabled the rule must still persist to state.db, "
                f"so persisted must be True. Got: {result['data']}"
            )
            assert result["data"]["persistence"] == "persisted"
            assert "note" not in result["data"]
        finally:
            agent_a.close_db()

        # Session B — fresh instance, still memory-disabled, same state.db (T22).
        agent_b = _build_agent_no_memory(tmp_path)
        try:
            assert (
                "newsletters@techcrunch.com"
                in agent_b._session_preferences["priority_senders"]
            ), (
                "priority sender captured with memory disabled was not restored "
                f"in a new session. Got: {agent_b._session_preferences['priority_senders']}"
            )
            # [Reflection C1] Prove the restored rule is APPLIED during triage,
            # not merely present in the dict — this is what T22 actually checks.
            from gaia_agent_email.tools.read_tools import _apply_session_preferences
            from gaia_agent_email.tools.triage_heuristics import CATEGORY_URGENT

            decision = {
                "from": "TechCrunch <newsletters@techcrunch.com>",
                "category": "FYI",
                "confident": True,
            }
            out = _apply_session_preferences(decision, agent_b._session_preferences)
            assert out["category"] == CATEGORY_URGENT, (
                "restored priority-sender rule must re-classify the sender's "
                f"mail as urgent on re-triage; got: {out}"
            )
            assert out["preference_applied"] == "priority_sender"
        finally:
            agent_b.close_db()


class TestHonestPersistenceStatus:
    """#2427 (AC-1 honest-statement branch): the tool never claims a durable
    save when nothing was written — it reports session-only honestly."""

    def test_normal_mode_reports_persisted(self, tmp_path):
        """A normal (non-incognito, db-ready) write reports persisted: true."""
        agent = _build_agent(tmp_path)
        try:
            result = _invoke_set_priority_sender("boss@company.com")
            assert result["ok"] is True
            data = result["data"]
            assert data["persisted"] is True
            assert data["persistence"] == "persisted"
            assert "note" not in data
        finally:
            agent.close_db()

    def test_incognito_reports_session_only(self, tmp_path):
        """An incognito write reports persisted: false + a session-only note."""
        agent = _build_agent(tmp_path)
        try:
            agent._incognito = True
            result = _invoke_set_priority_sender("secret@example.com")
            assert result["ok"] is True
            data = result["data"]
            # In-process mutation still happens; only persistence is suppressed.
            assert "secret@example.com" in agent._session_preferences["priority_senders"]
            assert data["persisted"] is False, (
                "incognito must NOT claim a durable save; "
                f"got: {data}"
            )
            assert data["persistence"] == "incognito"
            assert "SESSION ONLY" in data["note"].upper()
        finally:
            agent.close_db()

    def test_db_unavailable_reports_session_only(self, tmp_path):
        """When the state.db handle is not ready, report session-only, not success.

        Simulated by closing the db before the write — ``db_ready`` is then
        False, the fail-loudly guard the issue is fundamentally about.
        """
        agent = _build_agent(tmp_path)
        agent.close_db()
        assert agent.db_ready is False
        result = _invoke_set_priority_sender("boss@company.com")
        assert result["ok"] is True
        data = result["data"]
        assert data["persisted"] is False, (
            "an unavailable persistence layer must surface, not claim success; "
            f"got: {data}"
        )
        assert data["persistence"] == "unavailable"
        assert "SESSION ONLY" in data["note"].upper()

    def test_category_default_and_clear_report_persistence(self, tmp_path):
        """The other two preference tools also report the persistence outcome."""
        agent = _build_agent(tmp_path)
        try:
            cat = _invoke_set_category_default("FYI", "archive")
            assert cat["data"]["persisted"] is True
            assert cat["data"]["persistence"] == "persisted"

            cleared = _invoke_clear_session_preferences()
            assert cleared["data"]["persisted"] is True
            assert cleared["data"]["persistence"] == "persisted"
        finally:
            agent.close_db()
