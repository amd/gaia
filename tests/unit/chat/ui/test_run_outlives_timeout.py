# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A turn keeps its session until the worker thread has exited (#4198).

When a turn times out or its run is torn down, the worker thread is still
running tools on the session's cached agent. The session lock, chat semaphore,
and run registration must stay held until that thread is gone, or the next
turn runs concurrently on the same agent.
"""

import asyncio
import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import gaia.ui.agent_loop as agent_loop_mod
from gaia.ui import _chat_helpers as helpers
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.routers import chat
from gaia.ui.run_manager import RunManager

pytestmark = [pytest.mark.asyncio, pytest.mark.allow_network]


class _BlockingAgent:
    """Cached agent whose turn runs until the test releases it."""

    def __init__(self):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self.started = threading.Event()
        self.release = threading.Event()

    def _register_tools(self):
        pass

    def process_query(self, _message):
        self.started.set()
        self.release.wait(10)
        return "late answer"


@pytest.fixture
def runtime():
    db = ChatDatabase(":memory:")
    manager = RunManager()
    state = SimpleNamespace(
        session_locks={}, chat_semaphore=asyncio.BoundedSemaphore(1)
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    server = SimpleNamespace(
        _stream_chat_response=helpers._stream_chat_response,
        _get_chat_response=helpers._get_chat_response,
    )
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


def _seed(db):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _BlockingAgent()
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    return session["id"], agent


async def test_non_streaming_timeout_holds_session_until_worker_exits(runtime):
    db, _manager, http_request = runtime
    state = http_request.app.state
    sid, agent = _seed(db)

    with patch.object(helpers, "_CHAT_TIMEOUT_SECONDS", 0.1):
        task = asyncio.create_task(
            chat.send_message(
                ChatRequest(session_id=sid, message="hi", stream=False),
                http_request,
                db,
            )
        )
        try:
            assert await asyncio.to_thread(agent.started.wait, 5)
            await asyncio.sleep(0.4)

            assert not task.done()
            assert state.session_locks[sid].locked()
            assert state.chat_semaphore.locked()
            # The worker was told to stop at its next step boundary.
            assert agent._cancel_event is not None and agent._cancel_event.is_set()
        finally:
            agent.release.set()
        response = await asyncio.wait_for(task, timeout=5)

    assert "took too long" in response.content
    assert not state.session_locks[sid].locked()
    assert not state.chat_semaphore.locked()


async def test_torn_down_stream_holds_session_until_producer_exits(runtime):
    db, manager, http_request = runtime
    state = http_request.app.state
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
    try:
        assert await asyncio.to_thread(agent.started.wait, 5)
        run.task.cancel()
        await asyncio.sleep(0.4)

        assert manager.is_running(sid)
        assert state.session_locks[sid].locked()
        assert state.chat_semaphore.locked()
    finally:
        agent.release.set()
    await asyncio.gather(run.task, return_exceptions=True)
    await response.background()

    assert not manager.is_running(sid)
    assert not state.session_locks[sid].locked()
    assert not state.chat_semaphore.locked()


async def test_timed_out_goal_tick_holds_session_until_worker_exits(
    runtime, monkeypatch, tmp_path
):
    db, _manager, _http_request = runtime
    sid, agent = _seed(db)
    db.set_setting("agent_mode", "goal_driven")
    session = db.get_session(sid)

    # The tick imports ChatAgent before it checks the cache; a cache hit never uses it.
    fake_mod = types.ModuleType("gaia_agent_chat.agent")
    fake_mod.ChatAgent = fake_mod.ChatAgentConfig = object
    monkeypatch.setitem(sys.modules, "gaia_agent_chat", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "gaia_agent_chat.agent", fake_mod)
    (tmp_path / ".gaia" / "chat").mkdir(parents=True)
    (tmp_path / ".gaia" / "chat" / "initialized").touch()
    monkeypatch.setattr(agent_loop_mod.Path, "home", lambda: tmp_path)
    goal = types.SimpleNamespace(priority="high", title="t", description="")
    monkeypatch.setattr(
        agent_loop_mod.AgentLoop, "_get_actionable_goals", lambda s: [goal]
    )
    monkeypatch.setattr(agent_loop_mod, "_TICK_TIMEOUT", 0.1)

    loop = agent_loop_mod.AgentLoop()
    loop._db = db
    loop._app_state = SimpleNamespace(
        tunnel=None, session_locks={}, chat_semaphore=asyncio.Semaphore(1)
    )
    tick = asyncio.create_task(
        loop._run_step(agent_loop_mod.AgentTrigger("idle_tick", session["id"]))
    )
    try:
        assert await asyncio.to_thread(agent.started.wait, 5)
        await asyncio.sleep(0.4)

        assert not tick.done()
        assert loop._app_state.session_locks[sid].locked()
        assert agent._cancel_event.is_set()
    finally:
        agent.release.set()
    await asyncio.wait_for(tick, timeout=5)

    assert not loop._app_state.session_locks[sid].locked()
    assert not loop._app_state.chat_semaphore.locked()
