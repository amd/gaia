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
    """One turn stored the way the chat's own agent stores it.

    ``session_id=None`` is a pre-#5931 agent, which filed under a random id.
    Returns the id the agent filed under.
    """
    agent = _Agent()
    with patch.object(MemoryMixin, "_start_memory_embedder", return_value=True):
        agent.init_memory(db_path=store._db_path, session_id=session_id)
    agent._after_process_query(user, answer)
    agent.memory_store.log_tool_call(
        session_id=agent.memory_session_id,
        tool_name="web_search",
        args={"query": user},
        result_summary=answer,
        success=True,
    )
    filed_under = agent.memory_session_id
    agent.memory_store.close()
    return filed_under


def _tool_sessions(store):
    rows = store._conn.execute("SELECT session_id FROM tool_history").fetchall()
    return sorted(r[0] for r in rows)


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
    assert _tool_sessions(store) == [kept]
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
        "delete_session",
        side_effect=sqlite3.OperationalError("database is locked"),
    ):
        resp = client.delete(f"/api/sessions/{sid}")

    assert resp.status_code == 500
    assert "was not deleted" in resp.json()["detail"]
    assert client.get(f"/api/sessions/{sid}").status_code == 200
    assert _history_ids(client) == [sid]

    # The retry finishes the job.
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert _history_ids(client) == []


def test_the_chat_agent_files_memory_under_the_chat_id(store):
    """ChatAgent hands init_memory its UI session id, even across a rebuild."""
    from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

    seen = []

    def fake_init(self, **kwargs):
        seen.append(kwargs.get("session_id"))
        raise RuntimeError("stop after init_memory")

    with patch.object(ChatAgent, "init_memory", fake_init):
        for _ in range(2):
            with pytest.raises(RuntimeError, match="stop after init_memory"):
                ChatAgent(ChatAgentConfig(ui_session_id="chat-123", silent_mode=True))
    assert seen == ["chat-123", "chat-123"]


def test_deleting_all_chats_empties_history_and_keeps_facts(client, store):
    ids = [client.post("/api/sessions", json={}).json()["id"] for _ in range(3)]
    for i, sid in enumerate(ids):
        _chat_turn(store, sid, f"question {i}", f"answer {i}")
    store.store(category="fact", content="User works at AMD")

    for sid in ids:
        assert client.delete(f"/api/sessions/{sid}").status_code == 200

    assert _history_ids(client) == []
    assert _tool_sessions(store) == []
    assert [f["content"] for f in store.get_by_category("fact")] == [
        "User works at AMD"
    ]


def test_a_rebuilt_agent_files_under_the_same_chat(client, store):
    """Turns from two agents built for one chat are deleted together."""
    sid = client.post("/api/sessions", json={}).json()["id"]
    _chat_turn(store, sid, "first build", "one")
    _chat_turn(store, sid, "after a model switch", "two")
    assert _history_ids(client) == [sid]

    client.delete(f"/api/sessions/{sid}")

    assert _history_ids(client) == []


def test_unknown_chat_id_deletes_no_memory(client, store):
    """A delete for an id the UI does not know is a 404 and touches nothing,
    even when memory holds turns under that id (e.g. a TUI session)."""
    other = "not-an-agent-ui-chat"
    _chat_turn(store, other, "tui question", "tui answer")

    assert client.delete(f"/api/sessions/{other}").status_code == 404

    assert _history_ids(client) == [other]
    assert _tool_sessions(store) == [other]


def test_memory_from_before_the_fix_stays_until_pruned(client, store):
    """Turns a pre-fix agent filed under a random id cannot be tied to a chat:
    deleting the chat leaves them, and age pruning or Delete all memories
    removes them."""
    sid = client.post("/api/sessions", json={}).json()["id"]
    legacy = _chat_turn(store, None, "old pineapple", "old answer")
    _chat_turn(store, sid, "new pineapple", "new answer")
    assert legacy != sid

    client.delete(f"/api/sessions/{sid}")

    assert _history_ids(client) == [legacy]
    assert _tool_sessions(store) == [legacy]

    resp = client.delete("/api/memory/all", headers={"X-Gaia-UI": "1"})
    assert resp.status_code == 200
    assert _history_ids(client) == []
    assert _tool_sessions(store) == []
