# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Two processes opening the same new transcript must both succeed."""

import os
import pathlib
import sqlite3
from unittest.mock import patch

import pytest

from gaia.agents.base.history import SessionHistory


def test_a_file_created_by_another_process_first_does_not_raise(tmp_path):
    path = tmp_path / "transcript.db"
    path.touch()  # the other process won the create

    # The other process created the file after this one looked and found none.
    with patch.object(pathlib.Path, "exists", return_value=False):
        history = SessionHistory(path)

    history.append({"role": "user", "content": "hi"})
    assert path.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_a_new_transcript_is_private(tmp_path):
    path = tmp_path / "new.db"

    SessionHistory(path)

    assert path.stat().st_mode & 0o077 == 0


def test_two_opens_of_a_new_transcript_can_both_append(tmp_path):
    path = tmp_path / "shared.db"

    first, second = SessionHistory(path), SessionHistory(path)
    first.begin([{"role": "user", "content": "one"}])
    second.begin([{"role": "user", "content": "two"}])
    first.record([{"role": "user", "content": "one"}])
    second.record([{"role": "user", "content": "two"}])

    turns = SessionHistory(path).turns(agent=None)
    assert [t[0]["content"] for t in turns] == ["one", "two"]


def _legacy_archive(path):
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, messages TEXT NOT NULL)")
    db.commit()
    db.close()


def test_an_old_archive_gains_the_owner_column(tmp_path):
    path = tmp_path / "old.db"
    _legacy_archive(path)

    SessionHistory(path).begin([{"role": "user", "content": "hi"}])

    db = sqlite3.connect(str(path))
    columns = {row[1] for row in db.execute("PRAGMA table_info(turns)")}
    db.close()
    assert "owner" in columns


class _StalePragma:
    """A connection whose first table_info read predates another process's
    migration of the same archive."""

    def __init__(self, real):
        self._real = real
        self._stale = True

    def execute(self, sql, *args):
        if self._stale and sql.startswith("PRAGMA table_info"):
            self._stale = False
            return iter([(0, "id"), (1, "messages")])
        return self._real.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_losing_the_migration_race_to_another_process_does_not_raise(tmp_path):
    path = tmp_path / "old.db"
    _legacy_archive(path)
    SessionHistory(path)  # the other process migrates first

    real_connect = sqlite3.connect
    with patch.object(
        sqlite3, "connect", lambda *a, **k: _StalePragma(real_connect(*a, **k))
    ):
        SessionHistory(path)

    SessionHistory(path).begin([{"role": "user", "content": "hi"}])


def test_other_migration_errors_still_raise(tmp_path):
    path = tmp_path / "old.db"
    _legacy_archive(path)

    class _Locked(_StalePragma):
        def execute(self, sql, *args):
            if sql.startswith("ALTER TABLE"):
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args)

    real_connect = sqlite3.connect
    with patch.object(
        sqlite3, "connect", lambda *a, **k: _Locked(real_connect(*a, **k))
    ):
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            SessionHistory(path)
