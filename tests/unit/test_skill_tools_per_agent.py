# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill one chat loaded must not reach another chat's agent (#8819).

Skill tools register in the process-wide registry, so a chat started after
another loaded ``browser-pane`` used to see its tools without the skill being
loaded. Calling one ran the tool's permission check outside any skill context,
and the ``SkillContextError`` failed the whole turn.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.agents.base import tools as tools_mod
from gaia.agents.base.agent import Agent
from gaia.agents.base.verification import EXECUTED_KEY
from gaia.agents.tools.browser_use_tools import BrowserUseToolsMixin
from gaia.browser import driver as browser_driver

_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "hub" / "skills"
_TABS = "browser-pane/browser_tabs"


class _Chat(Agent, BrowserUseToolsMixin):
    """One chat's agent, freezing its tools at construction like ChatAgent."""

    SNAPSHOT = True

    def __init__(self, **kwargs):
        self.config = SimpleNamespace(ui_session_id="chat-1")
        super().__init__(**kwargs)

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_browser_use_tools()
        if self.SNAPSHOT:
            self._snapshot_tools()


class _UnsnapshottedChat(_Chat):
    """An agent that reads the process-wide registry directly."""

    SNAPSHOT = False


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    monkeypatch.setattr(browser_driver, "installed", lambda: False)
    saved = dict(tools_mod._TOOL_REGISTRY)
    yield
    tools_mod._TOOL_REGISTRY.clear()
    tools_mod._TOOL_REGISTRY.update(saved)


def _new(cls):
    with patch("gaia.agents.base.agent.AgentSDK"):
        return cls(silent_mode=True, skip_lemonade=True)


def _load_pane_skill(agent, tmp_path):
    from gaia.skills.manager import SkillManager

    return agent.load_skill(
        "browser-pane",
        manager=SkillManager(
            agent_skill_dirs=[str(_SKILLS_ROOT)],
            user_skills_root=tmp_path / "user-skills",
            include_claude_roots=False,
        ),
    )


def test_a_new_chat_does_not_inherit_another_chats_skill_tools(tmp_path):
    first = _new(_Chat)
    _load_pane_skill(first, tmp_path)
    assert _TABS in first._tools_registry

    second = _new(_Chat)

    assert not [n for n in second._tools_registry if n.startswith("browser-pane/")]
    result = second._execute_tool(_TABS, {})
    assert result["status"] == "error"
    assert result[EXECUTED_KEY] is False


def test_a_skill_tool_whose_skill_this_agent_never_loaded_is_a_tool_error(tmp_path):
    first = _new(_Chat)
    _load_pane_skill(first, tmp_path)
    second = _new(_UnsnapshottedChat)
    assert _TABS in second._tools_registry

    result = second._execute_tool(_TABS, {})

    assert result["status"] == "error"
    assert result[EXECUTED_KEY] is False
    assert "browser-pane" in result["error"]
    assert "load" in result["error"].lower()


def test_the_pane_permission_check_runs_once_this_chat_loads_the_skill(tmp_path):
    first = _new(_Chat)
    _load_pane_skill(first, tmp_path)
    second = _new(_Chat)
    _load_pane_skill(second, tmp_path)

    # No pane is attached in this test, so the check refuses — it must not raise.
    refusal = second._policy_refusal(_TABS, {})
    assert refusal is None or refusal["status"] == "error"
    assert "permission check failed" not in str(refusal)


def test_a_permission_check_that_raises_is_a_tool_error_not_a_failed_run():
    agent = _new(_Chat)
    ran = []

    def _broken_check(_args):
        raise LookupError("no pane for this chat")

    agent._instance_tools["probe_tool"] = {
        "name": "probe_tool",
        "description": "probe",
        "parameters": {},
        "function": lambda: ran.append(True) or "ran",
        "atomic": True,
        "preflight": _broken_check,
    }

    result = agent._execute_tool("probe_tool", {})

    assert ran == []
    assert result["status"] == "error"
    assert result[EXECUTED_KEY] is False
    assert "probe_tool" in result["error"]
    assert "no pane for this chat" in result["error"]
