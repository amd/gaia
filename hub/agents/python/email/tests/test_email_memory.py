# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MemoryMixin integration tests for EmailTriageAgent.

Acceptance criteria covered:
- AC (main): EmailTriageAgent composes MemoryMixin; memory.db and email/state.db
  coexist without conflict.
- AC (main): Memory tools are registered and available to the agent.
- Test-AC: unit test asserts a remembered value persists across an agent restart.
- Test-AC: test asserts the two databases initialize and operate independently.

All tests run WITHOUT Lemonade — the embedder is mocked out so the test suite
is hermetic and runs in CI.  GAIA_MEMORY_DISABLED=1 is used for the
tools-registered and independent-dbs tests (no live recall needed);
the persistence test mocks the embedder so register_memory_tools() actually
fires and the MemoryStore can be exercised.
"""

from __future__ import annotations

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
# Minimal fake backends (no FakeGmailBackend import needed — these tests
# only touch the memory/db layer, not the Gmail API surface).
# ---------------------------------------------------------------------------


class _MinimalMailBackend:
    """Satisfies the GmailBackend protocol just enough for EmailTriageAgent
    to construct without hitting a live mailbox.
    """

    pass


class _MinimalCalendarBackend:
    """Satisfies the CalendarBackend protocol just enough for construction."""

    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MEMORY_TOOL_NAMES = frozenset(
    {"remember", "recall", "update_memory", "forget", "search_past_conversations"}
)

EMBEDDING_DIM = 768


def _fake_embed(text: str) -> np.ndarray:
    """Return a deterministic unit vector so FAISS stays happy."""
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _build_agent(
    tmp_path: Path,
    *,
    memory_disabled: bool = False,
    memory_enabled: bool = True,
) -> EmailTriageAgent:
    """Build an EmailTriageAgent with injected fake backends and tmp db paths.

    When *memory_disabled* is True the function sets GAIA_MEMORY_DISABLED=1
    before construction and restores it after — the returned agent will have
    ``_memory_store = None`` and no memory tools registered.

    When *memory_disabled* is False the Lemonade embedder is mocked so the
    agent constructs without a running Lemonade server.

    *memory_enabled* maps to ``EmailAgentConfig.memory_enabled`` (#1666): with
    a live store and ``memory_enabled=False`` the agent starts in incognito.
    """
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
        memory_enabled=memory_enabled,
    )

    def _do_build():
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            return EmailTriageAgent(config=cfg)

    if memory_disabled:
        old = os.environ.get("GAIA_MEMORY_DISABLED")
        os.environ["GAIA_MEMORY_DISABLED"] = "1"
        try:
            return _do_build()
        finally:
            if old is None:
                del os.environ["GAIA_MEMORY_DISABLED"]
            else:
                os.environ["GAIA_MEMORY_DISABLED"] = old
    else:
        # Mock the Lemonade embedding endpoint so init_memory succeeds hermetically.
        with (
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
            return _do_build()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMemoryToolsRegistered:
    """AC: Memory tools are registered and available to the agent."""

    def test_five_memory_tools_in_registry(self, tmp_path):
        """All 5 memory CRUD tools appear in the tool registry after construction."""
        agent = _build_agent(tmp_path)
        try:
            from gaia.agents.base.tools import _TOOL_REGISTRY

            registered = set(_TOOL_REGISTRY.keys())
            missing = _MEMORY_TOOL_NAMES - registered
            assert not missing, (
                f"Memory tools missing from registry: {missing}. "
                f"Registered tools: {sorted(registered)}"
            )
        finally:
            agent.close_db()

    def test_memory_tools_alongside_email_tools(self, tmp_path):
        """Memory tools coexist with email-specific tools in the registry."""
        agent = _build_agent(tmp_path)
        try:
            from gaia.agents.base.tools import _TOOL_REGISTRY

            registered = set(_TOOL_REGISTRY.keys())
            # Spot-check an email-specific tool
            assert "list_inbox" in registered, "email tool list_inbox missing"
            # And all 5 memory tools
            assert _MEMORY_TOOL_NAMES.issubset(registered), (
                f"Memory tools not a subset of registry. Missing: "
                f"{_MEMORY_TOOL_NAMES - registered}"
            )
        finally:
            agent.close_db()

    def test_memory_tools_absent_when_disabled(self, tmp_path):
        """When GAIA_MEMORY_DISABLED=1, no memory tools are registered."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            from gaia.agents.base.tools import _TOOL_REGISTRY

            registered = set(_TOOL_REGISTRY.keys())
            overlap = _MEMORY_TOOL_NAMES & registered
            assert not overlap, (
                f"Memory tools should NOT be registered when "
                f"GAIA_MEMORY_DISABLED=1, but found: {overlap}"
            )
        finally:
            agent.close_db()


class TestMemoryPersistence:
    """Test-AC: a remembered value persists across an agent restart."""

    def test_remember_persists_across_restart(self, tmp_path):
        """Store a value in agent instance A; a fresh instance B at the same
        db path can read it back via the MemoryStore directly.
        """
        from gaia.agents.base.memory_store import MemoryStore

        memory_db = tmp_path / "memory.db"
        state_db = tmp_path / "state.db"

        # Build first agent instance and store a fact.
        agent_a = _build_agent(tmp_path)
        try:
            assert agent_a._memory_store is not None, (
                "_memory_store should be set — embedder was mocked but memory "
                "init should have completed"
            )
            store_a = agent_a._memory_store
            store_a.store(
                content="test fact for persistence",
                category="fact",
                context="email",
            )
        finally:
            agent_a.close_db()

        # Re-open the same memory DB directly (simulates a restarted agent
        # connecting to the same on-disk store) and verify the fact is there.
        store_b = MemoryStore(memory_db)
        rows = store_b.search("test fact for persistence", top_k=10)
        contents = [r["content"] for r in rows]
        assert any(
            "test fact for persistence" in c for c in contents
        ), f"Stored fact not found after restart. Got: {contents}"

        # Also confirm state.db exists and is a separate file.
        assert state_db.exists(), "state.db should exist"
        assert memory_db.exists(), "memory.db should exist"
        assert state_db != memory_db, "state.db and memory.db must be distinct files"


class TestIndependentDatabases:
    """Test-AC: state.db and memory.db initialize and operate independently."""

    def test_both_dbs_created(self, tmp_path):
        """Both state.db and memory.db are created during agent construction."""
        agent = _build_agent(tmp_path, memory_disabled=False)
        try:
            state_db = tmp_path / "state.db"
            memory_db = tmp_path / "memory.db"
            assert state_db.exists(), "state.db not created"
            assert memory_db.exists(), "memory.db not created"
        finally:
            agent.close_db()

    def test_dbs_are_distinct_files(self, tmp_path):
        """state.db and memory.db resolve to different paths."""
        agent = _build_agent(tmp_path)
        try:
            state_path = Path(agent.config.resolved_db_path()).resolve()
            memory_path = Path(agent.config.resolved_memory_db_path()).resolve()
            assert state_path != memory_path, (
                f"state.db and memory.db must be different files, "
                f"both resolved to {state_path}"
            )
        finally:
            agent.close_db()

    def test_writing_to_state_db_does_not_affect_memory_db(self, tmp_path):
        """Inserting a row into email_actions (state.db) leaves memory.db untouched."""
        import sqlite3

        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            state_db = tmp_path / "state.db"
            memory_db = tmp_path / "memory.db"
            assert state_db.exists(), "state.db not created"
            # Memory is disabled in this branch, so memory.db may or may not exist.
            # What matters is that they are distinct paths.
            assert state_db != memory_db

            # Get the row count in state.db (email_actions table)
            with sqlite3.connect(state_db) as conn:
                before = conn.execute("SELECT COUNT(*) FROM email_actions").fetchone()[
                    0
                ]

            # Insert a dummy action row directly (using the real schema from action_store.py).
            with sqlite3.connect(state_db) as conn:
                conn.execute(
                    "INSERT INTO email_actions "
                    "(action_id, action_type, message_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("test-action-id", "archive", "fake-msg-id", "{}", 0.0),
                )

            with sqlite3.connect(state_db) as conn:
                after = conn.execute("SELECT COUNT(*) FROM email_actions").fetchone()[0]

            assert after == before + 1, "state.db row count should have increased by 1"

            # memory.db should be untouched — either it doesn't exist (disabled)
            # or, if it does, its knowledge_items table has no new rows.
            if memory_db.exists():
                with sqlite3.connect(memory_db) as conn:
                    tables = [
                        r[0]
                        for r in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    ]
                    if "knowledge_items" in tables:
                        mem_count = conn.execute(
                            "SELECT COUNT(*) FROM knowledge_items"
                        ).fetchone()[0]
                        assert (
                            mem_count == 0
                        ), "memory.db should not have gained rows from a state.db write"
        finally:
            agent.close_db()

    def test_memory_context_is_email(self, tmp_path):
        """The memory context is set to 'email' (not 'global') for the email agent."""
        agent = _build_agent(tmp_path)
        try:
            assert (
                agent._memory_context == "email"
            ), f"Expected memory context 'email', got {agent._memory_context!r}"
        finally:
            agent.close_db()

    def test_config_memory_db_path_namespaced(self, tmp_path):
        """resolved_memory_db_path() returns a path namespaced under email/."""
        cfg = EmailAgentConfig(
            gmail_backend=_MinimalMailBackend(),
            calendar_backend=_MinimalCalendarBackend(),
        )
        path = Path(cfg.resolved_memory_db_path())
        # Default path should be ~/.gaia/email/memory.db
        assert path.name == "memory.db", f"Expected memory.db, got {path.name}"
        assert "email" in str(
            path
        ), f"Memory db path should be namespaced under email/, got {path}"

    def test_config_memory_db_path_injectable(self, tmp_path):
        """When memory_db_path is set in config, resolved_memory_db_path() returns it."""
        custom = str(tmp_path / "custom_memory.db")
        cfg = EmailAgentConfig(
            gmail_backend=_MinimalMailBackend(),
            calendar_backend=_MinimalCalendarBackend(),
            memory_db_path=custom,
        )
        assert cfg.resolved_memory_db_path() == custom


class TestRuntimeMemoryToggle:
    """#1666: runtime enable/disable of the agent's memory.

    Covers the write-gate (inbox profiling #1289, behavioral learning #1290,
    preference persistence #1288) and the read path (working-context injection),
    driven by both ``EmailAgentConfig.memory_enabled`` (construction) and
    ``set_memory_enabled`` (runtime), without an env var + restart.
    """

    _INTERACTION_ENTITY = "email:interaction:alice@example.com"

    def test_default_is_not_incognito(self, tmp_path):
        """A live store with memory_enabled=True (default) leaves the agent
        non-incognito — the runtime toggle changes nothing about existing
        behavior by default."""
        agent = _build_agent(tmp_path)
        try:
            assert agent._memory_store is not None
            assert agent._incognito is False
        finally:
            agent.close_db()

    def test_config_memory_enabled_false_starts_incognito(self, tmp_path):
        """memory_enabled=False constructs a live-store agent that starts in
        incognito — memory is off from the first turn, no restart required."""
        agent = _build_agent(tmp_path, memory_enabled=False)
        try:
            # Store IS initialized (this is a runtime toggle, not the startup
            # GAIA_MEMORY_DISABLED opt-out), but writes/reads are gated.
            assert agent._memory_store is not None
            assert agent._incognito is True
        finally:
            agent.close_db()

    def test_incognito_suppresses_inbox_profiling_write(self, tmp_path):
        """_record_interaction must not persist when memory is off (#1289 gate)."""
        agent = _build_agent(tmp_path, memory_enabled=False)
        try:
            agent._record_interaction("alice@example.com", "URGENT")
            rows = agent._memory_store.get_by_entity(self._INTERACTION_ENTITY)
            assert rows == [], f"incognito must skip profiling write, got: {rows}"
        finally:
            agent.close_db()

    def test_memory_on_records_inbox_profiling_write(self, tmp_path):
        """Regression guard: with memory on, profiling still writes."""
        agent = _build_agent(tmp_path)  # memory_enabled=True (default)
        try:
            agent._record_interaction("alice@example.com", "URGENT")
            rows = agent._memory_store.get_by_entity(self._INTERACTION_ENTITY)
            assert rows, "memory-on agent should record the interaction"
        finally:
            agent.close_db()

    def test_incognito_gates_profile_read_of_prior_data(self, tmp_path):
        """Data written while memory was on must not be surfaced by a read
        (``_read_interactions``/``profile_inbox``) once memory is toggled off."""
        agent = _build_agent(tmp_path)
        try:
            agent._record_interaction("alice@example.com", "URGENT")
            assert agent._read_interactions(), "sanity: write happened while on"

            agent.set_memory_enabled(False)
            assert (
                agent._read_interactions() == []
            ), "incognito must not surface stored interaction history on read"
        finally:
            agent.close_db()

    def test_set_memory_enabled_toggles_write_gate_at_runtime(self, tmp_path):
        """set_memory_enabled(False) then (True) flips the write gate on a live
        instance — no reconstruction."""
        agent = _build_agent(tmp_path)
        try:
            # Turn OFF at runtime — the write is skipped.
            agent.set_memory_enabled(False)
            assert agent._incognito is True
            agent._record_interaction("alice@example.com", "URGENT")
            assert agent._memory_store.get_by_entity(self._INTERACTION_ENTITY) == []

            # Turn back ON — the next write persists.
            agent.set_memory_enabled(True)
            assert agent._incognito is False
            agent._record_interaction("alice@example.com", "URGENT")
            assert agent._memory_store.get_by_entity(self._INTERACTION_ENTITY)
        finally:
            agent.close_db()

    def test_incognito_suppresses_behavioral_promotions(self, tmp_path):
        """_evaluate_promotions returns [] when memory is off (#1290 read gate)."""
        agent = _build_agent(tmp_path, memory_enabled=False)
        try:
            assert agent._evaluate_promotions() == []
        finally:
            agent.close_db()

    def test_incognito_gates_working_context_read_path(self, tmp_path):
        """The memory working-context fragments are empty when memory is off, so
        stored preferences/facts are not injected into the prompt."""
        agent = _build_agent(tmp_path)
        try:
            # Memory on: the stable fragment carries the memory instructions.
            assert agent.get_memory_system_prompt() != ""

            agent.set_memory_enabled(False)
            assert agent.get_memory_system_prompt() == ""
            assert agent.get_memory_dynamic_context() == ""
        finally:
            agent.close_db()

    def test_runtime_disable_flushes_composed_system_prompt(self, tmp_path):
        """A mid-session set_memory_enabled(False) must scrub stored
        preferences/facts from the CACHED, composed system prompt — not just the
        get_memory_system_prompt() helper. The email agent has no dynamic tool
        filter, so without an explicit rebuild the cached prompt would keep
        leaking memory to the model after a runtime toggle (PR #1966 review)."""
        marker = "PINEAPPLE_PREF_MARKER_42"
        agent = _build_agent(tmp_path)
        try:
            # Store a preference, then rebuild so the composed prompt includes it.
            agent._memory_store.store(
                category="preference", content=marker, context="email"
            )
            agent.rebuild_system_prompt()
            assert marker in agent.system_prompt, "sanity: pref injected while on"

            # Runtime disable must flush the cached prompt immediately.
            agent.set_memory_enabled(False)
            assert (
                marker not in agent.system_prompt
            ), "cached system prompt still leaks memory after runtime disable"

            # Re-enabling restores it.
            agent.set_memory_enabled(True)
            assert marker in agent.system_prompt
        finally:
            agent.close_db()

    def test_set_memory_enabled_returns_feedback(self, tmp_path):
        """The setter reports the applied state with actionable feedback."""
        agent = _build_agent(tmp_path)
        try:
            off = agent.set_memory_enabled(False)
            assert off["ok"] is True
            assert off["enabled"] is False
            assert off["available"] is True
            assert "disabled" in off["message"].lower()

            on = agent.set_memory_enabled(True)
            assert on["ok"] is True
            assert on["enabled"] is True
            assert "enabled" in on["message"].lower()
        finally:
            agent.close_db()

    def test_enable_when_unavailable_reports_failure(self, tmp_path):
        """Trying to ENABLE memory that was never initialized (GAIA_MEMORY_DISABLED)
        fails loudly with feedback — never a silent no-op."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            assert agent._memory_store is None
            result = agent.set_memory_enabled(True)
            # Cannot enable at runtime — reported, not silently ignored.
            assert result["ok"] is False
            assert result["enabled"] is False
            assert result["available"] is False
            assert "unavailable" in result["message"].lower()
            # Incognito unchanged; nothing falsely turned on.
            assert agent._incognito is True
        finally:
            agent.close_db()

    def test_disable_when_unavailable_is_satisfied(self, tmp_path):
        """Disabling already-unavailable memory is a satisfied request (ok=True):
        the caller asked for off, and off is what they get."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            result = agent.set_memory_enabled(False)
            assert result["ok"] is True
            assert result["enabled"] is False
            assert result["available"] is False
        finally:
            agent.close_db()

    def test_is_memory_enabled_and_status(self, tmp_path):
        """is_memory_enabled()/memory_status() reflect the live state."""
        agent = _build_agent(tmp_path)
        try:
            assert agent.is_memory_enabled() is True
            assert agent.memory_status()["available"] is True

            agent.set_memory_enabled(False)
            assert agent.is_memory_enabled() is False
            assert agent.memory_status()["enabled"] is False
        finally:
            agent.close_db()

        disabled = _build_agent(tmp_path, memory_disabled=True)
        try:
            assert disabled.is_memory_enabled() is False
            assert disabled.memory_status()["available"] is False
        finally:
            disabled.close_db()

    def test_memory_enabled_by_default(self, tmp_path):
        """Default construction leaves memory on — the toggle is opt-out."""
        agent = _build_agent(tmp_path)
        try:
            assert agent.config.memory_enabled is True
            assert agent.is_memory_enabled() is True
        finally:
            agent.close_db()
