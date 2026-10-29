# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A shell command that may write marks the turn's undo as incomplete (#5225).

GAIA cannot copy what an arbitrary command is about to change, so undo must
say it is incomplete for any command outside the read-only tier — and must not
cry wolf for one inside it.
"""

import os
from pathlib import Path

import pytest

from gaia.agents.base import file_snapshots as fs
from gaia.agents.tools.shell_tools import ShellToolsMixin


class _Console:
    def __init__(self, bypass: bool):
        self.full_access = bypass


class _Grants:
    def __init__(self, *binaries: str):
        self._binaries = frozenset(binaries)

    def binaries(self) -> frozenset:
        return self._binaries


class _Host(ShellToolsMixin):
    def __init__(self, bypass: bool, granted: tuple = ()):
        self.console = _Console(bypass)
        self.debug = False
        if granted:
            self._granted_binaries = _Grants(*granted)


@pytest.fixture
def shell_tool(monkeypatch):
    def build(bypass: bool, granted: tuple = ()):
        captured = {}

        def fake_tool(**kwargs):
            def wrap(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return wrap

        import gaia.agents.base.tools as tools_mod

        monkeypatch.setattr(tools_mod, "tool", fake_tool)
        _Host(bypass, granted).register_shell_tools()
        return captured["run_shell_command"]

    return build


def _untracked(root):
    turns = fs.list_turns("s", root=root)
    return [u for t in turns for u in t["untracked"]]


def test_read_only_command_leaves_undo_complete(shell_tool, tmp_path):
    run = shell_tool(bypass=False)
    root = tmp_path / "snapshots"
    with fs.recording_turn("s", root=root):
        result = run("echo hi", working_directory=str(tmp_path))
    assert result["status"] == "success", result
    assert _untracked(root) == []


def test_command_under_full_access_marks_undo_incomplete(shell_tool, tmp_path):
    run = shell_tool(bypass=True)
    root = tmp_path / "snapshots"
    with fs.recording_turn("s", root=root):
        result = run("echo hi", working_directory=str(tmp_path))
    assert result["status"] == "success", result
    assert _untracked(root) == [
        {"kind": "shell", "detail": "echo hi", "paths": [], "may_write": True}
    ]


def test_skill_granted_cli_marks_undo_incomplete(shell_tool, tmp_path):
    run = shell_tool(bypass=False, granted=("git",))
    root = tmp_path / "snapshots"
    with fs.recording_turn("s", root=root):
        result = run("git --version", working_directory=str(tmp_path))
    assert result["status"] == "success", result
    assert [u["detail"] for u in _untracked(root)] == ["git --version"]


# A shell delete GAIA can read exactly is copied first, so it shows as a
# deleted file with Undo instead of untracked shell work.

_DELETE = "del" if os.name == "nt" else "rm"


class _ScopedHost(_Host):
    def __init__(self, workspace):
        super().__init__(bypass=False)
        self._workspace = os.path.realpath(workspace)

    def _is_path_allowed(self, path):
        real = os.path.realpath(path)
        return os.path.commonpath([real, self._workspace]) == self._workspace


@pytest.fixture
def scoped_shell_tool(monkeypatch):
    def build(workspace):
        captured = {}

        def fake_tool(**kwargs):
            def wrap(fn):
                captured[kwargs.get("name", fn.__name__)] = fn
                return fn

            return wrap

        import gaia.agents.base.tools as tools_mod

        monkeypatch.setattr(tools_mod, "tool", fake_tool)
        _ScopedHost(workspace).register_shell_tools()
        return captured["run_shell_command"]

    return build


def test_shell_delete_in_workspace_is_a_restorable_deleted_file(
    scoped_shell_tool, tmp_path
):
    workspace = tmp_path / "work"
    workspace.mkdir()
    notes = workspace / "notes.txt"
    notes.write_text("hello\n", encoding="utf-8")
    run = scoped_shell_tool(workspace)
    root = tmp_path / "snapshots"

    with fs.recording_turn("s", root=root) as rec:
        result = run(f"{_DELETE} notes.txt", working_directory=str(workspace))
    assert result["status"] == "success", result
    assert not notes.exists()

    [turn] = fs.list_turns("s", root=root)
    assert turn["untracked"] == [] and not turn["incomplete"]
    assert [(Path(f["path"]).name, f["change"], f["tool"]) for f in turn["files"]] == [
        ("notes.txt", "deleted", "run_shell_command")
    ]
    [diff] = fs.diff_turn("s", rec.turn_id, root=root)
    assert "-hello" in diff["unified"]

    fs.restore_turn("s", rec.turn_id, root=root)
    assert notes.read_text(encoding="utf-8") == "hello\n"


def test_shell_delete_with_unknown_effects_stays_untracked(scoped_shell_tool, tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    run = scoped_shell_tool(workspace)
    root = tmp_path / "snapshots"

    command = f"{_DELETE} *.txt"
    with fs.recording_turn("s", root=root):
        run(command, working_directory=str(workspace))

    [turn] = fs.list_turns("s", root=root)
    assert turn["files"] == []
    assert [u["detail"] for u in turn["untracked"]] == [command]


def test_shell_delete_outside_workspace_is_not_restorable(scoped_shell_tool, tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("keep", encoding="utf-8")
    run = scoped_shell_tool(workspace)
    root = tmp_path / "snapshots"

    command = f"{_DELETE} {outside}"
    with fs.recording_turn("s", root=root):
        run(command, working_directory=str(workspace))

    [turn] = fs.list_turns("s", root=root)
    assert turn["files"] == []
    assert [u["detail"] for u in turn["untracked"]] == [command]


def test_a_failed_copy_leaves_the_file_and_runs_nothing(
    scoped_shell_tool, tmp_path, monkeypatch
):
    workspace = tmp_path / "work"
    workspace.mkdir()
    notes = workspace / "notes.txt"
    notes.write_text("hello", encoding="utf-8")
    run = scoped_shell_tool(workspace)

    def refuse(self, path, tool=None):
        raise fs.SnapshotError("disk full")

    monkeypatch.setattr(fs.TurnRecorder, "capture", refuse)
    with fs.recording_turn("s", root=tmp_path / "snapshots"):
        result = run(f"{_DELETE} notes.txt", working_directory=str(workspace))
    assert result["status"] == "error" and "disk full" in result["error"]
    assert notes.exists()
