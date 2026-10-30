# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's databases open while another process holds them (two agents, --attempts)."""

import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from gaia.utils import sqlite_wal
from gaia.utils.sqlite_wal import enable_wal


class _Clock:
    """Time that moves only when enable_wal sleeps, so retries never race the host."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(sqlite_wal, "time", fake)
    return fake


def _writer_holding(path: Path, mode: str) -> sqlite3.Connection:
    """A connection mid-write on *path*, as a second process creating its schema."""
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.execute(f"PRAGMA journal_mode={mode}")
    conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("INSERT INTO t VALUES (1)")
    return conn


class _Conn:
    """A connection whose WAL switch hits another opener's lock.

    *modes* is what ``PRAGMA journal_mode`` reads after each locked switch;
    after *switch_fails* locked attempts the switch itself succeeds.
    """

    def __init__(self, error, modes, switch_fails=10**9, busy_ms=200, wait=None):
        self.error, self.modes = error, list(modes)
        self.switch_fails, self.busy_ms, self.statements = switch_fails, busy_ms, []
        self.wait = wait  # called with the seconds a locked statement blocks

    def _blocked(self):
        if self.wait:
            self.wait(self.busy_ms / 1000)

    def execute(self, sql):
        self.statements.append(sql)
        if sql == "PRAGMA busy_timeout":
            return _Row((self.busy_ms,))
        if sql.startswith("PRAGMA busy_timeout="):
            self.busy_ms = int(sql.split("=")[1])
            return _Row((self.busy_ms,))
        if sql == "PRAGMA database_list":
            return _Row((0, "main", "/data/m.db"))
        if sql == "PRAGMA journal_mode=WAL":
            if self.switch_fails > 0:
                self.switch_fails -= 1
                self._blocked()
                raise sqlite3.OperationalError(self.error)
            return _Row(("wal",))
        mode = self.modes.pop(0) if len(self.modes) > 1 else self.modes[0]
        if isinstance(mode, Exception):
            self._blocked()
            raise mode
        return _Row((mode,))

    def switches(self):
        return self.statements.count("PRAGMA journal_mode=WAL")


class _Row:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def test_a_switch_locked_by_an_opener_that_already_switched_goes_on(clock):
    conn = _Conn("database is locked", ["wal"])
    enable_wal(conn)
    assert conn.switches() == 1


def test_a_switch_locked_while_the_other_opener_is_still_switching_waits_for_it(clock):
    # The race on Windows: the loser reads "delete" before the winner's switch lands.
    conn = _Conn("database is locked", ["delete", "delete", "delete", "wal"])
    enable_wal(conn)
    assert conn.switches() == 4


def test_a_switch_that_frees_up_is_retried_until_it_succeeds(clock):
    conn = _Conn("database is locked", ["delete"], switch_fails=3)
    enable_wal(conn)
    assert conn.switches() == 4


def test_a_locked_mode_read_is_retried_too(clock):
    locked = sqlite3.OperationalError("database is locked")
    conn = _Conn("database is locked", [locked, locked, "wal"])
    enable_wal(conn)
    assert conn.switches() == 3


@pytest.mark.parametrize("mode", ["delete", "truncate", "memory"])
def test_a_switch_locked_on_a_database_not_in_wal_raises_naming_the_mode(mode, clock):
    with pytest.raises(sqlite3.OperationalError, match=f"still in {mode} mode"):
        enable_wal(_Conn("database is locked", [mode], busy_ms=50))


def test_the_retry_stops_at_the_busy_timeout(clock):
    conn = _Conn("database is locked", ["delete"], busy_ms=150)
    with pytest.raises(sqlite3.OperationalError, match=r"after 0\.\d s"):
        enable_wal(conn)
    # The last sleep can start just before the deadline, and is at most 25 ms.
    assert 0.15 <= clock.now < 0.175
    assert conn.switches() > 1


def test_a_zero_busy_timeout_tries_once(clock):
    conn = _Conn("database is locked", ["delete"], busy_ms=0)
    with pytest.raises(sqlite3.OperationalError, match=r"\(1 attempt\)"):
        enable_wal(conn)
    assert conn.switches() == 1


def test_a_blocked_switch_and_mode_read_wait_the_busy_timeout_once(clock):
    locked = sqlite3.OperationalError("database is locked")
    conn = _Conn("database is locked", [locked], busy_ms=5000, wait=clock.sleep)
    with pytest.raises(sqlite3.OperationalError, match="after 5.0 s") as caught:
        enable_wal(conn)
    assert clock.now == 5.0
    assert "unknown (locked) mode" in str(caught.value)


def test_the_error_names_the_database_file_and_counts_attempts(clock):
    conn = _Conn("database is locked", ["delete"], busy_ms=1000, wait=clock.sleep)
    with pytest.raises(sqlite3.OperationalError) as caught:
        enable_wal(conn)
    assert "could not switch /data/m.db to WAL mode" in str(caught.value)
    assert "(1 attempt)" in str(caught.value)


def test_the_busy_timeout_is_restored_after_a_failure_and_a_success(clock):
    failing = _Conn("database is locked", ["delete"], busy_ms=50)
    with pytest.raises(sqlite3.OperationalError):
        enable_wal(failing)
    assert failing.busy_ms == 50

    working = _Conn("database is locked", ["wal"], busy_ms=50)
    enable_wal(working)
    assert working.busy_ms == 50


def test_a_zero_busy_timeout_still_sees_an_opener_that_already_switched(clock):
    enable_wal(_Conn("database is locked", ["wal"], busy_ms=0, wait=clock.sleep))


class _LateBlocker(_Conn):
    """Fails the first switches at once, then blocks each statement its full busy timeout."""

    def __init__(self, instant_fails, **kwargs):
        super().__init__("database is locked", ["delete"], **kwargs)
        self.instant_fails = instant_fails

    def _blocked(self):
        if self.instant_fails > 0:
            self.instant_fails -= 1
            return
        super()._blocked()


@pytest.mark.parametrize("instant_fails", [1, 3, 10])
def test_a_switch_that_blocks_on_a_retry_waits_only_the_time_left(instant_fails, clock):
    conn = _LateBlocker(instant_fails, busy_ms=1000, wait=clock.sleep)
    with pytest.raises(sqlite3.OperationalError, match="still in delete mode"):
        enable_wal(conn)
    # One jitter sleep (max 25 ms) can start just before the deadline.
    assert 1.0 <= clock.now <= 1.025
    assert conn.busy_ms == 1000


@pytest.mark.parametrize("error", ["disk I/O error", "unable to open database file"])
def test_other_failures_are_raised_unchanged(error, clock):
    conn = _Conn(error, ["wal"])
    with pytest.raises(sqlite3.OperationalError, match=f"^{error}$"):
        enable_wal(conn)
    assert conn.switches() == 1


def test_a_non_lock_failure_reading_the_mode_is_raised_unchanged(clock):
    conn = _Conn("database is locked", [sqlite3.OperationalError("disk I/O error")])
    with pytest.raises(sqlite3.OperationalError, match="^disk I/O error$"):
        enable_wal(conn)


def test_a_locked_database_not_in_wal_mode_still_fails_and_says_why(tmp_path):
    db = tmp_path / "m.db"
    holder = _writer_holding(db, "DELETE")
    try:
        conn = sqlite3.connect(str(db), timeout=0.2)
        t0 = time.monotonic()
        with pytest.raises(sqlite3.OperationalError) as caught:
            enable_wal(conn)
        assert time.monotonic() - t0 < 5
        assert "locked" in str(caught.value)
        assert "still in delete mode" in str(caught.value)
        assert str(db) in str(caught.value)
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


@pytest.mark.parametrize("where", ["_SCHEMA_SQL", "_V2_INDEX_SQL"])
def test_a_memory_schema_that_fails_partway_leaves_nothing_behind(
    tmp_path, monkeypatch, where
):
    # All-or-nothing is what makes a second opener wait for one write, not ~40.
    from gaia.agents.base import memory_store

    broken = "CREATE INDEX broken ON no_such_table(x)"
    if where == "_SCHEMA_SQL":
        monkeypatch.setattr(
            memory_store, where, memory_store._SCHEMA_SQL + f"\n{broken};\n"
        )
    else:
        monkeypatch.setattr(memory_store, where, [*memory_store._V2_INDEX_SQL, broken])
    db = tmp_path / "memory.db"
    with pytest.raises(sqlite3.OperationalError, match="no_such_table"):
        memory_store.MemoryStore(db)
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []
    finally:
        conn.close()


def test_an_existing_memory_db_opens_while_another_process_is_writing(tmp_path):
    from gaia.agents.base.memory_store import MemoryStore

    db = tmp_path / "memory.db"
    MemoryStore(db).close()
    holder = sqlite3.connect(str(db), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO meta VALUES ('k', 'v')")
    try:
        # Never released while the store opens: the open must not need the write lock.
        MemoryStore(db).close()
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_a_memory_db_missing_a_trigger_gets_it_back_on_open(tmp_path):
    from gaia.agents.base.memory_store import MemoryStore

    db = tmp_path / "memory.db"
    MemoryStore(db).close()
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TRIGGER conv_fts_ai")
    conn.commit()
    conn.close()

    store = MemoryStore(db)
    store.store_turn("s1", "user", "the quarterly forecast")
    store.close()
    conn = sqlite3.connect(str(db))
    try:
        hits = conn.execute(
            "SELECT rowid FROM conversations_fts WHERE conversations_fts MATCH 'forecast'"
        ).fetchall()
    finally:
        conn.close()
    assert len(hits) == 1


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


_OPENER = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from gaia.agents.base.memory_store import MemoryStore
    print("ready", flush=True)
    for line in sys.stdin:
        path, start = line.rsplit(" ", 1)
        while time.time() < float(start):
            pass
        try:
            MemoryStore(Path(path)).close()
            print("ok", flush=True)
        except Exception as exc:  # reported to the test, which fails on it
            print(f"{type(exc).__name__}: {exc}", flush=True)
    """)


@pytest.mark.parametrize("procs,rounds", [(2, 30), (6, 10)])
def test_several_processes_opening_one_new_memory_db_all_succeed(
    tmp_path, procs, rounds
):
    """What --attempts does: N agents start at once on a memory DB none has made."""
    src = str(Path(__file__).resolve().parents[2] / "src")
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _OPENER],
            env={**os.environ, "PYTHONPATH": src},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(procs)
    ]
    try:
        # Every child has imported before the first round, so they open together.
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        failed = []
        for trial in range(rounds):
            line = f"{tmp_path / f'memory-{trial}.db'} {time.time() + 0.2}\n"
            for child in children:
                child.stdin.write(line)
                child.stdin.flush()
            results = [child.stdout.readline().strip() for child in children]
            failed += [r for r in results if r != "ok"]
    finally:
        for child in children:
            child.stdin.close()
            child.wait(timeout=30)
    assert not failed, f"{len(failed)} of {procs * rounds} opens failed: {failed[0]}"


def test_chat_database_retries_wal_switch_while_another_process_releases(tmp_path):
    """Agent UI chat.db uses the same bounded WAL opener as other stores."""
    from gaia.ui.database import ChatDatabase

    db_path = tmp_path / "chat.db"
    holder = _writer_holding(db_path, "DELETE")
    release = threading.Timer(0.5, lambda: holder.execute("COMMIT"))
    release.start()
    try:
        db = ChatDatabase(str(db_path))
        assert db._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        db.close()
    finally:
        release.join()
        holder.close()
