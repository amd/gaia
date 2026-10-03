# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``remember_skill_lesson`` reaches the prompt on the turn it is needed.

The write path itself is covered by ``tests/unit/test_adaptive_skills.py``; this
file covers only whether the model can see the tool at all. It could not: the
tool sits in the ``skills`` bundle, and someone correcting a skill talks about
their meeting brief rather than about skills, so semantic selection never picked
that bundle. The model then answered from the bundle menu's prose — it reported
a transcript skill "has been updated on your machine" having called nothing.

Built with ``GaiaAgent.__new__(GaiaAgent)`` like ``test_lazy_skill_activation.py``
— no LLM, no Lemonade, no embedder.
"""

from __future__ import annotations

import pytest
from gaia_agent.agent import GaiaAgent

from gaia.agents.base.tool_loader import ToolBundle, ToolLoader

TOOL = "remember_skill_lesson"


def _agent(*, loaded: dict | None = None, learning: bool = True) -> GaiaAgent:
    agent = GaiaAgent.__new__(GaiaAgent)
    agent._loaded_skills = loaded if loaded is not None else {}
    agent._recalled_skills = []
    agent.learned_skills_enabled = lambda: learning
    return agent


# ── the SKILL signal ─────────────────────────────────────────────────────


def test_a_loaded_skill_puts_the_tool_in_reach():
    assert TOOL in _agent(loaded={"transcribe-meeting": object()})._recalled_skill_tools()


def test_no_loaded_skill_leaves_the_signal_untouched():
    """The flagship ships with no skills; a tool that can only refuse is tax."""
    assert _agent()._recalled_skill_tools() == []


def test_the_off_switch_keeps_it_out():
    """Under --no-learned-skills the write refuses, so offering it would only
    spend prompt tokens on a refusal."""
    agent = _agent(loaded={"transcribe-meeting": object()}, learning=False)
    assert TOOL not in agent._recalled_skill_tools()


def test_it_does_not_displace_a_recalled_procedure_s_tools():
    """The inherited signal (#1451) still comes first and stays intact."""

    class _Proc:
        tools_required = ["transcribe_media", "summarize_document"]

    agent = _agent(loaded={"transcribe-meeting": object()})
    agent._recalled_skills = [_Proc()]
    assert agent._recalled_skill_tools() == [
        "transcribe_media",
        "summarize_document",
        TOOL,
    ]


def test_a_procedure_already_naming_it_does_not_duplicate_it():
    class _Proc:
        tools_required = [TOOL]

    agent = _agent(loaded={"transcribe-meeting": object()})
    agent._recalled_skills = [_Proc()]
    assert agent._recalled_skill_tools() == [TOOL]


# ── what the loader does with it ─────────────────────────────────────────


@pytest.fixture
def loader() -> ToolLoader:
    """A loader whose embedder scores everything zero, so only CORE and the
    SKILL signal can admit a tool. That isolates the fix from ranking luck."""
    return ToolLoader(
        core_tools=frozenset({"remember"}),
        bundles=[ToolBundle(name="skills", members=frozenset({TOOL}), description="x")],
        embed_fn=lambda _text: [0.0] * 8,
        max_tools=10,
        threshold=0.2,
    )


def test_the_signal_admits_it_when_semantics_never_would(loader):
    """The reproduction, at the layer that failed: a brief-shaped query scores
    nothing against a bundle described in terms of skills."""
    registry = {"remember": {"description": "store a fact"}, TOOL: {"description": "x"}}
    query = "I want meeting briefs to start with action items grouped by owner"

    assert TOOL not in (loader.select(query, registry) or [])
    assert TOOL in (loader.select(query, registry, skill_tools=[TOOL]) or [])


def test_a_name_absent_from_the_registry_is_dropped_not_raised(loader):
    """Agents other than the flagship never register it."""
    assert TOOL not in (
        loader.select("anything", {"remember": {"description": "x"}}, skill_tools=[TOOL])
        or []
    )
