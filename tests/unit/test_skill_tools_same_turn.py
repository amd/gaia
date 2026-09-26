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
    # Appended after what was already offered, so the cached prefix survives.
    assert agent._active_tool_filter[:2] == ["load_skill", "read_file"]


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


class TestTheOfferSurvivesTheTurnItWasMadeIn:
    """The loader owns the turn's tool set, so it has to learn about the skill.

    Widening ``_active_tool_filter`` alone lasts exactly one turn: the next
    ``_refresh_active_tool_filter`` rebuilds the subset from the loader's own
    loaded set, which never heard about the skill. The skill body still says
    "use the shell tool" while the shell tool is gone — the same bug, one turn
    later.
    """

    @pytest.fixture
    def dyn_agent(self):
        agent = build_doc_agent_skeleton(
            profile="doc", deterministic=True, dynamic_tools=True
        )
        assert agent.tool_loader is not None
        assert "run_shell_command" in agent._tools_registry
        return agent

    def test_the_next_turn_still_offers_the_skills_tool(self, dyn_agent, manager):
        dyn_agent._refresh_active_tool_filter("do a thing")
        assert "run_shell_command" not in (dyn_agent._active_tool_filter or [])

        dyn_agent.load_skill("needs-shell", manager=manager)
        assert "run_shell_command" in _offered(dyn_agent)

        # Turn 2: the loader recomputes the subset from its own loaded set.
        dyn_agent._refresh_active_tool_filter("now the next step")

        assert "run_shell_command" in _offered(dyn_agent)

    def test_the_tool_is_admitted_to_the_loader_not_just_the_filter(
        self, dyn_agent, manager
    ):
        dyn_agent._refresh_active_tool_filter("do a thing")

        dyn_agent.load_skill("needs-shell", manager=manager)

        assert "run_shell_command" in dyn_agent.tool_loader._loaded

    def test_running_the_skills_tool_is_not_an_escape_hatch(self, dyn_agent, manager):
        """The intended happy path must not inflate the tau-tuning signal."""
        dyn_agent._refresh_active_tool_filter("do a thing")
        dyn_agent.load_skill("needs-shell", manager=manager)
        before = dyn_agent.tool_loader._escape_hatch_count

        dyn_agent._on_tool_invoked("run_shell_command")

        assert dyn_agent.tool_loader._escape_hatch_count == before

    def test_a_tool_this_agent_lacks_is_still_not_invented(self, dyn_agent, tmp_path):
        root = tmp_path / "ghost"
        write_skill_dir(
            root,
            "needs-ghost",
            SKILL.replace("needs-shell", "needs-ghost").replace(
                "run_shell_command\n---", "no_such_tool\n---"
            ),
        )
        dyn_agent._refresh_active_tool_filter("do a thing")

        dyn_agent.load_skill(
            "needs-ghost", manager=isolated_manager(tmp_path, agent_skill_dirs=[root])
        )

        assert "no_such_tool" not in (dyn_agent._active_tool_filter or [])
        assert "no_such_tool" not in dyn_agent.tool_loader._loaded


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
