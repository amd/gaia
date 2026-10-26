# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Every command the shell runs in a pipeline is a segment the allowlist sees.

A ``|`` ends a command whether or not it has spaces around it, so ``a|b`` and
``a | b`` must validate identically. A quoted ``|`` — and, under cmd.exe, a
``^|`` — is data, not a boundary. Validation only: nothing here executes.
"""

from pathlib import Path

import pytest

from gaia.agents.tools import shell_tools
from gaia.agents.tools.shell_tools import ShellToolsMixin, _parse_line

DIALECTS = ("cmd", "posix")


@pytest.fixture(params=DIALECTS)
def dialect(request, monkeypatch):
    """Read pipes the way cmd.exe or sh does, whatever OS runs the test."""
    monkeypatch.setattr(shell_tools, "_pipe_dialect", lambda: request.param)
    return request.param


def _use(monkeypatch, name):
    monkeypatch.setattr(shell_tools, "_pipe_dialect", lambda: name)


class _Console:
    def __init__(self, full_access: bool):
        self.full_access = full_access


class _Shell(ShellToolsMixin):
    def __init__(self, full_access: bool = False):
        self.console = _Console(full_access)


class _Grants:
    def __init__(self, *binaries):
        self._binaries = frozenset(binaries)

    def binaries(self) -> frozenset:
        return self._binaries


class _PathValidator:
    """Allows *root* and everything under it."""

    def __init__(self, root: Path):
        self._root = root.resolve()

    def is_path_allowed(self, path: str, prompt_user: bool = True) -> bool:
        return Path(path).resolve().is_relative_to(self._root)


def _validate(command: str, full_access: bool = False):
    error, steps = _Shell(full_access)._validate_shell_command(command)
    return error, [segment for step in steps for segment in step.segments]


def _error_text(error):
    return None if error is None else (error.get("tier"), error["error"])


@pytest.mark.parametrize("full_access", [False, True])
@pytest.mark.parametrize(
    "unspaced, spaced",
    [
        ("ls|grep x", "ls | grep x"),
        ("git status|head -5|sort", "git status | head -5 | sort"),
        ("ls .|rm x", "ls . | rm x"),
        ("ls .|shutdown /s", "ls . | shutdown /s"),
    ],
)
def test_spacing_around_a_pipe_does_not_change_the_verdict(
    dialect, full_access, unspaced, spaced
):
    tight_error, tight_segments = _validate(unspaced, full_access)
    loose_error, loose_segments = _validate(spaced, full_access)

    assert tight_segments == loose_segments
    assert _error_text(tight_error) == _error_text(loose_error)


@pytest.mark.parametrize("full_access", [False, True])
@pytest.mark.parametrize("command", ["ls .|rm x", "ls . | rm x"])
def test_a_pipe_into_a_disallowed_command_is_refused(dialect, full_access, command):
    error, segments = _validate(command, full_access)

    assert segments == [["ls", "."], ["rm", "x"]]
    assert error is not None
    assert "'rm'" in error["error"]


def test_the_unspaced_command_is_its_own_segment(dialect):
    steps, error = _parse_line("ls .|rm x")
    assert error is None
    assert steps[0].segments == [["ls", "."], ["rm", "x"]]


@pytest.mark.parametrize("full_access", [False, True])
def test_a_double_quoted_pipe_is_an_argument_not_a_boundary(dialect, full_access):
    error, segments = _validate('findstr "a|b" notes.txt', full_access)

    assert error is None
    assert segments == [["findstr", "a|b", "notes.txt"]]


def test_a_lone_quoted_pipe_stays_with_its_command(dialect):
    error, segments = _validate('findstr "|" notes.txt')

    assert error is None
    assert segments == [["findstr", "|", "notes.txt"]]


def test_a_caret_escaped_pipe_is_not_a_boundary_in_cmd(monkeypatch):
    _use(monkeypatch, "cmd")

    error, segments = _validate("echo a^|rm x")

    assert error is None
    assert segments == [["echo", "a^|rm", "x"]]


def test_single_quotes_protect_a_pipe_in_sh(monkeypatch):
    _use(monkeypatch, "posix")

    error, segments = _validate("grep 'a|b' notes.txt")

    assert error is None
    assert segments == [["grep", "a|b", "notes.txt"]]


@pytest.mark.parametrize("full_access", [False, True])
@pytest.mark.parametrize(
    "command",
    [
        # cmd.exe does not honour single quotes, so it cuts inside 'a|b'.
        "grep 'a|b' notes.txt",
        # cmd.exe reads \" as a quote opening, shlex as an escaped character.
        'echo "\\"|rm x"',
        # Nor does a backslash protect a pipe from cmd.exe.
        "echo a\\|rm x",
    ],
)
def test_a_cut_the_quoting_disagrees_with_is_refused_in_cmd(
    monkeypatch, full_access, command
):
    _use(monkeypatch, "cmd")

    error, segments = _validate(command, full_access)

    assert segments == []
    assert error is not None
    assert error["tier"] == shell_tools.TIER_REFUSE


def test_the_ambiguity_refusal_names_the_cause(monkeypatch):
    _use(monkeypatch, "cmd")

    error, _ = _validate("grep 'a|b' notes.txt")

    # The fix leads, so a truncated error line still shows it (#5700).
    assert error["error"].startswith(
        "Not run: wrap any argument containing '|' in double quotes."
    )
    assert "inside an argument" in error["error"]
    assert "double quotes" in error["hint"]


@pytest.mark.parametrize("command", ["ls |", "| ls", "ls || grep x |"])
def test_a_pipe_with_nothing_on_one_side_is_refused(dialect, command):
    error, segments = _validate(command)

    assert segments == []
    assert error is not None
    assert "Empty command" in error["error"]


def test_a_stderr_redirection_lands_on_the_unspaced_segment(dialect):
    steps, error = _parse_line("ls|grep x 2>&1")

    assert error is None
    assert steps[0].segments == [["ls"], ["grep", "x"]]
    assert steps[0].stderr_modes == ("", "2>&1")


def test_a_pipe_out_of_a_granted_cli_still_gets_its_path_checked(dialect, tmp_path):
    """A granted CLI's own operands skip the path scan; what it pipes into does not."""
    work = tmp_path / "work"
    work.mkdir()
    host = _Shell()
    host._granted_binaries = _Grants("gh")
    host.path_validator = _PathValidator(work)

    error, steps = host._validate_shell_command(
        "gh issue list --search x|cat ../secret.txt"
    )
    assert error is None, error
    assert steps[0].segments[1] == ["cat", "../secret.txt"]

    refusal = host._path_traversal_refusal(steps[0], str(work), frozenset({"gh"}))
    assert refusal is not None
    assert "secret.txt" in refusal["error"]


@pytest.mark.parametrize(
    "command",
    ["gh issue list --search x|echo pwned", "gh issue list --search x | echo pwned"],
)
def test_a_pipe_out_of_a_granted_cli_is_not_pre_authorized(dialect, command):
    host = _Shell()
    host._granted_binaries = _Grants("gh")

    assert not host.skill_grant_covers_call("run_shell_command", {"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "gh issue list --repo amd/gaia --state open",
        "gh pr view 1 -R amd/gaia",
        "gh --repo=amd/gaia issue list",
        "gh issue list -Ramd/gaia",
        'gh api repos/amd/gaia --jq ".open_issues_count"',
        "gh api -X GET repos/amd/gaia/issues",
    ],
)
def test_gh_repo_names_and_api_routes_are_not_paths(command, tmp_path):
    """#5737: 'amd/gaia' joined onto the cwd read as a folder outside the project."""
    work = tmp_path / "work"
    work.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    host = _Shell()
    host.path_validator = _PathValidator(work)
    _error, steps = host._validate_shell_command(command)
    outside: list = []

    refusal = host._path_traversal_refusal(
        steps[0], str(elsewhere), frozenset(), outside
    )

    assert refusal is None, refusal
    assert outside == []


@pytest.mark.parametrize(
    "command, path",
    [
        ("gh issue create --title x --body-file ../secret.txt", "secret.txt"),
        ("gh api --input ../body.json repos/amd/gaia", "body.json"),
    ],
)
def test_gh_flags_that_read_local_files_are_still_checked(command, path, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    host = _Shell()
    host.path_validator = _PathValidator(work)
    _error, steps = host._validate_shell_command(command)
    outside: list = []

    refusal = host._path_traversal_refusal(steps[0], str(work), frozenset(), outside)

    assert refusal is None
    assert [Path(p).name for p in outside] == [path]


def test_a_gh_api_endpoint_after_other_flags_is_still_checked(tmp_path):
    """Only `gh api <endpoint>` skips the check; `gh -R x api ../foo` does not."""
    work = tmp_path / "work"
    work.mkdir()
    host = _Shell()
    host.path_validator = _PathValidator(work)
    _error, steps = host._validate_shell_command("gh -R x/y api ../foo")
    outside: list = []

    host._path_traversal_refusal(steps[0], str(work), frozenset(), outside)

    assert [Path(p).name for p in outside] == ["foo"]
