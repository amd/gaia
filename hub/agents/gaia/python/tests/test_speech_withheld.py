# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where GAIA hasn't proven its voice, the flagship isn't offered speech tools."""

from __future__ import annotations

import contextlib

import pytest
from gaiapy.agent import GaiaAgent, GaiaAgentConfig

import gaia.config
from gaia.agents.base.model_features import SPEECH_OUTPUT_TOOLS, TIER_ENV
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.llm.lemonade_client import LARGE_DEFAULT_MODEL_NAME
from gaia.skills.errors import SkillUnavailableError


@contextlib.contextmanager
def _agent(platform, dynamic_tools=False):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(gaia.config, "_current_platform", lambda: platform)
            mp.setenv("GAIA_MEMORY_DISABLED", "1")
            mp.setenv("GAIA_DYNAMIC_TOOLS", "1" if dynamic_tools else "0")
            mp.delenv("GAIA_FAST", raising=False)
            mp.delenv(TIER_ENV, raising=False)
            yield GaiaAgent(
                config=GaiaAgentConfig(
                    silent_mode=True, model_id=LARGE_DEFAULT_MODEL_NAME
                )
            )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_a_proven_platform_offers_dictation():
    with _agent("linux") as agent:
        assert SPEECH_OUTPUT_TOOLS <= set(agent._tools_registry)
        assert "- dictation:" in agent.get_skill_catalog_system_prompt()


@pytest.mark.parametrize("platform, told", [("darwin", True), ("linux", False)])
def test_the_flagship_prompt_says_speech_is_off_only_on_a_mac(platform, told):
    with _agent(platform) as agent:
        prompt = agent._prompt_text()
        assert ("**NO SPEECH OUTPUT ON THIS PC:**" in prompt) is told
        assert ("aren't offered on macOS yet" in prompt) is told


def test_a_mac_is_offered_no_speech_tools_and_no_dictation_skill():
    with _agent("darwin") as agent:
        assert not SPEECH_OUTPUT_TOOLS & set(agent._tools_registry)
        assert "- dictation:" not in agent.get_skill_catalog_system_prompt()
        with pytest.raises(SkillUnavailableError, match="aren't offered on macOS"):
            agent.load_skill("dictation")
        assert "dictation" not in agent.loaded_skills


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_dynamic_tools_validate_without_a_drift_error(platform):
    with _agent(platform, dynamic_tools=True) as agent:
        assert agent.tool_loader is not None
        # A tool withheld on this PC is absent on purpose, not registry drift.
        agent._validate_tool_config()
        assert agent._dynamic_tools_validated


@pytest.mark.parametrize("platform, listed", [("darwin", False), ("linux", True)])
def test_search_tools_lists_dictation_only_where_speech_is_offered(platform, listed):
    with _agent(platform, dynamic_tools=True) as agent:
        agent.tool_loader.rank = lambda query, docs, n: [(k, 1.0) for k in docs]
        found = agent._search_tools("dictate this document to an mp3")
        assert found["status"] == "success"
        assert ("dictation" in {s["name"] for s in found["skills"]}) is listed


def test_a_mac_still_reports_a_tool_that_is_really_missing():
    with _agent("darwin", dynamic_tools=True) as agent:
        agent._tools_registry.pop("read_clipboard")
        with pytest.raises(ValueError, match="read_clipboard") as missing:
            agent._validate_tool_config()
        assert not any(name in str(missing.value) for name in SPEECH_OUTPUT_TOOLS)
        assert not agent._dynamic_tools_validated
