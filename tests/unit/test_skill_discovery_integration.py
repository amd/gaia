# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End-to-end proactive discovery against real ``SKILL.md`` files on disk.

The other two files stub something: ``test_skill_retriever.py`` scores strings,
``test_skill_discovery.py`` hands in a fake loader. Neither would notice if
``Agent.load_skill`` rejected what the retriever chose, or if the skill's body
never reached the prompt — and "we called it" is not "the call was valid".

So this drives the whole chain with nothing faked below the LLM: a real
:class:`~gaia.skills.manager.SkillManager` over real skill directories, the real
``Agent.load_skill``, the real prompt composition. Only the model is absent, and
the model is not part of what this feature decides.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from gaia.agents.base.skill_discovery import SkillDiscovery

SKILLS = {
    "github-triage": (
        "Triage GitHub work with the gh CLI — your unread notification inbox, or "
        "one repository's issue backlog. Groups what arrived, judges what is "
        "urgent, drafts the reply. Use when asked to triage issues or review a "
        "backlog."
    ),
    "data-explore": (
        "Load messy tabular data into SQL scratchpad tables and answer questions "
        "with real queries instead of eyeballing. Use when the user has a CSV, "
        "spreadsheet, or export."
    ),
}

BODY_MARKER = "STEP ONE: run `gh issue list`"


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    """A real on-disk skills root — frontmatter and body, parsed by the real parser."""
    root = tmp_path / "skills"
    for name, description in SKILLS.items():
        directory = root / name
        directory.mkdir(parents=True)
        body = BODY_MARKER if name == "github-triage" else "Load the file first."
        (directory / "SKILL.md").write_text(
            textwrap.dedent(f"""\
                ---
                name: {name}
                description: {description}
                version: 1.0.0
                ---

                # {name}

                {body}
                """),
            encoding="utf-8",
        )
    return root


@pytest.fixture
def agent(skills_root: Path, monkeypatch):
    """A minimal real ``Agent`` with discovery on and no LLM anywhere near it."""
    from gaia.agents.base.agent import Agent
    from gaia.skills.manager import SkillManager

    class _Agent(Agent):
        def _register_tools(self) -> None:
            pass

        def _get_system_prompt(self) -> str:
            return "You are a test agent."

    instance = _Agent.__new__(_Agent)
    instance._skill_manager = SkillManager(
        agent_skill_dirs=[skills_root],
        user_skills_root=skills_root / "__absent__",
        include_claude_roots=False,
    )
    instance._loaded_skills = {}
    instance._sticky_skill_turns = None
    instance._active_skill_filter = None
    instance._skill_discovery_result = None
    instance._instance_tools = None
    instance._skill_discovery = SkillDiscovery(instance._skill_manager)
    # Real prompt composition, no stub — a note that never reaches the prompt is
    # the bug this file exists to catch. ``_compose_system_prompt`` only needs
    # the mixin fragments and ``_get_system_prompt``; no model is involved.
    instance._active_tool_filter = None
    instance._system_prompt_cache = instance._compose_system_prompt()
    return instance


def test_a_described_but_unnamed_request_really_loads_the_skill(agent):
    """The reported defect, reproduced against real files and fixed.

    "what's been going on in my github inbox the past few days?" produced a
    fabricated answer with zero tool calls because nothing ever told the agent
    ``github-triage`` existed.
    """
    assert agent.loaded_skills == {}

    agent._discover_skills_for_turn(
        "what's been going on in my github inbox the past few days?"
    )

    assert "github-triage" in agent.loaded_skills
    assert agent._skill_discovery_result.loaded == "github-triage"


def test_the_loaded_skills_instructions_reach_the_prompt(agent):
    """A load that does not change the prompt has not done anything."""
    agent._discover_skills_for_turn("triage my github inbox")
    agent._refresh_active_skill_filter("triage my github inbox")

    prompt = agent.get_skills_system_prompt()
    assert BODY_MARKER in prompt


def test_the_body_survives_the_filter_that_runs_immediately_after(agent):
    """Discovery loads, then ``_refresh_active_skill_filter`` runs in the same turn.

    Without the sticky pin that refresh can collapse the body of the skill this
    turn was loaded for — a load that is instantly undone.
    """
    agent._discover_skills_for_turn("triage my github inbox")
    # Force the harshest case: a selector that matches nothing at all.
    agent._select_skills_for_turn = lambda _query: []
    agent._refresh_active_skill_filter("triage my github inbox")

    assert "github-triage" in (agent._active_skill_filter or [])
    assert BODY_MARKER in agent.get_skills_system_prompt()


def test_the_activation_note_reaches_the_composed_prompt(agent):
    """Regression: ``load_skill`` rebuilds the prompt *before* the result is
    recorded, so the note for this turn was composed from the previous turn's
    result and the "SKILL ACTIVATED" line went missing on every load."""
    agent._discover_skills_for_turn("triage my github inbox")

    assert "SKILL ACTIVATED" in agent.system_prompt
    assert "github-triage" in agent.system_prompt


def test_the_sourcing_rule_is_in_the_prompt_on_a_turn_with_no_match(agent):
    """The fabrication happened with no skill loaded, so the rule must be there
    when nothing matched — not only alongside a skill."""
    agent._discover_skills_for_turn("what is 17 times 23?")
    assert "==== SOURCING ====" in agent.system_prompt


def test_the_note_clears_once_the_turn_it_described_is_over(agent):
    """A stale "SKILL ACTIVATED" would make the model announce a skill on every
    later turn, and would pin volatile text into the prompt forever."""
    agent._discover_skills_for_turn("triage my github inbox")
    assert "SKILL ACTIVATED" in agent.system_prompt

    agent._discover_skills_for_turn("what is 17 times 23?")
    assert "SKILL ACTIVATED" not in agent.system_prompt


def test_an_unrelated_turn_loads_nothing_from_a_real_library(agent):
    agent._discover_skills_for_turn("what is 17 times 23?")
    assert agent.loaded_skills == {}
    assert agent.get_skills_system_prompt() == ""


def test_the_second_turn_does_not_reload_what_the_first_loaded(agent):
    agent._discover_skills_for_turn("triage my github inbox")
    agent._discover_skills_for_turn("anything else in the github backlog?")

    assert list(agent.loaded_skills) == ["github-triage"]
    # Already loaded, so discovery has nothing left to do with it.
    assert agent._skill_discovery_result.loaded is None


def test_a_different_request_loads_the_other_skill(agent):
    agent._discover_skills_for_turn("triage my github inbox")
    agent._discover_skills_for_turn("load this csv and give me totals by region")

    assert set(agent.loaded_skills) == {"github-triage", "data-explore"}


def test_an_empty_library_is_harmless(tmp_path, agent):
    from gaia.skills.manager import SkillManager

    agent._skill_discovery = SkillDiscovery(
        SkillManager(
            agent_skill_dirs=[tmp_path / "nothing"],
            user_skills_root=tmp_path / "nothing-either",
            include_claude_roots=False,
        )
    )
    agent._discover_skills_for_turn("triage my github inbox")
    assert agent.loaded_skills == {}


def test_a_skill_installed_mid_session_becomes_discoverable(agent, skills_root):
    """The index is rebuilt from discovery, not frozen at construction."""
    agent._discover_skills_for_turn("read me an rss feed")
    assert agent.loaded_skills == {}

    directory = skills_root / "rss-digest"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: rss-digest\ndescription: Read an RSS or Atom feed and "
        "summarize the newest entries.\nversion: 1.0.0\n---\n\n# rss\n\nBody.\n",
        encoding="utf-8",
    )
    agent._skill_manager.reload()

    agent._discover_skills_for_turn("what has this atom feed published lately?")
    assert "rss-digest" in agent.loaded_skills


class _TurnSetupDone(Exception):
    """Raised to end the turn once per-turn setup is done."""


def test_discovery_runs_before_the_turn_tool_filter(agent, monkeypatch):
    """A skill auto-loaded this turn registers tools, and the tool filter fixes
    the turn's tool list. Filtering first hands the model a recipe naming tools
    the same prompt does not contain — the exact failure this feature prevents.
    """
    order: list[str] = []

    monkeypatch.setattr(
        type(agent),
        "_discover_skills_for_turn",
        lambda self, q: order.append("discover"),
        raising=True,
    )
    monkeypatch.setattr(
        type(agent),
        "_refresh_active_tool_filter",
        lambda self, q: order.append("tools"),
        raising=True,
    )
    monkeypatch.setattr(
        type(agent),
        "_refresh_active_skill_filter",
        lambda self, q: order.append("bodies"),
        raising=True,
    )

    # Stop the turn the instant the three setup steps have run — the LLM loop
    # below them is not what this pins.
    def _stop(self, user_input):
        raise _TurnSetupDone

    monkeypatch.setattr(type(agent), "_begin_turn_record", _stop, raising=True)

    with pytest.raises(_TurnSetupDone):
        agent._process_query_impl("read me an rss feed")

    assert order == ["discover", "tools", "bodies"]
