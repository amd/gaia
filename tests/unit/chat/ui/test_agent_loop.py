# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""AgentLoop agent-mode gate tests (#2005).

The 'autonomous' mode's observation cycle is unimplemented, so the loop must
not pretend it exists: the default is 'goal_driven', and a legacy stored
'autonomous' is migrated to 'goal_driven' when the database opens (see
test_database.py). A goal store that cannot be read fails the tick loudly
rather than looking like "no goals".
"""

import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import gaia.ui.agent_loop as agent_loop_mod
from gaia.ui.agent_loop import AgentLoop, resolve_agent_mode


class FakeDB:
    """Minimal stand-in for ChatDatabase settings/session reads."""

    def __init__(self, settings=None, sessions=None):
        self._settings = settings or {}
        self._sessions = sessions if sessions is not None else [{"id": "s1"}]

    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self._settings[key] = value

    def create_session(self, title=None, kind=None):
        session = {"id": f"s{len(self._sessions) + 1}", "title": title, "kind": kind}
        self._sessions.append(session)
        return session

    def list_sessions(self, limit=20):
        return self._sessions

    def get_session(self, session_id):
        for s in self._sessions:
            if s["id"] == session_id:
                return s
        return None


class TestResolveAgentMode:
    def test_default_is_goal_driven(self):
        assert resolve_agent_mode(FakeDB()) == "goal_driven"

    def test_manual_and_goal_driven_pass_through(self):
        assert resolve_agent_mode(FakeDB({"agent_mode": "manual"})) == "manual"
        assert (
            resolve_agent_mode(FakeDB({"agent_mode": "goal_driven"})) == "goal_driven"
        )


class TestGoalStoreFailure:
    """An unreadable goal store must not read as "nothing to do"."""

    def test_unreadable_goal_store_raises_into_the_tick(self, tmp_path):
        with (
            patch.dict(os.environ, {"GAIA_HOME": str(tmp_path / ".gaia")}),
            patch(
                "gaia.agents.base.goal_store.GoalStore.get_actionable_goals",
                side_effect=sqlite3.OperationalError("database is locked"),
            ),
        ):
            with pytest.raises(RuntimeError, match="database is locked") as excinfo:
                AgentLoop()._get_actionable_goals()
        assert "goals.db" in str(excinfo.value)

    def test_readable_goal_store_returns_its_goals(self, tmp_path):
        with (
            patch.dict(os.environ, {"GAIA_HOME": str(tmp_path / ".gaia")}),
            patch(
                "gaia.agents.base.goal_store.GoalStore.get_actionable_goals",
                return_value=["g1", "g2"],
            ),
        ):
            assert AgentLoop()._get_actionable_goals() == ["g1", "g2"]

    def test_failure_is_kept_for_the_ui_until_the_store_opens(self, tmp_path):
        loop = AgentLoop()
        target = "gaia.agents.base.goal_store.GoalStore.get_actionable_goals"
        with patch.dict(os.environ, {"GAIA_HOME": str(tmp_path / ".gaia")}):
            with patch(
                target,
                side_effect=sqlite3.DatabaseError("file is not a database"),
            ):
                with pytest.raises(RuntimeError) as excinfo:
                    loop._get_actionable_goals()
            assert loop.goal_store_error == str(excinfo.value)
            # A file that is not a database is not fixed by a restart.
            assert loop.goal_store_damaged is True
            assert "damaged" in loop.goal_store_error
            assert "Start a new goal list" in loop.goal_store_error
            assert "restart" not in loop.goal_store_error.lower()
            with patch(target, return_value=[]):
                loop._get_actionable_goals()
        assert loop.goal_store_error is None
        assert loop.goal_store_damaged is False

    def test_a_locked_goal_store_says_to_close_the_other_window(self, tmp_path):
        loop = AgentLoop()
        target = "gaia.agents.base.goal_store.GoalStore.get_actionable_goals"
        with patch.dict(os.environ, {"GAIA_HOME": str(tmp_path / ".gaia")}):
            with patch(
                target, side_effect=sqlite3.OperationalError("database is locked")
            ):
                with pytest.raises(RuntimeError):
                    loop._get_actionable_goals()
        assert loop.goal_store_damaged is False
        assert "close other GAIA windows" in loop.goal_store_error


class TestRunStepModeGate:
    """_run_step pauses on 'manual' and idles without goals otherwise."""

    def _make_loop(self, db):
        loop = AgentLoop()
        loop._db = db
        loop._app_state = SimpleNamespace(tunnel=None, session_locks={})
        return loop

    async def _run(self, mode, tmp_path):
        settings = {"agent_mode": mode} if mode else {}
        loop = self._make_loop(FakeDB(settings))
        initialized = tmp_path / ".gaia" / "chat" / "initialized"
        initialized.parent.mkdir(parents=True, exist_ok=True)
        initialized.touch()
        with patch.dict(os.environ, {"GAIA_HOME": str(tmp_path / ".gaia")}):
            with patch.object(AgentLoop, "_get_actionable_goals", return_value=[]):
                trigger = agent_loop_mod.AgentTrigger("idle_tick", None)
                return await loop._run_step(trigger)

    async def test_manual_pauses(self, tmp_path):
        directive = await self._run("manual", tmp_path)
        assert directive.directive == "paused"

    async def test_goal_driven_idles_without_goals(self, tmp_path):
        directive = await self._run("goal_driven", tmp_path)
        assert directive.directive == "idle"

    async def test_unset_mode_defaults_to_goal_driven(self, tmp_path):
        directive = await self._run(None, tmp_path)
        assert directive.directive == "idle"

    async def test_gaiacpp_runs_queued_goals_and_marks_completed(
        self, tmp_path, monkeypatch
    ):
        import gaia.config as config_mod
        import gaia.ui._chat_helpers as helpers
        from gaia.agents.base.goal_store import GoalStore
        from gaia.ui.database import ChatDatabase
        from gaia.ui.sidecar.relay import _dispatch_one

        gaia_dir = tmp_path / ".gaia"
        initialized = gaia_dir / "chat" / "initialized"
        initialized.parent.mkdir(parents=True, exist_ok=True)
        initialized.touch()
        config_file = gaia_dir / "config.json"
        config_file.write_text('{"flagship_agent": "gaiacpp"}\n', encoding="utf-8")
        monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", config_file)
        # Even when gaiapy is importable in-process (is_sidecar=False), gaiacpp must relay.
        monkeypatch.setattr(
            helpers,
            "_agent_registry",
            SimpleNamespace(get=lambda _id: SimpleNamespace(is_sidecar=False)),
        )

        relayed = []

        def fake_relay(handler, prompt, **kwargs):
            relayed.append((prompt, kwargs["agent_type"]))
            _dispatch_one(
                handler,
                {
                    "type": "tool_call",
                    "tool": "set_loop_state",
                    "args": {
                        "state": "idle",
                        "goal_outcome": "done",
                        "reason": "14 * 12 = 168",
                    },
                },
            )
            _dispatch_one(
                handler,
                {
                    "type": "tool_result",
                    "tool": "set_loop_state",
                    "data": {
                        "status": "ok",
                        "state": "idle",
                        "goal_outcome": "done",
                        "reason": "14 * 12 = 168",
                        "wake_in_seconds": 0,
                        "message": "Loop state set to 'idle'. Goal marked done. Reason: 14 * 12 = 168",
                    },
                },
            )
            return "14 * 12 = 168."

        monkeypatch.setattr(helpers, "relay_one_prompt", fake_relay)

        db = ChatDatabase(":memory:")
        try:
            loop = self._make_loop(db)
            with patch.dict(os.environ, {"GAIA_HOME": str(gaia_dir)}):
                goal = GoalStore().create_goal(
                    "Compute 14 * 12",
                    "Compute 14 * 12 and mark this goal completed",
                    source="user",
                )
                directive = await loop._run_step(
                    agent_loop_mod.AgentTrigger("idle_tick", None)
                )
                settled = GoalStore().get_goal(goal.id)
        finally:
            db.close()

        assert directive.directive == "idle"
        assert directive.reason == "14 * 12 = 168"
        assert len(relayed) == 1
        assert relayed[0][1] == "gaia"
        assert "Compute 14 * 12" in relayed[0][0]
        assert loop._calls_this_hour == 1
        assert settled is not None
        assert settled.status == "completed"
        assert "14 * 12 = 168" in (settled.progress_notes or "")


class TestHourlyBudgetOnlySpentOnRealTicks:
    """The hourly call budget must only be spent when a tick actually reaches
    _execute_tick, not on idle no-ops (no session, or no actionable goals).
    See #3743 for the full production log evidence.
    """

    def _make_loop(self, db):
        loop = AgentLoop()
        loop._db = db
        loop._app_state = SimpleNamespace(tunnel=None, session_locks={})
        return loop

    async def _run(self, loop, tmp_path):
        initialized = tmp_path / ".gaia" / "chat" / "initialized"
        initialized.parent.mkdir(parents=True, exist_ok=True)
        initialized.touch()
        with patch.dict(os.environ, {"GAIA_HOME": str(tmp_path / ".gaia")}):
            trigger = agent_loop_mod.AgentTrigger("idle_tick", None)
            return await loop._run_step(trigger)

    async def test_idle_tick_with_no_goals_does_not_spend_budget(self, tmp_path):
        loop = self._make_loop(FakeDB({"agent_mode": "goal_driven"}))
        with patch.object(AgentLoop, "_get_actionable_goals", return_value=[]):
            for _ in range(5):
                directive = await self._run(loop, tmp_path)
                assert directive.directive == "idle"
        assert loop._calls_this_hour == 0

    async def test_idle_tick_with_no_goals_creates_no_background_chat(self, tmp_path):
        db = FakeDB({"agent_mode": "goal_driven"}, sessions=[])
        loop = self._make_loop(db)
        with patch.object(AgentLoop, "_get_actionable_goals", return_value=[]):
            await self._run(loop, tmp_path)
        assert db._sessions == []

    async def test_idle_tick_with_no_goals_never_hits_rate_limit(self, tmp_path):
        """Regression: previously this would exhaust the budget purely on
        no-op ticks and start returning 'hourly rate limit' instead of the
        honest 'nothing to do' idle."""
        loop = self._make_loop(FakeDB({"agent_mode": "goal_driven"}))
        with patch.object(AgentLoop, "_get_actionable_goals", return_value=[]):
            for _ in range(agent_loop_mod._HOURLY_LIMIT + 10):
                directive = await self._run(loop, tmp_path)
                assert directive.directive == "idle"
                assert directive.reason != "hourly rate limit"

    async def test_real_tick_does_spend_budget(self, tmp_path):
        """A tick that actually reaches _execute_tick must still count."""
        loop = self._make_loop(FakeDB({"agent_mode": "goal_driven"}))
        with (
            patch.object(
                AgentLoop, "_get_actionable_goals", return_value=[{"id": "g1"}]
            ),
            patch.object(
                AgentLoop,
                "_execute_tick",
                return_value=agent_loop_mod.LoopDirective("idle"),
            ),
        ):
            await self._run(loop, tmp_path)
        assert loop._calls_this_hour == 1

    async def test_rate_limit_still_throttles_real_ticks(self, tmp_path):
        """The limit must still bind once real work is actually happening —
        this fix must not disable throttling, only stop it firing on no-ops."""
        loop = self._make_loop(FakeDB({"agent_mode": "goal_driven"}))
        with (
            patch.object(
                AgentLoop, "_get_actionable_goals", return_value=[{"id": "g1"}]
            ),
            patch.object(
                AgentLoop,
                "_execute_tick",
                return_value=agent_loop_mod.LoopDirective("idle"),
            ),
        ):
            for _ in range(agent_loop_mod._HOURLY_LIMIT):
                await self._run(loop, tmp_path)
            limited = await self._run(loop, tmp_path)
        assert limited.reason == "hourly rate limit"
