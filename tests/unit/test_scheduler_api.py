# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""REST API tests for the GAIA Scheduler endpoints (M5: Scheduled Autonomy).

Tests the /api/schedules/* endpoints using FastAPI TestClient.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui.database import ChatDatabase
from gaia.ui.routers.schedules import get_scheduler, router
from gaia.ui.scheduler import ScheduledTask, Scheduler, parse_schedule_input

# ── Fixtures ──────────────────────────────────────────────────────────────────


async def fake_executor(prompt: str) -> str:
    """Minimal executor for API tests."""
    return f"ran: {prompt}"


@pytest.fixture
def app_with_scheduler():
    """Create a FastAPI app with scheduler for testing."""
    db = ChatDatabase(":memory:")
    scheduler = Scheduler(db=db, executor=fake_executor)

    # Run scheduler start in event loop
    loop = asyncio.new_event_loop()
    loop.run_until_complete(scheduler.start())

    app = FastAPI()
    app.include_router(router)
    app.state.scheduler = scheduler

    # Override dependency
    app.dependency_overrides[get_scheduler] = lambda: scheduler

    yield app, scheduler, db

    # Cleanup
    loop.run_until_complete(scheduler.shutdown())
    loop.close()
    db.close()


@pytest.fixture
def client(app_with_scheduler):
    """FastAPI test client."""
    app, _, _ = app_with_scheduler
    return TestClient(app)


# ── POST /api/schedules tests ────────────────────────────────────────────────


class TestCreateSchedule:
    """Test POST /api/schedules."""

    def test_create_schedule(self, client):
        resp = client.post(
            "/api/schedules",
            json={
                "name": "daily-report",
                "interval": "every 24h",
                "prompt": "Summarize today",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "daily-report"
        assert data["interval_seconds"] == 86400
        assert data["prompt"] == "Summarize today"
        assert data["status"] == "active"

    def test_create_schedule_30m(self, client):
        resp = client.post(
            "/api/schedules",
            json={
                "name": "check-emails",
                "interval": "every 30m",
                "prompt": "Check mail",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["interval_seconds"] == 1800

    def test_create_duplicate(self, client):
        client.post(
            "/api/schedules",
            json={"name": "dup", "interval": "every 1h", "prompt": "First"},
        )
        resp = client.post(
            "/api/schedules",
            json={"name": "dup", "interval": "every 2h", "prompt": "Second"},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_create_invalid_interval(self, client):
        resp = client.post(
            "/api/schedules",
            json={"name": "bad", "interval": "whenever", "prompt": "Prompt"},
        )
        assert resp.status_code == 400
        assert "couldn't read 'whenever'" in resp.json()["detail"]

    def test_create_missing_fields(self, client):
        resp = client.post("/api/schedules", json={"name": "incomplete"})
        assert resp.status_code == 422  # Pydantic validation


# ── GET /api/schedules tests ─────────────────────────────────────────────────


class TestListSchedules:
    """Test GET /api/schedules."""

    def test_list_empty(self, client):
        resp = client.get("/api/schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["schedules"] == []
        assert data["total"] == 0

    def test_list_with_tasks(self, client):
        client.post(
            "/api/schedules",
            json={"name": "task-a", "interval": "every 1h", "prompt": "A"},
        )
        client.post(
            "/api/schedules",
            json={"name": "task-b", "interval": "every 2h", "prompt": "B"},
        )
        resp = client.get("/api/schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        names = {s["name"] for s in data["schedules"]}
        assert names == {"task-a", "task-b"}

    @pytest.mark.parametrize(
        "phrase",
        ["every weekday at 8am", "every 30m", "every hour from 8am to 6pm on weekdays"],
    )
    def test_list_describes_schedule_like_the_preview(self, client, phrase):
        preview = client.post("/api/schedules/parse", json={"input": phrase}).json()
        client.post(
            "/api/schedules",
            json={"name": "described", "interval": phrase, "prompt": "P"},
        )
        [row] = client.get("/api/schedules").json()["schedules"]
        assert row["description"] == preview["description"]

    def test_weekday_schedule_is_not_described_as_daily(self, client):
        client.post(
            "/api/schedules",
            json={"name": "brief", "interval": "every weekday at 8am", "prompt": "P"},
        )
        [row] = client.get("/api/schedules").json()["schedules"]
        assert row["description"] == "Weekdays at 8:00 AM"

    def test_interval_only_row_says_how_often(self):
        from gaia.ui.scheduler import ScheduledTask

        task = ScheduledTask("id", "legacy", 86400, "P", schedule_config=None)
        assert task.to_dict()["description"] == "Every day"


# ── GET /api/schedules/{name} tests ──────────────────────────────────────────


class TestGetSchedule:
    """Test GET /api/schedules/{name}."""

    def test_get_existing(self, client):
        client.post(
            "/api/schedules",
            json={"name": "my-sched", "interval": "every 6h", "prompt": "Do it"},
        )
        resp = client.get("/api/schedules/my-sched")
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-sched"

    def test_get_not_found(self, client):
        resp = client.get("/api/schedules/nonexistent")
        assert resp.status_code == 404


# ── PUT /api/schedules/{name} tests ──────────────────────────────────────────


class TestUpdateSchedule:
    """Test PUT /api/schedules/{name}."""

    def test_pause_schedule(self, client):
        client.post(
            "/api/schedules",
            json={"name": "pausable", "interval": "every 1h", "prompt": "P"},
        )
        resp = client.put("/api/schedules/pausable", json={"status": "paused"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    def test_resume_schedule(self, client):
        client.post(
            "/api/schedules",
            json={"name": "resumable", "interval": "every 1h", "prompt": "R"},
        )
        client.put("/api/schedules/resumable", json={"status": "paused"})
        resp = client.put("/api/schedules/resumable", json={"status": "active"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_cancel_schedule(self, client):
        client.post(
            "/api/schedules",
            json={"name": "cancellable", "interval": "every 1h", "prompt": "C"},
        )
        resp = client.put("/api/schedules/cancellable", json={"status": "cancelled"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_update_not_found(self, client):
        resp = client.put("/api/schedules/ghost", json={"status": "paused"})
        assert resp.status_code == 404

    def test_invalid_status(self, client):
        client.post(
            "/api/schedules",
            json={"name": "inv", "interval": "every 1h", "prompt": "I"},
        )
        resp = client.put("/api/schedules/inv", json={"status": "invalid"})
        assert resp.status_code == 400


# ── DELETE /api/schedules/{name} tests ───────────────────────────────────────


class TestDeleteSchedule:
    """Test DELETE /api/schedules/{name}."""

    def test_delete_existing(self, client):
        client.post(
            "/api/schedules",
            json={"name": "del-me", "interval": "every 1h", "prompt": "D"},
        )
        resp = client.delete("/api/schedules/del-me")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # Verify it's gone
        resp = client.get("/api/schedules/del-me")
        assert resp.status_code == 404

    def test_delete_not_found(self, client):
        resp = client.delete("/api/schedules/nonexistent")
        assert resp.status_code == 404


# ── GET /api/schedules/{name}/results tests ──────────────────────────────────


class TestScheduleResults:
    """Test GET /api/schedules/{name}/results."""

    def test_results_empty(self, client):
        client.post(
            "/api/schedules",
            json={"name": "no-results", "interval": "every 1h", "prompt": "N"},
        )
        resp = client.get("/api/schedules/no-results/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["total"] == 0

    def test_results_not_found(self, client):
        resp = client.get("/api/schedules/nonexistent/results")
        assert resp.status_code == 404


class TestRunScheduleNow:
    """POST /api/schedules/{name}/run (#5718)."""

    def test_run_now(self, client):
        client.post(
            "/api/schedules",
            json={"name": "now", "interval": "every 1h", "prompt": "N"},
        )
        resp = client.post("/api/schedules/now/run")
        assert resp.status_code == 200
        assert resp.json()["name"] == "now"

    def test_run_now_unknown(self, client):
        assert client.post("/api/schedules/missing/run").status_code == 404

    def test_run_now_while_running_is_409(self, app_with_scheduler):
        _, scheduler, _ = app_with_scheduler
        client = TestClient(app_with_scheduler[0])
        client.post(
            "/api/schedules",
            json={"name": "busy", "interval": "every 1h", "prompt": "B"},
        )
        scheduler._tasks["busy"]._executing = True
        try:
            resp = client.post("/api/schedules/busy/run")
            assert resp.status_code == 409
            assert "already running" in resp.json()["detail"]
            listed = client.get("/api/schedules/busy").json()
            assert listed["running"] is True
        finally:
            scheduler._tasks["busy"]._executing = False

    def test_run_now_name_is_exact(self, client):
        client.post(
            "/api/schedules",
            json={"name": "exact", "interval": "every 1h", "prompt": "E"},
        )
        assert client.post("/api/schedules/EXACT/run").status_code == 404
        assert client.post("/api/schedules/exac/run").status_code == 404


# ── Names with "/" ──────────────────────────────────────────────────────────


LEGACY = "Inbox/News digest"
LEGACY_URL = "/api/schedules/Inbox%2FNews%20digest"


@pytest.fixture
def legacy_client():
    """A server whose database already holds a schedule named with a "/"."""
    db = ChatDatabase(":memory:")
    config = parse_schedule_input("every 2 hours")
    config.timezone = "UTC"
    Scheduler(db=db, executor=fake_executor)._db_create_task(
        ScheduledTask(
            task_id="legacy-id",
            name=LEGACY,
            interval_seconds=config.interval_seconds,
            prompt="Summarize my news",
            next_run_at=datetime.now(timezone.utc).isoformat(),
            schedule_config=config.to_json(),
        )
    )
    scheduler = Scheduler(db=db, executor=fake_executor)

    # Start on the client's loop, so the loaded schedule's timer lives there.
    @asynccontextmanager
    async def lifespan(_app):
        await scheduler.start()
        yield
        await scheduler.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app.dependency_overrides[get_scheduler] = lambda: scheduler
    with TestClient(app) as client:
        yield client, scheduler
    db.close()


class TestSlashInName:
    """A "/" in a name arrives decoded, so routes must still find the schedule."""

    def test_existing_slash_schedule_can_be_opened(self, legacy_client):
        client, _ = legacy_client
        resp = client.get(LEGACY_URL)
        assert resp.status_code == 200
        assert resp.json()["name"] == LEGACY
        assert client.get(f"{LEGACY_URL}/results").status_code == 200

    def test_existing_slash_schedule_can_be_paused_and_resumed(self, legacy_client):
        client, _ = legacy_client
        paused = client.put(LEGACY_URL, json={"status": "paused"})
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"
        resumed = client.put(LEGACY_URL, json={"status": "active"})
        assert resumed.json()["status"] == "active"

    def test_existing_slash_schedule_can_be_run_and_cancelled(self, legacy_client):
        client, _ = legacy_client
        assert client.post(f"{LEGACY_URL}/run").status_code == 200
        cancelled = client.put(LEGACY_URL, json={"status": "cancelled"})
        assert cancelled.json()["status"] == "cancelled"

    def test_existing_slash_schedule_can_be_deleted(self, legacy_client):
        client, scheduler = legacy_client
        assert client.delete(LEGACY_URL).status_code == 200
        assert scheduler.get_task(LEGACY) is None

    def test_new_slash_name_is_rejected_with_a_fix(self, client):
        resp = client.post(
            "/api/schedules",
            json={"name": "AM/PM check", "interval": "every 1h", "prompt": "P"},
        )
        assert resp.status_code == 400
        assert "/" in resp.json()["detail"]
        assert client.get("/api/schedules").json()["total"] == 0
