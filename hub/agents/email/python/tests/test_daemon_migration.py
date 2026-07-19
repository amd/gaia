# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Email -> daemon clock reconciliation tests (V2-15, #2156).

The acceptance test the epic flags as the highest regression risk: the email
sidecar's in-process clocks (BriefingScheduler #1918, EmailJobScheduler #1919)
migrate into the single daemon clock EXACTLY ONCE, with no double-adoption on a
re-run and no silently-dropped job. Also verifies the supervision gate so the
embedded clocks go dark under the daemon but stay live standalone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# parents[0] = tests/, [1] = email/, [2] = python/, [3] = agents/, [4] = hub/,
# [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import schedule_store  # noqa: E402
from gaia_agent_email.briefing import BriefingScheduleConfig  # noqa: E402
from gaia_agent_email.daemon_migration import (  # noqa: E402
    BRIEFING_JOB_ID,
    SOURCE_BRIEFING,
    SOURCE_ONE_SHOT,
    migrate_email_clocks,
)
from gaia_agent_email.supervision import is_daemon_supervised  # noqa: E402

from gaia.daemon.constants import DAEMON_SUPERVISION_ENV_VAR  # noqa: E402
from gaia.daemon.scheduler import store as daemon_store  # noqa: E402
from gaia.database.mixin import DatabaseMixin  # noqa: E402


class _DB(DatabaseMixin):
    """Bare DatabaseMixin host — one file holds both stores."""


def _make_db(tmp_path: Path) -> _DB:
    db = _DB()
    db.init_db(str(tmp_path / "state.db"))
    schedule_store.init_schema(db)
    daemon_store.init_schema(db)
    return db


# ---------------------------------------------------------------------------
# One-shot migration (EmailJobScheduler / schedule_store, #1919)
# ---------------------------------------------------------------------------


def test_one_shot_jobs_migrate_exactly_once(tmp_path):
    db = _make_db(tmp_path)
    j1 = schedule_store.create_job(
        db, kind=schedule_store.KIND_SCHEDULED_SEND, due_at=100.0, mailbox="a@x"
    )
    j2 = schedule_store.create_job(
        db, kind=schedule_store.KIND_SNOOZE, due_at=200.0, mailbox="a@x"
    )

    first = migrate_email_clocks(db)
    assert len(first.migrated) == 2

    # Re-running the adapter adopts nothing new — the flagged regression guard.
    second = migrate_email_clocks(db)
    assert second.migrated == ()
    assert len(second.skipped) == 2

    # Exactly two daemon jobs, one ledger row per source job.
    assert len(daemon_store.list_jobs(db)) == 2
    ledger = {
        r["source_job_id"] for r in daemon_store.list_ledger(db, source=SOURCE_ONE_SHOT)
    }
    assert ledger == {j1, j2}


def test_new_job_between_passes_migrates_once(tmp_path):
    """Transition window: legacy clock adds a job mid-migration."""
    db = _make_db(tmp_path)
    schedule_store.create_job(db, kind=schedule_store.KIND_SCHEDULED_SEND, due_at=100.0)
    migrate_email_clocks(db)

    schedule_store.create_job(db, kind=schedule_store.KIND_SNOOZE, due_at=150.0)
    result = migrate_email_clocks(db)
    assert len(result.migrated) == 1  # only the new job
    assert len(result.skipped) == 1
    assert len(daemon_store.list_jobs(db)) == 2


def test_fired_job_not_remigrated(tmp_path):
    """A job already migrated + fired never re-enters via a later pass."""
    db = _make_db(tmp_path)
    jid = schedule_store.create_job(
        db, kind=schedule_store.KIND_SCHEDULED_SEND, due_at=1.0
    )
    migrate_email_clocks(db)
    # Simulate the daemon firing it.
    daemon_jid = daemon_store.list_jobs(db)[0]["job_id"]
    daemon_store.claim_job(db, job_id=daemon_jid)
    daemon_store.mark_fired(db, job_id=daemon_jid)

    # The source job is still pending in schedule_store, but the ledger already
    # owns it, so a re-run must not create a second daemon job.
    result = migrate_email_clocks(db)
    assert result.migrated == ()
    assert len(daemon_store.list_jobs(db)) == 1
    assert jid in {
        r["source_job_id"] for r in daemon_store.list_ledger(db, source=SOURCE_ONE_SHOT)
    }


# ---------------------------------------------------------------------------
# Briefing migration (BriefingScheduler, #1918)
# ---------------------------------------------------------------------------


def test_enabled_briefing_migrates_once_as_recurring(tmp_path):
    db = _make_db(tmp_path)
    config = BriefingScheduleConfig(enabled=True, time_of_day="08:00")
    first = migrate_email_clocks(db, briefing_config=config)
    assert SOURCE_BRIEFING + ":" + BRIEFING_JOB_ID in first.migrated

    jobs = [j for j in daemon_store.list_jobs(db) if j["source"] == SOURCE_BRIEFING]
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "recurring"
    assert jobs[0]["interval_seconds"] == 86400

    # Idempotent.
    second = migrate_email_clocks(db, briefing_config=config)
    assert all(SOURCE_BRIEFING not in m for m in second.migrated)
    assert (
        len([j for j in daemon_store.list_jobs(db) if j["source"] == SOURCE_BRIEFING])
        == 1
    )


def test_disabled_briefing_contributes_nothing(tmp_path):
    db = _make_db(tmp_path)
    config = BriefingScheduleConfig(enabled=False)
    result = migrate_email_clocks(db, briefing_config=config)
    assert result.total == 0
    assert daemon_store.list_jobs(db) == []


# ---------------------------------------------------------------------------
# Supervision gate
# ---------------------------------------------------------------------------


def test_supervision_detected_only_on_exact_value(monkeypatch):
    monkeypatch.delenv(DAEMON_SUPERVISION_ENV_VAR, raising=False)
    assert is_daemon_supervised() is False

    monkeypatch.setenv(DAEMON_SUPERVISION_ENV_VAR, "1")
    assert is_daemon_supervised() is True

    # Any other value is NOT supervision — never silently gate a standalone run.
    monkeypatch.setenv(DAEMON_SUPERVISION_ENV_VAR, "true")
    assert is_daemon_supervised() is False
    monkeypatch.setenv(DAEMON_SUPERVISION_ENV_VAR, "")
    assert is_daemon_supervised() is False


def test_supervision_gate_uses_injected_environ():
    assert is_daemon_supervised({"GAIA_DAEMON_SUPERVISED": "1"}) is True
    assert is_daemon_supervised({}) is False
