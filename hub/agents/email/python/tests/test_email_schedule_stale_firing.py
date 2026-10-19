# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A scheduled send left ``firing`` by a crash must surface, never re-send.

``claim_job`` moves a row ``pending -> firing`` before the send runs. A crash,
or a locked DB when the outcome is written, used to leave it ``firing`` forever:
invisible as a failure and never retried. A pass now marks a claim older than
``stale_firing_seconds`` ``failed`` with the reason, and still never sends twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import schedule_store  # noqa: E402
from gaia_agent_email.scheduler import EmailJobScheduler  # noqa: E402

from gaia.database.mixin import DatabaseMixin  # noqa: E402

STALE = 600.0


class _DB(DatabaseMixin):
    pass


def _db(path: Path) -> _DB:
    db = _DB()
    db.init_db(str(path))
    schedule_store.init_schema(db)
    return db


def _scheduler(path: Path, calls: list) -> EmailJobScheduler:
    return EmailJobScheduler(
        str(path),
        executors={
            schedule_store.KIND_SCHEDULED_SEND: lambda job, _db: calls.append(job)
        },
        stale_firing_seconds=STALE,
    )


def test_send_left_firing_by_a_crash_is_failed_not_resent(tmp_path):
    path = tmp_path / "state.db"
    db = _db(path)
    job_id = schedule_store.create_job(
        db, kind=schedule_store.KIND_SCHEDULED_SEND, due_at=100.0, payload={}
    )
    # The previous process claimed the send and died before recording it.
    assert schedule_store.claim_job(db, job_id=job_id, now=100.0) is True

    calls: list = []
    scheduler = _scheduler(path, calls)
    # A claim still inside the window may be a send in flight elsewhere.
    assert scheduler.fire_due_jobs(now=100.0 + STALE - 1) == {"fired": [], "failed": []}
    assert schedule_store.get_job(db, job_id=job_id)["status"] == "firing"

    out = scheduler.fire_due_jobs(now=100.0 + STALE + 1)
    assert out == {"fired": [], "failed": [job_id]}
    assert calls == []
    job = schedule_store.get_job(db, job_id=job_id)
    assert job["status"] == "failed"
    assert "interrupted while firing" in job["error"]


def test_store_created_before_the_claim_column_is_migrated(tmp_path):
    """A row stuck by an older build has no claim time; it is stale on sight."""
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
    # The agent runs this on startup, before it starts the scheduler.
    schedule_store.init_schema(db)
    db.close_db()

    calls: list = []
    out = _scheduler(path, calls).fire_due_jobs(now=10.0)
    assert out == {"fired": [], "failed": ["old"]}
    assert calls == []


def test_nonpositive_stale_window_rejected(tmp_path):
    with pytest.raises(ValueError, match="stale_firing_seconds"):
        EmailJobScheduler(str(tmp_path / "s.db"), executors={}, stale_firing_seconds=0)
