# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Goal ticks share the session agent safely (#4197).

A tick reuses the session's cached agent, so it must hold the same session
lock and chat semaphore a user turn holds, skip the session while a turn is in
flight, and look the agent up by the session's real agent type and model — a
lookup under the wrong type evicts (and disconnects) the user's agent.
"""

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

import gaia.ui._chat_helpers as helpers
import gaia.ui.agent_loop as agent_loop_mod
from gaia.ui.agent_loop import AgentLoop, AgentTrigger
from gaia.ui.database import ChatDatabase
from gaia.ui.run_manager import run_manager

_GOAL = types.SimpleNamespace(priority="high", title="t", description="")


class _FakeAgent:
    def __init__(self, model_id):
        self.model_id = model_id
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self._mcp_manager = MagicMock()
        self.calls = []

    def _register_tools(self):
        pass

    def process_query(self, message):
        self.calls.append(message)
        return "ok"


@pytest.fixture
def env(monkeypatch, tmp_path):
    # The tick imports ChatAgent before it checks the cache; a cache hit never uses it.
    fake_mod = types.ModuleType("gaia_agent_chat.agent")
    fake_mod.ChatAgent = fake_mod.ChatAgentConfig = object
    monkeypatch.setitem(sys.modules, "gaia_agent_chat", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "gaia_agent_chat.agent", fake_mod)

    registry = MagicMock()
    registry.canonical_id.side_effect = lambda t: t
    registry.resolve_model.return_value = None
    monkeypatch.setattr(helpers, "_agent_registry", registry)

    initialized = tmp_path / ".gaia" / "chat" / "initialized"
    initialized.parent.mkdir(parents=True)
    initialized.touch()
    monkeypatch.setattr(agent_loop_mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(AgentLoop, "_get_actionable_goals", lambda self: [_GOAL])

    helpers._agent_cache.clear()
    yield
    helpers._agent_cache.clear()


def _make(agent_type="gaia"):
    db = ChatDatabase(":memory:")
    db.set_setting("agent_mode", "goal_driven")
    session = db.create_session(model="M-GGUF", agent_type=agent_type)
    agent = _FakeAgent("M-GGUF")
    helpers._store_agent(session["id"], "M-GGUF", [], agent, agent_type)
    loop = AgentLoop()
    loop._db = db
    loop._app_state = types.SimpleNamespace(
        tunnel=None, session_locks={}, chat_semaphore=asyncio.Semaphore(1)
    )
    return loop, session, agent


async def test_tick_reuses_gaia_agent_without_evicting_it(env):
    loop, session, agent = _make("gaia")

    await loop._run_step(AgentTrigger("idle_tick", session["id"]))

    assert helpers._agent_cache[session["id"]]["agent"] is agent
    agent._mcp_manager.disconnect_all.assert_not_called()
    assert len(agent.calls) == 1


async def test_tick_skips_session_whose_lock_is_held(env):
    loop, session, agent = _make("chat")
    lock = loop._app_state.session_locks.setdefault(session["id"], asyncio.Lock())
    await lock.acquire()

    directive = await loop._run_step(AgentTrigger("idle_tick", session["id"]))

    assert directive.directive == "idle"
    assert agent.calls == []
    assert loop._calls_this_hour == 0


async def test_tick_skips_session_with_active_run(env, monkeypatch):
    loop, session, agent = _make("chat")
    monkeypatch.setitem(run_manager._runs, session["id"], object())

    directive = await loop._run_step(AgentTrigger("idle_tick", session["id"]))

    assert directive.directive == "idle"
    assert agent.calls == []


async def test_tick_skips_while_another_session_holds_the_chat_semaphore(env):
    loop, session, agent = _make("chat")
    await loop._app_state.chat_semaphore.acquire()

    directive = await loop._run_step(AgentTrigger("idle_tick", session["id"]))

    assert directive.directive == "idle"
    assert agent.calls == []


async def test_tick_holds_lock_and_semaphore_while_agent_runs(env):
    loop, session, agent = _make("chat")
    state = loop._app_state
    held = {}

    def _process_query(message):
        held["lock"] = state.session_locks[session["id"]].locked()
        held["sem"] = state.chat_semaphore.locked()
        return "ok"

    agent.process_query = _process_query

    await loop._run_step(AgentTrigger("idle_tick", session["id"]))

    assert held == {"lock": True, "sem": True}
    assert not state.session_locks[session["id"]].locked()
    assert not state.chat_semaphore.locked()


async def test_followup_waits_for_its_own_turn_to_release_the_session(env):
    loop, session, agent = _make("chat")
    lock = loop._app_state.session_locks.setdefault(session["id"], asyncio.Lock())
    await lock.acquire()
    asyncio.get_running_loop().call_later(0.2, lock.release)

    await loop._run_step(AgentTrigger("user_message_followup", session["id"]))

    assert len(agent.calls) == 1


async def test_followup_gives_up_when_session_stays_busy(env, monkeypatch):
    monkeypatch.setattr(agent_loop_mod, "_FOLLOWUP_GRACE_SECONDS", 0.2)
    loop, session, agent = _make("chat")
    lock = loop._app_state.session_locks.setdefault(session["id"], asyncio.Lock())
    await lock.acquire()

    directive = await loop._run_step(
        AgentTrigger("user_message_followup", session["id"])
    )

    assert directive.reason == "session busy"
    assert agent.calls == []
