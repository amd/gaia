# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Relative file writes use the chat workspace, not the sidecar cwd (#5851)."""

from pathlib import Path

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator


class _Config:
    def __init__(self, working_directory: Path):
        self.working_directory = str(working_directory)
        self.project_root = None


class _Host(FileIOToolsMixin):
    def __init__(self, workspace: Path):
        self.config = _Config(workspace)
        self.console = None
        self.path_validator = PathValidator(allowed_paths=[str(workspace)])


@pytest.fixture
def tools(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    sidecar = tmp_path / "sidecar"
    workspace.mkdir()
    sidecar.mkdir()
    monkeypatch.chdir(sidecar)

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    host = _Host(workspace)
    try:
        host.register_file_io_tools()
        yield {
            "workspace": workspace,
            "sidecar": sidecar,
            "read_file": _TOOL_REGISTRY["read_file"]["function"],
            "write_file": _TOOL_REGISTRY["write_file"]["function"],
            "edit_file": _TOOL_REGISTRY["edit_file"]["function"],
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def test_a_relative_write_lands_in_the_chat_workspace(tools):
    result = tools["write_file"](file_path="notes.txt", content="hello")

    assert result["status"] == "success", result
    assert Path(result["file_path"]) == tools["workspace"] / "notes.txt"
    assert (tools["workspace"] / "notes.txt").read_text(encoding="utf-8") == "hello"
    assert not (tools["sidecar"] / "notes.txt").exists()


def test_read_and_edit_use_the_same_workspace_base(tools):
    target = tools["workspace"] / "notes.txt"
    target.write_text("before", encoding="utf-8")

    read = tools["read_file"](file_path="notes.txt")
    assert read["status"] == "success", read
    assert read["file_path"] == str(target)

    edited = tools["edit_file"](
        file_path="notes.txt", old_content="before", new_content="after"
    )
    assert edited["status"] == "success", edited
    assert target.read_text(encoding="utf-8") == "after"


def test_an_explicit_project_dir_still_overrides_the_workspace(tools):
    project = tools["workspace"] / "project"
    project.mkdir()

    result = tools["write_file"](
        file_path="notes.txt", content="project", project_dir=str(project)
    )

    assert result["status"] == "success", result
    assert Path(result["file_path"]) == project / "notes.txt"
    assert not (tools["workspace"] / "notes.txt").exists()
