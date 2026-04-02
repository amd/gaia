# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for the Memory Dashboard REST API router.

Tests all endpoints using FastAPI TestClient with an injected in-memory
MemoryStore — no real database file or running server required.

Endpoints covered:
  GET  /api/memory/stats              (v1 + v2 embedding/reconciliation)
  GET  /api/memory/activity
  GET  /api/memory/knowledge          (v1 + v2 include_superseded, time_from, time_to)
  POST /api/memory/knowledge
  PUT  /api/memory/knowledge/{id}
  DELETE /api/memory/knowledge/{id}
  GET  /api/memory/entities
  GET  /api/memory/entities/{entity}
  GET  /api/memory/contexts
  GET  /api/memory/tools
  GET  /api/memory/errors
  GET  /api/memory/conversations
  GET  /api/memory/conversations/search
  GET  /api/memory/conversations/{session_id}
  GET  /api/memory/upcoming
  POST /api/memory/consolidate        (v2)
  POST /api/memory/rebuild-embeddings (v2)
  POST /api/memory/reconcile          (v2)
  GET  /api/memory/embedding-coverage (v2)
  POST /api/memory/rebuild-fts
  POST /api/memory/prune
  GET  /api/memory/settings
  PUT  /api/memory/settings
  DELETE /api/memory/all
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.ui.routers.memory as memory_router_mod
from gaia.agents.base.memory_store import MemoryStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_store(tmp_path):
    """Fresh MemoryStore backed by a temp-file DB for each test."""
    store = MemoryStore(db_path=tmp_path / "test_memory.db")
    yield store
    store.close()


@pytest.fixture
def client(test_store):
    """FastAPI TestClient with the memory router and injected test store."""
    from gaia.ui.database import ChatDatabase
    from gaia.ui.dependencies import get_db

    app = FastAPI()
    app.include_router(memory_router_mod.router)

    # Inject test store into the module-level singleton
    memory_router_mod._store = test_store

    # Override the DB dependency so settings endpoints work without a real server
    db = ChatDatabase(":memory:")
    app.dependency_overrides[get_db] = lambda: db

    with TestClient(app) as c:
        yield c

    # Reset singleton so other tests get a fresh one
    memory_router_mod._store = None
    db.close()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _create_knowledge(client, content="Test fact", **kwargs):
    """POST a knowledge entry and return the response JSON."""
    body = {"content": content, "category": "fact", **kwargs}
    resp = client.post("/api/memory/knowledge", json=body)
    assert resp.status_code == 200
    return resp.json()


# ===========================================================================
# Stats & Activity
# ===========================================================================


class TestStatsAndActivity:

    def test_stats_returns_200(self, client):
        resp = client.get("/api/memory/stats")
        assert resp.status_code == 200

    def test_stats_structure(self, client):
        resp = client.get("/api/memory/stats")
        data = resp.json()
        assert "knowledge" in data
        assert "conversations" in data
        assert "tools" in data
        assert "db_size_bytes" in data
        assert isinstance(data["db_size_bytes"], int)
        assert data["db_size_bytes"] >= 0

    def test_activity_returns_200(self, client):
        resp = client.get("/api/memory/activity?days=7")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_activity_days_validation(self, client):
        # days must be 1–365
        assert client.get("/api/memory/activity?days=0").status_code == 422
        assert client.get("/api/memory/activity?days=366").status_code == 422
        assert client.get("/api/memory/activity?days=30").status_code == 200


# ===========================================================================
# Knowledge CRUD
# ===========================================================================


class TestKnowledgeCRUD:

    def test_create_knowledge(self, client):
        resp = client.post(
            "/api/memory/knowledge", json={"content": "New fact", "category": "fact"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert "knowledge_id" in data

    def test_list_knowledge_empty(self, client):
        resp = client.get("/api/memory/knowledge")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_list_knowledge_with_entry(self, client):
        _create_knowledge(client, "Listed fact")
        resp = client.get("/api/memory/knowledge")
        data = resp.json()
        assert data["total"] >= 1
        assert any("Listed fact" in item["content"] for item in data["items"])

    def test_list_knowledge_category_filter(self, client):
        _create_knowledge(client, "A preference", category="preference")
        _create_knowledge(client, "A fact", category="fact")

        resp = client.get("/api/memory/knowledge?category=preference")
        data = resp.json()
        for item in data["items"]:
            assert item["category"] == "preference"

    def test_list_knowledge_search(self, client):
        _create_knowledge(client, "Python programming language")
        _create_knowledge(client, "Unrelated entry XYZ")

        resp = client.get("/api/memory/knowledge?search=python")
        data = resp.json()
        contents = [item["content"].lower() for item in data["items"]]
        assert any("python" in c for c in contents)

    def test_list_knowledge_pagination(self, client):
        for i in range(10):
            _create_knowledge(client, f"Paginated entry {i}")

        page1 = client.get("/api/memory/knowledge?offset=0&limit=5").json()
        page2 = client.get("/api/memory/knowledge?offset=5&limit=5").json()

        ids1 = {item["id"] for item in page1["items"]}
        ids2 = {item["id"] for item in page2["items"]}
        assert ids1.isdisjoint(ids2)

    def test_update_knowledge(self, client):
        created = _create_knowledge(client, "Original content")
        kid = created["knowledge_id"]

        resp = client.put(
            f"/api/memory/knowledge/{kid}", json={"content": "Updated content"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

        # Verify update
        listing = client.get("/api/memory/knowledge").json()
        contents = [item["content"] for item in listing["items"]]
        assert "Updated content" in contents

    def test_update_nonexistent_returns_404(self, client):
        resp = client.put("/api/memory/knowledge/nonexistent-id", json={"content": "x"})
        assert resp.status_code == 404

    def test_update_empty_body_returns_400(self, client):
        created = _create_knowledge(client, "Entry for empty update test")
        kid = created["knowledge_id"]
        resp = client.put(f"/api/memory/knowledge/{kid}", json={})
        assert resp.status_code == 400

    def test_delete_knowledge(self, client):
        created = _create_knowledge(client, "To be deleted")
        kid = created["knowledge_id"]

        resp = client.delete(f"/api/memory/knowledge/{kid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Verify deletion
        listing = client.get("/api/memory/knowledge").json()
        ids = [item["id"] for item in listing["items"]]
        assert kid not in ids

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/memory/knowledge/no-such-id")
        assert resp.status_code == 404


# ===========================================================================
# Entities & Contexts
# ===========================================================================


class TestEntitiesAndContexts:

    def test_entities_empty(self, client):
        resp = client.get("/api/memory/entities")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_entities_with_data(self, client):
        _create_knowledge(client, "Alice is the team lead", entity="person:alice")
        resp = client.get("/api/memory/entities")
        data = resp.json()
        entities = [e["entity"] for e in data]
        assert "person:alice" in entities

    def test_entity_knowledge(self, client):
        _create_knowledge(client, "Bob likes Python", entity="person:bob")
        resp = client.get("/api/memory/entities/person:bob")
        assert resp.status_code == 200
        items = resp.json()
        assert isinstance(items, list)
        assert any("Bob" in item["content"] for item in items)

    def test_contexts_returns_list(self, client):
        _create_knowledge(client, "Work fact", context="work")
        resp = client.get("/api/memory/contexts")
        assert resp.status_code == 200
        data = resp.json()
        contexts = [c["context"] for c in data]
        assert "work" in contexts


# ===========================================================================
# Tool Performance & Errors
# ===========================================================================


class TestToolPerformance:

    def test_tools_empty(self, client):
        resp = client.get("/api/memory/tools")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_tools_with_data(self, client, test_store):
        test_store.log_tool_call(
            session_id="s1",
            tool_name="my_tool",
            args={},
            result_summary="ok",
            success=True,
            duration_ms=50,
        )
        resp = client.get("/api/memory/tools")
        data = resp.json()
        names = [t["tool_name"] for t in data]
        assert "my_tool" in names

    def test_tool_history(self, client, test_store):
        test_store.log_tool_call(
            session_id="s1",
            tool_name="hist_tool",
            args={"x": 1},
            result_summary="done",
            success=True,
        )
        resp = client.get("/api/memory/tools/hist_tool/history")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 1
        assert items[0]["tool_name"] == "hist_tool"

    def test_errors_returns_list(self, client):
        resp = client.get("/api/memory/errors")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ===========================================================================
# Conversations
# ===========================================================================


class TestConversations:

    def test_conversations_empty(self, client):
        resp = client.get("/api/memory/conversations")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_conversations_with_data(self, client, test_store):
        test_store.store_turn("sess-abc", "user", "Hello there")
        test_store.store_turn("sess-abc", "assistant", "Hi!")

        resp = client.get("/api/memory/conversations")
        data = resp.json()
        session_ids = [c["session_id"] for c in data]
        assert "sess-abc" in session_ids

    def test_conversation_detail(self, client, test_store):
        test_store.store_turn("sess-detail", "user", "Detail message")
        resp = client.get("/api/memory/conversations/sess-detail")
        assert resp.status_code == 200
        turns = resp.json()
        assert isinstance(turns, list)
        assert any("Detail message" in t["content"] for t in turns)

    def test_conversation_search(self, client, test_store):
        test_store.store_turn("sess-search", "user", "Quantum physics is interesting")
        resp = client.get("/api/memory/conversations/search?query=quantum+physics")
        assert resp.status_code == 200
        results = resp.json()
        assert isinstance(results, list)
        assert any("quantum" in r.get("content", "").lower() for r in results)

    def test_conversation_search_requires_query(self, client):
        resp = client.get("/api/memory/conversations/search")
        assert resp.status_code == 422


# ===========================================================================
# Temporal
# ===========================================================================


class TestTemporal:

    def test_upcoming_returns_list(self, client):
        resp = client.get("/api/memory/upcoming")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_upcoming_with_due_item(self, client, test_store):
        from datetime import datetime, timedelta

        future = (datetime.now().astimezone() + timedelta(days=3)).isoformat()
        test_store.store(category="fact", content="Meeting in 3 days", due_at=future)
        resp = client.get("/api/memory/upcoming?days=7")
        data = resp.json()
        assert any("Meeting" in item["content"] for item in data)


# ===========================================================================
# Maintenance
# ===========================================================================


class TestMaintenance:

    def test_rebuild_fts_returns_200(self, client):
        resp = client.post("/api/memory/rebuild-fts")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rebuilt"

    def test_rebuild_fts_restores_search(self, client, test_store):
        """After FTS corruption, rebuild-fts endpoint restores search."""
        kid = test_store.store(category="fact", content="Searchable after rebuild")
        # Corrupt FTS
        test_store._conn.execute("DELETE FROM knowledge_fts")
        test_store._conn.commit()

        client.post("/api/memory/rebuild-fts")

        # Knowledge search should now work
        results = test_store.search("searchable after rebuild")
        assert any(r["id"] == kid for r in results)

    def test_prune_returns_200(self, client):
        resp = client.post("/api/memory/prune?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert "tool_history_deleted" in data
        assert "conversations_deleted" in data
        assert "knowledge_deleted" in data

    def test_prune_days_validation(self, client):
        # days must be 7–365
        assert client.post("/api/memory/prune?days=6").status_code == 422
        assert client.post("/api/memory/prune?days=366").status_code == 422
        assert client.post("/api/memory/prune?days=90").status_code == 200

    def test_prune_removes_old_data(self, client, test_store):
        """Prune endpoint deletes data older than the specified cutoff."""
        from datetime import datetime, timedelta

        old_ts = (datetime.now().astimezone() - timedelta(days=100)).isoformat()
        test_store._conn.execute(
            "INSERT INTO tool_history (session_id, tool_name, args, result_summary, success, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ("old-sess", "old_tool", "{}", "done", 1, old_ts),
        )
        test_store._conn.commit()

        resp = client.post("/api/memory/prune?days=90")
        assert resp.json()["tool_history_deleted"] >= 1


# ===========================================================================
# 7. Category Validation
# ===========================================================================


class TestCategoryValidation:
    """POST/PUT knowledge endpoints reject invalid category values."""

    def test_create_invalid_category_returns_422(self, client):
        """POST /api/memory/knowledge with invalid category → 422."""
        resp = client.post(
            "/api/memory/knowledge",
            json={
                "content": "test content",
                "category": "invalid_cat",
            },
        )
        assert resp.status_code == 422

    def test_create_valid_category_succeeds(self, client):
        """POST /api/memory/knowledge with valid category → 200."""
        for cat in ("fact", "preference", "error", "skill", "note", "reminder"):
            resp = client.post(
                "/api/memory/knowledge",
                json={
                    "content": f"test {cat} content",
                    "category": cat,
                },
            )
            assert resp.status_code == 200, f"category={cat} failed: {resp.json()}"

    def test_update_invalid_category_returns_422(self, client, test_store):
        """PUT /api/memory/knowledge/{id} with invalid category → 422."""
        kid = test_store.store(category="fact", content="some fact")
        resp = client.put(f"/api/memory/knowledge/{kid}", json={"category": "junk"})
        assert resp.status_code == 422

    def test_update_valid_category_succeeds(self, client, test_store):
        """PUT /api/memory/knowledge/{id} with valid category → 200."""
        kid = test_store.store(category="fact", content="some fact")
        resp = client.put(f"/api/memory/knowledge/{kid}", json={"category": "skill"})
        assert resp.status_code == 200


# ===========================================================================
# 8. Search Query Length Validation
# ===========================================================================


class TestQueryLengthValidation:
    """Search endpoints enforce max query length."""

    def test_knowledge_search_too_long_returns_422(self, client):
        """GET /api/memory/knowledge?search=... with >500 char search → 422."""
        long_query = "x" * 501
        resp = client.get(f"/api/memory/knowledge?search={long_query}")
        assert resp.status_code == 422

    def test_conversations_search_too_long_returns_422(self, client):
        """GET /api/memory/conversations/search?query=... >500 chars → 422."""
        long_query = "x" * 501
        resp = client.get(f"/api/memory/conversations/search?query={long_query}")
        assert resp.status_code == 422

    def test_conversations_search_valid_length_succeeds(self, client):
        """GET /api/memory/conversations/search with normal query → 200."""
        resp = client.get("/api/memory/conversations/search?query=hello")
        assert resp.status_code == 200


# ===========================================================================
# 9. Session History Limit Param
# ===========================================================================


class TestSessionHistoryLimit:
    """GET /api/memory/conversations/{session_id} accepts limit query param."""

    def test_limit_param_respected(self, client, test_store):
        """limit query param caps the number of turns returned."""
        for i in range(10):
            test_store.store_turn("s1", "user", f"message {i}")

        resp = client.get("/api/memory/conversations/s1?limit=5")
        assert resp.status_code == 200
        assert len(resp.json()) <= 5

    def test_limit_too_large_returns_422(self, client):
        """limit > 500 → 422."""
        resp = client.get("/api/memory/conversations/s1?limit=501")
        assert resp.status_code == 422

    def test_entity_limit_param_respected(self, client, test_store):
        """GET /api/memory/entities/{entity} accepts limit query param."""
        test_store.store(
            category="fact", content="Alice works here", entity="person:alice"
        )
        resp = client.get("/api/memory/entities/person:alice?limit=10")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ===========================================================================
# POST /knowledge input validation — content and date fields
# ===========================================================================


class TestCreateKnowledgeInputValidation:
    """POST /api/memory/knowledge validates content, due_at as 422 not 500."""

    def test_empty_content_returns_422(self, client):
        """POST with empty content is rejected as 422 Unprocessable Entity."""
        resp = client.post(
            "/api/memory/knowledge", json={"content": "", "category": "fact"}
        )
        assert resp.status_code == 422

    def test_whitespace_only_content_returns_422(self, client):
        """POST with whitespace-only content is rejected as 422."""
        resp = client.post(
            "/api/memory/knowledge", json={"content": "   ", "category": "fact"}
        )
        assert resp.status_code == 422

    def test_invalid_due_at_returns_422(self, client):
        """POST with a non-ISO due_at string is rejected as 422, not 500."""
        resp = client.post(
            "/api/memory/knowledge",
            json={
                "content": "Test entry",
                "category": "reminder",
                "due_at": "tomorrow",
            },
        )
        assert resp.status_code == 422

    def test_natural_language_due_at_returns_422(self, client):
        """POST with 'next Friday' as due_at is rejected as 422."""
        resp = client.post(
            "/api/memory/knowledge",
            json={"content": "Dentist appointment", "due_at": "next Friday"},
        )
        assert resp.status_code == 422

    def test_valid_due_at_accepted(self, client):
        """POST with a valid ISO 8601 due_at returns 200."""
        resp = client.post(
            "/api/memory/knowledge",
            json={
                "content": "Valid reminder",
                "category": "reminder",
                "due_at": "2026-06-01T09:00:00+00:00",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

    def test_valid_content_accepted(self, client):
        """POST with non-empty content returns 200."""
        resp = client.post(
            "/api/memory/knowledge", json={"content": "Valid entry", "category": "fact"}
        )
        assert resp.status_code == 200


# ===========================================================================
# PUT /knowledge/{id} input validation — content, due_at, reminded_at
# ===========================================================================


class TestUpdateKnowledgeInputValidation:
    """PUT /api/memory/knowledge/{id} validates content, due_at, reminded_at."""

    def test_empty_content_returns_422(self, client, test_store):
        """PUT with empty content is rejected as 422."""
        kid = test_store.store(category="fact", content="Original content")
        resp = client.put(f"/api/memory/knowledge/{kid}", json={"content": ""})
        assert resp.status_code == 422

    def test_whitespace_content_returns_422(self, client, test_store):
        """PUT with whitespace-only content is rejected as 422."""
        kid = test_store.store(category="fact", content="Original content")
        resp = client.put(f"/api/memory/knowledge/{kid}", json={"content": "   "})
        assert resp.status_code == 422

    def test_invalid_due_at_returns_422(self, client, test_store):
        """PUT with a non-ISO due_at is rejected as 422, not 500."""
        kid = test_store.store(category="fact", content="Some entry")
        resp = client.put(f"/api/memory/knowledge/{kid}", json={"due_at": "not-a-date"})
        assert resp.status_code == 422

    def test_invalid_reminded_at_returns_422(self, client, test_store):
        """PUT with a non-ISO reminded_at is rejected as 422, not 500."""
        kid = test_store.store(category="reminder", content="Some reminder")
        resp = client.put(
            f"/api/memory/knowledge/{kid}", json={"reminded_at": "tomorrow"}
        )
        assert resp.status_code == 422

    def test_valid_reminded_at_accepted(self, client, test_store):
        """PUT with a valid ISO 8601 reminded_at returns 200."""
        kid = test_store.store(category="reminder", content="Check in on project")
        resp = client.put(
            f"/api/memory/knowledge/{kid}",
            json={"reminded_at": "2026-03-19T10:00:00+00:00"},
        )
        assert resp.status_code == 200

    def test_valid_content_update_accepted(self, client, test_store):
        """PUT with non-empty content returns 200."""
        kid = test_store.store(category="fact", content="Old content")
        resp = client.put(
            f"/api/memory/knowledge/{kid}", json={"content": "New content"}
        )
        assert resp.status_code == 200


class TestListKnowledgeSensitiveDefault:
    """Sensitive items must be excluded from list_knowledge by default."""

    def test_sensitive_items_excluded_by_default(self, client, test_store):
        """GET /api/memory/knowledge returns no sensitive items by default."""
        test_store.store(
            category="fact",
            content="Non-sensitive item for default filter test",
            sensitive=False,
        )
        test_store.store(
            category="fact",
            content="Sensitive item that should be hidden by default",
            sensitive=True,
        )
        resp = client.get("/api/memory/knowledge")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(
            not item.get("sensitive") for item in items
        ), "No sensitive items should appear without include_sensitive=true"

    def test_sensitive_items_included_with_flag(self, client, test_store):
        """GET /api/memory/knowledge?include_sensitive=true returns sensitive items."""
        test_store.store(
            category="fact",
            content="Sensitive item visible with include_sensitive flag",
            sensitive=True,
        )
        resp = client.get("/api/memory/knowledge?include_sensitive=true")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert any(
            item.get("sensitive") for item in items
        ), "Sensitive items must appear when include_sensitive=true"

    def test_sensitive_true_filter_shows_only_sensitive(self, client, test_store):
        """GET /api/memory/knowledge?sensitive=true returns only sensitive items."""
        test_store.store(
            category="fact", content="Non-sensitive baseline entry", sensitive=False
        )
        test_store.store(
            category="fact", content="Sensitive-only filter test entry", sensitive=True
        )
        resp = client.get("/api/memory/knowledge?sensitive=true")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(
            item.get("sensitive") for item in items
        ), "Only sensitive items should appear when sensitive=true"


# ===========================================================================
# v2 Router Tests — Consolidation Endpoint
# ===========================================================================


class TestConsolidateEndpoint:
    """Test POST /api/memory/consolidate endpoint."""

    @pytest.fixture(autouse=True)
    def reset_consolidate_fn(self):
        """Ensure _consolidate_fn is None before and after each test."""
        memory_router_mod._consolidate_fn = None
        yield
        memory_router_mod._consolidate_fn = None

    def test_consolidate_returns_503_when_no_agent(self, client):
        """POST /api/memory/consolidate returns 503 when no agent is registered."""
        resp = client.post("/api/memory/consolidate")
        assert resp.status_code == 503
        assert "agent session" in resp.json()["detail"].lower()

    def test_consolidate_returns_200_when_agent_registered(self, client):
        """POST /api/memory/consolidate returns 200 when _consolidate_fn is set."""
        memory_router_mod._consolidate_fn = lambda max_sessions=5: {
            "consolidated": 2,
            "extracted_items": 8,
        }
        resp = client.post("/api/memory/consolidate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["consolidated"] == 2
        assert data["extracted_items"] == 8

    def test_consolidate_max_sessions_param(self, client):
        """POST /api/memory/consolidate passes max_sessions to the callback."""
        captured = {}

        def mock_consolidate(max_sessions=5):
            captured["max_sessions"] = max_sessions
            return {"consolidated": 0, "extracted_items": 0}

        memory_router_mod._consolidate_fn = mock_consolidate
        resp = client.post("/api/memory/consolidate?max_sessions=10")
        assert resp.status_code == 200
        assert captured["max_sessions"] == 10

    def test_consolidate_max_sessions_validation(self, client):
        """POST /api/memory/consolidate rejects invalid max_sessions."""
        assert client.post("/api/memory/consolidate?max_sessions=0").status_code == 422
        assert client.post("/api/memory/consolidate?max_sessions=51").status_code == 422

    def test_consolidate_runtime_error_returns_500(self, client):
        """POST /api/memory/consolidate returns 500 on internal errors."""

        def boom(max_sessions=5):
            raise RuntimeError("LLM connection failed")

        memory_router_mod._consolidate_fn = boom
        resp = client.post("/api/memory/consolidate")
        assert resp.status_code == 500
        assert "RuntimeError" in resp.json()["detail"]


# ===========================================================================
# v2 Router Tests — Rebuild Embeddings Endpoint
# ===========================================================================


class TestRebuildEmbeddingsEndpoint:
    """Test POST /api/memory/rebuild-embeddings endpoint."""

    def test_rebuild_embeddings_returns_500_when_backfill_not_implemented(
        self, client, test_store
    ):
        """POST /api/memory/rebuild-embeddings returns 500 when store lacks backfill_embeddings.

        Unlike consolidate/reconcile which return 503 (no agent) or 501 (not
        implemented), rebuild-embeddings always tries the LemonadeProvider path
        and returns 500 for any error — including a missing store method.
        """
        if hasattr(test_store, "backfill_embeddings"):
            pytest.skip("MemoryStore now implements backfill_embeddings")
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1] * 768]
        with patch("gaia.llm.providers.lemonade.LemonadeProvider", return_value=mock_provider):
            resp = client.post("/api/memory/rebuild-embeddings")
        assert resp.status_code == 500

    def test_rebuild_embeddings_returns_200_when_implemented(self, client, test_store):
        """POST /api/memory/rebuild-embeddings returns 200 with mock method."""
        test_store.backfill_embeddings = lambda *args, **kwargs: {
            "backfilled": 5,
            "total_without": 10,
        }
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1] * 768]
        with patch("gaia.llm.providers.lemonade.LemonadeProvider", return_value=mock_provider):
            resp = client.post("/api/memory/rebuild-embeddings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["backfilled"] == 5
        assert data["total_without"] == 10

    def test_rebuild_embeddings_runtime_error_returns_500(self, client, test_store):
        """POST /api/memory/rebuild-embeddings returns 500 on internal errors."""

        def boom(*args, **kwargs):
            raise RuntimeError("Lemonade server unreachable")

        test_store.backfill_embeddings = boom
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [[0.1] * 768]
        with patch("gaia.llm.providers.lemonade.LemonadeProvider", return_value=mock_provider):
            resp = client.post("/api/memory/rebuild-embeddings")
        assert resp.status_code == 500


# ===========================================================================
# v2 Router Tests — Reconcile Endpoint
# ===========================================================================


class TestReconcileEndpoint:
    """Test POST /api/memory/reconcile endpoint."""

    @pytest.fixture(autouse=True)
    def reset_reconcile_fn(self):
        """Ensure _reconcile_fn is None before and after each test."""
        memory_router_mod._reconcile_fn = None
        yield
        memory_router_mod._reconcile_fn = None

    def test_reconcile_returns_503_when_no_agent_and_faiss_missing(self, client):
        """POST /api/memory/reconcile returns 503 when no agent and faiss unavailable."""
        resp = client.post("/api/memory/reconcile")
        assert resp.status_code == 503
        assert "faiss" in resp.json()["detail"].lower()

    def test_reconcile_returns_200_when_agent_registered(self, client):
        """POST /api/memory/reconcile returns 200 when _reconcile_fn is set."""
        memory_router_mod._reconcile_fn = lambda: {
            "pairs_checked": 10,
            "reinforced": 3,
            "contradicted": 1,
            "weakened": 2,
            "neutral": 4,
        }
        resp = client.post("/api/memory/reconcile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pairs_checked"] == 10
        assert data["contradicted"] == 1

    def test_reconcile_runtime_error_returns_500(self, client, mocker):
        """POST /api/memory/reconcile returns 500 when standalone path raises a runtime error."""
        import sys
        import types

        # Provide a minimal faiss stub so the ImportError guard passes
        fake_faiss = types.ModuleType("faiss")
        fake_faiss.IndexFlatIP = lambda dim: None  # not called — store raises first
        mocker.patch.dict(sys.modules, {"faiss": fake_faiss})

        # Make _get_store raise a RuntimeError after faiss is "imported"
        mocker.patch(
            "gaia.ui.routers.memory._get_store",
            side_effect=RuntimeError("DB connection exploded"),
        )

        resp = client.post("/api/memory/reconcile")
        assert resp.status_code == 500


# ===========================================================================
# v2 Router Tests — Embedding Coverage Endpoint
# ===========================================================================


class TestEmbeddingCoverageEndpoint:
    """Test GET /api/memory/embedding-coverage endpoint."""

    def test_embedding_coverage_returns_501_when_not_implemented(
        self, client, test_store
    ):
        """GET /api/memory/embedding-coverage returns 501 without store method."""
        if hasattr(test_store, "get_embedding_coverage"):
            pytest.skip("MemoryStore now implements get_embedding_coverage")
        resp = client.get("/api/memory/embedding-coverage")
        assert resp.status_code == 501

    def test_embedding_coverage_returns_200_when_implemented(self, client, test_store):
        """GET /api/memory/embedding-coverage returns 200 with mock method."""
        test_store.get_embedding_coverage = lambda: {
            "total_items": 100,
            "with_embedding": 80,
            "without_embedding": 20,
            "coverage_pct": 80.0,
        }
        resp = client.get("/api/memory/embedding-coverage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_items"] == 100
        assert data["coverage_pct"] == 80.0

    def test_embedding_coverage_runtime_error_returns_500(self, client, test_store):
        """GET /api/memory/embedding-coverage returns 500 on internal errors."""

        def boom():
            raise sqlite3.OperationalError("no such table: knowledge")

        test_store.get_embedding_coverage = boom
        resp = client.get("/api/memory/embedding-coverage")
        assert resp.status_code == 500


# ===========================================================================
# v2 Router Tests — Knowledge with Superseded Filter
# ===========================================================================


class TestKnowledgeSupersededFilter:
    """Test GET /api/memory/knowledge with include_superseded parameter."""

    def test_include_superseded_param_accepted(self, client):
        """Router accepts include_superseded query param."""
        resp = client.get("/api/memory/knowledge?include_superseded=true")
        assert resp.status_code == 200

    def test_include_superseded_false_accepted(self, client):
        """Router accepts include_superseded=false."""
        resp = client.get("/api/memory/knowledge?include_superseded=false")
        assert resp.status_code == 200

    def test_superseded_filtering_delegates_to_store(self, client, test_store):
        """When store supports v2 params, include_superseded is passed through."""
        captured = {}
        original = test_store.get_all_knowledge

        def mock_get_all(include_superseded=False, time_from=None, time_to=None, **kw):
            captured["include_superseded"] = include_superseded
            return original(**kw)

        test_store.get_all_knowledge = mock_get_all

        client.get("/api/memory/knowledge?include_superseded=true")
        assert captured.get("include_superseded") is True

        client.get("/api/memory/knowledge?include_superseded=false")
        assert captured.get("include_superseded") is False

    def test_superseded_works_with_real_store(self, client, test_store):
        """include_superseded filters correctly with the real MemoryStore.

        The data layer's get_all_knowledge now accepts include_superseded,
        so this tests actual filtering without mocks.
        """
        # Content must be completely different to avoid dedup (>80% overlap)
        old_id = test_store.store(
            category="fact",
            content="The deployment pipeline uses Jenkins with Docker agents",
        )
        new_id = test_store.store(
            category="skill",
            content="Always run pytest before merging pull requests",
        )
        test_store.update(old_id, superseded_by=new_id)

        # Default: superseded items hidden
        resp = client.get("/api/memory/knowledge?include_sensitive=true")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        assert new_id in ids
        assert old_id not in ids

        # With include_superseded=true: both visible
        resp = client.get(
            "/api/memory/knowledge?include_superseded=true&include_sensitive=true"
        )
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()["items"]]
        assert new_id in ids
        assert old_id in ids

    def test_include_superseded_fallback_when_time_params_unsupported(
        self, client, test_store
    ):
        """include_superseded still works even when time_from causes TypeError.

        When the store supports include_superseded but not time_from/time_to,
        the router should fall back to passing just include_superseded.
        """
        captured = {}
        original = test_store.get_all_knowledge

        def mock_only_superseded(include_superseded=False, **kw):
            # Accepts include_superseded but NOT time_from/time_to
            captured["include_superseded"] = include_superseded
            return original(include_superseded=include_superseded, **kw)

        test_store.get_all_knowledge = mock_only_superseded

        # Passes time_from which mock rejects → cascades to just include_superseded
        resp = client.get(
            "/api/memory/knowledge"
            "?include_superseded=true"
            "&time_from=2026-01-01T00:00:00%2B00:00"
        )
        assert resp.status_code == 200
        assert captured.get("include_superseded") is True


# ===========================================================================
# v2 Router Tests — Time Filter Parameters
# ===========================================================================


class TestTimeFilterParams:
    """Test GET /api/memory/knowledge with time_from/time_to parameters."""

    def test_time_from_param_accepted(self, client):
        """time_from with valid ISO 8601 is accepted."""
        resp = client.get("/api/memory/knowledge?time_from=2026-01-01T00:00:00%2B00:00")
        assert resp.status_code == 200

    def test_time_to_param_accepted(self, client):
        """time_to with valid ISO 8601 is accepted."""
        resp = client.get("/api/memory/knowledge?time_to=2026-12-31T23:59:59%2B00:00")
        assert resp.status_code == 200

    def test_both_time_params(self, client):
        """Both time_from and time_to are accepted together."""
        resp = client.get(
            "/api/memory/knowledge"
            "?time_from=2026-01-01T00:00:00%2B00:00"
            "&time_to=2026-12-31T23:59:59%2B00:00"
        )
        assert resp.status_code == 200

    def test_invalid_time_from_returns_422(self, client):
        """Invalid time_from returns 422."""
        resp = client.get("/api/memory/knowledge?time_from=not-a-date")
        assert resp.status_code == 422

    def test_invalid_time_to_returns_422(self, client):
        """Invalid time_to returns 422."""
        resp = client.get("/api/memory/knowledge?time_to=tomorrow")
        assert resp.status_code == 422

    def test_time_params_delegated_to_store(self, client, test_store):
        """When store supports v2 params, time_from/time_to are passed through."""
        captured = {}
        original = test_store.get_all_knowledge

        def mock_get_all(time_from=None, time_to=None, **kw):
            captured["time_from"] = time_from
            captured["time_to"] = time_to
            return original(**kw)

        test_store.get_all_knowledge = mock_get_all

        client.get(
            "/api/memory/knowledge"
            "?time_from=2026-03-01T00:00:00%2B00:00"
            "&time_to=2026-03-31T23:59:59%2B00:00"
        )
        assert captured.get("time_from") == "2026-03-01T00:00:00+00:00"
        assert captured.get("time_to") == "2026-03-31T23:59:59+00:00"


# ===========================================================================
# v2 Router Tests — Stats with v2 Fields
# ===========================================================================


class TestStatsV2Fields:
    """Test GET /api/memory/stats includes v2 embedding and reconciliation fields."""

    def test_stats_includes_embedding_section(self, client):
        """GET /api/memory/stats response includes 'embedding' section."""
        resp = client.get("/api/memory/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "embedding" in data
        emb = data["embedding"]
        assert "total_items" in emb
        assert "with_embedding" in emb
        assert "without_embedding" in emb
        assert "coverage_pct" in emb

    def test_stats_includes_reconciliation_section(self, client):
        """GET /api/memory/stats response includes 'reconciliation' section."""
        resp = client.get("/api/memory/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "reconciliation" in data
        recon = data["reconciliation"]
        assert "pairs_checked" in recon
        assert "contradictions_found" in recon

    def test_stats_embedding_populated_when_store_supports(self, client, test_store):
        """When store supports get_embedding_coverage, stats are populated."""
        test_store.get_embedding_coverage = lambda: {
            "total_items": 50,
            "with_embedding": 45,
            "without_embedding": 5,
            "coverage_pct": 90.0,
        }
        resp = client.get("/api/memory/stats")
        data = resp.json()
        assert data["embedding"]["total_items"] == 50
        assert data["embedding"]["coverage_pct"] == 90.0

    def test_stats_reconciliation_populated_when_store_supports(
        self, client, test_store
    ):
        """When store supports get_reconciliation_stats, stats are populated."""
        test_store.get_reconciliation_stats = lambda: {
            "last_run": "2026-04-01T10:00:00-07:00",
            "pairs_checked": 25,
            "contradictions_found": 3,
        }
        resp = client.get("/api/memory/stats")
        data = resp.json()
        assert data["reconciliation"]["pairs_checked"] == 25
        assert data["reconciliation"]["contradictions_found"] == 3

    def test_stats_defaults_when_store_lacks_v2_methods(self, client, test_store):
        """Stats returns safe defaults when store lacks v2 methods."""
        v2_methods = ("get_embedding_coverage", "get_reconciliation_stats")
        if all(hasattr(test_store, m) for m in v2_methods):
            pytest.skip("MemoryStore now implements v2 stats methods")
        resp = client.get("/api/memory/stats")
        data = resp.json()
        # Should still have embedding/reconciliation with default values
        assert data["embedding"]["total_items"] == 0
        assert data["reconciliation"]["pairs_checked"] == 0


# ===========================================================================
# v2 Router Tests — Upcoming Endpoint
# ===========================================================================


class TestUpcomingV2:
    """Test GET /api/memory/upcoming returns list."""

    def test_upcoming_returns_list(self, client):
        """GET /api/memory/upcoming returns 200 with list."""
        resp = client.get("/api/memory/upcoming")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ===========================================================================
# v2 Router Tests — Knowledge CRUD with v2 Fields
# ===========================================================================


class TestKnowledgeCRUDV2:
    """Test knowledge CRUD endpoints handle v2 fields."""

    def test_create_knowledge_returns_id(self, client):
        """POST /api/memory/knowledge response includes knowledge_id."""
        resp = client.post(
            "/api/memory/knowledge",
            json={"content": "Created knowledge v2 field test", "category": "fact"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "knowledge_id" in data

    def test_superseded_by_not_user_settable_via_api(self, client, test_store):
        """PUT /api/memory/knowledge/{id} rejects superseded_by (pipeline-managed)."""
        kid = test_store.store(
            category="fact", content="Some fact for superseded API test"
        )
        # superseded_by is not in the KnowledgeUpdate model, so it gets
        # stripped from the body → empty kwargs → 400 "No fields to update"
        resp = client.put(
            f"/api/memory/knowledge/{kid}",
            json={"superseded_by": "some-other-id"},
        )
        assert resp.status_code == 400


# ===========================================================================
# v2 Router Tests — Conversations with Consolidation Status
# ===========================================================================


class TestConversationsV2:
    """Test conversation endpoints include consolidation information."""

    def test_list_sessions_returns_200(self, client, test_store):
        """GET /api/memory/conversations returns 200."""
        test_store.store_turn("sess-v2-test", "user", "Hello from v2 session test")
        resp = client.get("/api/memory/conversations")
        assert resp.status_code == 200

    def test_conversation_search_returns_200(self, client, test_store):
        """GET /api/memory/conversations/search returns 200."""
        test_store.store_turn(
            "sess-search-v2", "user", "Searchable v2 conversation content test"
        )
        resp = client.get(
            "/api/memory/conversations/search?query=Searchable+v2+conversation"
        )
        assert resp.status_code == 200


# ===========================================================================
# Settings Endpoints
# ===========================================================================


class TestMemorySettings:
    """Test GET/PUT /api/memory/settings endpoints."""

    def test_get_settings_returns_200(self, client):
        """GET /api/memory/settings returns 200."""
        resp = client.get("/api/memory/settings")
        assert resp.status_code == 200

    def test_get_settings_default_mcp_disabled(self, client):
        """GET /api/memory/settings returns mcp_memory_enabled=false by default."""
        resp = client.get("/api/memory/settings")
        data = resp.json()
        assert "mcp_memory_enabled" in data
        assert data["mcp_memory_enabled"] is False

    def test_put_settings_enables_mcp(self, client):
        """PUT /api/memory/settings with mcp_memory_enabled=true returns true."""
        resp = client.put("/api/memory/settings", json={"mcp_memory_enabled": True})
        assert resp.status_code == 200
        assert resp.json()["mcp_memory_enabled"] is True

    def test_put_settings_disables_mcp(self, client):
        """PUT /api/memory/settings with mcp_memory_enabled=false returns false."""
        # Enable first
        client.put("/api/memory/settings", json={"mcp_memory_enabled": True})
        # Then disable
        resp = client.put("/api/memory/settings", json={"mcp_memory_enabled": False})
        assert resp.status_code == 200
        assert resp.json()["mcp_memory_enabled"] is False

    def test_put_settings_persists_across_get(self, client):
        """Setting persists: GET after PUT reflects the written value."""
        client.put("/api/memory/settings", json={"mcp_memory_enabled": True})
        resp = client.get("/api/memory/settings")
        assert resp.json()["mcp_memory_enabled"] is True

    def test_put_settings_ignores_unknown_keys(self, client):
        """PUT /api/memory/settings with unknown keys does not error."""
        resp = client.put("/api/memory/settings", json={"unknown_key": "value"})
        assert resp.status_code == 200
        # Unknown key doesn't affect mcp_memory_enabled default
        assert resp.json()["mcp_memory_enabled"] is False

    def test_put_settings_returns_200(self, client):
        """PUT /api/memory/settings returns 200."""
        resp = client.put("/api/memory/settings", json={})
        assert resp.status_code == 200

    # -- memory_enabled global toggle --

    def test_get_settings_includes_memory_enabled(self, client):
        """GET /api/memory/settings response includes memory_enabled key."""
        resp = client.get("/api/memory/settings")
        assert "memory_enabled" in resp.json()

    def test_memory_enabled_default_true(self, client):
        """memory_enabled defaults to true."""
        resp = client.get("/api/memory/settings")
        assert resp.json()["memory_enabled"] is True

    def test_put_settings_disables_memory(self, client):
        """PUT with memory_enabled=false persists and is returned."""
        resp = client.put("/api/memory/settings", json={"memory_enabled": False})
        assert resp.status_code == 200
        assert resp.json()["memory_enabled"] is False

    def test_put_settings_reenables_memory(self, client):
        """memory_enabled can be toggled back to true."""
        client.put("/api/memory/settings", json={"memory_enabled": False})
        resp = client.put("/api/memory/settings", json={"memory_enabled": True})
        assert resp.json()["memory_enabled"] is True

    def test_memory_enabled_persists_across_get(self, client):
        """Disabling memory persists: GET after PUT reflects false."""
        client.put("/api/memory/settings", json={"memory_enabled": False})
        resp = client.get("/api/memory/settings")
        assert resp.json()["memory_enabled"] is False

    def test_both_settings_can_be_updated_together(self, client):
        """Both mcp_memory_enabled and memory_enabled can be set in one PUT."""
        resp = client.put(
            "/api/memory/settings",
            json={"memory_enabled": False, "mcp_memory_enabled": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["memory_enabled"] is False
        assert data["mcp_memory_enabled"] is True


# ===========================================================================
# Clear All Memory Endpoint
# ===========================================================================


class TestClearAllMemory:
    """Test DELETE /api/memory/all endpoint."""

    def test_clear_all_returns_200(self, client, test_store):
        """DELETE /api/memory/all returns 200."""
        resp = client.delete("/api/memory/all")
        assert resp.status_code == 200

    def test_clear_all_returns_counts(self, client, test_store):
        """DELETE /api/memory/all response includes deleted row counts."""
        resp = client.delete("/api/memory/all")
        data = resp.json()
        assert "knowledge" in data
        assert "tool_history" in data
        assert "conversations" in data

    def test_clear_all_wipes_knowledge(self, client, test_store):
        """After DELETE /api/memory/all, knowledge list is empty."""
        test_store.store(category="fact", content="A fact that should be wiped")
        test_store.store(category="skill", content="A skill that should be wiped")

        resp = client.delete("/api/memory/all")
        assert resp.json()["knowledge"] == 2

        # Knowledge browser should now be empty
        resp = client.get("/api/memory/knowledge?include_sensitive=true")
        assert resp.json()["total"] == 0

    def test_clear_all_wipes_conversations(self, client, test_store):
        """After DELETE /api/memory/all, conversation history is empty."""
        test_store.store_turn("sess-clear", "user", "Hello")
        test_store.store_turn("sess-clear", "assistant", "Hi there")

        client.delete("/api/memory/all")

        resp = client.get("/api/memory/conversations")
        assert resp.json() == []

    def test_clear_all_is_idempotent(self, client, test_store):
        """DELETE /api/memory/all on an already-empty store returns 200 with zeros."""
        client.delete("/api/memory/all")
        resp = client.delete("/api/memory/all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["knowledge"] == 0
        assert data["tool_history"] == 0
        assert data["conversations"] == 0
