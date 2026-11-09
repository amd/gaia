# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Daemon-clock regression tests for scheduled failure notices (#5411)."""

from __future__ import annotations

from unittest.mock import patch

from gaia.daemon.scheduler import store
from gaia.daemon.scheduler.clock import DaemonClock
from gaia.daemon.scheduler.models import KIND_ONE_SHOT
from gaia.database.mixin import DatabaseMixin


class _DB(DatabaseMixin):
    pass


def _db(tmp_path: object) -> _DB:
    db = _DB()
    db.init_db(str(tmp_path / "clock.db"))
    store.init_schema(db)
    return db


def test_daemon_recovered_holder_failure_does_not_notify_twice(tmp_path):
    db = _db(tmp_path)
    job_id = store.register_job(
        db,
        source="email:schedule_store",
        kind=KIND_ONE_SHOT,
        fire_at=1.0,
        payload={"kind": "scheduled_send", "payload": {"subject": "Q3"}},
    )

    def late_failure(_job, clock_db):
        current = store.get_job(clock_db, job_id=job_id)
        assert current is not None
        assert store.recover_claim(clock_db, current, now=1000.0) == "failed"
        raise RuntimeError("late failure")

    clock = DaemonClock(
        str(tmp_path / "clock.db"), executors={KIND_ONE_SHOT: late_failure}
    )
    with patch("gaia.schedule.sinks.dispatch") as dispatch:
        assert clock.fire_due(now=2.0) == {"fired": [], "failed": [job_id]}
    dispatch.assert_not_called()
    assert store.get_job(db, job_id=job_id)["status"] == "failed"
    assert "late failure" in store.get_job(db, job_id=job_id)["error"]


def test_daemon_executor_failure_persists_then_notifies(tmp_path):
    db = _db(tmp_path)
    job_id = store.register_job(
        db,
        source="email:schedule_store",
        kind=KIND_ONE_SHOT,
        fire_at=1.0,
        payload={"kind": "scheduled_send", "payload": {"subject": "Q3"}},
    )
    clock = DaemonClock(
        str(tmp_path / "clock.db"),
        executors={
            KIND_ONE_SHOT: lambda _job, _db: (_ for _ in ()).throw(RuntimeError("boom"))
        },
    )
    with patch("gaia.schedule.sinks.dispatch") as dispatch:
        assert clock.fire_due(now=2.0) == {"fired": [], "failed": [job_id]}
    dispatch.assert_called_once_with(
        "notification",
        {"title": "Scheduled email failed"},
        "GAIA could not send the scheduled email (Q3). RuntimeError: boom",
    )
    assert store.get_job(db, job_id=job_id)["status"] == "failed"
