# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``remember_skill_lesson`` reaches the prompt on the turn it is needed.

The write path itself is covered by ``tests/unit/test_adaptive_skills.py``; this
file covers only whether the model can see the tool at all. It could not: the
tool sits outside CORE, and someone correcting a skill talks about their meeting
brief rather than about skills, so retrieval never finds it. The model then
reported a transcript skill "has been updated on your machine" having called
nothing. It joins the list with the skill, the way a skill's own tools do, and
never moves the session to the full list.

Built with ``GaiaAgent.__new__(GaiaAgent)`` like ``test_lazy_skill_activation.py``
— no LLM, no Lemonade, no embedder.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from gaia_agent.agent import GaiaAgent

from gaia.agents.base.tool_loader import ToolBundle, ToolLoader

TOOL = "remember_skill_lesson"


def _agent(*, loaded: dict | None = None, learning: bool = True) -> GaiaAgent:
    agent = GaiaAgent.__new__(GaiaAgent)
    agent._loaded_skills = loaded if loaded is not None else {}
    agent._recalled_skills = []
    agent._active_skill_filter = None
    agent.learned_skills_enabled = lambda: learning
    agent._loaded_skill_tools = lambda: []
    return agent


# ── the standing skill tools ─────────────────────────────────────────────


def test_a_loaded_skill_puts_the_tool_in_reach():
    agent = _agent(loaded={"transcribe-meeting": SimpleNamespace(tool_names=[])})
    assert agent._standing_skill_tools() == [TOOL]


def test_no_loaded_skill_leaves_the_list_untouched():
    """The flagship ships with no skills; a tool that can only refuse is tax."""
    assert _agent()._standing_skill_tools() == []


def test_the_off_switch_keeps_it_out():
    """Under --no-learned-skills the write refuses, so offering it would only
    spend prompt tokens on a refusal."""
    agent = _agent(loaded={"transcribe-meeting": SimpleNamespace(tool_names=[])}, learning=False)
    assert agent._standing_skill_tools() == []


def test_it_follows_the_skills_own_tools():
    agent = _agent(loaded={"transcribe-meeting": SimpleNamespace(tool_names=[])})
    agent._loaded_skill_tools = lambda: ["transcribe_media", "summarize_document"]
    assert agent._standing_skill_tools() == [
        "transcribe_media",
        "summarize_document",
        TOOL,
    ]


def test_a_skill_already_naming_it_does_not_duplicate_it():
    agent = _agent(loaded={"transcribe-meeting": SimpleNamespace(tool_names=[])})
    agent._loaded_skill_tools = lambda: [TOOL]
    assert agent._standing_skill_tools() == [TOOL]


def test_a_recalled_procedure_is_not_what_brings_it():
    """A procedure's tools are a jump; this one must not move the session."""

    class _Proc:
        tools_required = ["transcribe_media"]

    agent = _agent(loaded={"transcribe-meeting": SimpleNamespace(tool_names=[])})
    agent._recalled_skills = [_Proc()]
    assert agent._recalled_skill_tools() == ["transcribe_media"]


# ── what the loader does with it ─────────────────────────────────────────


@pytest.fixture
def loader() -> ToolLoader:
    """A loader whose embedder scores everything zero, so only CORE and a
    skill's tools are listed. That isolates the fix from ranking luck."""
    return ToolLoader(
        core_tools=frozenset({"remember"}),
        bundles=[ToolBundle(name="skills", members=frozenset({TOOL}), description="x")],
        embed_fn=lambda _text: [0.0] * 8,
        threshold=0.2,
    )


def test_it_joins_the_list_when_semantics_never_would(loader):
    """The reproduction, at the layer that failed: a brief-shaped query scores
    nothing against a tool described in terms of skills."""
    registry = {"remember": {"description": "store a fact"}, TOOL: {"description": "x"}}
    query = "I want meeting briefs to start with action items grouped by owner"

    assert loader.select(query, registry) == ["remember"]
    assert loader.select(query, registry, skill_tools=[TOOL]) == ["remember", TOOL]
    assert loader.jumped is False


def test_a_name_absent_from_the_registry_is_dropped_not_raised(loader):
    """Agents other than the flagship never register it."""
    registry = {"remember": {"description": "x"}}
    assert loader.select("anything", registry, skill_tools=[TOOL]) == ["remember"]
