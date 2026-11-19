# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship exposes report-from-sources' tools when the skill loads, and only then.

The skill ships ``tools.py`` (inventory_data_files, series_from_workbook,
chart_into_workbook). Loading it through the flagship's real bundled-skill
roots and the real ``Agent.load_skill`` must register the three namespaced
tools; unloading must remove them; and the default registered surface (the
``tools_count`` the capability docs pin) must not change, because skill tools
are not part of the default construction.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gaia.skills.manager import SkillManager

_REPO_ROOT = Path(__file__).resolve().parents[5]
SKILL = "report-from-sources"
TOOLS = {
    f"{SKILL}/{t}"
    for t in ("inventory_data_files", "series_from_workbook", "chart_into_workbook")
}


@pytest.fixture(scope="module")
def flagship():
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    return GaiaAgent(config=GaiaAgentConfig(silent_mode=True))


def test_loading_the_skill_registers_its_tools(flagship, tmp_path):
    manager = SkillManager(
        agent_skill_dirs=flagship.SKILL_DIRS,
        user_skills_root=tmp_path / "user-skills",
        include_claude_roots=False,
    )
    before = set(flagship._tools_registry)
    assert not TOOLS & before
    skill = flagship.load_skill(SKILL, manager=manager)
    try:
        assert set(flagship._tools_registry) - before == TOOLS
        offered = {s["function"]["name"] for s in flagship._build_openai_tool_schemas()}
        assert TOOLS <= offered
        # The consumed tools still reach the prompt through the skill signal.
        assert set(skill.gaia.tools_required) <= set(flagship._loaded_skill_tools())
    finally:
        assert flagship.unload_skill(SKILL)
    assert set(flagship._tools_registry) == before


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
