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

#: scheduler.MISSED_SINCE_SETTING, spelled out so these tests also run against
#: a build without it.
MISSED_SINCE = "scheduler_missed_runs_since"


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    # This build has been counting missed runs since well before these tests.
    database.set_setting(
        MISSED_SINCE,
        (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
    )
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


async def test_two_servers_on_one_database_run_a_due_run_once(tmp_path):
    """Both wake for the same due time; one claims it, the other stands down."""
    path = str(tmp_path / "chat.db")
    first, second = ChatDatabase(path), ChatDatabase(path)
    first.set_setting(
        MISSED_SINCE,
        (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
    )
    now = datetime.now(timezone.utc)
    _row(first, name="poll", interval=600, next_run_at=now - timedelta(minutes=21))
    a, b = _Recorder(), _Recorder()
    servers = [Scheduler(db=first, executor=a), Scheduler(db=second, executor=b)]
    for server in servers:
        await server.start()
    try:
        await _settle()
        results = servers[0].get_task_results("poll")
    finally:
        for server in servers:
            await server.shutdown()
        first.close()
        second.close()
    assert len(a.prompts) + len(b.prompts) == 1
    assert len([r for r in results if r["error"]]) == 1


async def test_a_task_saved_by_an_older_build_is_not_blamed_on_the_pc(monkeypatch):
    """The first start of this build counts nothing before itself: an older
    build reset a task's timer on every restart, so its stale next run time
    says nothing about the PC being off."""
    db = ChatDatabase(":memory:")
    try:
        now = datetime.now(timezone.utc)
        _row(db, name="legacy", interval=3600, next_run_at=now - timedelta(days=3))
        executor = _Recorder()
        sched = Scheduler(db=db, executor=executor)
        await sched.start()
        try:
            await _settle()
            results = sched.get_task_results("legacy")
            task = sched.get_task("legacy")
        finally:
            await sched.shutdown()
        assert db.get_setting(MISSED_SINCE) is not None
    finally:
        db.close()
    assert [r for r in results if r["error"]] == []
    assert datetime.fromisoformat(task["next_run_at"]) > now


async def test_a_stretch_past_the_count_cap_still_runs_its_latest_due_run(
    db, monkeypatch
):
    """Past the walking cap the latest due time is found, not the cap's last."""
    monkeypatch.setattr(scheduler_module, "_MAX_MISSED_COUNTED", 3)
    now = datetime.now(timezone.utc)
    latest = (now - timedelta(minutes=30)).replace(second=0, microsecond=0)
    config = ScheduleConfig(
        interval_seconds=86400, time_of_day=latest.strftime("%H:%M"), timezone="UTC"
    )
    _row(
        db,
        name="brief",
        interval=86400,
        next_run_at=latest - timedelta(days=10),
        config=config,
    )
    executor = _Recorder()
    sched = Scheduler(db=db, executor=executor)
    await sched.start()
    try:
        await _settle()
        results = sched.get_task_results("brief")
    finally:
        await sched.shutdown()
    assert executor.prompts == ["Summarize my inbox"]
    error = [r["error"] for r in results if r["error"]][0]
    assert error.startswith("Missed at least 3 runs")
    day_before = (latest - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    assert f"to {day_before} UTC" in error


async def test_an_interval_stretch_is_counted_exactly(db):
    """Every second for three days off: far past the walking cap, counted by
    arithmetic, and the run due a moment ago still runs."""
    now = datetime.now(timezone.utc)
    _row(db, name="tick", interval=1, next_run_at=now - timedelta(days=3))
    executor = _Recorder()
    sched = Scheduler(db=db, executor=executor)
    await sched.start()
    try:
        await _settle()
        results = sched.get_task_results("tick")
    finally:
        await sched.shutdown()
    assert executor.prompts[:1] == ["Summarize my inbox"]
    error = [r["error"] for r in results if r["error"]][0]
    count = int(error.split()[1])
    assert abs(count - 3 * 86400) <= 2, error


async def test_the_missed_since_key_is_the_schedulers():
    assert scheduler_module.MISSED_SINCE_SETTING == MISSED_SINCE


async def test_an_old_dense_window_task_first_seen_outside_its_window(db, monkeypatch):
    """An every-minute 9-to-5 task an older build left 240 days back, met by
    this build's first start in the evening: nothing before the first start
    is blamed, and nothing crashes."""
    monkeypatch.setattr(scheduler_module, "_MAX_MISSED_COUNTED", 50)
    now = datetime(2026, 10, 19, 20, 0, tzinfo=timezone.utc)
    config = ScheduleConfig(
        interval_seconds=60, start_hour=9, end_hour=17, timezone="UTC"
    )
    sched = Scheduler(db=db, executor=_Recorder())
    sched._missed_since = now - timedelta(minutes=1)  # this build's first start
    due = now - timedelta(days=240)
    task = scheduler_module.ScheduledTask(
        task_id="t1",
        name="w",
        interval_seconds=60,
        prompt="p",
        created_at=(due - timedelta(days=1)).isoformat(),
        next_run_at=due.isoformat(),
        schedule_config=config.to_json(),
    )
    plan = sched._plan(task, due, now)
    assert plan.missed == 0
    assert plan.run_now is False


async def test_a_bad_pass_is_logged_and_the_schedule_keeps_running(
    db, monkeypatch, caplog
):
    monkeypatch.setattr(scheduler_module, "WAKE_CHECK_SECONDS", 0.05)
    now = datetime.now(timezone.utc)
    _row(db, name="tick", interval=3600, next_run_at=now - timedelta(seconds=1))
    executor = _Recorder()
    sched = Scheduler(db=db, executor=executor)
    real_plan = Scheduler._plan
    calls = []

    def flaky_plan(self, *args):
        calls.append(1)
        if len(calls) == 1:
            raise AttributeError("'NoneType' object has no attribute 'astimezone'")
        return real_plan(self, *args)

    monkeypatch.setattr(Scheduler, "_plan", flaky_plan)
    caplog.set_level("ERROR", logger=scheduler_module.logger.name)
    await sched.start()
    try:
        for _ in range(100):
            if executor.prompts:
                break
            await asyncio.sleep(0.02)
    finally:
        await sched.shutdown()
    assert "could not handle its run due" in caplog.text
    assert executor.prompts == ["Summarize my inbox"]


async def test_a_pause_on_one_server_is_not_undone_by_the_other(tmp_path):
    path = str(tmp_path / "chat.db")
    first, second = ChatDatabase(path), ChatDatabase(path)
    first.set_setting(
        MISSED_SINCE, (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    )
    now = datetime.now(timezone.utc)
    _row(first, name="t", interval=1, next_run_at=now + timedelta(seconds=0.5))
    a, b = _Recorder(), _Recorder()
    servers = [Scheduler(db=first, executor=a), Scheduler(db=second, executor=b)]
    for server in servers:
        await server.start()
    try:
        await servers[0].pause_task("t")  # the user pauses it in one window
        await asyncio.sleep(2.0)
    finally:
        for server in servers:
            await server.shutdown()
        status = [r["status"] for r in first.list_scheduled_tasks()]
        first.close()
        second.close()
    assert a.prompts == [] and b.prompts == []
    assert status == ["paused"]
