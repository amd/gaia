# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""File search must look in GAIA's own documents folder.

GAIA saves files to ``~/.gaia/documents``, but every walker skips dot-folders,
so ``find_files(scope="home")`` and ``scope="everywhere"`` both reported a file
GAIA itself wrote as missing. Other dot-folders stay skipped. The home folder
and ``GAIA_HOME`` are temp dirs, so these never touch the real home.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.agents.tools.search_scope import hidden_granted_roots


class _Sandbox:
    """Stand-in for PathValidator: the paths the chat was granted."""

    scratch_dir = None

    def __init__(self, *paths):
        self.allowed_paths = {Path(p).resolve() for p in paths}

    def is_path_allowed(self, path, prompt_user=True):
        resolved = Path(path).resolve()
        return any(
            resolved == root or root in resolved.parents for root in self.allowed_paths
        )

    def validate_read(self, path, prompt_user=True):
        if not self.is_path_allowed(path, prompt_user=prompt_user):
            return False, f"Access denied: '{path}' is not in allowed paths"
        return True, ""

    def confine_roots(self, roots):
        """Keeps a drive-wide walk inside the temp home, off the real disk."""
        confined = []
        for root in map(Path, roots):
            if self.is_path_allowed(root):
                confined.append(root)
            else:
                confined.extend(
                    a for a in sorted(self.allowed_paths) if root in a.parents
                )
        return confined


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home whose GAIA documents folder and ``.cache`` each hold a draft."""
    home = (tmp_path / "home").resolve()
    project = home / "code" / "proj"
    project.mkdir(parents=True)
    (project / "readme.md").write_text("project\n")
    documents = home / ".gaia" / "documents"
    documents.mkdir(parents=True)
    (documents / "old_draft.txt").write_text("quarterly zebra plan\n")
    cache = home / ".cache"
    cache.mkdir()
    (cache / "old_draft.txt").write_text("quarterly zebra plan\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("GAIA_HOME", str(home / ".gaia"))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.chdir(project)
    return home


def _find_files(*allowed):
    class Agent(FileSystemToolsMixin):
        def __init__(self):
            self._web_client = None
            self._path_validator = _Sandbox(*allowed)
            self._fs_index = None
            self._tools = {}
            self._bookmarks = {}

    registered = {}

    def mock_tool(atomic=True):
        def decorator(func):
            registered[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", mock_tool):
        Agent().register_filesystem_tools()
    return registered["find_files"]


def _draft(home):
    return str(home / ".gaia" / "documents" / "old_draft.txt")


@pytest.mark.parametrize("scope", ["home", "everywhere"])
def test_find_files_searches_gaia_documents(home, scope):
    result = _find_files(home)("old_draft.txt", scope=scope)

    assert _draft(home) in result
    assert result.count("old_draft.txt") == 1, result
    assert str(home / ".cache") not in result


def test_content_search_reads_gaia_documents(home):
    result = _find_files(home)("zebra", search_type="content", scope="home")

    assert _draft(home) in result
    assert str(home / ".cache") not in result


def test_granted_folder_inside_a_dot_folder_is_searched(home):
    notes = home / ".config" / "notes"
    notes.mkdir(parents=True)
    (notes / "todo_list.md").write_text("x\n")

    result = _find_files(home, notes)("todo_list", scope="home")

    assert str(notes / "todo_list.md") in result


def test_nested_grants_return_each_file_once(home):
    gaia = home / ".gaia"
    result = _find_files(home, gaia, gaia / "documents")("old_draft", scope="home")

    assert result.count(_draft(home)) == 1, result


def test_documents_outside_the_scope_are_not_added(home):
    project = home / "code" / "proj"

    result = _find_files(project)("old_draft", scope="cwd")

    assert "No files found" in result
    assert hidden_granted_roots(_Sandbox(project), [project]) == []


def test_relocated_gaia_home_is_searched(home, monkeypatch):
    elsewhere = home / ".local" / "gaia-state"
    (elsewhere / "documents").mkdir(parents=True)
    (elsewhere / "documents" / "moved_draft.txt").write_text("x\n")
    monkeypatch.setenv("GAIA_HOME", str(elsewhere))

    result = _find_files(home)("moved_draft", scope="home")

    assert str(elsewhere / "documents" / "moved_draft.txt") in result


def test_search_file_finds_gaia_documents(home):
    mixin = FileSearchToolsMixin()
    mixin.path_validator = _Sandbox(home)
    saved = dict(_TOOL_REGISTRY)
    try:
        mixin.register_file_search_tools()
        search_file = _TOOL_REGISTRY["search_file"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    result = search_file("old_draft.txt")

    assert result["status"] == "success", result
    assert result["files"] == [_draft(home)]
