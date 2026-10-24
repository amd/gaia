# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The write prompt names the folder a relative ``file_path`` lands in (#5672).

The agent's working folder is the home folder in the desktop app, so a prompt
that says only ``notes.txt`` hides where the file goes.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent, _with_resolved_target
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator


class _AskedConsole(AgentConsole):
    """Approves every gated call and keeps the arguments each prompt showed."""

    def __init__(self):
        super().__init__()
        self.shown = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.shown.append((tool_name, dict(tool_args)))
        return True


class _FileAgent(Agent, FileIOToolsMixin):
    def _get_system_prompt(self) -> str:
        return "files"

    def _register_tools(self) -> None:
        self.register_file_io_tools()
        self._snapshot_tools()

    def _create_console(self):
        return _AskedConsole()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved = dict(_TOOL_REGISTRY)
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            built = _FileAgent(silent_mode=True, skip_lemonade=True)
        validator = PathValidator()
        validator.allowed_paths.add(tmp_path.resolve())
        built.path_validator = validator
        built.console = _AskedConsole()
        yield built
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_a_relative_write_is_shown_where_it_lands(agent, tmp_path):
    result = agent._execute_tool(
        "write_file", {"file_path": "shop2.txt", "content": "milk"}
    )

    assert result["status"] == "success", result
    [(tool, shown)] = agent.console.shown
    assert tool == "write_file"
    assert shown["file_path"] == str(tmp_path.resolve() / "shop2.txt")
    assert (tmp_path / "shop2.txt").read_text(encoding="utf-8") == "milk"


def test_a_project_dir_is_folded_into_the_shown_path(agent, tmp_path):
    (tmp_path / "proj").mkdir()

    agent._execute_tool(
        "write_file",
        {"file_path": "a.md", "content": "x", "project_dir": str(tmp_path / "proj")},
    )

    [(_, shown)] = agent.console.shown
    assert shown["file_path"] == str(tmp_path.resolve() / "proj" / "a.md")


def test_an_absolute_path_is_shown_unchanged(agent, tmp_path):
    target = str(tmp_path.resolve() / "abs.txt")

    agent._execute_tool("write_file", {"file_path": target, "content": "x"})

    [(_, shown)] = agent.console.shown
    assert shown["file_path"] == target


def test_a_tool_without_a_target_is_left_alone():
    args = {"file_path": "x.txt"}

    assert _with_resolved_target(args, None) is args
    assert _with_resolved_target(args, lambda a: None) is args


def test_arguments_that_cannot_resolve_are_shown_as_sent():
    def preflight(_args):
        return None

    def target_of(_args):
        raise KeyError("file_path")

    preflight.target_of = target_of
    args = {"file_path": "x.txt"}

    assert _with_resolved_target(args, preflight) is args


def test_the_shown_path_uses_the_tools_own_resolution(tmp_path):
    def preflight(_args):
        return None

    preflight.target_of = lambda a: Path(tmp_path) / "elsewhere" / a["file_path"]

    shown = _with_resolved_target({"file_path": "n.txt", "content": "c"}, preflight)

    assert shown == {
        "file_path": str(tmp_path / "elsewhere" / "n.txt"),
        "content": "c",
    }
