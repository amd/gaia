# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""REST API tests for the GAIA Scheduler endpoints (M5: Scheduled Autonomy).

Tests the /api/schedules/* endpoints using FastAPI TestClient.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui.routers.schedules import get_scheduler, router
from gaia.ui.scheduler import Scheduler

# ── Fixtures ──────────────────────────────────────────────────────────────────


class FakeDB:
    """Minimal database with scheduled_tasks and schedule_results tables."""

    def __init__(self):
        import sqlite3
        import threading

        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                interval_seconds INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT,
                last_run_at TEXT,
                next_run_at TEXT,
                last_result TEXT,
                run_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS schedule_results (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
                executed_at TEXT NOT NULL,
                result TEXT,
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_schedule_results_task
                ON schedule_results(task_id, executed_at DESC);
            """)

    def close(self):
        self._conn.close()


@pytest.fixture
def app_with_scheduler():
    """Create a FastAPI app with scheduler for testing."""
    db = FakeDB()
    scheduler = Scheduler(db=db)

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
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_create_invalid_interval(self, client):
        resp = client.post(
            "/api/schedules",
            json={"name": "bad", "interval": "whenever", "prompt": "Prompt"},
        )
        assert resp.status_code == 400
        assert "Cannot parse interval" in resp.json()["detail"]

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
