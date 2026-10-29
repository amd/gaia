# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship exposes report-from-sources' tools when the skill loads, and only then.

The skill ships ``tools.py`` (inventory_data_files, series_from_workbook,
chart_into_workbook). Loading it through the flagship's real bundled-skill
roots and the real ``Agent.load_skill`` must register the three namespaced
tools; unloading must remove them; and the default registered surface (the
``tools_count`` the capability docs pin) must not change, because skill tools
are not part of the default construction.

Driven through a stub that borrows the real ``Agent`` methods, like
``test_skill_prompt_budget.py``, so no LLM client or model server is started.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gaia.agents.base.agent import Agent
from gaia.skills.manager import SkillManager

_REPO_ROOT = Path(__file__).resolve().parents[5]
SKILL = "report-from-sources"
TOOLS = {
    f"{SKILL}/{t}"
    for t in ("inventory_data_files", "series_from_workbook", "chart_into_workbook")
}



class _StubAgent:
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
    loaded_skills = Agent.loaded_skills
    granted_binaries = Agent.granted_binaries
    _tools_registry = Agent._tools_registry
    _note_skill_active = Agent._note_skill_active
    _active_skill_names = Agent._active_skill_names
    _always_on_skill_names = Agent._always_on_skill_names
    _loaded_skill_tools = Agent._loaded_skill_tools
    load_skill = Agent.load_skill
    unload_skill = Agent.unload_skill

    def __init__(self, manager):
        self._skill_manager = manager
        self._instance_tools = {}

    def rebuild_system_prompt(self):
        pass


@pytest.fixture
def agent():
    import gaia_agent.agent as flagship

    roots = flagship._bundled_skill_roots()
    assert any((Path(r) / SKILL / "tools.py").is_file() for r in roots), roots
    return _StubAgent(SkillManager(agent_skill_dirs=[Path(r) for r in roots]))


def test_loading_the_skill_registers_its_tools(agent):
    assert not TOOLS & set(agent._tools_registry)
    skill = agent.load_skill(SKILL)
    try:
        assert TOOLS <= set(agent._tools_registry)
        assert TOOLS <= set(agent._instance_tools)
        # The consumed tools still reach the prompt through the skill signal.
        assert set(skill.gaia.tools_required) <= set(agent._loaded_skill_tools())
    finally:
        agent.unload_skill(SKILL)
    assert not TOOLS & set(agent._tools_registry)


def test_default_tool_count_is_unchanged_by_the_skill():
    """Skill tools are opt-in, so the documented default surface stays put."""
    import gaia_agent

    manifest = yaml.safe_load(
        (_REPO_ROOT / "hub/agents/gaia/python/gaia-agent.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert gaia_agent.build_gaia().tools_count == manifest["tools_count"]
    matrix = (_REPO_ROOT / "hub/agents/gaia/CAPABILITY_MATRIX.md").read_text(
        encoding="utf-8"
    )
    for tool in TOOLS:
        assert tool.split("/")[1] not in matrix
