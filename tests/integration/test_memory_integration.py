# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration tests for Memory v2 full pipeline.

Tests end-to-end storage, retrieval, embedding storage/retrieval,
deduplication, confidence scoring, temporal filtering, context isolation,
sensitivity, supersession, conversations, tool history, pruning, schema
migration, and concurrent access — all with REAL SQLite + REAL numpy
but MOCKED LLM (deterministic fake embeddings).

These tests verify that the full data pipeline works correctly when all
components are wired together, unlike unit tests that test each method
in isolation.
"""

import threading
import time
import uuid
from datetime import datetime, timedelta

import numpy as np
import pytest

from gaia.agents.base.memory_store import (
    CONFIDENCE_BUMP_PER_RECALL,
    MemoryStore,
)

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
def memory_store(tmp_path):
    """Real SQLite MemoryStore with v2 schema in a temp directory."""
    store = MemoryStore(db_path=tmp_path / "test_memory.db")
    yield store
    store.close()


@pytest.fixture
def fake_embedder():
    """Returns a function that generates deterministic 768-dim embeddings.

    Uses a seeded RNG based on text hash so the same text always produces
    the same embedding. Vectors are L2-normalized for cosine similarity.
    """

    def embed(text: str) -> np.ndarray:
        rng = np.random.RandomState(hash(text) % 2**32)
        vec = rng.randn(768).astype(np.float32)
        vec /= np.linalg.norm(vec)  # L2 normalize for cosine similarity
        return vec

    return embed


def _embedding_to_blob(vec: np.ndarray) -> bytes:
    """Convert a numpy float32 vector to raw bytes for SQLite BLOB storage."""
    return vec.tobytes()


def _blob_to_embedding(blob: bytes) -> np.ndarray:
    """Convert raw bytes back to a numpy float32 vector."""
    return np.frombuffer(blob, dtype=np.float32).copy()


# ---------------------------------------------------------------------------
# Test data: diverse knowledge items for multi-scenario testing
# ---------------------------------------------------------------------------


def _make_test_items():
    """Build test items with fresh timestamps (avoids stale module-level dates)."""
    return [
        {
            "category": "fact",
            "content": "User works at AMD on the GAIA project using Python 3.12",
            "context": "work",
            "domain": "employment",
            "entity": "project:gaia",
        },
        {
            "category": "fact",
            "content": "GAIA supports NPU acceleration on Ryzen AI processors",
            "context": "work",
            "domain": "hardware",
            "entity": "project:gaia",
        },
        {
            "category": "preference",
            "content": "User prefers dark mode and 4-space indentation in VSCode",
            "context": "work",
            "domain": "editor",
            "entity": "app:vscode",
        },
        {
            "category": "skill",
            "content": "To deploy GAIA: run tests, build wheel, push to staging, verify, promote",
            "context": "work",
            "domain": "deployment",
            "entity": "project:gaia",
        },
        {
            "category": "error",
            "content": "pip install torch fails without --index-url on AMD hardware",
            "context": "work",
            "domain": "deployment",
        },
        {
            "category": "note",
            "content": "Standup 2026-04-01: API migration to v2 complete, memory system next",
            "context": "work",
            "domain": "meeting:standup",
        },
        {
            "category": "fact",
            "content": "User has a golden retriever named Max who loves hiking",
            "context": "personal",
        },
        {
            "category": "reminder",
            "content": "Q2 performance review due April 15",
            "context": "work",
            "due_at": _future_iso(14),
        },
        {
            "category": "fact",
            "content": "Sarah Chen is VP of Engineering, email sarah@amd.com",
            "context": "work",
            "entity": "person:sarah_chen",
        },
        {
            "category": "preference",
            "content": "User prefers morning workouts before 7am at the gym",
            "context": "personal",
            "domain": "fitness",
        },
    ]


def _populate_store(store, items=None, embedder=None):
    """Store items and optionally attach embeddings. Returns list of IDs."""
    if items is None:
        items = _make_test_items()
    ids = []
    for item in items:
        kwargs = {
            "category": item["category"],
            "content": item["content"],
            "context": item.get("context", "global"),
        }
        if item.get("domain"):
            kwargs["domain"] = item["domain"]
        if item.get("entity"):
            kwargs["entity"] = item["entity"]
        if item.get("due_at"):
            kwargs["due_at"] = item["due_at"]
        if item.get("sensitive"):
            kwargs["sensitive"] = item["sensitive"]
        if item.get("source"):
            kwargs["source"] = item["source"]
        if "confidence" in item:
            kwargs["confidence"] = item["confidence"]

        kid = store.store(**kwargs)
        ids.append(kid)

        if embedder:
            vec = embedder(item["content"])
            store.store_embedding(kid, _embedding_to_blob(vec))

    return ids


# ===========================================================================
# 1. Full Storage and Retrieval
# ===========================================================================


class TestFullStorageAndRetrieval:
    """End-to-end: store items with embeddings, search via FTS5."""

    def test_store_embed_search_roundtrip(self, memory_store, fake_embedder):
        """Store 10 items with embeddings, verify FTS5 search returns relevant results."""
        ids = _populate_store(memory_store, embedder=fake_embedder)
        assert len(ids) == 10

        # FTS5 search — should find NPU-related item
        results = memory_store.search("NPU acceleration Ryzen")
        assert len(results) >= 1
        contents = [r["content"] for r in results]
        assert any("NPU acceleration" in c for c in contents)

    def test_store_embed_verify_blobs(self, memory_store, fake_embedder):
        """Store items with embeddings, verify embedding BLOBs are retrievable."""
        ids = _populate_store(memory_store, embedder=fake_embedder)

        items_with_emb = memory_store.get_items_with_embeddings()
        assert len(items_with_emb) == 10

        for item in items_with_emb:
            assert item["embedding"] is not None
            vec = _blob_to_embedding(item["embedding"])
            assert vec.shape == (768,)
            # Verify L2 normalized (norm ≈ 1.0)
            assert abs(np.linalg.norm(vec) - 1.0) < 1e-5

    def test_superseded_items_excluded_from_search(self, memory_store, fake_embedder):
        """Store item A, supersede with item B, verify only B found in search."""
        # Store original fact
        old_id = memory_store.store(
            category="fact",
            content="Project uses React 18 with class components",
            context="work",
        )
        fake_vec = fake_embedder("Project uses React 18 with class components")
        memory_store.store_embedding(old_id, _embedding_to_blob(fake_vec))

        # Store newer version
        new_id = memory_store.store(
            category="fact",
            content="Project migrated to React 19 with server components",
            context="work",
        )
        new_vec = fake_embedder("Project migrated to React 19 with server components")
        memory_store.store_embedding(new_id, _embedding_to_blob(new_vec))

        # Mark old as superseded
        memory_store.update(old_id, superseded_by=new_id)

        # FTS5 search — only the new version should appear
        results = memory_store.search("React")
        assert len(results) >= 1
        result_ids = [r["id"] for r in results]
        assert new_id in result_ids
        assert old_id not in result_ids

        # Verify the old item still exists with superseded_by set
        # (check via get_all_knowledge with include_superseded)
        all_items = memory_store.get_all_knowledge(
            search="React", include_superseded=True
        )
        all_ids = [item["id"] for item in all_items["items"]]
        assert old_id in all_ids
        superseded_item = next(i for i in all_items["items"] if i["id"] == old_id)
        assert superseded_item["superseded_by"] == new_id

    def test_temporal_filtering_with_search(self, memory_store, fake_embedder):
        """Store items at different timestamps, verify time_from/time_to filtering."""
        # Store items and note the time window
        time_before = _now_iso()
        time.sleep(0.05)  # Small gap to ensure distinct timestamps

        id1 = memory_store.store(
            category="note",
            content="Meeting notes from Monday morning standup team sync",
            context="work",
        )

        time.sleep(0.05)
        time_middle = _now_iso()
        time.sleep(0.05)

        id2 = memory_store.store(
            category="note",
            content="Meeting notes from Wednesday afternoon architecture review",
            context="work",
        )

        time_after = _now_iso()

        # Search with time_from only — should find both
        results = memory_store.search("Meeting notes", time_from=time_before)
        result_ids = [r["id"] for r in results]
        assert id1 in result_ids
        assert id2 in result_ids

        # Search with time range that excludes first item
        results = memory_store.search("Meeting notes", time_from=time_middle)
        result_ids = [r["id"] for r in results]
        assert id1 not in result_ids
        assert id2 in result_ids

        # Search with time_to before second item
        results = memory_store.search("Meeting notes", time_to=time_middle)
        result_ids = [r["id"] for r in results]
        assert id1 in result_ids
        assert id2 not in result_ids

    def test_context_isolation_in_search(self, memory_store, fake_embedder):
        """Store items in work/personal contexts, verify search respects context."""
        _populate_store(memory_store, embedder=fake_embedder)

        # Search only work context
        work_results = memory_store.search("prefers", context="work")
        for r in work_results:
            assert r["context"] == "work"

        # Search only personal context
        personal_results = memory_store.search("prefers", context="personal")
        for r in personal_results:
            assert r["context"] == "personal"

        # Verify different items returned
        work_ids = {r["id"] for r in work_results}
        personal_ids = {r["id"] for r in personal_results}
        assert work_ids.isdisjoint(personal_ids)

    def test_sensitive_items_excluded_from_default_search(
        self, memory_store, fake_embedder
    ):
        """Store sensitive + non-sensitive items, verify filtering."""
        # Store a non-sensitive fact
        public_id = memory_store.store(
            category="fact",
            content="The company headquarters is in Santa Clara California",
            context="work",
        )

        # Store a sensitive fact
        secret_id = memory_store.store(
            category="fact",
            content="The company API secret key is sk-abc123xyz",
            context="work",
            sensitive=True,
        )

        # Default search excludes sensitive
        results = memory_store.search("company")
        result_ids = [r["id"] for r in results]
        assert public_id in result_ids
        assert secret_id not in result_ids

        # Explicit include_sensitive shows both
        results = memory_store.search("company", include_sensitive=True)
        result_ids = [r["id"] for r in results]
        assert public_id in result_ids
        assert secret_id in result_ids

    def test_confidence_and_use_count_updated_on_search(
        self, memory_store, fake_embedder
    ):
        """Verify confidence bumps +0.02 and use_count increments on each recall."""
        kid = memory_store.store(
            category="fact",
            content="Python 3.12 supports improved error messages and perf",
            context="work",
            confidence=0.5,
        )

        # Get initial state
        initial = memory_store.get_by_category("fact", context="work")
        item = next(i for i in initial if i["id"] == kid)
        initial_confidence = item["confidence"]
        initial_use_count = item["use_count"]

        # Search triggers confidence bump
        results = memory_store.search("Python error messages")
        assert len(results) >= 1

        # Check the item was bumped
        bumped_item = next((r for r in results if r["id"] == kid), None)
        assert bumped_item is not None
        assert bumped_item["confidence"] == pytest.approx(
            initial_confidence + CONFIDENCE_BUMP_PER_RECALL, abs=1e-6
        )

        # Search again — should bump again
        results2 = memory_store.search("Python error messages")
        bumped2 = next((r for r in results2 if r["id"] == kid), None)
        assert bumped2 is not None
        assert bumped2["confidence"] == pytest.approx(
            initial_confidence + 2 * CONFIDENCE_BUMP_PER_RECALL, abs=1e-6
        )

        # Verify use_count via direct query
        after = memory_store.get_by_category("fact", context="work")
        item_after = next(i for i in after if i["id"] == kid)
        assert item_after["use_count"] == initial_use_count + 2

    def test_dedup_with_embeddings(self, memory_store, fake_embedder):
        """Store similar content (>80% overlap), verify dedup and embedding update."""
        # Store initial item with embedding
        id1 = memory_store.store(
            category="fact",
            content="GAIA supports local LLM inference on AMD Ryzen AI hardware",
            context="work",
        )
        vec1 = fake_embedder(
            "GAIA supports local LLM inference on AMD Ryzen AI hardware"
        )
        memory_store.store_embedding(id1, _embedding_to_blob(vec1))

        # Store very similar content — should dedup (>80% overlap)
        id2 = memory_store.store(
            category="fact",
            content="GAIA supports local LLM inference on AMD Ryzen AI processors",
            context="work",
        )

        # Should return same ID (deduped)
        assert id2 == id1

        # Store new embedding for the updated content
        vec2 = fake_embedder(
            "GAIA supports local LLM inference on AMD Ryzen AI processors"
        )
        memory_store.store_embedding(id2, _embedding_to_blob(vec2))

        # Verify only one item exists
        items = memory_store.get_items_with_embeddings(context="work")
        gaia_items = [i for i in items if "GAIA supports" in i["content"]]
        assert len(gaia_items) == 1
        assert "processors" in gaia_items[0]["content"]  # Newer content wins

    def test_entity_search_with_embeddings(self, memory_store, fake_embedder):
        """Store items with entities, verify entity-scoped search works."""
        ids = _populate_store(memory_store, embedder=fake_embedder)

        # Search by entity
        sarah_items = memory_store.get_by_entity("person:sarah_chen")
        assert len(sarah_items) >= 1
        assert all("sarah" in i["content"].lower() for i in sarah_items)

        # Search by project entity
        gaia_items = memory_store.get_by_entity("project:gaia")
        assert len(gaia_items) >= 2  # Multiple GAIA-related items

        # FTS search scoped to entity
        results = memory_store.search("engineering", entity="person:sarah_chen")
        assert len(results) >= 1
        assert all(r["entity"] == "person:sarah_chen" for r in results)


# ===========================================================================
# 2. Embedding Storage Lifecycle
# ===========================================================================


class TestEmbeddingStorageLifecycle:
    """Test embedding storage, retrieval, and backfill scenarios."""

    def test_store_and_retrieve_embeddings(self, memory_store, fake_embedder):
        """Store embeddings via store_embedding(), retrieve via get_items_with_embeddings()."""
        kid = memory_store.store(
            category="fact",
            content="Test embedding storage and retrieval pipeline",
        )

        vec = fake_embedder("Test embedding storage and retrieval pipeline")
        blob = _embedding_to_blob(vec)
        assert memory_store.store_embedding(kid, blob) is True

        items = memory_store.get_items_with_embeddings()
        assert len(items) == 1
        assert items[0]["id"] == kid
        retrieved_vec = _blob_to_embedding(items[0]["embedding"])
        np.testing.assert_array_almost_equal(vec, retrieved_vec)

    def test_items_without_embeddings(self, memory_store, fake_embedder):
        """Verify get_items_without_embeddings() returns unembed items."""
        # Store items without embeddings
        id1 = memory_store.store(category="fact", content="Item one no embedding")
        id2 = memory_store.store(category="fact", content="Item two no embedding")
        id3 = memory_store.store(category="fact", content="Item three has embedding")

        # Embed only the third
        vec = fake_embedder("Item three has embedding")
        memory_store.store_embedding(id3, _embedding_to_blob(vec))

        without = memory_store.get_items_without_embeddings()
        without_ids = [i["id"] for i in without]
        assert id1 in without_ids
        assert id2 in without_ids
        assert id3 not in without_ids

    def test_embedding_replacement(self, memory_store, fake_embedder):
        """Replacing an embedding overwrites the old one."""
        kid = memory_store.store(category="fact", content="Embedding replacement test")

        vec1 = fake_embedder("first version")
        memory_store.store_embedding(kid, _embedding_to_blob(vec1))

        vec2 = fake_embedder("second version")
        memory_store.store_embedding(kid, _embedding_to_blob(vec2))

        items = memory_store.get_items_with_embeddings()
        assert len(items) == 1
        retrieved = _blob_to_embedding(items[0]["embedding"])
        np.testing.assert_array_almost_equal(vec2, retrieved)
        # Ensure it's NOT the old embedding
        assert not np.allclose(vec1, retrieved)

    def test_store_embedding_nonexistent_id(self, memory_store):
        """store_embedding() returns False for nonexistent knowledge_id."""
        fake_blob = b"\x00" * (768 * 4)
        assert memory_store.store_embedding("nonexistent-id", fake_blob) is False

    def test_embedding_filters_by_category_and_context(
        self, memory_store, fake_embedder
    ):
        """get_items_with_embeddings() respects category/context filters."""
        items = [
            {
                "category": "fact",
                "content": "Work fact one for filtering",
                "context": "work",
            },
            {
                "category": "fact",
                "content": "Personal fact two for filtering",
                "context": "personal",
            },
            {
                "category": "skill",
                "content": "Work skill three for filtering",
                "context": "work",
            },
        ]
        ids = _populate_store(memory_store, items=items, embedder=fake_embedder)

        # Filter by category
        facts = memory_store.get_items_with_embeddings(category="fact")
        assert len(facts) == 2
        assert all(f["category"] == "fact" for f in facts)

        # Filter by context
        work_items = memory_store.get_items_with_embeddings(context="work")
        assert len(work_items) == 2
        assert all(w["context"] == "work" for w in work_items)

        # Filter by both
        work_facts = memory_store.get_items_with_embeddings(
            category="fact", context="work"
        )
        assert len(work_facts) == 1

    def test_superseded_items_excluded_from_embeddings(
        self, memory_store, fake_embedder
    ):
        """get_items_with_embeddings() excludes superseded items."""
        old_id = memory_store.store(
            category="fact", content="Old fact before supersession"
        )
        memory_store.store_embedding(
            old_id, _embedding_to_blob(fake_embedder("Old fact before supersession"))
        )

        new_id = memory_store.store(
            category="fact", content="New fact after supersession"
        )
        memory_store.store_embedding(
            new_id, _embedding_to_blob(fake_embedder("New fact after supersession"))
        )

        # Before supersession: both visible
        items = memory_store.get_items_with_embeddings()
        assert len(items) == 2

        # Supersede old
        memory_store.update(old_id, superseded_by=new_id)

        # After supersession: only new visible
        items = memory_store.get_items_with_embeddings()
        assert len(items) == 1
        assert items[0]["id"] == new_id


# ===========================================================================
# 3. Deduplication Pipeline
# ===========================================================================


class TestDeduplicationPipeline:
    """Test dedup across different scopes (category, context, entity)."""

    def test_dedup_same_category_context(self, memory_store):
        """Items with >80% overlap in same category+context are deduped."""
        id1 = memory_store.store(
            category="fact",
            content="The project uses Python 3.12 with uv package manager",
            context="work",
        )
        # Very similar content
        id2 = memory_store.store(
            category="fact",
            content="The project uses Python 3.12 with uv package manager tooling",
            context="work",
        )
        assert id2 == id1  # Should dedup

    def test_no_dedup_different_category(self, memory_store):
        """Same content in different categories should NOT dedup."""
        id1 = memory_store.store(
            category="fact",
            content="Deploy workflow: test build push verify promote",
            context="work",
        )
        id2 = memory_store.store(
            category="skill",
            content="Deploy workflow: test build push verify promote",
            context="work",
        )
        assert id2 != id1  # Different categories — no dedup

    def test_no_dedup_different_context(self, memory_store):
        """Same content in different contexts should NOT dedup."""
        id1 = memory_store.store(
            category="fact",
            content="Uses Python 3.12 with type hints everywhere",
            context="work",
        )
        id2 = memory_store.store(
            category="fact",
            content="Uses Python 3.12 with type hints everywhere",
            context="personal",
        )
        assert id2 != id1

    def test_no_dedup_different_entity(self, memory_store):
        """Same content with different entities should NOT dedup."""
        id1 = memory_store.store(
            category="fact",
            content="Prefers morning meetings before standup",
            context="work",
            entity="person:sarah_chen",
        )
        id2 = memory_store.store(
            category="fact",
            content="Prefers morning meetings before standup",
            context="work",
            entity="person:john_doe",
        )
        assert id2 != id1

    def test_dedup_updates_content_to_newer(self, memory_store):
        """Dedup replaces content with the newer version."""
        id1 = memory_store.store(
            category="fact",
            content="The team uses React 18 for frontend development",
            context="work",
        )
        id2 = memory_store.store(
            category="fact",
            content="The team uses React 19 for frontend development",
            context="work",
        )
        assert id2 == id1

        # Verify content was updated to newer version
        results = memory_store.search("React frontend development")
        match = next(r for r in results if r["id"] == id1)
        assert "React 19" in match["content"]

    def test_dedup_takes_max_confidence(self, memory_store):
        """Dedup takes the max confidence between old and new."""
        id1 = memory_store.store(
            category="fact",
            content="Python is the primary language for GAIA development",
            context="work",
            confidence=0.8,
        )
        id2 = memory_store.store(
            category="fact",
            content="Python is the primary language for GAIA development work",
            context="work",
            confidence=0.3,
        )
        assert id2 == id1

        # Confidence should still be 0.8 (max of 0.8 and 0.3)
        results = memory_store.search("Python primary language GAIA")
        match = next(r for r in results if r["id"] == id1)
        # After search bump: 0.8 + 0.02
        assert match["confidence"] == pytest.approx(0.82, abs=0.01)

    def test_low_overlap_not_deduped(self, memory_store):
        """Items with <80% word overlap are NOT deduped."""
        id1 = memory_store.store(
            category="fact",
            content="GAIA uses SQLite for persistent storage of agent memory",
            context="work",
        )
        id2 = memory_store.store(
            category="fact",
            content="The weather forecast for Tuesday shows rain and cold temps",
            context="work",
        )
        assert id2 != id1  # Very different content — no dedup


# ===========================================================================
# 4. Confidence Lifecycle
# ===========================================================================


class TestConfidenceLifecycle:
    """Test confidence decay, bumping, and pruning interactions."""

    def test_confidence_decay_after_30_days(self, memory_store):
        """Items not used for 30+ days have confidence decayed by 0.9x."""
        kid = memory_store.store(
            category="fact",
            content="Stale fact that nobody has accessed recently at all",
            context="work",
            confidence=0.5,
        )

        # Manually set last_used and updated_at to 35 days ago
        old_ts = _past_iso(35)
        memory_store._execute(
            "UPDATE knowledge SET last_used = ?, updated_at = ? WHERE id = ?",
            (old_ts, old_ts, kid),
        )

        # Apply decay
        decayed = memory_store.apply_confidence_decay(days_threshold=30)
        assert decayed >= 1

        # Verify confidence was decayed
        items = memory_store.get_by_category("fact", context="work")
        item = next(i for i in items if i["id"] == kid)
        assert item["confidence"] == pytest.approx(0.5 * 0.9, abs=1e-6)

    def test_no_decay_for_recently_used_items(self, memory_store):
        """Items used within 30 days should NOT be decayed."""
        kid = memory_store.store(
            category="fact",
            content="Recently used fact that should keep its confidence",
            context="work",
            confidence=0.7,
        )

        # last_used is set to now by store() — should not decay
        decayed = memory_store.apply_confidence_decay(days_threshold=30)

        items = memory_store.get_by_category("fact", context="work")
        item = next(i for i in items if i["id"] == kid)
        assert item["confidence"] == pytest.approx(0.7, abs=1e-6)

    def test_no_decay_runaway_on_rapid_restart(self, memory_store):
        """Decay checks BOTH last_used AND updated_at to prevent runaway decay.

        If updated_at was set to now by the first decay, the second decay
        should NOT re-decay the item (because updated_at > cutoff).
        """
        kid = memory_store.store(
            category="fact",
            content="Item that should only decay once not twice rapidly",
            context="work",
            confidence=0.5,
        )

        # Set last_used to 35 days ago, but updated_at stays recent
        old_ts = _past_iso(35)
        memory_store._execute(
            "UPDATE knowledge SET last_used = ? WHERE id = ?",
            (old_ts, kid),
        )

        # First decay: should work (both last_used and updated_at are old enough
        # only if updated_at is also old)
        # Actually the store() just set updated_at to now, so decay should NOT
        # trigger because updated_at is recent
        decayed = memory_store.apply_confidence_decay(days_threshold=30)

        items = memory_store.get_by_category("fact", context="work")
        item = next(i for i in items if i["id"] == kid)
        # updated_at is recent (set by store()), so no decay despite old last_used
        assert item["confidence"] == pytest.approx(0.5, abs=1e-6)

    def test_confidence_bump_capped_at_1(self, memory_store):
        """Confidence should never exceed 1.0 even with repeated bumps."""
        kid = memory_store.store(
            category="fact",
            content="High confidence fact that gets searched many times repeatedly",
            context="work",
            confidence=0.98,
        )

        # Search 5 times — each bumps +0.02
        for _ in range(5):
            memory_store.search("High confidence fact searched")

        items = memory_store.get_by_category("fact", context="work")
        item = next(i for i in items if i["id"] == kid)
        assert item["confidence"] <= 1.0
        assert item["confidence"] == pytest.approx(1.0, abs=1e-6)

    def test_prune_removes_low_confidence_old_items(self, memory_store):
        """Prune deletes knowledge items with confidence < 0.1 that are old."""
        kid = memory_store.store(
            category="fact",
            content="Very low confidence fact that should be pruned",
            context="work",
            confidence=0.05,
        )

        # Set last_used to 100 days ago
        old_ts = _past_iso(100)
        memory_store._execute(
            "UPDATE knowledge SET last_used = ?, updated_at = ?, confidence = 0.05 WHERE id = ?",
            (old_ts, old_ts, kid),
        )

        result = memory_store.prune(days=90)
        assert result["knowledge_deleted"] >= 1

        # Verify item is gone
        items = memory_store.get_by_category("fact", context="work")
        assert not any(i["id"] == kid for i in items)


# ===========================================================================
# 5. Conversation & Tool History Pipeline
# ===========================================================================


class TestConversationPipeline:
    """Test conversation storage, search, consolidation eligibility."""

    def test_conversation_store_and_search(self, memory_store):
        """Store conversation turns and search via FTS5."""
        session = str(uuid.uuid4())

        memory_store.store_turn(session, "user", "How do I set up RAG in GAIA?")
        memory_store.store_turn(
            session,
            "assistant",
            "To set up RAG, first install the rag extras and index your documents.",
        )
        memory_store.store_turn(session, "user", "What embedding model does it use?")
        memory_store.store_turn(
            session,
            "assistant",
            "GAIA uses nomic-embed-text for embeddings via Lemonade.",
        )

        # Search conversations
        results = memory_store.search_conversations("RAG")
        assert len(results) >= 1
        assert any("RAG" in r["content"] for r in results)

        # Search for embedding model
        results = memory_store.search_conversations("nomic-embed")
        assert len(results) >= 1

    def test_conversation_context_filtering(self, memory_store):
        """Conversations respect context filtering."""
        session_work = str(uuid.uuid4())
        session_personal = str(uuid.uuid4())

        memory_store.store_turn(
            session_work,
            "user",
            "Deploy the GAIA API to staging",
            context="work",
        )
        memory_store.store_turn(
            session_personal,
            "user",
            "Plan a birthday party for Max",
            context="personal",
        )

        # Search by context
        work_results = memory_store.search_conversations("plan", context="work")
        personal_results = memory_store.search_conversations("plan", context="personal")

        # "Plan" only appears in personal context
        assert len(personal_results) >= 1
        # Work context shouldn't have "plan a birthday party"
        for r in work_results:
            assert "birthday" not in r["content"].lower()

    def test_conversation_history_ordering(self, memory_store):
        """get_history returns turns in chronological order (oldest first)."""
        session = str(uuid.uuid4())

        for i in range(5):
            time.sleep(0.01)
            memory_store.store_turn(session, "user", f"Turn {i}")

        history = memory_store.get_history(session_id=session)
        assert len(history) == 5
        for i, turn in enumerate(history):
            assert turn["content"] == f"Turn {i}"

    def test_consolidation_eligibility(self, memory_store):
        """Sessions with enough old turns are eligible for consolidation."""
        # Create an old session (> 14 days ago)
        old_session = str(uuid.uuid4())
        old_ts = _past_iso(20)

        # Insert old turns via direct SQL to control the timestamp.
        # The AFTER INSERT trigger on conversations auto-syncs to conversations_fts.
        for i in range(6):
            memory_store._execute(
                "INSERT INTO conversations (session_id, role, content, context, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (old_session, "user", f"Old consolidation turn {i}", "global", old_ts),
            )

        # Create a recent session
        recent_session = str(uuid.uuid4())
        for i in range(6):
            memory_store.store_turn(recent_session, "user", f"Recent turn {i}")

        # Check eligibility
        eligible = memory_store.get_unconsolidated_sessions(
            older_than_days=14, min_turns=5
        )
        assert old_session in eligible
        assert recent_session not in eligible

    def test_mark_turns_consolidated(self, memory_store):
        """mark_turns_consolidated sets consolidated_at on specified turns."""
        session = str(uuid.uuid4())
        for i in range(3):
            memory_store.store_turn(session, "user", f"Consolidation turn {i}")

        history = memory_store.get_history(session_id=session)
        turn_ids = [t["id"] for t in history]

        # Mark first two turns as consolidated
        updated = memory_store.mark_turns_consolidated(turn_ids[:2])
        assert updated == 2

        # Verify consolidated_at is set (not None) via direct query
        with memory_store._lock:
            cursor = memory_store._conn.execute(
                "SELECT id, consolidated_at FROM conversations WHERE session_id = ? ORDER BY id",
                (session,),
            )
            rows = cursor.fetchall()

        assert rows[0][1] is not None  # First turn consolidated
        assert rows[1][1] is not None  # Second turn consolidated
        assert rows[2][1] is None  # Third turn NOT consolidated


class TestToolHistoryPipeline:
    """Test tool call logging and querying."""

    def test_log_and_retrieve_tool_calls(self, memory_store):
        """Log tool calls and retrieve via get_tool_stats."""
        session = str(uuid.uuid4())

        # Log successful calls
        memory_store.log_tool_call(
            session_id=session,
            tool_name="remember",
            args={"content": "Test memory"},
            result_summary="Stored successfully",
            success=True,
            duration_ms=50,
        )
        memory_store.log_tool_call(
            session_id=session,
            tool_name="remember",
            args={"content": "Another memory"},
            result_summary="Stored successfully",
            success=True,
            duration_ms=30,
        )

        # Log a failed call
        memory_store.log_tool_call(
            session_id=session,
            tool_name="remember",
            args={"content": ""},
            result_summary=None,
            success=False,
            error="content must be non-empty",
            duration_ms=5,
        )

        stats = memory_store.get_tool_stats("remember")
        assert stats["total_calls"] == 3
        assert stats["success_rate"] == pytest.approx(2 / 3, abs=0.01)
        assert stats["last_error"] == "content must be non-empty"

    def test_tool_errors_retrieval(self, memory_store):
        """get_tool_errors returns only failed calls."""
        session = str(uuid.uuid4())

        memory_store.log_tool_call(
            session, "recall", {"query": "test"}, "Results", True, duration_ms=20
        )
        memory_store.log_tool_call(
            session,
            "recall",
            {"query": ""},
            None,
            False,
            error="query is empty",
            duration_ms=1,
        )

        errors = memory_store.get_tool_errors()
        assert len(errors) >= 1
        assert errors[0]["error"] == "query is empty"
        assert errors[0]["tool_name"] == "recall"


# ===========================================================================
# 6. Temporal Awareness
# ===========================================================================


class TestTemporalAwareness:
    """Test due_at, reminded_at, and get_upcoming interactions."""

    def test_upcoming_items_basic(self, memory_store):
        """Items due within 7 days appear in get_upcoming."""
        kid = memory_store.store(
            category="reminder",
            content="Submit quarterly report to management",
            context="work",
            due_at=_future_iso(3),
        )

        upcoming = memory_store.get_upcoming(within_days=7)
        assert len(upcoming) >= 1
        assert any(u["id"] == kid for u in upcoming)

    def test_overdue_items_included(self, memory_store):
        """Overdue items (due_at in the past) appear when include_overdue=True."""
        kid = memory_store.store(
            category="reminder",
            content="Overdue task from last week submit docs",
            context="work",
            due_at=_past_iso(3),
        )

        # With include_overdue (default True)
        upcoming = memory_store.get_upcoming(within_days=7, include_overdue=True)
        assert any(u["id"] == kid for u in upcoming)

        # Without include_overdue
        upcoming_no_overdue = memory_store.get_upcoming(
            within_days=7, include_overdue=False
        )
        assert not any(u["id"] == kid for u in upcoming_no_overdue)

    def test_reminded_items_excluded(self, memory_store):
        """Items already reminded (reminded_at >= due_at) are excluded."""
        due = _future_iso(2)
        kid = memory_store.store(
            category="reminder",
            content="Already reminded about this upcoming deadline",
            context="work",
            due_at=due,
        )

        # Before reminder: should appear
        upcoming = memory_store.get_upcoming()
        assert any(u["id"] == kid for u in upcoming)

        # Mark as reminded (after due_at)
        future_reminded = (datetime.now().astimezone() + timedelta(days=3)).isoformat()
        memory_store.update(kid, reminded_at=future_reminded)

        # After reminder: should NOT appear
        upcoming = memory_store.get_upcoming()
        assert not any(u["id"] == kid for u in upcoming)

    def test_upcoming_context_filtering(self, memory_store):
        """get_upcoming respects context filter."""
        memory_store.store(
            category="reminder",
            content="Work deadline for Q2 goals review",
            context="work",
            due_at=_future_iso(3),
        )
        memory_store.store(
            category="reminder",
            content="Personal dentist appointment next week",
            context="personal",
            due_at=_future_iso(5),
        )

        work_upcoming = memory_store.get_upcoming(context="work")
        assert all(u["context"] == "work" for u in work_upcoming)
        assert len(work_upcoming) >= 1

        personal_upcoming = memory_store.get_upcoming(context="personal")
        assert all(u["context"] == "personal" for u in personal_upcoming)
        assert len(personal_upcoming) >= 1

    def test_far_future_items_excluded(self, memory_store):
        """Items due far in the future (>7 days) are NOT in default get_upcoming."""
        kid = memory_store.store(
            category="reminder",
            content="Conference talk preparation for next month",
            context="work",
            due_at=_future_iso(30),
        )

        upcoming = memory_store.get_upcoming(within_days=7)
        assert not any(u["id"] == kid for u in upcoming)

        # But with extended window:
        upcoming_30 = memory_store.get_upcoming(within_days=30)
        assert any(u["id"] == kid for u in upcoming_30)


# ===========================================================================
# 7. Dashboard Stats Integration
# ===========================================================================


class TestDashboardStats:
    """Test that dashboard aggregate queries work with populated data."""

    def test_stats_with_mixed_data(self, memory_store, fake_embedder):
        """get_stats() returns correct aggregates across all tables."""
        # Populate knowledge
        _populate_store(memory_store, embedder=fake_embedder)

        # Add conversations
        session = str(uuid.uuid4())
        for i in range(5):
            memory_store.store_turn(
                session, "user" if i % 2 == 0 else "assistant", f"Stats test turn {i}"
            )

        # Add tool history
        memory_store.log_tool_call(
            session, "remember", {"content": "x"}, "ok", True, duration_ms=10
        )

        stats = memory_store.get_stats()

        # Knowledge stats
        assert stats["knowledge"]["total"] == 10
        assert "fact" in stats["knowledge"]["by_category"]
        assert "work" in stats["knowledge"]["by_context"]
        assert stats["knowledge"]["entity_count"] >= 3  # gaia, vscode, sarah
        assert 0 < stats["knowledge"]["avg_confidence"] <= 1.0

        # Conversation stats
        assert stats["conversations"]["total_turns"] == 5
        assert stats["conversations"]["total_sessions"] == 1

        # Tool stats
        assert stats["tools"]["total_calls"] >= 1
        assert stats["tools"]["overall_success_rate"] > 0

    def test_get_all_knowledge_pagination(self, memory_store, fake_embedder):
        """get_all_knowledge supports pagination with offset/limit."""
        _populate_store(memory_store, embedder=fake_embedder)

        # First page
        page1 = memory_store.get_all_knowledge(limit=3, offset=0)
        assert len(page1["items"]) == 3
        assert page1["total"] == 10
        assert page1["offset"] == 0
        assert page1["limit"] == 3

        # Second page
        page2 = memory_store.get_all_knowledge(limit=3, offset=3)
        assert len(page2["items"]) == 3
        assert page2["total"] == 10

        # Pages should have different items
        page1_ids = {i["id"] for i in page1["items"]}
        page2_ids = {i["id"] for i in page2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

    def test_get_all_knowledge_sorting(self, memory_store):
        """get_all_knowledge sorts by confidence, updated_at, etc."""
        memory_store.store(
            category="fact", content="Low confidence item", confidence=0.1
        )
        time.sleep(0.01)
        memory_store.store(
            category="fact", content="High confidence item", confidence=0.9
        )
        time.sleep(0.01)
        memory_store.store(
            category="fact", content="Medium confidence item", confidence=0.5
        )

        # Sort by confidence desc
        result = memory_store.get_all_knowledge(sort_by="confidence", order="desc")
        confidences = [i["confidence"] for i in result["items"]]
        assert confidences == sorted(confidences, reverse=True)

        # Sort by confidence asc
        result = memory_store.get_all_knowledge(sort_by="confidence", order="asc")
        confidences = [i["confidence"] for i in result["items"]]
        assert confidences == sorted(confidences)

    def test_activity_timeline(self, memory_store):
        """get_activity_timeline returns daily activity counts."""
        session = str(uuid.uuid4())
        memory_store.store_turn(session, "user", "Activity timeline test data")
        memory_store.store(category="fact", content="Timeline knowledge item")
        memory_store.log_tool_call(
            session, "recall", {"q": "x"}, "ok", True, duration_ms=5
        )

        timeline = memory_store.get_activity_timeline(days=7)
        assert isinstance(timeline, list)
        assert len(timeline) >= 1

        # Today should have activity
        today = datetime.now().strftime("%Y-%m-%d")
        today_entry = next((t for t in timeline if t["date"] == today), None)
        assert today_entry is not None
        assert today_entry["conversations"] >= 1
        assert today_entry["knowledge_added"] >= 1
        assert today_entry["tool_calls"] >= 1


# ===========================================================================
# 8. Reconciliation Support
# ===========================================================================


class TestReconciliationSupport:
    """Test get_items_for_reconciliation for contradiction detection."""

    def test_get_items_for_reconciliation(self, memory_store, fake_embedder):
        """get_items_for_reconciliation returns items with embeddings for comparison."""
        ids = _populate_store(memory_store, embedder=fake_embedder)

        items = memory_store.get_items_for_reconciliation()
        assert len(items) == 10

        # All items should have embeddings
        for item in items:
            assert item["embedding"] is not None
            vec = _blob_to_embedding(item["embedding"])
            assert vec.shape == (768,)

    def test_reconciliation_context_filter(self, memory_store, fake_embedder):
        """get_items_for_reconciliation respects context filter."""
        _populate_store(memory_store, embedder=fake_embedder)

        work_items = memory_store.get_items_for_reconciliation(context="work")
        assert all(i["context"] == "work" for i in work_items)

        personal_items = memory_store.get_items_for_reconciliation(context="personal")
        assert all(i["context"] == "personal" for i in personal_items)

    def test_reconciliation_excludes_superseded(self, memory_store, fake_embedder):
        """Superseded items should not appear in reconciliation results."""
        old_id = memory_store.store(
            category="fact", content="Old reconciliation fact superseded"
        )
        memory_store.store_embedding(
            old_id,
            _embedding_to_blob(fake_embedder("Old reconciliation fact superseded")),
        )
        new_id = memory_store.store(
            category="fact", content="New reconciliation fact current"
        )
        memory_store.store_embedding(
            new_id, _embedding_to_blob(fake_embedder("New reconciliation fact current"))
        )

        memory_store.update(old_id, superseded_by=new_id)

        items = memory_store.get_items_for_reconciliation()
        item_ids = [i["id"] for i in items]
        assert new_id in item_ids
        assert old_id not in item_ids


# ===========================================================================
# 9. Pruning Pipeline
# ===========================================================================


class TestPruningPipeline:
    """Test multi-table pruning with data integrity."""

    def test_prune_old_conversations_and_tools(self, memory_store):
        """Prune removes conversations and tool_history older than N days."""
        session = str(uuid.uuid4())
        old_ts = _past_iso(100)

        # Insert old data via direct SQL (bypassing store_turn to set timestamp)
        memory_store._execute(
            "INSERT INTO conversations (session_id, role, content, context, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (session, "user", "Old conversation to prune", "global", old_ts),
        )
        memory_store._execute(
            "INSERT INTO tool_history "
            "(session_id, tool_name, args, result_summary, success, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session, "test_tool", None, "ok", 1, old_ts),
        )

        # Add recent data that should NOT be pruned
        recent_session = str(uuid.uuid4())
        memory_store.store_turn(recent_session, "user", "Recent conversation kept")
        memory_store.log_tool_call(
            recent_session, "test_tool", None, "ok", True, duration_ms=5
        )

        result = memory_store.prune(days=90)
        assert result["conversations_deleted"] >= 1
        assert result["tool_history_deleted"] >= 1

        # Verify recent data still exists
        history = memory_store.get_history(session_id=recent_session)
        assert len(history) >= 1

    def test_prune_preserves_high_confidence_knowledge(self, memory_store):
        """Prune does NOT delete old knowledge with confidence >= 0.1."""
        kid = memory_store.store(
            category="fact",
            content="Important fact with high confidence to preserve",
            context="work",
            confidence=0.5,
        )

        # Make it old
        old_ts = _past_iso(100)
        memory_store._execute(
            "UPDATE knowledge SET last_used = ?, updated_at = ? WHERE id = ?",
            (old_ts, old_ts, kid),
        )

        result = memory_store.prune(days=90)
        assert result["knowledge_deleted"] == 0

        # Still exists
        items = memory_store.get_by_category("fact", context="work")
        assert any(i["id"] == kid for i in items)


# ===========================================================================
# 10. Schema Migration
# ===========================================================================


class TestSchemaMigration:
    """Test that v2 schema features work on fresh databases."""

    def test_fresh_install_gets_v2_schema(self, memory_store):
        """Fresh install creates schema version 2."""
        with memory_store._lock:
            cursor = memory_store._conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            )
            version = cursor.fetchone()[0]
        assert version == 2

    def test_embedding_column_exists(self, memory_store):
        """The embedding BLOB column exists in the knowledge table."""
        kid = memory_store.store(
            category="fact", content="Schema test embedding column"
        )
        blob = b"\x00" * (768 * 4)
        result = memory_store.store_embedding(kid, blob)
        assert result is True

    def test_superseded_by_column_exists(self, memory_store):
        """The superseded_by column exists in the knowledge table."""
        old_id = memory_store.store(category="fact", content="Schema test old item")
        new_id = memory_store.store(
            category="fact", content="Schema test new replacement item"
        )
        result = memory_store.update(old_id, superseded_by=new_id)
        assert result is True

    def test_consolidated_at_column_exists(self, memory_store):
        """The consolidated_at column exists in the conversations table."""
        session = str(uuid.uuid4())
        memory_store.store_turn(session, "user", "Schema test consolidation column")
        history = memory_store.get_history(session_id=session)
        assert len(history) >= 1

        updated = memory_store.mark_turns_consolidated([history[0]["id"]])
        assert updated == 1


# ===========================================================================
# 11. Concurrent Access
# ===========================================================================


class TestConcurrentAccess:
    """Test thread safety of MemoryStore operations."""

    def test_concurrent_stores(self, memory_store):
        """Multiple threads can store items concurrently without errors."""
        lock = threading.Lock()
        results = []
        errors = []

        # Each item uses completely different vocabulary to avoid dedup
        unique_contents = [
            "Python decorators provide metaprogramming capabilities for functions",
            "SQLite WAL mode enables concurrent reads with single writer",
            "Kubernetes orchestrates container deployments across clusters",
            "React hooks replaced class-based lifecycle component methods",
            "Rust ownership model prevents memory safety bugs at compile time",
            "Docker images layer filesystem changes for reproducible builds",
            "GraphQL schemas define typed queries replacing REST endpoints",
            "Redis caches frequently accessed data in volatile memory",
            "Terraform provisions cloud infrastructure declaratively with code",
            "WebAssembly compiles native performance binaries for browsers",
        ]

        def store_item(i):
            try:
                kid = memory_store.store(
                    category="fact",
                    content=unique_contents[i],
                    context="work",
                )
                with lock:
                    results.append(kid)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=store_item, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent store errors: {errors}"
        assert len(results) == 10
        # All IDs should be unique (no dedup because content is different)
        assert len(set(results)) == 10

    def test_concurrent_search_and_store(self, memory_store, fake_embedder):
        """Concurrent reads (search) and writes (store) don't deadlock."""
        # Pre-populate some data
        _populate_store(memory_store, embedder=fake_embedder)

        lock = threading.Lock()
        search_results = []
        store_results = []
        errors = []

        def do_search(query):
            try:
                r = memory_store.search(query)
                with lock:
                    search_results.append(len(r))
            except Exception as e:
                with lock:
                    errors.append(f"search: {e}")

        concurrent_contents = [
            "Nginx reverse proxy load balances upstream servers",
            "PostgreSQL MVCC provides snapshot isolation transactions",
            "MongoDB sharding distributes documents across replica sets",
            "Elasticsearch inverted indexes enable full text search",
            "Prometheus scrapes metrics from instrumented service endpoints",
        ]

        def do_store(i):
            try:
                kid = memory_store.store(
                    category="fact",
                    content=concurrent_contents[i],
                    context="work",
                )
                with lock:
                    store_results.append(kid)
            except Exception as e:
                with lock:
                    errors.append(f"store: {e}")

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=do_search, args=("GAIA",)))
            threads.append(threading.Thread(target=do_store, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        assert len(search_results) == 5
        assert len(store_results) == 5


# ===========================================================================
# 12. Input Validation
# ===========================================================================


class TestInputValidation:
    """Test boundary conditions and validation across the pipeline."""

    def test_store_rejects_empty_content(self, memory_store):
        """store() raises ValueError for empty or whitespace-only content."""
        with pytest.raises(ValueError, match="content must be non-empty"):
            memory_store.store(category="fact", content="")

        with pytest.raises(ValueError, match="content must be non-empty"):
            memory_store.store(category="fact", content="   ")

    def test_update_rejects_empty_content(self, memory_store):
        """update() raises ValueError for whitespace-only content."""
        kid = memory_store.store(
            category="fact", content="Valid content for update test"
        )
        with pytest.raises(ValueError, match="content must be non-empty"):
            memory_store.update(kid, content="   ")

    def test_store_truncates_long_content(self, memory_store):
        """Content longer than MAX_CONTENT_LENGTH is truncated, not rejected."""
        from gaia.agents.base.memory_store import MAX_CONTENT_LENGTH

        long_content = "x" * (MAX_CONTENT_LENGTH + 500)
        kid = memory_store.store(category="fact", content=long_content)

        items = memory_store.get_by_category("fact")
        item = next(i for i in items if i["id"] == kid)
        assert len(item["content"]) == MAX_CONTENT_LENGTH

    def test_store_clamps_confidence(self, memory_store):
        """Confidence values outside [0.0, 1.0] are clamped."""
        id_high = memory_store.store(
            category="fact",
            content="High confidence clamped above one",
            confidence=1.5,
        )
        id_low = memory_store.store(
            category="fact",
            content="Low confidence clamped below zero",
            confidence=-0.3,
        )

        items = memory_store.get_by_category("fact")
        high = next(i for i in items if i["id"] == id_high)
        low = next(i for i in items if i["id"] == id_low)
        assert high["confidence"] == 1.0
        assert low["confidence"] == 0.0

    def test_store_normalizes_empty_strings_to_none(self, memory_store):
        """Empty-string entity and domain are normalized to NULL."""
        kid = memory_store.store(
            category="fact",
            content="Empty string normalization for entity and domain fields",
            entity="",
            domain="",
        )

        items = memory_store.get_by_category("fact")
        item = next(i for i in items if i["id"] == kid)
        assert item["entity"] is None
        assert item["domain"] is None

    def test_store_empty_turns_skipped(self, memory_store):
        """store_turn() silently skips empty or whitespace-only content."""
        session = str(uuid.uuid4())
        memory_store.store_turn(session, "user", "")
        memory_store.store_turn(session, "user", "   ")
        memory_store.store_turn(session, "user", "Real turn content")

        history = memory_store.get_history(session_id=session)
        assert len(history) == 1
        assert history[0]["content"] == "Real turn content"

    def test_update_nonexistent_returns_false(self, memory_store):
        """update() returns False for nonexistent knowledge_id."""
        result = memory_store.update("nonexistent-id-123", content="New content")
        assert result is False

    def test_delete_nonexistent_returns_false(self, memory_store):
        """delete() returns False for nonexistent knowledge_id."""
        result = memory_store.delete("nonexistent-id-456")
        assert result is False


# ===========================================================================
# 13. Source Management
# ===========================================================================


class TestSourceManagement:
    """Test source tracking and bulk operations."""

    def test_source_counts(self, memory_store):
        """get_source_counts() returns correct counts by source."""
        memory_store.store(
            category="fact",
            content="Python decorators enable metaprogramming patterns",
            source="tool",
        )
        memory_store.store(
            category="skill",
            content="Kubernetes orchestrates container deployments across nodes",
            source="tool",
        )
        memory_store.store(
            category="fact",
            content="Redis provides in-memory caching for low latency",
            source="discovery",
        )
        memory_store.store(
            category="preference",
            content="Dark mode preferred for all editor configurations",
            source="user",
            confidence=0.8,
        )

        counts = memory_store.get_source_counts()
        assert counts["tool"] == 2
        assert counts["discovery"] == 1
        assert counts["user"] == 1

    def test_delete_by_source(self, memory_store):
        """delete_by_source() removes all items with a given source."""
        memory_store.store(
            category="fact",
            content="PostgreSQL MVCC provides snapshot isolation transactions",
            source="discovery",
        )
        memory_store.store(
            category="skill",
            content="Terraform provisions cloud infrastructure declaratively",
            source="discovery",
        )
        memory_store.store(
            category="fact",
            content="GraphQL schemas define typed queries for APIs",
            source="tool",
        )

        deleted = memory_store.delete_by_source("discovery")
        assert deleted == 2

        # Verify discovery items gone, tool item remains
        counts = memory_store.get_source_counts()
        assert "discovery" not in counts
        assert counts.get("tool") == 1

        # Verify FTS is also cleaned up — search shouldn't find deleted items
        results = memory_store.search("PostgreSQL MVCC snapshot")
        assert len(results) == 0
        results = memory_store.search("Terraform provisions cloud")
        assert len(results) == 0


# ===========================================================================
# 14. update_confidence Direct
# ===========================================================================


class TestUpdateConfidence:
    """Test update_confidence() method directly."""

    def test_update_confidence_positive_delta(self, memory_store):
        """update_confidence() increases confidence by delta."""
        kid = memory_store.store(
            category="fact",
            content="Confidence delta positive test entry",
            confidence=0.5,
        )
        memory_store.update_confidence(kid, delta=0.1)

        items = memory_store.get_by_category("fact")
        item = next(i for i in items if i["id"] == kid)
        assert item["confidence"] == pytest.approx(0.6, abs=1e-6)

    def test_update_confidence_negative_delta(self, memory_store):
        """update_confidence() decreases confidence by negative delta."""
        kid = memory_store.store(
            category="fact",
            content="Confidence delta negative test entry",
            confidence=0.5,
        )
        memory_store.update_confidence(kid, delta=-0.2)

        items = memory_store.get_by_category("fact")
        item = next(i for i in items if i["id"] == kid)
        assert item["confidence"] == pytest.approx(0.3, abs=1e-6)

    def test_update_confidence_clamped_to_bounds(self, memory_store):
        """update_confidence() clamps result to [0.0, 1.0]."""
        kid = memory_store.store(
            category="fact",
            content="Confidence clamping bounds test entry",
            confidence=0.9,
        )

        # Overflow
        memory_store.update_confidence(kid, delta=0.5)
        items = memory_store.get_by_category("fact")
        item = next(i for i in items if i["id"] == kid)
        assert item["confidence"] == pytest.approx(1.0, abs=1e-6)

        # Underflow
        memory_store.update_confidence(kid, delta=-2.0)
        items = memory_store.get_by_category("fact")
        item = next(i for i in items if i["id"] == kid)
        assert item["confidence"] == pytest.approx(0.0, abs=1e-6)


# ===========================================================================
# 15. FTS Rebuild Correctness
# ===========================================================================


class TestFTSRebuild:
    """Test that FTS index rebuild restores search after corruption."""

    def test_rebuild_restores_search_after_fts_clear(self, memory_store):
        """After clearing FTS index, rebuild_fts() restores search results."""
        kid = memory_store.store(
            category="fact",
            content="Searchable content before FTS rebuild test",
        )

        # Verify search works
        results = memory_store.search("Searchable content rebuild")
        assert len(results) >= 1

        # Corrupt: clear the FTS index
        with memory_store._lock:
            memory_store._conn.execute("DELETE FROM knowledge_fts")
            memory_store._conn.commit()

        # Search should fail now
        results = memory_store.search("Searchable content rebuild")
        assert len(results) == 0

        # Rebuild
        memory_store.rebuild_fts()

        # Search should work again
        results = memory_store.search("Searchable content rebuild")
        assert len(results) >= 1
        assert any(r["id"] == kid for r in results)

    def test_rebuild_fts_preserves_conversation_search(self, memory_store):
        """rebuild_fts() also restores conversation FTS index."""
        session = str(uuid.uuid4())
        memory_store.store_turn(
            session, "user", "Conversation FTS rebuild test message"
        )

        # Verify search works
        results = memory_store.search_conversations("rebuild test")
        assert len(results) >= 1

        # Rebuild (should not break anything)
        memory_store.rebuild_fts()

        # Search still works
        results = memory_store.search_conversations("rebuild test")
        assert len(results) >= 1


# ===========================================================================
# 16. Cross-Table Interactions
# ===========================================================================


class TestCrossTableInteractions:
    """Test interactions between knowledge, conversations, and tools."""

    def test_full_session_lifecycle(self, memory_store, fake_embedder):
        """Simulate a full agent session: conversation -> tool calls -> knowledge."""
        session = str(uuid.uuid4())

        # 1. User asks a question
        memory_store.store_turn(session, "user", "What AMD processor supports NPU?")

        # 2. Agent searches memory (via recall tool)
        memory_store.log_tool_call(
            session,
            "recall",
            {"query": "AMD NPU processor"},
            "No results",
            True,
            duration_ms=15,
        )

        # 3. Agent responds
        memory_store.store_turn(
            session,
            "assistant",
            "AMD Ryzen AI processors support NPU acceleration.",
        )

        # 4. Agent stores a fact (via remember tool)
        kid = memory_store.store(
            category="fact",
            content="AMD Ryzen AI processors have dedicated NPU hardware",
            context="work",
            source="tool",
        )
        vec = fake_embedder("AMD Ryzen AI processors have dedicated NPU hardware")
        memory_store.store_embedding(kid, _embedding_to_blob(vec))

        memory_store.log_tool_call(
            session,
            "remember",
            {"content": "AMD Ryzen AI processors have dedicated NPU hardware"},
            f"Stored as {kid}",
            True,
            duration_ms=30,
        )

        # Verify everything is queryable
        history = memory_store.get_history(session_id=session)
        assert len(history) == 2

        conv_results = memory_store.search_conversations("NPU")
        assert len(conv_results) >= 1

        knowledge_results = memory_store.search("Ryzen NPU")
        assert len(knowledge_results) >= 1

        tool_stats = memory_store.get_tool_stats("recall")
        assert tool_stats["total_calls"] >= 1

        stats = memory_store.get_stats()
        assert stats["conversations"]["total_turns"] == 2
        assert stats["knowledge"]["total"] >= 1
        assert stats["tools"]["total_calls"] >= 2

    def test_supersession_chain(self, memory_store, fake_embedder):
        """Test a chain of fact updates via supersession."""
        # Version 1
        v1_id = memory_store.store(
            category="fact",
            content="Project uses Python 3.10 with pip",
            context="work",
        )
        memory_store.store_embedding(
            v1_id,
            _embedding_to_blob(fake_embedder("Project uses Python 3.10 with pip")),
        )

        # Version 2 supersedes v1
        v2_id = memory_store.store(
            category="fact",
            content="Project uses Python 3.11 with poetry",
            context="work",
        )
        memory_store.store_embedding(
            v2_id,
            _embedding_to_blob(fake_embedder("Project uses Python 3.11 with poetry")),
        )
        memory_store.update(v1_id, superseded_by=v2_id)

        # Version 3 supersedes v2
        v3_id = memory_store.store(
            category="fact",
            content="Project uses Python 3.12 with uv",
            context="work",
        )
        memory_store.store_embedding(
            v3_id,
            _embedding_to_blob(fake_embedder("Project uses Python 3.12 with uv")),
        )
        memory_store.update(v2_id, superseded_by=v3_id)

        # Only v3 should be active
        results = memory_store.search("Project Python")
        active_ids = [r["id"] for r in results]
        assert v3_id in active_ids
        assert v2_id not in active_ids
        assert v1_id not in active_ids

        # Full history visible with include_superseded
        all_items = memory_store.get_all_knowledge(include_superseded=True)
        all_ids = [i["id"] for i in all_items["items"]]
        assert v1_id in all_ids
        assert v2_id in all_ids
        assert v3_id in all_ids

        # Only active items have embeddings visible
        embedded = memory_store.get_items_with_embeddings()
        embedded_ids = [i["id"] for i in embedded]
        assert v3_id in embedded_ids
        assert v1_id not in embedded_ids
        assert v2_id not in embedded_ids
