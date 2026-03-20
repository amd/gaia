# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for the Memory Dashboard REST API router.

Tests all endpoints using FastAPI TestClient with an injected in-memory
MemoryStore — no real database file or running server required.

Endpoints covered:
  GET  /api/memory/stats
  GET  /api/memory/activity
  GET  /api/memory/knowledge
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
  POST /api/memory/rebuild-fts
  POST /api/memory/prune
"""

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
    app = FastAPI()
    app.include_router(memory_router_mod.router)

    # Inject test store into the module-level singleton
    memory_router_mod._store = test_store

    with TestClient(app) as c:
        yield c

    # Reset singleton so other tests get a fresh one
    memory_router_mod._store = None


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
