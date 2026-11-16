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

import shutil
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


# ---------------------------------------------------------------------------
# Adversarial: the verdict follows the shell's rules, not the output's words
# ---------------------------------------------------------------------------

ANSWER_PROGRAMS = ["grep", "egrep", "fgrep", "rg", "findstr", "diff", "cmp", "fc"]


@pytest.mark.parametrize("program", ANSWER_PROGRAMS)
def test_every_answer_program_is_a_success_at_1_and_an_error_at_2(program):
    assert _answer_exit([program, "x", "y"], 1) is not None
    for code in (2, 3, 127, 255, -9):
        assert _answer_exit([program, "x", "y"], code) is None, (program, code)


@pytest.mark.parametrize(
    "argv",
    [
        ["grepx", "x"],
        ["mygrep", "x"],
        ["grep2", "x"],
        ["/opt/grep-wrapper", "x"],
        ["diffstat", "x"],
        ["git", "log"],
        ["git", "diff", "--stat"],
        ["git", "show"],
        ["python", "-c", "import sys; sys.exit(1)"],
        ["pytest"],
        ["make"],
    ],
)
def test_lookalike_or_ordinary_programs_exiting_1_are_failures(argv):
    assert _answer_exit(argv, 1) is None


@pytest.mark.parametrize(
    "argv",
    [
        [r"C:\Program Files\Git\usr\bin\GREP.EXE", "x"],
        ["/usr/local/bin/rg", "x"],
        ["git", "diff", "--stat", "--exit-code"],
        ["git", "--no-pager", "grep", "x"],
    ],
)
def test_answer_programs_are_recognised_however_they_are_spelled(argv):
    assert _answer_exit(argv, 1) is not None


@pytest.mark.parametrize(
    "line, outcomes, status, names",
    [
        # && skips the rest once a command fails.
        (
            "ls missing && echo x",
            {"ls missing": (2, "", "nope\n")},
            "error",
            "ls missing",
        ),
        # || handles only the failure it follows; the next one stands.
        (
            "ls missing || ls missing2",
            {"ls missing": (2, "", "a\n"), "ls missing2": (2, "", "b\n")},
            "error",
            "ls missing2",
        ),
        (
            "ls missing || echo x ; ls missing2",
            {
                "ls missing": (2, "", "a\n"),
                "echo x": (0, "x\n", ""),
                "ls missing2": (2, "", "b\n"),
            },
            "error",
            "ls missing2",
        ),
        (
            "ls missing || echo x && ls missing2",
            {
                "ls missing": (2, "", "a\n"),
                "echo x": (0, "x\n", ""),
                "ls missing2": (2, "", "b\n"),
            },
            "error",
            "ls missing2",
        ),
        (
            "ls a ; ls missing || echo x",
            {
                "ls a": (0, "a\n", ""),
                "ls missing": (2, "", "a\n"),
                "echo x": (0, "x\n", ""),
            },
            "success",
            None,
        ),
        (
            "ls missing ; ls a ; echo x",
            {
                "ls missing": (2, "", "first\n"),
                "ls a": (0, "a\n", ""),
                "echo x": (0, "x\n", ""),
            },
            "error",
            "ls missing",
        ),
        (
            "ls m1 ; ls m2 ; ls m3",
            {
                "ls m1": (1, "", "one\n"),
                "ls m2": (2, "", "two\n"),
                "ls m3": (0, "", ""),
            },
            "error",
            "ls m1",
        ),
    ],
)
def test_connectors_decide_which_failure_stands(
    fake_steps, tmp_path, line, outcomes, status, names
):
    fake_steps.update(outcomes)

    result = _run(line, tmp_path)

    assert result["status"] == status, result
    if names:
        assert result["error"].startswith(f"'{names}' exited with code")
    else:
        assert "error" not in result


def test_and_skips_the_command_after_a_failure(fake_steps, tmp_path):
    fake_steps["ls missing"] = (2, "", "nope\n")

    result = _run("ls missing && echo x", tmp_path)

    assert [s["command"] for s in result["steps"]] == ["ls missing"]


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Like ``fake_steps``, but each outcome also carries every stage's exit code."""
    outcomes = {}

    def fake_run_step(step, cwd, timeout, granted, waiter=None, bypass=False):
        code, stages = outcomes[step.text]
        completed = subprocess.CompletedProcess(step.text, code, "", "")
        completed.stage_codes = stages
        return completed

    monkeypatch.setattr(shell_tools, "_run_step", fake_run_step)
    return outcomes


@pytest.mark.parametrize(
    "line, code, stages, status",
    [
        ("ls | grep x", 1, [0, 1], "success"),  # grep found nothing
        ("ls | grep x", 1, [2, 1], "error"),  # ls failed upstream
        ("ls | grep x", 1, [1, 1], "error"),
        ("ls | grep x", 2, [0, 2], "error"),  # grep itself failed
        ("ls | sort", 2, [0, 2], "error"),
        ("ls | sort", 1, [0, 1], "error"),  # sort has no answer exit
        ("grep x f.txt | sort", 0, [1, 0], "success"),  # upstream no-match
        ("ls | grep x | sort", 1, [0, 0, 1], "error"),
        ("ls | sort | grep x", 1, [0, 0, 1], "success"),
        ("ls | sort | grep x", 1, [0, 3, 1], "error"),
    ],
)
def test_in_a_pipeline_an_answer_exit_counts_only_from_the_last_stage(
    fake_pipeline, tmp_path, line, code, stages, status
):
    fake_pipeline[line] = (code, stages)

    result = _run(line, tmp_path)

    assert result["status"] == status, result


@pytest.mark.parametrize(
    "stdout, stderr",
    [
        ("ERROR: disk almost full\n", ""),
        ("", "error: this is only a warning\n"),
        ("FAILED\nTraceback (most recent call last):\n", "fatal: but not really\n"),
        ("exit code 1\nreturn_code: 2\n", "status: error\n"),
    ],
)
def test_output_that_says_error_at_exit_zero_stays_a_success(
    fake_steps, tmp_path, stdout, stderr
):
    fake_steps["ls"] = (0, stdout, stderr)

    result = _run("ls", tmp_path)

    assert result["status"] == "success", result
    assert result["has_errors"] is False
    assert "error" not in result
    assert not Agent._is_error_result(result)


@pytest.mark.parametrize("code", [1, 2, 126, 127, 255, -9, -15, 3221225477])
def test_any_other_non_zero_exit_is_an_error_naming_the_code(
    fake_steps, tmp_path, code
):
    fake_steps["ls"] = (code, "", "")

    result = _run("ls", tmp_path)

    assert result["status"] == "error"
    assert f"exited with code {code}" in result["error"]
    assert Agent._is_error_result(result)


@pytest.mark.parametrize(
    "result, is_error",
    [
        ({"status": "success", "return_code": 1, "expected_exit": ""}, True),
        ({"status": "success", "return_code": 1, "expected_exit": None}, True),
        ({"status": "success", "return_code": "1"}, True),
        ({"status": "success", "return_code": 0, "has_errors": True}, True),
        ({"status": "success", "success": False}, True),
        ({"status": "error", "return_code": 0}, True),
        ({"status": "error", "return_code": 1, "expected_exit": "grep"}, True),
        ({"status": "success", "return_code": 0}, False),
        ({"status": "success"}, False),
    ],
)
def test_the_agent_loop_predicate_cannot_be_talked_out_of_an_error(result, is_error):
    assert Agent._is_error_result(result) is is_error


# ---------------------------------------------------------------------------
# Existence probes: exit 1 is "not found", an answer the model still reads
# ---------------------------------------------------------------------------


@pytest.fixture
def approved(monkeypatch):
    """Run a line whose confirmation the user already approved.

    A probe like ``where`` is confirm-tier, so outside the agent loop it is
    refused before it runs; a host with a console is one the gate already
    asked, which is how the loop reaches the tool.
    """
    import types

    monkeypatch.setattr(
        "gaia.agents.base.console.auto_approve_env_enabled", lambda: False
    )
    host = _Host()
    host.console = types.SimpleNamespace()
    host.register_shell_tools()
    tool = get_tool_metadata("run_shell_command")["function"]
    return lambda command, cwd: tool(command=command, working_directory=str(cwd))


PROBES = [
    (["where", "soffice"], "not found: soffice"),
    (["where.exe", "/q", "soffice"], "not found: soffice"),
    (["which", "soffice"], "not found: soffice"),
    (["command", "-v", "soffice"], "not found: soffice"),
    (["test", "-e", "out.xlsx"], "test -e out.xlsx is false"),
    (["test", "-f", "out.xlsx"], "test -f out.xlsx is false"),
    (["test", "-d", "build"], "test -d build is false"),
    (["[", "-f", "out.xlsx", "]"], "test -f out.xlsx is false"),
    (["powershell", "-Command", "Get-Command soffice"], "not found: soffice"),
    (["pwsh", "-c", "gcm soffice -ErrorAction SilentlyContinue"], "not found: soffice"),
]


@pytest.mark.parametrize("argv, says", PROBES)
def test_a_probe_exiting_1_is_an_answer_naming_what_was_not_found(argv, says):
    assert _answer_exit(argv, 1).startswith(says)


@pytest.mark.parametrize("argv, _says", PROBES)
def test_a_probe_exiting_2_or_more_is_still_a_failure(argv, _says):
    for code in (2, 3, 127, 255, -9):
        assert _answer_exit(argv, code) is None, (argv, code)


@pytest.mark.parametrize(
    "argv",
    [
        ["where"],  # nothing asked about: a usage error
        ["command", "soffice"],  # runs soffice, not a lookup
        ["test", "-f", "a", "-a", "-x", "b"],  # compound test
        ["test", "-n", "x"],  # string test, not existence
        ["test", "a", "=", "b"],
        ["[", "-f", "a"],  # unterminated bracket
        ["powershell", "-Command", "Get-Command x; Remove-Item y"],
        ["powershell", "-Command", "Get-Command x | ForEach-Object { & $_ }"],
        ["powershell", "-Command", "Get-Process soffice"],
        ["powershell", "-Command", "Get-Command   "],  # names nothing
        ["wherever", "x"],
        ["whichever", "x"],
        ["soffice", "--version"],
    ],
)
def test_lookalike_probes_exiting_1_are_failures(argv):
    assert _answer_exit(argv, 1) is None


@pytest.mark.skipif(sys.platform == "win32", reason="cmd.exe's type prints a file")
def test_posix_type_is_a_probe():
    assert _answer_exit(["type", "soffice"], 1).startswith("not found: soffice")


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe's type prints a file")
def test_cmd_type_of_a_missing_file_is_a_failure():
    assert _answer_exit(["type", "data.csv"], 1) is None


def test_a_probe_that_finds_nothing_is_a_success_the_model_can_read(
    fake_steps, tmp_path, approved
):
    stderr = "INFO: Could not find files for the given pattern(s).\n"
    fake_steps["where soffice"] = (1, "", stderr)

    result = approved("where soffice", tmp_path)

    assert result["status"] == "success", result
    assert result["return_code"] == 1
    assert result["expected_exit"].startswith("not found: soffice")
    assert result["stderr"] == stderr
    assert not Agent._is_error_result(result)


def test_a_probe_that_gates_work_with_and_still_fails_when_the_name_is_missing(
    fake_steps, tmp_path, approved
):
    fake_steps["where soffice"] = (1, "", "INFO: Could not find files\n")

    result = approved(
        "where soffice && soffice --headless --convert-to pdf a.docx", tmp_path
    )

    assert result["status"] == "error", result
    assert result["error"].startswith("'where soffice' exited with code 1: not found")
    assert "did not run" in result["error"]
    assert [s["command"] for s in result["steps"]] == ["where soffice"]
    assert Agent._is_error_result(result)


def test_a_probe_whose_miss_the_line_handles_with_or_is_a_success(
    fake_steps, tmp_path, approved
):
    fake_steps["where soffice"] = (1, "", "")
    fake_steps["echo missing"] = (0, "missing\n", "")

    result = approved("where soffice && soffice --version || echo missing", tmp_path)

    assert result["status"] == "success", result


def test_a_probe_that_found_the_program_runs_what_it_gates(
    fake_steps, tmp_path, approved
):
    fake_steps["where soffice"] = (0, "C:\\LO\\soffice.exe\n", "")
    fake_steps["soffice --version"] = (0, "LibreOffice 25\n", "")

    result = approved("where soffice && soffice --version", tmp_path)

    assert result["status"] == "success", result
    assert result["return_code"] == 0


def test_grep_gating_work_with_and_stays_a_conditional(fake_steps, tmp_path):
    fake_steps["grep -q TODO notes.txt"] = (1, "", "")

    result = _run("grep -q TODO notes.txt && echo has-todos", tmp_path)

    assert result["status"] == "success", result
    assert result["expected_exit"] == "grep exits 1 when nothing matches"


@pytest.mark.skipif(
    shutil.which("test") is None, reason="needs a real `test` program on PATH"
)
def test_a_real_file_probe_answers_both_ways(tmp_path, approved):
    (tmp_path / "out.xlsx").write_bytes(b"x")

    there = approved("test -f out.xlsx", tmp_path)
    missing = approved("test -f gone.xlsx", tmp_path)

    assert there["status"] == "success", there
    assert there["return_code"] == 0
    assert "expected_exit" not in there
    assert missing["status"] == "success", missing
    assert missing["return_code"] == 1
    assert (
        missing["expected_exit"] == "test -f gone.xlsx is false (exit 1 is its answer)"
    )
