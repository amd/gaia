# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A background goal run that fails stops, and its goal says why (#5840).

A failed tick left its goals actionable, so the loop reran it at once until
the hourly budget was gone, and the goal sat at "queued" with no reason. On
the desktop app, where the flagship runs as a sidecar, every tick failed.
"""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.agents.base.goal_store import GoalStore
from gaia.ui.agent_loop import AgentLoop, AgentTrigger
from gaia.ui.database import ChatDatabase


@pytest.fixture
def gaia_home(tmp_path):
    home = tmp_path / ".gaia"
    (home / "chat").mkdir(parents=True)
    (home / "chat" / "initialized").touch()
    with patch.dict(os.environ, {"GAIA_HOME": str(home)}):
        yield home


@pytest.fixture
def loop(gaia_home):
    db = ChatDatabase(":memory:")
    loop = AgentLoop()
    loop._db = db
    loop._app_state = SimpleNamespace(tunnel=None, session_locks={})
    yield loop
    db.close()


def _queued_goal():
    return GoalStore().create_goal("Tidy notes", "Sort my notes", source="user")


def _run_followup(loop):
    async def followup():
        loop._running_lock = asyncio.Lock()
        await loop._process_trigger(AgentTrigger("user_message_followup", None))

    asyncio.run(followup())


def _sidecar_flagship(monkeypatch, relay):
    monkeypatch.setattr(
        helpers,
        "_agent_registry",
        SimpleNamespace(get=lambda _id: SimpleNamespace(is_sidecar=True)),
    )
    monkeypatch.setattr(helpers, "relay_one_prompt", relay)


def test_a_desktop_install_runs_the_tick_on_the_agent_binary(loop, monkeypatch):
    """The apps ship the agent as its own binary; goals run on it (#5840)."""
    goal = _queued_goal()
    prompts = []

    def relay(handler, prompt, **kwargs):
        prompts.append((prompt, kwargs["agent_type"]))
        # The agent called set_loop_state; the relay hands it to the loop.
        handler.loop_state_directive = {"directive": "idle", "reason": "done"}
        return "Sorted."

    _sidecar_flagship(monkeypatch, relay)
    _run_followup(loop)

    assert loop._calls_this_hour == 1
    [(prompt, agent_type)] = prompts
    assert "Tidy notes" in prompt and agent_type == "gaia"
    assert GoalStore().get_goal(goal.id).status != "failed"


def test_a_failed_tick_does_not_rerun_on_the_next_message(loop, monkeypatch):
    goal = _queued_goal()

    class FailingAgent:
        conversation_history = []
        device = "gpu"
        system_prompt = "test"

        def _register_tools(self):
            pass

        def process_query(self, query):
            raise RuntimeError("model server went away")

    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_get_cached_agent", lambda *args: FailingAgent())

    _run_followup(loop)
    _run_followup(loop)

    assert loop._calls_this_hour == 1
    failed = GoalStore().get_goal(goal.id)
    assert failed.status == "failed"
    assert "model server went away" in failed.progress_notes


def test_a_failure_keeps_the_progress_an_earlier_run_recorded(loop, monkeypatch):
    goal = _queued_goal()
    GoalStore().update_goal_status(
        goal.id, "queued", progress_notes="Read 3 of 5 files."
    )

    def relay(handler, prompt, **kwargs):
        raise RuntimeError("the GAIA agent could not be started")

    _sidecar_flagship(monkeypatch, relay)
    _run_followup(loop)

    failed = GoalStore().get_goal(goal.id)
    assert failed.status == "failed"
    assert failed.progress_notes.startswith("Read 3 of 5 files.\n")
    assert "the GAIA agent could not be started" in failed.progress_notes
    assert "pip install" not in failed.progress_notes


def test_cancelled_goal_and_queued_sibling_survive_a_stopped_tick(loop, monkeypatch):
    cancelled = _queued_goal()
    queued = _queued_goal()

    class CancelledAgent:
        conversation_history = []
        device = "gpu"
        system_prompt = "test"

        def _register_tools(self):
            pass

        def process_query(self, query):
            GoalStore().cancel_goal(cancelled.id)
            assert loop.cancel_goal(cancelled.id) is True
            raise RuntimeError("the request was cancelled")

    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_get_cached_agent", lambda *args: CancelledAgent())

    _run_followup(loop)

    assert GoalStore().get_goal(cancelled.id).status == "cancelled"
    assert GoalStore().get_goal(queued.id).status == "queued"


@pytest.mark.parametrize(
    "args, directive",
    [
        ({"state": "scheduled", "wake_in_seconds": 5}, ("scheduled", 30)),
        ({"state": "paused", "reason": "need input"}, ("paused", 0)),
        ({"state": "nonsense"}, ("idle", 0)),
    ],
)
def test_a_relayed_set_loop_state_reaches_the_loop(args, directive):
    """Relayed, set_loop_state runs on the sidecar's console, so the relay
    hands its call to the tick's handler, checked as the tool checks it."""
    from gaia.ui.sidecar.relay import _loop_state_directive

    got = _loop_state_directive(args)
    assert (got["directive"], got["wake_in_seconds"]) == directive
