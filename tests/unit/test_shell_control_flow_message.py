# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shell control flow is named as such, not reported as a missing file.

Outside full access commands run directly rather than through a shell, so `for`
is not a binary that happens to be absent — it can never run. Reporting it as
``[Errno 2] No such file or directory: 'for'`` sends the agent looking for a
PATH problem that does not exist, which is what it did in a measured benchmark
run. Full access hands the whole line to the shell, which runs it.
"""

import os

import pytest

from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools.shell_tools import ShellToolsMixin


class _FullAccessHost(ShellToolsMixin):
    """Full access, so nothing else can be what refuses the command."""

    class console:
        full_access = True
        auto_approve_gated_tools = True


class _AskHost(ShellToolsMixin):
    class console:
        full_access = False


def _run(command, cwd):
    host = _FullAccessHost()
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"](
        command=command, working_directory=str(cwd), timeout=30
    )


CONTROL_FLOW = [
    ("for f in a b; do echo $f; done", "for"),
    ("while true; do echo x; done", "while"),
    ("if [ -f x ]; then echo y; fi", "if"),
    ("until false; do echo x; done", "until"),
    ("select f in a b; do echo $f; done", "select"),
]


@pytest.mark.parametrize("command,keyword", CONTROL_FLOW)
def test_control_flow_is_named_not_reported_as_a_missing_file(command, keyword):
    error, _steps = _AskHost()._validate_shell_command(command)

    assert error["executed"] is False, error
    assert keyword in error["error"], error
    assert "shell control flow" in error["error"], error


def test_the_refusal_names_something_that_would_work():
    error, _steps = _AskHost()._validate_shell_command("for f in a b; do echo $f; done")

    assert "run_python" in error["hint"], error


@pytest.mark.parametrize(
    "command", [c for c, _ in CONTROL_FLOW] + ["case $x in a) echo a;; esac"]
)
def test_full_access_hands_control_flow_to_the_shell(command):
    error, steps = _FullAccessHost()._validate_shell_command(command)

    assert error is None
    # No steps: the shell gets the whole line, not one piece of the loop.
    assert steps == []


def test_a_loop_runs_under_full_access(tmp_path):
    command = (
        "for %i in (a b) do @echo item-%i"
        if os.name == "nt"
        else "for i in a b; do echo item-$i; done"
    )

    result = _run(command, tmp_path)

    assert result["status"] == "success", result
    assert "item-a" in result["stdout"] and "item-b" in result["stdout"]
    assert "shell control flow" not in str(result.get("error")), result


def test_a_condition_runs_under_full_access(tmp_path):
    (tmp_path / "x").write_text("x\n")
    command = (
        "if exist x (echo found) else (echo missing)"
        if os.name == "nt"
        else "if [ -f x ]; then echo found; else echo missing; fi"
    )

    result = _run(command, tmp_path)

    assert result["status"] == "success", result
    assert "found" in result["stdout"]


def test_a_binary_whose_name_starts_like_a_keyword_still_runs(tmp_path):
    """`find` begins with `fi`; matching must be on the whole token."""
    (tmp_path / "keep.txt").write_text("x\n")

    result = _run("find . -name keep.txt", tmp_path)

    assert result["status"] == "success", result
    assert "keep.txt" in result["stdout"]
