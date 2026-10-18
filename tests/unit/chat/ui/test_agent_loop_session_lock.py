# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Goal ticks share the session agent safely (#4197).

A tick reuses the session's cached agent, so it must hold the same session
lock and turn-queue slot a user turn holds, skip the session while a turn is in
flight, and look the agent up by the session's real agent type and model — a
lookup under the wrong type evicts (and disconnects) the user's agent.
"""

import asyncio
import sys
import threading
import types
from unittest.mock import MagicMock

import pytest

import gaia.ui._chat_helpers as helpers
import gaia.ui.agent_loop as agent_loop_mod
from gaia.ui.agent_loop import AgentLoop, AgentTrigger
from gaia.ui.database import ChatDatabase
from gaia.ui.run_manager import run_manager
from gaia.ui.turn_queue import TurnQueue

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
    # The tick imports GaiaAgent before it checks the cache; a cache hit never uses it.
    fake_mod = types.ModuleType("gaia_agent.agent")
    fake_mod.GaiaAgent = fake_mod.GaiaAgentConfig = object
    monkeypatch.setitem(sys.modules, "gaia_agent", types.ModuleType("x"))
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", fake_mod)

    registry = MagicMock()
    registry.canonical_id.side_effect = lambda t: t
    registry.resolve_model.return_value = None
    monkeypatch.setattr(helpers, "_agent_registry", registry)

    initialized = tmp_path / ".gaia" / "chat" / "initialized"
    initialized.parent.mkdir(parents=True)
    initialized.touch()
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / ".gaia"))
    monkeypatch.setattr(AgentLoop, "_get_actionable_goals", lambda self: [_GOAL])

    monkeypatch.setattr(agent_loop_mod, "turn_queue", TurnQueue())
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
    loop._app_state = types.SimpleNamespace(tunnel=None, session_locks={})
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


async def test_tick_skips_while_another_session_holds_the_turn_queue(env):
    loop, session, agent = _make("chat")
    await agent_loop_mod.turn_queue.acquire("another-session")

    directive = await loop._run_step(AgentTrigger("idle_tick", session["id"]))

    assert directive.directive == "idle"
    assert agent.calls == []


async def test_tick_holds_lock_and_turn_slot_while_agent_runs(env):
    loop, session, agent = _make("chat")
    state = loop._app_state
    held = {}

    def _process_query(message):
        held["lock"] = state.session_locks[session["id"]].locked()
        held["sem"] = agent_loop_mod.turn_queue.locked()
        return "ok"

    agent.process_query = _process_query

    await loop._run_step(AgentTrigger("idle_tick", session["id"]))

    assert held == {"lock": True, "sem": True}
    assert not state.session_locks[session["id"]].locked()
    assert not agent_loop_mod.turn_queue.locked()


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


async def test_tick_without_a_cached_agent_builds_the_flagship(env, monkeypatch):
    built = []

    class FakeConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeGaiaAgent(_FakeAgent):
        def __init__(self, config):
            super().__init__(config.kwargs["model_id"])
            built.append((config, self))

    fake_mod = sys.modules["gaia_agent.agent"]
    monkeypatch.setattr(fake_mod, "GaiaAgent", FakeGaiaAgent, raising=False)
    monkeypatch.setattr(fake_mod, "GaiaAgentConfig", FakeConfig, raising=False)
    loop, session, _cached = _make("gaia")
    helpers._agent_cache.clear()

    await loop._run_step(AgentTrigger("idle_tick", session["id"]))

    assert len(built) == 1
    config, agent = built[0]
    assert config.kwargs["ui_session_id"] == session["id"]
    assert config.kwargs["silent_mode"] is True
    assert len(agent.calls) == 1


async def test_a_timed_out_tick_stuck_in_a_tool_frees_the_model_not_the_session(
    env, monkeypatch
):
    monkeypatch.setattr(agent_loop_mod, "_TICK_TIMEOUT", 0.2)
    monkeypatch.setattr(agent_loop_mod, "_TICK_STOP_GRACE_SECONDS", 0.2, raising=False)
    loop, session, agent = _make("chat")
    unstick = threading.Event()

    def hung_tool(_message):
        agent.console.print_tool_usage("run_shell_command")
        unstick.wait(30)  # A tool the stop can't reach.
        return "late"

    agent.process_query = hung_tool
    try:
        await asyncio.wait_for(
            loop._run_step(AgentTrigger("idle_tick", session["id"])), 5
        )
        assert not agent_loop_mod.turn_queue.locked()
        # The session's agent is still in use until the thread exits.
        lock = loop._app_state.session_locks[session["id"]]
        assert lock.locked()
        unstick.set()
        for _ in range(100):
            if not lock.locked():
                break
            await asyncio.sleep(0.02)
        assert not lock.locked()
    finally:
        unstick.set()


async def test_a_timed_out_tick_mid_model_call_keeps_the_model_until_it_stops(
    env, monkeypatch
):
    monkeypatch.setattr(agent_loop_mod, "_TICK_TIMEOUT", 0.2)
    monkeypatch.setattr(agent_loop_mod, "_TICK_STOP_GRACE_SECONDS", 0.2, raising=False)
    loop, session, agent = _make("chat")
    unstick = threading.Event()
    # Generating: no tool is running, so the GPU is still in use.
    agent.process_query = lambda _message: unstick.wait(30) and "late"
    try:
        step = asyncio.create_task(
            loop._run_step(AgentTrigger("idle_tick", session["id"]))
        )
        await asyncio.sleep(0.8)
        assert not step.done()
        assert agent_loop_mod.turn_queue.locked(), "freed the GPU mid-generation"
        unstick.set()
        await asyncio.wait_for(step, 5)
        assert not agent_loop_mod.turn_queue.locked()
    finally:
        unstick.set()


async def test_a_tick_cancelled_at_shutdown_keeps_its_session_until_it_stops(env):
    loop, session, agent = _make("chat")
    unstick = threading.Event()
    agent.process_query = lambda _message: unstick.wait(30) and "late"
    try:
        step = asyncio.create_task(
            loop._run_step(AgentTrigger("idle_tick", session["id"]))
        )
        await asyncio.sleep(0.3)
        step.cancel()
        with pytest.raises(asyncio.CancelledError):
            await step
        lock = loop._app_state.session_locks[session["id"]]
        assert lock.locked(), "released the session while its tick still runs"
        unstick.set()
        for _ in range(100):
            if not lock.locked():
                break
            await asyncio.sleep(0.02)
        assert not lock.locked()
    finally:
        unstick.set()
