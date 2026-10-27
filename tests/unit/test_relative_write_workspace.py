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
            "registry": dict(_TOOL_REGISTRY),
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


def _tool(tools, name):
    return tools["registry"][name]["function"]


@pytest.mark.parametrize(
    "name, kwargs, file_name",
    (
        ("write_markdown_file", {"content": "# hi\n"}, "notes.md"),
        ("write_python_file", {"content": "x = 1\n"}, "helper.py"),
    ),
)
def test_every_create_tool_writes_into_the_chat_workspace(
    tools, name, kwargs, file_name
):
    result = _tool(tools, name)(file_path=file_name, **kwargs)

    assert result["status"] == "success", result
    assert (tools["workspace"] / file_name).exists()
    assert not (tools["sidecar"] / file_name).exists()


def test_python_edits_and_diffs_use_the_workspace_file(tools):
    original = "def f():\n    return 1\n"
    target = tools["workspace"] / "mod.py"
    target.write_text(original, encoding="utf-8")
    # A same-named file in the sidecar cwd must never be the one touched.
    decoy = tools["sidecar"] / "mod.py"
    decoy.write_text(original, encoding="utf-8")
    assert tools["read_file"](file_path="mod.py")["status"] == "success"

    diff = _tool(tools, "generate_diff")(file_path="mod.py", new_content="x = 2\n")
    assert diff["status"] == "success", diff

    edited = _tool(tools, "edit_python_file")(
        file_path="mod.py", old_content="return 1", new_content="return 2"
    )
    assert edited["status"] == "success", edited
    assert "return 2" in target.read_text(encoding="utf-8")

    replaced = _tool(tools, "replace_function")(
        file_path="mod.py",
        function_name="f",
        new_implementation="def f():\n    return 3\n",
    )
    assert replaced["status"] == "success", replaced
    assert "return 3" in target.read_text(encoding="utf-8")
    assert decoy.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "name",
    (
        "write_file",
        "edit_file",
        "write_markdown_file",
        "write_python_file",
        "edit_python_file",
        "replace_function",
    ),
)
def test_the_approval_prompt_names_the_workspace_path(tools, name):
    target_of = tools["registry"][name]["preflight"].target_of

    assert Path(target_of({"file_path": "notes.txt"})) == (
        tools["workspace"] / "notes.txt"
    )


@pytest.mark.parametrize(
    "name, kwargs",
    (
        ("write_file", {"content": "x"}),
        ("write_markdown_file", {"content": "x"}),
        ("write_python_file", {"content": "x = 1\n"}),
    ),
)
def test_a_relative_path_cannot_climb_out_of_the_workspace(tools, name, kwargs):
    result = _tool(tools, name)(file_path="../escape.py", **kwargs)

    assert result["status"] == "error", result
    assert not (tools["workspace"].parent / "escape.py").exists()


def test_an_absolute_path_is_not_rebased_onto_the_workspace(tools):
    inside = tools["workspace"] / "sub" / "abs.md"

    result = _tool(tools, "write_markdown_file")(file_path=str(inside), content="x")

    assert result["status"] == "success", result
    assert inside.exists()
    assert not (tools["workspace"] / "sub" / "sub").exists()
