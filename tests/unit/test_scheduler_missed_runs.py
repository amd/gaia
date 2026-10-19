# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A scheduled task that could not run says so in its history.

Runs that came due while the PC was off, asleep, or GAIA was closed used to
vanish: the timer restarted from scratch on the next start (or woke late and
ran once), and the history showed nothing for the runs that never happened.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from gaia.ui import scheduler as scheduler_module
from gaia.ui.database import ChatDatabase
from gaia.ui.scheduler import ScheduleConfig, Scheduler, parse_schedule_input

pytestmark = pytest.mark.asyncio


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


def _row(db, *, name, interval, next_run_at, config=None):
    db.create_scheduled_task(
        {
            "id": str(uuid.uuid4()),
            "name": name,
            "interval_seconds": interval,
            "prompt": "Summarize my inbox",
            "status": "active",
            "created_at": (next_run_at - timedelta(days=30)).isoformat(),
            "next_run_at": next_run_at.isoformat(),
            "run_count": 0,
            "error_count": 0,
            "session_id": None,
            "schedule_config": config.to_json() if config else None,
        }
    )


class _Recorder:
    def __init__(self):
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return "done"


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0.01)


async def test_runs_missed_while_the_pc_was_off_show_in_the_history(db):
    now = datetime.now(timezone.utc)
    latest = (now - timedelta(hours=2)).replace(second=0, microsecond=0)
    config = ScheduleConfig(
        interval_seconds=86400, time_of_day=latest.strftime("%H:%M"), timezone="UTC"
    )
    # Three daily runs came due while GAIA was closed, the last 2 hours ago.
    first_due = latest - timedelta(days=2)
    _row(db, name="inbox", interval=86400, next_run_at=first_due, config=config)

    executor = _Recorder()
    sched = Scheduler(db=db, executor=executor)
    await sched.start()
    try:
        await _settle()
        results = sched.get_task_results("inbox")
        task = sched.get_task("inbox")
    finally:
        await sched.shutdown()

    assert executor.prompts == [], "a run hours late must not fire on its own"
    assert len(results) == 1
    missed = results[0]
    assert missed["result"] is None
    assert "while the PC was off or asleep" in missed["error"]
    assert missed["error"].startswith("Missed 3 runs")
    assert datetime.fromisoformat(task["next_run_at"]) > now


async def test_a_run_that_just_came_due_runs_and_earlier_ones_are_reported(db):
    now = datetime.now(timezone.utc)
    # Every 10 minutes; GAIA was closed for 25 minutes and is back 1 minute
    # after the latest due time.
    _row(
        db,
        name="poll",
        interval=600,
        next_run_at=now - timedelta(minutes=21),
    )
    executor = _Recorder()
    sched = Scheduler(db=db, executor=executor)
    await sched.start()
    try:
        await _settle()
        results = sched.get_task_results("poll")
    finally:
        await sched.shutdown()

    assert executor.prompts == ["Summarize my inbox"]
    errors = [r["error"] for r in results if r["error"]]
    assert len(errors) == 1 and errors[0].startswith("Missed 2 runs")
    assert [r["result"] for r in results if r["result"]] == ["done"]


async def test_a_restart_before_the_run_is_due_keeps_its_time(db):
    """Restarting more often than the interval used to mean it never ran."""
    now = datetime.now(timezone.utc)
    _row(db, name="soon", interval=86400, next_run_at=now + timedelta(seconds=0.3))
    executor = _Recorder()
    sched = Scheduler(db=db, executor=executor)
    await sched.start()
    try:
        await asyncio.sleep(1.0)
    finally:
        await sched.shutdown()
    assert executor.prompts == ["Summarize my inbox"]


async def test_a_pc_that_sleeps_through_a_run_reports_it_on_waking(db, monkeypatch):
    clock = {"now": datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(scheduler_module, "_utcnow", lambda: clock["now"])
    monkeypatch.setattr(scheduler_module, "WAKE_CHECK_SECONDS", 0.02)
    config = parse_schedule_input("daily at 9am")
    config.timezone = "UTC"
    _row(
        db,
        name="brief",
        interval=86400,
        next_run_at=datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc),
        config=config,
    )
    executor = _Recorder()
    sched = Scheduler(db=db, executor=executor)
    await sched.start()
    try:
        await asyncio.sleep(0.1)
        assert sched.get_task_results("brief") == []
        # The PC sleeps at 08:00 and wakes at 11:30.
        clock["now"] = datetime(2026, 10, 18, 11, 30, tzinfo=timezone.utc)
        await asyncio.sleep(0.2)
        results = sched.get_task_results("brief")
        task = sched.get_task("brief")
    finally:
        await sched.shutdown()

    assert executor.prompts == []
    assert len(results) == 1
    assert results[0]["error"].startswith("Missed the run due 2026-10-18 09:00 UTC")
    assert task["next_run_at"] == "2026-10-19T09:00:00+00:00"
