# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A default file search on macOS must not walk into the folders macOS guards.

The first read of ``~/Documents`` makes macOS show a permission prompt and
blocks the read until someone answers, so an undirected ``find_files`` hung
until the 180 s tool timeout (#5578). The platform is patched and the home
folder is a temp dir, so these run anywhere and never touch the real home.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin

_GUARDED = (
    "Desktop",
    "Documents",
    "Downloads",
    "Library/Mobile Documents",
    "Music",
    "Pictures/Photos Library.photoslibrary",
)


class _Sandbox:
    """Stand-in for PathValidator: the paths the operator declared."""

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
    """A home with a project as cwd and a target file in every guarded folder."""
    home = (tmp_path / "home").resolve()
    project = home / "code" / "proj"
    project.mkdir(parents=True)
    (project / "target_project.md").write_text("x\n")
    for rel in _GUARDED:
        folder = home / rel
        folder.mkdir(parents=True)
        (folder / f"target_{folder.name.replace(' ', '_').lower()}.md").write_text(
            "x\n"
        )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(project)
    return home


@pytest.fixture
def on_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


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


def _file_search_tool(name, *allowed):
    mixin = FileSearchToolsMixin()
    mixin.path_validator = _Sandbox(*allowed)
    saved = dict(_TOOL_REGISTRY)
    try:
        mixin.register_file_search_tools()
        return _TOOL_REGISTRY[name]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _guarded_hits(text):
    return [n for n in ("documents", "desktop", "downloads") if f"target_{n}" in text]


class TestFindFilesOnMacOS:
    @pytest.mark.parametrize("scope", ["smart", "home", "cwd"])
    def test_a_default_scope_does_not_enter_the_guarded_folders(
        self, home, on_macos, scope
    ):
        out = _find_files(home)("target_*", scope=scope)

        assert "target_project.md" in out, out
        assert _guarded_hits(out) == [], out
        assert "target_mobile_documents" not in out, out

    def test_the_walk_never_lists_a_guarded_folder(self, home, on_macos):
        listed = []
        real_scandir = os.scandir

        def spy(path):
            listed.append(Path(path))
            return real_scandir(path)

        with patch("gaia.agents.tools.filesystem_tools.os.scandir", spy):
            _find_files(home)("target_*", scope="smart")

        assert not {home / rel for rel in _GUARDED} & set(listed), listed

    def test_the_result_says_what_was_skipped_and_why(self, home, on_macos):
        out = _find_files(home)("target_*", scope="smart")

        assert out.splitlines()[-1] == (
            "Skipped ~/Desktop, ~/Documents, ~/Downloads, "
            "~/Library/Mobile Documents, ~/Music, "
            "~/Pictures/Photos Library.photoslibrary: macOS asks for permission "
            "before GAIA can read them; ask the user to name the folder to "
            "search it."
        ), out

    def test_a_miss_still_carries_the_note(self, home, on_macos):
        out = _find_files(home)("nothing_matches_this", scope="smart")

        assert out.startswith("No files found"), out
        assert "Skipped ~/" in out, out

    def test_a_named_folder_is_searched(self, home, on_macos):
        out = _find_files(home)("target_*", scope=str(home / "Documents"))

        assert "target_documents.md" in out, out
        assert "Skipped" not in out, out

    def test_an_allowed_folder_is_searched(self, home, on_macos):
        out = _find_files(home, home / "Documents")("target_*", scope="smart")

        assert "target_documents.md" in out, out
        assert "~/Documents" not in out, out


class TestOtherPlatformsAreUnchanged:
    def test_linux_smart_scope_still_searches_documents(self, home, on_linux):
        out = _find_files(home)("target_*", scope="smart")

        assert sorted(_guarded_hits(out)) == ["desktop", "documents", "downloads"]
        assert "Skipped" not in out, out

    def test_linux_search_file_still_searches_documents(self, home, on_linux):
        result = _file_search_tool("search_file", home)("target")

        names = {Path(f).name for f in result["files"]}
        assert "target_documents.md" in names, result
        assert "skipped_folders" not in result


class TestSearchFileOnMacOS:
    def test_an_undirected_search_skips_the_guarded_folders(self, home, on_macos):
        result = _file_search_tool("search_file", home)("target")

        assert [Path(f).name for f in result["files"]] == ["target_project.md"]
        assert result["skipped_folders"].startswith("Skipped ~/"), result

    def test_deep_search_skips_them_too(self, home, on_macos):
        walked = []
        real_iterdir = Path.iterdir

        def spy(self):
            walked.append(self)
            return real_iterdir(self)

        with patch.object(Path, "iterdir", spy):
            result = _file_search_tool("search_file", home)(
                "nothing_matches_this", deep_search=True
            )

        assert all(home in p.parents or p == home for p in walked), walked
        assert not {home / rel for rel in _GUARDED} & set(walked)

        assert not {str(home / rel) for rel in _GUARDED} & set(
            result.get("searched_paths", [])
        )
        assert "skipped_folders" in result, result

    def test_a_named_directory_is_searched(self, home, on_macos):
        result = _file_search_tool("search_file", home)(
            "target", directory=str(home / "Downloads")
        )

        assert [Path(f).name for f in result["files"]] == ["target_downloads.md"]
        assert "skipped_folders" not in result


class TestSearchDirectoryOnMacOS:
    def test_an_undirected_search_skips_the_guarded_folders(self, home, on_macos):
        (home / "Documents" / "reports").mkdir()
        (home / "code" / "proj" / "reports").mkdir()

        result = _file_search_tool("search_directory", home)("reports")

        # The project is reached from both roots; what matters is Documents is not.
        assert set(result["directories"]) == {str(home / "code" / "proj" / "reports")}
        assert "skipped_folders" in result, result
