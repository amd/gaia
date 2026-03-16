# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration tests for the GAIA Scheduler (M5: Scheduled Autonomy).

Tests:
- Scheduler fires tasks on the correct interval
- Scheduler executes prompt through executor callback
- Scheduler survives restart (tasks reloaded from DB)
- Scheduler handles executor errors gracefully
- Concurrent scheduled tasks fire independently
- REST API lifecycle (create -> fire -> result -> query)
- Scheduler shutdown is clean
"""

import asyncio
import sqlite3
import threading

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from gaia.ui.routers.schedules import get_scheduler, router
from gaia.ui.scheduler import ScheduledTask, Scheduler, parse_interval

# ── Fixtures ──────────────────────────────────────────────────────────────────


class FakeDB:
    """In-memory database with scheduled_tasks and schedule_results tables."""

    def __init__(self):
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
                error_count INTEGER DEFAULT 0,
                session_id TEXT,
                schedule_config TEXT
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
def fake_db():
    db = FakeDB()
    yield db
    db.close()


# ── Scheduler Execution Tests ────────────────────────────────────────────────


class TestSchedulerExecution:
    """Test that the scheduler fires tasks and records results."""

    @pytest.mark.asyncio
    async def test_scheduler_fires_on_interval(self, fake_db):
        """Create task with 1-second interval -> wait -> verify it fired."""
        results = []

        async def executor(prompt):
            results.append(prompt)
            return f"Done: {prompt}"

        sched = Scheduler(db=fake_db, executor=executor)
        await sched.start()

        await sched.create_task("fire-test", "every 1s", "Fire me")
        await asyncio.sleep(2.5)

        # Should have fired at least twice in 2.5 seconds
        assert len(results) >= 2
        assert all(r == "Fire me" for r in results)

        task = sched.get_task("fire-test")
        assert task["run_count"] >= 2
        assert task["last_run_at"] is not None

        await sched.shutdown()

    @pytest.mark.asyncio
    async def test_scheduler_executes_prompt(self, fake_db):
        """Scheduled task runs and stores result in schedule_results."""

        async def executor(prompt):
            return f"Executed: {prompt}"

        sched = Scheduler(db=fake_db, executor=executor)
        await sched.start()

        await sched.create_task("exec-test", "every 1s", "Do the thing")
        await asyncio.sleep(1.5)

        results = sched.get_task_results("exec-test")
        assert len(results) >= 1
        assert "Executed: Do the thing" in results[0]["result"]
        assert results[0]["error"] is None

        await sched.shutdown()

    @pytest.mark.asyncio
    async def test_scheduler_handles_agent_error(self, fake_db):
        """Executor error is stored as result, task continues on next interval."""
        call_count = 0

        async def flaky_executor(prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("First run failed")
            return "Recovered"

        sched = Scheduler(db=fake_db, executor=flaky_executor)
        await sched.start()

        await sched.create_task("flaky", "every 1s", "Flaky task")
        await asyncio.sleep(2.5)

        task = sched.get_task("flaky")
        assert task["error_count"] >= 1
        # Task should still be active
        assert task["status"] == "active"
        # Should have run more than once (recovered)
        assert task["run_count"] >= 2

        await sched.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_scheduled_tasks(self, fake_db):
        """Multiple tasks with different intervals all fire independently."""
        fast_results = []
        slow_results = []

        async def executor(prompt):
            if "fast" in prompt:
                fast_results.append(prompt)
            else:
                slow_results.append(prompt)
            return f"Done: {prompt}"

        sched = Scheduler(db=fake_db, executor=executor)
        await sched.start()

        await sched.create_task("fast-task", "every 1s", "fast ping")
        await sched.create_task("slow-task", "every 2s", "slow ping")

        await asyncio.sleep(3.5)

        # Fast should have fired ~3 times, slow ~1-2 times
        assert len(fast_results) >= 2
        assert len(slow_results) >= 1
        # Fast should have more executions than slow
        assert len(fast_results) > len(slow_results)

        await sched.shutdown()


# ── Scheduler Restart/Persistence Tests ──────────────────────────────────────


class TestSchedulerRestart:
    """Tasks persist across scheduler restarts."""

    @pytest.mark.asyncio
    async def test_scheduler_survives_restart(self, fake_db):
        """Create task -> shutdown -> start new scheduler -> task continues."""
        results = []

        async def executor(prompt):
            results.append(prompt)
            return "Done"

        # First scheduler: create task
        sched1 = Scheduler(db=fake_db, executor=executor)
        await sched1.start()
        await sched1.create_task("persist-task", "every 1s", "Persistent prompt")
        await asyncio.sleep(1.5)
        count_before = len(results)
        assert count_before >= 1
        await sched1.shutdown()

        # Second scheduler: task should be reloaded
        sched2 = Scheduler(db=fake_db, executor=executor)
        await sched2.start()

        # Verify task exists
        tasks = sched2.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["name"] == "persist-task"

        # Wait for it to fire again
        await asyncio.sleep(1.5)
        assert len(results) > count_before

        await sched2.shutdown()

    @pytest.mark.asyncio
    async def test_paused_task_survives_restart(self, fake_db):
        """Paused task stays paused after restart."""
        sched1 = Scheduler(db=fake_db)
        await sched1.start()
        await sched1.create_task("paused-persist", "every 1h", "P")
        await sched1.pause_task("paused-persist")
        await sched1.shutdown()

        sched2 = Scheduler(db=fake_db)
        await sched2.start()
        task = sched2.get_task("paused-persist")
        assert task["status"] == "paused"
        await sched2.shutdown()

    @pytest.mark.asyncio
    async def test_cancelled_task_survives_restart(self, fake_db):
        """Cancelled task stays cancelled after restart."""
        sched1 = Scheduler(db=fake_db)
        await sched1.start()
        await sched1.create_task("cancelled-persist", "every 1h", "C")
        await sched1.cancel_task("cancelled-persist")
        await sched1.shutdown()

        sched2 = Scheduler(db=fake_db)
        await sched2.start()
        task = sched2.get_task("cancelled-persist")
        assert task["status"] == "cancelled"
        await sched2.shutdown()

    @pytest.mark.asyncio
    async def test_results_persist_across_restart(self, fake_db):
        """Execution results are available after restart."""

        async def executor(prompt):
            return "Result data"

        sched1 = Scheduler(db=fake_db, executor=executor)
        await sched1.start()
        await sched1.create_task("results-persist", "every 1s", "Get results")
        await asyncio.sleep(1.5)
        await sched1.shutdown()

        sched2 = Scheduler(db=fake_db)
        await sched2.start()
        results = sched2.get_task_results("results-persist")
        assert len(results) >= 1
        assert results[0]["result"] == "Result data"
        await sched2.shutdown()


# ── REST API Lifecycle ───────────────────────────────────────────────────────


class TestSchedulerAPILifecycle:
    """Full lifecycle through REST API: create -> fire -> result -> query.

    Uses httpx.AsyncClient with ASGI transport so the scheduler timers and
    HTTP requests share the same event loop.
    """

    @staticmethod
    def _make_app(db, executor=None):
        """Create FastAPI app with scheduler (not yet started)."""
        scheduler = Scheduler(db=db, executor=executor)
        app = FastAPI()
        app.include_router(router)
        app.state.scheduler = scheduler
        app.dependency_overrides[get_scheduler] = lambda: scheduler
        return app, scheduler

    @pytest.mark.asyncio
    async def test_create_fire_query_lifecycle(self, fake_db):
        """Full lifecycle: create task -> wait for it to fire -> query results."""
        results_captured = []

        async def executor(prompt):
            results_captured.append(prompt)
            return f"Processed: {prompt}"

        app, scheduler = self._make_app(fake_db, executor=executor)
        await scheduler.start()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            # Create
            resp = await client.post(
                "/api/schedules",
                json={"name": "lifecycle", "interval": "every 1s", "prompt": "Do it"},
            )
            assert resp.status_code == 200
            assert resp.json()["name"] == "lifecycle"

            # Wait for execution (shares event loop with scheduler timers)
            await asyncio.sleep(1.5)

            # Query task
            resp = await client.get("/api/schedules/lifecycle")
            assert resp.status_code == 200
            data = resp.json()
            assert data["run_count"] >= 1

            # Query results
            resp = await client.get("/api/schedules/lifecycle/results")
            assert resp.status_code == 200
            results_data = resp.json()
            assert results_data["total"] >= 1
            assert "Processed: Do it" in results_data["results"][0]["result"]

        await scheduler.shutdown()

    @pytest.mark.asyncio
    async def test_pause_resume_via_api(self, fake_db):
        """Pause -> resume cycle through REST API."""
        results = []

        async def executor(prompt):
            results.append(prompt)
            return "OK"

        app, scheduler = self._make_app(fake_db, executor=executor)
        await scheduler.start()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            # Create
            await client.post(
                "/api/schedules",
                json={"name": "pausable", "interval": "every 1s", "prompt": "P"},
            )

            # Let it fire once
            await asyncio.sleep(1.5)
            count_before_pause = len(results)
            assert count_before_pause >= 1

            # Pause
            resp = await client.put(
                "/api/schedules/pausable", json={"status": "paused"}
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "paused"

            # Wait — should NOT fire while paused
            await asyncio.sleep(1.5)
            assert len(results) == count_before_pause

            # Resume
            resp = await client.put(
                "/api/schedules/pausable", json={"status": "active"}
            )
            assert resp.status_code == 200

            # Wait — should fire again
            await asyncio.sleep(1.5)
            assert len(results) > count_before_pause

        await scheduler.shutdown()

    @pytest.mark.asyncio
    async def test_delete_stops_execution(self, fake_db):
        """Deleting a task stops it from firing."""
        results = []

        async def executor(prompt):
            results.append(prompt)
            return "OK"

        app, scheduler = self._make_app(fake_db, executor=executor)
        await scheduler.start()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            await client.post(
                "/api/schedules",
                json={"name": "deletable", "interval": "every 1s", "prompt": "D"},
            )

            await asyncio.sleep(1.5)
            count_before = len(results)

            # Delete
            resp = await client.delete("/api/schedules/deletable")
            assert resp.status_code == 200

            # Wait — should NOT fire after delete
            await asyncio.sleep(1.5)
            assert len(results) == count_before

            # Verify it's gone
            resp = await client.get("/api/schedules/deletable")
            assert resp.status_code == 404

        await scheduler.shutdown()


# ── Shutdown Tests ───────────────────────────────────────────────────────────


class TestSchedulerShutdownIntegration:
    """Clean shutdown under various conditions."""

    @pytest.mark.asyncio
    async def test_shutdown_with_running_tasks(self, fake_db):
        """Shutdown while tasks are actively running completes cleanly."""

        async def slow_executor(prompt):
            await asyncio.sleep(0.5)
            return "Slow done"

        sched = Scheduler(db=fake_db, executor=slow_executor)
        await sched.start()

        await sched.create_task("s1", "every 1s", "Slow 1")
        await sched.create_task("s2", "every 1s", "Slow 2")
        await sched.create_task("s3", "every 1s", "Slow 3")

        await asyncio.sleep(0.5)

        # Should not hang or raise
        await sched.shutdown()
        assert not sched.running

    @pytest.mark.asyncio
    async def test_shutdown_then_restart(self, fake_db):
        """Shutdown -> start new scheduler -> works fine."""
        sched1 = Scheduler(db=fake_db)
        await sched1.start()
        await sched1.create_task("restart-test", "every 1h", "Test")
        await sched1.shutdown()

        sched2 = Scheduler(db=fake_db)
        await sched2.start()
        tasks = sched2.list_tasks()
        assert len(tasks) == 1
        await sched2.shutdown()


# ── Server Wiring Tests ─────────────────────────────────────────────────


class TestServerSchedulerWiring:
    """Verify that create_app() wires up the scheduler with a real executor.

    These tests catch the gap where the Scheduler was constructed without an
    executor, causing all tasks to run in dry-run mode.
    """

    @pytest.mark.asyncio
    async def test_server_app_has_executor(self):
        """create_app() should produce a scheduler with executor != None."""
        from contextlib import asynccontextmanager

        from asgi_lifespan import LifespanManager

        from gaia.ui.server import create_app

        app = create_app(db_path=":memory:")

        # Drive the ASGI lifespan so scheduler is created on app.state
        async with LifespanManager(app) as manager:
            scheduler = app.state.scheduler
            assert scheduler is not None, "Scheduler not attached to app.state"
            assert (
                scheduler._executor is not None
            ), "Scheduler has no executor — scheduled tasks will run in dry-run mode"

    @pytest.mark.asyncio
    async def test_server_scheduler_fires_with_executor(self):
        """A task created through the real server app should use the executor."""
        from unittest.mock import AsyncMock

        from asgi_lifespan import LifespanManager

        from gaia.ui.server import create_app

        app = create_app(db_path=":memory:")

        async with LifespanManager(app):
            scheduler = app.state.scheduler

            # Replace the executor with a mock to avoid needing a real LLM
            mock_executor = AsyncMock(return_value="Mock LLM response")
            scheduler._executor = mock_executor

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                # Create a short-interval task via the API
                resp = await client.post(
                    "/api/schedules",
                    json={
                        "name": "wiring-test",
                        "interval": "every 1s",
                        "prompt": "Hello",
                    },
                )
                assert resp.status_code == 200

                # Wait for it to fire
                await asyncio.sleep(1.5)

                # Verify the mock executor was called (not dry-run)
                assert mock_executor.call_count >= 1
                mock_executor.assert_called_with("Hello")

                # Verify result was stored
                resp = await client.get("/api/schedules/wiring-test/results")
                assert resp.status_code == 200
                results = resp.json()
                assert results["total"] >= 1
                assert results["results"][0]["result"] == "Mock LLM response"

            await scheduler.shutdown()
