# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Credential files never take part in a recursive search.

A search that printed nothing from ``.env`` could still name it (``-l``),
count its matches (``-c``) or answer through its exit code (``-q``), which
says whether the file contains a guess. So grep, rg and git grep run with
every credential file excluded, and a search that cannot be given those
exclusions (findstr /s, Select-String, one inside a script, one after ``--``)
is refused when the tree it searches holds a credential file.

These are classification tests: the process layer is stubbed throughout.
"""

import fnmatch
import os
import shlex
import subprocess

import pytest

from gaia import security
from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools import shell_tools
from gaia.agents.tools.shell_tools import (
    SKIPPED_CREDENTIALS_NOTE,
    ShellToolsMixin,
    _credential_exclusions,
    _excluded_search_step,
    _parse_line,
)

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="Windows command")

#: Every credential file name, in the case it is listed and in two others.
CREDENTIAL_NAMES = sorted(
    {
        variant
        for name in security.SENSITIVE_FILE_NAMES
        for variant in (name, name.upper(), name.title())
    }
    | {
        f"{stem}{ext}"
        for ext in security.SENSITIVE_EXTENSIONS
        for stem in ("server", "Deploy")
    }
    | {f"server{ext.upper()}" for ext in security.SENSITIVE_EXTENSIONS}
)

#: Names next to credential files that a search must still read.
ORDINARY_NAMES = [
    "notes.txt",
    "env.txt",
    ".env.example",
    ".envrc.md",
    "secrets.md",
    "secrets.json.md",
    "credentials.py",
    "key.txt",
    "pem.py",
    "README.md",
    "main.py",
]


class _Validator:
    def __init__(self, root):
        self.root = os.path.realpath(root)

    def is_path_allowed(self, path, prompt_user=True):
        return os.path.realpath(path).startswith(self.root)


class _Console:
    def __init__(self, full_access):
        self.full_access = full_access
        self.auto_approve_gated_tools = full_access


class _Host(ShellToolsMixin):
    def __init__(self, root, full_access=False):
        self.console = _Console(full_access)
        self.debug = False
        self.path_validator = _Validator(root)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temp HOME whose ``proj/secrets/`` holds a credential file."""
    home = tmp_path / "home"
    secrets = home / "proj" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / ".env").write_text("TOKEN=guess-me\n")
    (secrets / "notes.txt").write_text("guess what\n")
    clean = home / "proj" / "src"
    clean.mkdir()
    (clean / "a.py").write_text("# TODO guess\n")
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    monkeypatch.delenv("POSIXLY_CORRECT", raising=False)
    return home


def _refusal(command, home, cwd=None):
    host = _Host(home)
    steps, error = _parse_line(command, bypass_gates=True)
    assert error is None, error
    for step in steps:
        refusal = host._path_traversal_refusal(
            step, str(cwd or home / "proj"), frozenset()
        )
        if refusal:
            return refusal
    return None


def _only_step(command, bypass=False):
    steps, error = _parse_line(command, bypass_gates=bypass)
    assert error is None, error
    assert len(steps) == 1
    return steps[0]


def _excluded_by(flavor, name):
    """Whether *flavor*'s exclusion arguments keep a file called *name* out."""
    for word in _credential_exclusions(flavor):
        if flavor == "grep":
            glob = word.removeprefix("--exclude=")
            if fnmatch.fnmatchcase(name, glob):
                return True
        elif flavor == "rg":
            glob = word.removeprefix("--iglob=!")
            if fnmatch.fnmatchcase(name.lower(), glob.lower()):
                return True
        else:
            magic, _, glob = word.partition(")")
            assert magic == ":(top,exclude,icase,glob" and glob.startswith("**/")
            if fnmatch.fnmatchcase(name.lower(), glob[3:].lower()):
                return True
    return False


# --------------------------------------- the exclusions cover every credential file


@pytest.mark.parametrize("flavor", ["grep", "rg", "git"])
@pytest.mark.parametrize("name", CREDENTIAL_NAMES)
def test_every_credential_file_name_is_excluded(flavor, name):
    assert security.is_credential_file_name(name)
    assert _excluded_by(flavor, name), (flavor, name)


@pytest.mark.parametrize("flavor", ["grep", "rg", "git"])
@pytest.mark.parametrize("name", ORDINARY_NAMES)
def test_an_ordinary_file_is_still_searched(flavor, name):
    assert not security.is_credential_file_name(name)
    assert not _excluded_by(flavor, name), (flavor, name)


# ------------------------- probes of a credential file's contents skip the file


@pytest.mark.parametrize(
    "command, flavor",
    [
        ("grep -rl guess secrets/", "grep"),
        ("grep -rL guess secrets/", "grep"),
        ("grep -rc guess secrets/", "grep"),
        ("grep -rq guess secrets/", "grep"),
        ("grep -r --files-with-matches guess secrets", "grep"),
        ("grep -r --count guess secrets", "grep"),
        ("grep -rqs TOKEN=g secrets", "grep"),
        ("grep -rl --include=.env guess .", "grep"),
        ("grep -rl --include .env guess .", "grep"),
        ("egrep -rl 'TOKEN=g.*' .", "grep"),
        ("grep -Rl guess secrets", "grep"),
        ("rg -l guess secrets", "rg"),
        ("rg -c guess secrets", "rg"),
        ("rg -q guess secrets", "rg"),
        ("rg --files-with-matches --hidden -uu guess", "rg"),
        ("rg -l -g .env guess", "rg"),
        ("rg -l --iglob .ENV guess secrets", "rg"),
        ("git grep -l guess", "git"),
        ("git grep -c guess", "git"),
        ("git grep -q guess", "git"),
        ("git grep --untracked -l guess -- secrets", "git"),
        ("git grep --no-index -l guess secrets", "git"),
        ("git -C secrets grep -l guess", "git"),
    ],
)
def test_a_name_count_or_exit_code_probe_runs_without_the_credential_file(
    home, command, flavor
):
    step = _only_step(command)
    run, rewritten = _excluded_search_step(step, bypass=False)

    assert rewritten == {0}
    words = _credential_exclusions(flavor)
    # The user's words stay as they were, and the exclusions come last, so
    # grep's and rg's "last matching include/exclude wins" is ours.
    assert run.segments[0] == [*step.segments[0], *words]
    assert _refusal(command, home) is None, command


@pytest.mark.parametrize(
    "command, flavor",
    [
        ("grep -rl guess secrets/ && echo found", "grep"),
        ("grep -rq guess secrets/ || echo absent", "grep"),
        ("rg -q guess secrets && echo found", "rg"),
        ("git grep -q guess; echo $?", "git"),
    ],
)
def test_an_exit_code_probe_on_a_chained_line_skips_the_file(home, command, flavor):
    steps, error = _parse_line(command)
    assert error is None, error

    run, rewritten = _excluded_search_step(steps[0], bypass=False)

    assert rewritten == {0}
    assert run.segments[0][-len(_credential_exclusions(flavor)) :] == (
        _credential_exclusions(flavor)
    )
    assert _refusal(command, home) is None


@pytest.mark.parametrize(
    "command",
    [
        "grep -rl guess -- secrets/",
        "grep -r -e guess -- secrets",
        "rg -l guess -- secrets",
        "grep -rl guess secrets -e",
        "rg -l guess secrets -g",
        "grep -rl --frobnicate guess secrets",
        "rg -l --frobnicate guess secrets",
        "git grep --frobnicate -l guess",
        "POSIXLY_CORRECT=1 grep -rl guess secrets",
        "env POSIXLY_CORRECT=1 grep -rl guess secrets",
        'bash -c "grep -rl guess secrets"',
        'sh -c "rg -l guess secrets"',
        'bash -c "git grep -c guess"',
        pytest.param("findstr /s /m guess secrets\\*", marks=WINDOWS_ONLY),
        pytest.param("findstr /s /c:guess secrets\\*", marks=WINDOWS_ONLY),
        pytest.param("findstr /s /m guess *", marks=WINDOWS_ONLY),
        pytest.param("findstr /S /M /I GUESS *.*", marks=WINDOWS_ONLY),
        pytest.param("findstr /s /m guess *.env", marks=WINDOWS_ONLY),
        'powershell -Command "Get-ChildItem secrets -Recurse | Select-String guess -List"',
        'powershell -Command "gci -r | sls guess -Quiet"',
        'powershell -Command "Select-String -Path * -Pattern guess -Recurse"',
    ],
)
def test_a_search_that_cannot_be_given_exclusions_is_refused(home, command):
    refusal = _refusal(command, home)

    assert refusal is not None, command
    assert refusal["executed"] is False
    assert "credentials" in refusal["error"]
    assert "tier" not in refusal  # no approval lifts it


@pytest.mark.parametrize(
    "command",
    [
        "grep -rl guess -- src",
        'bash -c "grep -rn TODO src"',
        pytest.param("findstr /s /m guess src\\*", marks=WINDOWS_ONLY),
        pytest.param("findstr /s /m guess *.py", marks=WINDOWS_ONLY),
        'powershell -Command "gci src -r | sls TODO"',
        'powershell -Command "gci -r -Filter *.py | sls TODO"',
    ],
)
def test_the_same_search_of_a_clean_tree_runs(home, command):
    assert _refusal(command, home) is None, command


def test_a_credential_file_deep_in_the_tree_is_found(home):
    deep = home / "proj" / "src" / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "Server.PEM").write_text("-----BEGIN-----\n")

    refusal = _refusal('bash -c "grep -rl BEGIN src"', home)

    assert refusal is not None and "Server.PEM" in refusal["error"]
    assert _refusal("grep -rl BEGIN src", home) is None


def test_a_case_sensitive_exclude_is_not_read_as_skipping_another_case(home):
    (home / "proj" / "secrets" / ".env").rename(home / "proj" / "secrets" / ".ENV")

    refusal = _refusal('bash -c "grep -rl --exclude=.env guess secrets"', home)

    assert refusal is not None and ".ENV" in refusal["error"]


def test_an_include_glob_with_a_folder_does_not_hide_the_file(home):
    """rg's ``-g 'secrets/**'`` is a path glob; it must not read as excluding .env."""
    refusal = _refusal("rg -l -g 'secrets/**' guess --", home)

    assert refusal is not None


def test_a_tree_too_large_to_check_is_refused(home, monkeypatch):
    monkeypatch.setattr(shell_tools, "_TREE_CHECK_LIMIT", 2)
    (home / "proj" / "secrets" / ".env").unlink()

    refusal = _refusal('bash -c "grep -rl guess ."', home)

    assert refusal is not None and refusal["executed"] is False
    assert "too many files" in refusal["error"]


def test_a_disguised_link_deep_in_a_followed_tree_is_refused(home):
    deep = home / "proj" / "src" / "nested"
    deep.mkdir()
    try:
        os.symlink(home / "proj" / "secrets" / ".env", deep / "notes.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")

    refusal = _refusal("grep -Rl guess src", home)

    assert refusal is not None and "notes.txt" in refusal["error"]
    # grep -r does not follow a link it meets inside a folder.
    assert _refusal("grep -rl guess src", home) is None


def test_a_rg_wildcard_operand_naming_a_credential_file_is_refused(home):
    """rg reads a file named on its command line whatever its globs say."""
    (home / "proj" / "secrets" / "secrets.json").write_text("{}\n")

    refusal = _refusal("rg -l guess secrets/s*", home)

    assert refusal is not None and "secrets.json" in refusal["error"]


# ------------------------------------------------ a search behind a launcher


@pytest.mark.parametrize(
    "command, flavor, index",
    [
        ("env grep -rl guess secrets", "grep", 0),
        ("env LC_ALL=C grep -rc guess secrets", "grep", 0),
        ("timeout 5 grep -rq guess secrets", "grep", 0),
        ("nice -n 5 rg -l guess secrets", "rg", 0),
        ("xargs grep -l guess", "grep", 0),
        ("find secrets -type f | xargs grep -c guess", "grep", 1),
        ("ls secrets | xargs -I{} grep -l guess secrets/{}", "grep", 1),
        ("git ls-files | xargs git grep -l guess", "git", 1),
    ],
)
def test_a_search_behind_a_launcher_gets_the_exclusions(home, command, flavor, index):
    step = _only_step(command)

    run, rewritten = _excluded_search_step(step, bypass=False)

    assert rewritten == {index}
    words = _credential_exclusions(flavor)
    assert run.segments[index] == [*step.segments[index], *words]
    assert _refusal(command, home) is None, command


@pytest.mark.parametrize(
    "command",
    [
        "xargs rg -l guess",
        "find . -type f | xargs rg -c guess",
        "env POSIXLY_CORRECT=1 grep -rl guess secrets",
        "timeout 5 grep -rl guess -- secrets",
    ],
)
def test_a_search_behind_a_launcher_that_cannot_skip_the_file_is_refused(home, command):
    refusal = _refusal(command, home)

    assert refusal is not None, command
    assert refusal["executed"] is False


# ----------------------------------------------- the text a shell runs matches


@pytest.mark.parametrize("dialect", ["posix", "cmd"])
@pytest.mark.parametrize(
    "command, index",
    [
        ("grep -rl guess secrets", 0),
        ("grep -rn TODO . | head -5", 0),
        ("ls | grep -rl guess secrets", 1),
        ("grep -rn TODO . 2>&1 | sort", 0),
        ("rg -l 'a b' secrets | wc -l", 0),
        ("git grep -l guess -- secrets | sort", 0),
    ],
)
def test_the_shell_text_runs_the_same_words_as_the_argv(
    monkeypatch, dialect, command, index
):
    monkeypatch.setattr(shell_tools, "_pipe_dialect", lambda: dialect)
    step = _only_step(command)

    run, rewritten = _excluded_search_step(step, bypass=True)

    assert rewritten == {index}
    for text in (run.text, run.shell_text):
        pieces = shell_tools._split_shell_pipes(text)
        assert len(pieces) == len(run.segments)
        for piece, segment in zip(pieces, run.segments):
            words = shell_tools._tokenize(
                shell_tools._take_stderr_redirections(piece)[0]
            )
            assert words == segment, (dialect, text)


def test_a_cmd_line_ending_in_a_caret_is_not_rewritten(monkeypatch):
    monkeypatch.setattr(shell_tools, "_pipe_dialect", lambda: "cmd")
    step = _only_step("grep -rl guess secrets^")

    run, rewritten = _excluded_search_step(step, bypass=True)

    assert rewritten == frozenset() and run is step


def test_a_step_with_an_assignment_runs_as_argv_and_is_rewritten(home):
    step = _only_step("LC_ALL=C grep -rl guess secrets")

    run, rewritten = _excluded_search_step(step, bypass=True)

    assert rewritten == {0}
    assert run.segments[0][-1] == _credential_exclusions("grep")[-1]
    assert run.text == step.text


def test_posix_argument_order_in_the_environment_is_not_rewritten(home, monkeypatch):
    monkeypatch.setenv("POSIXLY_CORRECT", "1")

    run, rewritten = _excluded_search_step(_only_step("grep -rl guess ."), False)

    assert rewritten == frozenset()
    assert _refusal("grep -rl guess secrets", home) is not None


@pytest.mark.parametrize(
    "command",
    ["grep -l guess secrets/notes.txt", "rg --version", "git log -1", "ls -R"],
)
def test_a_command_that_is_not_a_recursive_search_is_left_alone(command):
    step = _only_step(command)

    run, rewritten = _excluded_search_step(step, bypass=True)

    assert rewritten == frozenset() and run is step


# ---------------------------------------------------- the run gets the exclusions


@pytest.fixture
def captured_runs(monkeypatch):
    runs = []

    def fake_run_step(step, cwd, timeout, granted, waiter=None, bypass=False):
        runs.append(step)
        return subprocess.CompletedProcess(step.text, 1, "", "")

    monkeypatch.setattr(shell_tools, "_run_step", fake_run_step)
    return runs


@pytest.mark.parametrize("full_access", [False, True])
def test_the_executed_search_carries_the_exclusions(home, captured_runs, full_access):
    host = _Host(home, full_access=full_access)
    host.register_shell_tools()
    run = get_tool_metadata("run_shell_command")["function"]

    result = run(command="grep -rl guess secrets", working_directory=str(home / "proj"))

    assert len(captured_runs) == 1, result
    segment = captured_runs[0].segments[0]
    assert segment[:4] == ["grep", "-rl", "guess", "secrets"]
    assert segment[4:] == _credential_exclusions("grep")
    assert SKIPPED_CREDENTIALS_NOTE in result.get("note", "")
    assert result["command"] == "grep -rl guess secrets"


def test_a_refused_search_never_reaches_the_process_layer(home, captured_runs):
    host = _Host(home, full_access=True)
    host.register_shell_tools()
    run = get_tool_metadata("run_shell_command")["function"]

    result = run(
        command="grep -rl guess -- secrets", working_directory=str(home / "proj")
    )

    assert captured_runs == []
    assert result["executed"] is False
    assert "guess-me" not in str(result)


def test_an_ordinary_command_gets_no_note(home, captured_runs):
    host = _Host(home, full_access=True)
    host.register_shell_tools()
    run = get_tool_metadata("run_shell_command")["function"]

    result = run(command="ls", working_directory=str(home / "proj"))

    assert SKIPPED_CREDENTIALS_NOTE not in result.get("note", "")


def test_exclusion_words_survive_posix_quoting():
    for flavor in ("grep", "rg", "git"):
        words = _credential_exclusions(flavor)
        assert shlex.split(" ".join(shlex.quote(w) for w in words)) == words
