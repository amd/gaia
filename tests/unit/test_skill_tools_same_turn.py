# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill loaded mid-turn offers its tools on the very next model step.

The turn's tool list is settled from the user's message before the first model
call. A skill the model loads during that turn used to join the prompt but not
the tool list, so the tool its recipe names was missing until the next turn. In
a one-turn task there is no next turn: after loading github-triage, whose recipe
is "run every GitHub command through run_shell_command", the agent had no shell
tool and ran `gh` through run_python — a snippet the user must approve on every
call, where the skill's grant would have needed no prompt.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from gaia.eval.tool_cost import build_doc_agent_skeleton  # noqa: E402

from .skills_helpers import isolated_manager, write_skill_dir  # noqa: E402

SKILL = (
    "---\nname: needs-shell\ndescription: Uses the shell. Use when testing.\n"
    "metadata:\n  gaia:\n    tools_required:\n      - run_shell_command\n"
    "---\n\n# needs-shell\n\nRun every command through run_shell_command.\n"
)


def _skill(name: str, *required: str) -> str:
    tools = "".join(f"      - {tool}\n" for tool in required)
    return (
        f"---\nname: {name}\ndescription: A test skill. Use when testing.\n"
        f"metadata:\n  gaia:\n    tools_required:\n{tools}"
        f"---\n\n# {name}\n\nBody.\n"
    )


@pytest.fixture
def manager(tmp_path):
    root = tmp_path / "skills"
    write_skill_dir(root, "needs-shell", SKILL)
    write_skill_dir(
        root, "also-shell", _skill("also-shell", "run_shell_command", "edit_file")
    )
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
    agent._apply_tool_filter(["remember", "read_file"])
    assert "run_shell_command" not in _offered(agent)

    agent.load_skill("needs-shell", manager=manager)

    assert agent._active_tool_filter == [
        "remember",
        "read_file",
        "run_shell_command",
    ]
    assert _offered(agent) == agent._active_tool_filter


def test_reloading_a_skill_brings_back_tools_that_fell_out(agent, manager):
    agent.load_skill("needs-shell", manager=manager)
    agent._apply_tool_filter(["remember"])  # the next turn picked a narrower set

    agent.load_skill("needs-shell", manager=manager)

    assert _offered(agent) == ["remember", "run_shell_command"]


def test_no_tool_subset_stays_no_tool_subset(agent, manager):
    """Agents without dynamic tool selection offer every tool already."""
    assert agent._active_tool_filter is None

    agent.load_skill("needs-shell", manager=manager)

    assert agent._active_tool_filter is None


def test_unloading_narrows_a_filter_without_a_loader(agent, manager):
    agent._apply_tool_filter(["remember"])
    agent.load_skill("needs-shell", manager=manager)

    assert agent.unload_skill("needs-shell") is True

    assert agent._active_tool_filter == ["remember"]


def test_a_required_tool_this_agent_lacks_is_not_invented(agent, tmp_path):
    root = tmp_path / "other"
    write_skill_dir(
        root,
        "needs-ghost",
        SKILL.replace("needs-shell", "needs-ghost").replace(
            "run_shell_command\n---", "no_such_tool\n---"
        ),
    )
    agent._apply_tool_filter(["remember"])

    agent.load_skill(
        "needs-ghost", manager=isolated_manager(tmp_path, agent_skill_dirs=[root])
    )

    assert agent._active_tool_filter == ["remember"]


class TestTheLoaderOwnsTheList:
    """The loader owns the turn's list, so it has to learn about the skill.

    Widening ``_active_tool_filter`` alone lasts exactly one turn: the next
    ``_refresh_active_tool_filter`` rebuilds the list from the loader, which
    never heard about the skill. The skill body still says "use the shell tool"
    while the shell tool is gone — the same bug, one turn later.
    """

    @pytest.fixture
    def dyn_agent(self):
        agent = build_doc_agent_skeleton(
            profile="doc", deterministic=True, dynamic_tools=True
        )
        assert agent.tool_loader is not None
        assert "run_shell_command" in agent._tools_registry
        agent._refresh_active_tool_filter("do a thing")
        self.core = list(agent._active_tool_filter)
        assert "run_shell_command" not in self.core
        assert "edit_file" not in self.core
        return agent

    def test_the_tool_arrives_on_the_end_of_the_list(self, dyn_agent, manager):
        dyn_agent.load_skill("needs-shell", manager=manager)
        assert _offered(dyn_agent) == [*self.core, "run_shell_command"]

    def test_the_tool_arrives_without_a_jump(self, dyn_agent, manager):
        dyn_agent.load_skill("needs-shell", manager=manager)
        assert dyn_agent.tool_loader.jumped is False
        assert dyn_agent._conversation_request_kwargs() == {}

    def test_the_next_turn_still_offers_the_skills_tool(self, dyn_agent, manager):
        dyn_agent.load_skill("needs-shell", manager=manager)

        dyn_agent._refresh_active_tool_filter("now the next step")

        assert _offered(dyn_agent) == [*self.core, "run_shell_command"]
        assert dyn_agent.tool_loader.jumped is False

    def test_a_tool_this_agent_lacks_is_still_not_invented(self, dyn_agent, tmp_path):
        root = tmp_path / "ghost"
        write_skill_dir(
            root,
            "needs-ghost",
            SKILL.replace("needs-shell", "needs-ghost").replace(
                "run_shell_command\n---", "no_such_tool\n---"
            ),
        )

        dyn_agent.load_skill(
            "needs-ghost", manager=isolated_manager(tmp_path, agent_skill_dirs=[root])
        )

        assert dyn_agent._active_tool_filter == self.core
        assert dyn_agent.tool_loader._added == []

    def test_unloading_takes_its_tools_off_the_list(self, dyn_agent, manager):
        dyn_agent.load_skill("needs-shell", manager=manager)

        assert dyn_agent.unload_skill("needs-shell") is True

        assert _offered(dyn_agent) == self.core
        assert dyn_agent.tool_loader.jumped is False

    def test_unloading_keeps_what_another_loaded_skill_needs(self, dyn_agent, manager):
        dyn_agent.load_skill("needs-shell", manager=manager)
        dyn_agent.load_skill("also-shell", manager=manager)
        assert _offered(dyn_agent) == [*self.core, "run_shell_command", "edit_file"]

        dyn_agent.unload_skill("also-shell")

        assert _offered(dyn_agent) == [*self.core, "run_shell_command"]

        dyn_agent.load_skill("also-shell", manager=manager)
        dyn_agent.unload_skill("needs-shell")

        assert _offered(dyn_agent) == [*self.core, "run_shell_command", "edit_file"]

    def test_an_unload_that_keeps_everything_is_no_notice(self, dyn_agent, manager):
        """Nothing changed, so nothing re-reads and the UI says nothing."""
        dyn_agent.load_skill("also-shell", manager=manager)
        dyn_agent.load_skill("needs-shell", manager=manager)
        dyn_agent.conversation_history = [{"role": "user", "content": "hi"}]
        dyn_agent._tool_notice_due = False

        dyn_agent.unload_skill("needs-shell")

        assert dyn_agent._tool_list_notice() is False

    def test_after_a_jump_a_required_tool_stays(self, dyn_agent, manager):
        dyn_agent.load_skill("needs-shell", manager=manager)
        dyn_agent.tool_loader.jump("search")
        dyn_agent._apply_tool_filter(
            dyn_agent.tool_loader.offered(dyn_agent._tools_registry)
        )
        full = list(dyn_agent._active_tool_filter)

        dyn_agent.unload_skill("needs-shell")

        assert dyn_agent._active_tool_filter == full
        assert "run_shell_command" in full

    def test_a_tool_the_skill_brought_leaves_even_after_a_jump(
        self, dyn_agent, tmp_path
    ):
        """It left the registry with the skill, and a list cannot name it."""
        root = tmp_path / "own"
        write_skill_dir(
            root,
            "brings-tool",
            "---\nname: brings-tool\ndescription: Brings a tool. Use when testing.\n"
            "metadata:\n  gaia:\n    security_tier: verified\n    tools:\n"
            "      - name: ping\n        description: Ping.\n"
            "        parameters: {}\n        returns: {type: string}\n"
            "---\n\n# brings-tool\n\nBody.\n",
            tools=TOOLS_PY,
        )
        manager = isolated_manager(tmp_path, agent_skill_dirs=[root])
        dyn_agent.load_skill("brings-tool", manager=manager)
        assert "brings-tool/ping" in dyn_agent._tools_registry
        dyn_agent.tool_loader.jump("search")
        dyn_agent._apply_tool_filter(
            dyn_agent.tool_loader.offered(dyn_agent._tools_registry)
        )
        assert "brings-tool/ping" in dyn_agent._active_tool_filter

        dyn_agent.unload_skill("brings-tool")

        assert "brings-tool/ping" not in dyn_agent._tools_registry
        assert dyn_agent._active_tool_filter == list(dyn_agent._tools_registry)


TOOLS_PY = '''from gaia.agents.base.tools import tool


@tool
def ping() -> str:
    """Ping."""
    return "pong"
'''
