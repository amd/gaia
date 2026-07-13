# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Cross-session preference persistence tests for EmailTriageAgent.

Acceptance criteria covered:
- AC (main): priority/low-priority sender set in one session is present in a fresh session.
- AC (main): category-default action persists across restarts.
- Test-AC: priority sender set in session A is in _session_preferences in session B.
- Test-AC: clear_session_preferences clears persistence so a new session starts empty.

Embedder is mocked out (same pattern as test_email_memory.py) so tests run
hermetically without Lemonade.  GAIA_MEMORY_DISABLED=1 is NOT used here because
we need _memory_store to be set for preference persistence to work.
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

_PREF_ENTITY = "email:preferences"
_PREF_DOMAIN = "email_agent_prefs"
_PREF_CATEGORY = "preference"


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

    def test_no_duplicate_memory_records(self, tmp_path):
        """Writing preferences multiple times does not accumulate duplicate records."""
        agent_a = _build_agent(tmp_path)
        try:
            # Write the same sender several times
            for _ in range(5):
                _invoke_set_priority_sender("boss@company.com")
        finally:
            agent_a.close_db()

        # Re-open the memory store directly and count records for the entity
        from gaia.agents.base.memory_store import MemoryStore

        store = MemoryStore(tmp_path / "memory.db")
        rows = store.get_by_entity(_PREF_ENTITY)
        assert (
            len(rows) == 1
        ), f"Expected exactly 1 memory record for {_PREF_ENTITY!r}, got {len(rows)}: {rows}"


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
    """When GAIA_MEMORY_DISABLED=1, preferences still work in-session but aren't persisted."""

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
