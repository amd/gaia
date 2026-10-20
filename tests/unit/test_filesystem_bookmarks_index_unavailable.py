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


def _coded(cls, message, code):
    exc = cls(message)
    exc.sqlite_errorcode = code
    return exc


@pytest.mark.parametrize(
    "exc,expect,never",
    [
        (_coded(sqlite3.OperationalError, "database is locked", 5), "try again", None),
        (_coded(sqlite3.OperationalError, "locked", 6), "try again", None),
        (
            _coded(sqlite3.DatabaseError, "database disk image is malformed", 11),
            "damaged",
            "try again",
        ),
        (
            _coded(sqlite3.OperationalError, "unable to open database file", 14),
            "read and write",
            "try again",
        ),
        (
            _coded(sqlite3.OperationalError, "attempt to write a readonly db", 8),
            "read and write",
            "try again",
        ),
        (PermissionError("access denied"), "read and write", "try again"),
    ],
    ids=["busy", "locked", "corrupt", "cantopen", "readonly", "oserror"],
)
def test_the_advice_matches_why_the_index_wont_open(
    monkeypatch, saved, exc, expect, never
):
    db_path, _ = saved

    def fails(*_a, **_k):
        raise exc

    monkeypatch.setattr(index_mod, "FileSystemIndexService", fails)
    _, bookmark = _agent(_configured(db_path))

    result = bookmark(action="list")

    assert result.startswith("Error:")
    assert str(db_path) in result
    assert expect in result
    if never:
        assert never not in result


def test_concurrent_calls_open_the_index_once(monkeypatch, saved):
    import threading
    import time

    db_path, _ = saved
    opened = []
    real = FileSystemIndexService

    def slow_open(*args, **kwargs):
        time.sleep(0.2)
        service = real(*args, **kwargs)
        opened.append(service)
        return service

    monkeypatch.setattr(index_mod, "FileSystemIndexService", slow_open)
    agent, _ = _agent(_configured(db_path))

    threads = [threading.Thread(target=agent._bookmark_index) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    try:
        assert len(opened) == 1
    finally:
        for service in opened:
            service.close_db()


def test_the_first_bookmark_result_says_the_index_was_rebuilt(tmp_path):
    db_path = tmp_path / "file_index.db"
    db_path.write_bytes(b"this is not a sqlite database" * 100)
    agent, bookmark = _agent(_configured(db_path))

    first = bookmark(action="list")
    second = bookmark(action="list")
    agent._fs_index.close_db()

    assert first.startswith("Note: the file index was damaged")
    assert "file_index.db.corrupt-" in first
    assert "Note:" not in second


def test_the_open_lock_exists_for_any_object_the_tools_bind_to():
    from gaia.agents.tools.filesystem_tools import _open_lock_for

    bare = SimpleNamespace()

    assert _open_lock_for(bare) is _open_lock_for(bare)


@pytest.mark.parametrize(
    "code,kind",
    [
        (517, "busy"),  # SQLITE_BUSY_SNAPSHOT
        (262, "busy"),  # SQLITE_LOCKED_SHAREDCACHE
        (267, "corrupt"),  # SQLITE_CORRUPT_VTAB
        (526, "access"),  # SQLITE_CANTOPEN_ISDIR
        (1032, "access"),  # SQLITE_READONLY_DBMOVED
        (522, "other"),  # SQLITE_IOERR_SHORT_READ
    ],
)
def test_extended_codes_are_classified_by_their_primary_code(code, kind):
    from gaia.agents.tools.filesystem_tools import _sqlite_kind

    assert _sqlite_kind(_coded(sqlite3.OperationalError, "x", code)) == kind
