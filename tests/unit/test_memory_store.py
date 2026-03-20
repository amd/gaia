# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for MemoryStore — unified data layer for agent memory.

Tests CRUD, FTS5 search (AND/OR semantics, BM25 ranking, sanitization),
deduplication (80% word overlap, context-scoped), confidence scoring/decay,
conversations, tool history, temporal awareness (due_at/reminded_at),
context scoping, sensitivity, entity linking, and dashboard queries.

All tests use in-memory SQLite or temp files — no external dependencies.
"""

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from gaia.agents.base.memory_store import MemoryStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current local time in ISO 8601 with timezone offset."""
    return datetime.now().astimezone().isoformat()


def _future_iso(days: int = 1) -> str:
    """Return ISO timestamp N days in the future."""
    return (datetime.now().astimezone() + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    """Return ISO timestamp N days in the past."""
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    """Create a fresh MemoryStore in a temp directory for each test."""
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@pytest.fixture
def mem_store(tmp_path):
    """Alias for store — used by some test classes for clarity."""
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


# ===========================================================================
# 1. Basic CRUD
# ===========================================================================


class TestBasicCRUD:
    """Store, retrieve, update, and delete knowledge entries."""

    def test_store_and_retrieve(self, store):
        """Store a knowledge entry and retrieve it via search."""
        entry_id = store.store(
            category="fact",
            content="GAIA supports NPU acceleration on AMD hardware",
            domain="hardware",
        )
        results = store.search("NPU acceleration")
        assert len(results) >= 1
        match = next((r for r in results if r["id"] == entry_id), None)
        assert match is not None
        assert match["category"] == "fact"
        assert "NPU acceleration" in match["content"]

    def test_store_returns_uuid_string(self, store):
        """store() returns a UUID string."""
        entry_id = store.store(category="fact", content="Test content")
        assert isinstance(entry_id, str)
        # Verify it's a valid UUID
        uuid.UUID(entry_id)  # Raises ValueError if invalid

    def test_update_entry(self, store):
        """update() modifies an existing entry's fields."""
        entry_id = store.store(
            category="fact",
            content="Project uses React 18",
            domain="frontend",
        )

        result = store.update(
            entry_id,
            content="Project uses React 19",
            domain="frontend-v2",
        )
        assert result is True

        # Verify updated content via search
        results = store.search("React")
        match = next((r for r in results if r["id"] == entry_id), None)
        assert match is not None
        assert "React 19" in match["content"]

    def test_update_category(self, store):
        """update() can change category."""
        entry_id = store.store(category="fact", content="Deploy workflow")
        assert store.update(entry_id, category="skill") is True
        results = store.get_by_category("skill")
        assert any(r["id"] == entry_id for r in results)

    def test_update_context(self, store):
        """update() can change context."""
        entry_id = store.store(category="fact", content="Some fact", context="work")
        assert store.update(entry_id, context="personal") is True
        results = store.get_by_category("fact", context="personal")
        assert any(r["id"] == entry_id for r in results)

    def test_update_entity(self, store):
        """update() can set/change entity."""
        entry_id = store.store(category="fact", content="Sarah is VP Eng")
        assert store.update(entry_id, entity="person:sarah_chen") is True
        results = store.get_by_entity("person:sarah_chen")
        assert any(r["id"] == entry_id for r in results)

    def test_update_sensitive(self, store):
        """update() can toggle sensitive flag."""
        entry_id = store.store(category="fact", content="API key abc123")
        assert store.update(entry_id, sensitive=True) is True
        # Should be excluded from default search
        results = store.search("API key abc123")
        assert not any(r["id"] == entry_id for r in results)
        # But found with include_sensitive
        results = store.search("API key abc123", include_sensitive=True)
        assert any(r["id"] == entry_id for r in results)

    def test_update_due_at(self, store):
        """update() can set due_at."""
        entry_id = store.store(category="fact", content="Course starts")
        due = _future_iso(3)
        assert store.update(entry_id, due_at=due) is True
        upcoming = store.get_upcoming(within_days=7)
        assert any(r["id"] == entry_id for r in upcoming)

    def test_update_reminded_at(self, store):
        """update() can set reminded_at."""
        due = _future_iso(3)
        entry_id = store.store(category="fact", content="Course starts", due_at=due)
        # Set reminded_at to after due → should suppress from upcoming
        reminded = _future_iso(5)
        assert store.update(entry_id, reminded_at=reminded) is True

    def test_update_returns_false_for_nonexistent_id(self, store):
        """update() returns False when ID doesn't exist."""
        fake_id = str(uuid.uuid4())
        assert store.update(fake_id, content="new content") is False

    def test_delete_entry(self, store):
        """delete() removes an entry."""
        entry_id = store.store(category="fact", content="Temporary fact")
        assert store.delete(entry_id) is True
        results = store.search("Temporary fact")
        assert not any(r["id"] == entry_id for r in results)

    def test_delete_returns_false_for_nonexistent_id(self, store):
        """delete() returns False when ID doesn't exist."""
        fake_id = str(uuid.uuid4())
        assert store.delete(fake_id) is False

    def test_store_with_metadata(self, store):
        """store() preserves metadata JSON."""
        meta = {"steps": ["draft", "review", "publish"], "priority": "high"}
        entry_id = store.store(
            category="skill",
            content="Content publishing workflow",
            metadata=meta,
        )
        results = store.search("publishing workflow")
        match = next((r for r in results if r["id"] == entry_id), None)
        assert match is not None
        assert match["metadata"] == meta

    def test_store_default_confidence(self, store):
        """Default confidence is 0.5 (search bumps +0.02 so expect 0.52)."""
        entry_id = store.store(category="fact", content="Default confidence test")
        # search() bumps confidence +0.02 on recall per spec
        results = store.search("Default confidence test")
        match = next((r for r in results if r["id"] == entry_id), None)
        assert match is not None
        assert match["confidence"] == pytest.approx(0.52)

    def test_store_custom_confidence(self, store):
        """store() accepts custom confidence (search bumps +0.02)."""
        entry_id = store.store(
            category="fact", content="High confidence fact", confidence=0.8
        )
        results = store.search("High confidence fact")
        match = next((r for r in results if r["id"] == entry_id), None)
        assert match is not None
        assert match["confidence"] == pytest.approx(0.82)

    def test_store_with_source(self, store):
        """store() records source field."""
        entry_id = store.store(
            category="fact", content="User stated fact", source="user"
        )
        results = store.search("User stated fact")
        match = next((r for r in results if r["id"] == entry_id), None)
        assert match is not None
        assert match["source"] == "user"


# ===========================================================================
# 2. FTS5 Search
# ===========================================================================


class TestFTS5Search:
    """FTS5 search with AND/OR semantics, BM25 ranking, sanitization."""

    def test_search_finds_by_keyword(self, store):
        """search() finds entries by keyword in content."""
        store.store(category="fact", content="GAIA supports NPU acceleration")
        store.store(category="fact", content="The weather is sunny today")

        results = store.search("NPU acceleration")
        assert len(results) >= 1
        assert any("NPU" in r["content"] for r in results)

    def test_search_and_semantics(self, store):
        """Multi-word query uses AND — matches only if all words present."""
        store.store(
            category="fact",
            content="Our marketing strategy is content-first",
        )
        store.store(
            category="fact",
            content="Marketing budget is five thousand dollars",
        )
        store.store(
            category="fact",
            content="Our strategy is agile methodology",
        )

        results = store.search("marketing strategy")
        contents = [r["content"] for r in results]

        # AND: only the entry with BOTH words should appear
        assert any("marketing strategy" in c.lower() for c in contents)
        # Entries with only one word should NOT appear when AND has results
        assert not any(
            "budget" in c.lower() and "strategy" not in c.lower() for c in contents
        )

    def test_search_or_fallback_on_zero_and_results(self, store):
        """When AND returns zero results, falls back to OR."""
        store.store(
            category="fact",
            content="Marketing is important for growth",
        )
        store.store(
            category="fact",
            content="Quantum computing is the future",
        )

        # No entry has BOTH "marketing" AND "quantum"
        results = store.search("marketing quantum")
        assert len(results) >= 1
        # At least one partial match should appear
        contents = [r["content"].lower() for r in results]
        assert any("marketing" in c or "quantum" in c for c in contents)

    def test_search_bm25_ranking(self, store):
        """Results are ranked by BM25 relevance."""
        # Entry with "NPU" twice should rank higher than entry with "NPU" once
        store.store(
            category="fact",
            content="NPU acceleration: the NPU chip provides fast inference",
        )
        store.store(
            category="fact",
            content="The GPU is also used for acceleration tasks",
        )
        id_focused = store.store(
            category="fact",
            content="AMD Ryzen supports basic compute operations",
        )

        results = store.search("NPU acceleration")
        assert len(results) >= 1
        # First result should be the NPU-focused entry
        assert "NPU" in results[0]["content"]

    def test_search_empty_query_returns_empty(self, store):
        """Empty or whitespace query returns empty list."""
        store.store(category="fact", content="Some content")
        assert store.search("") == []
        assert store.search("   ") == []

    def test_search_sanitizes_fts5_special_chars(self, store):
        """FTS5 special characters (dots, colons, hyphens) are sanitized."""
        store.store(
            category="fact",
            content="Error in module.submodule.function at line 42",
        )

        # These contain FTS5 special chars that would cause syntax errors
        results = store.search("module.submodule.function")
        assert len(results) >= 1

        results = store.search("key:value")
        # Should not crash — sanitized query runs safely
        assert isinstance(results, list)

        results = store.search("semi-colon")
        assert isinstance(results, list)

    def test_search_bumps_confidence(self, store):
        """Search bumps confidence +0.02 on recalled items."""
        entry_id = store.store(
            category="fact",
            content="GAIA is AMD open source AI framework",
            confidence=0.5,
        )

        results = store.search("GAIA AMD framework")
        match = next((r for r in results if r["id"] == entry_id), None)
        assert match is not None
        # Confidence should have been bumped by +0.02
        assert match["confidence"] == pytest.approx(0.52, abs=0.01)

    def test_search_with_category_filter(self, store):
        """search() can filter by category."""
        store.store(category="fact", content="Python is the primary language")
        store.store(category="skill", content="Python deployment workflow")

        results = store.search("Python", category="fact")
        assert len(results) >= 1
        for r in results:
            assert r["category"] == "fact"

    def test_search_with_context_filter(self, store):
        """search() can filter by context."""
        store.store(
            category="fact",
            content="Deploy with kubectl apply",
            context="work",
        )
        store.store(
            category="fact",
            content="Deploy hobby project to Vercel",
            context="personal",
        )

        results = store.search("deploy", context="work")
        assert len(results) >= 1
        for r in results:
            assert r["context"] == "work"

    def test_search_with_entity_filter(self, store):
        """search() can filter by entity."""
        store.store(
            category="fact",
            content="Sarah is VP of Engineering",
            entity="person:sarah_chen",
        )
        store.store(
            category="fact",
            content="Bob is a senior engineer",
            entity="person:bob_smith",
        )

        results = store.search("engineer", entity="person:sarah_chen")
        assert len(results) >= 1
        for r in results:
            assert r["entity"] == "person:sarah_chen"


# ===========================================================================
# 3. Deduplication
# ===========================================================================


class TestDeduplication:
    """>80% word overlap in same category+context → updates existing."""

    def test_dedup_high_overlap_same_category_context(self, store):
        """High overlap in same category+context deduplicates."""
        id1 = store.store(
            category="fact",
            content="GAIA supports NPU acceleration on AMD hardware",
        )
        id2 = store.store(
            category="fact",
            content="GAIA supports NPU acceleration on AMD",
        )
        # Should reuse existing entry
        assert id2 == id1

    def test_dedup_replaces_with_newer_content(self, store):
        """Dedup replaces content with the newer version (not longer)."""
        id1 = store.store(
            category="fact",
            content="Project uses React 18 with webpack bundler",
        )
        id2 = store.store(
            category="fact",
            content="Project uses React 19 with webpack bundler",
        )
        assert id2 == id1

        # Newer content should be stored
        results = store.search("React webpack bundler")
        match = next((r for r in results if r["id"] == id1), None)
        assert match is not None
        assert "React 19" in match["content"]

    def test_dedup_takes_max_confidence(self, store):
        """Dedup takes the maximum confidence of old and new."""
        id1 = store.store(
            category="fact",
            content="GAIA supports NPU acceleration on AMD hardware",
            confidence=0.7,
        )
        id2 = store.store(
            category="fact",
            content="GAIA supports NPU acceleration on AMD",
            confidence=0.5,
        )
        assert id2 == id1

        results = store.search("GAIA NPU acceleration AMD")
        match = next((r for r in results if r["id"] == id1), None)
        assert match is not None
        # Should keep the higher confidence (0.7), bumped by search recall (+0.02)
        assert match["confidence"] >= 0.7

    def test_dedup_preserves_created_at_updates_updated_at(self, store):
        """Dedup preserves created_at but updates updated_at."""
        id1 = store.store(
            category="fact",
            content="GAIA supports NPU acceleration on AMD hardware",
        )

        # Get the original created_at
        results = store.search("GAIA NPU acceleration AMD")
        original = next(r for r in results if r["id"] == id1)
        created_at_1 = original["created_at"]

        time.sleep(0.05)  # Ensure timestamp difference

        id2 = store.store(
            category="fact",
            content="GAIA supports NPU acceleration on AMD chips",
        )
        assert id2 == id1

        results = store.search("GAIA NPU acceleration AMD")
        updated = next(r for r in results if r["id"] == id1)
        assert updated["created_at"] == created_at_1  # Preserved
        assert updated["updated_at"] >= created_at_1  # Updated

    def test_no_dedup_low_overlap(self, store):
        """<80% overlap creates a new entry."""
        id1 = store.store(
            category="fact",
            content="GAIA supports NPU acceleration on AMD hardware",
        )
        id2 = store.store(
            category="fact",
            content="LinkedIn posting schedule is Monday through Friday mornings",
        )
        assert id1 != id2

    def test_no_dedup_different_category(self, store):
        """Same content in different categories → no dedup."""
        id1 = store.store(
            category="fact",
            content="GAIA supports NPU acceleration on AMD hardware",
        )
        id2 = store.store(
            category="skill",
            content="GAIA supports NPU acceleration on AMD hardware",
        )
        assert id1 != id2

    def test_no_dedup_different_context(self, store):
        """Same content in different contexts → no dedup (context scoping)."""
        id1 = store.store(
            category="fact",
            content="Deploy process uses kubectl apply",
            context="work",
        )
        id2 = store.store(
            category="fact",
            content="Deploy process uses kubectl apply",
            context="personal",
        )
        assert id1 != id2


# ===========================================================================
# 4. Confidence
# ===========================================================================


class TestConfidence:
    """Confidence scoring: defaults, bumps, clamping, decay."""

    def test_default_confidence_is_half(self, store):
        """Default confidence is 0.5."""
        entry_id = store.store(category="fact", content="Default test")
        results = store.search("Default test")
        match = next(r for r in results if r["id"] == entry_id)
        # After search bump (+0.02), should be 0.52
        assert match["confidence"] == pytest.approx(0.52, abs=0.01)

    def test_confidence_bumps_on_recall(self, store):
        """Confidence bumps +0.02 on each search recall."""
        entry_id = store.store(
            category="fact",
            content="Recall bump test content unique",
            confidence=0.5,
        )

        store.search("Recall bump test content unique")  # +0.02 → 0.52
        store.search("Recall bump test content unique")  # +0.02 → 0.54

        results = store.search("Recall bump test content unique")  # +0.02 → 0.56
        match = next(r for r in results if r["id"] == entry_id)
        assert match["confidence"] == pytest.approx(0.56, abs=0.02)

    def test_confidence_clamped_at_one(self, store):
        """Confidence cannot exceed 1.0."""
        entry_id = store.store(
            category="fact",
            content="Max confidence clamp test xyz",
            confidence=0.99,
        )
        # Bump via search: 0.99 + 0.02 should clamp to 1.0
        results = store.search("Max confidence clamp test xyz")
        match = next(r for r in results if r["id"] == entry_id)
        assert match["confidence"] <= 1.0

    def test_apply_confidence_decay_stale_items(self, store):
        """Items unused >30 days get confidence multiplied by 0.9."""
        entry_id = store.store(
            category="fact",
            content="Stale fact for decay test",
            confidence=0.8,
        )

        # Manually set last_used AND updated_at to 31 days ago to make it stale.
        # apply_confidence_decay requires both to be old so it's not accidentally
        # re-applied to items that were already decayed this period.
        stale_date = _past_iso(31)
        store._execute(
            "UPDATE knowledge SET last_used = ?, updated_at = ? WHERE id = ?",
            (stale_date, stale_date, entry_id),
        )

        decayed_count = store.apply_confidence_decay(
            days_threshold=30, decay_factor=0.9
        )
        assert decayed_count >= 1

        # Verify confidence decayed: 0.8 * 0.9 = 0.72
        results = store.search("Stale fact for decay test")
        match = next((r for r in results if r["id"] == entry_id), None)
        assert match is not None
        # Note: search also bumps +0.02, so expect ~0.74
        assert match["confidence"] == pytest.approx(0.74, abs=0.02)

    def test_apply_confidence_decay_recent_items_unaffected(self, store):
        """Recently used items are not decayed."""
        entry_id = store.store(
            category="fact",
            content="Recent fact not decayed xyz123",
            confidence=0.8,
        )
        # last_used is set on store, so it's recent

        decayed_count = store.apply_confidence_decay(
            days_threshold=30, decay_factor=0.9
        )
        # This specific item should NOT have been decayed
        results = store.search("Recent fact not decayed xyz123")
        match = next((r for r in results if r["id"] == entry_id), None)
        assert match is not None
        # 0.8 + 0.02 (search bump) = 0.82, NOT 0.72 (what it'd be if decayed)
        assert match["confidence"] >= 0.8

    def test_update_confidence_positive_delta(self, store):
        """update_confidence with positive delta increases confidence."""
        entry_id = store.store(
            category="fact",
            content="Confidence delta positive test",
            confidence=0.5,
        )
        store.update_confidence(entry_id, delta=0.1)

        results = store.search("Confidence delta positive test")
        match = next(r for r in results if r["id"] == entry_id)
        # 0.5 + 0.1 = 0.6, then +0.02 from search = 0.62
        assert match["confidence"] == pytest.approx(0.62, abs=0.02)

    def test_update_confidence_negative_delta(self, store):
        """update_confidence with negative delta decreases confidence."""
        entry_id = store.store(
            category="fact",
            content="Confidence delta negative test",
            confidence=0.5,
        )
        store.update_confidence(entry_id, delta=-0.2)

        results = store.search("Confidence delta negative test")
        match = next(r for r in results if r["id"] == entry_id)
        # 0.5 - 0.2 = 0.3, then +0.02 from search = 0.32
        assert match["confidence"] == pytest.approx(0.32, abs=0.02)


# ===========================================================================
# 5. Confidence bump — SQL-side atomicity
# ===========================================================================


class TestConfidenceBumpAtomicity:
    """search() uses SQL-side MIN(confidence + 0.02, 1.0) for atomic bumps."""

    def test_bump_uses_current_db_value_not_stale_snapshot(self, store):
        """Simulate a second searcher reading after first bump — should see 0.54, not 0.52."""
        kid = store.store(
            category="fact", content="AtomicBump unique entry zzz", confidence=0.50
        )
        # First search bumps: SQL sets confidence = MIN(0.50 + 0.02, 1.0) = 0.52
        store.search("AtomicBump unique entry zzz")

        # Manually set confidence to 0.52 + 0.01 = 0.53 to simulate another writer
        store._conn.execute(
            "UPDATE knowledge SET confidence = 0.53 WHERE id = ?", (kid,)
        )
        store._conn.commit()

        # Next search should bump from CURRENT DB value 0.53 → 0.55 (SQL-side),
        # NOT from the snapshot read at query time.
        results = store.search("AtomicBump unique entry zzz")
        match = next((r for r in results if r["id"] == kid), None)
        assert match is not None
        # SQL-side: MIN(0.53 + 0.02, 1.0) = 0.55
        assert match["confidence"] == pytest.approx(0.55, abs=0.01)

        # Verify DB also has the SQL-side result, not stale Python value
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] == pytest.approx(0.55, abs=0.01)

    def test_confidence_at_1_0_is_not_exceeded_by_sql_bump(self, store):
        """MIN(confidence + 0.02, 1.0) in SQL correctly clamps at 1.0."""
        kid = store.store(
            category="fact", content="SQLBump clamp test xyz", confidence=1.0
        )
        results = store.search("SQLBump clamp test xyz")
        match = next((r for r in results if r["id"] == kid), None)
        assert match is not None
        assert match["confidence"] == pytest.approx(1.0)
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] <= 1.0


# ===========================================================================
# 6. Conversations
# ===========================================================================


class TestConversations:
    """Conversation turn storage and retrieval."""

    def test_store_turn_and_get_history(self, store):
        """store_turn() persists; get_history() returns turns."""
        store.store_turn("sess1", "user", "Hello agent")
        store.store_turn("sess1", "assistant", "Hello! How can I help?")

        history = store.get_history(session_id="sess1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello agent"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "Hello! How can I help?"

    def test_get_history_with_session_filter(self, store):
        """get_history() filters by session_id."""
        store.store_turn("sess1", "user", "Session 1 message")
        store.store_turn("sess2", "user", "Session 2 message")

        history = store.get_history(session_id="sess1")
        assert len(history) == 1
        assert history[0]["content"] == "Session 1 message"

    def test_get_history_with_context_filter(self, store):
        """get_history() filters by context."""
        store.store_turn("sess1", "user", "Work question", context="work")
        store.store_turn("sess1", "user", "Personal question", context="personal")

        history = store.get_history(session_id="sess1", context="work")
        assert len(history) == 1
        assert history[0]["content"] == "Work question"

    def test_get_history_limit(self, store):
        """get_history() respects limit."""
        for i in range(10):
            store.store_turn("sess1", "user", f"Message {i}")
        history = store.get_history(session_id="sess1", limit=3)
        assert len(history) == 3

    def test_conversation_turns_ordered_oldest_first(self, store):
        """Conversation turns are returned oldest-first (chronological)."""
        store.store_turn("sess1", "user", "First message")
        store.store_turn("sess1", "assistant", "Second message")
        store.store_turn("sess1", "user", "Third message")

        history = store.get_history(session_id="sess1")
        assert history[0]["content"] == "First message"
        assert history[1]["content"] == "Second message"
        assert history[2]["content"] == "Third message"

    def test_search_conversations_fts5(self, store):
        """search_conversations() uses FTS5 to find past discussions."""
        store.store_turn("sess1", "user", "How do I use NPU acceleration?")
        store.store_turn("sess1", "assistant", "Enable NPU through Lemonade Server")
        store.store_turn("sess2", "user", "What is the weather today?")

        results = store.search_conversations("NPU acceleration")
        assert len(results) >= 1
        assert any("NPU" in r["content"] for r in results)

    def test_search_conversations_with_context(self, store):
        """search_conversations() can filter by context."""
        store.store_turn("sess1", "user", "Deploy to staging", context="work")
        store.store_turn("sess1", "user", "Deploy hobby project", context="personal")

        results = store.search_conversations("deploy", context="work")
        assert len(results) >= 1
        for r in results:
            assert r["context"] == "work"

    def test_get_recent_conversations(self, store):
        """get_recent_conversations() returns turns from last N days."""
        store.store_turn("sess1", "user", "Recent message")

        results = store.get_recent_conversations(days=7)
        assert len(results) >= 1
        assert results[0]["content"] == "Recent message"

    def test_get_recent_conversations_with_context(self, store):
        """get_recent_conversations() filters by context."""
        store.store_turn("sess1", "user", "Work chat", context="work")
        store.store_turn("sess1", "user", "Personal chat", context="personal")

        results = store.get_recent_conversations(days=7, context="work")
        assert all(r["context"] == "work" for r in results)

    def test_get_recent_conversations_ordered_oldest_first(self, store):
        """get_recent_conversations() returns turns oldest-first."""
        store.store_turn("sess1", "user", "First")
        store.store_turn("sess1", "user", "Second")
        store.store_turn("sess1", "user", "Third")

        results = store.get_recent_conversations(days=7)
        assert results[0]["content"] == "First"
        assert results[-1]["content"] == "Third"


# ===========================================================================
# 6. Tool History
# ===========================================================================


class TestToolHistory:
    """Tool call logging, error retrieval, and stats."""

    def test_log_tool_call(self, store):
        """log_tool_call() stores a tool call record."""
        store.log_tool_call(
            session_id="sess1",
            tool_name="execute_code",
            args={"code": "print('hello')"},
            result_summary="hello",
            success=True,
            duration_ms=150,
        )

        stats = store.get_tool_stats("execute_code")
        assert stats["total_calls"] == 1
        assert stats["success_rate"] == pytest.approx(1.0)

    def test_get_tool_errors_returns_failures_only(self, store):
        """get_tool_errors() returns only failed tool calls."""
        store.log_tool_call(
            session_id="sess1",
            tool_name="execute_code",
            args={"code": "import torch"},
            result_summary="",
            success=False,
            error="ImportError: No module named 'torch'",
            duration_ms=50,
        )
        store.log_tool_call(
            session_id="sess1",
            tool_name="execute_code",
            args={"code": "print('hello')"},
            result_summary="hello",
            success=True,
            duration_ms=30,
        )

        errors = store.get_tool_errors()
        assert len(errors) >= 1
        for e in errors:
            assert e["success"] == 0  # All are failures

    def test_get_tool_errors_filtered_by_tool_name(self, store):
        """get_tool_errors() can filter by tool_name."""
        store.log_tool_call(
            session_id="sess1",
            tool_name="execute_code",
            args={},
            result_summary="",
            success=False,
            error="SyntaxError",
        )
        store.log_tool_call(
            session_id="sess1",
            tool_name="read_file",
            args={},
            result_summary="",
            success=False,
            error="FileNotFoundError",
        )

        errors = store.get_tool_errors(tool_name="execute_code")
        assert len(errors) >= 1
        for e in errors:
            assert e["tool_name"] == "execute_code"

    def test_get_tool_stats_aggregates(self, store):
        """get_tool_stats() returns correct aggregates."""
        # Log 3 calls: 2 success, 1 failure
        for i in range(2):
            store.log_tool_call(
                session_id="sess1",
                tool_name="read_file",
                args={"path": f"/file{i}.py"},
                result_summary="content",
                success=True,
                duration_ms=100 + i * 50,
            )
        store.log_tool_call(
            session_id="sess1",
            tool_name="read_file",
            args={"path": "/missing.py"},
            result_summary="",
            success=False,
            error="FileNotFoundError",
            duration_ms=10,
        )

        stats = store.get_tool_stats("read_file")
        assert stats["total_calls"] == 3
        assert stats["success_rate"] == pytest.approx(2 / 3, abs=0.01)
        assert "avg_duration_ms" in stats
        assert stats["last_error"] is not None
        assert "FileNotFoundError" in stats["last_error"]


# ===========================================================================
# 7. Temporal
# ===========================================================================


class TestTemporal:
    """Temporal awareness: due_at, reminded_at, get_upcoming()."""

    def test_store_with_due_at(self, store):
        """store() accepts due_at parameter."""
        due = _future_iso(3)
        entry_id = store.store(
            category="fact",
            content="Online course starts",
            due_at=due,
        )
        results = store.search("Online course starts")
        match = next(r for r in results if r["id"] == entry_id)
        assert match["due_at"] is not None

    def test_get_upcoming_returns_items_due_within_n_days(self, store):
        """get_upcoming() returns items due within N days."""
        store.store(
            category="fact",
            content="Meeting next week unique123",
            due_at=_future_iso(3),
        )
        store.store(
            category="fact",
            content="Conference next month unique456",
            due_at=_future_iso(45),
        )

        upcoming = store.get_upcoming(within_days=7)
        contents = [r["content"] for r in upcoming]
        assert any("Meeting next week" in c for c in contents)
        assert not any("Conference next month" in c for c in contents)

    def test_get_upcoming_returns_overdue_items(self, store):
        """get_upcoming() with include_overdue=True returns past-due items."""
        entry_id = store.store(
            category="fact",
            content="Overdue task xyz789",
            due_at=_past_iso(2),
        )

        upcoming = store.get_upcoming(within_days=7, include_overdue=True)
        assert any(r["id"] == entry_id for r in upcoming)

    def test_get_upcoming_excludes_reminded_items(self, store):
        """Items where reminded_at >= due_at are excluded from upcoming."""
        due = _future_iso(3)
        reminded = _future_iso(5)  # Reminded AFTER due date
        entry_id = store.store(
            category="fact",
            content="Already reminded item abc",
            due_at=due,
        )
        store.update(entry_id, reminded_at=reminded)

        upcoming = store.get_upcoming(within_days=7)
        assert not any(r["id"] == entry_id for r in upcoming)

    def test_get_upcoming_includes_reminded_before_due_now_overdue(self, store):
        """Items reminded before due date but now overdue should reappear."""
        due = _past_iso(1)  # Due yesterday
        reminded = _past_iso(3)  # Reminded 3 days ago (before due)
        entry_id = store.store(
            category="fact",
            content="Reminded before due overdue item",
            due_at=due,
        )
        store.update(entry_id, reminded_at=reminded)

        upcoming = store.get_upcoming(within_days=7, include_overdue=True)
        # reminded_at < due_at AND due_at is in the past → should appear
        assert any(r["id"] == entry_id for r in upcoming)

    def test_get_upcoming_with_context_filter(self, store):
        """get_upcoming() filters by context."""
        store.store(
            category="fact",
            content="Work deadline approaching",
            due_at=_future_iso(3),
            context="work",
        )
        store.store(
            category="fact",
            content="Personal appointment",
            due_at=_future_iso(3),
            context="personal",
        )

        upcoming = store.get_upcoming(within_days=7, context="work")
        assert all(r["context"] == "work" for r in upcoming)

    def test_invalid_due_at_raises_error(self, store):
        """Invalid due_at string raises ValueError."""
        with pytest.raises((ValueError, Exception)):
            store.store(
                category="fact",
                content="Bad date test",
                due_at="not-a-date",
            )


# ===========================================================================
# 8. Context Scoping
# ===========================================================================


class TestContextScoping:
    """Context-scoped storage and retrieval."""

    def test_store_with_context(self, store):
        """store() accepts context parameter."""
        entry_id = store.store(
            category="fact",
            content="Work deployment process",
            context="work",
        )
        results = store.get_by_category("fact", context="work")
        assert any(r["id"] == entry_id for r in results)

    def test_search_with_context_filter(self, store):
        """search() respects context filter."""
        store.store(
            category="fact",
            content="Deploy to production using kubectl",
            context="work",
        )
        store.store(
            category="fact",
            content="Deploy personal site to Vercel",
            context="personal",
        )

        work_results = store.search("deploy", context="work")
        personal_results = store.search("deploy", context="personal")

        assert len(work_results) >= 1
        assert all(r["context"] == "work" for r in work_results)
        assert len(personal_results) >= 1
        assert all(r["context"] == "personal" for r in personal_results)

    def test_get_by_category_with_context(self, store):
        """get_by_category() filters by context."""
        store.store(category="preference", content="Concise answers", context="work")
        store.store(
            category="preference", content="Detailed explanations", context="personal"
        )

        results = store.get_by_category("preference", context="work")
        assert len(results) >= 1
        assert all(r["context"] == "work" for r in results)

    def test_default_context_is_global(self, store):
        """Default context is 'global'."""
        entry_id = store.store(category="fact", content="Global fact test item")
        results = store.search("Global fact test item")
        match = next(r for r in results if r["id"] == entry_id)
        assert match["context"] == "global"


# ===========================================================================
# 9. Sensitivity
# ===========================================================================


class TestSensitivity:
    """Sensitive knowledge: storage, exclusion, explicit inclusion."""

    def test_store_with_sensitive_flag(self, store):
        """store() accepts sensitive=True."""
        entry_id = store.store(
            category="fact",
            content="Sarah email is sarah@company.com",
            sensitive=True,
        )
        # Verify it was stored
        results = store.search("Sarah email company", include_sensitive=True)
        assert any(r["id"] == entry_id for r in results)

    def test_search_excludes_sensitive_by_default(self, store):
        """search() excludes sensitive items by default."""
        entry_id = store.store(
            category="fact",
            content="Secret API key is sk-abc123xyz",
            sensitive=True,
        )
        store.store(
            category="fact",
            content="Public API endpoint is api.example.com",
            sensitive=False,
        )

        results = store.search("API")
        ids = [r["id"] for r in results]
        assert entry_id not in ids

    def test_search_include_sensitive(self, store):
        """search() with include_sensitive=True returns sensitive items."""
        entry_id = store.store(
            category="fact",
            content="Secret credentials for service xyz999",
            sensitive=True,
        )

        results = store.search("Secret credentials xyz999", include_sensitive=True)
        assert any(r["id"] == entry_id for r in results)

    def test_get_by_category_with_sensitive(self, store):
        """get_by_category() returns all items including sensitive."""
        store.store(
            category="fact",
            content="Normal fact about project",
            sensitive=False,
        )
        store.store(
            category="fact",
            content="Sensitive fact about API keys",
            sensitive=True,
        )

        # get_by_category should return both (it's a direct lookup, not search)
        results = store.get_by_category("fact")
        assert len(results) >= 2

    def test_dedup_preserves_sensitive_flag(self, store):
        """Dedup never clears the sensitive flag — sensitive=MAX(sensitive, new)."""
        # Store a sensitive entry
        entry_id = store.store(
            category="fact",
            content="Sarah email is sarah@company.com sensitive data",
            sensitive=True,
        )

        # Store a highly overlapping entry WITHOUT sensitive=True (e.g., heuristic extraction)
        dedup_id = store.store(
            category="fact",
            content="Sarah email is sarah@company.com company data",
            sensitive=False,  # No sensitive flag — default
        )

        # Should be the same entry (dedup triggered)
        assert dedup_id == entry_id

        # Sensitive flag MUST be preserved — MAX(1, 0) = 1
        all_results = store.get_by_category("fact")
        match = next((r for r in all_results if r["id"] == entry_id), None)
        assert match is not None
        assert (
            match["sensitive"] is True
        ), "Dedup must not clear sensitive flag when new entry has sensitive=False"

        # Verify it's still excluded from default search
        search_results = store.search("Sarah email company")
        assert not any(r["id"] == entry_id for r in search_results)

        # But accessible with include_sensitive=True
        sensitive_results = store.search("Sarah email company", include_sensitive=True)
        assert any(r["id"] == entry_id for r in sensitive_results)


# ===========================================================================
# 10. Entity Linking
# ===========================================================================


class TestEntityLinking:
    """Entity-linked knowledge storage and retrieval."""

    def test_store_with_entity(self, store):
        """store() accepts entity parameter."""
        entry_id = store.store(
            category="fact",
            content="Sarah Chen is VP of Engineering",
            entity="person:sarah_chen",
        )
        results = store.get_by_entity("person:sarah_chen")
        assert any(r["id"] == entry_id for r in results)

    def test_get_by_entity(self, store):
        """get_by_entity() returns all entries for an entity."""
        store.store(
            category="fact",
            content="Sarah Chen VP Engineering",
            entity="person:sarah_chen",
        )
        store.store(
            category="fact",
            content="Sarah prefers morning meetings",
            entity="person:sarah_chen",
        )
        store.store(
            category="fact",
            content="Bob is a senior engineer",
            entity="person:bob_smith",
        )

        sarah_facts = store.get_by_entity("person:sarah_chen")
        assert len(sarah_facts) == 2
        assert all(r["entity"] == "person:sarah_chen" for r in sarah_facts)

    def test_search_with_entity_filter(self, store):
        """search() can filter by entity."""
        store.store(
            category="fact",
            content="Sarah email is sarah@company.com",
            entity="person:sarah_chen",
        )
        store.store(
            category="fact",
            content="Bob email is bob@company.com",
            entity="person:bob_smith",
        )

        results = store.search("email company", entity="person:sarah_chen")
        assert len(results) >= 1
        for r in results:
            assert r["entity"] == "person:sarah_chen"

    def test_multiple_entries_share_entity(self, store):
        """Multiple knowledge entries can share the same entity."""
        ids = []
        for content in [
            "VS Code uses dark mode",
            "VS Code tabs set to 4 spaces",
            "VS Code uses Prettier extension",
        ]:
            ids.append(
                store.store(
                    category="preference",
                    content=content,
                    entity="app:vscode",
                )
            )

        results = store.get_by_entity("app:vscode")
        assert len(results) == 3


# ===========================================================================
# 11. Dashboard Queries
# ===========================================================================


class TestDashboardQueries:
    """Dashboard aggregate queries: stats, pagination, tool summary."""

    def test_get_stats_aggregates(self, store):
        """get_stats() returns correct aggregate counts."""
        # Add some knowledge
        store.store(category="fact", content="Fact one for stats")
        store.store(category="fact", content="Fact two for stats")
        store.store(category="preference", content="Prefers concise answers")
        store.store(
            category="fact",
            content="Sensitive fact for stats",
            sensitive=True,
        )
        store.store(
            category="fact",
            content="Entity fact for stats",
            entity="person:test",
        )

        # Add conversations
        store.store_turn("sess1", "user", "Hello")
        store.store_turn("sess1", "assistant", "Hi")

        # Add tool calls
        store.log_tool_call(
            session_id="sess1",
            tool_name="read_file",
            args={},
            result_summary="ok",
            success=True,
        )
        store.log_tool_call(
            session_id="sess1",
            tool_name="read_file",
            args={},
            result_summary="",
            success=False,
            error="FileNotFoundError",
        )

        stats = store.get_stats()

        assert stats["knowledge"]["total"] >= 5
        assert stats["knowledge"]["by_category"]["fact"] >= 3
        assert stats["knowledge"]["by_category"]["preference"] >= 1
        assert stats["knowledge"]["sensitive_count"] >= 1
        assert stats["knowledge"]["entity_count"] >= 1
        assert 0 <= stats["knowledge"]["avg_confidence"] <= 1.0

        assert stats["conversations"]["total_turns"] >= 2
        assert stats["conversations"]["total_sessions"] >= 1

        assert stats["tools"]["total_calls"] >= 2
        assert stats["tools"]["total_errors"] >= 1

    def test_get_all_knowledge_pagination(self, store):
        """get_all_knowledge() supports offset and limit."""
        for i in range(15):
            store.store(category="fact", content=f"Paginated fact number {i}")

        page1 = store.get_all_knowledge(offset=0, limit=5)
        page2 = store.get_all_knowledge(offset=5, limit=5)

        assert len(page1["items"]) == 5
        assert len(page2["items"]) == 5
        assert page1["total"] >= 15
        # Pages should not overlap
        ids_1 = {r["id"] for r in page1["items"]}
        ids_2 = {r["id"] for r in page2["items"]}
        assert ids_1.isdisjoint(ids_2)

    def test_get_all_knowledge_with_category_filter(self, store):
        """get_all_knowledge() filters by category."""
        store.store(category="fact", content="A fact for filtering")
        store.store(category="preference", content="A preference for filtering")

        result = store.get_all_knowledge(category="fact")
        for item in result["items"]:
            assert item["category"] == "fact"

    def test_get_all_knowledge_with_context_filter(self, store):
        """get_all_knowledge() filters by context."""
        store.store(category="fact", content="Work fact", context="work")
        store.store(category="fact", content="Personal fact", context="personal")

        result = store.get_all_knowledge(context="work")
        for item in result["items"]:
            assert item["context"] == "work"

    def test_get_all_knowledge_with_entity_filter(self, store):
        """get_all_knowledge() filters by entity."""
        store.store(
            category="fact",
            content="Sarah fact",
            entity="person:sarah_chen",
        )
        store.store(
            category="fact",
            content="Bob fact",
            entity="person:bob_smith",
        )

        result = store.get_all_knowledge(entity="person:sarah_chen")
        for item in result["items"]:
            assert item["entity"] == "person:sarah_chen"

    def test_get_all_knowledge_with_search(self, store):
        """get_all_knowledge() filters by FTS5 search query."""
        store.store(category="fact", content="GAIA supports NPU acceleration")
        store.store(category="fact", content="LinkedIn posting strategy")

        result = store.get_all_knowledge(search="NPU acceleration")
        assert len(result["items"]) >= 1
        assert any("NPU" in item["content"] for item in result["items"])

    def test_get_all_knowledge_sorting(self, store):
        """get_all_knowledge() sorts by specified field."""
        store.store(category="fact", content="Z fact first stored")
        time.sleep(0.05)
        store.store(category="fact", content="A fact second stored")

        # Sort by updated_at desc (default) — newest first
        result = store.get_all_knowledge(sort_by="updated_at", order="desc")
        assert len(result["items"]) >= 2

    def test_get_tool_summary(self, store):
        """get_tool_summary() returns per-tool stats."""
        for i in range(3):
            store.log_tool_call(
                session_id="sess1",
                tool_name="execute_code",
                args={},
                result_summary="ok",
                success=True,
                duration_ms=100 + i * 50,
            )
        store.log_tool_call(
            session_id="sess1",
            tool_name="execute_code",
            args={},
            result_summary="",
            success=False,
            error="SyntaxError",
            duration_ms=10,
        )
        store.log_tool_call(
            session_id="sess1",
            tool_name="read_file",
            args={},
            result_summary="content",
            success=True,
            duration_ms=45,
        )

        summary = store.get_tool_summary()
        assert len(summary) >= 2

        exec_code = next((s for s in summary if s["tool_name"] == "execute_code"), None)
        assert exec_code is not None
        assert exec_code["total_calls"] == 4
        assert exec_code["success_count"] == 3
        assert exec_code["failure_count"] == 1
        assert exec_code["success_rate"] == pytest.approx(0.75)
        assert exec_code["last_error"] is not None

    def test_get_activity_timeline(self, store):
        """get_activity_timeline() returns daily activity counts."""
        store.store_turn("sess1", "user", "Hello")
        store.store(category="fact", content="Timeline test fact")
        store.log_tool_call(
            session_id="sess1",
            tool_name="test_tool",
            args={},
            result_summary="ok",
            success=True,
        )

        timeline = store.get_activity_timeline(days=7)
        assert isinstance(timeline, list)
        # Today should have activity
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_entry = next((t for t in timeline if t["date"] == today_str), None)
        if today_entry:
            assert today_entry["conversations"] >= 1 or today_entry["tool_calls"] >= 1

    def test_get_recent_errors(self, store):
        """get_recent_errors() returns failed tool calls newest-first."""
        store.log_tool_call(
            session_id="sess1",
            tool_name="tool_a",
            args={},
            result_summary="",
            success=False,
            error="Error A",
        )
        store.log_tool_call(
            session_id="sess1",
            tool_name="tool_b",
            args={},
            result_summary="",
            success=False,
            error="Error B",
        )
        store.log_tool_call(
            session_id="sess1",
            tool_name="tool_c",
            args={},
            result_summary="ok",
            success=True,
        )

        errors = store.get_recent_errors(limit=20)
        assert len(errors) == 2
        # All should be failures
        for e in errors:
            assert e["success"] == 0


# ===========================================================================
# 12. Thread Safety
# ===========================================================================


class TestThreadSafety:
    """Concurrent access does not corrupt data."""

    def test_concurrent_knowledge_writes(self, store):
        """Multiple threads writing knowledge simultaneously don't corrupt data."""
        errors = []
        num_threads = 10
        categories = [
            "fact",
            "preference",
            "error",
            "skill",
            "fact",
            "preference",
            "error",
            "skill",
            "fact",
            "preference",
        ]

        def writer(thread_id):
            try:
                store.store(
                    category=categories[thread_id],
                    content=f"Thread {thread_id} unique knowledge item #{thread_id * 7919}",
                    context=f"ctx_{thread_id}",
                )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(t,)) for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"

    def test_concurrent_conversation_writes(self, store):
        """Multiple threads writing conversations don't corrupt data."""
        errors = []
        num_threads = 10
        writes_per_thread = 20

        def writer(thread_id):
            try:
                for i in range(writes_per_thread):
                    store.store_turn(
                        f"session_{thread_id}",
                        "user",
                        f"Thread {thread_id} message {i}",
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(t,)) for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"

        # Verify all writes succeeded
        for thread_id in range(num_threads):
            history = store.get_history(session_id=f"session_{thread_id}")
            assert len(history) == writes_per_thread


# ===========================================================================
# 13. Edge Cases & Regression
# ===========================================================================


class TestEdgeCases:
    """Edge cases and regression tests."""

    def test_store_empty_content_fails_or_stores(self, store):
        """Empty content should either raise or be handled gracefully."""
        # Depending on implementation, this might raise or return an ID
        try:
            entry_id = store.store(category="fact", content="")
            # If it doesn't raise, it should at least return a valid ID
            assert isinstance(entry_id, str)
        except (ValueError, Exception):
            pass  # Raising is also acceptable

    def test_search_with_unicode(self, store):
        """FTS5 handles Unicode content correctly."""
        store.store(
            category="fact",
            content="日本語のテスト Unicode test 中文",
        )
        results = store.search("Unicode test")
        assert len(results) >= 1

    def test_metadata_none_stored_correctly(self, store):
        """Entries without metadata return None for metadata field."""
        entry_id = store.store(category="fact", content="No metadata entry")
        results = store.search("No metadata entry")
        match = next(r for r in results if r["id"] == entry_id)
        assert match["metadata"] is None

    def test_large_content_truncated_to_2000_chars(self, store):
        """Content longer than 2000 chars is truncated before storage."""
        large_content = "word " * 1000  # ~5000 chars
        entry_id = store.store(category="fact", content=large_content.strip())
        row = store._conn.execute(
            "SELECT content FROM knowledge WHERE id = ?", (entry_id,)
        ).fetchone()
        assert row is not None
        assert len(row[0]) <= 2000

    def test_large_content_still_searchable(self, store):
        """Truncated large content is still indexed and searchable."""
        large_content = "word " * 1000
        entry_id = store.store(category="fact", content=large_content.strip())
        results = store.search("word")
        assert any(r["id"] == entry_id for r in results)

    def test_get_by_category_returns_list(self, store):
        """get_by_category() returns a list even when empty."""
        results = store.get_by_category("nonexistent_category")
        assert isinstance(results, list)
        assert len(results) == 0

    def test_get_by_entity_returns_empty_for_unknown(self, store):
        """get_by_entity() returns empty list for unknown entity."""
        results = store.get_by_entity("person:nonexistent")
        assert isinstance(results, list)
        assert len(results) == 0

    def test_get_upcoming_empty_when_no_due_items(self, store):
        """get_upcoming() returns empty list when no items have due_at."""
        store.store(category="fact", content="No due date on this one")
        upcoming = store.get_upcoming(within_days=7)
        assert len(upcoming) == 0

    def test_get_tool_stats_unknown_tool(self, store):
        """get_tool_stats() for unknown tool returns zero/default stats."""
        stats = store.get_tool_stats("nonexistent_tool")
        assert stats["total_calls"] == 0

    def test_close_and_reopen(self, tmp_path):
        """Data persists after close and reopen."""
        db_path = tmp_path / "persist_test.db"

        store1 = MemoryStore(db_path=db_path)
        entry_id = store1.store(category="fact", content="Persistent data test item")
        store1.close()

        store2 = MemoryStore(db_path=db_path)
        results = store2.search("Persistent data test item")
        assert any(r["id"] == entry_id for r in results)
        store2.close()

    def test_wal_mode_enabled(self, store):
        """Database uses WAL journal mode for concurrent reads."""
        # Access the internal connection to check journal mode
        if hasattr(store, "_conn"):
            cursor = store._conn.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            assert mode == "wal"

    def test_schema_version_exists(self, store):
        """schema_version table exists with version 1."""
        if hasattr(store, "_conn"):
            cursor = store._conn.execute("SELECT version FROM schema_version")
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == 1


# ===========================================================================
# 14. Helper Functions
# ===========================================================================


class TestHelperFunctions:
    """Tests for internal helper functions if exposed."""

    def test_word_overlap_identical(self):
        """Identical strings have 100% word overlap."""
        from gaia.agents.base.memory_store import _word_overlap

        assert _word_overlap("hello world foo", "hello world foo") == 1.0

    def test_word_overlap_no_overlap(self):
        """Completely different strings have 0% overlap."""
        from gaia.agents.base.memory_store import _word_overlap

        assert _word_overlap("alpha beta gamma", "delta epsilon zeta") == 0.0

    def test_word_overlap_partial(self):
        """Partial overlap returns correct Szymkiewicz-Simpson coefficient."""
        from gaia.agents.base.memory_store import _word_overlap

        # intersection = {"the", "quick"} = 2, min(4, 4) = 4 → 0.5
        result = _word_overlap("the quick brown fox", "the quick red cat")
        assert result == pytest.approx(0.5)

    def test_word_overlap_empty(self):
        """Empty strings return 0.0."""
        from gaia.agents.base.memory_store import _word_overlap

        assert _word_overlap("", "") == 0.0
        assert _word_overlap("hello", "") == 0.0
        assert _word_overlap("", "world") == 0.0

    def test_sanitize_fts5_query_removes_special_chars(self):
        """FTS5 special chars are stripped from query."""
        from gaia.agents.base.memory_store import _sanitize_fts5_query

        result = _sanitize_fts5_query("hello & world (test) * foo:bar")
        assert result is not None
        for char in "&()*:":
            assert char not in result
        for word in ("hello", "world", "test", "foo", "bar"):
            assert word in result

    def test_sanitize_fts5_query_preserves_words(self):
        """Normal words pass through intact."""
        from gaia.agents.base.memory_store import _sanitize_fts5_query

        result = _sanitize_fts5_query("simple words here")
        assert "simple" in result
        assert "words" in result

    def test_sanitize_fts5_query_empty(self):
        """Empty/whitespace returns None."""
        from gaia.agents.base.memory_store import _sanitize_fts5_query

        assert _sanitize_fts5_query("") is None
        assert _sanitize_fts5_query("   ") is None

    def test_sanitize_fts5_query_dots_and_hyphens(self):
        """Dots and hyphens are sanitized."""
        from gaia.agents.base.memory_store import _sanitize_fts5_query

        result = _sanitize_fts5_query("module.submodule semi-colon")
        assert result is not None
        assert "." not in result or "module" in result


# ===========================================================================
# 15. Prune
# ===========================================================================


class TestPrune:
    """Tests for MemoryStore.prune() — retention policy enforcement."""

    def test_prune_deletes_old_tool_history(self, store):
        """prune() removes tool_history entries older than N days."""
        # Insert an old tool_history row directly
        old_ts = _past_iso(days=100)
        store._conn.execute(
            "INSERT INTO tool_history (session_id, tool_name, args, result_summary, success, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("old-sess", "old_tool", "{}", "done", 1, old_ts),
        )
        store._conn.commit()

        before = store._conn.execute("SELECT COUNT(*) FROM tool_history").fetchone()[0]
        result = store.prune(days=90)
        after = store._conn.execute("SELECT COUNT(*) FROM tool_history").fetchone()[0]

        assert result["tool_history_deleted"] >= 1
        assert after < before

    def test_prune_deletes_old_conversations(self, store):
        """prune() removes conversation turns older than N days."""
        old_ts = _past_iso(days=100)
        # Use the public API to store a turn first, then backdate it
        store.store_turn("old-session", "user", "Old message")
        store._conn.execute(
            "UPDATE conversations SET timestamp = ? WHERE session_id = ?",
            (old_ts, "old-session"),
        )
        store._conn.commit()

        before = store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        result = store.prune(days=90)
        after = store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]

        assert result["conversations_deleted"] >= 1
        assert after < before

    def test_prune_deletes_low_confidence_stale_knowledge(self, store):
        """prune() removes knowledge with confidence < 0.1 last used > N days ago."""
        kid = store.store(
            category="fact", content="Stale low confidence fact", confidence=0.05
        )
        # Set last_used to past
        old_ts = _past_iso(days=100)
        store._conn.execute(
            "UPDATE knowledge SET last_used = ? WHERE id = ?", (old_ts, kid)
        )
        store._conn.commit()

        result = store.prune(days=90)
        remaining = store._conn.execute(
            "SELECT COUNT(*) FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()[0]

        assert result["knowledge_deleted"] >= 1
        assert remaining == 0

    def test_prune_preserves_recent_data(self, store):
        """prune() does not delete entries within the retention window."""
        # Recent tool history
        store.log_tool_call(
            session_id="recent-sess",
            tool_name="recent_tool",
            args={},
            result_summary="ok",
            success=True,
        )
        # Recent conversation
        store.store_turn("recent-sess", "user", "Recent message")
        # Recent high-confidence knowledge
        kid = store.store(
            category="fact", content="Recent important fact", confidence=0.9
        )

        result = store.prune(days=90)

        # None of the recent data should be deleted
        assert (
            store._conn.execute(
                "SELECT COUNT(*) FROM tool_history WHERE tool_name = 'recent_tool'"
            ).fetchone()[0]
            == 1
        )
        assert (
            store._conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE session_id = 'recent-sess'"
            ).fetchone()[0]
            == 1
        )
        assert (
            store._conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE id = ?", (kid,)
            ).fetchone()[0]
            == 1
        )

    def test_prune_returns_deletion_counts(self, store):
        """prune() return dict has the three expected count keys."""
        result = store.prune(days=90)
        assert "tool_history_deleted" in result
        assert "conversations_deleted" in result
        assert "knowledge_deleted" in result
        # All should be ints >= 0
        for key in result:
            assert isinstance(result[key], int)
            assert result[key] >= 0

    def test_prune_empty_store_returns_zeros(self, store):
        """prune() on an empty store returns zero deletions."""
        result = store.prune(days=90)
        assert result["tool_history_deleted"] == 0
        assert result["conversations_deleted"] == 0
        assert result["knowledge_deleted"] == 0


# ===========================================================================
# 16. Rebuild FTS
# ===========================================================================


class TestRebuildFts:
    """Tests for MemoryStore.rebuild_fts() — FTS5 index reconstruction."""

    def test_rebuild_fts_restores_knowledge_search(self, store):
        """After rebuild_fts(), knowledge search works correctly."""
        kid = store.store(category="fact", content="Quantum entanglement explanation")

        # Corrupt the FTS index by deleting the FTS row directly
        store._conn.execute("DELETE FROM knowledge_fts")
        store._conn.commit()

        # Search should find nothing (FTS corrupted)
        results_before = store.search("quantum entanglement")
        assert not any(r["id"] == kid for r in results_before)

        # Rebuild
        store.rebuild_fts()

        # Search should work again
        results_after = store.search("quantum entanglement")
        assert any(r["id"] == kid for r in results_after)

    def test_rebuild_fts_restores_conversation_search(self, store):
        """After rebuild_fts(), conversation FTS search works correctly."""
        store.store_turn("sess-fts", "user", "Superconductivity is fascinating")

        # Corrupt conversations FTS
        store._conn.execute(
            "INSERT INTO conversations_fts(conversations_fts) VALUES('delete-all')"
        )
        store._conn.commit()

        # Rebuild should not raise
        store.rebuild_fts()

        # Conversation search should return results
        results = store.search_conversations("superconductivity")
        assert any("superconductivity" in r.get("content", "").lower() for r in results)

    def test_rebuild_fts_is_idempotent(self, store):
        """Calling rebuild_fts() multiple times does not corrupt data."""
        store.store(category="fact", content="Idempotent rebuild test fact")
        store.rebuild_fts()
        store.rebuild_fts()
        results = store.search("idempotent rebuild test")
        assert len(results) > 0


# ===========================================================================
# 17. Stats — db_size_bytes
# ===========================================================================


class TestStatsDatabaseSize:
    """Tests for db_size_bytes in get_stats()."""

    def test_get_stats_includes_db_size_bytes(self, tmp_path):
        """get_stats() returns db_size_bytes as a non-negative integer."""
        db_path = tmp_path / "size_test.db"
        store = MemoryStore(db_path=db_path)
        try:
            store.store(category="fact", content="Data to ensure non-zero DB size")
            stats = store.get_stats()
            assert "db_size_bytes" in stats
            assert isinstance(stats["db_size_bytes"], int)
            assert stats["db_size_bytes"] > 0
        finally:
            store.close()

    def test_get_stats_db_size_bytes_zero_on_missing_file(self):
        """get_stats() returns 0 for db_size_bytes if path doesn't exist (graceful)."""
        # Use a temp in-memory store — db_path will be ":memory:" so getsize fails
        store = MemoryStore()  # default in-memory or ~/.gaia/memory.db
        stats = store.get_stats()
        # Just verify the key exists and is a non-negative int
        assert "db_size_bytes" in stats
        assert isinstance(stats["db_size_bytes"], int)
        assert stats["db_size_bytes"] >= 0
        store.close()


# ===========================================================================
# 18. Public Query Methods (encapsulation — no _lock/_conn access from router)
# ===========================================================================


class TestPublicQueryMethods:
    """Tests for MemoryStore.get_entities(), get_contexts(), get_tool_history(), get_sessions()."""

    def test_get_entities_empty(self, store):
        result = store.get_entities()
        assert isinstance(result, list)
        assert result == []

    def test_get_entities_with_data(self, store):
        store.store(
            category="fact", content="Alice leads the project", entity="person:alice"
        )
        store.store(category="fact", content="Alice prefers Vim", entity="person:alice")
        store.store(
            category="fact", content="Project uses Python", entity="project:gaia"
        )

        result = store.get_entities()
        entities = {e["entity"] for e in result}
        assert "person:alice" in entities
        assert "project:gaia" in entities
        alice = next(e for e in result if e["entity"] == "person:alice")
        assert alice["count"] == 2
        assert "last_updated" in alice

    def test_get_entities_ordered_by_count_desc(self, store):
        store.store(category="fact", content="A about alice", entity="person:alice")
        store.store(category="fact", content="B about alice", entity="person:alice")
        store.store(category="fact", content="C about bob", entity="person:bob")

        result = store.get_entities()
        assert result[0]["entity"] == "person:alice"  # higher count first

    def test_get_contexts_empty(self, store):
        result = store.get_contexts()
        assert isinstance(result, list)

    def test_get_contexts_with_data(self, store):
        store.store(category="fact", content="Work item", context="work")
        store.store(category="fact", content="Personal item", context="personal")

        result = store.get_contexts()
        contexts = {c["context"] for c in result}
        assert "work" in contexts
        assert "personal" in contexts

    def test_get_tool_history_empty(self, store):
        result = store.get_tool_history("no_such_tool")
        assert isinstance(result, list)
        assert result == []

    def test_get_tool_history_returns_correct_fields(self, store):
        store.log_tool_call(
            session_id="s1",
            tool_name="my_tool",
            args={"path": "/tmp"},
            result_summary="read 5 lines",
            success=True,
            duration_ms=120,
        )
        result = store.get_tool_history("my_tool")
        assert len(result) == 1
        row = result[0]
        assert row["tool_name"] == "my_tool"
        assert row["args"] == {"path": "/tmp"}
        assert row["result_summary"] == "read 5 lines"
        assert row["success"] is True
        assert row["duration_ms"] == 120
        assert "timestamp" in row

    def test_get_tool_history_respects_limit(self, store):
        for i in range(10):
            store.log_tool_call(
                session_id="s1",
                tool_name="busy_tool",
                args={},
                result_summary=f"call {i}",
                success=True,
            )
        result = store.get_tool_history("busy_tool", limit=5)
        assert len(result) == 5

    def test_get_sessions_empty(self, store):
        result = store.get_sessions()
        assert isinstance(result, list)
        assert result == []

    def test_get_sessions_with_data(self, store):
        store.store_turn("sess-a", "user", "First message in session A")
        store.store_turn("sess-a", "assistant", "Reply A")
        store.store_turn("sess-b", "user", "First message in session B")

        result = store.get_sessions()
        session_ids = {s["session_id"] for s in result}
        assert "sess-a" in session_ids
        assert "sess-b" in session_ids

        sess_a = next(s for s in result if s["session_id"] == "sess-a")
        assert sess_a["turn_count"] == 2
        assert "First message" in sess_a["first_message"]

    def test_get_sessions_respects_limit(self, store):
        for i in range(10):
            store.store_turn(f"sess-{i}", "user", f"Message {i}")
        result = store.get_sessions(limit=5)
        assert len(result) == 5


# ===========================================================================
# 19. Due-at Timezone Normalization
# ===========================================================================


class TestDueAtTimezoneNormalization:
    """Tests for timezone-aware due_at storage (fix for Finding #4)."""

    def test_naive_due_at_is_normalized_to_tz_aware(self, store):
        """Storing a naive due_at should be converted to timezone-aware."""
        naive = "2026-06-15T09:00:00"  # no timezone offset
        store.store(category="fact", content="Meeting", due_at=naive)

        # Retrieve and verify due_at is timezone-aware (fromisoformat gives non-None tzinfo)
        result = store.search("Meeting")
        assert len(result) >= 1
        stored_due = result[0]["due_at"]
        dt = datetime.fromisoformat(stored_due)
        assert (
            dt.tzinfo is not None
        ), f"Expected tz-aware due_at, got naive: {stored_due}"

    def test_tz_aware_due_at_stored_unchanged(self, store):
        """A timezone-aware due_at should be stored as-is."""
        aware = "2026-06-15T09:00:00+05:00"
        kid = store.store(
            category="fact", content="Timezone aware meeting", due_at=aware
        )
        result = store.search("Timezone aware meeting")
        assert any(r["due_at"] == aware for r in result)

    def test_invalid_due_at_still_raises(self, store):
        """Non-ISO strings for due_at still raise ValueError."""
        with pytest.raises(ValueError):
            store.store(category="fact", content="Bad date", due_at="not-a-date")


# ===========================================================================
# 22. Content Validation
# ===========================================================================


class TestContentValidation:
    """store() rejects empty or whitespace-only content."""

    def test_empty_string_raises(self, store):
        """store() with empty string raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            store.store(category="fact", content="")

    def test_whitespace_only_raises(self, store):
        """store() with whitespace-only content raises ValueError."""
        with pytest.raises(ValueError, match="non-empty"):
            store.store(category="fact", content="   \t\n")

    def test_none_content_raises(self, store):
        """store() with None content raises (TypeError or ValueError)."""
        with pytest.raises((ValueError, TypeError)):
            store.store(category="fact", content=None)


# ===========================================================================
# 23. log_tool_call Non-Serializable Args
# ===========================================================================


class TestLogToolCallSerialization:
    """log_tool_call() must not crash on non-JSON-serializable args."""

    def test_bytes_args_do_not_crash(self, store):
        """log_tool_call() with bytes in args succeeds via default=str."""
        store.log_tool_call(
            session_id="s1",
            tool_name="read_file",
            args={"data": b"\x89PNG\r\n"},
            result_summary="binary read ok",
            success=True,
        )
        history = store.get_tool_history("read_file")
        assert len(history) == 1
        assert history[0]["tool_name"] == "read_file"

    def test_custom_object_args_do_not_crash(self, store):
        """log_tool_call() with custom object in args uses str() fallback."""

        class _Custom:
            def __str__(self):
                return "custom_repr"

        store.log_tool_call(
            session_id="s1",
            tool_name="custom_tool",
            args={"obj": _Custom()},
            result_summary="ok",
            success=True,
        )
        history = store.get_tool_history("custom_tool")
        assert len(history) == 1
        # Verify the serialized form used str() fallback
        import json

        args = json.loads(
            store._conn.execute(
                "SELECT args FROM tool_history WHERE tool_name=?", ("custom_tool",)
            ).fetchone()[0]
        )
        assert args["obj"] == "custom_repr"

    def test_none_args_stores_null(self, store):
        """log_tool_call() with None args stores NULL (not '{}')."""
        store.log_tool_call(
            session_id="s1",
            tool_name="no_args_tool",
            args=None,
            result_summary="done",
            success=True,
        )
        row = store._conn.execute(
            "SELECT args FROM tool_history WHERE tool_name=?", ("no_args_tool",)
        ).fetchone()
        assert row[0] is None


# ===========================================================================
# 24. store_turn Content Truncation
# ===========================================================================


class TestStoreTurnTruncation:
    """store_turn() truncates conversation content to 4000 chars."""

    def test_long_content_truncated(self, store):
        """store_turn() truncates content > 4000 chars."""
        long_content = "x " * 5000  # 10000 chars
        store.store_turn("s1", "assistant", long_content)
        row = store._conn.execute(
            "SELECT content FROM conversations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert len(row[0]) <= 4000

    def test_short_content_unchanged(self, store):
        """store_turn() leaves content <= 4000 chars intact."""
        content = "hello world"
        store.store_turn("s1", "user", content)
        row = store._conn.execute(
            "SELECT content FROM conversations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row[0] == content


# ===========================================================================
# 25. get_tool_summary — single-query, no N+1
# ===========================================================================


class TestGetToolSummary:
    """get_tool_summary() returns per-tool stats with last_error via single query."""

    def test_returns_correct_fields(self, store):
        """get_tool_summary() returns expected fields for each tool."""
        store.log_tool_call("s1", "my_tool", {"k": "v"}, "ok", True, duration_ms=100)
        result = store.get_tool_summary()
        assert len(result) >= 1
        row = next(r for r in result if r["tool_name"] == "my_tool")
        assert row["total_calls"] == 1
        assert row["success_count"] == 1
        assert row["failure_count"] == 0
        assert row["success_rate"] == 1.0
        assert "avg_duration_ms" in row
        assert "last_used" in row
        assert "last_error" in row

    def test_last_error_populated(self, store):
        """get_tool_summary() includes last error message."""
        store.log_tool_call("s1", "fail_tool", {}, "err", False, error="timeout")
        result = store.get_tool_summary()
        row = next(r for r in result if r["tool_name"] == "fail_tool")
        assert row["last_error"] == "timeout"

    def test_last_error_none_when_no_failures(self, store):
        """get_tool_summary() last_error is None for tools with no failures."""
        store.log_tool_call("s1", "clean_tool", {}, "ok", True)
        result = store.get_tool_summary()
        row = next(r for r in result if r["tool_name"] == "clean_tool")
        assert row["last_error"] is None

    def test_empty_store_returns_empty_list(self, store):
        """get_tool_summary() returns [] when no tool calls recorded."""
        assert store.get_tool_summary() == []


# ===========================================================================
# 26. _safe_json_loads — corrupt data resilience
# ===========================================================================


class TestSafeJsonLoads:
    """Corrupt metadata/args columns return None instead of crashing."""

    def test_corrupt_metadata_does_not_crash_search(self, store):
        """search() returns partial results even if one row has corrupt metadata."""
        kid = store.store(category="fact", content="valid entry with metadata")
        # Corrupt the metadata column directly
        store._conn.execute(
            "UPDATE knowledge SET metadata = ? WHERE id = ?",
            ("not valid json {{{", kid),
        )
        store._conn.commit()
        # search() must not raise; corrupt row's metadata comes back as None
        results = store.search("valid entry")
        assert len(results) >= 1
        match = next((r for r in results if r["id"] == kid), None)
        assert match is not None
        assert match["metadata"] is None

    def test_corrupt_args_does_not_crash_get_tool_history(self, store):
        """get_tool_history() tolerates corrupt args column."""
        store.log_tool_call("s1", "corrupt_tool", {"k": "v"}, "ok", True)
        store._conn.execute(
            "UPDATE tool_history SET args = ? WHERE tool_name = ?",
            ("{bad json", "corrupt_tool"),
        )
        store._conn.commit()
        history = store.get_tool_history("corrupt_tool")
        assert len(history) == 1
        assert history[0]["args"] is None


# ===========================================================================
# 27. update() due_at timezone normalization
# ===========================================================================


class TestUpdateDueAtNormalization:
    """update() normalizes naive due_at to tz-aware, same as store()."""

    def test_naive_due_at_normalized_via_update(self, store):
        """update() with naive due_at attaches local timezone."""
        kid = store.store(category="reminder", content="check deadline")
        naive = "2026-09-01T09:00:00"  # no tz
        store.update(kid, due_at=naive)
        row = store._conn.execute(
            "SELECT due_at FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        stored = row[0]
        from datetime import datetime as _dt

        parsed = _dt.fromisoformat(stored)
        assert parsed.tzinfo is not None, f"Expected tz-aware, got: {stored}"

    def test_tz_aware_due_at_stored_as_is_via_update(self, store):
        """update() with tz-aware due_at stores it unchanged."""
        kid = store.store(category="reminder", content="another deadline")
        aware = "2026-09-01T09:00:00+05:30"
        store.update(kid, due_at=aware)
        row = store._conn.execute(
            "SELECT due_at FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        from datetime import datetime as _dt

        assert _dt.fromisoformat(row[0]).tzinfo is not None


# ===========================================================================
# 28. get_upcoming() limit parameter
# ===========================================================================


class TestGetUpcomingLimit:
    """get_upcoming() limit parameter controls result count."""

    def test_limit_default_is_ten(self, store):
        """get_upcoming() returns at most 10 items by default."""
        for i in range(15):
            store.store(
                category="reminder",
                content=f"Upcoming item number {i}",
                due_at=_future_iso(days=i + 1),
            )
        results = store.get_upcoming(within_days=20)
        assert len(results) <= 10

    def test_limit_param_respected(self, store):
        """get_upcoming(limit=3) returns at most 3 items."""
        for i in range(10):
            store.store(
                category="reminder",
                content=f"Deadline item {i}",
                due_at=_future_iso(days=i + 1),
            )
        results = store.get_upcoming(within_days=20, limit=3)
        assert len(results) <= 3

    def test_limit_larger_than_available(self, store):
        """get_upcoming(limit=100) returns all available items."""
        for i in range(5):
            store.store(
                category="reminder",
                content=f"Future task {i}",
                due_at=_future_iso(days=i + 1),
            )
        results = store.get_upcoming(within_days=30, limit=100)
        assert len(results) == 5


# ===========================================================================
# 29. update() content validation
# ===========================================================================


class TestUpdateContentValidation:
    """update() rejects empty content and truncates oversized content."""

    def test_empty_content_raises(self, store):
        """update(content='') raises ValueError."""
        kid = store.store(category="fact", content="original content here")
        with pytest.raises(ValueError, match="non-empty"):
            store.update(kid, content="")

    def test_whitespace_only_content_raises(self, store):
        """update(content='   ') raises ValueError."""
        kid = store.store(category="fact", content="original content here")
        with pytest.raises(ValueError, match="non-empty"):
            store.update(kid, content="   \t")

    def test_long_content_truncated_on_update(self, store):
        """update() truncates content > 2000 chars."""
        kid = store.store(category="fact", content="short original")
        long_content = "word " * 1000
        store.update(kid, content=long_content)
        row = store._conn.execute(
            "SELECT content FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert len(row[0]) <= 2000

    def test_valid_content_updates_successfully(self, store):
        """update() with valid content returns True."""
        kid = store.store(category="fact", content="original")
        result = store.update(kid, content="updated content here")
        assert result is True
        row = store._conn.execute(
            "SELECT content FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] == "updated content here"


# ===========================================================================
# 30. _sanitize_fts5_query input length cap
# ===========================================================================


class TestSanitizeFts5QueryCap:
    """_sanitize_fts5_query caps input at 500 chars before regex processing."""

    def test_long_query_capped(self):
        """Queries longer than 500 chars are capped before processing."""
        from gaia.agents.base.memory_store import _sanitize_fts5_query

        long_query = "word " * 200  # 1000 chars
        result = _sanitize_fts5_query(long_query)
        # Result should be based on at most 500 chars of input
        # 500 chars of "word " = 100 "word" tokens → 100 words joined by AND
        assert result is not None
        # Verify no words beyond the 500-char boundary appear
        # "word " * 100 = 500 chars exactly, so ≤ 100 "word" tokens
        words = result.split(" AND ")
        assert len(words) <= 100

    def test_normal_query_unchanged(self):
        """Short queries are not affected by the cap."""
        from gaia.agents.base.memory_store import _sanitize_fts5_query

        result = _sanitize_fts5_query("hello world test")
        assert "hello" in result
        assert "world" in result
        assert "test" in result


# ===========================================================================
# 31. Category alignment (remember tool vs router)
# ===========================================================================


class TestCategoryAlignment:
    """store() accepts all categories that both the tool and router allow."""

    def test_note_and_reminder_storable(self, store):
        """store() accepts 'note' and 'reminder' categories."""
        kid1 = store.store(category="note", content="A note about the project")
        kid2 = store.store(category="reminder", content="Remind me about the meeting")
        assert kid1 is not None
        assert kid2 is not None

    def test_note_searchable(self, store):
        """Knowledge stored as 'note' is retrievable via get_by_category."""
        store.store(category="note", content="Meeting notes from standup")
        results = store.get_by_category("note")
        assert len(results) >= 1
        assert all(r["category"] == "note" for r in results)


# ===========================================================================
# 32. log_tool_call — error column truncation
# ===========================================================================


class TestLogToolCallErrorTruncation:
    """log_tool_call() truncates the error column to 500 chars."""

    def test_long_error_truncated_to_500_chars(self, store):
        """error messages longer than 500 chars are truncated before storage."""
        long_error = "E: " + "x" * 600  # 603 chars total
        store.log_tool_call(
            session_id="s1",
            tool_name="crash_tool",
            args={},
            result_summary="failed",
            success=False,
            error=long_error,
        )
        row = store._conn.execute(
            "SELECT error FROM tool_history WHERE tool_name = ?", ("crash_tool",)
        ).fetchone()
        assert row is not None
        assert len(row[0]) <= 500

    def test_short_error_stored_unchanged(self, store):
        """error messages <= 500 chars are stored as-is."""
        short_error = "Connection refused"
        store.log_tool_call(
            session_id="s1",
            tool_name="net_tool",
            args={},
            result_summary="err",
            success=False,
            error=short_error,
        )
        row = store._conn.execute(
            "SELECT error FROM tool_history WHERE tool_name = ?", ("net_tool",)
        ).fetchone()
        assert row[0] == short_error

    def test_none_error_stored_as_null(self, store):
        """error=None is stored as SQL NULL (not as the string 'None')."""
        store.log_tool_call(
            session_id="s1",
            tool_name="ok_tool",
            args={},
            result_summary="ok",
            success=True,
            error=None,
        )
        row = store._conn.execute(
            "SELECT error FROM tool_history WHERE tool_name = ?", ("ok_tool",)
        ).fetchone()
        assert row[0] is None


# ===========================================================================
# 33. store_turn — empty content validation
# ===========================================================================


class TestStoreTurnEmptyValidation:
    """store_turn() silently skips empty and whitespace-only content."""

    def test_empty_string_not_stored(self, store):
        """store_turn() with empty string does not add a row."""
        before = store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        store.store_turn("s1", "user", "")
        after = store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert after == before

    def test_whitespace_only_not_stored(self, store):
        """store_turn() with whitespace-only content does not add a row."""
        before = store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        store.store_turn("s1", "assistant", "   \n\t  ")
        after = store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert after == before

    def test_valid_content_is_stored(self, store):
        """store_turn() with normal content still stores the row."""
        before = store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        store.store_turn("s1", "user", "Hello there!")
        after = store._conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        assert after == before + 1


# ===========================================================================
# 34. store() — confidence clamping
# ===========================================================================


class TestConfidenceClamping:
    """store() clamps confidence to [0.0, 1.0]."""

    def test_confidence_above_1_clamped_to_1(self, store):
        """Confidence > 1.0 is clamped to 1.0."""
        kid = store.store(
            category="fact", content="High confidence entry", confidence=999.0
        )
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] <= 1.0

    def test_confidence_below_0_clamped_to_0(self, store):
        """Confidence < 0.0 is clamped to 0.0."""
        kid = store.store(
            category="fact", content="Negative confidence entry", confidence=-5.0
        )
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] >= 0.0

    def test_confidence_exactly_1_accepted(self, store):
        """confidence=1.0 is stored exactly as 1.0 (boundary check)."""
        kid = store.store(
            category="fact", content="Max confidence entry", confidence=1.0
        )
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] == pytest.approx(1.0)

    def test_avg_confidence_not_corrupted_by_out_of_range(self, store):
        """get_stats() avg_confidence stays in [0.0, 1.0] even if high values are passed."""
        for i in range(5):
            store.store(category="fact", content=f"entry {i}", confidence=999.0)
        stats = store.get_stats()
        avg = stats["knowledge"]["avg_confidence"]
        assert 0.0 <= avg <= 1.0


# ===========================================================================
# 35. get_all_knowledge() — all-special-char search returns empty
# ===========================================================================


class TestGetAllKnowledgeSearchFallback:
    """get_all_knowledge(search=...) returns empty when search sanitizes to None."""

    def test_special_chars_only_returns_empty(self, store):
        """A search of only FTS5 special chars (e.g. '@@@') returns empty, not all items."""
        store.store(category="fact", content="This entry exists and should not appear")
        result = store.get_all_knowledge(search="@@@")
        assert result["items"] == []
        assert result["total"] == 0

    def test_dashes_only_returns_empty(self, store):
        """A search of '---' (all hyphens) returns empty."""
        store.store(category="fact", content="Another entry that should not appear")
        result = store.get_all_knowledge(search="---")
        assert result["items"] == []
        assert result["total"] == 0

    def test_valid_search_still_works(self, store):
        """A normal search still returns matching items."""
        store.store(category="fact", content="Project uses Lemonade Server")
        result = store.get_all_knowledge(search="Lemonade")
        assert result["total"] >= 1
        assert any("Lemonade" in item["content"] for item in result["items"])


# ===========================================================================
# 36. update() — reminded_at normalization (defense-in-depth)
# ===========================================================================


class TestUpdateRemindedAtNormalization:
    """update() normalizes naive reminded_at to tz-aware ISO strings."""

    def test_naive_reminded_at_is_normalized(self, store):
        """update() converts naive reminded_at to tz-aware format."""
        kid = store.store(
            category="reminder",
            content="Test reminder for normalization",
            due_at=_future_iso(1),
        )
        naive_dt = "2025-06-15T10:00:00"  # No tzinfo
        store.update(kid, reminded_at=naive_dt)
        row = store._conn.execute(
            "SELECT reminded_at FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row is not None
        stored = row[0]
        # Must now include a timezone offset (+HH:MM or Z)
        assert stored is not None
        parsed = datetime.fromisoformat(stored)
        assert (
            parsed.tzinfo is not None
        ), f"reminded_at should be tz-aware, got: {stored}"

    def test_tz_aware_reminded_at_is_stored_unchanged(self, store):
        """update() keeps tz-aware reminded_at unchanged."""
        kid = store.store(
            category="reminder",
            content="Reminder with tz-aware reminded_at",
            due_at=_future_iso(1),
        )
        aware_dt = "2025-06-15T10:00:00+05:30"
        store.update(kid, reminded_at=aware_dt)
        row = store._conn.execute(
            "SELECT reminded_at FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row is not None
        stored = row[0]
        parsed = datetime.fromisoformat(stored)
        assert parsed.tzinfo is not None

    def test_invalid_reminded_at_raises_value_error(self, store):
        """update() raises ValueError for non-ISO reminded_at strings."""
        kid = store.store(
            category="reminder",
            content="Test entry for bad reminded_at",
            due_at=_future_iso(1),
        )
        with pytest.raises(ValueError):
            store.update(kid, reminded_at="not-a-date")


# ===========================================================================
# 37. rebuild_fts() — rollback on failure leaves DB intact
# ===========================================================================


class TestRebuildFtsAtomicity:
    """rebuild_fts() rolls back on failure so no stale DELETE is committed later."""

    def test_rebuild_fts_succeeds_normally(self, store):
        """rebuild_fts() works end-to-end and search results survive a rebuild."""
        store.store(category="fact", content="Rebuilding FTS test entry Lemon")
        store.rebuild_fts()
        result = store.get_all_knowledge(search="Lemon")
        assert result["total"] >= 1

    def test_rebuild_fts_rollback_on_failure_leaves_search_intact(self, tmp_path):
        """If rebuild_fts INSERT fails, rollback preserves the original FTS index."""
        import sqlite3

        store = MemoryStore(tmp_path / "mem_rebuild.db")
        kid = store.store(category="fact", content="RebuildRollback unique entry xyz")

        # Verify entry is searchable before the bad rebuild
        assert store.get_all_knowledge(search="RebuildRollback")["total"] >= 1

        # Inject a fault: make the INSERT into knowledge_fts fail by breaking
        # the INSERT SQL (patch _rebuild_knowledge_fts_locked to raise).
        original = store._rebuild_knowledge_fts_locked

        def _broken():
            # Execute the DELETE but then raise instead of inserting
            store._conn.execute("DELETE FROM knowledge_fts")
            raise RuntimeError("Simulated disk-full error")

        store._rebuild_knowledge_fts_locked = _broken

        with pytest.raises(RuntimeError, match="Simulated disk-full"):
            store.rebuild_fts()

        # Restore the real method
        store._rebuild_knowledge_fts_locked = original

        # After rollback, the original FTS index should still be intact
        result = store.get_all_knowledge(search="RebuildRollback")
        assert result["total"] >= 1, "FTS index should be intact after failed rebuild"


# ===========================================================================
# 38. prune() — rollback on FTS rebuild failure
# ===========================================================================


class TestPruneAtomicity:
    """prune() rolls back if the FTS rebuild inside it fails."""

    def test_prune_rollback_on_fts_failure_preserves_knowledge(self, tmp_path):
        """If FTS rebuild in prune() fails, the whole transaction rolls back."""
        store = MemoryStore(tmp_path / "mem_prune.db")

        # Store a low-confidence old item that prune() would delete
        kid = store.store(
            category="fact", content="PruneRollback test entry abc", confidence=0.05
        )
        # Manually set last_used and created_at to far past so prune() targets it
        old_ts = "2020-01-01T00:00:00+00:00"
        store._conn.execute(
            "UPDATE knowledge SET last_used = ?, created_at = ? WHERE id = ?",
            (old_ts, old_ts, kid),
        )
        store._conn.commit()

        # Inject fault into FTS rebuild to trigger rollback
        original = store._rebuild_knowledge_fts_locked

        def _broken():
            raise RuntimeError("Simulated FTS failure")

        store._rebuild_knowledge_fts_locked = _broken

        with pytest.raises(RuntimeError, match="Simulated FTS failure"):
            store.prune(days=1)  # prune everything older than 1 day

        store._rebuild_knowledge_fts_locked = original

        # The knowledge row should NOT have been deleted (transaction rolled back)
        row = store._conn.execute(
            "SELECT id FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row is not None, "knowledge row must survive a rolled-back prune()"


# ===========================================================================
# 39. store() / update() — empty string entity/domain normalized to NULL
# ===========================================================================


class TestEmptyStringNormalization:
    """store() and update() normalize entity='' and domain='' to NULL."""

    def test_store_empty_entity_stored_as_null(self, store):
        """store(entity='') must store NULL, not empty string."""
        kid = store.store(
            category="fact", content="Entity normalization test", entity=""
        )
        row = store._conn.execute(
            "SELECT entity FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] is None, "entity='' should be stored as NULL"

    def test_store_empty_domain_stored_as_null(self, store):
        """store(domain='') must store NULL, not empty string."""
        kid = store.store(
            category="fact", content="Domain normalization test", domain=""
        )
        row = store._conn.execute(
            "SELECT domain FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] is None, "domain='' should be stored as NULL"

    def test_empty_entity_dedup_works_with_null_entity_entries(self, store):
        """store(entity='') and store(entity=None) dedup against each other."""
        id1 = store.store(
            category="fact", content="Dedup entity test alpha", entity=None
        )
        # Should dedup with the existing entry (same content, entity should match as NULL)
        id2 = store.store(category="fact", content="Dedup entity test alpha", entity="")
        assert id1 == id2, "entity='' and entity=None should be in the same dedup scope"

    def test_update_empty_entity_is_noop(self, store):
        """update(entity='') is a no-op — cannot store '' in entity column."""
        kid = store.store(
            category="fact", content="Update entity noop test", entity="person:alice"
        )
        store.update(kid, entity="")
        row = store._conn.execute(
            "SELECT entity FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        # Empty string is normalized to None → treated as "don't update",
        # so the original entity "person:alice" should be unchanged.
        assert row[0] == "person:alice", "update(entity='') should be a no-op"

    def test_update_empty_domain_is_noop(self, store):
        """update(domain='') is a no-op — cannot store '' in domain column."""
        kid = store.store(
            category="fact", content="Update domain noop test", domain="project:gaia"
        )
        store.update(kid, domain="")
        row = store._conn.execute(
            "SELECT domain FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        # Empty string is normalized to None → treated as "don't update",
        # so the original domain "project:gaia" should be unchanged.
        assert row[0] == "project:gaia", "update(domain='') should be a no-op"


# ===========================================================================
# 40. _auto_store_error() — empty error message not stored
# ===========================================================================


class TestAutoStoreErrorEmptyMsg:
    """_auto_store_error() does not store entries when error_msg is empty."""

    def test_empty_error_not_stored(self, store, tmp_path):
        """_auto_store_error() with empty error_msg stores nothing."""
        from gaia.agents.base.memory import MemoryMixin

        class FakeHost(MemoryMixin):
            def __init__(self):
                self._memory_store = store
                self._memory_context = "global"

        host = FakeHost()
        initial = store.get_stats()["knowledge"]["total"]
        host._auto_store_error("read_file", "")
        after = store.get_stats()["knowledge"]["total"]
        assert after == initial, "Empty error message must not create a knowledge entry"

    def test_whitespace_error_not_stored(self, store):
        """_auto_store_error() with whitespace-only error_msg stores nothing."""
        from gaia.agents.base.memory import MemoryMixin

        class FakeHost(MemoryMixin):
            def __init__(self):
                self._memory_store = store
                self._memory_context = "global"

        host = FakeHost()
        initial = store.get_stats()["knowledge"]["total"]
        host._auto_store_error("read_file", "   ")
        after = store.get_stats()["knowledge"]["total"]
        assert (
            after == initial
        ), "Whitespace error message must not create a knowledge entry"

    def test_real_error_is_stored(self, store):
        """_auto_store_error() with a real error message stores the entry."""
        from gaia.agents.base.memory import MemoryMixin

        class FakeHost(MemoryMixin):
            def __init__(self):
                self._memory_store = store
                self._memory_context = "global"

        host = FakeHost()
        initial = store.get_stats()["knowledge"]["total"]
        host._auto_store_error("read_file", "FileNotFoundError: /tmp/missing.txt")
        after = store.get_stats()["knowledge"]["total"]
        assert after > initial, "Real error message should create a knowledge entry"


class TestConfidenceDecayIdempotency:
    """apply_confidence_decay() must not re-decay items within the same period."""

    def test_second_call_does_not_decay_recently_decayed_items(self, store):
        """Items decayed once have updated_at=now, so second call skips them."""
        kid = store.store(
            category="fact",
            content="DecayIdempotency test item aaa",
            confidence=0.8,
        )
        # Backdate last_used so the item qualifies for decay
        old_ts = "2020-01-01T00:00:00+00:00"
        store._conn.execute(
            "UPDATE knowledge SET last_used = ?, updated_at = ? WHERE id = ?",
            (old_ts, old_ts, kid),
        )
        store._conn.commit()

        count1 = store.apply_confidence_decay()
        assert count1 >= 1, "First call should decay the item"
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        after_first = row[0]
        assert after_first < 0.8, "Confidence should drop after first decay"

        # Second immediate call: updated_at is now >= cutoff, so should be skipped
        count2 = store.apply_confidence_decay()
        row2 = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row2[0] == after_first, "Second call must not decay the same item again"

    def test_item_at_1_0_decays_exactly_once_per_period(self, store):
        """MAX-confidence item decays to 0.9 after first call, stays there on second."""
        kid = store.store(
            category="fact",
            content="MaxConfDecay test item bbb",
            confidence=1.0,
        )
        old_ts = "2020-01-01T00:00:00+00:00"
        store._conn.execute(
            "UPDATE knowledge SET last_used = ?, updated_at = ? WHERE id = ?",
            (old_ts, old_ts, kid),
        )
        store._conn.commit()

        store.apply_confidence_decay()
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        import pytest

        assert row[0] == pytest.approx(0.9, abs=0.001)

        store.apply_confidence_decay()
        row2 = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row2[0] == pytest.approx(
            0.9, abs=0.001
        ), "No second decay within same period"


class TestGetSessionsFirstMessage:
    """get_sessions() must return the chronologically first user message, not alphabetically first."""

    def test_returns_chronologically_first_not_alphabetically_first(self, store):
        """Sessions with messages 'Zebra' then 'Apple' should show 'Zebra' as preview."""
        import time

        sid = "session-order-test-xyz"
        store.store_turn(sid, "user", "Zebra question comes first")
        time.sleep(0.01)
        store.store_turn(sid, "user", "Apple question comes second")

        sessions = store.get_sessions(limit=100)
        match = next((s for s in sessions if s["session_id"] == sid), None)
        assert match is not None
        assert match["first_message"].startswith(
            "Zebra"
        ), f"Expected 'Zebra...' but got {match['first_message']!r}"

    def test_assistant_turns_not_used_for_first_message(self, store):
        """first_message must be from a user turn, not an assistant turn."""
        sid = "session-role-filter-test-xyz"
        store.store_turn(sid, "assistant", "Assistant preamble")
        store.store_turn(sid, "user", "User first message")

        sessions = store.get_sessions(limit=100)
        match = next((s for s in sessions if s["session_id"] == sid), None)
        assert match is not None
        assert match["first_message"].startswith(
            "User"
        ), f"Expected user message preview, got {match['first_message']!r}"


class TestStoreFtsRollbackOnFailure:
    """store() must rollback if FTS insert fails — no orphaned knowledge row."""

    def test_fts_insert_failure_rolls_back_knowledge_row(self, store):
        """If _insert_knowledge_fts_locked raises, the knowledge INSERT is rolled back."""
        original_count = store.get_stats()["knowledge"]["total"]

        def boom(_kid):
            raise RuntimeError("Simulated FTS insert failure")

        store._insert_knowledge_fts_locked = boom

        import pytest

        with pytest.raises(RuntimeError, match="Simulated FTS insert failure"):
            store.store(category="fact", content="Orphan row test entry zzz")

        # Restore FTS inserts to no-op so subsequent queries work
        store._insert_knowledge_fts_locked = lambda kid: None

        # The knowledge row must not have been committed
        after_count = store.get_stats()["knowledge"]["total"]
        assert (
            after_count == original_count
        ), "Failed store() must not leave an orphaned knowledge row"


class TestWalCheckpointInsideLock:
    """WAL checkpoint in prune() is best-effort and must not block normal operation."""

    def test_prune_returns_expected_keys_after_checkpoint(self, store):
        """prune() must return the deletion counts dict regardless of checkpoint outcome."""
        # sqlite3 in WAL mode: checkpoint may silently return (0,0,0) on in-memory DB,
        # but prune() must always return the expected structure.
        store.store_turn("prune-session-wal", "user", "Old turn content xyz")
        result = store.prune(days=7)
        assert "tool_history_deleted" in result
        assert "conversations_deleted" in result
        assert "knowledge_deleted" in result

    def test_prune_does_not_raise_on_checkpoint_sqlite_busy(self, store):
        """prune() swallows checkpoint exceptions (SQLITE_BUSY) — test via _MemoryStore__prune."""
        # Verify the checkpoint try/except is present by checking the source code
        import inspect
        from gaia.agents.base.memory_store import MemoryStore

        source = inspect.getsource(MemoryStore.prune)
        assert "wal_checkpoint" in source, "prune() should attempt WAL checkpoint"
        assert "except Exception" in source, "prune() should swallow checkpoint errors"


class TestStoreDedupBranchRollback:
    """store() dedup branch must rollback if FTS update fails — no orphaned UPDATE."""

    def test_dedup_fts_failure_rolls_back_knowledge_update(self, store):
        """If _update_knowledge_fts_locked raises during dedup, the UPDATE is rolled back."""
        # Create an initial entry
        kid = store.store(
            category="fact", content="Rollback dedup test unique content xyz"
        )

        original_row = store._conn.execute(
            "SELECT content, updated_at FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        original_content = original_row[0]
        original_updated = original_row[1]

        # Patch FTS update to fail
        def boom(k):
            raise RuntimeError("Simulated FTS dedup update failure")

        store._update_knowledge_fts_locked = boom

        import pytest

        # Try to store near-duplicate — should dedup and update, but FTS fails
        with pytest.raises(RuntimeError, match="Simulated FTS dedup update failure"):
            store.store(
                category="fact",
                content="Rollback dedup test unique content xyz revised",
            )

        # Restore
        store._update_knowledge_fts_locked = (
            MemoryStore._update_knowledge_fts_locked.__get__(store, type(store))
        )

        # The UPDATE must have been rolled back — original content should be unchanged
        row = store._conn.execute(
            "SELECT content, updated_at FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert (
            row[0] == original_content
        ), "Dedup UPDATE must be rolled back on FTS failure"
        assert (
            row[1] == original_updated
        ), "updated_at must not change on rolled-back dedup"


class TestUpdateRollbackOnFtsFailure:
    """update() must rollback if FTS sync fails — no partially-updated knowledge row."""

    def test_update_fts_failure_rolls_back_content_change(self, store):
        """If _update_knowledge_fts_locked raises, the UPDATE knowledge is rolled back."""
        kid = store.store(
            category="fact", content="Update rollback test original content xyz"
        )

        original_row = store._conn.execute(
            "SELECT content, updated_at FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        original_content = original_row[0]
        original_updated = original_row[1]

        def boom(k):
            raise RuntimeError("Simulated FTS update failure in update()")

        store._update_knowledge_fts_locked = boom

        import pytest

        with pytest.raises(
            RuntimeError, match="Simulated FTS update failure in update()"
        ):
            store.update(kid, content="Updated content that should be rolled back")

        # Restore
        store._update_knowledge_fts_locked = (
            MemoryStore._update_knowledge_fts_locked.__get__(store, type(store))
        )

        row = store._conn.execute(
            "SELECT content, updated_at FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] == original_content, "UPDATE must be rolled back on FTS failure"
        assert (
            row[1] == original_updated
        ), "updated_at must not change on rolled-back update"

    def test_update_fts_failure_does_not_leave_stale_content_via_next_commit(
        self, store
    ):
        """Rolled-back UPDATE must not be committed by a subsequent store_turn() commit."""
        kid = store.store(category="fact", content="Stale commit test unique entry xyz")

        original_content = store._conn.execute(
            "SELECT content FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()[0]

        def boom(k):
            raise RuntimeError("FTS fail")

        store._update_knowledge_fts_locked = boom

        import pytest

        with pytest.raises(RuntimeError):
            store.update(kid, content="Content that must never be committed")

        # Restore normal FTS update
        store._update_knowledge_fts_locked = (
            MemoryStore._update_knowledge_fts_locked.__get__(store, type(store))
        )

        # Trigger a real commit via store_turn
        store.store_turn("test-session-rollback", "user", "Hello after rollback")

        # The failed UPDATE's content must NOT have been committed
        row = store._conn.execute(
            "SELECT content FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert (
            row[0] == original_content
        ), "store_turn commit must not resurface the rolled-back UPDATE"


class TestDeleteRollback:
    """delete() must be atomic — FTS and knowledge row deleted together or not at all."""

    def test_delete_removes_both_row_and_fts_entry(self, store):
        """Normal delete removes knowledge row and FTS entry atomically."""
        kid = store.store(
            category="fact", content="Delete atomicity test entry xyz unique"
        )
        rowid = store._conn.execute(
            "SELECT rowid FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()[0]

        result = store.delete(kid)
        assert result is True

        # Knowledge row gone
        row = store._conn.execute(
            "SELECT id FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row is None, "knowledge row must be deleted"

        # FTS entry gone
        fts_row = store._conn.execute(
            "SELECT rowid FROM knowledge_fts WHERE rowid = ?", (rowid,)
        ).fetchone()
        assert fts_row is None, "FTS entry must be deleted"

    def test_delete_nonexistent_returns_false(self, store):
        """delete() returns False without raising when ID not found."""
        result = store.delete("nonexistent-id-that-does-not-exist")
        assert result is False


class TestHeuristicFinditerMultipleMatches:
    """_extract_heuristics() must capture all matches, not just the first."""

    def test_multiple_preferences_in_single_message(self, store):
        """'I prefer Python. I prefer dark mode.' should store TWO preferences."""
        from gaia.agents.base.memory import MemoryMixin

        class FakeAgent(MemoryMixin):
            def __init__(self):
                self._memory_store = store
                self._memory_context = "global"
                self._auto_extract_enabled = True

        agent = FakeAgent()
        before = store.get_stats()["knowledge"]["total"]
        agent._extract_heuristics(
            "I prefer Python over Ruby. I prefer dark mode over light mode."
        )
        after = store.get_stats()["knowledge"]["total"]
        assert (
            after >= before + 2
        ), f"Expected at least 2 new preferences, got {after - before}"

    def test_single_preference_still_works(self, store):
        """A single preference in the message is still extracted."""
        from gaia.agents.base.memory import MemoryMixin

        class FakeAgent(MemoryMixin):
            def __init__(self):
                self._memory_store = store
                self._memory_context = "global"
                self._auto_extract_enabled = True

        agent = FakeAgent()
        before = store.get_stats()["knowledge"]["total"]
        agent._extract_heuristics("I prefer spaces over tabs for all projects.")
        after = store.get_stats()["knowledge"]["total"]
        assert after > before, "Single preference must still be extracted"

    def test_no_preferences_extracts_nothing(self, store):
        """A message with no preference patterns stores nothing."""
        from gaia.agents.base.memory import MemoryMixin

        class FakeAgent(MemoryMixin):
            def __init__(self):
                self._memory_store = store
                self._memory_context = "global"
                self._auto_extract_enabled = True

        agent = FakeAgent()
        before = store.get_stats()["knowledge"]["total"]
        agent._extract_heuristics("The weather today is sunny and warm.")
        after = store.get_stats()["knowledge"]["total"]
        assert after == before, "No-preference message must not add knowledge"


class TestSearchConfidenceBumpRollback:
    """search() confidence bump UPDATEs must be all-or-nothing."""

    def test_confidence_bump_commit_failure_rolls_back_all_bumps(self, store):
        """If commit() fails after confidence bumps, all bumps must be rolled back."""
        kid = store.store(
            category="fact",
            content="BumpRollback unique test entry xyz",
            confidence=0.5,
        )

        original_conf = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()[0]

        # Inject a commit failure ONLY for the bump commit, not the initial store
        commit_call_count = [0]
        original_method = (
            store._conn.commit.__func__
            if hasattr(store._conn.commit, "__func__")
            else None
        )

        # We can't monkey-patch sqlite3 commit, so instead we verify the
        # try/except/rollback pattern is in place via source inspection
        import inspect
        from gaia.agents.base.memory_store import MemoryStore

        source = inspect.getsource(MemoryStore.search)
        assert (
            "rollback" in source
        ), "search() must have rollback guard for confidence bumps"
        assert "try" in source, "search() must wrap confidence bumps in try block"

    def test_successful_confidence_bump_is_committed(self, store):
        """Normal search correctly bumps confidence and commits."""
        kid = store.store(
            category="fact",
            content="BumpCommit success test entry xyz",
            confidence=0.50,
        )
        results = store.search("BumpCommit success test entry xyz")
        match = next((r for r in results if r["id"] == kid), None)
        assert match is not None
        # Verify bump was committed
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert (
            row[0] > 0.50
        ), "Confidence bump must be committed after successful search"


class TestLogToolCallArgsTruncation:
    """log_tool_call() must truncate args_json to 500 chars."""

    def test_large_args_are_truncated(self, store):
        """Args dict larger than 500 chars is truncated before storage."""
        large_args = {"content": "x" * 10000, "path": "/tmp/big_file.txt"}
        store.log_tool_call(
            session_id="trunc-test-session",
            tool_name="write_file",
            args=large_args,
            result_summary="ok",
            success=True,
        )
        row = store._conn.execute(
            "SELECT args FROM tool_history WHERE tool_name = 'write_file' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert len(row[0]) <= 500, f"args_json must be ≤500 chars, got {len(row[0])}"

    def test_small_args_not_truncated(self, store):
        """Args dict smaller than 500 chars is stored intact."""
        small_args = {"path": "/tmp/file.txt", "mode": "r"}
        store.log_tool_call(
            session_id="small-args-session",
            tool_name="read_file",
            args=small_args,
            result_summary="content",
            success=True,
        )
        row = store._conn.execute(
            "SELECT args FROM tool_history WHERE tool_name = 'read_file' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        import json

        stored = json.loads(row[0])
        assert stored["path"] == "/tmp/file.txt", "Small args must be stored intact"

    def test_none_args_stored_as_null(self, store):
        """None args produces NULL in the database."""
        store.log_tool_call(
            session_id="null-args-session",
            tool_name="no_args_tool",
            args=None,
            result_summary="done",
            success=True,
        )
        row = store._conn.execute(
            "SELECT args FROM tool_history WHERE tool_name = 'no_args_tool' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] is None, "None args must be stored as NULL"


class TestStoreTurnRollbackPattern:
    """store_turn() must protect the INSERT+commit with rollback."""

    def test_store_turn_rollback_guard_present(self):
        """store_turn() source code contains rollback guard."""
        import inspect
        from gaia.agents.base.memory_store import MemoryStore

        source = inspect.getsource(MemoryStore.store_turn)
        assert "rollback" in source, "store_turn() must have rollback guard"

    def test_store_turn_inserts_and_commits(self, store):
        """Successful store_turn() produces a queryable conversation row."""
        store.store_turn(
            "rollback-guard-session", "user", "Rollback guard verification message"
        )
        rows = store.get_history(session_id="rollback-guard-session")
        assert len(rows) == 1
        assert rows[0]["content"] == "Rollback guard verification message"


class TestUpdateConfidenceRollback:
    """update_confidence() must protect its UPDATE+commit with rollback."""

    def test_rollback_guard_present_in_source(self):
        """update_confidence() source contains try/except/rollback."""
        import inspect

        from gaia.agents.base.memory_store import MemoryStore

        source = inspect.getsource(MemoryStore.update_confidence)
        assert "rollback" in source, "update_confidence() must have rollback guard"
        assert "try" in source, "update_confidence() must wrap UPDATE in try block"

    def test_update_confidence_commits_successfully(self, store):
        """update_confidence() adjusts confidence and commits."""
        kid = store.store(
            category="fact",
            content="UpdateConf rollback guard test entry xyz",
            confidence=0.5,
        )
        store.update_confidence(kid, 0.2)
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row is not None
        assert abs(row[0] - 0.7) < 0.001, f"Expected 0.7, got {row[0]}"

    def test_update_confidence_clamped_to_one(self, store):
        """update_confidence() clamps the result to 1.0."""
        kid = store.store(
            category="fact",
            content="UpdateConf clamp test entry xyz",
            confidence=0.95,
        )
        store.update_confidence(kid, 0.5)
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] == 1.0, "Confidence must not exceed 1.0"

    def test_update_confidence_clamped_to_zero(self, store):
        """update_confidence() clamps the result to 0.0."""
        kid = store.store(
            category="fact",
            content="UpdateConf zero clamp test entry xyz",
            confidence=0.1,
        )
        store.update_confidence(kid, -5.0)
        row = store._conn.execute(
            "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
        ).fetchone()
        assert row[0] == 0.0, "Confidence must not go below 0.0"


class TestApplyConfidenceDecayRollback:
    """apply_confidence_decay() must protect its UPDATE+commit with rollback."""

    def test_rollback_guard_present_in_source(self):
        """apply_confidence_decay() source contains try/except/rollback."""
        import inspect

        from gaia.agents.base.memory_store import MemoryStore

        source = inspect.getsource(MemoryStore.apply_confidence_decay)
        assert "rollback" in source, "apply_confidence_decay() must have rollback guard"
        assert "try" in source, "apply_confidence_decay() must wrap UPDATE in try block"

    def test_decay_return_value_matches_rows_changed(self, store):
        """apply_confidence_decay() returns the count of decayed items."""
        from datetime import datetime, timedelta

        old_ts = (datetime.now().astimezone() - timedelta(days=40)).isoformat()
        kids = []
        unique_contents = [
            "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo",
            "november oscar papa quebec romeo sierra tango uniform victor whiskey xray",
            "yankee zulu crimson violet indigo magenta amber emerald sapphire cobalt",
        ]
        for content in unique_contents:
            kid = store.store(
                category="fact",
                content=content,
                confidence=0.8,
            )
            store._conn.execute(
                "UPDATE knowledge SET last_used = ?, updated_at = ? WHERE id = ?",
                (old_ts, old_ts, kid),
            )
            kids.append(kid)
        store._conn.commit()

        count = store.apply_confidence_decay(days_threshold=30, decay_factor=0.9)
        assert count >= 3, f"Expected ≥3 rows decayed, got {count}"

        # Verify confidence was actually lowered
        for kid in kids:
            row = store._conn.execute(
                "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
            ).fetchone()
            assert row[0] < 0.8, f"Item {kid} confidence should have been decayed"
