# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``a && b``, ``a; b`` and ``a || b`` run under the read-only allowlist.

Refusing the connectors outright cost a round trip per step: explore, then
verify, then look at the failure, each one a fresh ~12K-token prompt. The
security model is unchanged — every segment of every pipeline goes through the
same tiered validation, one refusal anywhere refuses the whole line before
anything runs, and no line ever reaches a shell on POSIX.

Real host, real subprocesses, ``tmp_path`` — a mock here would prove the code
was called, not that the command it built runs.
"""

import os
import sys

import pytest

from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools import shell_tools
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.skills.binaries import BinaryGrants

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Windows runs each pipeline through cmd.exe"
)


class _Host(ShellToolsMixin):
    """Minimal host: the mixin only needs its own __init__ for rate limiting."""


class _Confined(ShellToolsMixin):
    """A host whose path validator allows *root* and nothing above it."""

    def __init__(self, root):
        super().__init__()
        self.path_validator = _RootValidator(root)


class _RootValidator:
    def __init__(self, root):
        self.root = str(root)

    def is_path_allowed(self, path: str) -> bool:
        root = os.path.realpath(self.root)
        real = os.path.realpath(path)
        return real == root or real.startswith(root + os.sep)


def _shell(host):
    host.register_shell_tools()
    return get_tool_metadata("run_shell_command")["function"]


def _run(command, cwd, host=None, **kwargs):
    """Run *command* through a fresh host, so the rate limiter never trips."""
    return _shell(host or _Host())(
        command=command, working_directory=str(cwd), **kwargs
    )


@pytest.fixture
def notes(tmp_path):
    (tmp_path / "notes.txt").write_text("pear\napple\nfig\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Connector semantics
# ---------------------------------------------------------------------------


def test_and_runs_both_and_concatenates_their_output(notes):
    result = _run("ls && cat notes.txt", notes)

    assert result["status"] == "success", result
    assert "notes.txt" in result["stdout"]
    assert "pear" in result["stdout"]
    assert result["stdout"].index("notes.txt") < result["stdout"].index("pear")
    assert result["return_code"] == 0


def test_and_stops_at_the_first_failure_and_reports_its_code(notes):
    result = _run("cat missing.txt && echo never", notes)

    assert result["return_code"] != 0, result
    assert result["has_errors"] is True
    assert "never" not in result["stdout"]
    assert "missing.txt" in result["stderr"]
    assert [step["command"] for step in result["steps"]] == ["cat missing.txt"]


def test_or_runs_the_fallback_only_when_the_first_fails(notes):
    result = _run("cat missing.txt || echo fallback", notes)

    assert result["stdout"].strip() == "fallback", result
    assert result["return_code"] == 0
    assert result["has_errors"] is False

    skipped = _run("cat notes.txt || echo fallback", notes)
    assert "fallback" not in skipped["stdout"], skipped


def test_semicolon_runs_the_next_command_regardless(notes):
    result = _run("cat missing.txt; echo after", notes)

    assert result["stdout"].strip() == "after", result
    assert result["return_code"] == 0, "the last-run command's code is the line's"
    assert "missing.txt" in result["stderr"]


def test_a_failure_a_semicolon_walked_past_is_still_a_failure(notes):
    """`pytest -q; ls` must not report a failing suite as a passing check.

    The verification footer reads the result's error flag, and the last-run
    command's exit code alone would say the line was fine.
    """
    result = _run("cat missing.txt; echo after", notes)

    assert result["has_errors"] is True, result
    assert result["steps"][0]["return_code"] != 0


def test_a_skipped_step_leaves_the_status_for_the_next_connector(notes):
    """``false && a || b`` runs b — skipping a does not reset the exit code."""
    result = _run("cat missing.txt && echo a || echo b", notes)

    assert result["stdout"].strip() == "b", result


def test_a_pipeline_inside_a_compound_line_still_pipes(notes):
    result = _run("cat notes.txt | sort && wc -l notes.txt", notes)

    assert result["status"] == "success", result
    assert result["stdout"].startswith("apple\nfig\npear\n")
    assert result["stdout"].strip().endswith("notes.txt")
    assert result["return_code"] == 0


def test_every_step_reports_its_own_exit_code(notes):
    result = _run("cat missing.txt; echo after; cat notes.txt", notes)

    assert [step["command"] for step in result["steps"]] == [
        "cat missing.txt",
        "echo after",
        "cat notes.txt",
    ]
    codes = [step["return_code"] for step in result["steps"]]
    assert codes[0] != 0 and codes[1] == 0 and codes[2] == 0, result


def test_one_line_costs_the_rate_limiter_one_command(notes):
    """The point of the change is fewer round trips, so a line is one step."""
    host = _Host()
    result = _run("pwd && pwd && pwd && pwd", notes, host=host)

    assert result["status"] == "success", result
    assert len(host.shell_command_times) == 1


# ---------------------------------------------------------------------------
# Validation composes across segments
# ---------------------------------------------------------------------------


def test_a_refused_segment_refuses_the_line_before_anything_runs(notes, monkeypatch):
    spawned = []
    monkeypatch.setattr(
        shell_tools.subprocess,
        "run",
        lambda *a, **kw: spawned.append(a) or pytest.fail("a refused line ran"),
    )
    monkeypatch.setattr(
        shell_tools.subprocess,
        "Popen",
        lambda *a, **kw: spawned.append(a) or pytest.fail("a refused line ran"),
    )

    result = _run("ls && rm -rf /", notes)

    assert result["status"] == "error"
    assert "not in the allowed list" in result["error"]
    assert result["executed"] is False
    assert spawned == []


def test_a_refused_segment_in_the_middle_refuses_the_line(notes):
    result = _run("ls; git push; ls", notes)

    assert result["status"] == "error", result
    assert result["executed"] is False


@pytest.mark.parametrize(
    "command",
    [
        "ls && echo hi > marker.txt",
        "echo $(whoami) && ls",
        "ls && echo `whoami`",
        "ls && cat < notes.txt",
        "ls && sleep 10 &",
        "ls &\nrm -rf /",
    ],
)
def test_redirection_substitution_and_backgrounding_stay_refused(command, notes):
    result = _run(command, notes)

    assert result["status"] == "error", result
    assert result["executed"] is False
    assert not (notes / "marker.txt").exists()


def test_an_inline_environment_assignment_is_scoped_to_its_own_command(notes):
    """See tests/unit/test_shell_env_assignment.py for the rule in full."""
    result = _run("ls && FOO=bar ls", notes)

    assert result["status"] == "success", result


def test_a_loader_variable_stays_refused(notes):
    result = _run("ls && PATH=/tmp ls", notes)

    assert result["status"] == "error", result
    assert result["executed"] is False


def test_a_newline_is_not_a_connector(notes):
    result = _run("ls\nrm -rf /", notes)

    assert result["status"] == "error", result
    assert result["executed"] is False


def test_an_operator_inside_double_quotes_is_data_not_a_connector(notes):
    result = _run('echo "a && b; c" && echo done', notes)

    assert result["status"] == "success", result
    assert result["stdout"] == "a && b; c\ndone\n"


@pytest.mark.parametrize(
    "command",
    ["grep 'a||b' notes.txt", "grep 'a&&b' notes.txt", "ls ';rm -rf x'"],
)
def test_single_quotes_do_not_smuggle_an_operator_past_the_split(command, notes):
    """cmd.exe does not honour single quotes, so neither does the splitter.

    Reading them as quotes here would send the operator on to cmd.exe as a
    real separator, splitting the line into commands nothing validated.
    """
    result = _run(command, notes)

    assert result["status"] == "error", result
    assert result["executed"] is False


@pytest.mark.parametrize("command", ["ls &&", "&& ls", "ls ; ; ls"])
def test_a_missing_operand_is_refused_not_guessed(command, notes):
    result = _run(command, notes)

    assert result["status"] == "error", result
    assert result["executed"] is False


# ---------------------------------------------------------------------------
# cd sets the directory for the rest of its own line
# ---------------------------------------------------------------------------


def test_cd_moves_the_commands_after_it(notes):
    (notes / "sub").mkdir()
    (notes / "sub" / "only-here.txt").write_text("x\n")

    result = _run("cd sub && ls", notes, host=_Confined(notes))

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "only-here.txt"


def test_cd_outside_the_allowed_paths_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside").mkdir()

    result = _run("cd ../outside && ls", root, host=_Confined(root))

    assert result["status"] == "error", result
    assert "denied" in result["error"].lower()
    assert result["executed"] is False


def test_cd_to_a_missing_directory_says_so_instead_of_running_the_line(notes):
    result = _run("cd nope && ls", notes)

    assert result["status"] == "error", result
    assert "nope" in result["error"]
    assert result["executed"] is False


@pytest.mark.parametrize(
    "command",
    ["cd && ls", "cd a b && ls", "cd - && ls", "ls | cd sub", "cd sub | ls"],
)
def test_cd_is_only_allowed_as_a_bare_one_argument_command(command, notes):
    (notes / "sub").mkdir()

    result = _run(command, notes)

    assert result["status"] == "error", result
    assert result["executed"] is False


def test_cd_does_not_leak_into_the_next_call(notes):
    (notes / "sub").mkdir()
    host = _Confined(notes)

    _run("cd sub && pwd", notes, host=host)
    result = _run("pwd", notes, host=host)

    assert result["stdout"].strip() == str(notes.resolve())


def test_a_line_whose_only_command_is_missing_says_nothing_ran(notes):
    """The verification footer reads that flag to tell a check that never ran
    apart from one that ran and failed (#3677). `tasklist` is whitelisted and
    Windows-only, so off Windows it is a command that cannot start.
    """
    (notes / "sub").mkdir()

    result = _run("cd sub && tasklist", notes)

    assert result["status"] == "error", result
    assert result["executed"] is False


def test_a_missing_command_mid_line_does_not_disown_what_already_ran(notes):
    result = _run("ls && tasklist && ls", notes)

    assert result["has_errors"] is True, result
    assert result.get("executed", True) is True
    assert [step["return_code"] for step in result["steps"]] == [0, 127]
    assert "notes.txt" in result["stdout"]


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------


def test_a_timeout_kills_every_process_and_starts_no_later_step(notes, monkeypatch):
    started = []
    real_popen = shell_tools.subprocess.Popen

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(shell_tools.subprocess, "Popen", recording_popen)
    os.mkfifo(notes / "never-written")

    result = _run("cat never-written | sort && echo after", notes, timeout=1)

    assert result.get("timed_out") is True, result
    assert "after" not in result["stdout"]
    assert started and all(proc.poll() is not None for proc in started)


# ---------------------------------------------------------------------------
# The confirmation and skill-grant tiers, composed across segments
# ---------------------------------------------------------------------------


class _Gated(ShellToolsMixin):
    """A host wired to the real confirmation gate."""

    from gaia.agents.base.agent import Agent as _Agent

    CONFIRMATION_REQUIRED_TOOLS: tuple = ()
    _tools_registry: dict = {}
    confirmation_required_tools = _Agent.confirmation_required_tools
    _call_is_pre_authorized = _Agent._call_is_pre_authorized
    _tool_requires_confirmation = _Agent._tool_requires_confirmation

    def __init__(self, *binaries: str):
        super().__init__()
        self._granted_binaries = BinaryGrants()
        for binary in binaries:
            self._granted_binaries.grant(binary, skill_name="github-triage")


def _needs_modal(host, command: str) -> bool:
    return host._tool_requires_confirmation("run_shell_command", {"command": command})


def test_a_grant_covers_a_line_of_granted_reads():
    assert _needs_modal(_Gated("gh"), "gh issue list && gh issue view 1") is False


def test_the_grant_reads_each_segment_through_its_policy_spelling():
    """`python -m pytest` is the pytest grant on every segment of the line, not
    just the first — the mapping has to run per segment, or the second one
    reads as an ungranted `python`."""
    host = _Gated("pytest")

    assert _needs_modal(host, "python -m pytest -q tests/unit") is False
    assert (
        _needs_modal(host, "pytest -q tests/a && python -m pytest -q tests/b") is False
    )
    assert _needs_modal(host, "pytest -q tests/a && python -m pytest --pdb") is True


@pytest.mark.parametrize(
    "command",
    [
        "gh issue list && rm -rf x",
        "gh issue list && ls",
        "gh issue list; gh auth token",
        "gh issue list || gh issue comment 1 --body hi",
    ],
)
def test_a_grant_covers_a_line_only_when_it_covers_every_segment(command):
    assert _needs_modal(_Gated("gh"), command) is True


def test_a_confirm_tier_segment_makes_the_whole_line_one_prompt():
    """One line, one modal, showing the line the user is approving."""
    host = _Gated("gh")
    line = "gh issue list && gh issue comment 1 --body hi"

    assert host.policy_refusal_for_call("run_shell_command", {"command": line}) is None
    assert _needs_modal(host, line) is True


def test_a_refused_segment_is_refused_before_the_prompt():
    host = _Gated("gh")
    error = host.policy_refusal_for_call(
        "run_shell_command", {"command": "gh issue list && gh auth token"}
    )

    assert error is not None
    assert "auth token" in error["error"]


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def test_windows_never_hands_a_compound_line_to_cmd_exe_as_one_string(
    notes, monkeypatch
):
    """cmd.exe would run the whole line with none of it validated as a line.

    Each pipeline goes through the Windows path on its own, in sequence.
    """
    calls = []

    class _Exited:
        """Popen's surface for a command that has already finished."""

        returncode = 0
        pid = -1

        def __init__(self, args):
            self.args = args

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs.get("shell", False)))
        return _Exited(args)

    class _WindowsOS:
        """The real os, answering 'nt' — patching os.name itself breaks pathlib."""

        name = "nt"

        def __getattr__(self, attribute):
            return getattr(os, attribute)

    monkeypatch.setattr(shell_tools, "os", _WindowsOS())
    monkeypatch.setattr(shell_tools.subprocess, "Popen", fake_popen)

    _run("ls && cat notes.txt", notes)

    assert [shell for _, shell in calls] == [True, True]
    assert all(isinstance(args, str) for args, _ in calls)
    assert not any("&&" in args for args, _ in calls)
    assert [args for args, _ in calls] == ["ls", "cat notes.txt"]
