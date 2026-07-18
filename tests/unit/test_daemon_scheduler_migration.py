# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Reconciliation tests for the daemon clock (V2-15, #2156).

These cover the epic's explicitly-flagged regression: absorbing an in-sidecar
clock's jobs into the daemon clock MUST be exactly-once — no double-run when
old and new paths briefly coexist, and no silently-dropped job. The concrete
email adapter is exercised in the email package's own suite; here the source is
a generic batch so the guarantee is tested at the reconciler itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.daemon.scheduler import store
from gaia.daemon.scheduler.clock import _ClockDB
from gaia.daemon.scheduler.migration import assert_no_dropped, reconcile_jobs
from gaia.daemon.scheduler.models import (
    KIND_ONE_SHOT,
    KIND_RECURRING,
    DroppedJobError,
    MigratableJob,
    ReconciliationError,
)


def _open_store(tmp_path: Path) -> _ClockDB:
    db = _ClockDB()
    db.init_db(str(tmp_path / "clock.db"))
    store.init_schema(db)
    return db


def _job(sid: str) -> MigratableJob:
    return MigratableJob(
        source="email:schedule_store",
        source_job_id=sid,
        kind=KIND_ONE_SHOT,
        fire_at=100.0,
        payload={"sid": sid},
    )


def test_reconcile_adopts_each_job_once(tmp_path):
    db = _open_store(tmp_path)
    jobs = [_job("a"), _job("b"), _job("c")]
    result = reconcile_jobs(db, jobs)
    assert len(result.migrated) == 3
    assert result.skipped == ()
    assert len(store.list_jobs(db)) == 3


def test_reconcile_is_idempotent_no_double_run(tmp_path):
    """Re-running the SAME batch adopts nothing new — the exactly-once spine.

    This is the flagged regression: a briefing/send migrated once must never be
    adopted (and therefore fired) a second time when reconciliation runs again.
    """
    db = _open_store(tmp_path)
    jobs = [_job("a"), _job("b")]

    first = reconcile_jobs(db, jobs)
    assert len(first.migrated) == 2

    second = reconcile_jobs(db, jobs)
    assert second.migrated == ()
    assert len(second.skipped) == 2

    # Exactly two daemon jobs exist — no duplicates from the second pass.
    assert len(store.list_jobs(db)) == 2
    assert len(store.list_ledger(db, source="email:schedule_store")) == 2


def test_reconcile_no_double_run_when_both_paths_coexist(tmp_path):
    """A NEW job added between passes is adopted; prior ones stay single.

    Models the transition window where the legacy in-sidecar clock is still
    producing jobs while the daemon has begun adopting them.
    """
    db = _open_store(tmp_path)
    reconcile_jobs(db, [_job("a")])
    # Legacy path produced one more job before it was gated off.
    result = reconcile_jobs(db, [_job("a"), _job("b")])
    assert result.migrated == ("email:schedule_store:b",)
    assert result.skipped == ("email:schedule_store:a",)
    assert len(store.list_jobs(db)) == 2


def test_dropped_job_detected_loudly(tmp_path):
    db = _open_store(tmp_path)
    reconcile_jobs(db, [_job("a"), _job("b")])
    # The source still owns "c" but it never reached the ledger.
    with pytest.raises(DroppedJobError) as exc:
        assert_no_dropped(
            db, source="email:schedule_store", source_job_ids=["a", "b", "c"]
        )
    assert "c" in str(exc.value)


def test_assert_no_dropped_passes_when_all_present(tmp_path):
    db = _open_store(tmp_path)
    reconcile_jobs(db, [_job("a"), _job("b")])
    # Must not raise.
    assert_no_dropped(db, source="email:schedule_store", source_job_ids=["a", "b"])


def test_unschedulable_job_raises_before_partial_adoption(tmp_path):
    db = _open_store(tmp_path)
    bad = MigratableJob(
        source="email:schedule_store",
        source_job_id="x",
        kind=KIND_ONE_SHOT,
        fire_at=None,  # one-shot with no fire time — unschedulable
    )
    with pytest.raises(ReconciliationError, match="fire_at"):
        reconcile_jobs(db, [_job("a"), bad])
    # All-or-nothing: the valid job in the batch was NOT partially adopted.
    assert store.list_jobs(db) == []


def test_recurring_missing_interval_raises(tmp_path):
    db = _open_store(tmp_path)
    bad = MigratableJob(
        source="email:briefing",
        source_job_id="daily",
        kind=KIND_RECURRING,
        interval_seconds=None,
    )
    with pytest.raises(ReconciliationError, match="interval"):
        reconcile_jobs(db, [bad])


def test_missing_source_job_id_raises(tmp_path):
    db = _open_store(tmp_path)
    bad = MigratableJob(
        source="email", source_job_id="", kind=KIND_ONE_SHOT, fire_at=1.0
    )
    with pytest.raises(ReconciliationError, match="source_job_id"):
        reconcile_jobs(db, [bad])
