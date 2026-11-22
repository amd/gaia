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
def _agent(platform):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(gaia.config, "_current_platform", lambda: platform)
            mp.setenv("GAIA_MEMORY_DISABLED", "1")
            mp.setenv("GAIA_DYNAMIC_TOOLS", "0")
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


def test_a_mac_is_offered_no_speech_tools_and_no_dictation_skill():
    with _agent("darwin") as agent:
        assert not SPEECH_OUTPUT_TOOLS & set(agent._tools_registry)
        assert "- dictation:" not in agent.get_skill_catalog_system_prompt()
        with pytest.raises(SkillUnavailableError, match="aren't offered on macOS"):
            agent.load_skill("dictation")
        assert "dictation" not in agent.loaded_skills
