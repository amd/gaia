# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A content search must not ask the user about every file it scans.

In the Agent UI the path validator asks the host before an out-of-scope read.
Content search checked each scanned file through that same gate, so one
``find_files(search_type="content", scope="home")`` raised a permission prompt
per file outside the chat's folders: 4,249 of them in one turn. These tests use
the real ``PathValidator`` with a host prompt attached, the setup that flooded.
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
    inside = home / "chat-folder"
    outside = home / "other-project"
    inside.mkdir(parents=True)
    outside.mkdir()
    (inside / "notes.md").write_text("the acme auth token rotates daily\n")
    for i in range(5):
        (outside / f"doc{i}.md").write_text("acme auth lives here too\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(inside)
    # tmp paths are otherwise "unaskable"; let the host prompt be reached.
    monkeypatch.setattr(PathValidator, "_unaskable_reason", lambda self, path: None)
    return home, inside


def _find_files(validator):
    class Host(FileSystemToolsMixin):
        def __init__(self):
            self._web_client = None
            self._path_validator = validator
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
        Host().register_filesystem_tools()
    return registered["find_files"]


def test_a_content_search_over_home_asks_nothing(home):
    _, inside = home
    asked = []
    validator = PathValidator(allowed_paths=[str(inside)])
    validator.set_access_prompt(lambda path: asked.append(path) or False)

    out = _find_files(validator)("acme auth", search_type="content", scope="home")

    assert asked == []
    assert "notes.md" in out


def test_it_says_how_many_files_it_could_not_search(home):
    _, inside = home
    validator = PathValidator(allowed_paths=[str(inside)])
    validator.set_access_prompt(lambda path: False)

    out = _find_files(validator)("acme auth", search_type="content", scope="home")

    assert "5 file(s) outside the folders you can read were not searched" in out
    assert "doc0.md" not in out


def test_a_miss_still_says_what_was_skipped(home):
    _, inside = home
    validator = PathValidator(allowed_paths=[str(inside)])
    validator.set_access_prompt(lambda path: False)

    out = _find_files(validator)("no such phrase", search_type="content", scope="home")

    assert out.startswith("No files found matching 'no such phrase'.")
    assert "ask the user to add its folder" in out


def test_nothing_skipped_adds_no_note(home):
    home_dir, _ = home
    validator = PathValidator(allowed_paths=[str(home_dir)])

    out = _find_files(validator)("acme auth", search_type="content", scope="home")

    assert "not searched" not in out
    assert "doc0.md" in out and "notes.md" in out
