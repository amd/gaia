# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship's prompt names the GitHub skill before the user says "triage".

In a 14-task benchmark run, GAIA failed both GitHub questions that never used a
word the skill matcher keyed on: nothing told it a skill granting ``gh`` was
installed, so it scraped github.com for a private repo until the step limit.
When it did load the skill, the same kind of task passed in 15 steps.
"""

from __future__ import annotations

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.tools import _TOOL_REGISTRY


@pytest.fixture
def prompt(monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.delenv("GAIA_SKILL_DISCOVERY", raising=False)
    saved = dict(_TOOL_REGISTRY)
    try:
        yield GaiaAgent(config=GaiaAgentConfig(silent_mode=True)).system_prompt
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_the_prompt_lists_the_skill_that_grants_gh(prompt):
    assert "github-triage" in prompt
    assert "gh CLI" in prompt
