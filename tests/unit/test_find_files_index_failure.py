# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A failing file index must be reported, not silently swapped for a disk walk.

``find_files`` caught any exception from the index, logged it at debug and
walked the disk, so a locked or damaged index looked like a slow search with
nothing in the result or the visible logs saying why (#6678).

No LLM or external service required.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.filesystem.index import FileSystemIndexService


class _Sandbox:
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


def _find_files(root, index):
    class MockAgent(FileSystemToolsMixin):
        def __init__(self):
            self._web_client = None
            self._path_validator = _Sandbox(root)
            self._fs_index = index
            self._tools = {}
            self._bookmarks = {}

    registered = {}

    def mock_tool(atomic=True):
        def decorator(func):
            registered[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", mock_tool):
        MockAgent().register_filesystem_tools()
    return registered["find_files"]


def _failing_index(exc):
    index = MagicMock()
    index.query_files.side_effect = exc
    return index


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "quarterly_report.txt").write_text("numbers\n")
    return root


class TestIndexFailureIsReported:
    def test_a_locked_index_is_named_in_the_result(self, project, caplog):
        find_files = _find_files(
            project, _failing_index(sqlite3.OperationalError("database is locked"))
        )

        with caplog.at_level(logging.WARNING):
            out = find_files("quarterly", scope=str(project))

        assert "quarterly_report.txt" in out, out
        assert "file index failed" in out
        assert "database is locked" in out
        assert "another GAIA process" in out
        assert any(
            r.levelno == logging.WARNING and "database is locked" in r.getMessage()
            for r in caplog.records
        )

    def test_a_damaged_index_says_it_is_damaged(self, project):
        find_files = _find_files(
            project, _failing_index(sqlite3.DatabaseError("file is not a database"))
        )

        out = find_files("quarterly", scope=str(project))

        assert "quarterly_report.txt" in out, out
        assert "is damaged" in out

    def test_the_note_survives_an_empty_walk(self, project):
        find_files = _find_files(
            project, _failing_index(sqlite3.OperationalError("disk I/O error"))
        )

        out = find_files("nothing_like_this", scope=str(project))

        assert "No files found" in out
        assert "file index failed" in out
        assert "disk I/O error" in out

    def test_an_unexpected_index_error_is_not_walked_past(self, project):
        find_files = _find_files(project, _failing_index(KeyError("path")))

        out = find_files("quarterly", scope=str(project))

        assert out.startswith("Error searching files"), out
        assert "quarterly_report.txt" not in out

    def test_a_healthy_index_adds_no_note(self, project):
        index = MagicMock()
        index.query_files.return_value = []

        out = _find_files(project, index)("quarterly", scope=str(project))

        assert "quarterly_report.txt" in out, out
        assert "file index" not in out


class TestScanPrefixIsAFolderBoundary:
    """``docs`` must not claim ``docs2``'s files as its own."""

    @pytest.fixture
    def index(self, tmp_path):
        service = FileSystemIndexService(db_path=str(tmp_path / "index.db"))
        yield service
        service.close_db()

    def test_rescanning_a_folder_keeps_a_sibling_sharing_its_prefix(
        self, tmp_path, index
    ):
        docs = tmp_path / "docs"
        docs2 = tmp_path / "docs2"
        docs.mkdir()
        docs2.mkdir()
        (docs / "a.txt").write_text("a")
        (docs2 / "b.txt").write_text("b")

        index.scan_directory(str(docs2))
        index.scan_directory(str(docs))
        stats = index.scan_directory(str(docs), incremental=True)

        assert stats["files_removed"] == 0
        assert index.query_files(name="b")

    def test_directory_stats_count_only_the_folder_itself(self, tmp_path, index):
        docs = tmp_path / "docs"
        docs2 = tmp_path / "docs2"
        docs.mkdir()
        docs2.mkdir()
        (docs / "a.txt").write_text("a")
        (docs2 / "b.txt").write_text("b")
        (docs2 / "c.txt").write_text("c")

        index.scan_directory(str(docs2))
        index.scan_directory(str(docs), incremental=False)

        assert index.get_directory_stats(str(docs))["file_count"] == 1

    def test_an_underscore_in_the_folder_is_not_a_wildcard(self, tmp_path, index):
        mine = tmp_path / "my_docs"
        other = tmp_path / "myXdocs"
        mine.mkdir()
        other.mkdir()
        (mine / "a.txt").write_text("a")
        (other / "b.txt").write_text("b")

        index.scan_directory(str(other))
        index.scan_directory(str(mine))

        assert index.query_files(name="b")
