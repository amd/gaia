"""A shell line that only reads, inside the allowed folders, runs with nobody asked."""

import os

import pytest

import gaia.agents.tools.shell_tools as st
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.security import PathValidator


class Host(ShellToolsMixin):
    def __init__(self, root):
        self.path_validator = PathValidator(allowed_paths=[str(root)])

    def full_access_active(self):
        return False


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def _unasked(host, tmp_path, command):
    return host.skill_grant_covers_call(
        "run_shell_command",
        {"command": command, "working_directory": str(tmp_path)},
    )


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "git log --oneline -3",
        "git log main --oneline -3 2>$null",
        "git log --oneline 2>/dev/null && git remote -v 2>$null",
        "git branch -a",
    ],
)
def test_reads_run_unasked(host, tmp_path, command):
    assert _unasked(host, tmp_path, command)


@pytest.mark.parametrize(
    "command",
    [
        "rm notes.txt",
        "git branch new-feature",
        "git push",
        # A repository's own config can run a program for these.
        "git status",
        "git log -p",
        "git diff",
        # The shell could rewrite these before the command sees them.
        "echo $HOME",
        "ls *.py",
        "powershell -Command Get-ChildItem",
    ],
)
def test_anything_else_still_asks(host, tmp_path, command):
    assert not _unasked(host, tmp_path, command)


def test_a_read_outside_the_allowed_folders_still_asks(host, tmp_path):
    outside = tmp_path.parent / "elsewhere.txt"
    assert not _unasked(host, tmp_path, f"cat {outside.as_posix()}")


def test_only_the_shell_tool_is_exempt(host, tmp_path):
    assert not host.skill_grant_covers_call(
        "run_cli_command", {"command": "ls", "working_directory": str(tmp_path)}
    )


PLAIN_CONFIG = """[core]
\trepositoryformatversion = 0
\tfilemode = false
[remote "origin"]
\turl = https://github.com/amd/gaia.git
\tfetch = +refs/heads/*:refs/remotes/origin/*
[branch "main"]
\tremote = origin
\tmerge = refs/heads/main
"""


def _repo(root, config=PLAIN_CONFIG):
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "refs").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (root / ".git" / "config").write_text(config)


def test_a_git_read_in_a_plain_repo_runs_unasked(host, tmp_path):
    _repo(tmp_path)
    assert _unasked(host, tmp_path, "git log --oneline -3")
    assert _unasked(host, tmp_path, "git -C . remote -v")


@pytest.mark.parametrize(
    "extra",
    [
        "[log]\n\tshowSignature = true\n[gpg]\n\tprogram = ./evil\n",
        "[core]\n\tfsmonitor = ./evil\n",
        "[include]\n\tpath = other.cfg\n",
        "[alias]\n\tlg = !./evil\n",
    ],
)
def test_a_repo_that_names_a_program_still_asks(host, tmp_path, extra):
    _repo(tmp_path, PLAIN_CONFIG + extra)
    assert not _unasked(host, tmp_path, "git log --oneline -3")


def test_a_worktree_reads_its_common_config_too(host, tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _repo(main, PLAIN_CONFIG + "[log]\n\tshowSignature = true\n")
    wt_dir = main / ".git" / "worktrees" / "wt"
    wt_dir.mkdir(parents=True)
    (wt_dir / "commondir").write_text("../..")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_dir.as_posix()}")
    assert not _unasked(host, tmp_path, "cd wt && git log --oneline")


def test_a_setting_on_its_section_header_line_still_asks(host, tmp_path):
    _repo(tmp_path, PLAIN_CONFIG + "[log] showSignature = true\n[gpg] program = evil\n")
    assert not _unasked(host, tmp_path, "git log -1")


def test_a_bare_repository_still_asks(host, tmp_path):
    (tmp_path / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / "objects").mkdir()
    (tmp_path / "refs").mkdir()
    (tmp_path / "config").write_text("[log]\n\tshowSignature = true\n")
    assert not _unasked(host, tmp_path, "git log -1")


def test_git_remote_show_reaches_the_network_so_it_asks(host, tmp_path):
    _repo(tmp_path)
    assert _unasked(host, tmp_path, "git remote -v")
    assert not _unasked(host, tmp_path, "git remote show origin")


@pytest.mark.parametrize(
    "command",
    [
        # cmd.exe reads ^" as a literal quote, so the & and > after it are live.
        'echo ^"x & type nul > pwned.txt & rem ^"',
        'git log -1 ^"x & calc & rem ^"',
        "echo %USERPROFILE%",
        "echo !PATH!",
        'grep "a b" notes.txt',
        "ls > listing.txt",
        "ls & calc",
        "ls | sort -o out.txt",
    ],
)
def test_a_line_a_shell_could_read_differently_still_asks(host, tmp_path, command):
    assert not _unasked(host, tmp_path, command)


@pytest.mark.parametrize(
    "command",
    [
        # A value attached to a switch is a path the folder check never sees.
        r"findstr /d:C:\Windows . win.ini",
        r"findstr /g:C:\secret.txt notes.txt",
        "grep --file=/etc/passwd notes.txt",
        "grep -f/etc/passwd notes.txt",
        # These reach another machine, or run a script.
        "wmic /node:10.0.0.1 os get caption",
        "wmic os get /format:x.xsl",
        "systeminfo /s 10.0.0.1",
        "tasklist /s 10.0.0.1",
        # Not on the short list, even though they are read-only otherwise.
        "sort notes.txt",
        "find . -name x",
        "ps",
    ],
)
def test_only_plain_listed_reads_run_unasked(host, tmp_path, command):
    assert not _unasked(host, tmp_path, command)


def test_a_listed_name_given_as_a_path_still_asks(host, tmp_path):
    """A program named ls.exe in some folder is not the ls on PATH."""
    assert not _unasked(host, tmp_path, f"{(tmp_path / 'ls.exe').as_posix()} -la")


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "head -n 5 notes.txt",
        "dir",
        # Off Windows `dir` is GNU dir, and /b is a path at the root.
        pytest.param(
            "dir /b notes.txt",
            marks=pytest.mark.skipif(os.name != "nt", reason="cmd.exe switch"),
        ),
    ],
)
def test_plain_reads_with_options_run_unasked(host, tmp_path, command):
    (tmp_path / "notes.txt").write_text("hi\n")
    assert _unasked(host, tmp_path, command)


@pytest.mark.parametrize(
    "command",
    [
        # cmd's dir reads a comma or semicolon as a second path.
        "dir /b x,C:/Windows",
        "dir x;..",
        "git log --oneline; rm notes.txt",
        # Relative to drive D's own current folder, not this one.
        "cat D:notes.txt",
        "dir D:",
    ],
)
def test_separators_cmd_reads_as_paths_still_ask(host, tmp_path, command):
    assert not _unasked(host, tmp_path, command)


def test_an_empty_dot_git_below_a_repo_still_asks(host, tmp_path):
    """git skips a .git that is not a repository and uses the one above it."""
    _repo(tmp_path, PLAIN_CONFIG + "[gpg]\n    program = ./evil\n")
    inner = tmp_path / "inner"
    (inner / ".git").mkdir(parents=True)
    assert not _unasked(host, tmp_path, "cd inner && git log -1")


def _link(link, target):
    """A link to *target*: a junction on Windows, which needs no privilege."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link)


@pytest.fixture
def outside_link(tmp_path):
    """The allowed folder, holding ``out``: a link to a folder outside it."""
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET")
    (allowed / "notes.txt").write_text("TODO")
    _link(allowed / "out", outside)
    return allowed


@pytest.mark.parametrize(
    "command",
    [
        "ls out",
        "grep -r SECRET out",
        "grep -R SECRET .",
        "grep -rn TODO .",
        "ls -LR .",
        "stat -L out",
        "dir /s",
    ],
)
def test_a_link_out_of_the_allowed_folder_still_asks(outside_link, command):
    host = Host(outside_link)
    assert not _unasked(host, outside_link, command)


@pytest.mark.parametrize(
    "command",
    [
        "grep -rn TODO .",
        "grep -r TODO notes.txt",
        "grep -R TODO sub",
        "grep --recursive TODO .",
        "grep -d recurse TODO .",
        "grep -id recurse TODO .",
        "ls -R",
        "ls -lR sub",
        "dir /s",
    ],
)
def test_a_walk_asks_even_with_no_link_in_it(host, tmp_path, command):
    """A walk reads files nobody named, so it is never unasked."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "notes.txt").write_text("TODO")
    (tmp_path / "notes.txt").write_text("TODO")
    assert not _unasked(host, tmp_path, command)


@pytest.mark.parametrize("command", ["ls ~", "grep -r password ~", "du ."])
def test_home_and_tree_sizes_still_ask(host, tmp_path, command):
    assert not _unasked(host, tmp_path, command)


@pytest.mark.parametrize(
    "command",
    [
        "findstr /si SECRET notes.txt",
        "findstr -s -i SECRET notes.txt",
        "dir /b /s",
        "dir /a:h",
        "ls out/..",
        "cd out/.. && ls",
        "cd .. && ls",
    ],
)
def test_switches_and_climbs_still_ask(outside_link, command):
    host = Host(outside_link)
    assert not _unasked(host, outside_link, command)


def test_a_dot_git_file_pointing_outside_still_asks(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    _repo(outside)
    (allowed / "wt").mkdir()
    (allowed / "wt" / ".git").write_text(f"gitdir: {(outside / '.git').as_posix()}")
    host = Host(allowed)
    assert not _unasked(host, allowed, "cd wt && git log -1")
    assert not _unasked(host, allowed, "git -C wt log -1")


def test_a_git_range_is_not_a_climb(host, tmp_path):
    _repo(tmp_path)
    assert _unasked(host, tmp_path, "git log --oneline main..feature")


def test_a_dot_git_folder_reads_its_common_config_too(host, tmp_path):
    """git follows commondir inside a .git folder, not only from a .git file."""
    other = tmp_path / "other"
    other.mkdir()
    _repo(other, PLAIN_CONFIG + "[core]\n    fsmonitor = ./evil\n")
    _repo(tmp_path)
    (tmp_path / ".git" / "commondir").write_text("../other/.git")
    assert not _unasked(host, tmp_path, "git log -1")


# --- A program found in the working folder never runs unasked -------------


@pytest.mark.parametrize(
    "command, planted",
    [
        ("git log --oneline -3", "git.bat"),
        ("git log --oneline -3", "GIT.CMD"),
        ("git log --oneline -3", "git.com"),
        pytest.param(
            "git.exe log -1",
            "git.exe",
            marks=pytest.mark.skipif(os.name != "nt", reason="Windows spelling"),
        ),
        ("ls", "ls.cmd"),
        ("ls", "ls.exe"),
        ("whoami", "whoami.exe"),
        ("whoami", "whoami.vbs"),
        ("uname -a", "uname"),
        ("grep hi notes.txt", "grep.bat"),
        # ``which`` runs ``where`` when PATH has no ``which``.
        pytest.param(
            "which git",
            "where.bat",
            marks=pytest.mark.skipif(os.name != "nt", reason="Windows spelling"),
        ),
    ],
)
def test_a_same_named_program_in_the_working_folder_asks(
    host, tmp_path, command, planted
):
    (tmp_path / "notes.txt").write_text("hi\n")
    _repo(tmp_path)
    assert _unasked(host, tmp_path, command), "unasked before the file is added"
    (tmp_path / planted).write_text("")
    assert not _unasked(host, tmp_path, command)


def test_a_program_in_the_folder_cd_moves_to_asks(host, tmp_path):
    (tmp_path / "sub").mkdir()
    assert _unasked(host, tmp_path, "cd sub && whoami")
    (tmp_path / "sub" / "whoami.cmd").write_text("")
    assert not _unasked(host, tmp_path, "cd sub && whoami")


def test_any_pathext_extension_counts(host, tmp_path, monkeypatch):
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.ODD")
    (tmp_path / "whoami.odd").write_text("")
    assert not _unasked(host, tmp_path, "whoami")


def test_a_folder_named_like_the_program_does_not_ask(host, tmp_path):
    (tmp_path / "git.bat").mkdir()
    (tmp_path / "ls").mkdir()
    _repo(tmp_path)
    assert _unasked(host, tmp_path, "git log -1")
    assert _unasked(host, tmp_path, "ls")


@pytest.mark.parametrize("entry", [".", "bin", ""])
def test_a_relative_path_entry_asks(host, tmp_path, monkeypatch, entry):
    if entry == "" and os.name == "nt":
        pytest.skip("cmd.exe skips an empty PATH entry")
    monkeypatch.setenv("PATH", entry + os.pathsep + os.environ.get("PATH", ""))
    assert not _unasked(host, tmp_path, "whoami")


def test_program_on_path_skips_the_working_folder_and_relative_entries(
    tmp_path, monkeypatch
):
    work, bin_ = tmp_path / "work", tmp_path / "bin"
    work.mkdir()
    bin_.mkdir()
    (work / "git.bat").write_text("")
    (bin_ / "git.exe").write_text("")
    monkeypatch.setenv("PATHEXT", ".EXE;.BAT")
    monkeypatch.setenv("PATH", os.pathsep.join([str(work), "work", str(bin_)]))
    assert st._program_on_path("git", str(work)) == str(bin_ / "git.exe")
    assert st._program_on_path("nothere", str(work)) is None


def test_program_on_path_skips_a_file_with_no_extension(tmp_path, monkeypatch):
    """cmd.exe passes over an npm-style ``grep`` shim to the ``grep.exe`` after it."""
    shims, bin_ = tmp_path / "shims", tmp_path / "bin"
    shims.mkdir()
    bin_.mkdir()
    (shims / "grep").write_text("#!/bin/sh\n")
    (bin_ / "grep.exe").write_text("")
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setenv("PATH", os.pathsep.join([str(shims), str(bin_)]))
    assert st._program_on_path("grep", str(tmp_path)) == str(bin_ / "grep.exe")


def test_listed_programs_are_pinned_to_their_path_location(tmp_path, monkeypatch):
    work, bin_ = tmp_path / "work", tmp_path / "bin"
    work.mkdir()
    bin_.mkdir()
    for name in ("git.exe", "grep.exe", "npm.cmd"):
        (bin_ / name).write_text("")
    monkeypatch.setenv("PATHEXT", ".EXE;.CMD")
    monkeypatch.setenv("PATH", str(bin_))
    text, listed = st._pin_listed_programs("git log -3 | grep fix", str(work))
    assert text == f'"{bin_ / "git.exe"}" log -3 | "{bin_ / "grep.exe"}" fix'
    assert listed
    # dir and echo are cmd.exe's own; no file can stand in for them.
    assert st._pin_listed_programs("dir /b", str(work)) == ("dir /b", True)
    # Anything else keeps its usual lookup, and the folder search with it.
    text, listed = st._pin_listed_programs("npm test", str(work))
    assert (text, listed) == ("npm test", False)


def test_the_cmd_launcher_runs_chcp_and_cmd_by_path(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Win")
    monkeypatch.delenv("COMSPEC", raising=False)
    line, _ = st._cmd_exe_launch("git log", {})
    cmd = os.path.join(r"C:\Win", "System32", "cmd.exe")
    chcp = os.path.join(r"C:\Win", "System32", "chcp.com")
    assert line.startswith(f'"{cmd}" /v:on /c ""{chcp}" 65001>nul && "{cmd}" /c ')


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe launch")
def test_a_listed_step_tells_cmd_not_to_search_its_folder(tmp_path, monkeypatch):
    seen = {}

    class Stop(Exception):
        pass

    def fake_popen(args, **kwargs):
        seen.update(kwargs, args=args)
        raise Stop

    monkeypatch.setattr(st.subprocess, "Popen", fake_popen)
    for command, expected in (("git log -1", "1"), ("npm test", None)):
        steps, error = st._parse_line(command, bypass_gates=False)
        assert error is None
        with pytest.raises(Stop):
            st._run_step(steps[0], str(tmp_path), 5, frozenset())
        assert seen["env"].get("NoDefaultCurrentDirectoryInExePath") == expected


# --- An unasked read honours the protected-file list ----------------------


@pytest.fixture
def secrets(tmp_path):
    (tmp_path / ".env").write_text("API_KEY=x")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / ".env").write_text("API_KEY=x")
    (tmp_path / ".npmrc").write_text("//registry/:_authToken=x")
    (tmp_path / ".git-credentials").write_text("https://u:t@host")
    (tmp_path / "id_rsa").write_text("KEY")
    (tmp_path / "server.pem").write_text("KEY")
    (tmp_path / "README.md").write_text("hello")
    (tmp_path / "src").mkdir()
    (tmp_path / "file.py").write_text("foo = 1\n")
    return tmp_path


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "cat secrets/.env",
        "cat secrets\\.env",
        "cd secrets && cat .env",
        "grep password .env",
        "head -n 3 .env.staging",
        "cat id_rsa",
        "cat server.pem",
        "grep -r password .",
        "grep -R password",
        "ls .git",
        "cd .git && cat config",
    ],
)
def test_a_protected_file_asks(host, secrets, command):
    assert not _unasked(host, secrets, command)


@pytest.mark.parametrize("name", [".npmrc", ".git-credentials"])
def test_a_protected_file_named_by_full_path_asks(host, secrets, name):
    assert not _unasked(host, secrets, f"cat {(secrets / name).as_posix()}")


def test_the_secret_folders_ask_even_when_allowed(tmp_path, monkeypatch):
    validator = PathValidator(allowed_paths=[str(tmp_path)])
    host = Host(tmp_path)
    host.path_validator = validator
    monkeypatch.setattr(validator, "is_read_blocked", lambda p: (p.endswith("aws"), ""))
    (tmp_path / "aws").mkdir()
    assert not _unasked(host, tmp_path / "aws", "ls")
    assert not _unasked(host, tmp_path, "ls aws")


def test_ordinary_reads_stay_unasked(host, secrets):
    _repo(secrets)
    for command in (
        "cat README.md",
        "ls src",
        "ls",
        "git log --oneline -3",
        "grep foo file.py",
        "head -n 1 README.md 2>/dev/null",
    ):
        assert _unasked(host, secrets, command), command


# --- Reads that never end, devices, and gpg ask -----------------------------


@pytest.mark.parametrize(
    "command",
    [
        "tail -f notes.txt",
        "tail -F notes.txt",
        "tail -n 5 -f notes.txt",
        "tail --follow notes.txt",
        "cat CON",
        "cat con",
        "cat PRN",
        "cat AUX",
        "cat NUL",
        "cat COM1",
        "cat LPT1",
        "cat con.txt",
        "head sub/COM3",
        "git log --show-signature",
        "git log -1 --show-signature",
    ],
)
def test_endless_reads_devices_and_signatures_ask(host, tmp_path, command):
    (tmp_path / "notes.txt").write_text("hi\n")
    (tmp_path / "sub").mkdir()
    _repo(tmp_path)
    assert not _unasked(host, tmp_path, command)


def test_a_plain_tail_stays_unasked(host, tmp_path):
    (tmp_path / "notes.txt").write_text("hi\n")
    assert _unasked(host, tmp_path, "tail -n 5 notes.txt")
