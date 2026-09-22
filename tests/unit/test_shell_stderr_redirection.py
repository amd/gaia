# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The two stderr redirections that write nothing must not be refused.

``2>&1`` and ``2>/dev/null`` create no file and run no command, yet the
operator blocklist refused them along with real writes because it refused any
``>``. Across seven benchmark runs, 19 of 21 operator refusals were one of
these two — they are how every model asks for a test run (``pytest -q 2>&1 |
tail -20``) — and each cost the call plus a recovery step.

Every other redirection stays refused, ``2>`` to any other path included.
"""

import os
import sys

import pytest

from gaia.agents.tools.shell_tools import ShellToolsMixin

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Windows runs these through cmd.exe"
)

MISSING = "cat: missing.txt: No such file or directory"


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


def _run(command, cwd):
    """Run *command* through a fresh host, so the rate limiter never trips."""
    from gaia.agents.base.tools import get_tool_metadata

    host = _Host()
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"](
        command=command, working_directory=str(cwd)
    )


@pytest.fixture
def workdir(tmp_path):
    (tmp_path / "good.txt").write_text("hello\n")
    return tmp_path


def test_discarded_stderr_leaves_stdout_intact(workdir):
    result = _run("cat good.txt missing.txt 2>/dev/null", workdir)

    assert result["status"] == "success", result
    assert result["stdout"] == "hello\n"
    assert result["stderr"] == ""


def test_a_clean_command_still_succeeds_with_the_redirection(workdir):
    result = _run("cat good.txt 2>/dev/null", workdir)

    assert result["stdout"] == "hello\n"
    assert result["stderr"] == ""
    assert result["return_code"] == 0
    assert result["has_errors"] is False


def test_merged_stderr_arrives_on_stdout(workdir):
    result = _run("cat good.txt missing.txt 2>&1", workdir)

    assert result["status"] == "success", result
    assert "hello" in result["stdout"]
    assert MISSING in result["stdout"]
    assert result["stderr"] == ""


def test_a_merged_segment_feeds_the_merged_stream_downstream(workdir):
    result = _run('cat good.txt missing.txt 2>&1 | grep "No such file"', workdir)

    assert result["status"] == "success", result
    assert MISSING in result["stdout"]


def test_a_discarding_segment_feeds_only_stdout_downstream(workdir):
    result = _run("cat good.txt missing.txt 2>/dev/null | wc -l", workdir)

    assert result["stdout"].strip() == "1", result
    assert result["stderr"] == ""
    # pipefail still sees the upstream failure the discarded stderr described.
    assert result["has_errors"] is True


def test_discarding_stderr_does_not_launder_a_failure(workdir):
    result = _run("cat missing.txt 2>/dev/null", workdir)

    assert result["stdout"] == ""
    assert result["stderr"] == ""
    assert result["return_code"] != 0
    assert result["has_errors"] is True


def test_merging_stderr_does_not_launder_a_failure(workdir):
    result = _run("cat missing.txt 2>&1", workdir)

    assert MISSING in result["stdout"]
    assert result["return_code"] != 0
    assert result["has_errors"] is True


def test_the_redirection_applies_to_its_own_segment_only(workdir):
    result = _run("cat missing.txt 2>/dev/null; cat missing.txt", workdir)

    assert result["stdout"] == ""
    assert result["stderr"].count("No such file") == 1, result
    assert result["has_errors"] is True


def test_a_quoted_redirection_stays_an_argument(workdir):
    (workdir / "log.txt").write_text("run pytest 2>&1 please\n")

    result = _run('grep "2>&1" log.txt', workdir)

    assert result["return_code"] == 0, result
    assert "run pytest 2>&1 please" in result["stdout"]


REFUSED = [
    "cat good.txt 2>out.txt",
    "cat good.txt 2>/etc/passwd",
    "cat good.txt 1>out.txt",
    "cat good.txt >out.txt",
    "cat good.txt >>out.txt",
    "cat good.txt &>out.txt",
    "cat good.txt >&2",
    "cat good.txt 2>&2",
    "cat good.txt 2>/dev/urandom",
    "cat good.txt 2>>out.txt",
    "cat good.txt 2>&1 > out.txt",
    "cat good.txt 2>&1>out.txt",
    "cat good.txt 2>&1extra",
    "cat good.txt 2>/dev/nullx",
    "cat good.txt '2>&1' > out.txt",
    "cat good.txt 2>&1\ncat good.txt > out.txt",
]


@pytest.mark.parametrize("command", REFUSED)
def test_every_other_redirection_is_still_refused(command, workdir):
    before = sorted(os.listdir(workdir))

    result = _run(command, workdir)

    assert result["status"] == "error", result
    assert result["executed"] is False
    assert sorted(os.listdir(workdir)) == before, "a refused command wrote a file"


def test_the_refusal_names_the_two_forms_that_are_allowed(workdir):
    result = _run("cat good.txt > out.txt", workdir)

    said = f"{result['error']} {result.get('hint', '')}"
    assert "2>&1" in said and "2>/dev/null" in said, said


def test_the_model_is_told_which_two_forms_it_may_write():
    from gaia.agents.base.tools import get_tool_metadata

    host = _Host()
    host.register_shell_tools()

    described = get_tool_metadata("run_shell_command")["description"]

    assert "2>&1" in described and "2>/dev/null" in described, described


def test_a_refused_stderr_redirect_is_told_the_form_that_works(workdir):
    result = _run("cat good.txt 2>out.txt", workdir)

    said = f"{result['error']} {result.get('hint', '')}"
    assert "2>/dev/null" in said, said
