# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""AgentLoop agent-mode gate tests (#2005).

The 'autonomous' mode's observation cycle is unimplemented, so the loop must
not pretend it exists: the default is 'goal_driven', legacy-stored
'autonomous' values normalize to 'goal_driven' with a loud warning, and the
loop's behavior for a legacy 'autonomous' value is bit-identical to
'goal_driven' (idle when no goals — no phantom observation cycle).
"""

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

    def list_sessions(self, limit=20):
        return self._sessions

    def get_session(self, session_id):
        for s in self._sessions:
            if s["id"] == session_id:
                return s
        return None


@pytest.fixture(autouse=True)
def _reset_warned_flag():
    agent_loop_mod._warned_legacy_autonomous = False
    yield
    agent_loop_mod._warned_legacy_autonomous = False


class TestResolveAgentMode:
    def test_default_is_goal_driven(self):
        assert resolve_agent_mode(FakeDB()) == "goal_driven"

    def test_manual_and_goal_driven_pass_through(self):
        assert resolve_agent_mode(FakeDB({"agent_mode": "manual"})) == "manual"
        assert (
            resolve_agent_mode(FakeDB({"agent_mode": "goal_driven"})) == "goal_driven"
        )

    def test_legacy_autonomous_normalizes_with_warning(self, caplog):
        with caplog.at_level("WARNING", logger="gaia.ui.agent_loop"):
            mode = resolve_agent_mode(FakeDB({"agent_mode": "autonomous"}))
        assert mode == "goal_driven"
        assert any("2005" in r.message for r in caplog.records)
        assert any("not implemented" in r.message for r in caplog.records)

    def test_legacy_autonomous_warns_only_once(self, caplog):
        db = FakeDB({"agent_mode": "autonomous"})
        with caplog.at_level("WARNING", logger="gaia.ui.agent_loop"):
            resolve_agent_mode(db)
            resolve_agent_mode(db)
        warnings = [r for r in caplog.records if "2005" in r.message]
        assert len(warnings) == 1


class TestRunStepModeGate:
    """_run_step must treat legacy 'autonomous' exactly as 'goal_driven'."""

    def _make_loop(self, db):
        loop = AgentLoop()
        loop._db = db
        loop._app_state = type("S", (), {"tunnel": None})()
        return loop

    async def _run(self, mode, tmp_path):
        settings = {"agent_mode": mode} if mode else {}
        loop = self._make_loop(FakeDB(settings))
        initialized = tmp_path / ".gaia" / "chat" / "initialized"
        initialized.parent.mkdir(parents=True, exist_ok=True)
        initialized.touch()
        with patch("gaia.ui.agent_loop.Path.home", return_value=tmp_path):
            with patch.object(AgentLoop, "_get_actionable_goals", return_value=[]):
                trigger = agent_loop_mod.AgentTrigger("idle_tick", None)
                return await loop._run_step(trigger)

    async def test_manual_pauses(self, tmp_path):
        directive = await self._run("manual", tmp_path)
        assert directive.directive == "paused"

    async def test_goal_driven_idles_without_goals(self, tmp_path):
        directive = await self._run("goal_driven", tmp_path)
        assert directive.directive == "idle"

    async def test_legacy_autonomous_identical_to_goal_driven(self, tmp_path):
        """No phantom observation cycle: legacy 'autonomous' == 'goal_driven'."""
        auto = await self._run("autonomous", tmp_path)
        goal = await self._run("goal_driven", tmp_path)
        assert (auto.directive, auto.wake_in_seconds, auto.reason) == (
            goal.directive,
            goal.wake_in_seconds,
            goal.reason,
        )

    async def test_unset_mode_defaults_to_goal_driven(self, tmp_path):
        directive = await self._run(None, tmp_path)
        assert directive.directive == "idle"
