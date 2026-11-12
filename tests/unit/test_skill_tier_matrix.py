# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which hub skills each model feature tier can run.

A compact-tier model (Gemma-4-E4B, the default on most PCs) is not offered some
tools, so a skill that needs one cannot load there. That is a product decision,
so the outcome is pinned here: a tier change, or a skill gaining a withheld
tool, shows up as a failing diff instead of a skill quietly vanishing for most
users. The agent half checks the catalogue hides those skills and ``load_skill``
refuses them with a message the model can relay.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gaia.agents.base.agent import _skill_tool_names
from gaia.agents.base.model_features import COMPACT, TIERS, withheld_tools
from gaia.skills import parse_skill_file
from gaia.skills.errors import SkillUnavailableError
from gaia.skills.manager import SkillManager

REPO_ROOT = Path(__file__).resolve().parents[2]
HUB_SKILLS = REPO_ROOT / "hub" / "skills"
SKILL_DIRS = sorted(d for d in HUB_SKILLS.iterdir() if (d / "SKILL.md").is_file())

#: Hub skills each tier cannot run. Change this only with the tier or the skill.
UNAVAILABLE = {
    "full": set(),
    "compact": {"calendar", "data-explore", "image-gen", "inbox-triage", "send-email"},
}


def _unavailable(tier: str) -> set[str]:
    withheld = withheld_tools(TIERS[tier])
    return {
        d.name
        for d in SKILL_DIRS
        if set(_skill_tool_names(parse_skill_file(d))) & withheld
    }


def test_the_matrix_names_every_tier():
    assert set(UNAVAILABLE) == set(TIERS)


@pytest.mark.parametrize("tier", sorted(TIERS))
def test_tier_runs_the_pinned_set_of_hub_skills(tier: str):
    assert _unavailable(tier) == UNAVAILABLE[tier], (
        f"Hub skills the {tier!r} tier cannot run changed. If intended, update "
        f"UNAVAILABLE in {Path(__file__).name} and the compact-tier eval "
        "scenarios (requires_compact_tier) that cover them."
    )


@pytest.fixture
def manager(tmp_path: Path) -> SkillManager:
    root = tmp_path / "gaia-home" / "skills"
    root.mkdir(parents=True)
    for skill_dir in SKILL_DIRS:
        shutil.copytree(skill_dir, root / skill_dir.name)
    return SkillManager(user_skills_root=root, claude_skill_dirs=[tmp_path / "none"])


def _agent(manager: SkillManager, features):
    """The shipped load/catalogue methods bound to a stub with *features*."""
    from gaia.agents.base.agent import Agent

    class _StubAgent:
        REQUIRED_CONNECTORS: list = []
        SKILL_DIRS: list = []
        _instance_tools = None
        _loaded_skills = None
        _active_skill_filter = None
        _skill_catalog_enabled = True
        model_id = "Gemma-4-E4B-it-GGUF"

        skill_manager = Agent.skill_manager
        loaded_skills = Agent.loaded_skills
        granted_binaries = Agent.granted_binaries
        _tools_registry = Agent._tools_registry
        _format_tools_for_prompt = Agent._format_tools_for_prompt
        _note_skill_active = Agent._note_skill_active
        _pin_skill_body = Agent._pin_skill_body
        _lists_project_skills = Agent._lists_project_skills
        load_skill = Agent.load_skill
        unload_skill = Agent.unload_skill
        get_skills_system_prompt = Agent.get_skills_system_prompt
        get_skill_catalog_system_prompt = Agent.get_skill_catalog_system_prompt

        def rebuild_system_prompt(self):
            return None

    agent = _StubAgent()
    agent._skill_manager = manager
    agent.model_features = features
    return agent


def _listed(agent) -> set[str]:
    """Skill names on the catalogue's ``- name: description`` lines."""
    catalog = agent.get_skill_catalog_system_prompt()
    return {
        line[2:].split(":", 1)[0]
        for line in catalog.splitlines()
        if line.startswith("- ")
    }


@pytest.mark.parametrize("tier", sorted(TIERS))
def test_catalogue_lists_exactly_the_skills_the_tier_can_run(manager, tier):
    listed = _listed(_agent(manager, TIERS[tier]))

    assert listed == {d.name for d in SKILL_DIRS} - UNAVAILABLE[tier]


@pytest.mark.parametrize("name", sorted(UNAVAILABLE["compact"]))
def test_compact_load_refuses_with_a_message_the_model_can_relay(manager, name):
    agent = _agent(manager, COMPACT)

    with pytest.raises(SkillUnavailableError, match="larger model"):
        agent.load_skill(name, manager=manager)
    assert name not in agent.loaded_skills


@pytest.fixture
def library_agent(tmp_path, monkeypatch):
    """A real agent with the skill-library tools, over the hub skills."""
    from unittest.mock import patch

    from gaia.agents.base.agent import Agent
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.agents.tools.skill_library_tools import SkillLibraryToolsMixin

    class _LibraryAgent(Agent, SkillLibraryToolsMixin):
        def _get_system_prompt(self):
            return "skills"

        def _register_tools(self):
            self.register_skill_library_tools()

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _LibraryAgent(silent_mode=True, skip_lemonade=True)
    agent._skill_manager = SkillManager(
        agent_skill_dirs=[HUB_SKILLS],
        user_skills_root=tmp_path / "user-skills",
        include_claude_roots=False,
    )
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def test_compact_list_skills_says_installing_needs_a_larger_model(library_agent):
    library_agent.model_features = COMPACT

    payload = library_agent._execute_tool("list_skills", {})

    assert payload["count"] == len(SKILL_DIRS)
    assert "needs a larger model" in payload["hint"]


def test_full_list_skills_has_no_hub_hint(library_agent):
    library_agent.model_features = TIERS["full"]

    payload = library_agent._execute_tool("list_skills", {})

    assert "hint" not in payload
