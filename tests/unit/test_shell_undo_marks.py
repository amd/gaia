# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A shell command that may write marks the turn's undo as incomplete (#5225).

GAIA cannot copy what an arbitrary command is about to change, so undo must
say it is incomplete for any command outside the read-only tier — and must not
cry wolf for one inside it.
"""

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
