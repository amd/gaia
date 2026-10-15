# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Schedules the user describes in local time must run in local time."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI

from gaia.ui import scheduler as scheduler_module
from gaia.ui.database import ChatDatabase
from gaia.ui.routers.schedules import router
from gaia.ui.scheduler import (
    ScheduleConfig,
    Scheduler,
    compute_next_run,
    parse_schedule_input,
    resolve_zone,
)

LA = "America/Los_Angeles"


def _utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _config(text, tz):
    config = parse_schedule_input(text)
    config.timezone = tz
    return config


@pytest.mark.parametrize(
    "text,tz,now,expected",
    [
        # The rc13 report: 02:16 PDT, "every day at 9am" fired at 09:00 UTC.
        ("every day at 9am", LA, "2026-10-15T09:16", "2026-10-15T16:00"),
        # Spring forward (8 Mar 2026): 9am PDT is 16:00Z, not 17:00Z.
        ("daily at 9am", LA, "2026-03-07T18:00", "2026-03-08T16:00"),
        # Fall back (1 Nov 2026): 9am PST is 17:00Z, not 16:00Z.
        ("daily at 9am", LA, "2026-10-31T17:00", "2026-11-01T17:00"),
        # 02:30 does not exist on 8 Mar: run just after the jump (03:30 PDT).
        ("daily at 2:30am", LA, "2026-03-08T09:00", "2026-03-08T10:30"),
        # 01:30 happens twice on 1 Nov: run the first one only...
        ("daily at 1:30am", LA, "2026-11-01T07:00", "2026-11-01T08:30"),
        # ...and once it ran, the repeat an hour later is not a second run.
        ("daily at 1:30am", LA, "2026-11-01T08:30", "2026-11-02T09:30"),
        # Weekday is the local one: Sunday 23:00Z is already Monday in Tokyo.
        ("every monday at 9am", "Asia/Tokyo", "2026-10-18T23:00", "2026-10-19T00:00"),
        # Half-hour offset zone.
        ("daily at 9am", "Asia/Kolkata", "2026-10-15T00:00", "2026-10-15T03:30"),
        # Southern hemisphere DST starts 4 Oct 2026 in Sydney (+10 -> +11).
        ("daily at 9am", "Australia/Sydney", "2026-10-03T10:00", "2026-10-03T22:00"),
        # Window evaluated locally: 18:30 PDT is past "8am to 6pm".
        ("every hour from 8am to 6pm", LA, "2026-10-15T01:30", "2026-10-15T15:00"),
        # Weekend skip is local too: Fri 17:30 PDT is Sat 00:30Z.
        (
            "every hour from 8am to 6pm on weekdays",
            LA,
            "2026-10-17T00:30",
            "2026-10-19T15:00",
        ),
        # Interval-only schedules ignore the zone entirely.
        ("every 6h", "Asia/Tokyo", "2026-10-15T09:16", "2026-10-15T15:16"),
    ],
)
def test_next_run_is_in_the_schedule_time_zone(text, tz, now, expected):
    assert compute_next_run(_config(text, tz), after=_utc(now)) == _utc(expected)


@pytest.mark.parametrize("tz", [LA, "Europe/London", "Australia/Sydney"])
def test_daily_time_holds_across_a_dst_change(tz):
    """Walk 60 consecutive runs through a DST change: always 9:00 local, once a day."""
    zone = ZoneInfo(tz)
    config = _config("daily at 9am", tz)
    after = (
        _utc("2026-02-20T00:00")
        if tz != "Australia/Sydney"
        else _utc("2026-09-01T00:00")
    )
    previous = None
    for _ in range(60):
        run = compute_next_run(config, after=after)
        local = run.astimezone(zone)
        assert (local.hour, local.minute) == (9, 0), local
        if previous is not None:
            assert local.date() - previous.date() == timedelta(days=1)
        previous = local
        after = run


def test_days_without_time_keep_the_local_clock_across_dst():
    config = _config("every day on weekdays", LA)
    # Fri 6 Mar 10:00 PST -> next is Mon 9 Mar 10:00 PDT, after the jump.
    run = compute_next_run(config, after=_utc("2026-03-06T18:00"))
    assert run.astimezone(ZoneInfo(LA)).replace(tzinfo=None) == datetime(
        2026, 3, 9, 10, 0
    )


def test_missing_zone_means_this_machine(monkeypatch):
    monkeypatch.setattr(scheduler_module, "local_timezone_name", lambda: LA)
    config = parse_schedule_input("daily at 9am")
    assert config.timezone is None
    assert compute_next_run(config, after=_utc("2026-10-15T09:16")) == _utc(
        "2026-10-15T16:00"
    )


@pytest.mark.parametrize("bad", ["Mars/Olympus", "../../etc/passwd", "PDT+1"])
def test_unknown_zone_is_rejected(bad):
    with pytest.raises(ValueError, match="Unknown time zone"):
        resolve_zone(bad)


def test_unresolvable_machine_zone_fails_loudly(monkeypatch):
    import tzlocal

    monkeypatch.setattr(tzlocal, "get_localzone_name", lambda: None)
    with pytest.raises(RuntimeError, match="TZ environment"):
        resolve_zone(None)


def test_config_round_trips_its_zone():
    config = _config("daily at 9am", LA)
    assert ScheduleConfig.from_json(config.to_json()).timezone == LA


# ── API ──────────────────────────────────────────────────────────────────────


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
async def test_api_creates_in_the_browser_zone(api):
    app, scheduler, _ = api
    async with _client(app) as client:
        resp = await client.post(
            "/api/schedules",
            json={
                "name": "brief",
                "interval": "every day at 9am",
                "prompt": "p",
                "timezone": "Asia/Kolkata",
            },
        )
    await scheduler.shutdown()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert json.loads(body["schedule_config"])["timezone"] == "Asia/Kolkata"
    local = datetime.fromisoformat(body["next_run_at"]).astimezone(
        ZoneInfo("Asia/Kolkata")
    )
    assert (local.hour, local.minute) == (9, 0)


@pytest.mark.asyncio
@pytest.mark.allow_network
async def test_api_parse_previews_local_next_run(api):
    app, _, _ = api
    async with _client(app) as client:
        resp = await client.post(
            "/api/schedules/parse",
            json={"input": "daily at 9am", "timezone": LA},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == LA
    local = datetime.fromisoformat(body["next_run_at"]).astimezone(ZoneInfo(LA))
    assert (local.hour, local.minute) == (9, 0)


@pytest.mark.asyncio
@pytest.mark.allow_network
@pytest.mark.parametrize("path", ["/api/schedules", "/api/schedules/parse"])
async def test_api_rejects_unknown_zone(api, path):
    app, scheduler, _ = api
    payload = {"input": "daily at 9am", "timezone": "Mars/Olympus"}
    if path == "/api/schedules":
        payload = {"name": "x", "interval": "daily at 9am", "prompt": "p"}
        payload["timezone"] = "Mars/Olympus"
    async with _client(app) as client:
        resp = await client.post(path, json=payload)
    await scheduler.shutdown()
    assert resp.status_code == 400
    assert "Unknown time zone" in resp.json()["detail"]
    assert scheduler.list_tasks() == []


# ── Schedules saved before time zones existed ────────────────────────────────


def _legacy_row(db, name, config, next_run_at):
    db.create_scheduled_task(
        {
            "id": f"id-{name}",
            "name": name,
            "interval_seconds": config["interval_seconds"],
            "prompt": "p",
            "status": "active",
            "created_at": "2026-10-14T09:16:40+00:00",
            "next_run_at": next_run_at,
            "run_count": 0,
            "error_count": 0,
            "session_id": None,
            "schedule_config": json.dumps(config),
        }
    )


@pytest.mark.asyncio
async def test_legacy_utc_schedule_is_moved_to_local_time(monkeypatch):
    monkeypatch.setattr(scheduler_module, "local_timezone_name", lambda: LA)
    db = ChatDatabase(":memory:")
    # The exact record from the report, minus the new key.
    legacy = {
        "interval_seconds": 86400,
        "time_of_day": "09:00",
        "start_hour": None,
        "end_hour": None,
        "days_of_week": None,
        "description": "Daily, at 9:00 AM",
        "raw_input": "every day at 9am",
    }
    _legacy_row(db, "morning-brief", legacy, "2026-10-16T09:00:00+00:00")
    interval_only = {**legacy, "time_of_day": None, "interval_seconds": 21600}
    _legacy_row(db, "six-hourly", interval_only, "2026-10-15T12:00:00+00:00")

    async def executor(_prompt):
        return "ran"

    scheduler = Scheduler(db, executor=executor)
    await scheduler._load_tasks()
    try:
        rows = {r["name"]: r for r in db.list_scheduled_tasks()}
        moved = rows["morning-brief"]
        assert json.loads(moved["schedule_config"])["timezone"] == LA
        local = datetime.fromisoformat(moved["next_run_at"]).astimezone(ZoneInfo(LA))
        assert (local.hour, local.minute) == (9, 0)

        # An elapsed-time schedule keeps its pending run untouched.
        kept = rows["six-hourly"]
        assert kept["next_run_at"] == "2026-10-15T12:00:00+00:00"
        assert json.loads(kept["schedule_config"]).get("timezone") is None
    finally:
        await scheduler.shutdown()
        db.close()
