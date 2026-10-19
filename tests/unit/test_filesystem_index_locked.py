# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A locked or briefly unreadable file index is not a corrupt one.

``file_index.db`` used to be deleted on *any* error opening it, including
``sqlite3.OperationalError`` ("database is locked" from another GAIA process,
"disk I/O error" from an AV scan or backup holding the file). That wiped the
user's bookmarks along with the index. Only ``sqlite3.DatabaseError`` (not a
database / malformed) means corrupt.
"""

from __future__ import annotations

import sqlite3

import pytest

from gaia.filesystem.index import FileSystemIndexService


@pytest.fixture
def index_with_bookmark(tmp_path):
    db_path = tmp_path / "file_index.db"
    service = FileSystemIndexService(db_path=str(db_path))
    service.add_bookmark(str(tmp_path), label="keep me")
    service.close_db()
    return db_path


def _bookmarks(db_path):
    service = FileSystemIndexService(db_path=str(db_path))
    try:
        return [b["label"] for b in service.list_bookmarks()]
    finally:
        service.close_db()


class _FailsOnce:
    """A connection whose next statement matching ``prefix`` raises, once."""

    def __init__(self, conn, prefix, pending):
        self._conn = conn
        self._prefix = prefix
        self._pending = pending

    def execute(self, sql, *args):
        if self._pending and sql.startswith(self._prefix):
            raise self._pending.pop()
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _fail_first(monkeypatch, prefix, error):
    real_init = FileSystemIndexService.init_db
    pending = [error]

    def init_db(self, path=":memory:", **kwargs):
        real_init(self, path, **kwargs)
        self._db = _FailsOnce(self._db, prefix, pending)

    monkeypatch.setattr(FileSystemIndexService, "init_db", init_db)


@pytest.mark.parametrize("message", ["database is locked", "disk I/O error"])
def test_operational_error_on_open_keeps_the_index(
    monkeypatch, index_with_bookmark, message
):
    _fail_first(monkeypatch, "PRAGMA journal_mode", sqlite3.OperationalError(message))

    with pytest.raises(sqlite3.OperationalError, match=message):
        FileSystemIndexService(db_path=str(index_with_bookmark))

    monkeypatch.undo()
    assert _bookmarks(index_with_bookmark) == ["keep me"]


@pytest.mark.parametrize("prefix", ["PRAGMA journal_mode", "PRAGMA integrity_check"])
def test_a_lock_error_closes_the_connection_before_raising(
    monkeypatch, index_with_bookmark, prefix
):
    closed = []
    real_close = FileSystemIndexService.close_db

    def close_db(self):
        closed.append(True)
        real_close(self)

    _fail_first(monkeypatch, prefix, sqlite3.OperationalError("database is locked"))
    monkeypatch.setattr(FileSystemIndexService, "close_db", close_db)

    with pytest.raises(sqlite3.OperationalError):
        FileSystemIndexService(db_path=str(index_with_bookmark))

    assert closed == [True]


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.ProgrammingError("closed database"),
        sqlite3.InternalError("internal"),
    ],
    ids=["programming", "internal"],
)
def test_a_non_corruption_database_error_keeps_the_index(
    monkeypatch, index_with_bookmark, error
):
    _fail_first(monkeypatch, "PRAGMA integrity_check", error)

    with pytest.raises(type(error)):
        FileSystemIndexService(db_path=str(index_with_bookmark))

    monkeypatch.undo()
    assert _bookmarks(index_with_bookmark) == ["keep me"]


@pytest.mark.parametrize("message", ["database is locked", "disk I/O error"])
def test_operational_error_in_integrity_check_keeps_the_index(
    monkeypatch, index_with_bookmark, message
):
    _fail_first(
        monkeypatch, "PRAGMA integrity_check", sqlite3.OperationalError(message)
    )

    with pytest.raises(sqlite3.OperationalError, match=message):
        FileSystemIndexService(db_path=str(index_with_bookmark))

    monkeypatch.undo()
    assert _bookmarks(index_with_bookmark) == ["keep me"]


def test_a_garbage_file_is_still_rebuilt(tmp_path):
    db_path = tmp_path / "file_index.db"
    db_path.write_bytes(b"this is not a sqlite database" * 100)

    service = FileSystemIndexService(db_path=str(db_path))
    try:
        assert service.list_bookmarks() == []
        assert service._check_integrity() is True
    finally:
        service.close_db()


def test_malformed_integrity_result_is_still_rebuilt(monkeypatch, index_with_bookmark):
    _fail_first(
        monkeypatch,
        "PRAGMA integrity_check",
        sqlite3.DatabaseError("database disk image is malformed"),
    )

    service = FileSystemIndexService(db_path=str(index_with_bookmark))
    try:
        assert service.list_bookmarks() == []
    finally:
        service.close_db()


def test_a_real_exclusive_lock_raises_and_keeps_the_index(index_with_bookmark):
    # Proves a real lock reaches the raise path. It reproduces the old deletion
    # only on POSIX: on Windows the open handle made the unlink fail anyway.
    holder = sqlite3.connect(str(index_with_bookmark))
    holder.execute("PRAGMA journal_mode=DELETE")
    holder.execute("PRAGMA locking_mode=EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            FileSystemIndexService(db_path=str(index_with_bookmark))
    finally:
        holder.rollback()
        holder.close()

    assert _bookmarks(index_with_bookmark) == ["keep me"]
