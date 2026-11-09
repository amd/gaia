# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for unattended scheduled-email failure notices (#5411)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent_email")

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia_agent_email import schedule_store  # noqa: E402
from gaia_agent_email.failure_notify import notify_scheduled_failure  # noqa: E402
from gaia_agent_email.scheduler import EmailJobScheduler  # noqa: E402

from gaia.database.mixin import DatabaseMixin  # noqa: E402


class _DB(DatabaseMixin):
    pass


def _db(tmp_path: Path) -> _DB:
    db = _DB()
    db.init_db(str(tmp_path / "state.db"))
    schedule_store.init_schema(db)
    return db


def test_failure_notice_uses_existing_notification_sink():
    job = {"job_id": "j1", "kind": "scheduled_send", "payload": {"subject": "Q3"}}
    with patch("gaia.schedule.sinks.dispatch") as dispatch:
        notify_scheduled_failure(job, "RuntimeError: provider down", source="test")
    dispatch.assert_called_once_with(
        "notification",
        {"title": "Scheduled email failed"},
        "GAIA could not send the scheduled email (Q3). RuntimeError: provider down",
    )


def test_failure_notice_reads_subject_from_migrated_daemon_payload():
    job = {
        "job_id": "j1",
        "kind": "one_shot",
        "payload": {
            "kind": "scheduled_send",
            "payload": {"subject": "Q3"},
        },
    }
    with patch("gaia.schedule.sinks.dispatch") as dispatch:
        notify_scheduled_failure(job, "provider down", source="daemon")
    dispatch.assert_called_once_with(
        "notification",
        {"title": "Scheduled email failed"},
        "GAIA could not send the scheduled email (Q3). provider down",
    )


def test_snooze_failure_is_not_reported_as_email_send():
    job = {"job_id": "j1", "kind": "snooze", "payload": {}}
    with patch("gaia.schedule.sinks.dispatch") as dispatch:
        notify_scheduled_failure(job, "RuntimeError: provider down", source="test")
    dispatch.assert_called_once_with(
        "notification",
        {"title": "Snoozed email restore failed"},
        "GAIA could not restore the snoozed email. RuntimeError: provider down",
    )


def test_recovery_notice_warns_that_a_send_may_have_happened():
    job = {"job_id": "j1", "kind": "scheduled_send", "payload": {"subject": "Q3"}}
    with patch("gaia.schedule.sinks.dispatch") as dispatch:
        notify_scheduled_failure(
            job,
            "The send was interrupted while firing and may already have been sent; "
            "check your Sent folder before retrying.",
            source="email scheduler recovery",
        )
    dispatch.assert_called_once_with(
        "notification",
        {"title": "Scheduled email may not have been sent"},
        "GAIA could not confirm the scheduled email (Q3) was sent. It may already "
        "have been sent; check your Sent folder before retrying. The send was "
        "interrupted while firing and may already have been sent; check your Sent "
        "folder before retrying.",
    )


def test_notification_failure_never_escapes():
    job = {"job_id": "j1", "kind": "scheduled_send", "payload": {}}
    with patch("gaia.schedule.sinks.dispatch", side_effect=RuntimeError("no desktop")):
        notify_scheduled_failure(job, "provider down", source="test")


def test_recovered_holder_failure_does_not_notify_twice(tmp_path):
    db = _db(tmp_path)
    job_id = schedule_store.create_job(
        db, kind="scheduled_send", due_at=1.0, payload={"subject": "Q3"}
    )

    def late_failure(_job, scheduler_db):
        current = schedule_store.get_job(scheduler_db, job_id=job_id)
        assert current is not None
        assert (
            schedule_store.recover_claim(scheduler_db, current, now=1000.0) == "failed"
        )
        raise RuntimeError("late failure")

    scheduler = EmailJobScheduler(
        str(tmp_path / "state.db"), executors={"scheduled_send": late_failure}
    )
    with patch("gaia_agent_email.scheduler.notify_scheduled_failure") as notify:
        assert scheduler.fire_due_jobs(now=2.0) == {
            "fired": [],
            "failed": [job_id],
        }
    notify.assert_not_called()
    assert schedule_store.get_job(db, job_id=job_id)["status"] == "failed"
    assert "late failure" in schedule_store.get_job(db, job_id=job_id)["error"]


def test_executor_failure_persists_then_notifies(tmp_path):
    db = _db(tmp_path)
    job_id = schedule_store.create_job(
        db, kind="scheduled_send", due_at=1.0, payload={"subject": "Q3"}
    )
    scheduler = EmailJobScheduler(
        str(tmp_path / "state.db"),
        executors={
            "scheduled_send": lambda _job, _db: (_ for _ in ()).throw(
                RuntimeError("boom")
            )
        },
    )
    with patch("gaia_agent_email.scheduler.notify_scheduled_failure") as notify:
        assert scheduler.fire_due_jobs(now=2.0) == {"fired": [], "failed": [job_id]}
    notify.assert_called_once()
    assert "RuntimeError: boom" in notify.call_args.args[1]
    assert schedule_store.get_job(db, job_id=job_id)["status"] == "failed"


def test_missing_executor_persists_then_notifies(tmp_path):
    db = _db(tmp_path)
    job_id = schedule_store.create_job(
        db, kind="scheduled_send", due_at=1.0, payload={"subject": "Q3"}
    )
    scheduler = EmailJobScheduler(str(tmp_path / "state.db"), executors={})
    with patch("gaia_agent_email.scheduler.notify_scheduled_failure") as notify:
        assert scheduler.fire_due_jobs(now=2.0) == {"fired": [], "failed": [job_id]}
    notify.assert_called_once()
    assert "no executor" in notify.call_args.args[1]
    assert schedule_store.get_job(db, job_id=job_id)["status"] == "failed"
