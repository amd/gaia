# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Bookmarks must not silently move to memory when the index won't open.

If the file index was locked when the agent started, the bookmark tool used
the in-memory dict for the whole (cached) session. "list" then said "No
bookmarks saved yet" while they sat on disk, and "add" said "Bookmarked" but
the bookmark was gone when the session ended. Now the tool re-opens the
index on each call and reports an actionable error while it can't.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.filesystem import index as index_mod
from gaia.filesystem.index import FileSystemIndexService


def _agent(config):
    class Agent(FileSystemToolsMixin):
        def __init__(self):
            self._path_validator = None
            self._fs_index = None  # what ChatAgent leaves after a failed open
            self._bookmarks = {}
            self.config = config

    tools = {}

    def fake_tool(atomic=True):
        def decorator(func):
            tools[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", fake_tool):
        agent = Agent()
        agent.register_filesystem_tools()
    return agent, tools["bookmark"]


@pytest.fixture
def saved(tmp_path):
    """An on-disk index holding one bookmark; returns (db_path, bookmarked dir)."""
    db_path = tmp_path / "file_index.db"
    target = tmp_path / "project"
    target.mkdir()
    service = FileSystemIndexService(db_path=str(db_path))
    service.add_bookmark(str(target), label="project")
    service.close_db()
    return db_path, target


@pytest.fixture
def locked(monkeypatch):
    """Make opening the index fail with a lock error until ``release()``."""
    state = {"locked": True}
    real = FileSystemIndexService

    def open_index(*args, **kwargs):
        if state["locked"]:
            raise sqlite3.OperationalError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(index_mod, "FileSystemIndexService", open_index)
    return lambda: state.update(locked=False)


def _configured(db_path):
    return SimpleNamespace(enable_filesystem=True, filesystem_index_path=str(db_path))


def test_list_while_locked_is_an_error_not_an_empty_list(saved, locked):
    db_path, _ = saved
    _, bookmark = _agent(_configured(db_path))

    result = bookmark(action="list")

    assert result.startswith("Error:")
    assert "No bookmarks saved yet" not in result
    assert "database is locked" in result


def test_add_while_locked_saves_nothing_anywhere(saved, locked, tmp_path):
    db_path, _ = saved
    agent, bookmark = _agent(_configured(db_path))
    other = tmp_path / "other"
    other.mkdir()

    result = bookmark(action="add", path=str(other), label="other")

    assert result.startswith("Error:")
    assert "Bookmarked" not in result
    assert agent._bookmarks == {}


def test_the_index_is_reopened_once_the_lock_clears(saved, locked, tmp_path):
    db_path, target = saved
    agent, bookmark = _agent(_configured(db_path))
    assert bookmark(action="list").startswith("Error:")

    locked()
    other = tmp_path / "other"
    other.mkdir()

    assert "project" in bookmark(action="list")
    assert "Bookmarked" in bookmark(action="add", path=str(other), label="other")
    agent._fs_index.close_db()

    reopened = FileSystemIndexService(db_path=str(db_path))
    try:
        labels = {b["label"] for b in reopened.list_bookmarks()}
    finally:
        reopened.close_db()
    assert labels == {"project", "other"}


@pytest.mark.parametrize(
    "config",
    [None, SimpleNamespace(enable_filesystem=False, filesystem_index_path="x")],
    ids=["no-config", "index-disabled"],
)
def test_an_agent_without_an_index_keeps_bookmarks_in_memory(config, tmp_path):
    agent, bookmark = _agent(config)

    assert "Bookmarked" in bookmark(action="add", path=str(tmp_path), label="t")
    assert str(tmp_path.resolve()) in agent._bookmarks
    assert '"t"' in bookmark(action="list")
