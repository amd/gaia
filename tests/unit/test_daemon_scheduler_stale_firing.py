# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A job left in ``firing`` by a crash or a locked DB must not stay stuck.

``claim_job`` moves a row ``pending -> firing`` before its executor runs. If the
process dies, or the DB is locked when the outcome is written back, nothing ever
moved the row on, so a recurring job never fired again. Each pass now re-queues
a recurring row whose claim has gone stale, and fails a stale one-shot row (it
may already have taken effect, so it is not run a second time).
"""

from __future__ import annotations

import sqlite3

import pytest

from gaia.daemon.scheduler import store
from gaia.daemon.scheduler.clock import DaemonClock, _ClockDB
from gaia.daemon.scheduler.models import (
    KIND_ONE_SHOT,
    KIND_RECURRING,
    STATUS_FAILED,
    STATUS_FIRING,
    STATUS_PENDING,
)

STALE = 600.0


def _db(path):
    db = _ClockDB()
    db.init_db(str(path))
    store.init_schema(db)
    return db


def test_recurring_job_whose_outcome_write_failed_fires_again(tmp_path, monkeypatch):
    path = tmp_path / "clock.db"
    db = _db(path)
    jid = store.register_job(
        db, source="t", kind=KIND_RECURRING, fire_at=100.0, interval_seconds=60
    )
    calls = []
    clock = DaemonClock(
        str(path),
        executors={KIND_RECURRING: lambda job, _db: calls.append(job["job_id"])},
        stale_firing_seconds=STALE,
    )

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "mark_fired", locked)
    with pytest.raises(sqlite3.OperationalError):
        clock.fire_due(now=100.0)
    monkeypatch.undo()
    assert store.get_job(_db(path), job_id=jid)["status"] == STATUS_FIRING

    # Within the claim window the row still belongs to whoever claimed it.
    assert clock.fire_due(now=100.0 + STALE - 1) == {"fired": [], "failed": []}
    assert calls == [jid]

    # Past it, the stuck row is re-queued and fires on the same pass.
    assert clock.fire_due(now=100.0 + STALE + 1) == {"fired": [jid], "failed": []}
    assert calls == [jid, jid]
    job = store.get_job(_db(path), job_id=jid)
    assert job["status"] == STATUS_PENDING
    assert job["error"] is None


def test_one_shot_job_left_firing_by_a_crash_fails_loudly_not_rerun(tmp_path):
    path = tmp_path / "clock.db"
    db = _db(path)
    jid = store.register_job(db, source="t", kind=KIND_ONE_SHOT, fire_at=100.0)
    # A previous daemon claimed the job and died before writing the outcome.
    assert store.claim_job(db, job_id=jid, now=100.0) is True

    calls = []
    clock = DaemonClock(
        str(path),
        executors={KIND_ONE_SHOT: lambda job, _db: calls.append(job)},
        stale_firing_seconds=STALE,
    )
    assert clock.fire_due(now=100.0 + STALE + 1) == {"fired": [], "failed": [jid]}
    assert calls == []
    job = store.get_job(_db(path), job_id=jid)
    assert job["status"] == STATUS_FAILED
    assert "interrupted while firing" in job["error"]


def test_row_claimed_before_claim_times_were_recorded_is_recovered(tmp_path):
    """A row stuck by an older daemon has no claim time; it is stale on sight."""
    path = tmp_path / "clock.db"
    db = _db(path)
    jid = store.register_job(
        db, source="t", kind=KIND_RECURRING, fire_at=100.0, interval_seconds=60
    )
    db.update(
        "daemon_jobs",
        {"status": STATUS_FIRING, "claimed_at": None},
        "job_id = :id",
        {"id": jid},
    )
    clock = DaemonClock(
        str(path),
        executors={KIND_RECURRING: lambda job, _db: None},
        stale_firing_seconds=STALE,
    )
    assert clock.fire_due(now=200.0) == {"fired": [jid], "failed": []}


def test_store_created_before_the_claim_column_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    db = _ClockDB()
    db.init_db(str(path))
    db.execute(
        "CREATE TABLE daemon_jobs (job_id TEXT PRIMARY KEY, source TEXT NOT NULL,"
        " source_job_id TEXT, kind TEXT NOT NULL, fire_at REAL,"
        " interval_seconds INTEGER, payload_json TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending', error TEXT,"
        " created_at REAL NOT NULL, fired_at REAL,"
        " run_count INTEGER NOT NULL DEFAULT 0)"
    )
    db.execute(
        "INSERT INTO daemon_jobs (job_id, source, kind, fire_at, payload_json,"
        " status, created_at) VALUES ('old', 't', 'one_shot', 1, '{}', 'firing', 0)"
    )
    db.close_db()

    clock = DaemonClock(str(path), executors={KIND_ONE_SHOT: lambda job, _db: None})
    assert clock.fire_due(now=10.0) == {"fired": [], "failed": ["old"]}


def test_nonpositive_stale_window_rejected(tmp_path):
    with pytest.raises(ValueError, match="stale_firing_seconds"):
        DaemonClock(str(tmp_path / "c.db"), executors={}, stale_firing_seconds=0)
