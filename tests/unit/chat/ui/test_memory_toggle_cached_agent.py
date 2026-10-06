# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Memory off / private chat applies to the next turn of an already-cached agent.

Each chat keeps its agent cached between turns, so a toggle flipped after the
first turn must reach that same agent: no memory written while off, and memory
back on the turn after it is switched on again. Background goal ticks
and scheduled runs follow the same rule. The embedder is mocked throughout.
"""

import asyncio
import sys
import threading
import types
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.ui._chat_helpers as helpers
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.ui.agent_loop import AgentLoop
from gaia.ui.database import ChatDatabase
from gaia.ui.dependencies import get_db
from gaia.ui.models import ChatRequest
from gaia.ui.routers import memory as memory_router
from gaia.ui.routers import sessions as sessions_router


class _Base:
    """The slice of the agent loop a turn needs: run the turn, then the hook."""

    def process_query(self, user_input, **_kwargs):
        self.turns_run += 1
        self.turn_result = _TOOL_REGISTRY["remember"]["function"](
            fact=f"fact number {self.turns_run}"
        )
        self._after_process_query(user_input, "ok")
        return {"result": "ok"}

    def warm_up(self, progress=None):
        return None


class _MemoryAgent(MemoryMixin, _Base):
    """A cached chat agent with a real memory store behind it."""

    def __init__(self, db_path):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self.rag = None
        self.console = None
        self._cancel_event = threading.Event()
        self.turn_result = None
        self.turns_run = 0
        self.init_memory(db_path=db_path, incognito=True)
        self._auto_extract_enabled = False  # no LLM extraction in unit tests

    def _register_tools(self):
        self.register_memory_tools()


def _knowledge(agent) -> int:
    return agent.memory_store.get_all_knowledge(limit=100)["total"]


def _turns(agent) -> int:
    return agent.memory_store.count_conversation_turns()


@pytest.fixture
def mocked_embedder():
    embedder = MagicMock()
    embedder.embed.return_value = [np.ones(768, dtype=np.float32).tolist()]
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(MemoryMixin, "_get_embedder", MagicMock(return_value=embedder))
        )
        cache = stack.enter_context(patch.object(MemoryMixin, "_get_embedding_cache"))
        cache.return_value.get.return_value = None
        for name in (
            "_backfill_embeddings",
            "_rebuild_faiss_index",
            "_rebuild_proc_faiss_index",
            "_run_memory_post_init",
        ):
            stack.enter_context(patch.object(MemoryMixin, name, return_value=0))
        stack.enter_context(
            patch(
                "gaia.agents.base.memory._system_context_is_enabled",
                return_value=False,
            )
        )
        yield


@pytest.fixture
def chat(tmp_path, monkeypatch, mocked_embedder):
    """A chat whose agent is already cached, plus the UI's two toggle routes."""
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    helpers._agent_cache.clear()

    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent = _MemoryAgent(tmp_path / "memory.db")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")

    app = FastAPI()
    app.include_router(memory_router.router)
    app.include_router(sessions_router.router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, headers={"X-Gaia-UI": "1"}) as client:
        yield SimpleNamespace(db=db, sid=session["id"], agent=agent, client=client)

    helpers._agent_cache.clear()
    db.close()


def _turn(chat, stream: bool) -> None:
    session = chat.db.get_session(chat.sid)
    request = ChatRequest(session_id=chat.sid, message="hi", stream=stream)
    if not stream:
        asyncio.run(helpers._get_chat_response(chat.db, session, request))
        return

    async def drain():
        run = SimpleNamespace(handler=None)
        async for _ in helpers._stream_chat_impl(run, chat.db, session, request):
            pass

    asyncio.run(drain())


def _set_memory(chat, enabled: bool) -> None:
    resp = chat.client.put("/api/memory/settings", json={"memory_enabled": enabled})
    assert resp.status_code == 200, resp.text


def _toggle_private(chat) -> None:
    resp = chat.client.patch(f"/api/sessions/{chat.sid}/private")
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("stream", [False, True], ids=["non_streaming", "streaming"])
def test_memory_off_then_on_applies_to_the_next_turn(chat, stream):
    _turn(chat, stream)
    assert chat.agent.turn_result["status"] == "stored"
    knowledge, turns = _knowledge(chat.agent), _turns(chat.agent)

    _set_memory(chat, False)
    _turn(chat, stream)

    assert chat.agent.turn_result["status"] == "skipped"
    assert "turned off in Settings" in chat.agent.turn_result["message"]
    assert (_knowledge(chat.agent), _turns(chat.agent)) == (knowledge, turns)

    _set_memory(chat, True)
    _turn(chat, stream)

    assert chat.agent.turn_result["status"] == "stored"
    assert _knowledge(chat.agent) == knowledge + 1
    assert _turns(chat.agent) > turns


@pytest.mark.parametrize("stream", [False, True], ids=["non_streaming", "streaming"])
def test_marking_a_used_chat_private_applies_to_the_next_turn(chat, stream):
    _turn(chat, stream)
    knowledge, turns = _knowledge(chat.agent), _turns(chat.agent)

    _toggle_private(chat)
    _turn(chat, stream)

    assert chat.agent.turn_result["status"] == "skipped"
    assert "private chat" in chat.agent.turn_result["message"]
    assert (_knowledge(chat.agent), _turns(chat.agent)) == (knowledge, turns)

    _toggle_private(chat)
    _turn(chat, stream)

    assert chat.agent.turn_result["status"] == "stored"


@pytest.fixture
def tick(chat, monkeypatch):
    # The tick imports the agent package before it checks the cache.
    fake_pkg = types.ModuleType("gaia_agent")
    fake_mod = types.ModuleType("gaia_agent.agent")
    fake_mod.GaiaAgent = fake_mod.GaiaAgentConfig = object
    monkeypatch.setitem(sys.modules, "gaia_agent", fake_pkg)
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", fake_mod)
    monkeypatch.setattr(AgentLoop, "_get_actionable_goals", lambda self: [])

    loop = AgentLoop()
    loop._db = chat.db
    goal = SimpleNamespace(priority="high", title="t", description="")

    def run():
        session = chat.db.get_session(chat.sid)
        asyncio.run(loop._execute_tick(chat.sid, session, [goal]))

    return run


def test_background_tick_in_a_private_chat_writes_no_memory(chat, tick):
    _turn(chat, stream=False)
    knowledge, turns = _knowledge(chat.agent), _turns(chat.agent)

    _toggle_private(chat)
    tick()

    assert chat.agent.turn_result["status"] == "skipped"
    assert "private chat" in chat.agent.turn_result["message"]
    assert (_knowledge(chat.agent), _turns(chat.agent)) == (knowledge, turns)


def test_background_tick_follows_the_memory_setting(chat, tick):
    _set_memory(chat, False)
    tick()
    assert chat.agent.turn_result["status"] == "skipped"
    assert "turned off in Settings" in chat.agent.turn_result["message"]

    _set_memory(chat, True)
    tick()
    assert chat.agent.turn_result["status"] == "stored"


@pytest.mark.parametrize("enabled", [False, True], ids=["memory_off", "memory_on"])
def test_scheduled_run_follows_the_memory_setting(monkeypatch, enabled):
    from gaia.ui.server import _run_scheduled_prompt

    built = {}

    class _Agent:
        def __init__(self, config):
            built["config"] = config
            self._incognito = config.memory_incognito

        def process_query(self, _prompt):
            return {"result": "done"}

    fake_mod = types.ModuleType("gaia_agent.agent")
    fake_mod.GaiaAgent = _Agent
    fake_mod.GaiaAgentConfig = SimpleNamespace
    monkeypatch.setitem(sys.modules, "gaia_agent", types.ModuleType("gaia_agent"))
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", fake_mod)

    db = ChatDatabase(":memory:")
    db.set_setting("memory_enabled", "true" if enabled else "false")

    assert _run_scheduled_prompt(db, "check the news") == "done"
    assert built["config"].memory_incognito is (not enabled)
    db.close()
