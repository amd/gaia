# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A find_files miss names the folders it searched (#5026).

A bare "No files found" let the model rerun the same scan; naming the folders
shows the search was complete.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
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


def _searched(out):
    return out.split("Searched: ", 1)[1].split(". Repeating", 1)[0].split(", ")


def test_smart_scope_lists_every_existing_root_once(home):
    # Projects, not Downloads: macOS never walks Downloads in a default search.
    (home / "Projects").mkdir()
    out = _find_files([str(home)])("no_such_file_xyz", scope="smart")

    roots = _searched(out)
    assert str((home / "Work").resolve()) in roots
    assert str((home / "Projects").resolve()) in roots
    assert len(roots) == len(set(roots))
    # A folder that does not exist was never searched, so it is not named.
    assert not any(r.endswith("Desktop") for r in roots)


def test_a_macos_guarded_folder_is_skipped_not_named_as_searched(home, monkeypatch):
    (home / "Downloads").mkdir()
    monkeypatch.setattr(
        "gaia.agents.tools.protected_folders.sys",
        SimpleNamespace(platform="darwin"),
    )
    out = _find_files([str(home)])("no_such_file_xyz", scope="smart")

    roots = _searched(out)
    assert str((home / "Work").resolve()) in roots
    assert str((home / "Downloads").resolve()) not in roots
    assert "Skipped ~/Downloads: macOS asks for permission" in out


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


def _tiny_budget(monkeypatch, entries):
    from gaia.agents.tools import filesystem_tools

    real = filesystem_tools.SearchBudget
    monkeypatch.setattr(
        filesystem_tools, "SearchBudget", lambda: real(seconds=600, entries=entries)
    )


def _finished_part(out):
    if "Finished searching: " not in out:
        return ""
    return out.split("Finished searching: ", 1)[1].split(".", 1)[0]


def test_a_search_that_stopped_early_does_not_say_repeating_is_useless(
    home, monkeypatch
):
    for i in range(50):
        (home / "Work" / f"file_{i}.txt").write_text("x")
    _tiny_budget(monkeypatch, entries=5)

    out = _find_files([str(home)])("no_such_file_xyz", scope="home")

    assert "stopped early" in out
    assert "Repeating this search will not find it" not in out
    assert "Searched:" not in out
    # The root it stopped in is unfinished, so it is not reported as finished.
    assert str(home.resolve()) not in _finished_part(out)
    assert "Not finished:" in out


def test_a_stopped_search_names_only_the_roots_it_finished(home, monkeypatch):
    (home / "Downloads").mkdir()
    for i in range(50):
        (home / "Work" / f"file_{i}.txt").write_text("x")
    _tiny_budget(monkeypatch, entries=5)

    out = _find_files([str(home)])("no_such_file_xyz", scope="smart")

    assert "Repeating this search will not find it" not in out
    finished = _finished_part(out)
    not_finished = out.split("Not finished: ", 1)[1]
    for root in finished.split(", ") if finished else []:
        assert root not in not_finished
    assert str((home / "Work").resolve()) not in finished
