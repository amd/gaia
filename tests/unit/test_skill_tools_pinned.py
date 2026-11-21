# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# flake8: noqa: F811
# (pytest fixtures imported from a sibling test module are named again as arguments)
"""A loaded skill's tools are on the list the model is sent, and stay there.

A skill's instructions name tools. However the skill came to be loaded, each
of them must be callable in the next model request and in later turns, whatever
the per-turn selection scored, and a tool that cannot be offered is named in
the load result instead of the load reporting plain success.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

pytest.importorskip("gaiapy")

from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.agents.base.tool_loader import ToolLoader  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY, tool  # noqa: E402

from .skills_helpers import isolated_manager, write_skill_dir  # noqa: E402
from .test_tool_list_invariants import (  # noqa: E402, F401
    FULL_CORE_TOOLS,
    _turn,
    flagship,
)

SKILL = (
    "---\nname: {name}\ndescription: Uses the tree. Use when testing.\n"
    "metadata:\n  gaia:\n    tools_required:\n      - {tool}\n"
    "---\n\n# {name}\n\nDraw it with {tool}.\n"
)


@pytest.fixture
def skills(tmp_path):
    root = tmp_path / "skills"
    write_skill_dir(root, "one-tree", SKILL.format(name="one-tree", tool="tree"))
    write_skill_dir(
        root, "no-such", SKILL.format(name="no-such", tool="tool_nobody_has")
    )
    return isolated_manager(tmp_path, agent_skill_dirs=[root])


def _load(name):
    return {"id": "load", "name": "load_skill", "args": {"name": name}}


def _result_of(model, call_id="load"):
    for message in model.requests[-1]["messages"]:
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            content = message["content"]
            if isinstance(content, list):
                content = content[0]["text"]
            return json.loads(content)
    raise AssertionError("no result for the load_skill call was sent back")


# ── the model loads it ────────────────────────────────────────────────────


def test_the_next_request_lists_the_skills_tools(flagship, skills):
    agent, model = flagship
    agent._skill_manager = skills
    assert "tree" not in FULL_CORE_TOOLS

    _turn(agent, model, "hello there", [_load("one-tree"), "Loaded."])

    assert "tree" not in model.names(0)
    assert "tree" in model.names(1)
    # Admitted, not swept in by a jump to the full list.
    assert not agent.tool_loader.jumped


def test_later_turns_keep_them_whatever_the_selection_scores(flagship, skills):
    agent, model = flagship
    agent._skill_manager = skills
    _turn(agent, model, "hello there", [_load("one-tree"), "Loaded."])

    _turn(agent, model, "thanks", ["Sure."])
    _turn(agent, model, "ok", ["Yes."])

    assert "tree" in model.names()
    assert not agent.tool_loader.jumped


def test_the_load_result_names_the_tools_now_callable(flagship, skills):
    agent, model = flagship
    agent._skill_manager = skills
    _turn(agent, model, "hello there", [_load("one-tree"), "Loaded."])

    result = _result_of(model)
    assert result["status"] == "success"
    assert result["tools_available"] == ["tree"]
    assert "on your tool list now" in result["tools_hint"]
    assert "warning" not in result


def test_a_tool_turned_off_is_named_not_reported_as_loaded(flagship, skills):
    agent, model = flagship
    agent._skill_manager = skills
    agent._denied_tool_patterns = ("tree",)

    _turn(agent, model, "hello there", [_load("one-tree"), "Loaded."])

    result = _result_of(model)
    assert "tree" not in model.names()
    assert result["unavailable_tools"] == ["tree"]
    assert "tools_available" not in result
    assert "tree" in result["warning"] and "cannot call" in result["warning"]


def test_a_tool_nobody_registered_is_named(flagship, skills):
    agent, model = flagship
    agent._skill_manager = skills

    _turn(agent, model, "hello there", [_load("no-such"), "Loaded."])

    result = _result_of(model)
    assert result["unmet_tools_required"] == ["tool_nobody_has"]
    assert "tools_available" not in result
    assert "tool_nobody_has" in result["warning"]


# ── it was loaded some other way ──────────────────────────────────────────


def test_a_skill_loaded_before_the_turn_is_listed_though_its_body_is_hidden(
    flagship, skills, monkeypatch
):
    agent, model = flagship
    agent.load_skill("one-tree", manager=skills)
    # The per-turn body selection leaves this skill out.
    monkeypatch.setattr(agent, "_select_skills_for_turn", lambda _query: [])

    _turn(agent, model, "hello there", ["Hi."])
    _turn(agent, model, "and again", ["Yes."])

    assert "one-tree" not in (agent._active_skill_filter or [])
    assert "tree" in model.names(0)
    assert "tree" in model.names()
    assert not agent.tool_loader.jumped


class _OwnSelection(Agent):
    """An agent that picks its own tool list each turn and knows no skills."""

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        @tool
        def read_file(file_path: str) -> dict:
            """Probe reader."""
            return {"status": "success"}

        @tool
        def tree(path: str) -> dict:
            """Probe tree."""
            return {"status": "success"}

    def _select_tools_for_turn(self, user_input):
        return ["read_file"]


def test_an_agent_with_its_own_selection_keeps_them_every_turn(skills):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _OwnSelection(silent_mode=True, skip_lemonade=True)
        agent._refresh_active_tool_filter("first")
        assert agent._active_tool_filter == ["read_file"]

        agent.load_skill("one-tree", manager=skills)
        assert agent._active_tool_filter == ["read_file", "tree"]

        for query in ("second", "third"):
            agent._refresh_active_tool_filter(query)
            assert agent._active_tool_filter == ["read_file", "tree"]

        agent.unload_skill("one-tree")
        agent._refresh_active_tool_filter("fourth")
        assert agent._active_tool_filter == ["read_file"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_a_skill_naming_a_missing_tool_is_logged_as_a_warning(skills, caplog):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _OwnSelection(silent_mode=True, skip_lemonade=True)
        with caplog.at_level(logging.WARNING, logger="gaia.agents.base.agent"):
            agent.load_skill("no-such", manager=skills)
        assert "tool_nobody_has" in caplog.text
        assert "cannot call" in caplog.text
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


# ── the log shows a mid-turn admission ────────────────────────────────────


def test_admitting_tools_is_logged(caplog):
    loader = ToolLoader(frozenset({"core"}), [], lambda text: None)
    registry = {"core": {}, "extra": {}}
    with caplog.at_level(logging.INFO, logger="gaia.agents.base.tool_loader"):
        loader.admit_tools(["extra", "ghost"], registry)
        loader.admit_tools(["extra"], registry)
    lines = [r.getMessage() for r in caplog.records if "admit" in r.getMessage()]
    assert len(lines) == 1
    first = json.loads(lines[0].split("TOOL_LOADER ", 1)[1])
    assert first["tools"] == ["extra"] and first["not_registered"] == ["ghost"]
