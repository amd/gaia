# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Credential files never take part in a recursive search.

A line that is one grep, rg or git grep runs as argv, with no shell, and
with every credential file excluded. Any other recursive search (findstr /s,
Select-String, one inside a script, piped, chained, or using any shell
feature) is refused when the tree it searches holds a credential file.

These are classification tests: the process layer is stubbed throughout.
"""

import fnmatch
import os
import shlex

import pytest

from gaia import security
from gaia.agents.base.tools import get_tool_metadata
from gaia.agents.tools import shell_tools
from gaia.agents.tools.shell_tools import (
    SKIPPED_CREDENTIALS_NOTE,
    ShellToolsMixin,
    _credential_exclusions,
    _grep_exclusion_patterns,
    _parse_line,
    _search_plan,
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


@pytest.fixture(autouse=True)
def posix_grep(monkeypatch):
    """grep that reads its arguments as given; the Windows tests switch it off."""
    monkeypatch.setattr(shell_tools, "_greps_expand_arguments", lambda: False)


def _refusal(command, home, cwd=None):
    host = _Host(home)
    steps, error = _parse_line(command, bypass_gates=True)
    assert error is None, error
    searchable = _search_plan(command, steps) is not None
    for step in steps:
        refusal = host._path_traversal_refusal(
            step, str(cwd or home / "proj"), frozenset(), searchable=searchable
        )
        if refusal:
            return refusal
    return None


def _argv(command):
    steps, error = _parse_line(command, bypass_gates=True)
    assert error is None, error
    return _search_plan(command, steps)


def _excluded_by(flavor, name):
    """Whether *flavor*'s exclusions keep a file called *name* out."""
    if flavor == "grep":
        return any(
            fnmatch.fnmatchcase(name, glob) for glob in _grep_exclusion_patterns()
        )
    for word in _credential_exclusions(flavor):
        if flavor == "rg":
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
        ('grep -rn "a b" .', "grep"),
        ("grep -Rl guess secrets", "grep"),
        ("rg -l guess secrets", "rg"),
        ("rg -c guess secrets", "rg"),
        ("rg -q guess secrets", "rg"),
        ("rg --files-with-matches --hidden -uu guess", "rg"),
        ("rg -l -g .env guess", "rg"),
        ("rg -l --iglob .ENV guess secrets", "rg"),
        ("rg -n -g '!node_modules' TODO", "rg"),
        ("git grep -l guess", "git"),
        ("git grep -c guess", "git"),
        ("git grep -q guess", "git"),
        ("git grep --untracked -l guess -- secrets", "git"),
        ("git grep --no-index -l guess secrets", "git"),
        ("git grep -l guess HEAD:secrets", "git"),
        ("git -C secrets grep -l guess", "git"),
    ],
)
def test_a_name_count_or_exit_code_probe_runs_without_the_credential_file(
    home, command, flavor
):
    steps, _ = _parse_line(command, bypass_gates=True)

    assert _argv(command) == (steps[0].segments[0], flavor)
    assert _refusal(command, home) is None, command


@pytest.mark.parametrize(
    "command",
    [
        # Shell features: the words are not plain argv, so nothing is rewritten.
        "grep -rl guess . # trailing comment",
        "grep -rl guess secrets/ && echo found",
        "grep -rq guess secrets/ || echo absent",
        "rg -q guess secrets; echo $?",
        "grep -rc guess secrets | sort",
        "grep -rl guess secrets 2>&1",
        "grep -rl guess $HOME/proj/secrets",
        'grep -rl guess "$PWD"',
        "grep -rl guess `pwd`",
        "grep -rl guess secrets/*",
        "grep -rl guess ~/proj/secrets",
        "LC_ALL=C grep -rl guess secrets",
        "cd secrets && grep -rl guess .",
        # Forms the exclusions could be misread in.
        "grep -rl guess -- secrets/",
        "grep -r -e guess -- secrets",
        "rg -l guess -- secrets",
        "grep -rl guess secrets -e",
        "rg -l guess secrets -g",
        "grep -rl --frobnicate guess secrets",
        "rg -l --frobnicate guess secrets",
        "git grep --frobnicate -l guess",
        # Launchers and scripts.
        "env grep -rl guess secrets",
        "timeout 5 grep -rq guess secrets",
        "nice -n 5 rg -l guess secrets",
        "xargs grep -l guess",
        "find secrets -type f | xargs grep -c guess",
        "xargs rg -l guess",
        "env POSIXLY_CORRECT=1 grep -rl guess secrets",
        'bash -c "grep -rl guess secrets"',
        'sh -c "rg -l guess secrets"',
        'bash -c "git grep -c guess"',
        pytest.param("findstr /s /m guess secrets\\*", marks=WINDOWS_ONLY),
        pytest.param("findstr /s /c:guess secrets\\*", marks=WINDOWS_ONLY),
        pytest.param("findstr /s /m guess *", marks=WINDOWS_ONLY),
        pytest.param("findstr /S /M /I GUESS *.*", marks=WINDOWS_ONLY),
        'powershell -Command "Get-ChildItem secrets -Recurse | Select-String guess -List"',
        'powershell -Command "gci -r | sls guess -Quiet"',
        'powershell -Command "Select-String -Path * -Pattern guess -Recurse"',
    ],
)
def test_a_search_that_cannot_run_with_exclusions_is_refused(home, command):
    assert _argv(command) is None, command

    refusal = _refusal(command, home)

    assert refusal is not None, command
    assert refusal["executed"] is False
    assert "credentials" in refusal["error"]
    assert "tier" not in refusal  # no approval lifts it


@pytest.mark.parametrize(
    "command",
    [
        "grep -rl guess -- src",
        "grep -rc TODO src | sort",
        "env grep -rn TODO src",
        'bash -c "grep -rn TODO src"',
        pytest.param("findstr /s /m guess src\\*", marks=WINDOWS_ONLY),
        pytest.param("findstr /s /m guess *.py", marks=WINDOWS_ONLY),
        'powershell -Command "gci src -r | sls TODO"',
        'powershell -Command "gci -r -Filter *.py | sls TODO"',
    ],
)
def test_the_same_search_of_a_clean_tree_runs(home, command):
    assert _refusal(command, home) is None, command


@pytest.mark.parametrize(
    "command", ["grep -rn TODO .", "rg -n TODO", "git grep -n TODO"]
)
def test_an_ordinary_search_of_a_folder_with_credentials_still_runs(home, command):
    assert _argv(command) is not None
    assert _refusal(command, home) is None


# --------------------------------------------- git grep of an object or a blob


@pytest.mark.parametrize(
    "command",
    [
        "git grep -l guess HEAD:.env",
        "git grep -c guess HEAD:secrets/.env",
        "git grep -q TOKEN main~2:config/secrets.json",
        "git grep guess HEAD:keys/deploy.PEM",
        "git grep -l guess -- HEAD:.env",
    ],
)
def test_a_git_grep_of_a_credential_blob_is_refused(home, command):
    """git greps a ``<rev>:<path>`` blob whole, past any exclusion pathspec."""
    refusal = _refusal(command, home)

    assert refusal is not None, command
    assert refusal["executed"] is False
    assert "credentials" in refusal["error"]


@pytest.mark.parametrize(
    "command", ["git grep -l guess 1a2b3c4d", "git grep -l guess HEAD^{blob}"]
)
def test_a_git_grep_of_an_unnamed_object_is_refused_over_credentials(home, command):
    assert _refusal(command, home) is not None, command
    (home / "proj" / "secrets" / ".env").unlink()
    assert _refusal(command, home) is None, command


# ------------------------------------------------- other tree-walking search tools


@pytest.mark.parametrize(
    "command",
    [
        "busybox grep -rl guess secrets",
        "busybox grep -r -c guess .",
        "rgrep -l guess secrets",
        "ugrep -rl guess secrets",
        "ug -l guess",
        "ag -l guess",
        "ag -l guess secrets",
        "ack -l guess",
        "sift -l guess .",
        "fd . secrets -x grep -l guess",
        "fd -X grep -c guess",
        "fd --exec=cat",
        "find . -type f -exec grep -l guess {} +",
        "find secrets -exec cat {} +",
        "grep -rl guess $(pwd)",
        "find . -name *.txt -execdir grep -c guess {} +",
        "timeout 5 ag -l guess",
        'bash -c "ag -l guess"',
    ],
)
def test_another_tree_walking_search_is_refused_over_credentials(home, command):
    refusal = _refusal(command, home)

    assert refusal is not None, command
    assert refusal["executed"] is False
    assert "credentials" in refusal["error"]


@pytest.mark.parametrize(
    "command",
    ["find src -type f -exec grep -l guess {} +", "busybox grep -l guess src/a.py"],
)
def test_another_search_of_a_clean_tree_runs(home, command):
    assert _refusal(command, home) is None, command


# ----------------------------------------------------------- the tree check


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


def test_posix_argument_order_in_the_environment_is_not_rewritten(home, monkeypatch):
    monkeypatch.setenv("POSIXLY_CORRECT", "1")

    assert _argv("grep -rl guess secrets") is None
    assert _refusal("grep -rl guess secrets", home) is not None


@pytest.mark.parametrize(
    "command",
    ["grep -l guess secrets/notes.txt", "rg --version", "git log -1", "ls -R"],
)
def test_a_command_that_is_not_a_recursive_search_is_left_alone(command):
    assert _argv(command) is None


# ------------------------------------- what reaches the process is plain argv


class _FakeProcess:
    def __init__(self, args):
        self.args = args
        self.returncode = 1
        self.pid = 0

    def communicate(self, timeout=None):
        return "", ""

    def poll(self):
        return self.returncode


@pytest.fixture
def popen_calls(monkeypatch):
    """Every process the shell tool starts, as ``(args, kwargs)``; none really runs."""
    calls = []

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return _FakeProcess(args)

    monkeypatch.setattr(shell_tools.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        shell_tools, "_program_on_path", lambda name, cwd: f"/bin/{name}"
    )
    return calls


#: What grep gets after the user's own words: one --exclude per pattern.
GREP_EXCLUSIONS = [f"--exclude={pattern}" for pattern in _grep_exclusion_patterns()]


def _assert_grep_exclusions(words):
    """Each pattern is its own --exclude word: BSD grep has no --exclude-from."""
    assert words == GREP_EXCLUSIONS, words


def _run(home, command, full_access=False):
    host = _Host(home, full_access=full_access)
    host.register_shell_tools()
    run = get_tool_metadata("run_shell_command")["function"]
    return run(command=command, working_directory=str(home / "proj"))


@pytest.mark.parametrize("full_access", [False, True])
def test_the_search_runs_as_argv_with_the_exclusions(home, popen_calls, full_access):
    result = _run(home, "grep -rl guess secrets", full_access)

    assert len(popen_calls) == 1, result
    args, kwargs = popen_calls[0]
    assert isinstance(args, list)
    assert not kwargs.get("shell")
    assert os.path.basename(args[0]) in ("grep", "grep.exe")
    assert args[1:4] == ["-rl", "guess", "secrets"]
    _assert_grep_exclusions(args[4:])
    assert SKIPPED_CREDENTIALS_NOTE in result.get("note", "")
    assert result["command"] == "grep -rl guess secrets"


@pytest.mark.parametrize("full_access", [False, True])
@pytest.mark.parametrize(
    "command",
    [
        'grep -rl guess . "x\\"',
        "grep -rl guess . # comment",
        "grep -rl guess . #",
        'grep -rl guess "secrets" "x\\" -e guess',
        "grep -rl guess . ;",
    ],
)
def test_a_line_a_shell_would_reread_never_runs_without_the_exclusions(
    home, popen_calls, full_access, command
):
    """Either refused, or run as argv that carries every exclusion."""
    result = _run(home, command, full_access)

    for args, kwargs in popen_calls:
        assert isinstance(args, list) and not kwargs.get("shell"), (command, args)
        _assert_grep_exclusions(args[-len(GREP_EXCLUSIONS) :])
    if not popen_calls:
        assert result.get("executed") is False, result
    assert "guess-me" not in str(result)


def test_an_ordinary_recursive_search_still_runs(home, popen_calls):
    result = _run(home, "grep -rn TODO .")

    assert len(popen_calls) == 1, result
    args, kwargs = popen_calls[0]
    assert args[1:4] == ["-rn", "TODO", "."]
    _assert_grep_exclusions(args[4:])


def test_a_refused_search_never_reaches_the_process_layer(home, popen_calls):
    result = _run(home, "grep -rl guess -- secrets", full_access=True)

    assert popen_calls == []
    assert result["executed"] is False
    assert "guess-me" not in str(result)


def test_an_ordinary_command_gets_no_note(home, popen_calls):
    result = _run(home, "ls", full_access=True)

    assert SKIPPED_CREDENTIALS_NOTE not in result.get("note", "")


def test_exclusion_words_survive_posix_quoting():
    for flavor in ("grep", "rg", "git"):
        words = _credential_exclusions(flavor)
        assert shlex.split(" ".join(shlex.quote(w) for w in words)) == words


# ------------------------------------- @file words and planted pattern names


@pytest.mark.parametrize(
    "command",
    [
        "grep -rl guess . @f",
        "grep -rl guess src @f",
        "grep -rl @f guess secrets",
        "rg -l guess @f",
        "git grep -l guess @f",
        'grep -rl guess "@f" src',
    ],
)
def test_an_at_file_word_is_refused_over_a_credential_tree(home, command):
    """Git for Windows' grep reads ``@f`` as more arguments, ``--`` included."""
    (home / "proj" / "f").write_text("--\n")

    assert _argv(command) is None, command
    refusal = _refusal(command, home)

    assert refusal is not None, command
    assert refusal["executed"] is False


def test_an_at_file_search_of_a_clean_tree_runs(home):
    (home / "proj" / "secrets" / ".env").unlink()

    assert _refusal("grep -rl guess . @f", home) is None


def test_a_planted_pattern_named_file_does_not_weaken_the_exclusions(home, popen_calls):
    for name in ("--exclude=.env", "--exclude=.[eE][nN][vV]"):
        try:
            (home / "proj" / name).write_text("x\n")
        except OSError:
            continue  # '[' or '=' not allowed in a file name here

    result = _run(home, "grep -rl guess .")

    assert len(popen_calls) == 1, result
    args, kwargs = popen_calls[0]
    _assert_grep_exclusions(args[4:])
    assert "--exclude=.[eE][nN][vV]" in args


# ------------------------------- grep that wildcard-expands its own arguments


@pytest.fixture
def windows_grep(monkeypatch):
    """Git for Windows' grep: every argument it gets is wildcard-expanded."""
    monkeypatch.setattr(shell_tools, "_greps_expand_arguments", lambda: True)


@pytest.mark.parametrize(
    "command",
    [
        "grep -rl guess .",
        'grep -rl guess . "?-"',
        "grep -rn TODO .",
        "egrep -rl guess secrets",
        "fgrep -rc guess .",
        "grep.exe -rl guess .",
        "busybox grep -rl guess .",
    ],
)
def test_a_recursive_grep_that_expands_its_arguments_is_refused(
    home, windows_grep, command
):
    assert _argv(command) is None, command

    refusal = _refusal(command, home)

    assert refusal is not None, command
    assert refusal["executed"] is False
    assert "rg or git grep" in refusal["hint"]


@pytest.mark.parametrize(
    "command", ["grep -rl guess .", 'grep -rl guess . "?-"', "grep -rn TODO ."]
)
def test_a_recursive_grep_that_expands_its_arguments_runs_in_a_clean_tree(
    home, windows_grep, command
):
    (home / "proj" / "secrets" / ".env").unlink()

    assert _refusal(command, home) is None, command


@pytest.mark.parametrize(
    "command, flavor",
    [("rg -l guess", "rg"), ("rg -n TODO", "rg"), ("git grep -l guess", "git")],
)
def test_rg_and_git_grep_still_run_excluded_where_grep_expands(
    home, windows_grep, popen_calls, command, flavor
):
    result = _run(home, command)

    assert len(popen_calls) == 1, result
    args, kwargs = popen_calls[0]
    assert not kwargs.get("shell")
    assert args[-len(_credential_exclusions(flavor)) :] == (
        _credential_exclusions(flavor)
    )


def test_a_refused_grep_where_grep_expands_never_starts(
    home, windows_grep, popen_calls
):
    result = _run(home, 'grep -rl guess . "?-"', full_access=True)

    assert popen_calls == []
    assert result["executed"] is False
    assert "guess-me" not in str(result)


def test_posix_grep_still_runs_with_the_exclusions(home, popen_calls):
    result = _run(home, 'grep -rl guess . "?-"')

    assert len(popen_calls) == 1, result
    args, kwargs = popen_calls[0]
    assert args[1:5] == ["-rl", "guess", ".", "?-"]
    _assert_grep_exclusions(args[5:])


# ----------------------------------- grep's exclusions on GNU and BSD (macOS)


def _added_words(args, command):
    """The words after *command*'s own: what the shell tool added."""
    assert isinstance(args, list), args
    own = shlex.split(command)
    assert os.path.basename(args[0]) in (own[0], own[0] + ".exe")
    assert args[1 : len(own)] == own[1:], args
    return args[len(own) :]


def test_the_exclusions_come_after_the_users_include(home, popen_calls):
    """In GNU and BSD grep the last matching include/exclude wins."""
    _run(home, "grep -rl --include=.env guess secrets")

    args, kwargs = popen_calls[0]
    assert not kwargs.get("shell")
    assert args.index("--include=.env") < args.index("--exclude=.[eE][nN][vV]")
    assert _added_words(args, "grep -rl --include=.env guess secrets") == (
        GREP_EXCLUSIONS
    )


def test_planted_names_never_turn_into_arguments(home, popen_calls):
    """Argv with no shell: nothing expands ``.[eE][nN][vV]`` into a planted name."""
    for name in ("--", ".eNv", "-e", "--exclude-from=x"):
        (home / "proj" / "secrets" / name).write_text("x\n")

    _run(home, "grep -rl guess secrets")

    args, kwargs = popen_calls[0]
    assert not kwargs.get("shell")
    assert _added_words(args, "grep -rl guess secrets") == GREP_EXCLUSIONS


@pytest.mark.parametrize(
    "command",
    [
        "grep -rn TODO src",
        "grep -rl --include=a.py TODO src",
        "egrep -rn TODO src",
        "grep -rn TODO src/a.py src",
    ],
)
def test_a_clean_tree_grep_gets_no_extra_arguments(home, popen_calls, command):
    """Exclusions only when needed, so a normal search runs exactly as typed."""
    result = _run(home, command)

    assert len(popen_calls) == 1, result
    args, kwargs = popen_calls[0]
    assert not kwargs.get("shell")
    assert _added_words(args, command) == []


@pytest.mark.parametrize(
    "command",
    [
        "grep -Rn TODO src",
        "grep -rn --dereference-recursive TODO src",
        "grep -rRn TODO src",
    ],
)
def test_a_grep_that_follows_links_keeps_the_exclusions_in_a_clean_tree(
    home, popen_calls, command
):
    """The tree check does not descend linked folders; grep -R does."""
    _run(home, command)

    args, kwargs = popen_calls[0]
    assert not kwargs.get("shell")
    assert _added_words(args, command) == GREP_EXCLUSIONS


def test_a_credential_file_behind_a_followed_folder_link_stays_out(home, popen_calls):
    other = home / "proj" / "other"
    other.mkdir()
    (other / ".env").write_text("TOKEN=guess-me\n")
    try:
        os.symlink(other, home / "proj" / "src" / "link", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")

    _run(home, "grep -Rn guess src")

    args, _ = popen_calls[0]
    assert _added_words(args, "grep -Rn guess src") == GREP_EXCLUSIONS


@pytest.mark.parametrize("command", ["rg -n TODO src", "git grep -n TODO -- src"])
def test_rg_and_git_grep_keep_their_exclusions_in_a_clean_tree(
    home, popen_calls, command
):
    flavor = "rg" if command.startswith("rg") else "git"

    _run(home, command)

    args, _ = popen_calls[0]
    assert args[-len(_credential_exclusions(flavor)) :] == (
        _credential_exclusions(flavor)
    )


def test_the_tree_is_read_again_when_the_grep_starts(home):
    """A credential file added after the check still gets the exclusions."""
    steps, _ = _parse_line("grep -rn TODO src", bypass_gates=True)
    plan = _search_plan("grep -rn TODO src", steps)
    (home / "proj" / "src" / "id_rsa").write_text("-----BEGIN-----\n")

    with shell_tools._searched_as_argv(steps[0], plan, str(home / "proj")) as step:
        assert step.argv_only
        assert step.segments[0] == ["grep", "-rn", "TODO", "src", *GREP_EXCLUSIONS]


def test_a_tree_too_large_to_check_gets_the_exclusions(home, monkeypatch):
    monkeypatch.setattr(shell_tools, "_TREE_CHECK_LIMIT", 0)
    steps, _ = _parse_line("grep -rn TODO src", bypass_gates=True)
    plan = _search_plan("grep -rn TODO src", steps)

    with shell_tools._searched_as_argv(steps[0], plan, str(home / "proj")) as step:
        assert step.segments[0][4:] == GREP_EXCLUSIONS


@pytest.mark.parametrize("command", ["grep -rl guess secrets", "grep -rn TODO src"])
def test_windows_grep_is_still_never_rewritten(home, windows_grep, command):
    assert _argv(command) is None
