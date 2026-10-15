# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A command that fails comes back as ``status: "error"``, naming the cause.

``run_shell_command`` used to report every command that ran as a success, so
the Agent UI and TUI drew a failed build as a passed step. Now a non-zero exit,
a timeout or a missing program is an error with an ``error`` that names the
exit code and the last stderr line, and stdout/stderr/return_code stay intact.

The exception is a program whose exit 1 is documented as an answer — grep's
"no match", diff's "the inputs differ". Those stay a success and say why in
``expected_exit``.

The process layer (``_run_step``) is stubbed where the point is the verdict, so
these run on every platform; the pipeline test launches real processes.
"""

import subprocess
import sys

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.checks import CheckResult
from gaia.agents.base.errors import exit_error
from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools import shell_tools
from gaia.agents.tools.shell_tools import ShellToolsMixin, _answer_exit


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


def _run(command, cwd, **kwargs):
    host = _Host()
    host.register_shell_tools()
    tool = get_tool_metadata("run_shell_command")["function"]
    return tool(command=command, working_directory=str(cwd), **kwargs)


@pytest.fixture
def fake_steps(monkeypatch):
    """Stub the process layer: each step's text maps to (code, stdout, stderr)."""
    outcomes = {}

    def fake_run_step(step, cwd, timeout, granted, waiter=None, bypass=False):
        outcome = outcomes[step.text]
        if isinstance(outcome, BaseException):
            raise outcome
        code, out, err = outcome
        return subprocess.CompletedProcess(step.text, code, out, err)

    monkeypatch.setattr(shell_tools, "_run_step", fake_run_step)
    return outcomes


# ---------------------------------------------------------------------------
# Failures are errors
# ---------------------------------------------------------------------------


def test_a_non_zero_exit_is_an_error_naming_the_code_and_stderr(fake_steps, tmp_path):
    fake_steps["ls missing"] = (2, "", "ls: missing: No such file\n")

    result = _run("ls missing", tmp_path)

    assert result["status"] == "error"
    assert (
        result["error"] == "'ls missing' exited with code 2: ls: missing: No such file"
    )
    assert result["return_code"] == 2
    assert result["stderr"] == "ls: missing: No such file\n"
    assert result["has_errors"] is True
    assert "expected_exit" not in result


def test_a_zero_exit_is_a_success(fake_steps, tmp_path):
    fake_steps["ls"] = (0, "a.txt\n", "")

    result = _run("ls", tmp_path)

    assert result["status"] == "success"
    assert result["has_errors"] is False
    assert "error" not in result


def test_a_failure_a_later_step_walked_past_is_still_the_error(fake_steps, tmp_path):
    fake_steps["ls missing"] = (2, "", "no such file\n")
    fake_steps["echo done"] = (0, "done\n", "")

    result = _run("ls missing ; echo done", tmp_path)

    assert result["return_code"] == 0
    assert result["status"] == "error"
    assert result["error"] == "'ls missing' exited with code 2: no such file"


def test_a_failure_the_line_handles_with_or_is_a_success(fake_steps, tmp_path):
    fake_steps["ls missing"] = (2, "", "no such file\n")
    fake_steps["echo none"] = (0, "none\n", "")

    result = _run("ls missing || echo none", tmp_path)

    assert result["status"] == "success", result


def test_a_timeout_is_an_error(fake_steps, tmp_path):
    fake_steps["ls"] = subprocess.TimeoutExpired("ls", 5, output=b"partial", stderr=b"")

    result = _run("ls", tmp_path, timeout=5)

    assert result["status"] == "error"
    assert result["timed_out"] is True
    assert "timed out after 5 seconds" in result["error"]
    assert result["stdout"] == "partial"


def test_a_check_whose_summary_failed_is_an_error_even_at_exit_zero(
    fake_steps, tmp_path, monkeypatch
):
    # `pytest | tail` exits with tail's 0 on cmd.exe; the summary is the verdict.
    fake_steps["ls"] = (0, "1 failed, 3 passed\n", "")
    monkeypatch.setattr(
        shell_tools,
        "check_from_command",
        lambda *a, **k: CheckResult(
            label="pytest",
            target="pytest",
            kind="test",
            passed=False,
            summary="1 failed, 3 passed",
        ),
    )

    result = _run("ls", tmp_path)

    assert result["status"] == "error"
    assert result["error"] == "pytest reported failures: 1 failed, 3 passed"


# ---------------------------------------------------------------------------
# Documented answer exits stay a success
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, reason",
    [
        ("grep needle notes.txt", "grep exits 1 when nothing matches"),
        ("findstr needle notes.txt", "findstr exits 1 when nothing matches"),
        ("diff a.txt b.txt", "diff exits 1 when the inputs differ"),
    ],
)
def test_an_answer_exit_is_a_success_that_says_why(
    fake_steps, tmp_path, command, reason
):
    fake_steps[command] = (1, "", "")

    result = _run(command, tmp_path)

    assert result["status"] == "success", result
    assert result["return_code"] == 1
    assert result["expected_exit"] == reason
    assert result["has_errors"] is False
    assert result["steps"][-1]["expected_exit"] == reason
    assert not Agent._is_error_result(result)


def test_exit_two_from_grep_is_still_an_error(fake_steps, tmp_path):
    fake_steps["grep needle notes.txt"] = (2, "", "grep: notes.txt: No such file\n")

    result = _run("grep needle notes.txt", tmp_path)

    assert result["status"] == "error"
    assert "exited with code 2" in result["error"]


def test_a_no_match_followed_by_more_work_is_not_a_failure(fake_steps, tmp_path):
    fake_steps["grep needle notes.txt"] = (1, "", "")
    fake_steps["ls"] = (0, "notes.txt\n", "")

    result = _run("grep needle notes.txt ; ls", tmp_path)

    assert result["status"] == "success", result
    assert "expected_exit" not in result


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["git", "diff", "--exit-code"], "git diff --exit-code exits 1"),
        (["git", "diff", "--quiet", "HEAD"], "git diff --exit-code exits 1"),
        (["git", "-C", "repo", "grep", "x"], "git grep exits 1"),
        (["/usr/bin/grep", "x"], "grep exits 1"),
        (["C:\\Git\\usr\\bin\\grep.exe", "x"], "grep exits 1"),
    ],
)
def test_answer_exits_are_read_from_the_program(argv, expected):
    assert _answer_exit(argv, 1).startswith(expected)


@pytest.mark.parametrize(
    "argv, code",
    [
        (["git", "diff"], 1),  # plain git diff exits 0 whatever it prints
        (["git", "status"], 1),
        (["ls"], 1),
        (["grep", "x"], 2),
        (["diff", "a", "b"], 2),
    ],
)
def test_other_exits_are_not_answers(argv, code):
    assert _answer_exit(argv, code) is None


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows runs each pipeline through cmd.exe"
)
def test_grep_no_match_after_a_failed_stage_is_a_failure(tmp_path):
    (tmp_path / "notes.txt").write_text("pear\n")

    no_match = _run("cat notes.txt | grep apple", tmp_path)
    upstream_failed = _run("cat missing.txt | grep apple", tmp_path)

    assert no_match["status"] == "success", no_match
    assert no_match["expected_exit"] == "grep exits 1 when nothing matches"
    assert upstream_failed["status"] == "error", upstream_failed


# ---------------------------------------------------------------------------
# The shared helpers
# ---------------------------------------------------------------------------


def test_exit_error_names_the_last_stderr_line():
    assert exit_error("make", 2, "warning\nmake: *** [all] Error 1\n\n") == (
        "'make' exited with code 2: make: *** [all] Error 1"
    )
    assert exit_error("make", 2, b"boom\n") == "'make' exited with code 2: boom"
    assert exit_error("make", 2) == "'make' exited with code 2"


def test_the_agent_loop_counts_a_bare_non_zero_exit_as_an_error():
    assert Agent._is_error_result({"status": "success", "return_code": 3})
    assert not Agent._is_error_result(
        {"status": "success", "return_code": 1, "expected_exit": "grep: no match"}
    )


def test_under_2_to_1_the_error_reads_the_verdict_from_stdout(fake_steps, tmp_path):
    fake_steps["ls missing"] = (2, "ls: missing: No such file\n", "")

    result = _run("ls missing", tmp_path)

    assert (
        result["error"] == "'ls missing' exited with code 2: ls: missing: No such file"
    )


def test_an_or_does_not_handle_a_failure_before_a_semicolon(fake_steps, tmp_path):
    # sh: `||` covers its own and-or list only, so `ls missing` still failed.
    fake_steps["ls missing"] = (2, "", "no such file\n")
    fake_steps["grep FAILED out.txt"] = (1, "", "")
    fake_steps["echo none"] = (0, "none\n", "")

    result = _run("ls missing ; grep FAILED out.txt || echo none", tmp_path)

    assert result["status"] == "error", result
    assert result["error"] == "'ls missing' exited with code 2: no such file"


def test_an_or_handles_a_failure_earlier_in_its_own_list(fake_steps, tmp_path):
    fake_steps["ls missing"] = (2, "", "no such file\n")
    fake_steps["echo none"] = (0, "none\n", "")

    result = _run("ls missing && ls || echo none", tmp_path)

    assert result["status"] == "success", result


def test_a_passing_check_piped_into_a_grep_that_finds_nothing_passes(
    fake_steps, tmp_path, monkeypatch
):
    seen = {}

    def fake_check(command, segments, return_code, stdout, stderr):
        seen["return_code"] = return_code
        return CheckResult(
            label="pytest",
            target="pytest",
            kind="test",
            passed=return_code == 0,
            summary="",
        )

    monkeypatch.setattr(shell_tools, "check_from_command", fake_check)
    fake_steps["ls | grep FAILED"] = (1, "", "")

    result = _run("ls | grep FAILED", tmp_path)

    assert seen["return_code"] == 0
    assert result["status"] == "success", result
    assert result["expected_exit"] == "grep exits 1 when nothing matches"
