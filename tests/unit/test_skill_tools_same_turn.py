# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill loaded mid-turn offers its tools on the very next model step.

The per-turn tool subset is picked from the user's message before the first
model call. A skill the model loads during that turn used to join the prompt
but not the tool list, so the tool its recipe names was missing until the next
turn. In a one-turn task there is no next turn: after loading github-triage,
whose recipe is "run every GitHub command through run_shell_command", the agent
had no shell tool and ran `gh` through run_python — a snippet the user must
approve on every call, where the skill's grant would have needed no prompt.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402

from .skills_helpers import isolated_manager, write_skill_dir  # noqa: E402

SKILL = (
    "---\nname: needs-shell\ndescription: Uses the shell. Use when testing.\n"
    "metadata:\n  gaia:\n    tools_required:\n      - run_shell_command\n"
    "---\n\n# needs-shell\n\nRun every command through run_shell_command.\n"
)


@pytest.fixture
def manager(tmp_path):
    root = tmp_path / "skills"
    write_skill_dir(root, "needs-shell", SKILL)
    return isolated_manager(tmp_path, agent_skill_dirs=[root])


@pytest.fixture
def agent():
    agent = build_doc_agent_skeleton(profile="doc", deterministic=True)
    assert "run_shell_command" in agent._tools_registry
    return agent


def _offered(agent):
    """Tool names the next model call carries, on the native and text paths."""
    native = agent._build_openai_tool_schemas(filter_to=agent._active_tool_filter)
    names = [schema["function"]["name"] for schema in native]
    text = agent._format_tools_for_prompt(filter_to=agent._active_tool_filter)
    assert all(f"- {name}(" in text for name in names)
    return names


def test_a_skill_loaded_mid_turn_offers_its_required_tools_now(agent, manager):
    agent._apply_tool_filter(["load_skill", "read_file"])
    assert "run_shell_command" not in _offered(agent)

    agent.load_skill("needs-shell", manager=manager)

    assert "run_shell_command" in agent._active_tool_filter
    assert "run_shell_command" in _offered(agent)
    assert {"load_skill", "read_file"} <= set(agent._active_tool_filter)


def test_reloading_a_skill_brings_back_tools_that_fell_out(agent, manager):
    agent.load_skill("needs-shell", manager=manager)
    agent._apply_tool_filter(["load_skill"])  # the next turn picked a narrower set

    agent.load_skill("needs-shell", manager=manager)

    assert "run_shell_command" in _offered(agent)


def test_no_tool_subset_stays_no_tool_subset(agent, manager):
    """Agents without dynamic tool selection offer every tool already."""
    assert agent._active_tool_filter is None

    agent.load_skill("needs-shell", manager=manager)

    assert agent._active_tool_filter is None


def test_a_required_tool_this_agent_lacks_is_not_invented(agent, tmp_path):
    root = tmp_path / "other"
    write_skill_dir(
        root,
        "needs-ghost",
        SKILL.replace("needs-shell", "needs-ghost").replace(
            "run_shell_command\n---", "no_such_tool\n---"
        ),
    )
    agent._apply_tool_filter(["load_skill"])

    agent.load_skill(
        "needs-ghost", manager=isolated_manager(tmp_path, agent_skill_dirs=[root])
    )

    assert agent._active_tool_filter == ["load_skill"]
