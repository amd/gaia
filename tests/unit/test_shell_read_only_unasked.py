"""A shell line that only reads, inside the allowed folders, runs with nobody asked."""

import pytest

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
        "git log --oneline 2>/dev/null; git remote -v 2>$null",
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
    (root / ".git").mkdir()
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
