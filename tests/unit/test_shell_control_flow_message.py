# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shell control flow is named as such, not reported as a missing file.

Commands run directly rather than through a shell, so `for` is not a binary
that happens to be absent — it can never run. Reporting it as ``[Errno 2] No
such file or directory: 'for'`` sends the agent looking for a PATH problem that
does not exist, which is what it did in a measured benchmark run.
"""

import pytest

from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools.shell_tools import ShellToolsMixin


class _FullAccessHost(ShellToolsMixin):
    """Full access, so nothing else can be what refuses the command."""

    class console:
        full_access = True
        auto_approve_gated_tools = True


def _run(command, cwd):
    host = _FullAccessHost()
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"](
        command=command, working_directory=str(cwd), timeout=30
    )


@pytest.mark.parametrize(
    "command,keyword",
    [
        ("for f in a b; do echo $f; done", "for"),
        ("while true; do echo x; done", "while"),
        ("if [ -f x ]; then echo y; fi", "if"),
        ("until false; do echo x; done", "until"),
        ("select f in a b; do echo $f; done", "select"),
    ],
)
def test_control_flow_is_named_not_reported_as_a_missing_file(
    command, keyword, tmp_path
):
    result = _run(command, tmp_path)

    assert result["status"] == "error", result
    assert keyword in result["error"], result
    assert "shell control flow" in result["error"], result
    assert "No such file or directory" not in result["error"], result


def test_the_refusal_names_something_that_would_work(tmp_path):
    result = _run("for f in a b; do echo $f; done", tmp_path)

    assert "run_python" in result["hint"], result


def test_a_binary_whose_name_starts_like_a_keyword_still_runs(tmp_path):
    """`find` begins with `fi`; matching must be on the whole token."""
    (tmp_path / "keep.txt").write_text("x\n")

    result = _run("find . -name keep.txt", tmp_path)

    assert result["status"] == "success", result
    assert "keep.txt" in result["stdout"]


def test_a_case_statement_is_refused_clearly_by_the_separator_check(tmp_path):
    """`case` uses `;;`, which reads as an empty command between separators.

    A different refusal, reached earlier, but still one the agent can act on —
    so this pins that it stays clear rather than falling through to exec.
    """
    result = _run("case $x in a) echo a;; esac", tmp_path)

    assert result["status"] == "error", result
    assert "No such file or directory" not in str(result.get("error")), result
