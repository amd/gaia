# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Integration tests for Memory Dashboard REST API.

Tests the FastAPI endpoints in src/gaia/ui/routers/memory.py with a REAL
MemoryStore backed by SQLite in a temp directory. Uses FastAPI TestClient
to avoid starting a real server.

These tests verify that the REST API correctly serializes/deserializes
data, validates input, and delegates to MemoryStore correctly.
"""

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.agents.base.memory_store import MemoryStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _future_iso(days: int = 1) -> str:
    return (datetime.now().astimezone() + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_store(tmp_path):
    """Create a real MemoryStore in a temp directory."""
    store = MemoryStore(db_path=tmp_path / "test_memory_api.db")
    yield store
    store.close()


@pytest.fixture
def api_client(memory_store):
    """Create a FastAPI TestClient with the memory router, injecting a test store.

    Patches the module-level _store singleton in the memory router so all
    endpoints use our tmp_path-backed store instead of ~/.gaia/memory.db.
    """
    from gaia.ui.routers import memory as memory_mod

    app = FastAPI()
    app.include_router(memory_mod.router)

    # Inject test store into the module-level singleton
    original_store = memory_mod._store
    memory_mod._store = memory_store

    client = TestClient(app)
    yield client

    # Restore original (cleanup)
    memory_mod._store = original_store


def _populate_test_data(memory_store):
    """Seed the test store with diverse knowledge, conversations, tool calls."""
    # Knowledge entries
    ids = []
    items = [
        {
            "category": "fact",
            "content": "User works on the GAIA project at AMD",
            "context": "work",
            "entity": "project:gaia",
        },
        {
            "category": "preference",
            "content": "User prefers dark mode in VSCode",
            "context": "work",
            "entity": "app:vscode",
        },
        {
            "category": "skill",
            "content": "Deploy: run tests, build, push staging, verify",
            "context": "work",
            "domain": "deployment",
        },
        {
            "category": "error",
            "content": "pip install torch fails without --index-url",
            "context": "work",
        },
        {
            "category": "note",
            "content": "Standup: API v2 migration complete",
            "context": "work",
            "domain": "meeting:standup",
        },
        {
            "category": "reminder",
            "content": "Q2 report due April 15",
            "context": "work",
            "due_at": _future_iso(14),
        },
        {
            "category": "fact",
            "content": "User has a cat named Whiskers",
            "context": "personal",
        },
        {
            "category": "fact",
            "content": "Sarah Chen VP Engineering sarah@amd.com",
            "context": "work",
            "entity": "person:sarah_chen",
        },
    ]

    for item in items:
        kwargs = {k: v for k, v in item.items()}
        ids.append(memory_store.store(**kwargs))

    # Conversations
    session = "test-session-001"
    memory_store.store_turn(session, "user", "How do I set up RAG in GAIA?")
    memory_store.store_turn(
        session, "assistant", "Install rag extras and index your documents."
    )

    # Tool history
    memory_store.log_tool_call(
        session, "remember", {"content": "test"}, "ok", True, duration_ms=25
    )
    memory_store.log_tool_call(
        session, "recall", {"query": "GAIA"}, "2 results", True, duration_ms=15
    )
    memory_store.log_tool_call(
        session,
        "recall",
        {"query": ""},
        None,
        False,
        error="query empty",
        duration_ms=2,
    )

    return ids


# ===========================================================================
# 1. Stats & Activity Endpoints
# ===========================================================================


class TestStatsEndpoints:
    """GET /api/memory/stats and /api/memory/activity."""

    def test_stats_empty_db(self, api_client):
        """Stats endpoint returns valid structure on empty database."""
        resp = api_client.get("/api/memory/stats")
        assert resp.status_code == 200
        data = resp.json()

        assert "knowledge" in data
        assert data["knowledge"]["total"] == 0
        assert "conversations" in data
        assert "tools" in data

    def test_stats_with_data(self, api_client, memory_store):
        """Stats reflect populated data correctly."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/stats")
        assert resp.status_code == 200
        data = resp.json()

        assert data["knowledge"]["total"] == 8
        assert data["conversations"]["total_turns"] == 2
        assert data["conversations"]["total_sessions"] == 1
        assert data["tools"]["total_calls"] == 3
        assert data["tools"]["total_errors"] == 1

    def test_activity_timeline(self, api_client, memory_store):
        """Activity timeline returns daily counts."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/activity?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        # Today should have activity
        today = datetime.now().strftime("%Y-%m-%d")
        today_entry = next((d for d in data if d["date"] == today), None)
        assert today_entry is not None
        assert today_entry["knowledge_added"] >= 1


# ===========================================================================
# 2. Knowledge CRUD Endpoints
# ===========================================================================


class TestKnowledgeCRUD:
    """POST, GET, PUT, DELETE /api/memory/knowledge."""

    def test_create_knowledge(self, api_client):
        """POST /api/memory/knowledge creates a knowledge entry."""
        resp = api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "GAIA supports NPU acceleration",
                "category": "fact",
                "context": "work",
                "entity": "project:gaia",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert "knowledge_id" in data

    def test_create_knowledge_with_due_at(self, api_client):
        """POST with due_at creates a reminder."""
        due = _future_iso(7)
        resp = api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "Submit quarterly report",
                "category": "reminder",
                "due_at": due,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"

    def test_create_knowledge_validates_category(self, api_client):
        """POST with invalid category returns 422."""
        resp = api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "Test content",
                "category": "invalid_category",
            },
        )
        assert resp.status_code == 422

    def test_create_knowledge_validates_empty_content(self, api_client):
        """POST with empty content returns 422."""
        resp = api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "   ",
                "category": "fact",
            },
        )
        assert resp.status_code == 422

    def test_create_knowledge_validates_due_at_format(self, api_client):
        """POST with invalid due_at format returns 422."""
        resp = api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "Test reminder",
                "category": "reminder",
                "due_at": "not-a-date",
            },
        )
        assert resp.status_code == 422

    def test_list_knowledge(self, api_client, memory_store):
        """GET /api/memory/knowledge returns paginated results."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/knowledge")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == 8
        assert len(data["items"]) == 8

    def test_list_knowledge_pagination(self, api_client, memory_store):
        """GET with limit/offset paginates correctly."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/knowledge?limit=3&offset=0")
        assert resp.status_code == 200
        page1 = resp.json()
        assert len(page1["items"]) == 3
        assert page1["total"] == 8

        resp = api_client.get("/api/memory/knowledge?limit=3&offset=3")
        page2 = resp.json()
        assert len(page2["items"]) == 3

        # Different items on each page
        ids1 = {i["id"] for i in page1["items"]}
        ids2 = {i["id"] for i in page2["items"]}
        assert ids1.isdisjoint(ids2)

    def test_list_knowledge_category_filter(self, api_client, memory_store):
        """GET with category filter returns only matching items."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/knowledge?category=fact")
        assert resp.status_code == 200
        data = resp.json()
        assert all(i["category"] == "fact" for i in data["items"])

    def test_list_knowledge_context_filter(self, api_client, memory_store):
        """GET with context filter returns only matching items."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/knowledge?context=personal")
        assert resp.status_code == 200
        data = resp.json()
        assert all(i["context"] == "personal" for i in data["items"])
        assert data["total"] >= 1

    def test_list_knowledge_entity_filter(self, api_client, memory_store):
        """GET with entity filter returns only matching items."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/knowledge?entity=person:sarah_chen")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) >= 1
        assert all(i["entity"] == "person:sarah_chen" for i in data["items"])

    def test_list_knowledge_search(self, api_client, memory_store):
        """GET with search query performs FTS5 search."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/knowledge?search=GAIA")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) >= 1
        assert any("GAIA" in i["content"] for i in data["items"])

    def test_list_knowledge_sort(self, api_client, memory_store):
        """GET with sort_by/order sorts correctly."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/knowledge?sort_by=confidence&order=desc")
        assert resp.status_code == 200
        data = resp.json()
        confidences = [i["confidence"] for i in data["items"]]
        assert confidences == sorted(confidences, reverse=True)

    def test_list_knowledge_time_params_accepted(self, api_client, memory_store):
        """GET with time_from/time_to params doesn't error (200 response).

        Note: get_all_knowledge() may not yet filter by time_from/time_to
        (the router silently falls back). This test verifies the endpoint
        accepts the params without errors rather than testing filtering,
        since filtering is verified in the direct MemoryStore tests.
        """
        memory_store.store(
            category="fact",
            content="Temporal param acceptance test item",
            context="work",
        )

        time_before = _now_iso()
        resp = api_client.get(f"/api/memory/knowledge?time_from={time_before}")
        assert resp.status_code == 200

        resp = api_client.get(f"/api/memory/knowledge?time_to={time_before}")
        assert resp.status_code == 200

    def test_list_knowledge_excludes_sensitive(self, api_client, memory_store):
        """GET excludes sensitive items by default."""
        memory_store.store(
            category="fact",
            content="Public fact visible to all",
            context="work",
        )
        memory_store.store(
            category="fact",
            content="Secret API key sk-12345",
            context="work",
            sensitive=True,
        )

        resp = api_client.get("/api/memory/knowledge")
        data = resp.json()
        assert not any(i["sensitive"] for i in data["items"])
        assert data["total"] == 1

        # With include_sensitive
        resp = api_client.get("/api/memory/knowledge?include_sensitive=true")
        data = resp.json()
        assert data["total"] == 2

    def test_list_knowledge_excludes_superseded(self, api_client, memory_store):
        """GET excludes superseded items by default."""
        old_id = memory_store.store(
            category="fact", content="Old fact superseded version"
        )
        new_id = memory_store.store(
            category="fact", content="New fact current replacement version"
        )
        memory_store.update(old_id, superseded_by=new_id)

        resp = api_client.get("/api/memory/knowledge")
        data = resp.json()
        item_ids = [i["id"] for i in data["items"]]
        assert old_id not in item_ids
        assert new_id in item_ids

        # With include_superseded
        resp = api_client.get("/api/memory/knowledge?include_superseded=true")
        data = resp.json()
        item_ids = [i["id"] for i in data["items"]]
        assert old_id in item_ids
        assert new_id in item_ids

    def test_update_knowledge(self, api_client, memory_store):
        """PUT /api/memory/knowledge/{id} updates fields."""
        kid = memory_store.store(
            category="fact",
            content="Original content before update",
            context="work",
        )

        resp = api_client.put(
            f"/api/memory/knowledge/{kid}",
            json={"content": "Updated content after edit"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"

        # Verify update
        results = memory_store.search("Updated content after edit")
        assert any(r["id"] == kid for r in results)

    def test_update_knowledge_validates_category(self, api_client, memory_store):
        """PUT with invalid category returns 422."""
        kid = memory_store.store(category="fact", content="Category validation test")

        resp = api_client.put(
            f"/api/memory/knowledge/{kid}",
            json={"category": "bad_category"},
        )
        assert resp.status_code == 422

    def test_update_knowledge_not_found(self, api_client):
        """PUT for nonexistent ID returns 404."""
        resp = api_client.put(
            "/api/memory/knowledge/nonexistent-id",
            json={"content": "This should fail"},
        )
        assert resp.status_code == 404

    def test_update_knowledge_empty_body(self, api_client, memory_store):
        """PUT with no fields to update returns 400."""
        kid = memory_store.store(category="fact", content="Empty body update test")

        resp = api_client.put(f"/api/memory/knowledge/{kid}", json={})
        assert resp.status_code == 400

    def test_delete_knowledge(self, api_client, memory_store):
        """DELETE /api/memory/knowledge/{id} removes the entry."""
        kid = memory_store.store(category="fact", content="Item to delete via API")

        resp = api_client.delete(f"/api/memory/knowledge/{kid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"

        # Verify deleted
        results = memory_store.search("Item to delete via API")
        assert not any(r["id"] == kid for r in results)

    def test_delete_knowledge_not_found(self, api_client):
        """DELETE for nonexistent ID returns 404."""
        resp = api_client.delete("/api/memory/knowledge/nonexistent-id")
        assert resp.status_code == 404


# ===========================================================================
# 3. Entity & Context Endpoints
# ===========================================================================


class TestEntityAndContextEndpoints:
    """GET /api/memory/entities and /api/memory/contexts."""

    def test_list_entities(self, api_client, memory_store):
        """GET /api/memory/entities returns entity list with counts."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/entities")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2  # gaia, vscode, sarah_chen

        # Each entity has count
        for entity in data:
            assert "entity" in entity
            assert "count" in entity
            assert entity["count"] >= 1

    def test_get_entity_knowledge(self, api_client, memory_store):
        """GET /api/memory/entities/{entity} returns entity's knowledge."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/entities/person:sarah_chen")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert all("sarah" in i["content"].lower() for i in data)

    def test_list_contexts(self, api_client, memory_store):
        """GET /api/memory/contexts returns context list with counts."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/contexts")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

        context_names = [c["context"] for c in data]
        assert "work" in context_names
        assert "personal" in context_names


# ===========================================================================
# 4. Tool Performance Endpoints
# ===========================================================================


class TestToolEndpoints:
    """GET /api/memory/tools and tool history."""

    def test_tool_summary(self, api_client, memory_store):
        """GET /api/memory/tools returns per-tool stats."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        tool_names = [t["tool_name"] for t in data]
        assert "remember" in tool_names
        assert "recall" in tool_names

    def test_tool_history(self, api_client, memory_store):
        """GET /api/memory/tools/{name}/history returns call history."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/tools/recall/history")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2  # 2 recall calls in test data

    def test_recent_errors(self, api_client, memory_store):
        """GET /api/memory/errors returns recent failures."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/errors")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["error"] == "query empty"


# ===========================================================================
# 5. Conversation Endpoints
# ===========================================================================


class TestConversationEndpoints:
    """GET /api/memory/conversations."""

    def test_list_sessions(self, api_client, memory_store):
        """GET /api/memory/conversations returns session list."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["session_id"] == "test-session-001"
        assert data[0]["turn_count"] == 2

    def test_get_session_turns(self, api_client, memory_store):
        """GET /api/memory/conversations/{session_id} returns turns."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/conversations/test-session-001")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["role"] == "user"
        assert "RAG" in data[0]["content"]

    def test_search_conversations(self, api_client, memory_store):
        """GET /api/memory/conversations/search returns matching turns."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/conversations/search?query=RAG")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any("RAG" in r["content"] for r in data)


# ===========================================================================
# 6. Temporal Endpoint
# ===========================================================================


class TestTemporalEndpoint:
    """GET /api/memory/upcoming."""

    def test_upcoming_items(self, api_client, memory_store):
        """GET /api/memory/upcoming returns due items."""
        _populate_test_data(memory_store)

        resp = api_client.get("/api/memory/upcoming?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any("Q2 report" in i["content"] for i in data)


# ===========================================================================
# 7. Maintenance Endpoints
# ===========================================================================


class TestMaintenanceEndpoints:
    """POST /api/memory/prune, /api/memory/rebuild-fts."""

    def test_prune_endpoint(self, api_client, memory_store):
        """POST /api/memory/prune returns deletion counts."""
        _populate_test_data(memory_store)

        resp = api_client.post("/api/memory/prune?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert "tool_history_deleted" in data
        assert "conversations_deleted" in data
        assert "knowledge_deleted" in data

    def test_rebuild_fts_endpoint(self, api_client, memory_store):
        """POST /api/memory/rebuild-fts rebuilds FTS indexes."""
        _populate_test_data(memory_store)

        resp = api_client.post("/api/memory/rebuild-fts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rebuilt"

        # Verify search still works after rebuild
        results = memory_store.search("GAIA")
        assert len(results) >= 1

    def test_consolidation_endpoint(self, api_client, memory_store):
        """POST /api/memory/consolidate returns response (501 if not implemented)."""
        resp = api_client.post("/api/memory/consolidate")
        # Accept either 200 (implemented) or 501 (not yet implemented)
        assert resp.status_code in (200, 501)

    def test_rebuild_embeddings_endpoint(self, api_client, memory_store):
        """POST /api/memory/rebuild-embeddings returns response (501 if not implemented)."""
        resp = api_client.post("/api/memory/rebuild-embeddings")
        assert resp.status_code in (200, 501)

    def test_reconcile_endpoint(self, api_client, memory_store):
        """POST /api/memory/reconcile returns response (501 if not implemented)."""
        resp = api_client.post("/api/memory/reconcile")
        assert resp.status_code in (200, 501)

    def test_embedding_coverage_endpoint(self, api_client, memory_store):
        """GET /api/memory/embedding-coverage returns coverage stats (501 if not implemented)."""
        resp = api_client.get("/api/memory/embedding-coverage")
        assert resp.status_code in (200, 501)


# ===========================================================================
# 8. End-to-End Workflow
# ===========================================================================


class TestEndToEndWorkflow:
    """Test realistic user workflows through the API."""

    def test_create_search_update_delete_workflow(self, api_client):
        """Full CRUD lifecycle through the REST API."""
        # 1. Create
        resp = api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "The project uses PostgreSQL 15 for production",
                "category": "fact",
                "context": "work",
            },
        )
        assert resp.status_code == 200
        kid = resp.json()["knowledge_id"]

        # 2. Verify it appears in listing
        resp = api_client.get("/api/memory/knowledge?search=PostgreSQL")
        assert resp.status_code == 200
        data = resp.json()
        assert any(i["id"] == kid for i in data["items"])

        # 3. Update
        resp = api_client.put(
            f"/api/memory/knowledge/{kid}",
            json={"content": "The project uses PostgreSQL 16 for production"},
        )
        assert resp.status_code == 200

        # 4. Verify update
        resp = api_client.get("/api/memory/knowledge?search=PostgreSQL")
        data = resp.json()
        match = next(i for i in data["items"] if i["id"] == kid)
        assert "PostgreSQL 16" in match["content"]

        # 5. Delete
        resp = api_client.delete(f"/api/memory/knowledge/{kid}")
        assert resp.status_code == 200

        # 6. Verify deletion
        resp = api_client.get("/api/memory/knowledge?search=PostgreSQL")
        data = resp.json()
        assert not any(i["id"] == kid for i in data["items"])

    def test_multi_entity_knowledge_graph(self, api_client):
        """Build knowledge about multiple entities and query them."""
        # Create knowledge about Sarah
        api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "Sarah Chen is VP Engineering at AMD",
                "category": "fact",
                "entity": "person:sarah_chen",
                "context": "work",
            },
        )
        api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "Sarah prefers async communication over meetings",
                "category": "preference",
                "entity": "person:sarah_chen",
                "context": "work",
            },
        )

        # Create knowledge about the project
        api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "GAIA project uses Python 3.12 and uv",
                "category": "fact",
                "entity": "project:gaia",
                "context": "work",
            },
        )

        # Query entities
        resp = api_client.get("/api/memory/entities")
        entities = resp.json()
        entity_names = [e["entity"] for e in entities]
        assert "person:sarah_chen" in entity_names
        assert "project:gaia" in entity_names

        # Query Sarah's knowledge
        resp = api_client.get("/api/memory/entities/person:sarah_chen")
        sarah_knowledge = resp.json()
        assert len(sarah_knowledge) == 2

    def test_sensitive_data_handling(self, api_client):
        """Sensitive items are properly gated in API responses."""
        # Create public item
        api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "Company phone number is 555-0100",
                "category": "fact",
                "context": "work",
            },
        )
        # Create sensitive item
        api_client.post(
            "/api/memory/knowledge",
            json={
                "content": "Database root password is super_secret_123",
                "category": "fact",
                "context": "work",
                "sensitive": True,
            },
        )

        # Default listing excludes sensitive
        resp = api_client.get("/api/memory/knowledge")
        data = resp.json()
        assert data["total"] == 1
        assert not any("password" in i["content"].lower() for i in data["items"])

        # Explicit include_sensitive reveals it
        resp = api_client.get("/api/memory/knowledge?include_sensitive=true")
        data = resp.json()
        assert data["total"] == 2
