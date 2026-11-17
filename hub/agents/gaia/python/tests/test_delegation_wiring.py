# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship offers delegate_task on the full tier, and only as max_helpers allows."""

from __future__ import annotations

import contextlib

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.model_features import TIER_ENV
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.delegate_tools import DELEGATION_PROMPT, MAX_HELPERS_ENV
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, LARGE_DEFAULT_MODEL_NAME


@contextlib.contextmanager
def _agent(model_id, env=None, **config):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("GAIA_MEMORY_DISABLED", "1")
            mp.setenv("GAIA_DYNAMIC_TOOLS", "0")
            mp.delenv("GAIA_FAST", raising=False)
            mp.delenv(TIER_ENV, raising=False)
            mp.delenv(MAX_HELPERS_ENV, raising=False)
            for name, value in (env or {}).items():
                mp.setenv(name, value)
            yield GaiaAgent(
                config=GaiaAgentConfig(silent_mode=True, model_id=model_id, **config)
            )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _offers(agent) -> bool:
    schemas = agent._build_openai_tool_schemas()
    return "delegate_task" in {s["function"]["name"] for s in schemas}


def test_a_full_tier_model_is_offered_helpers():
    with _agent(LARGE_DEFAULT_MODEL_NAME) as agent:
        assert "delegate_task" in agent._tools_registry
        assert _offers(agent)
        assert DELEGATION_PROMPT in agent.system_prompt
        assert "delegate_task" not in _TOOL_REGISTRY


def test_a_compact_model_is_not_offered_helpers():
    with _agent(DEFAULT_MODEL_NAME) as agent:
        assert "delegate_task" not in agent._tools_registry
        assert not _offers(agent)
        assert DELEGATION_PROMPT not in agent.system_prompt


@pytest.mark.parametrize(
    "config, env",
    [({"max_helpers": 2}, None), ({}, {MAX_HELPERS_ENV: "2"})],
    ids=["config", "env"],
)
def test_a_compact_model_gets_helpers_when_asked_for_by_number(config, env):
    with _agent(DEFAULT_MODEL_NAME, env=env, **config) as agent:
        assert "delegate_task" in agent._tools_registry
        assert _offers(agent)
        assert agent.model_features.delegation is True
        assert DELEGATION_PROMPT in agent.system_prompt


@pytest.mark.parametrize("model_id", [LARGE_DEFAULT_MODEL_NAME, DEFAULT_MODEL_NAME])
def test_zero_max_helpers_leaves_the_tool_out(model_id):
    with _agent(model_id, max_helpers=0) as agent:
        assert "delegate_task" not in agent._tools_registry
        assert DELEGATION_PROMPT not in agent.system_prompt


def test_a_model_switch_to_a_compact_model_withdraws_helpers():
    with _agent(LARGE_DEFAULT_MODEL_NAME) as agent:
        agent.apply_model_features(DEFAULT_MODEL_NAME)
        assert "delegate_task" not in agent._tools_registry
        agent.apply_model_features(LARGE_DEFAULT_MODEL_NAME)
        assert "delegate_task" in agent._tools_registry


def test_a_bad_env_setting_stops_the_agent_with_the_reason():
    with pytest.raises(ValueError, match=MAX_HELPERS_ENV):
        with _agent(LARGE_DEFAULT_MODEL_NAME, env={MAX_HELPERS_ENV: "many"}):
            pass


def test_a_helper_reads_inside_the_flagships_folders(tmp_path):
    (tmp_path / "a.txt").write_text("inside")
    with _agent(LARGE_DEFAULT_MODEL_NAME, allowed_paths=[str(tmp_path)]) as agent:
        helper = agent._build_helper()
        assert {str(p) for p in helper.path_validator.allowed_paths} == {
            str(p) for p in agent.path_validator.allowed_paths
        }
        result = helper._execute_tool(
            "read_file", {"file_path": str(tmp_path / "a.txt")}
        )
        assert result["status"] == "success"
        assert "inside" in result["content"]
