# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A find_files miss names the folders it searched (#5026).

A bare "No files found" let the model rerun the same scan; naming the folders
shows the search was complete.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.security import PathValidator


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    work = home / "Work"
    work.mkdir(parents=True)
    (work / "report.txt").write_text("quarterly numbers\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(work)
    return home


def _find_files(allowed):
    class Host(FileSystemToolsMixin):
        def __init__(self):
            self._web_client = None
            self._path_validator = PathValidator(allowed_paths=allowed)
            self._fs_index = None
            self._tools = {}
            self._bookmarks = {}

    registered = {}

    def mock_tool(atomic=True, **_kwargs):
        def decorator(func):
            registered[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", mock_tool):
        Host().register_filesystem_tools()
    return registered["find_files"]


def test_a_name_miss_lists_the_searched_folder(home):
    out = _find_files([str(home)])("no_such_file_xyz", scope="home")

    assert out.startswith("No files found matching 'no_such_file_xyz'.")
    assert f"Searched: {home.resolve()}." in out
    assert "Repeating this search will not find it" in out


def test_a_content_miss_lists_the_searched_folder(home):
    out = _find_files([str(home)])(
        "no such phrase", search_type="content", scope="home"
    )

    assert f"Searched: {home.resolve()}." in out


def test_smart_scope_lists_every_existing_root_once(home):
    (home / "Downloads").mkdir()
    out = _find_files([str(home)])("no_such_file_xyz", scope="smart")

    searched = out.split("Searched: ", 1)[1].split(". Repeating", 1)[0]
    roots = searched.split(", ")
    assert str((home / "Work").resolve()) in roots
    assert str((home / "Downloads").resolve()) in roots
    assert len(roots) == len(set(roots))
    # A folder that does not exist was never searched, so it is not named.
    assert "Desktop" not in searched


def test_a_hit_does_not_list_searched_folders(home):
    out = _find_files([str(home)])("report", scope="home")

    assert "report.txt" in out
    assert "Searched:" not in out


def test_no_searchable_folder_is_said_plainly(home, monkeypatch):
    # Every root in scope vanished between resolution and the walk.
    monkeypatch.setattr(
        "gaia.agents.tools.filesystem_tools.confine_roots",
        lambda host, roots: [home / "gone"],
    )
    out = _find_files([str(home)])("anything", scope="home")

    assert "Searched:" not in out
    assert "No folder in scope 'home' could be searched." in out
