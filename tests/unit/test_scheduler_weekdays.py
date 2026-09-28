# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The displayed schedule and its actual timer must honor the same days."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from gaia.ui import scheduler as scheduler_module
from gaia.ui.database import ChatDatabase
from gaia.ui.routers.schedules import router
from gaia.ui.scheduler import Scheduler

pytestmark = [pytest.mark.asyncio, pytest.mark.allow_network]


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        instant = datetime(2026, 9, 12, 10, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.instant

    monkeypatch.setattr(scheduler_module, "datetime", Clock)
    return Clock


@pytest.mark.parametrize(
    "schedule,start,expected",
    [
        ("every hour on weekdays", "2026-09-11T23:30", "2026-09-14T00:30"),
        ("every hour on weekdays", "2026-09-12T10:00", "2026-09-14T11:00"),
        ("every hour on weekdays", "2026-09-10T10:00", "2026-09-10T11:00"),
        ("every hour on weekends", "2026-09-14T10:00", "2026-09-19T11:00"),
        ("every hour", "2026-09-12T10:00", "2026-09-12T11:00"),
        ("daily at 9am", "2026-09-12T10:00", "2026-09-13T09:00"),
    ],
)
async def test_timer_fires_when_the_schedule_says(
    clock, monkeypatch, schedule, start, expected
):
    clock.instant = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    expected_time = datetime.fromisoformat(expected).replace(tzinfo=timezone.utc)
    db = ChatDatabase(":memory:")
    fired = []

    async def executor(prompt):
        fired.append((clock.now(), prompt))
        scheduler._running = False
        return "ran"

    async def advance(seconds):
        clock.instant += timedelta(seconds=seconds)

    scheduler = Scheduler(db, executor=executor)
    monkeypatch.setattr(scheduler_module.asyncio, "sleep", advance)
    try:
        created = await scheduler.create_task("test", schedule, "scheduled prompt")
        assert datetime.fromisoformat(created["next_run_at"]) == expected_time
        await scheduler.start()
        await scheduler.tasks["test"]._timer_task
        assert fired == [(expected_time, "scheduled prompt")]
        assert scheduler.get_task("test")["run_count"] == 1
        assert scheduler.get_task_results("test")[0]["result"] == "ran"
    finally:
        await scheduler.shutdown()
        db.close()


async def test_http_resume_keeps_weekday_constraint(clock):
    db = ChatDatabase(":memory:")

    async def executor(_prompt):
        raise AssertionError("The scheduler is not started in this API test")

    scheduler = Scheduler(db, executor=executor)
    app = FastAPI()
    app.include_router(router)
    app.state.scheduler = scheduler
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            created = await client.post(
                "/api/schedules",
                json={
                    "name": "weekdays",
                    "interval": "every hour on weekdays",
                    "prompt": "test",
                },
            )
            assert created.status_code == 200
            paused = await client.put(
                "/api/schedules/weekdays", json={"status": "paused"}
            )
            assert paused.status_code == 200
            resumed = await client.put(
                "/api/schedules/weekdays", json={"status": "active"}
            )
            assert resumed.status_code == 200
            assert resumed.json()["next_run_at"] == created.json()["next_run_at"]
            assert resumed.json()["next_run_at"] == "2026-09-14T11:00:00+00:00"
    finally:
        await scheduler.shutdown()
        db.close()
