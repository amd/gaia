# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The model's copy of a shell result leaves out what it already knows.

A result echoed the command up to three times and carried a dozen fields that
are empty or default on almost every call; on short commands that wrapper was
larger than the output. The full result still reaches everything else.
"""

import json

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.shell_tools import lean_shell_result

_COMMAND = "ls -la /app | head -3"


def _result(**changes):
    result = {
        "command": _COMMAND,
        "shell": "/usr/bin/bash",
        "timeout": 30,
        "timeout_class": "default",
        "cwd": "/app",
        "status": "success",
        "stdout": "total 8\n",
        "stderr": "",
        "return_code": 0,
        "has_errors": False,
        "duration_seconds": 0.005863210004463326,
        "output_truncated": False,
        "steps": [{"command": _COMMAND, "return_code": 0}],
        "check_result": None,
    }
    result.update(changes)
    return result


def test_a_plain_success_keeps_only_what_the_model_cannot_know():
    assert lean_shell_result(_result()) == {
        "cwd": "/app",
        "status": "success",
        "stdout": "total 8\n",
        "return_code": 0,
        "duration_seconds": 0.0,
    }


def test_the_command_is_never_echoed_back():
    assert _COMMAND not in json.dumps(lean_shell_result(_result()))


def test_the_full_result_is_left_untouched():
    full = _result()
    before = json.dumps(full, sort_keys=True)
    lean_shell_result(full)
    assert json.dumps(full, sort_keys=True) == before


@pytest.mark.parametrize(
    "changes, kept",
    [
        ({"stderr": "warning: x\n"}, {"stderr": "warning: x\n"}),
        ({"has_errors": True, "status": "error"}, {"has_errors": True}),
        ({"output_truncated": True}, {"output_truncated": True}),
        ({"check_result": {"passed": False}}, {"check_result": {"passed": False}}),
        ({"error": "exited with code 2"}, {"error": "exited with code 2"}),
        ({"hint": "narrow it"}, {"hint": "narrow it"}),
        ({"executed": False}, {"executed": False}),
    ],
)
def test_a_field_that_says_something_is_kept(changes, kept):
    lean = lean_shell_result(_result(**changes))
    for key, value in kept.items():
        assert lean[key] == value


def test_a_timeout_keeps_the_limit_that_killed_it():
    lean = lean_shell_result(_result(timed_out=True, status="error", timeout=600))
    assert lean["timed_out"] is True
    assert lean["timeout"] == 600
    assert lean["timeout_class"] == "default"


def test_a_line_of_several_steps_keeps_each_exit_code():
    steps = [
        {"command": "make", "return_code": 0},
        {"command": "make test", "return_code": 2},
    ]
    assert lean_shell_result(_result(steps=steps))["steps"] == steps


class _Agent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool(model_view=lambda result: {"kept": result["kept"]})
        def viewed_tool_for_test() -> dict:
            """A tool whose model copy is smaller than its result."""
            return {"kept": 1, "dropped": 2}

        @tool
        def plain_tool_for_test() -> dict:
            """A tool with no model view."""
            return {"kept": 1, "dropped": 2}


@pytest.fixture
def agent():
    saved = dict(_TOOL_REGISTRY)
    try:
        yield _Agent(silent_mode=True, skip_lemonade=True)
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _text(message):
    return message["content"][0]["text"]


def test_the_message_to_the_model_carries_the_tools_model_view(agent):
    full = {"kept": 1, "dropped": 2}
    viewed = agent._create_tool_message("viewed_tool_for_test", full)
    assert json.loads(_text(viewed)) == {"kept": 1}
    assert full == {"kept": 1, "dropped": 2}


def test_a_tool_with_no_model_view_is_sent_whole(agent):
    full = {"kept": 1, "dropped": 2}
    plain = agent._create_tool_message("plain_tool_for_test", full)
    assert json.loads(_text(plain)) == full


def test_run_shell_command_registers_the_lean_view():
    from gaia.agents.tools.shell_tools import ShellToolsMixin

    class _Host(ShellToolsMixin):
        pass

    saved = dict(_TOOL_REGISTRY)
    try:
        _Host().register_shell_tools()
        assert _TOOL_REGISTRY["run_shell_command"]["model_view"] is lean_shell_result
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)
