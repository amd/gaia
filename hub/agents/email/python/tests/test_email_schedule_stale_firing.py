# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A scheduled send left ``firing`` by a crash must surface, never re-send,
and a send still in flight on another driver must never be taken over.

A claim is a lease the firing driver renews while the send runs; only its
holder records the outcome. A lease that stops being renewed is recovered:
a send is marked failed (and listed as failed), a snooze is re-queued.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import schedule_store  # noqa: E402
from gaia_agent_email.scheduler import EmailJobScheduler  # noqa: E402
from gaia_agent_email.tools.schedule_tools import (  # noqa: E402
    list_scheduled_jobs_impl,
)

from gaia.database.mixin import DatabaseMixin  # noqa: E402

STALE = 600.0
BEAT = 10.0
CONFIRM = 2 * BEAT
SEND = schedule_store.KIND_SCHEDULED_SEND
SNOOZE = schedule_store.KIND_SNOOZE


class _DB(DatabaseMixin):
    pass


def _db(path: Path) -> _DB:
    db = _DB()
    db.init_db(str(path))
    schedule_store.init_schema(db)
    return db


def _scheduler(path: Path, executor) -> EmailJobScheduler:
    return EmailJobScheduler(
        str(path),
        executors={SEND: executor, SNOOZE: executor},
        stale_firing_seconds=STALE,
        heartbeat_seconds=BEAT,
    )


def test_send_left_firing_by_a_crash_is_failed_listed_and_not_resent(tmp_path):
    path = tmp_path / "state.db"
    db = _db(path)
    job_id = schedule_store.create_job(
        db, kind=SEND, due_at=100.0, payload={"to": "a@b.c", "subject": "Q3"}
    )
    # The previous process claimed the send and died before recording it.
    assert schedule_store.claim_job(db, job_id=job_id, now=100.0)

    calls: list = []
    scheduler = _scheduler(path, lambda job, _db: calls.append(job))
    assert scheduler.fire_due_jobs(now=100.0 + STALE - 1) == {"fired": [], "failed": []}
    assert scheduler.fire_due_jobs(now=100.0 + STALE + 1) == {"fired": [], "failed": []}
    out = scheduler.fire_due_jobs(now=100.0 + STALE + 1 + CONFIRM)
    assert out == {"fired": [], "failed": [job_id]}
    assert calls == []

    listing = list_scheduled_jobs_impl(db, now=100.0 + STALE + CONFIRM + 60)
    assert listing["pending"] == []
    [failed] = listing["failed"]
    assert failed["job_id"] == job_id and failed["subject"] == "Q3"
    assert "interrupted while firing" in failed["error"]


def test_snooze_left_firing_is_requeued_and_restored(tmp_path):
    """Putting a message back in the inbox twice is harmless; hiding it is not."""
    path = tmp_path / "state.db"
    db = _db(path)
    job_id = schedule_store.create_job(db, kind=SNOOZE, due_at=100.0, payload={})
    assert schedule_store.claim_job(db, job_id=job_id, now=100.0)

    calls: list = []
    scheduler = _scheduler(path, lambda job, _db: calls.append(job["job_id"]))
    scheduler.fire_due_jobs(now=100.0 + STALE + 1)
    out = scheduler.fire_due_jobs(now=100.0 + STALE + 1 + CONFIRM)
    assert out == {"fired": [job_id], "failed": []}
    assert calls == [job_id]
    assert schedule_store.get_job(db, job_id=job_id)["status"] == "fired"


@pytest.mark.parametrize("kind", [SEND, SNOOZE])
def test_a_fire_still_in_flight_is_not_taken_over_after_a_wake_up(tmp_path, kind):
    """Driver A is mid-fire when driver B's first pass after a long sleep sees
    its lease hours old. A's heartbeat lands before B is sure; B leaves it."""
    path = tmp_path / "state.db"
    db = _db(path)
    job_id = schedule_store.create_job(db, kind=kind, due_at=100.0, payload={})
    gate, entered, runs = threading.Event(), threading.Event(), []

    def slow(job, _db):
        runs.append("A")
        entered.set()
        gate.wait(10)
        if kind == SNOOZE:
            raise RuntimeError("message already moved")

    a = EmailJobScheduler(
        str(path), {kind: slow}, stale_firing_seconds=STALE, heartbeat_seconds=0.1
    )
    b = _scheduler(path, lambda job, _db: runs.append("B"))
    thread = threading.Thread(target=lambda: a.fire_due_jobs(now=100.0))
    thread.start()
    try:
        assert entered.wait(5)
        woke = 100.0 + 3 * 3600
        assert b.fire_due_jobs(now=woke) == {"fired": [], "failed": []}
        time.sleep(0.5)  # A's heartbeats renew the lease with the real clock
        assert b.fire_due_jobs(now=woke + CONFIRM + 1) == {"fired": [], "failed": []}
        assert schedule_store.get_job(db, job_id=job_id)["status"] == "firing"
    finally:
        gate.set()
        thread.join(10)
    job = schedule_store.get_job(db, job_id=job_id)
    assert runs == ["A"]
    assert job["status"] == ("fired" if kind == SEND else "failed")


def test_a_late_error_never_overwrites_the_rerun_that_succeeded(tmp_path):
    db = _db(tmp_path / "state.db")
    job_id = schedule_store.create_job(db, kind=SNOOZE, due_at=1.0, payload={})
    first = schedule_store.claim_job(db, job_id=job_id, now=1.0)
    [stale] = schedule_store.find_stale_firing(db, now=STALE + 2, stale_after=STALE)
    assert schedule_store.recover_claim(db, stale, now=STALE + 2) == "requeued"
    second = schedule_store.claim_job(db, job_id=job_id, now=STALE + 3)
    assert schedule_store.mark_fired(db, job_id=job_id, claim_id=second)

    assert not schedule_store.mark_failed(
        db, job_id=job_id, claim_id=first, error="message already moved"
    )
    job = schedule_store.get_job(db, job_id=job_id)
    assert job["status"] == "fired" and job["error"] is None


def test_only_the_claim_holder_can_record_an_outcome(tmp_path):
    db = _db(tmp_path / "state.db")
    job_id = schedule_store.create_job(db, kind=SEND, due_at=1.0, payload={})
    holder = schedule_store.claim_job(db, job_id=job_id, now=1.0)
    assert not schedule_store.mark_failed(
        db, job_id=job_id, claim_id="someone-else", error="x"
    )
    assert not schedule_store.mark_fired(db, job_id=job_id, claim_id="someone-else")
    assert schedule_store.get_job(db, job_id=job_id)["status"] == "firing"
    assert schedule_store.mark_fired(db, job_id=job_id, claim_id=holder)


def test_a_row_stuck_by_an_older_build_starts_its_window_at_upgrade(tmp_path):
    path = tmp_path / "old.db"
    db = _DB()
    db.init_db(str(path))
    db.execute(
        "CREATE TABLE email_scheduled_jobs (job_id TEXT PRIMARY KEY,"
        " kind TEXT NOT NULL, due_at REAL NOT NULL, payload_json TEXT NOT NULL,"
        " mailbox TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT,"
        " created_at REAL NOT NULL, fired_at REAL)"
    )
    db.execute(
        "INSERT INTO email_scheduled_jobs (job_id, kind, due_at, payload_json,"
        " status, created_at) VALUES ('old', 'scheduled_send', 1, '{}', 'firing', 0)"
    )
    db.close_db()

    calls: list = []
    scheduler = _scheduler(path, lambda job, _db: calls.append(job))
    upgraded = time.time()
    assert scheduler.fire_due_jobs(now=upgraded) == {"fired": [], "failed": []}
    assert scheduler.fire_due_jobs(now=upgraded + CONFIRM) == {
        "fired": [],
        "failed": [],
    }
    later = upgraded + STALE + 1
    scheduler.fire_due_jobs(now=later)
    assert scheduler.fire_due_jobs(now=later + CONFIRM) == {
        "fired": [],
        "failed": ["old"],
    }
    assert calls == []


def test_two_connections_migrating_at_once_both_start(tmp_path):
    path = tmp_path / "race.db"
    winner, loser = _DB(), _DB()
    winner.init_db(str(path))
    loser.init_db(str(path))
    real_query = loser.query

    def stale_view(sql, *args, **kwargs):
        rows = real_query(sql, *args, **kwargs)
        if sql.startswith("PRAGMA table_info"):
            schedule_store.init_schema(winner)
        return rows

    loser.execute(
        "CREATE TABLE email_scheduled_jobs (job_id TEXT PRIMARY KEY,"
        " kind TEXT NOT NULL, due_at REAL NOT NULL, payload_json TEXT NOT NULL,"
        " mailbox TEXT, status TEXT NOT NULL DEFAULT 'pending', error TEXT,"
        " created_at REAL NOT NULL, fired_at REAL)"
    )
    loser.query = stale_view
    schedule_store.init_schema(loser)  # must not raise "duplicate column name"


def test_bad_lease_settings_rejected(tmp_path):
    with pytest.raises(ValueError, match="stale_firing_seconds"):
        EmailJobScheduler(str(tmp_path / "s.db"), executors={}, stale_firing_seconds=0)
    with pytest.raises(ValueError, match="heartbeat_seconds"):
        EmailJobScheduler(
            str(tmp_path / "s.db"),
            executors={},
            stale_firing_seconds=60,
            heartbeat_seconds=90,
        )
