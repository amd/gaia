# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship on a compact model: the features Gemma fails are gone, and nothing else."""

from __future__ import annotations

import contextlib

import pytest
from gaia_agent import stdio
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.model_features import COMPACT, FULL, TIER_ENV, withheld_tools
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    LARGE_DEFAULT_MODEL_NAME,
    NPU_DEFAULT_MODEL_NAME,
)
from gaia.skills.errors import SkillUnavailableError

# Each needs a tool from one compact-withheld feature.
SKILLS_ON_WITHHELD_TOOLS = ("data-explore", "image-gen", "inbox-triage")


@contextlib.contextmanager
def _agent(model_id):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GAIA_MEMORY_DISABLED", "1")
            mp.delenv("GAIA_FAST", raising=False)
            mp.delenv(TIER_ENV, raising=False)
            yield GaiaAgent(config=GaiaAgentConfig(silent_mode=True, model_id=model_id))
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture(scope="module")
def full_tools():
    with _agent(LARGE_DEFAULT_MODEL_NAME) as agent:
        return set(agent._tools_registry)


@pytest.fixture(params=[DEFAULT_MODEL_NAME, NPU_DEFAULT_MODEL_NAME], ids=["gpu", "npu"])
def gemma(request):
    with _agent(request.param) as agent:
        yield agent


def test_gemma_loses_exactly_the_compact_features_tools(gemma, full_tools):
    withheld = withheld_tools(COMPACT)
    # Every name in the table is a real tool, or the table is guarding nothing.
    assert withheld <= full_tools, sorted(withheld - full_tools)
    assert full_tools - set(gemma._tools_registry) == withheld


def test_gemma_keeps_installed_skills_and_documents(gemma):
    for name in ("load_skill", "list_skills", "capture_skill", "analyze_data_file"):
        assert name in gemma._tools_registry


def test_gemma_is_not_told_about_withheld_features(gemma):
    prompt = gemma.system_prompt
    assert "DATA ANALYSIS WORKFLOW (Scratchpad)" not in prompt
    assert "**IMAGE GENERATION:** Not available in this session." in prompt


def test_gemma_catalog_hides_skills_it_cannot_run(gemma):
    catalog = gemma.get_skill_catalog_system_prompt()
    for name in SKILLS_ON_WITHHELD_TOOLS:
        assert f"- {name}:" not in catalog
    assert "- research-report:" in catalog


@pytest.mark.parametrize("name", SKILLS_ON_WITHHELD_TOOLS)
def test_gemma_refuses_to_load_a_skill_it_cannot_run(gemma, name):
    with pytest.raises(SkillUnavailableError, match="larger model"):
        gemma.load_skill(name)
    assert name not in gemma.loaded_skills


def test_a_larger_model_lists_every_skill():
    with _agent(LARGE_DEFAULT_MODEL_NAME) as agent:
        catalog = agent.get_skill_catalog_system_prompt()
    for name in SKILLS_ON_WITHHELD_TOOLS:
        assert f"- {name}:" in catalog


def _switch(agent, model_id):
    stdio._apply_switch(
        agent,
        use_claude=False,
        model_id=model_id,
        claude_model=None,
        new_client=agent.chat.llm_client,
    )


def test_switching_gemma_to_a_larger_model_and_back(gemma, full_tools):
    _switch(gemma, LARGE_DEFAULT_MODEL_NAME)
    assert gemma.model_features is FULL
    assert set(gemma._tools_registry) == full_tools
    assert "DATA ANALYSIS WORKFLOW (Scratchpad)" in gemma.system_prompt
    assert gemma._scratchpad is not None
    catalog = gemma.get_skill_catalog_system_prompt()
    for name in SKILLS_ON_WITHHELD_TOOLS:
        assert f"- {name}:" in catalog

    _switch(gemma, DEFAULT_MODEL_NAME)
    assert gemma.model_features is COMPACT
    assert full_tools - set(gemma._tools_registry) == withheld_tools(COMPACT)
    assert "DATA ANALYSIS WORKFLOW (Scratchpad)" not in gemma.system_prompt


def test_a_larger_model_switched_to_gemma_loses_the_features(full_tools):
    with _agent(LARGE_DEFAULT_MODEL_NAME) as agent:
        _switch(agent, NPU_DEFAULT_MODEL_NAME)
        assert full_tools - set(agent._tools_registry) == withheld_tools(COMPACT)
        with pytest.raises(SkillUnavailableError, match="larger model"):
            agent.load_skill("image-gen")


def test_a_failed_switch_keeps_the_old_features(gemma):
    before = set(gemma._tools_registry)

    def _boom():
        raise RuntimeError("prompt composition bug")

    gemma.rebuild_system_prompt = _boom
    with pytest.raises(RuntimeError, match="rolled back"):
        _switch(gemma, LARGE_DEFAULT_MODEL_NAME)
    assert gemma.model_features is COMPACT
    assert set(gemma._tools_registry) == before
