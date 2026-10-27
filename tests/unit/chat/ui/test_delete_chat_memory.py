# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deleting a chat deletes its conversation from Memory (#5931).

The agent built for a chat files its memory turns under the chat's id, so the
delete can find them. Facts learned from the chat stay in "About you".
"""

import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.ui.routers import memory as memory_router
from gaia.ui.server import create_app


class _Agent(MemoryMixin):
    pass


@pytest.fixture
def store(tmp_path):
    store = MemoryStore(db_path=tmp_path / "memory.db")
    original = memory_router._store
    memory_router._store = store
    yield store
    memory_router._store = original
    store.close()


@pytest.fixture
def client(store):
    return TestClient(create_app(db_path=":memory:"))


def _chat_turn(store, session_id, user, answer):
    """One turn stored the way the chat's own agent stores it."""
    agent = _Agent()
    with patch.object(MemoryMixin, "_start_memory_embedder", return_value=True):
        agent.init_memory(db_path=store._db_path, session_id=session_id)
    agent._after_process_query(user, answer)
    agent.memory_store.close()


def _history_ids(client):
    return [c["session_id"] for c in client.get("/api/memory/conversations").json()]


def test_deleting_a_chat_removes_it_from_conversation_history(client, store):
    gone = client.post("/api/sessions", json={}).json()["id"]
    kept = client.post("/api/sessions", json={}).json()["id"]
    _chat_turn(store, gone, "say pineapple", "pineapple")
    _chat_turn(store, kept, "say mango", "mango")
    store.store(category="fact", content="User likes pineapple")
    assert sorted(_history_ids(client)) == sorted([gone, kept])

    assert client.delete(f"/api/sessions/{gone}").status_code == 200

    assert _history_ids(client) == [kept]
    assert client.get(f"/api/memory/conversations/{gone}").json() == []
    search = client.get(
        "/api/memory/conversations/search", params={"query": "pineapple"}
    )
    assert search.json() == []
    facts = store.get_by_category("fact")
    assert [f["content"] for f in facts] == ["User likes pineapple"]


def test_a_memory_failure_keeps_the_chat_and_says_so(client, store):
    sid = client.post("/api/sessions", json={}).json()["id"]
    _chat_turn(store, sid, "say pineapple", "pineapple")

    with patch.object(
        MemoryStore,
        "delete_session_conversations",
        side_effect=sqlite3.OperationalError("database is locked"),
    ):
        resp = client.delete(f"/api/sessions/{sid}")

    assert resp.status_code == 500
    assert "was not deleted" in resp.json()["detail"]
    assert client.get(f"/api/sessions/{sid}").status_code == 200
    assert _history_ids(client) == [sid]
