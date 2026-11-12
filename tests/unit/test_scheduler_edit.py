# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""PATCH /api/schedules/{name}: change when a schedule runs and what it asks (#7428)."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui.database import ChatDatabase
from gaia.ui.routers.schedules import get_scheduler, router
from gaia.ui.scheduler import Scheduler


async def fake_executor(prompt: str) -> str:
    return f"ran: {prompt}"


@pytest.fixture
def setup():
    db = ChatDatabase(":memory:")
    scheduler = Scheduler(db=db, executor=fake_executor)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(scheduler.start())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_scheduler] = lambda: scheduler
    yield TestClient(app), scheduler, db
    loop.run_until_complete(scheduler.shutdown())
    loop.close()
    db.close()


def _create(client, name="report", interval="every 6h", prompt="Summarize"):
    resp = client.post(
        "/api/schedules",
        json={"name": name, "interval": interval, "prompt": prompt, "timezone": "UTC"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_edit_changes_schedule_and_prompt_and_keeps_identity(setup):
    client, _, db = setup
    before = _create(client)

    resp = client.patch(
        "/api/schedules/report",
        json={
            "interval": "daily at 9am",
            "prompt": "Write the brief",
            "timezone": "UTC",
        },
    )

    assert resp.status_code == 200, resp.text
    after = resp.json()
    assert after["id"] == before["id"]
    assert after["name"] == "report"
    assert after["prompt"] == "Write the brief"
    assert after["interval_seconds"] == 86400
    assert json.loads(after["schedule_config"])["time_of_day"] == "09:00"
    assert after["description_parts"]["kind"] == "daily"
    assert after["next_run_at"].endswith("09:00:00+00:00")
    # Persisted, so a restart reads the edit back.
    row = next(r for r in db.list_scheduled_tasks() if r["id"] == before["id"])
    assert row["prompt"] == "Write the brief"
    assert row["interval_seconds"] == 86400
    assert row["next_run_at"] == after["next_run_at"]


def test_edit_keeps_a_paused_schedule_paused(setup):
    client, _, _ = setup
    _create(client)
    client.put("/api/schedules/report", json={"status": "paused"})

    after = client.patch(
        "/api/schedules/report", json={"interval": "every 2h", "prompt": "P"}
    ).json()

    assert after["status"] == "paused"
    assert after["next_run_at"] is None
    assert after["interval_seconds"] == 7200


def test_edit_restarts_the_timer_of_an_active_schedule(setup):
    client, scheduler, _ = setup
    _create(client)
    old_timer = scheduler._tasks["report"]._timer_task

    client.patch("/api/schedules/report", json={"interval": "every 2h", "prompt": "P"})

    task = scheduler._tasks["report"]
    assert old_timer is not None and old_timer.done()
    assert task._timer_task is not None and task._timer_task is not old_timer


def test_edit_rejects_a_schedule_it_cannot_read(setup):
    client, _, _ = setup
    _create(client)

    resp = client.patch(
        "/api/schedules/report", json={"interval": "whenever", "prompt": "P"}
    )

    assert resp.status_code == 400
    assert client.get("/api/schedules/report").json()["interval_seconds"] == 21600


def test_edit_rejects_an_empty_prompt(setup):
    client, _, _ = setup
    _create(client)

    resp = client.patch(
        "/api/schedules/report", json={"interval": "every 2h", "prompt": "   "}
    )

    assert resp.status_code == 400
    assert "prompt is empty" in resp.json()["detail"]


def test_edit_refuses_while_a_run_is_in_flight(setup):
    client, scheduler, _ = setup
    _create(client)
    scheduler._tasks["report"]._executing = True
    try:
        resp = client.patch(
            "/api/schedules/report", json={"interval": "every 2h", "prompt": "P"}
        )
        assert resp.status_code == 400
        assert "running now" in resp.json()["detail"]
    finally:
        scheduler._tasks["report"]._executing = False


def test_edit_unknown_schedule_is_404(setup):
    client, _, _ = setup
    resp = client.patch(
        "/api/schedules/ghost", json={"interval": "every 2h", "prompt": "P"}
    )
    assert resp.status_code == 404
