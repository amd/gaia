# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's databases open while another process holds them (two agents, --attempts)."""

import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from gaia.utils.sqlite_wal import enable_wal


def _writer_holding(path: Path, mode: str) -> sqlite3.Connection:
    """A connection mid-write on *path*, as a second process creating its schema."""
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.execute(f"PRAGMA journal_mode={mode}")
    conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO t VALUES (1)")
    return conn


class _Conn:
    """A connection whose WAL switch hits another opener's lock."""

    def __init__(self, error: str, mode: str):
        self.error, self.mode, self.statements = error, mode, []

    def execute(self, sql):
        self.statements.append(sql)
        if sql == "PRAGMA journal_mode=WAL":
            raise sqlite3.OperationalError(self.error)
        return _Row((self.mode,))


class _Row:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def test_a_switch_locked_by_an_opener_that_already_switched_goes_on():
    conn = _Conn("database is locked", "wal")
    enable_wal(conn)
    assert conn.statements == ["PRAGMA journal_mode=WAL", "PRAGMA journal_mode"]


@pytest.mark.parametrize("mode", ["delete", "truncate", "memory"])
def test_a_switch_locked_on_a_database_not_in_wal_raises_naming_the_mode(mode):
    with pytest.raises(sqlite3.OperationalError, match=f"in {mode} mode, not WAL"):
        enable_wal(_Conn("database is locked", mode))


@pytest.mark.parametrize("error", ["disk I/O error", "unable to open database file"])
def test_other_failures_are_raised_unchanged(error):
    conn = _Conn(error, "wal")
    with pytest.raises(sqlite3.OperationalError, match=f"^{error}$"):
        enable_wal(conn)
    assert conn.statements == ["PRAGMA journal_mode=WAL"]


def test_a_locked_database_not_in_wal_mode_still_fails_and_says_why(tmp_path):
    db = tmp_path / "m.db"
    holder = _writer_holding(db, "DELETE")
    try:
        conn = sqlite3.connect(str(db), timeout=0.2)
        with pytest.raises(sqlite3.OperationalError) as caught:
            enable_wal(conn)
        assert "locked" in str(caught.value)
    finally:
        holder.execute("ROLLBACK")


def test_a_file_that_is_not_a_database_is_still_reported_as_such(tmp_path):
    bad = tmp_path / "not.db"
    bad.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(sqlite3.DatabaseError, match="not a database"):
        enable_wal(sqlite3.connect(str(bad)))


def test_a_fresh_database_is_switched(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "new.db"))
    enable_wal(conn)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_memory_store_opens_while_another_process_creates_its_schema(tmp_path):
    from gaia.agents.base.memory_store import MemoryStore

    db = tmp_path / "memory.db"
    holder = _writer_holding(db, "WAL")
    # The other process finishes its write a moment later.
    release = threading.Timer(0.5, lambda: holder.execute("COMMIT"))
    release.start()
    try:
        store = MemoryStore(db)
        store.close()
    finally:
        release.join()


def test_file_index_does_not_rebuild_a_database_that_is_only_locked(tmp_path):
    from gaia.filesystem.index import FileSystemIndexService

    db = tmp_path / "file_index.db"
    holder = _writer_holding(db, "WAL")
    release = threading.Timer(0.5, lambda: holder.execute("COMMIT"))
    release.start()
    try:
        service = FileSystemIndexService(str(db))
        assert service.rebuilt_from is None
        service.close_db()
    finally:
        release.join()
    assert not list(tmp_path.glob("file_index.db.*corrupt*"))


_OPEN_AT = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    start = float(sys.argv[2])
    while time.time() < start:
        pass
    from gaia.agents.base.memory_store import MemoryStore
    MemoryStore(Path(sys.argv[1])).close()
    print("ok")
    """)


def test_several_processes_opening_one_new_memory_db_all_succeed(tmp_path):
    """What --attempts does: N agents start at once on a memory DB none has made."""
    src = str(Path(__file__).resolve().parents[2] / "src")
    for trial in range(3):
        db = tmp_path / f"memory-{trial}.db"
        start = time.time() + 3
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", _OPEN_AT, str(db), str(start)],
                env={**__import__("os").environ, "PYTHONPATH": src},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(6)
        ]
        results = [p.communicate(timeout=120) for p in procs]
        failed = [err for out, err in results if out.strip() != "ok"]
        assert not failed, failed[0]
