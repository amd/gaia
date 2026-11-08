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


def test_daemon_executor_failure_persists_then_notifies(tmp_path):
    db = _db(tmp_path)
    job_id = store.register_job(
        db, source="email:schedule_store", kind=KIND_ONE_SHOT,
        fire_at=1.0, payload={"subject": "Q3"},
    )
    clock = DaemonClock(
        str(tmp_path / "clock.db"),
        executors={KIND_ONE_SHOT: lambda _job, _db: (_ for _ in ()).throw(RuntimeError("boom"))},
    )
    with patch("gaia.schedule.sinks.dispatch") as dispatch:
        assert clock.fire_due(now=2.0) == {"fired": [], "failed": [job_id]}
    dispatch.assert_called_once()
    assert "boom" in dispatch.call_args.args[2]
    assert store.get_job(db, job_id=job_id)["status"] == "failed"