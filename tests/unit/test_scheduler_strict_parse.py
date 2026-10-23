# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The schedule parser reads all of the input or rejects it with what to fix."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from gaia.ui.database import ChatDatabase
from gaia.ui.routers.schedules import router
from gaia.ui.scheduler import (
    ScheduleConfig,
    Scheduler,
    compute_next_run,
    parse_schedule_input,
)

LA = "America/Los_Angeles"
#: Thursday 22 Oct 2026, 10:00 PDT.
NOW = datetime(2026, 10, 22, 17, 0, tzinfo=timezone.utc)
WEEKDAYS = [0, 1, 2, 3, 4]


def _utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _parse(text, tz=LA):
    config = parse_schedule_input(text)
    config.timezone = tz
    return config


@pytest.mark.parametrize(
    "text,interval,time_of_day,days,window,next_run",
    [
        # Tue 27 Oct is the next Tuesday, not Tue 3 Nov.
        ("every tuesday", 604800, None, [1], None, "2026-10-27T17:00"),
        ("every tuesday at 9am", 604800, "09:00", [1], None, "2026-10-27T16:00"),
        ("every other day", 172800, None, None, None, "2026-10-24T17:00"),
        ("every 2 weeks", 1209600, None, None, None, "2026-11-05T17:00"),
        ("every 1m", 60, None, None, None, "2026-10-22T17:01"),
        ("every 90m", 5400, None, None, None, "2026-10-22T18:30"),
        ("daily at 9pm", 86400, "21:00", None, None, "2026-10-23T04:00"),
        # 10am is not still ahead at 10:00, so the first run is Friday.
        ("weekdays at 10am", 86400, "10:00", WEEKDAYS, None, "2026-10-23T17:00"),
        ("every weekday", 86400, None, WEEKDAYS, None, "2026-10-23T17:00"),
        (
            "every hour from 8am to 6pm on weekdays",
            3600,
            None,
            WEEKDAYS,
            (8, 18),
            "2026-10-22T18:00",
        ),
    ],
)
def test_valid_schedules_read_exactly(
    text, interval, time_of_day, days, window, next_run
):
    config = _parse(text)
    assert config.interval_seconds == interval, config.description
    assert config.time_of_day == time_of_day
    assert config.days_of_week == days
    assert (config.start_hour, config.end_hour) == (window or (None, None))
    assert compute_next_run(config, after=NOW) == _utc(next_run)


@pytest.mark.parametrize(
    "text,message",
    [
        ("every other tuesday at 25:00", "25:00 isn't a time — use 0:00–23:59."),
        ("daily at 25:00", "25:00 isn't a time — use 0:00–23:59."),
        ("daily at 9:75", "9:75 isn't a time — use 0:00–23:59."),
        ("daily at 13pm", "13pm isn't a time — use 1–12 with am or pm"),
        ("at 9", "'9' could be morning or evening — say 9am, 9pm or 9:00."),
        ("daily 9am", "Put 'at' before the time, like 'daily at 9am'."),
        ("every other tuesday", "'every other tuesday' isn't supported yet"),
        ("tomorrow at noon", "One-time tasks aren't supported yet"),
        ("next friday", "One-time tasks aren't supported yet"),
        ("in 5 minutes", "One-time tasks aren't supported yet"),
        ("on 10/25 at 9am", "One-time tasks aren't supported yet"),
        ("on feb 30 at 9am", "February 30 isn't a date."),
        ("every month", "Monthly and yearly schedules aren't supported yet"),
        ("every 1s", "'every 1s' is too often"),
        ("every second", "at most once a minute"),
        ("every 2 days at 9am", "can't be combined with a time of day"),
        ("every 2 weeks on monday", "can't be combined with set days"),
        ("weekly at 9am", "Say which day to run on"),
        ("every 7d at 9am", "Say which day to run on"),
        ("daily at 9am and at 5pm", "Give one time per schedule"),
        ("every 30m from 9:30am to 5pm", "starts and ends on the hour"),
        ("from 8am to 6pm", "Say how often to run inside the window"),
        ("every hour from 8am to 6pm at 9am", "not both"),
        ("every day sometimes", "GAIA couldn't read 'sometimes'"),
        ("every 400 weeks", "too far apart"),
        ("   ", "Describe when to run it"),
    ],
)
def test_unreadable_schedules_are_rejected(text, message):
    config = parse_schedule_input(text)
    assert config.interval_seconds == 0
    assert message in config.description


@pytest.mark.parametrize(
    "text,now,expected",
    [
        # Same weekday, time still ahead: today.
        ("every tuesday at 9am", "2026-10-20T08:00", "2026-10-20T09:00"),
        # Same weekday, at or after the time: next week.
        ("every tuesday at 9am", "2026-10-20T09:00", "2026-10-27T09:00"),
        ("every tuesday at 9am", "2026-10-20T10:00", "2026-10-27T09:00"),
        # Across the Sunday -> Monday week boundary.
        ("every monday at 9am", "2026-10-25T22:00", "2026-10-26T09:00"),
        ("weekdays at 9am", "2026-10-23T10:00", "2026-10-26T09:00"),
        ("every sunday at 9am", "2026-10-26T08:00", "2026-11-01T09:00"),
        # No time given: the next listed day at the current clock time.
        ("every tuesday", "2026-10-19T23:59", "2026-10-20T23:59"),
        ("every tuesday", "2026-10-20T08:00", "2026-10-27T08:00"),
        ("every monday", "2026-10-25T10:00", "2026-10-26T10:00"),
        ("every monday and wednesday", "2026-10-26T10:00", "2026-10-28T10:00"),
    ],
)
def test_weekday_next_run(text, now, expected):
    config = _parse(text, tz="UTC")
    assert compute_next_run(config, after=_utc(now)) == _utc(expected)


def test_weekly_day_without_time_steps_one_week():
    config = _parse("every tuesday", tz="UTC")
    run = compute_next_run(config, after=_utc("2026-10-22T10:00"))
    for _ in range(4):
        following = compute_next_run(config, after=run)
        assert following - run == timedelta(days=7)
        run = following


# ── API and saved schedules ──────────────────────────────────────────────────


@pytest.fixture
def api():
    db = ChatDatabase(":memory:")

    async def executor(_prompt):
        raise AssertionError("not started")

    scheduler = Scheduler(db, executor=executor)
    app = FastAPI()
    app.include_router(router)
    app.state.scheduler = scheduler
    yield app, scheduler, db
    db.close()


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.mark.asyncio
@pytest.mark.allow_network
async def test_api_reports_why_a_schedule_is_rejected(api):
    app, scheduler, _ = api
    async with _client(app) as client:
        parsed = await client.post(
            "/api/schedules/parse", json={"input": "tomorrow at noon"}
        )
        created = await client.post(
            "/api/schedules",
            json={"name": "lunch", "interval": "tomorrow at noon", "prompt": "p"},
        )
    await scheduler.shutdown()
    assert parsed.status_code == 200
    assert parsed.json()["valid"] is False
    assert "One-time tasks" in parsed.json()["description"]
    assert created.status_code == 400
    assert "One-time tasks" in created.json()["detail"]
    assert scheduler.list_tasks() == []


@pytest.mark.asyncio
async def test_saved_schedules_the_parser_now_rejects_still_load():
    """Rows keep their stored config; the stricter parser only gates new ones."""
    db = ChatDatabase(":memory:")
    old = {
        "every-2-days-9am": ScheduleConfig(
            interval_seconds=172800,
            time_of_day="09:00",
            raw_input="every 2 days at 9am",
            timezone="UTC",
        ),
        "every-second": ScheduleConfig(interval_seconds=1, raw_input="every 1s"),
    }
    for name, config in old.items():
        db.create_scheduled_task(
            {
                "id": f"id-{name}",
                "name": name,
                "interval_seconds": config.interval_seconds,
                "prompt": "p",
                "status": "active",
                "created_at": "2026-10-14T09:16:40+00:00",
                "next_run_at": "2026-10-23T09:00:00+00:00",
                "run_count": 0,
                "error_count": 0,
                "session_id": None,
                "schedule_config": config.to_json(),
            }
        )

    async def executor(_prompt):
        return "ran"

    scheduler = Scheduler(db, executor=executor)
    await scheduler._load_tasks()
    try:
        tasks = {t["name"]: t for t in scheduler.list_tasks()}
        assert tasks["every-second"]["interval_seconds"] == 1
        stored = json.loads(tasks["every-2-days-9am"]["schedule_config"])
        assert stored["time_of_day"] == "09:00"
        assert compute_next_run(
            ScheduleConfig(**stored), after=_utc("2026-10-23T09:00")
        ) == _utc("2026-10-24T09:00")
    finally:
        await scheduler.shutdown()
        db.close()


@pytest.mark.parametrize(
    "text,time_of_day,days",
    [
        ("every morning at 8", "08:00", None),
        ("every evening at 7", "19:00", None),
        ("every afternoon at 3", "15:00", None),
        ("every night at 10", "22:00", None),
        ("every night at 1", "01:00", None),
        ("daily at 8 in the morning", "08:00", None),
        ("every day at 9:30 in the evening", "21:30", None),
        ("at 10 at night", "22:00", None),
        ("every weekday morning at 8", "08:00", WEEKDAYS),
        ("every morning at 8am", "08:00", None),
        ("every evening at 19:00", "19:00", None),
    ],
)
def test_morning_or_evening_settles_am_or_pm(text, time_of_day, days):
    """A user who says "morning" has already said which 8 they mean."""
    config = _parse(text)
    assert config.interval_seconds > 0, config.description
    assert config.time_of_day == time_of_day
    assert config.days_of_week == days


@pytest.mark.parametrize(
    "text,message",
    [
        ("every morning at 8pm", "'morning' and '8pm' disagree"),
        ("every evening at 7am", "'evening' and '7am' disagree"),
        ("every morning", "Say what time in the morning, like 'every morning at 8'."),
    ],
)
def test_a_time_of_day_word_that_cant_settle_the_time_says_why(text, message):
    config = _parse(text)
    assert config.interval_seconds == 0
    assert message in config.description
