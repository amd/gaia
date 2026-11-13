# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A goal that can't be done stops, says why, and its runs are paid for (#7995).

A goal whose file did not exist was run again every minute on the cloud model
(17 times in 16 minutes), stayed "Queued" forever, and none of those runs
counted toward the monthly spending limit.
"""

import asyncio
import os
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.agents.base.goal_store import GoalStore
from gaia.ui import spending
from gaia.ui.agent_loop import GOAL_MAX_TRIES, AgentLoop, AgentTrigger
from gaia.ui.database import ChatDatabase

FLASH = "fireworks.glm-5p3-flash"
MISSING = r"Not found: Q:\no-such-drive\plan-b2.txt"


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
    db.set_setting("custom_model", FLASH)
    loop = AgentLoop()
    loop._db = db
    loop._app_state = SimpleNamespace(tunnel=None, session_locks={})
    yield loop
    db.close()


def _goal():
    return GoalStore().create_goal(
        r"Summarize Q:\no-such-drive\plan-b2.txt",
        "Open that exact path and summarize it.",
        source="user",
    )


def _tick(loop):
    async def run():
        loop._running_lock = asyncio.Lock()
        await loop._process_trigger(AgentTrigger("idle_tick", None))

    asyncio.run(run())


def _due_now(goal_id):
    """Skip the wait before the next try, as if it had passed."""
    store = GoalStore()
    with store._lock:
        store._get_conn().execute(
            "UPDATE goals SET retry_at = ? WHERE id = ?", (time.time() - 1, goal_id)
        )
        store._get_conn().commit()


def _sidecar(monkeypatch, answer=MISSING, directive=None):
    runs = []

    def relay(handler, prompt, **kwargs):
        runs.append(prompt)
        if kwargs.get("stats") is not None:
            kwargs["stats"].update(input_tokens=12_000, output_tokens=400)
        if directive is not None:
            handler.loop_state_directive = directive
        return answer

    monkeypatch.setattr(
        helpers,
        "_agent_registry",
        SimpleNamespace(get=lambda _id: SimpleNamespace(is_sidecar=True)),
    )
    monkeypatch.setattr(helpers, "relay_one_prompt", relay)
    return runs


def test_an_unfinished_goal_waits_before_it_is_tried_again(loop, monkeypatch):
    goal = _goal()
    runs = _sidecar(monkeypatch)

    _tick(loop)
    _tick(loop)

    assert len(runs) == 1
    waiting = GoalStore().get_goal(goal.id)
    assert waiting.status == "queued"
    assert waiting.attempts == 1
    assert waiting.retry_at > time.time()
    assert MISSING in waiting.progress_notes


def test_a_goal_that_never_finishes_fails_with_the_reason(loop, monkeypatch):
    goal = _goal()
    runs = _sidecar(monkeypatch)

    for _ in range(GOAL_MAX_TRIES):
        _tick(loop)
        _due_now(goal.id)
    _tick(loop)

    assert len(runs) == GOAL_MAX_TRIES
    failed = GoalStore().get_goal(goal.id)
    assert failed.status == "failed"
    assert f"{GOAL_MAX_TRIES} tries" in failed.progress_notes
    assert MISSING in failed.progress_notes


def test_the_agent_can_fail_a_goal_it_cannot_do(loop, monkeypatch):
    goal = _goal()
    _sidecar(
        monkeypatch,
        directive={"directive": "idle", "reason": MISSING, "goal_outcome": "failed"},
    )

    _tick(loop)

    failed = GoalStore().get_goal(goal.id)
    assert failed.status == "failed"
    assert MISSING in failed.progress_notes


def test_the_agent_can_finish_a_goal(loop, monkeypatch):
    goal = _goal()
    _sidecar(
        monkeypatch,
        answer="Summarized.",
        directive={"directive": "idle", "reason": "done", "goal_outcome": "done"},
    )

    _tick(loop)

    assert GoalStore().get_goal(goal.id).status == "completed"


def test_checking_back_later_is_not_a_failed_try(loop, monkeypatch):
    goal = _goal()
    _sidecar(
        monkeypatch,
        answer="Build still running.",
        directive={
            "directive": "scheduled",
            "wake_in_seconds": 60,
            "reason": "waiting for the build",
        },
    )

    _tick(loop)

    held = GoalStore().get_goal(goal.id)
    assert held.status == "queued" and held.attempts == 0
    assert time.time() + 50 < held.retry_at < time.time() + 70


def test_a_run_that_times_out_counts_as_a_try_and_says_so(loop, monkeypatch):
    import gaia.ui.agent_loop as agent_loop_mod

    goal = _goal()
    monkeypatch.setattr(agent_loop_mod, "_TICK_TIMEOUT", 0.3)

    def slow_relay(handler, prompt, **kwargs):
        time.sleep(0.6)
        return "late"

    monkeypatch.setattr(
        helpers,
        "_agent_registry",
        SimpleNamespace(get=lambda _id: SimpleNamespace(is_sidecar=True)),
    )
    monkeypatch.setattr(helpers, "relay_one_prompt", slow_relay)

    _tick(loop)

    waiting = GoalStore().get_goal(goal.id)
    assert waiting.status == "queued" and waiting.attempts == 1
    assert "timed out" in waiting.progress_notes


def test_a_goal_run_counts_toward_cloud_spending(loop, monkeypatch):
    _goal()
    _sidecar(monkeypatch)

    _tick(loop)

    report = spending.month_report(loop._db)
    assert report["turns"] == 1
    [row] = report["by_model"]
    assert row["model"] == FLASH and row["usd"] > 0


def test_a_reached_limit_stops_goal_runs_and_says_so(loop, monkeypatch):
    goal = _goal()
    runs = _sidecar(monkeypatch)
    spending.set_limit(loop._db, 0.01)
    spending.record_turn(
        loop._db, FLASH, {"input_tokens": 1_000_000, "output_tokens": 100_000}
    )

    _tick(loop)

    assert runs == []
    held = GoalStore().get_goal(goal.id)
    assert held.status == "queued" and held.attempts == 0
    assert "monthly limit" in held.progress_notes
    assert "Settings › Model" in held.progress_notes


def test_an_existing_goal_list_gains_the_retry_columns(gaia_home):
    path = gaia_home / "goals.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE goals (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_approval',
            source TEXT NOT NULL DEFAULT 'user',
            mode_required TEXT NOT NULL DEFAULT 'goal_driven',
            approved_for_auto INTEGER NOT NULL DEFAULT 0,
            priority TEXT NOT NULL DEFAULT 'medium',
            progress_notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO goals VALUES ('g1', 'Old', 'd', 'queued', 'user',
            'goal_driven', 1, 'medium', '', '2026-01-01', '2026-01-01');
        """)
    conn.commit()
    conn.close()

    [goal] = GoalStore(db_path=path).get_actionable_goals()

    assert goal.id == "g1" and goal.attempts == 0 and goal.retry_at is None


@pytest.mark.parametrize(
    "sent, kept", [("done", "done"), ("failed", "failed"), ("finished", ""), (None, "")]
)
def test_the_relay_carries_the_goal_outcome(sent, kept):
    from gaia.ui.email_sidecar.relay import _loop_state_directive

    args = {"state": "idle", "reason": "r"}
    if sent is not None:
        args["goal_outcome"] = sent
    assert _loop_state_directive(args)["goal_outcome"] == kept
