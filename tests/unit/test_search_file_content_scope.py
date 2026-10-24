# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""search_file_content searches the workspace the user means, and every text file in it.

Observed: "No matches found for 'style_data\\(' in 0 files" on a folder where
findstr found matches. The default directory "." resolved against the sidecar
process's cwd instead of the workspace, and files with an unlisted extension
were never opened, so the search reported an honest-looking zero.
"""

from pathlib import Path

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _Host(FileSearchToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A project the session is scoped to, and an unrelated process cwd."""
    project = tmp_path / "project"
    (project / "src" / "ui").mkdir(parents=True)
    (project / "src" / "ui" / "table.ts").write_text(
        "export function style_data(rows) {}\n", encoding="utf-8"
    )
    (project / "src" / "report.R").write_text("style_data(df)\n", encoding="utf-8")
    (project / "src" / "app.py").write_text("style_data(x)\n", encoding="utf-8")
    (project / "logo.png").write_bytes(b"\x89PNG\x00\x00style_data(")
    (project / "blob").write_bytes(b"\x00\x01style_data(")
    elsewhere = tmp_path / "sidecar"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    validator = PathValidator(allowed_paths=[str(project)])
    _Host(validator).register_file_search_tools()
    return project.resolve()


def _search(**kwargs):
    return _TOOL_REGISTRY["search_file_content"]["function"](**kwargs)


def _files(result):
    return sorted(Path(m["file"]).name for m in result["matches"])


def test_default_directory_is_the_workspace_not_the_process_cwd(workspace):
    result = _search(pattern=r"style_data\(")

    assert result["status"] == "success"
    assert result["directory"] == str(workspace)
    assert "app.py" in _files(result)


def test_relative_directory_resolves_inside_the_workspace(workspace):
    result = _search(pattern=r"style_data\(", directory="src/ui")

    assert _files(result) == ["table.ts"]


def test_text_files_with_unlisted_extensions_are_searched(workspace):
    result = _search(pattern=r"style_data\(", directory=str(workspace))

    assert _files(result) == ["app.py", "report.R", "table.ts"]
    assert result["files_searched"] == 3


def test_recursive_glob_file_pattern_matches_by_name(workspace):
    result = _search(pattern="style_data", file_pattern="**/*.ts")

    assert _files(result) == ["table.ts"]


def test_path_file_pattern_matches_the_relative_path(workspace):
    result = _search(pattern="style_data", file_pattern="src/ui/*")

    assert _files(result) == ["table.ts"]


def test_nothing_to_search_is_not_reported_as_no_matches(workspace):
    result = _search(pattern="style_data", file_pattern="*.go")

    assert result["files_searched"] == 0
    assert "Nothing was searched" in result["message"]
    assert "not an empty result" in result["message"]


def test_a_relative_folder_outside_every_root_is_an_error(workspace):
    result = _search(pattern="x", directory="no/such/folder")

    assert result["status"] == "error"
    assert "not under any workspace root" in result["error"]


def test_dependency_and_vcs_folders_are_not_descended_into(workspace):
    for skipped in ("node_modules/pkg", ".git/objects"):
        folder = workspace / skipped
        folder.mkdir(parents=True)
        (folder / "copy.js").write_text("style_data(x)\n", encoding="utf-8")

    result = _search(pattern=r"style_data\(")
    assert "copy.js" not in _files(result)

    # Searching one of them directly still works.
    direct = _search(pattern=r"style_data\(", directory="node_modules")
    assert _files(direct) == ["copy.js"]
