# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The flagship's code-search scope is allowed_paths, not the project root.

Drives the real `_register_tools` line rather than the mixin with a
test-supplied ceiling — the mixin tests cannot see what the agent passes in,
which is where #3544 actually survived its first fix.
"""

from unittest.mock import patch

import pytest

gaia_agent = pytest.importorskip("gaia_agent.agent")
chat_agent = pytest.importorskip("gaia_agent_chat.agent")
GaiaAgent = gaia_agent.GaiaAgent
GaiaAgentConfig = gaia_agent.GaiaAgentConfig
ChatAgent = chat_agent.ChatAgent


def _register_tools_only(config, project_root):
    """Run just the flagship's tool-registration body, nothing else."""
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = config
    agent.observers = []  # __del__ runs on an agent that never ran __init__
    with (
        patch.object(GaiaAgent, "_maybe_build_skill_loader", return_value=None),
        patch.object(GaiaAgent, "_maybe_build_skill_discovery", return_value=None),
        patch.object(GaiaAgent, "register_skill_library_tools"),
        patch.object(GaiaAgent, "register_skill_learning_tools"),
        patch.object(GaiaAgent, "register_code_index_tools"),
        patch.object(GaiaAgent, "_project_map_root", return_value=project_root),
        patch.object(ChatAgent, "_register_tools"),
    ):
        agent._register_tools()
    return agent


def test_a_session_started_inside_a_repo_can_still_reach_the_others(tmp_path):
    projects = tmp_path / "projects"
    first = projects / "a"
    first.mkdir(parents=True)
    (projects / "b").mkdir()

    agent = _register_tools_only(
        GaiaAgentConfig(allowed_paths=[str(projects)]), str(first)
    )

    assert agent._repo_path == str(first)
    assert agent._code_index_ceilings == (str(projects),)


def test_every_allowed_root_is_reachable_not_just_the_first(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()

    agent = _register_tools_only(
        GaiaAgentConfig(allowed_paths=[str(one), str(two)]), str(one)
    )

    assert agent._code_index_ceilings == (str(one), str(two))
