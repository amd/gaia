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
import gaia.ui.agent_loop as agent_loop_mod
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


def test_a_desktop_install_fails_the_goal_once_and_says_why(loop, monkeypatch):
    goal = _queued_goal()
    sidecar_flagship = SimpleNamespace(is_sidecar=True)
    monkeypatch.setattr(
        helpers, "_agent_registry", SimpleNamespace(get=lambda _id: sidecar_flagship)
    )

    _run_followup(loop)

    assert loop._calls_this_hour == 1
    failed = GoalStore().get_goal(goal.id)
    assert failed.status == "failed"
    assert agent_loop_mod.SIDECAR_TICKS_UNSUPPORTED in failed.progress_notes
    assert "pip install" not in failed.progress_notes


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
