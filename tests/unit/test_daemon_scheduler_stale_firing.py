# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A job left in ``firing`` by a crash or a locked DB must not stay stuck,
and a fire that is still running must not be taken over.

``claim_job`` moves a row ``pending -> firing`` and returns a lease token. The
firing driver renews the lease while its executor runs, and only the token's
holder can record the outcome. A lease that goes ``stale_firing_seconds``
without renewal, and that this clock then watches stay unrenewed for two more
heartbeat intervals, is recovered: a recurring row is re-queued, a one-shot
row fails (it may already have taken effect, so it is not run twice).
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from gaia.daemon.scheduler import store
from gaia.daemon.scheduler.clock import DaemonClock, _ClockDB
from gaia.daemon.scheduler.models import (
    KIND_ONE_SHOT,
    KIND_RECURRING,
    STATUS_FAILED,
    STATUS_FIRED,
    STATUS_FIRING,
    STATUS_PENDING,
)

STALE = 600.0
BEAT = 10.0
CONFIRM = 2 * BEAT


def _db(path):
    db = _ClockDB()
    db.init_db(str(path))
    store.init_schema(db)
    return db


def _clock(path, executors):
    return DaemonClock(
        str(path), executors, stale_firing_seconds=STALE, heartbeat_seconds=BEAT
    )


def test_recurring_job_whose_outcome_write_failed_fires_again(tmp_path, monkeypatch):
    path = tmp_path / "clock.db"
    jid = store.register_job(
        _db(path), source="t", kind=KIND_RECURRING, fire_at=100.0, interval_seconds=60
    )
    calls = []
    clock = _clock(path, {KIND_RECURRING: lambda job, _db: calls.append(job["job_id"])})

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "mark_fired", locked)
    with pytest.raises(sqlite3.OperationalError):
        clock.fire_due(now=100.0)
    monkeypatch.undo()
    assert store.get_job(_db(path), job_id=jid)["status"] == STATUS_FIRING

    # Within the stale window the lease still belongs to its holder.
    assert clock.fire_due(now=100.0 + STALE - 1) == {"fired": [], "failed": []}
    # Stale, but first seen now: watched, not recovered yet.
    assert clock.fire_due(now=100.0 + STALE + 1) == {"fired": [], "failed": []}
    assert calls == [jid]
    # Still unrenewed after two heartbeat intervals: re-queued and fired.
    out = clock.fire_due(now=100.0 + STALE + 1 + CONFIRM)
    assert out == {"fired": [jid], "failed": []}
    assert calls == [jid, jid]
    job = store.get_job(_db(path), job_id=jid)
    assert job["status"] == STATUS_PENDING
    assert job["error"] is None


def test_one_shot_job_left_firing_by_a_crash_fails_loudly_not_rerun(tmp_path):
    path = tmp_path / "clock.db"
    db = _db(path)
    jid = store.register_job(db, source="t", kind=KIND_ONE_SHOT, fire_at=100.0)
    # A previous daemon claimed the job and died before writing the outcome.
    assert store.claim_job(db, job_id=jid, now=100.0) is not None

    calls = []
    clock = _clock(path, {KIND_ONE_SHOT: lambda job, _db: calls.append(job)})
    assert clock.fire_due(now=100.0 + STALE + 1) == {"fired": [], "failed": []}
    out = clock.fire_due(now=100.0 + STALE + 1 + CONFIRM)
    assert out == {"fired": [], "failed": [jid]}
    assert calls == []
    job = store.get_job(_db(path), job_id=jid)
    assert job["status"] == STATUS_FAILED
    assert "interrupted while firing" in job["error"]


def test_a_rerunnable_one_shot_left_firing_is_requeued_and_fires_again(tmp_path):
    path = tmp_path / "clock.db"
    db = _db(path)
    jid = store.register_job(
        db,
        source="t",
        kind=KIND_ONE_SHOT,
        fire_at=100.0,
        payload={"rerunnable": True},
    )
    assert store.claim_job(db, job_id=jid, now=100.0) is not None

    calls = []
    clock = _clock(path, {KIND_ONE_SHOT: lambda job, _db: calls.append(job["job_id"])})
    assert clock.fire_due(now=100.0 + STALE + 1) == {"fired": [], "failed": []}
    out = clock.fire_due(now=100.0 + STALE + 1 + CONFIRM)
    assert out == {"fired": [jid], "failed": []}
    assert calls == [jid]
    assert store.get_job(_db(path), job_id=jid)["status"] == STATUS_FIRED


def test_a_truthy_but_not_true_rerunnable_flag_still_fails(tmp_path):
    db = _db(tmp_path / "clock.db")
    jid = store.register_job(
        db, source="t", kind=KIND_ONE_SHOT, fire_at=100.0, payload={"rerunnable": "yes"}
    )
    assert store.claim_job(db, job_id=jid, now=100.0) is not None
    stale = store.find_stale_firing(db, now=100.0 + STALE + 1, stale_after=STALE)
    assert store.recover_claim(db, stale[0], now=100.0 + STALE + 1) == "failed"


def test_a_live_holder_that_renews_after_a_wake_up_keeps_its_job(tmp_path):
    """After sleep/resume every lease looks hours old; a live holder's next
    heartbeat must clear it before anyone recovers it."""
    path = tmp_path / "clock.db"
    db = _db(path)
    jid = store.register_job(db, source="t", kind=KIND_ONE_SHOT, fire_at=100.0)
    claim = store.claim_job(db, job_id=jid, now=100.0)
    other = _clock(path, {KIND_ONE_SHOT: lambda job, _db: None})

    woke = 100.0 + 3 * 3600
    assert other.fire_due(now=woke) == {"fired": [], "failed": []}
    assert store.renew_claim(db, job_id=jid, claim_id=claim, now=woke + 1)
    assert other.fire_due(now=woke + CONFIRM + 1) == {"fired": [], "failed": []}
    assert store.get_job(db, job_id=jid)["status"] == STATUS_FIRING
    assert store.mark_fired(db, job_id=jid, claim_id=claim)
    assert store.get_job(db, job_id=jid)["status"] == STATUS_FIRED


def test_only_the_claim_holder_can_record_an_outcome(tmp_path):
    db = _db(tmp_path / "clock.db")
    jid = store.register_job(db, source="t", kind=KIND_ONE_SHOT, fire_at=1.0)
    first = store.claim_job(db, job_id=jid, now=1.0)
    stale = store.find_stale_firing(db, now=1.0 + STALE + 1, stale_after=STALE)
    assert store.recover_claim(db, stale[0], now=1.0 + STALE + 1) == "failed"
    # Re-scheduled by the user and claimed again by a second driver.
    db.update("daemon_jobs", {"status": STATUS_PENDING}, "job_id = :id", {"id": jid})
    second = store.claim_job(db, job_id=jid, now=2000.0)

    assert store.mark_failed(db, job_id=jid, claim_id=first, error="late") is False
    assert store.mark_fired(db, job_id=jid, claim_id=first) is False
    job = store.get_job(db, job_id=jid)
    assert job["status"] == STATUS_FIRING and job["claim_id"] == second
    assert "late" not in (job["error"] or "")
    assert store.mark_fired(db, job_id=jid, claim_id=second) is True


def test_a_holder_alive_after_recovery_overwrites_the_guessed_failure(tmp_path):
    db = _db(tmp_path / "clock.db")
    jid = store.register_job(db, source="t", kind=KIND_ONE_SHOT, fire_at=1.0)
    claim = store.claim_job(db, job_id=jid, now=1.0)
    stale = store.find_stale_firing(db, now=1.0 + STALE + 1, stale_after=STALE)
    assert store.recover_claim(db, stale[0], now=1.0 + STALE + 1) == "failed"

    assert store.mark_fired(db, job_id=jid, claim_id=claim) is True
    job = store.get_job(db, job_id=jid)
    assert job["status"] == STATUS_FIRED and job["error"] is None


def test_a_running_executor_renews_its_lease(tmp_path):
    path = tmp_path / "clock.db"
    jid = store.register_job(_db(path), source="t", kind=KIND_ONE_SHOT, fire_at=1.0)
    seen = []

    def slow(job, _db):
        time.sleep(0.5)
        seen.append(store.get_job(_db, job_id=job["job_id"])["claimed_at"])

    clock = DaemonClock(
        str(path), {KIND_ONE_SHOT: slow}, stale_firing_seconds=60, heartbeat_seconds=0.1
    )
    clock.fire_due(now=1.0)
    # Claimed at the fake now=1.0; renewed with the real clock while running.
    assert seen[0] > 1.0


def test_a_row_stuck_by_an_older_build_starts_its_window_at_upgrade(tmp_path):
    """No claim time recorded: stale after the window, not on sight."""
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

    clock = _clock(path, {KIND_ONE_SHOT: lambda job, _db: None})
    before = time.time()
    assert clock.fire_due(now=before) == {"fired": [], "failed": []}
    # The upgrade stamps the row with the real time it ran, which on a slow
    # runner can be well after `before`; measure the window from that stamp.
    check = _ClockDB()
    check.init_db(str(path))
    upgraded = check.query("SELECT claimed_at FROM daemon_jobs WHERE job_id = 'old'")[
        0
    ]["claimed_at"]
    check.close_db()
    assert upgraded >= before
    assert clock.fire_due(now=upgraded + CONFIRM) == {"fired": [], "failed": []}
    later = upgraded + STALE + 1
    assert clock.fire_due(now=later) == {"fired": [], "failed": []}
    assert clock.fire_due(now=later + CONFIRM) == {"fired": [], "failed": ["old"]}


def test_two_connections_adding_the_lease_columns_at_once_both_start(tmp_path):
    path = tmp_path / "race.db"
    winner, loser = _ClockDB(), _ClockDB()
    winner.init_db(str(path))
    loser.init_db(str(path))
    loser.execute(
        store.DAEMON_JOBS_DDL.replace(
            "    claimed_at      REAL,\n    claim_id        TEXT,\n"
            "    superseded_claim TEXT\n",
            "    claimed_at      REAL\n",
        )
    )
    real_query = loser.query

    def stale_view(sql, *args, **kwargs):
        rows = real_query(sql, *args, **kwargs)
        if sql.startswith("PRAGMA table_info"):
            # The winner adds the columns after the loser looked.
            store.init_schema(winner)
        return rows

    loser.query = stale_view
    store.init_schema(loser)  # must not raise "duplicate column name"
    columns = {r["name"] for r in real_query("PRAGMA table_info(daemon_jobs)")}
    assert {"claim_id", "superseded_claim"} <= columns


def test_bad_lease_settings_rejected(tmp_path):
    with pytest.raises(ValueError, match="stale_firing_seconds"):
        DaemonClock(str(tmp_path / "c.db"), executors={}, stale_firing_seconds=0)
    with pytest.raises(ValueError, match="heartbeat_seconds"):
        DaemonClock(
            str(tmp_path / "c.db"),
            executors={},
            stale_firing_seconds=60,
            heartbeat_seconds=60,
        )


@pytest.mark.parametrize("kind", [KIND_ONE_SHOT, KIND_RECURRING])
def test_a_lease_renewed_after_it_was_read_is_not_recovered(tmp_path, kind):
    """The holder's heartbeat lands between the stale scan and the recovery
    write: the recovery must lose, or a live job is failed or run twice."""
    db = _db(tmp_path / "clock.db")
    jid = store.register_job(
        db, source="t", kind=kind, fire_at=1.0, interval_seconds=60
    )
    claim = store.claim_job(db, job_id=jid, now=1.0)
    [stale] = store.find_stale_firing(db, now=STALE + 2, stale_after=STALE)
    assert store.renew_claim(db, job_id=jid, claim_id=claim, now=STALE + 2)

    assert store.recover_claim(db, stale, now=STALE + 2) is None
    job = store.get_job(db, job_id=jid)
    assert job["status"] == STATUS_FIRING and job["claim_id"] == claim


def test_the_recovery_error_names_a_local_time(tmp_path):
    from datetime import datetime

    db = _db(tmp_path / "clock.db")
    claimed = datetime(2026, 10, 18, 14, 5).timestamp()
    jid = store.register_job(db, source="t", kind=KIND_ONE_SHOT, fire_at=claimed)
    store.claim_job(db, job_id=jid, now=claimed)
    later = claimed + 45 * 60
    [stale] = store.find_stale_firing(db, now=later, stale_after=STALE)
    store.recover_claim(db, stale, now=later)
    error = store.get_job(db, job_id=jid)["error"]
    assert "14:05 on 2026-10-18 (45 minutes ago)" in error
    assert str(int(claimed)) not in error
