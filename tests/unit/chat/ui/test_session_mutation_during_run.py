# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Session changes never pull the agent out from under a running turn (#4199).

Deleting a session, switching its agent/device/mail provider, or detaching a
document evicts the cached agent, which disconnects its MCP tools. While a
turn is running on that agent:

- DELETE cancels the turn and waits for it to finish before deleting and evicting.
- PUT (agent/device/mail provider) and document detach return 409, the same
  answer ``/api/chat/send`` gives for a busy session. Renames still work.
"""

import asyncio
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat, sessions
from gaia.ui.run_manager import RunManager, run_manager
from gaia.ui.server import create_app


class _Agent:
    """Cached agent that honours Stop only after its current tool returns."""

    def __init__(self):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self._mcp_manager = MagicMock()
        self.started = threading.Event()
        self.tool_done = threading.Event()

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.started.set()
        self.tool_done.wait(10)
        return "answer"


def _seed(db):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _Agent()
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    return session["id"], agent


# ── DELETE: cancel, then wait ───────────────────────────────────────────────


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    manager = RunManager()
    state = SimpleNamespace(
        session_locks={}, chat_semaphore=asyncio.BoundedSemaphore(1)
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    server = SimpleNamespace(_stream_chat_response=helpers._stream_chat_response)
    helpers._agent_cache.clear()
    with (
        patch.object(chat, "run_manager", manager),
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(chat, "_server_mod", return_value=server),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield db, manager, request
    helpers._agent_cache.clear()
    db.close()


@pytest.mark.allow_network
async def test_delete_waits_for_running_turn_before_evicting(runtime, caplog):
    db, manager, http_request = runtime
    sid, agent = _seed(db)

    response = await chat.send_message(
        ChatRequest(session_id=sid, message="hi", stream=True), http_request, db
    )
    disconnected = asyncio.Event()

    async def receive():
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            disconnected.set()

    await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    run = manager.get(sid)
    with (
        patch("gaia.ui.routers.sessions.run_manager", manager, create=True),
        caplog.at_level(logging.ERROR),
    ):
        try:
            assert await asyncio.to_thread(agent.started.wait, 5)
            delete = asyncio.create_task(sessions.delete_session(sid, http_request, db))
            await asyncio.sleep(0.3)

            assert not delete.done()
            assert run.handler.cancelled.is_set()
            agent._mcp_manager.disconnect_all.assert_not_called()
            assert sid in helpers._agent_cache
        finally:
            agent.tool_done.set()
        result = await asyncio.wait_for(delete, timeout=5)
        await asyncio.gather(run.task, return_exceptions=True)
        await response.background()

    assert result == {"deleted": True}
    assert db.get_session(sid) is None
    assert sid not in helpers._agent_cache
    agent._mcp_manager.disconnect_all.assert_called_once()
    assert not any("Chat streaming error" in r.getMessage() for r in caplog.records)


async def test_delete_waits_for_a_turn_holding_the_session_lock(runtime):
    db, _manager, http_request = runtime
    sid, agent = _seed(db)
    lock = http_request.app.state.session_locks.setdefault(sid, asyncio.Lock())
    await lock.acquire()

    delete = asyncio.create_task(sessions.delete_session(sid, http_request, db))
    await asyncio.sleep(0.1)
    assert not delete.done()
    assert db.get_session(sid) is not None
    agent._mcp_manager.disconnect_all.assert_not_called()

    lock.release()
    assert await asyncio.wait_for(delete, timeout=5) == {"deleted": True}
    assert db.get_session(sid) is None
    agent._mcp_manager.disconnect_all.assert_called_once()


# ── PUT / detach: 409 while busy ────────────────────────────────────────────


@pytest.fixture
def app_client():
    app = create_app(db_path=":memory:")
    client = TestClient(app)
    helpers._agent_cache.clear()
    sid, agent = _seed(app.state.db)
    yield client, sid, agent
    helpers._agent_cache.clear()


@pytest.mark.parametrize(
    "change", [{"agent_type": "chat"}, {"device": "gpu"}, {"mail_provider": "google"}]
)
def test_update_that_evicts_is_rejected_while_run_is_active(
    app_client, monkeypatch, change
):
    client, sid, agent = app_client
    monkeypatch.setitem(run_manager._runs, sid, object())

    resp = client.put(f"/api/sessions/{sid}", json=change)

    assert resp.status_code == 409
    assert sid in helpers._agent_cache
    agent._mcp_manager.disconnect_all.assert_not_called()


def test_rename_still_works_while_run_is_active(app_client, monkeypatch):
    client, sid, _agent = app_client
    monkeypatch.setitem(run_manager._runs, sid, object())

    resp = client.put(f"/api/sessions/{sid}", json={"title": "Renamed"})

    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed"


def test_detach_is_rejected_while_run_is_active(app_client, monkeypatch):
    client, sid, agent = app_client
    monkeypatch.setitem(run_manager._runs, sid, object())

    resp = client.delete(f"/api/sessions/{sid}/documents/doc-1")

    assert resp.status_code == 409
    assert sid in helpers._agent_cache
    agent._mcp_manager.disconnect_all.assert_not_called()


def test_detach_is_rejected_while_session_lock_is_held(app_client):
    client, sid, agent = app_client
    lock = asyncio.Lock()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(lock.acquire())
        client.app.state.session_locks[sid] = lock

        resp = client.delete(f"/api/sessions/{sid}/documents/doc-1")
    finally:
        loop.close()

    assert resp.status_code == 409
    agent._mcp_manager.disconnect_all.assert_not_called()
