# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Skill-body prompt-budget regression (#2848 follow-up).

The bug this guards against: once ``load_skill`` activates a skill, its full
``SKILL.md`` body used to render in the system prompt on every subsequent
turn for the life of the session — regardless of whether that turn had
anything to do with it. Measured on this agent's two bundled GitHub skills
(``github-issue-response``, imported from ``.claude/skills``, and
``github-triage``, bundled under ``hub/skills``): loading both permanently
added their combined body to every turn's prompt.

These tests exercise the REAL ``Agent.get_skills_system_prompt`` render path
against the REAL, on-disk ``SKILL.md`` files this repo ships — not a
reimplementation, not a synthetic fixture — through a stub double that never
boots the LLM stack (mirrors ``test_skill_library_tools.py``'s activation
assertions, minus the network-dependent install path).

Assertions are relative (a lazy-inactive-turn prompt is some SMALL fraction of
the legacy prompt), not exact character counts, so this doesn't need updating
every time someone edits prose in the skills' SKILL.md bodies. What must never
regress: (1) a loaded-but-irrelevant skill's body basically disappears, (2)
its name/description survives as a menu line so it stays discoverable, and
(3) the full body comes back the moment the turn is marked as needing it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.agents.base.agent import Agent
from gaia.skills.manager import SkillManager

# parents[0]=tests/ [1]=python/ [2]=gaia/ [3]=agents/ [4]=hub/ [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_CLAUDE_SKILLS = _REPO_ROOT / ".claude" / "skills"
_HUB_SKILLS = _REPO_ROOT / "hub" / "skills"

pytestmark = pytest.mark.skipif(
    not (_CLAUDE_SKILLS / "github-issue-response" / "SKILL.md").is_file()
    or not (_HUB_SKILLS / "github-triage" / "SKILL.md").is_file(),
    reason="reference skills not present in this checkout",
)


class _StubAgent:
    """Drives the real skill-prompt methods without booting the LLM stack."""

    REQUIRED_CONNECTORS: list = []
    SKILL_DIRS: list = []
    SKILL_MANIFEST = None
    _instance_tools = None
    _loaded_skills = None
    _skill_sets = None
    _requested_skill_set = None
    _active_skill_set = None
    _skill_set_loaded = None
    _active_skill_filter = None
    _SKILL_MANIFEST_FILENAME = Agent._SKILL_MANIFEST_FILENAME

    skill_manager = Agent.skill_manager
    skill_sets = Agent.skill_sets
    _resolve_skill_manifest = Agent._resolve_skill_manifest
    _parse_skill_declarations = Agent._parse_skill_declarations
    loaded_skills = Agent.loaded_skills
    granted_binaries = Agent.granted_binaries
    _tools_registry = Agent._tools_registry
    _format_tools_for_prompt = Agent._format_tools_for_prompt
    _note_skill_active = Agent._note_skill_active
    _always_on_skill_names = Agent._always_on_skill_names
    load_skill = Agent.load_skill
    unload_skill = Agent.unload_skill
    get_skills_system_prompt = Agent.get_skills_system_prompt

    def __init__(self, manager):
        self._skill_manager = manager
        self.rebuilt = 0

    def rebuild_system_prompt(self):
        self.rebuilt += 1


@pytest.fixture
def agent():
    manager = SkillManager(agent_skill_dirs=[_CLAUDE_SKILLS, _HUB_SKILLS])
    return _StubAgent(manager)


def test_zero_skills_loaded_is_empty_in_both_modes(agent):
    assert agent.get_skills_system_prompt() == ""
    agent._active_skill_filter = []
    assert agent.get_skills_system_prompt() == ""


@pytest.mark.parametrize(
    "names",
    [
        ["github-issue-response"],
        ["github-issue-response", "github-triage"],
    ],
    ids=["one-skill", "two-skills"],
)
def test_inactive_skill_prompt_is_a_small_fraction_of_the_legacy_size(agent, names):
    for name in names:
        agent.load_skill(name)

    legacy = agent.get_skills_system_prompt()  # _active_skill_filter still None
    assert legacy, "loaded skill(s) must contribute something to the legacy prompt"

    agent._active_skill_filter = []  # a turn the per-turn selector found irrelevant
    lazy_floor = agent.get_skills_system_prompt()

    # The real fix: an irrelevant-this-turn skill's body basically disappears.
    # 20% is a generous ceiling — measured well under 10% on this repo's own
    # skills (#2848) — chosen so prose edits to the SKILL.md bodies don't
    # make this flaky.
    assert len(lazy_floor) < len(legacy) * 0.2, (
        f"lazy floor ({len(lazy_floor)} chars) is not a small fraction of the "
        f"legacy prompt ({len(legacy)} chars) — a loaded-but-irrelevant skill "
        "is still costing most of its body every turn."
    )


def test_inactive_skill_stays_discoverable_as_a_menu_line(agent):
    agent.load_skill("github-issue-response")
    agent.load_skill("github-triage")
    agent._active_skill_filter = []

    prompt = agent.get_skills_system_prompt()

    assert "- github-issue-response:" in prompt
    assert "- github-triage:" in prompt
    # The menu line is a discovery aid, not the recipe — it must be far
    # smaller than the real body it stands in for.
    skill = agent.loaded_skills["github-issue-response"]
    assert len(skill.body) > 2000  # sanity: this skill really is non-trivial
    menu_section = prompt  # only section present when nothing is active
    assert len(menu_section) < len(skill.body)


def test_active_skill_body_is_restored_in_full(agent):
    """Capability preservation: marking a skill active brings its REAL body
    back verbatim — not a summary, not a truncated version."""
    agent.load_skill("github-issue-response")
    agent.load_skill("github-triage")
    real_body = agent.loaded_skills["github-issue-response"].body

    agent._active_skill_filter = ["github-issue-response"]
    prompt = agent.get_skills_system_prompt()

    assert "==== LOADED SKILLS (active this turn) ====" in prompt
    assert real_body in prompt
    # The other loaded skill stays hidden — the fix is per-skill, not all-or-nothing.
    assert "- github-triage:" in prompt
    assert agent.loaded_skills["github-triage"].body not in prompt


def test_reactivating_via_load_skill_brings_the_body_back(agent):
    """The explicit escape hatch: calling load_skill again on an
    already-loaded-but-hidden skill works even without a semantic match."""
    agent.load_skill("github-issue-response")
    agent._active_skill_filter = []
    assert agent.loaded_skills["github-issue-response"].body not in (
        agent.get_skills_system_prompt()
    )

    agent.load_skill("github-issue-response")  # already loaded -> reactivate

    prompt = agent.get_skills_system_prompt()
    assert agent.loaded_skills["github-issue-response"].body in prompt
