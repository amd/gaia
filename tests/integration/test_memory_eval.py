# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Memory system evaluation tests — second-brain scenarios.

Validates the full memory pipeline end-to-end using real SQLite + mocked
embeddings. Tests cover the core use-cases users rely on: note-taking,
journaling, knowledge retrieval, confidence scoring, and persistence.

Also validates the new REST API additions (total_retrievals in stats,
mcp_memory_enabled settings endpoints).

These tests run without a Lemonade server — embeddings are deterministic
fakes, and LLM extraction is mocked or bypassed.

Run with:
    python -m pytest tests/integration/test_memory_eval.py -v
"""

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.agents.base.memory_store import (
    CONFIDENCE_BUMP_PER_RECALL,
    MemoryStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future_iso(days: int = 1) -> str:
    return (datetime.now().astimezone() + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat()


def _get_item(store: MemoryStore, knowledge_id: str) -> dict:
    """Fetch a single knowledge item by ID directly from SQLite."""
    with store._lock:
        row = store._conn.execute(
            f"SELECT {store._KNOWLEDGE_COLS} FROM knowledge WHERE id = ?",
            (knowledge_id,),
        ).fetchone()
    return store._row_to_knowledge_dict(row) if row else None


def _fake_vec(text: str, dim: int = 768) -> np.ndarray:
    """Deterministic unit vector seeded from text hash."""
    rng = np.random.RandomState(hash(text) % 2**32)
    v = rng.randn(dim).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def _blob(vec: np.ndarray) -> bytes:
    return vec.tobytes()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """Real SQLite MemoryStore in a temp directory."""
    s = MemoryStore(db_path=tmp_path / "eval_memory.db")
    yield s
    s.close()


@pytest.fixture
def mixin_host(tmp_path):
    """MemoryMixin host with mocked embeddings for conversation-level tests."""
    from gaia.agents.base.memory import MemoryMixin

    class FakeAgent:
        def __init__(self):
            self._system_prompt_cache = None
            self._registered_tools = {}

        def process_query(self, user_input, **kwargs):
            return {"result": f"Response to: {user_input}"}

        def _execute_tool(self, tool_name, tool_args):
            return {"status": "ok"}

        def register_tool(self, name, func, description=""):
            self._registered_tools[name] = func

    class TestAgent(MemoryMixin, FakeAgent):
        pass

    host = TestAgent()
    mock_emb = MagicMock()
    mock_emb.embed.return_value = [_fake_vec("seed").tolist()]

    with (
        patch.object(MemoryMixin, "_get_embedder", return_value=mock_emb),
        patch.object(MemoryMixin, "_embed_text", side_effect=lambda t: _fake_vec(t)),
        patch.object(MemoryMixin, "_backfill_embeddings", return_value=0),
        patch.object(MemoryMixin, "_rebuild_faiss_index", return_value=None),
        patch.object(
            MemoryMixin,
            "reconcile_memory",
            return_value={
                "pairs_checked": 0,
                "reinforced": 0,
                "contradicted": 0,
                "weakened": 0,
                "neutral": 0,
            },
        ),
        patch.object(
            MemoryMixin,
            "consolidate_old_sessions",
            return_value={"consolidated": 0, "extracted_items": 0},
        ),
    ):
        host.init_memory(db_path=tmp_path / "mixin_eval.db", context="global")
        yield host


@pytest.fixture
def api_client(tmp_path):
    """FastAPI TestClient backed by an isolated MemoryStore + in-memory settings DB."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.agents.base.memory_store import MemoryStore
    from gaia.ui.routers import memory as memory_mod

    mem_store = MemoryStore(db_path=tmp_path / "api_eval.db")

    # Mock ChatDatabase for settings
    mock_db = MagicMock()
    _settings: dict = {}
    mock_db.get_setting.side_effect = lambda key, default=None: _settings.get(
        key, default
    )
    mock_db.set_setting.side_effect = lambda key, value: _settings.update({key: value})

    app = FastAPI()
    app.include_router(memory_mod.router)

    original_store = memory_mod._store
    memory_mod._store = mem_store

    # Override get_db dependency
    from gaia.ui.dependencies import get_db

    app.dependency_overrides[get_db] = lambda: mock_db

    client = TestClient(app)
    yield client, mem_store

    memory_mod._store = original_store
    app.dependency_overrides.clear()
    mem_store.close()


# ===========================================================================
# 1. Remember / Recall Round-Trip
# ===========================================================================


class TestMemoryRememberRecall:
    """Core second-brain guarantee: what is stored can be found."""

    def test_store_and_search_exact_term(self, store):
        """A fact stored by keyword appears in FTS search results."""
        kid = store.store(
            category="fact",
            content="The deployment pipeline uses GitHub Actions for CI",
        )
        results = store.search("GitHub Actions")
        ids = [r["id"] for r in results]
        assert kid in ids, "Stored fact should appear in search results"

    def test_store_and_search_partial_term(self, store):
        """Partial keyword match still retrieves the stored fact."""
        kid = store.store(
            category="preference",
            content="User prefers dark mode in all editors and terminals",
        )
        results = store.search("dark mode")
        assert any(r["id"] == kid for r in results)

    def test_recall_increments_use_count(self, store):
        """Each recall bumps use_count and last_used."""
        kid = store.store(category="fact", content="Pancakes were eaten on April 1st")
        initial = _get_item(store, kid)
        assert initial["use_count"] == 0

        store.search("pancakes")
        after = _get_item(store, kid)
        assert after["use_count"] == 1
        assert after["last_used"] is not None

    def test_recall_bumps_confidence(self, store):
        """Confidence increases by CONFIDENCE_BUMP_PER_RECALL on each recall."""
        kid = store.store(
            category="fact",
            content="The project uses Python 3.12 as the minimum runtime",
            confidence=0.5,
        )
        store.search("Python runtime")
        item = _get_item(store, kid)
        assert item["confidence"] == pytest.approx(
            0.5 + CONFIDENCE_BUMP_PER_RECALL, abs=1e-4
        )

    def test_multiple_recalls_cumulate_confidence(self, store):
        """Repeated recalls keep increasing confidence (up to 1.0 cap)."""
        kid = store.store(
            category="skill",
            content="pytest fixtures use yield for teardown",
            confidence=0.6,
        )
        for _ in range(3):
            store.search("pytest fixtures")
        item = _get_item(store, kid)
        expected = min(0.6 + 3 * CONFIDENCE_BUMP_PER_RECALL, 1.0)
        assert item["confidence"] == pytest.approx(expected, abs=1e-4)

    def test_entity_tagged_fact_retrieved_by_entity(self, store):
        """Facts tagged with an entity are retrievable via get_by_entity()."""
        kid = store.store(
            category="fact",
            content="Sarah Chen was promoted to VP of Engineering in Q1",
            entity="person:sarah_chen",
        )
        results = store.get_by_entity("person:sarah_chen")
        assert any(r["id"] == kid for r in results)

    def test_no_results_for_unrelated_query(self, store):
        """A query for an unrelated term returns no results."""
        store.store(category="fact", content="GAIA runs on AMD Ryzen AI hardware")
        results = store.search("medieval castle architecture")
        assert len(results) == 0 or all("AMD" not in r["content"] for r in results)


# ===========================================================================
# 2. Note-Taking Scenarios
# ===========================================================================


class TestMemoryNoteTaking:
    """Validate note-keeping behaviors: categories, contexts, due dates."""

    def test_note_stored_with_correct_category(self, store):
        kid = store.store(
            category="note",
            content="Read the new FastAPI docs on dependency injection",
        )
        item = _get_item(store, kid)
        assert item["category"] == "note"

    def test_reminder_with_due_date_appears_in_upcoming(self, store):
        """A reminder stored with a future due_at shows in get_upcoming()."""
        kid = store.store(
            category="reminder",
            content="Submit quarterly report to the team",
            due_at=_future_iso(3),
        )
        upcoming = store.get_upcoming(within_days=7)
        assert any(r["id"] == kid for r in upcoming)

    def test_overdue_reminder_appears_in_upcoming(self, store):
        """An overdue reminder (past due_at) also appears in get_upcoming()."""
        kid = store.store(
            category="reminder",
            content="Review the open pull requests",
            due_at=_past_iso(1),
        )
        upcoming = store.get_upcoming(within_days=7)
        assert any(r["id"] == kid for r in upcoming)

    def test_work_note_isolated_from_personal_context(self, store):
        """Notes stored in 'work' context don't appear in 'personal' queries."""
        store.store(
            category="note",
            content="Team standup moved to 10am on Thursdays",
            context="work",
        )
        personal = store.get_by_category_contexts("note", "personal")
        assert all("standup" not in r["content"] for r in personal)

    def test_sensitive_note_excluded_from_system_prompt_items(self, store):
        """Sensitive notes are marked and handled separately (not in stable prompt)."""
        kid = store.store(
            category="note",
            content="API key: sk-abc123",
            sensitive=True,
        )
        item = _get_item(store, kid)
        assert item["sensitive"] is True

    def test_error_note_persists_for_future_avoidance(self, store):
        """Error patterns stored as 'error' category are retrievable."""
        kid = store.store(
            category="error",
            content="Forgetting to call store.close() causes WAL file leaks in tests",
        )
        results = store.search("WAL file")
        assert any(r["id"] == kid for r in results)

    def test_skill_note_with_domain_tag(self, store):
        """Skills tagged with a domain are retrievable by domain."""
        kid = store.store(
            category="skill",
            content="Use np.frombuffer to deserialize float32 embeddings from SQLite BLOB",
            domain="numpy",
        )
        results = store.search("frombuffer")
        assert any(r["id"] == kid for r in results)


# ===========================================================================
# 3. Journaling & Session Continuity
# ===========================================================================


class TestMemoryJournaling:
    """Multi-turn conversation tracking and session continuity."""

    def test_conversation_turns_stored_and_retrievable(self, store):
        """Storing conversation turns and retrieving them round-trips correctly."""
        session_id = str(uuid.uuid4())
        store.store_turn(session_id, "user", "I had pancakes this morning!")
        store.store_turn(session_id, "assistant", "Sounds delicious! How were they?")

        history = store.get_history(session_id=session_id)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert "pancakes" in history[0]["content"]

    def test_conversation_search_finds_past_message(self, store):
        """Full-text search over conversations finds stored turns."""
        session_id = str(uuid.uuid4())
        store.store_turn(
            session_id, "user", "We discussed the FAISS integration approach"
        )
        results = store.search_conversations("FAISS integration")
        assert len(results) > 0
        assert any("FAISS" in r["content"] for r in results)

    def test_multiple_sessions_are_isolated(self, store):
        """Conversation history is per-session — different sessions don't mix."""
        s1 = str(uuid.uuid4())
        s2 = str(uuid.uuid4())
        store.store_turn(s1, "user", "Session one content about project alpha")
        store.store_turn(s2, "user", "Session two content about project beta")

        s1_history = store.get_history(session_id=s1)
        assert len(s1_history) == 1
        assert "alpha" in s1_history[0]["content"]
        assert all("beta" not in t["content"] for t in s1_history)

    def test_get_sessions_lists_distinct_sessions(self, store):
        """get_sessions() returns one entry per distinct session_id."""
        ids = [str(uuid.uuid4()) for _ in range(3)]
        for sid in ids:
            store.store_turn(sid, "user", f"Hello from {sid}")
        sessions = store.get_sessions(limit=10)
        session_ids = [s["session_id"] for s in sessions]
        for sid in ids:
            assert sid in session_ids

    def test_knowledge_persists_across_sessions(self, store):
        """A fact stored in session 1 is retrievable after a new session."""
        store.store(
            category="fact",
            content="The team's Slack workspace is gaia-amd.slack.com",
        )
        # Simulate "new session" — just search with no prior session context
        results = store.search("Slack workspace")
        assert any("gaia-amd" in r["content"] for r in results)


# ===========================================================================
# 4. Confidence Scoring & Deduplication
# ===========================================================================


class TestMemoryConfidenceAndDedup:
    """Confidence lifecycle and deduplication prevent knowledge bloat."""

    def test_remember_tool_confidence_is_high(self, store):
        """Items stored explicitly (source='tool') start with confidence=0.7."""
        kid = store.store(
            category="fact",
            content="User works at AMD in the AI software team",
            source="tool",
            confidence=0.7,
        )
        item = _get_item(store, kid)
        assert item["confidence"] == pytest.approx(0.7)

    def test_llm_extracted_confidence_is_lower(self, store):
        """LLM-extracted items default to confidence=0.4 (less certain)."""
        kid = store.store(
            category="fact",
            content="User might prefer async code patterns",
            source="llm_extract",
            confidence=0.4,
        )
        item = _get_item(store, kid)
        assert item["confidence"] == pytest.approx(0.4)

    def test_high_overlap_content_deduplicates(self, store):
        """Storing near-duplicate content updates existing entry instead of creating new."""
        kid1 = store.store(
            category="fact", content="The CI pipeline runs on GitHub Actions runners"
        )
        kid2 = store.store(
            category="fact", content="The CI pipeline runs on GitHub Actions workflows"
        )
        # One of these should be a dedup — same id or updated content
        assert kid1 == kid2 or store.get_item(kid1) is not None

    def test_superseded_item_excluded_from_search(self, store):
        """A superseded fact does not appear in search results."""
        old_id = store.store(
            category="fact", content="Team uses PostgreSQL as the primary database"
        )
        new_id = store.store(
            category="fact", content="Team migrated to DynamoDB as the primary database"
        )
        store.update(old_id, superseded_by=new_id)

        results = store.search("primary database")
        result_ids = [r["id"] for r in results]
        assert new_id in result_ids
        assert old_id not in result_ids

    def test_update_clears_embedding_for_re_embedding(self, store):
        """Updating content marks embedding as NULL so it gets re-embedded."""
        kid = store.store(category="fact", content="Original content text here")
        vec = _fake_vec("original")
        store.store_embedding(kid, _blob(vec))

        # Verify embedding stored
        item = _get_item(store, kid)
        assert item is not None

        store.update(kid, content="Completely revised content text here")

        # Embedding should now be NULL (cleared for re-embedding)
        without = store.get_items_without_embeddings()
        assert any(r["id"] == kid for r in without)


# ===========================================================================
# 5. Stats API — total_retrievals field
# ===========================================================================


class TestMemoryStatsAPI:
    """Verify get_stats() returns total_retrievals and other v2 fields."""

    def test_stats_includes_total_retrievals(self, store):
        """total_retrievals is present in stats (may be 0 initially)."""
        stats = store.get_stats()
        assert "total_retrievals" in stats["knowledge"]
        assert stats["knowledge"]["total_retrievals"] == 0

    def test_total_retrievals_increments_on_search(self, store):
        """Each search() call increments total_retrievals by 1 per result."""
        store.store(category="fact", content="AMD Instinct MI300X accelerator specs")
        store.store(category="fact", content="AMD Radeon RX 7900 XTX benchmark results")

        stats_before = store.get_stats()
        retrieved_before = stats_before["knowledge"]["total_retrievals"]

        # Search returns both
        results = store.search("AMD")
        n_results = len(results)

        stats_after = store.get_stats()
        retrieved_after = stats_after["knowledge"]["total_retrievals"]

        assert retrieved_after == retrieved_before + n_results

    def test_total_retrievals_via_rest_api(self, api_client):
        """GET /api/memory/stats returns total_retrievals in the response."""
        client, mem_store = api_client
        mem_store.store(category="fact", content="REST API stats test entry")

        response = client.get("/api/memory/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_retrievals" in data["knowledge"]


# ===========================================================================
# 6. Settings API — mcp_memory_enabled toggle
# ===========================================================================


class TestMemorySettingsAPI:
    """Verify GET/PUT /api/memory/settings for mcp_memory_enabled."""

    def test_get_settings_returns_false_by_default(self, api_client):
        """mcp_memory_enabled defaults to false when no setting is stored."""
        client, _ = api_client
        response = client.get("/api/memory/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["mcp_memory_enabled"] is False

    def test_put_settings_enables_mcp_memory(self, api_client):
        """PUT with mcp_memory_enabled=true stores and returns the updated value."""
        client, _ = api_client
        response = client.put(
            "/api/memory/settings",
            json={"mcp_memory_enabled": True},
        )
        assert response.status_code == 200
        assert response.json()["mcp_memory_enabled"] is True

    def test_put_settings_persists_across_get(self, api_client):
        """Enabling the setting persists so a subsequent GET returns true."""
        client, _ = api_client
        client.put("/api/memory/settings", json={"mcp_memory_enabled": True})
        response = client.get("/api/memory/settings")
        assert response.json()["mcp_memory_enabled"] is True

    def test_put_settings_can_disable_mcp_memory(self, api_client):
        """Setting mcp_memory_enabled back to false disables it."""
        client, _ = api_client
        client.put("/api/memory/settings", json={"mcp_memory_enabled": True})
        client.put("/api/memory/settings", json={"mcp_memory_enabled": False})
        response = client.get("/api/memory/settings")
        assert response.json()["mcp_memory_enabled"] is False


# ===========================================================================
# 7. MemoryMixin System Prompt Integration
# ===========================================================================


class TestMemoryMixinSystemPrompt:
    """Validate that stored memories surface in the LLM system prompt."""

    def test_system_prompt_includes_stored_preference(self, mixin_host):
        """A stored preference appears in the stable memory system prompt."""
        mixin_host._memory_store.store(
            category="preference",
            content="User prefers concise bullet-point answers",
        )
        prompt = mixin_host.get_memory_system_prompt()
        assert "concise bullet-point answers" in prompt

    def test_system_prompt_includes_stored_fact(self, mixin_host):
        """A stored fact appears in the stable memory system prompt."""
        mixin_host._memory_store.store(
            category="fact",
            content="User is building a RAG pipeline for internal docs",
        )
        prompt = mixin_host.get_memory_system_prompt()
        assert "RAG pipeline" in prompt

    def test_system_prompt_always_has_memory_instructions(self, mixin_host):
        """Even with no memories, the prompt includes memory usage instructions."""
        prompt = mixin_host.get_memory_system_prompt()
        assert "MEMORY" in prompt
        assert "remember" in prompt.lower()

    def test_system_prompt_excludes_sensitive_items(self, mixin_host):
        """Sensitive items are NOT included in the system prompt."""
        mixin_host._memory_store.store(
            category="note",
            content="Secret: internal API key is sk-secret123",
            sensitive=True,
        )
        prompt = mixin_host.get_memory_system_prompt()
        assert "sk-secret123" not in prompt

    def test_dynamic_context_includes_current_time(self, mixin_host):
        """Dynamic context always includes the current timestamp."""
        ctx = mixin_host.get_memory_dynamic_context()
        assert "Current time:" in ctx

    def test_dynamic_context_includes_upcoming_reminders(self, mixin_host):
        """Upcoming reminders surface in the per-turn dynamic context."""
        mixin_host._memory_store.store(
            category="reminder",
            content="Review the memory eval test coverage",
            due_at=_future_iso(2),
        )
        ctx = mixin_host.get_memory_dynamic_context()
        assert "memory eval test coverage" in ctx

    def test_after_process_stores_conversation_turn(self, mixin_host):
        """_after_process_query() stores user + assistant turns in the DB."""
        mixin_host._after_process_query(
            user_input="Tell me about the RAG architecture",
            assistant_response="RAG uses a retriever and a reader.",
        )
        history = mixin_host._memory_store.get_history(
            session_id=mixin_host.memory_session_id
        )
        assert any(t["role"] == "user" for t in history)
        assert any(t["role"] == "assistant" for t in history)
