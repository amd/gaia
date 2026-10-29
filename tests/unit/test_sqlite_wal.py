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
from types import SimpleNamespace

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


def test_a_switch_locked_by_an_opener_that_already_switched_goes_on():
    conn = _Conn("database is locked", ["wal"])
    enable_wal(conn)
    assert conn.switches() == 1


def test_a_switch_locked_while_the_other_opener_is_still_switching_waits_for_it():
    # The race on Windows: the loser reads "delete" before the winner's switch lands.
    conn = _Conn("database is locked", ["delete", "delete", "delete", "wal"])
    enable_wal(conn)
    assert conn.switches() == 4


def test_a_switch_that_frees_up_is_retried_until_it_succeeds():
    conn = _Conn("database is locked", ["delete"], switch_fails=3)
    enable_wal(conn)
    assert conn.switches() == 4


def test_a_locked_mode_read_is_retried_too():
    locked = sqlite3.OperationalError("database is locked")
    conn = _Conn("database is locked", [locked, locked, "wal"])
    enable_wal(conn)
    assert conn.switches() == 3


@pytest.mark.parametrize("mode", ["delete", "truncate", "memory"])
def test_a_switch_locked_on_a_database_not_in_wal_raises_naming_the_mode(mode):
    with pytest.raises(sqlite3.OperationalError, match=f"still in {mode} mode"):
        enable_wal(_Conn("database is locked", [mode], busy_ms=50))


class _FakeClock:
    """Replaces the helper's clock so a blocked statement costs no real time."""

    def __init__(self, monkeypatch):
        self.now = 1000.0
        fake = SimpleNamespace(monotonic=lambda: self.now, sleep=self.advance)
        monkeypatch.setattr("gaia.utils.sqlite_wal.time", fake)

    def advance(self, seconds):
        self.now += seconds


def test_the_retry_stops_at_the_busy_timeout():
    conn = _Conn("database is locked", ["delete"], busy_ms=150)
    t0 = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match=r"after \d\.\d s"):
        enable_wal(conn)
    assert 0.15 <= time.monotonic() - t0 < 2
    assert conn.switches() > 1


def test_a_zero_busy_timeout_tries_once():
    conn = _Conn("database is locked", ["delete"], busy_ms=0)
    with pytest.raises(sqlite3.OperationalError, match=r"\(1 attempt\)"):
        enable_wal(conn)
    assert conn.switches() == 1


def test_a_blocked_switch_and_mode_read_wait_the_busy_timeout_once(monkeypatch):
    clock = _FakeClock(monkeypatch)
    locked = sqlite3.OperationalError("database is locked")
    conn = _Conn("database is locked", [locked], busy_ms=5000, wait=clock.advance)
    with pytest.raises(sqlite3.OperationalError, match="after 5.0 s") as caught:
        enable_wal(conn)
    assert clock.now - 1000.0 == 5.0
    assert "unknown (locked) mode" in str(caught.value)


def test_the_error_names_the_database_file_and_counts_attempts(monkeypatch):
    clock = _FakeClock(monkeypatch)
    conn = _Conn("database is locked", ["delete"], busy_ms=1000, wait=clock.advance)
    with pytest.raises(sqlite3.OperationalError) as caught:
        enable_wal(conn)
    assert "could not switch /data/m.db to WAL mode" in str(caught.value)
    assert "(1 attempt)" in str(caught.value)


def test_the_busy_timeout_is_restored_after_a_failure_and_a_success():
    failing = _Conn("database is locked", ["delete"], busy_ms=50)
    with pytest.raises(sqlite3.OperationalError):
        enable_wal(failing)
    assert failing.busy_ms == 50

    working = _Conn("database is locked", ["wal"], busy_ms=50)
    enable_wal(working)
    assert working.busy_ms == 50


@pytest.mark.parametrize("error", ["disk I/O error", "unable to open database file"])
def test_other_failures_are_raised_unchanged(error):
    conn = _Conn(error, ["wal"])
    with pytest.raises(sqlite3.OperationalError, match=f"^{error}$"):
        enable_wal(conn)
    assert conn.switches() == 1


def test_a_non_lock_failure_reading_the_mode_is_raised_unchanged():
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
