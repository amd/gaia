# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A schedule's description comes as data too, so the Agent UI can word it."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui.routers.schedules import router
from gaia.ui.scheduler import (
    ScheduledTask,
    describe_parts,
    parse_schedule_input,
)

WEEKDAYS = [0, 1, 2, 3, 4]


def _every(count, unit, days=None):
    return {
        "kind": "every",
        "count": count,
        "unit": unit,
        "days": days,
        "time": None,
        "start_hour": None,
        "end_hour": None,
    }


def _window(count, unit, start, end, days=None):
    parts = _every(count, unit, days)
    return {**parts, "kind": "window", "start_hour": start, "end_hour": end}


def _days(days, time=None):
    return {
        "kind": "days",
        "count": None,
        "unit": None,
        "days": days,
        "time": time,
        "start_hour": None,
        "end_hour": None,
    }


def _daily(time):
    return {**_days(None, time), "kind": "daily"}


#: One input per description shape the parser produces, with its English.
SHAPES = [
    ("every 2m", _every(2, "minute"), "Every 2 minutes"),
    ("every 1m", _every(1, "minute"), "Every minute"),
    ("every 90s", _every(90, "second"), "Every 90 seconds"),
    ("every 90m", _every(90, "minute"), "Every 90 minutes"),
    ("hourly", _every(1, "hour"), "Every hour"),
    ("every 3 hours", _every(3, "hour"), "Every 3 hours"),
    ("daily", _every(1, "day"), "Every day"),
    ("every other day", _every(2, "day"), "Every 2 days"),
    ("weekly", _every(1, "week"), "Every week"),
    ("every 2 weeks", _every(2, "week"), "Every 2 weeks"),
    (
        "every 2 hours on weekdays",
        _every(2, "hour", WEEKDAYS),
        "Every 2 hours on weekdays",
    ),
    ("daily at 9pm", _daily("21:00"), "Daily at 9:00 PM"),
    ("every weekday", _days(WEEKDAYS), "Weekdays"),
    ("weekdays at 10am", _days(WEEKDAYS, "10:00"), "Weekdays at 10:00 AM"),
    ("weekends at 9:30am", _days([5, 6], "09:30"), "Weekends at 9:30 AM"),
    ("mon-sun at 7am", _days(list(range(7)), "07:00"), "Daily at 7:00 AM"),
    ("every tuesday", _days([1]), "Every Tuesday"),
    ("every tuesday at 9am", _days([1], "09:00"), "Every Tuesday at 9:00 AM"),
    (
        "every monday and wednesday at noon",
        _days([0, 2], "12:00"),
        "Every Monday, Wednesday at 12:00 PM",
    ),
    (
        "every hour from 8am to 6pm on weekdays",
        _window(1, "hour", 8, 18, WEEKDAYS),
        "Every hour, 8:00 AM - 6:00 PM, weekdays",
    ),
    (
        "every 30m from 9am to 5pm",
        _window(30, "minute", 9, 17),
        "Every 30 minutes, 9:00 AM - 5:00 PM",
    ),
]


@pytest.mark.parametrize("text,parts,english", SHAPES)
def test_every_shape_has_parts_and_keeps_its_english(text, parts, english):
    config = parse_schedule_input(text)
    assert config.interval_seconds > 0, config.description
    assert describe_parts(config) == parts
    assert config.description == english


def test_shapes_cover_every_kind_and_unit():
    covered = [p for _, p, _ in SHAPES if p]
    assert {p["kind"] for p in covered} == {"every", "window", "days", "daily"}
    assert {p["unit"] for p in covered if p["unit"]} == {
        "second",
        "minute",
        "hour",
        "day",
        "week",
    }


def test_rejected_input_has_no_parts():
    with pytest.raises(ValueError, match="isn't a time"):
        describe_parts(parse_schedule_input("daily at 25:00"))


def test_plain_interval_task_has_parts():
    task = ScheduledTask("id", "legacy", 86400, "P", schedule_config=None)
    data = task.to_dict()
    assert data["description"] == "Every day"
    assert data["description_parts"] == _every(1, "day")


def test_stored_task_has_parts():
    config = parse_schedule_input("weekdays at 8am")
    task = ScheduledTask("id", "brief", 86400, "P", schedule_config=config.to_json())
    assert task.to_dict()["description_parts"] == _days(WEEKDAYS, "08:00")


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_parse_endpoint_returns_parts(client):
    body = client.post("/api/schedules/parse", json={"input": "every 2m"}).json()
    assert body["description"] == "Every 2 minutes"
    assert body["description_parts"] == _every(2, "minute")


def test_parse_endpoint_has_no_parts_for_rejected_input(client):
    body = client.post("/api/schedules/parse", json={"input": "tomorrow"}).json()
    assert body["valid"] is False
    assert body["description_parts"] is None
