# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Inbox-profiling tests for EmailTriageAgent (#1289).

Acceptance criteria covered:
- AC (main): A ``profile_inbox`` capability summarizes sender/category patterns
  from remembered interaction history.
- Test-AC: Profile reflects seeded historical interactions in a fixture inbox.

Additional test coverage:
- record-on-triage: triaging one message writes one interaction record.
- memory-disabled: ``profile_inbox`` returns a clean empty/disabled result,
  no error.
- bounding/idempotency: recording the same sender N times keeps a single
  rolling record (no unbounded rows).

Embedder is mocked (same pattern as test_email_memory.py) so tests run
hermetically without Lemonade.
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

_INTERACTION_ENTITY_PREFIX = "email:interaction:"
_INTERACTION_DOMAIN = "email_agent_interactions"
_INTERACTION_CATEGORY = "interaction"


def _fake_embed(text: str) -> np.ndarray:
    """Deterministic unit vector — keeps FAISS happy."""
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _build_agent(tmp_path: Path, *, memory_disabled: bool = False) -> EmailTriageAgent:
    """Build EmailTriageAgent with injected fakes and tmp db paths.

    When *memory_disabled* is True sets GAIA_MEMORY_DISABLED=1 before
    construction and restores the env var after. Otherwise the Lemonade
    embedder is mocked so init_memory succeeds without a running server.
    """
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
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


def _invoke_profile_inbox(agent: EmailTriageAgent) -> dict:
    """Call the profile_inbox tool directly via the tool registry."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("profile_inbox")
    assert entry is not None, "profile_inbox tool not registered"
    result = entry["function"]()
    return json.loads(result)


def _seed_interaction(
    agent: EmailTriageAgent,
    sender: str,
    category: str,
    count: int = 1,
) -> None:
    """Directly call the private _record_interaction helper N times to seed history."""
    for _ in range(count):
        agent._record_interaction(sender, category)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProfileInboxToolRegistered:
    """AC: profile_inbox capability is registered after construction."""

    def test_profile_inbox_in_registry(self, tmp_path):
        """profile_inbox appears in the tool registry after construction."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        agent = _build_agent(tmp_path)
        try:
            assert (
                "profile_inbox" in _TOOL_REGISTRY
            ), f"profile_inbox not in registry. Keys: {sorted(_TOOL_REGISTRY)}"
        finally:
            agent.close_db()

    def test_profile_inbox_alongside_other_tools(self, tmp_path):
        """profile_inbox coexists with existing email tools."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        agent = _build_agent(tmp_path)
        try:
            assert "list_inbox" in _TOOL_REGISTRY
            assert "profile_inbox" in _TOOL_REGISTRY
        finally:
            agent.close_db()


class TestProfileReflectsSeededHistory:
    """Test-AC: profile reflects seeded historical interactions."""

    def test_seeded_sender_appears_in_profile(self, tmp_path):
        """After seeding 3 interactions for a sender, profile reports count=3."""
        agent = _build_agent(tmp_path)
        try:
            _seed_interaction(agent, "boss@company.com", "urgent", count=3)
            result = _invoke_profile_inbox(agent)
            assert result["ok"] is True, f"profile_inbox failed: {result}"

            data = result["data"]
            senders = {s["sender"]: s for s in data["top_senders"]}
            assert (
                "boss@company.com" in senders
            ), f"boss@company.com not in top_senders: {data['top_senders']}"
            assert (
                senders["boss@company.com"]["count"] == 3
            ), f"Expected count=3, got: {senders['boss@company.com']}"
        finally:
            agent.close_db()

    def test_dominant_category_correct(self, tmp_path):
        """Dominant category for a sender matches the most-seen category."""
        agent = _build_agent(tmp_path)
        try:
            _seed_interaction(agent, "newsletter@example.com", "low priority", count=5)
            _seed_interaction(agent, "newsletter@example.com", "informational", count=2)
            result = _invoke_profile_inbox(agent)
            assert result["ok"] is True

            senders = {s["sender"]: s for s in result["data"]["top_senders"]}
            assert "newsletter@example.com" in senders
            assert (
                senders["newsletter@example.com"]["dominant_category"] == "low priority"
            ), f"Expected dominant_category=low priority: {senders['newsletter@example.com']}"
        finally:
            agent.close_db()

    def test_multiple_senders_all_present(self, tmp_path):
        """All seeded senders appear in the profile."""
        agent = _build_agent(tmp_path)
        try:
            _seed_interaction(agent, "alice@company.com", "urgent", count=2)
            _seed_interaction(agent, "bob@company.com", "actionable", count=1)
            _seed_interaction(agent, "newsletter@news.com", "low priority", count=4)
            result = _invoke_profile_inbox(agent)
            assert result["ok"] is True

            present = {s["sender"] for s in result["data"]["top_senders"]}
            assert "alice@company.com" in present
            assert "bob@company.com" in present
            assert "newsletter@news.com" in present
        finally:
            agent.close_db()

    def test_empty_profile_on_no_history(self, tmp_path):
        """profile_inbox with no interactions returns ok with empty top_senders."""
        agent = _build_agent(tmp_path)
        try:
            result = _invoke_profile_inbox(agent)
            assert result["ok"] is True
            assert (
                result["data"]["top_senders"] == []
            ), f"Expected empty top_senders, got: {result['data']['top_senders']}"
        finally:
            agent.close_db()

    def test_profile_total_messages_correct(self, tmp_path):
        """total_messages in profile equals sum of all interaction counts."""
        agent = _build_agent(tmp_path)
        try:
            _seed_interaction(agent, "alice@company.com", "urgent", count=3)
            _seed_interaction(agent, "bob@company.com", "informational", count=2)
            result = _invoke_profile_inbox(agent)
            assert result["ok"] is True
            assert (
                result["data"]["total_messages"] == 5
            ), f"Expected total_messages=5: {result['data']}"
        finally:
            agent.close_db()


class TestRecordOnTriage:
    """record-on-triage: calling triage writes interaction records."""

    def test_record_interaction_creates_memory_record(self, tmp_path):
        """_record_interaction for a sender creates exactly one memory record."""
        agent = _build_agent(tmp_path)
        try:
            agent._record_interaction("boss@company.com", "urgent")
            store = agent._memory_store
            assert store is not None

            entity = f"{_INTERACTION_ENTITY_PREFIX}boss@company.com"
            rows = store.get_by_entity(entity)
            assert (
                len(rows) == 1
            ), f"Expected 1 interaction record, got {len(rows)}: {rows}"
            payload = json.loads(rows[0]["content"])
            assert payload["sender"] == "boss@company.com"
            assert payload["count"] == 1
            assert payload["category_counts"]["urgent"] == 1
        finally:
            agent.close_db()

    def test_record_interaction_increments_count(self, tmp_path):
        """Recording the same sender twice increments count to 2 in the same record."""
        agent = _build_agent(tmp_path)
        try:
            agent._record_interaction("alice@company.com", "actionable")
            agent._record_interaction("alice@company.com", "actionable")

            entity = f"{_INTERACTION_ENTITY_PREFIX}alice@company.com"
            rows = agent._memory_store.get_by_entity(entity)
            assert len(rows) == 1, f"Expected 1 record, got {len(rows)}"
            payload = json.loads(rows[0]["content"])
            assert payload["count"] == 2
            assert payload["category_counts"]["actionable"] == 2
        finally:
            agent.close_db()

    def test_record_interaction_tracks_multiple_categories(self, tmp_path):
        """Different categories for the same sender are tracked separately."""
        agent = _build_agent(tmp_path)
        try:
            agent._record_interaction("mixed@company.com", "urgent")
            agent._record_interaction("mixed@company.com", "informational")
            agent._record_interaction("mixed@company.com", "urgent")

            entity = f"{_INTERACTION_ENTITY_PREFIX}mixed@company.com"
            rows = agent._memory_store.get_by_entity(entity)
            assert len(rows) == 1
            payload = json.loads(rows[0]["content"])
            assert payload["count"] == 3
            assert payload["category_counts"]["urgent"] == 2
            assert payload["category_counts"]["informational"] == 1
        finally:
            agent.close_db()


class TestMemoryDisabled:
    """memory-disabled: profile_inbox returns clean result, no error."""

    def test_profile_inbox_memory_disabled_ok(self, tmp_path):
        """When GAIA_MEMORY_DISABLED=1, profile_inbox returns ok with empty profile."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            assert agent._memory_store is None
            result = _invoke_profile_inbox(agent)
            assert (
                result["ok"] is True
            ), f"Expected ok=True when memory disabled, got: {result}"
            data = result["data"]
            assert data["top_senders"] == []
            assert data["total_messages"] == 0
        finally:
            agent.close_db()

    def test_record_interaction_memory_disabled_no_error(self, tmp_path):
        """_record_interaction silently skips when memory is disabled (no exception)."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            assert agent._memory_store is None
            # Must not raise
            agent._record_interaction("someone@example.com", "urgent")
        finally:
            agent.close_db()


class TestBoundingAndIdempotency:
    """Bounding/idempotency: one rolling record per sender, no unbounded accumulation."""

    def test_n_recordings_keep_single_record(self, tmp_path):
        """Recording the same sender 10 times produces exactly 1 memory record."""
        agent = _build_agent(tmp_path)
        try:
            for _ in range(10):
                agent._record_interaction("repeat@company.com", "informational")

            entity = f"{_INTERACTION_ENTITY_PREFIX}repeat@company.com"
            rows = agent._memory_store.get_by_entity(entity)
            assert (
                len(rows) == 1
            ), f"Expected exactly 1 record after 10 recordings, got {len(rows)}: {rows}"
            payload = json.loads(rows[0]["content"])
            assert (
                payload["count"] == 10
            ), f"count should be 10, got: {payload['count']}"
        finally:
            agent.close_db()

    def test_different_senders_each_get_own_record(self, tmp_path):
        """Three different senders produce three distinct records."""
        agent = _build_agent(tmp_path)
        try:
            agent._record_interaction("a@company.com", "urgent")
            agent._record_interaction("b@company.com", "actionable")
            agent._record_interaction("c@company.com", "informational")

            for sender in ("a@company.com", "b@company.com", "c@company.com"):
                entity = f"{_INTERACTION_ENTITY_PREFIX}{sender}"
                rows = agent._memory_store.get_by_entity(entity)
                assert (
                    len(rows) == 1
                ), f"Expected 1 record for {sender}, got {len(rows)}"
        finally:
            agent.close_db()

    def test_interactions_persist_across_restart(self, tmp_path):
        """Interaction records survive an agent restart (same memory.db path)."""
        agent_a = _build_agent(tmp_path)
        try:
            agent_a._record_interaction("persist@company.com", "urgent")
            agent_a._record_interaction("persist@company.com", "urgent")
        finally:
            agent_a.close_db()

        # Re-open the same memory DB directly and verify the record survived.
        from gaia.agents.base.memory_store import MemoryStore

        store = MemoryStore(tmp_path / "memory.db")
        entity = f"{_INTERACTION_ENTITY_PREFIX}persist@company.com"
        rows = store.get_by_entity(entity)
        assert len(rows) == 1, f"Expected 1 record after restart, got {len(rows)}"
        payload = json.loads(rows[0]["content"])
        assert payload["count"] == 2

    def test_read_interactions_returns_all_senders(self, tmp_path):
        """_read_interactions() returns all seeded sender records."""
        agent = _build_agent(tmp_path)
        try:
            agent._record_interaction("x@company.com", "urgent")
            agent._record_interaction("y@company.com", "low priority")
            agent._record_interaction("z@company.com", "informational")

            records = agent._read_interactions()
            senders = {r["sender"] for r in records}
            assert senders == {
                "x@company.com",
                "y@company.com",
                "z@company.com",
            }, f"Expected 3 senders, got: {senders}"
        finally:
            agent.close_db()
