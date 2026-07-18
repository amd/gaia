# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the single daemon-owned clock (V2-15, #2156).

Covers the store + ``DaemonClock`` driver in isolation: a one-shot job fires
exactly once, a recurring job re-arms, a missing executor fails loudly (never a
silent skip), and the atomic claim prevents a second driver from double-firing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.daemon.scheduler import store
from gaia.daemon.scheduler.clock import DaemonClock, _ClockDB
from gaia.daemon.scheduler.models import (
    KIND_ONE_SHOT,
    KIND_RECURRING,
    STATUS_FAILED,
    STATUS_FIRED,
    STATUS_PENDING,
)


def _open_store(tmp_path: Path) -> _ClockDB:
    db = _ClockDB()
    db.init_db(str(tmp_path / "clock.db"))
    store.init_schema(db)
    return db


def test_one_shot_job_fires_exactly_once(tmp_path):
    db = _open_store(tmp_path)
    jid = store.register_job(
        db, source="test", kind=KIND_ONE_SHOT, fire_at=100.0, payload={"n": 1}
    )
    calls = []
    clock = DaemonClock(
        str(tmp_path / "clock.db"),
        executors={KIND_ONE_SHOT: lambda job, _db: calls.append(job["job_id"])},
    )

    # Not due yet.
    assert clock.fire_due(now=50.0) == {"fired": [], "failed": []}
    # Due.
    first = clock.fire_due(now=150.0)
    assert first == {"fired": [jid], "failed": []}
    # A second pass must NOT re-fire — the job is now fired, not pending.
    second = clock.fire_due(now=200.0)
    assert second == {"fired": [], "failed": []}

    assert calls == [jid]
    db2 = _open_store(tmp_path)
    assert store.get_job(db2, job_id=jid)["status"] == STATUS_FIRED


def test_recurring_job_rearms_after_firing(tmp_path):
    db = _open_store(tmp_path)
    jid = store.register_job(
        db,
        source="test",
        kind=KIND_RECURRING,
        fire_at=100.0,
        interval_seconds=60,
    )
    clock = DaemonClock(
        str(tmp_path / "clock.db"),
        executors={KIND_RECURRING: lambda job, _db: None},
    )
    clock.fire_due(now=100.0)

    db2 = _open_store(tmp_path)
    job = store.get_job(db2, job_id=jid)
    assert job["status"] == STATUS_PENDING  # re-armed, not terminal
    assert job["run_count"] == 1
    # Rescheduled from wall-clock-now + interval (not old fire_at + interval),
    # so a long daemon downtime skips missed fires instead of a catch-up storm.
    assert job["fire_at"] > 100.0


def test_missing_executor_fails_loudly(tmp_path):
    db = _open_store(tmp_path)
    jid = store.register_job(db, source="test", kind="unregistered_kind", fire_at=1.0)
    clock = DaemonClock(str(tmp_path / "clock.db"), executors={})
    result = clock.fire_due(now=10.0)

    assert result == {"fired": [], "failed": [jid]}
    db2 = _open_store(tmp_path)
    job = store.get_job(db2, job_id=jid)
    assert job["status"] == STATUS_FAILED
    assert "no executor registered" in job["error"]


def test_executor_exception_marks_failed_not_dropped(tmp_path):
    db = _open_store(tmp_path)
    jid = store.register_job(db, source="test", kind=KIND_ONE_SHOT, fire_at=1.0)

    def boom(job, _db):
        raise RuntimeError("send blew up")

    clock = DaemonClock(str(tmp_path / "clock.db"), executors={KIND_ONE_SHOT: boom})
    result = clock.fire_due(now=10.0)

    assert result == {"fired": [], "failed": [jid]}
    db2 = _open_store(tmp_path)
    job = store.get_job(db2, job_id=jid)
    assert job["status"] == STATUS_FAILED
    assert "send blew up" in job["error"]


def test_claim_is_atomic_no_double_fire(tmp_path):
    """A claimed job cannot be claimed again — the exactly-once guard."""
    db = _open_store(tmp_path)
    jid = store.register_job(db, source="test", kind=KIND_ONE_SHOT, fire_at=1.0)
    assert store.claim_job(db, job_id=jid) is True
    # Second claim loses: the row is already firing, not pending.
    assert store.claim_job(db, job_id=jid) is False


def test_in_memory_path_rejected():
    with pytest.raises(ValueError, match="file-backed"):
        DaemonClock(":memory:", executors={})


def test_nonpositive_poll_rejected(tmp_path):
    with pytest.raises(ValueError, match="poll_seconds"):
        DaemonClock(str(tmp_path / "c.db"), executors={}, poll_seconds=0)
