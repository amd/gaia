# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the GAIA Scheduler (M5: Scheduled Autonomy).

Tests cover:
- Interval string parsing
- Task creation, pause, resume, cancel, delete
- Timer loop execution
- Database persistence
- Shutdown cleanup
"""

import asyncio

import pytest
import pytest_asyncio

from gaia.ui.database import ChatDatabase
from gaia.ui.scheduler import (
    ScheduleConfig,
    ScheduledTask,
    Scheduler,
    parse_interval,
    parse_schedule_input,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


async def fake_executor(prompt: str) -> str:
    """Minimal executor for tests that don't care about execution results."""
    return f"ran: {prompt}"


@pytest.fixture
def fake_db():
    db = ChatDatabase(":memory:")
    yield db
    db.close()


@pytest_asyncio.fixture
async def scheduler(fake_db):
    sched = Scheduler(db=fake_db, executor=fake_executor)
    await sched.start()
    yield sched
    await sched.shutdown()


# ── parse_interval tests ─────────────────────────────────────────────────────


class TestParseInterval:
    """Test the interval string parser."""

    def test_every_minutes(self):
        assert parse_interval("every 30m") == 1800

    def test_every_hours(self):
        assert parse_interval("every 6h") == 21600

    def test_every_seconds(self):
        assert parse_interval("every 30s") == 30

    def test_every_days(self):
        assert parse_interval("every 2d") == 172800

    def test_every_minutes_long(self):
        assert parse_interval("every 5 minutes") == 300

    def test_every_hours_long(self):
        assert parse_interval("every 2 hours") == 7200

    def test_daily_alias(self):
        assert parse_interval("daily") == 86400

    def test_hourly_alias(self):
        assert parse_interval("hourly") == 3600

    def test_bare_shorthand(self):
        assert parse_interval("30m") == 1800

    def test_bare_hours(self):
        assert parse_interval("6h") == 21600

    def test_case_insensitive(self):
        assert parse_interval("Every 30M") == 1800

    def test_invalid_interval(self):
        with pytest.raises(ValueError, match="Cannot parse interval"):
            parse_interval("next tuesday")

    def test_empty_string(self):
        with pytest.raises(ValueError):
            parse_interval("")

    def test_every_24h(self):
        assert parse_interval("every 24h") == 86400


# ── ScheduledTask tests ──────────────────────────────────────────────────────


class TestScheduledTask:
    """Test the ScheduledTask data class."""

    def test_to_dict(self):
        task = ScheduledTask(
            task_id="abc123",
            name="test-task",
            interval_seconds=3600,
            prompt="Do something",
        )
        d = task.to_dict()
        assert d["id"] == "abc123"
        assert d["name"] == "test-task"
        assert d["interval_seconds"] == 3600
        assert d["prompt"] == "Do something"
        assert d["status"] == "active"
        assert d["run_count"] == 0
        assert d["error_count"] == 0

    def test_default_status(self):
        task = ScheduledTask(task_id="x", name="t", interval_seconds=60, prompt="p")
        assert task.status == "active"


# ── Scheduler create/list tests ──────────────────────────────────────────────


class TestSchedulerCreate:
    """Test task creation and listing."""

    @pytest.mark.asyncio
    async def test_create_task(self, scheduler):
        result = await scheduler.create_task("my-task", "every 30m", "Do thing")
        assert result["name"] == "my-task"
        assert result["interval_seconds"] == 1800
        assert result["prompt"] == "Do thing"
        assert result["status"] == "active"
        assert result["next_run_at"] is not None

    @pytest.mark.asyncio
    async def test_create_duplicate_name(self, scheduler):
        await scheduler.create_task("dup", "every 1h", "First")
        with pytest.raises(ValueError, match="already exists"):
            await scheduler.create_task("dup", "every 2h", "Second")

    @pytest.mark.asyncio
    async def test_create_invalid_interval(self, scheduler):
        with pytest.raises(ValueError, match="Cannot parse interval"):
            await scheduler.create_task("bad", "whenever", "Prompt")

    @pytest.mark.asyncio
    async def test_list_tasks(self, scheduler):
        await scheduler.create_task("a", "every 1h", "Prompt A")
        await scheduler.create_task("b", "every 2h", "Prompt B")
        tasks = scheduler.list_tasks()
        assert len(tasks) == 2
        names = {t["name"] for t in tasks}
        assert names == {"a", "b"}

    @pytest.mark.asyncio
    async def test_get_task(self, scheduler):
        await scheduler.create_task("find-me", "every 5m", "Hello")
        task = scheduler.get_task("find-me")
        assert task is not None
        assert task["prompt"] == "Hello"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, scheduler):
        assert scheduler.get_task("nope") is None

    @pytest.mark.asyncio
    async def test_task_persists_to_db(self, fake_db):
        """Task should be written to database on creation."""
        sched = Scheduler(db=fake_db, executor=fake_executor)
        await sched.start()
        await sched.create_task("db-test", "every 1h", "Check DB")

        # Verify row exists via the public API
        rows = [r for r in fake_db.list_scheduled_tasks() if r["name"] == "db-test"]
        assert len(rows) == 1
        assert rows[0]["interval_seconds"] == 3600

        await sched.shutdown()


# ── Scheduler pause/resume/cancel tests ──────────────────────────────────────


class TestSchedulerLifecycle:
    """Test pause, resume, cancel, delete operations."""

    @pytest.mark.asyncio
    async def test_pause_task(self, scheduler):
        await scheduler.create_task("pausable", "every 1h", "Test")
        result = await scheduler.pause_task("pausable")
        assert result["status"] == "paused"
        assert result["next_run_at"] is None

    @pytest.mark.asyncio
    async def test_pause_not_active(self, scheduler):
        await scheduler.create_task("p", "every 1h", "Test")
        await scheduler.pause_task("p")
        with pytest.raises(ValueError, match="not active"):
            await scheduler.pause_task("p")

    @pytest.mark.asyncio
    async def test_resume_task(self, scheduler):
        await scheduler.create_task("resumable", "every 1h", "Test")
        await scheduler.pause_task("resumable")
        result = await scheduler.resume_task("resumable")
        assert result["status"] == "active"
        assert result["next_run_at"] is not None

    @pytest.mark.asyncio
    async def test_resume_not_paused(self, scheduler):
        await scheduler.create_task("r", "every 1h", "Test")
        with pytest.raises(ValueError, match="not paused"):
            await scheduler.resume_task("r")

    @pytest.mark.asyncio
    async def test_cancel_task(self, scheduler):
        await scheduler.create_task("cancellable", "every 1h", "Test")
        result = await scheduler.cancel_task("cancellable")
        assert result["status"] == "cancelled"
        assert result["next_run_at"] is None

    @pytest.mark.asyncio
    async def test_cancel_not_found(self, scheduler):
        with pytest.raises(KeyError, match="not found"):
            await scheduler.cancel_task("nonexistent")

    @pytest.mark.asyncio
    async def test_delete_task(self, scheduler):
        await scheduler.create_task("deletable", "every 1h", "Test")
        result = await scheduler.delete_task("deletable")
        assert result is True
        assert scheduler.get_task("deletable") is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, scheduler):
        with pytest.raises(KeyError, match="not found"):
            await scheduler.delete_task("ghost")

    @pytest.mark.asyncio
    async def test_delete_removes_from_db(self, fake_db):
        sched = Scheduler(db=fake_db, executor=fake_executor)
        await sched.start()
        await sched.create_task("db-del", "every 1h", "Test")
        await sched.delete_task("db-del")

        rows = [r for r in fake_db.list_scheduled_tasks() if r["name"] == "db-del"]
        assert rows == []

        await sched.shutdown()


# ── Scheduler execution tests ────────────────────────────────────────────────


class TestSchedulerExecution:
    """Test the timer execution loop."""

    @pytest.mark.asyncio
    async def test_executor_called(self, fake_db):
        """Executor should be called when task fires."""
        results = []

        async def mock_executor(prompt):
            results.append(prompt)
            return f"Executed: {prompt}"

        sched = Scheduler(db=fake_db, executor=mock_executor)
        await sched.start()

        # Create a task with 1-second interval
        await sched.create_task("fast", "every 1s", "Quick test")

        # Wait for it to fire at least once
        await asyncio.sleep(1.5)

        assert len(results) >= 1
        assert results[0] == "Quick test"

        # Check that the task recorded the run
        task = sched.get_task("fast")
        assert task["run_count"] >= 1
        assert task["last_run_at"] is not None

        await sched.shutdown()

    @pytest.mark.asyncio
    async def test_executor_error_recorded(self, fake_db):
        """Executor errors should be caught and recorded."""

        async def failing_executor(prompt):
            raise RuntimeError("Something broke")

        sched = Scheduler(db=fake_db, executor=failing_executor)
        await sched.start()

        await sched.create_task("fail", "every 1s", "Will fail")
        await asyncio.sleep(1.5)

        task = sched.get_task("fail")
        assert task["error_count"] >= 1
        assert "Something broke" in (task["last_result"] or "")

        # Task should still be active (errors don't stop scheduling)
        assert task["status"] == "active"

        await sched.shutdown()

    @pytest.mark.asyncio
    async def test_results_stored(self, fake_db):
        """Execution results should be stored in schedule_results."""

        async def mock_executor(prompt):
            return "Done"

        sched = Scheduler(db=fake_db, executor=mock_executor)
        await sched.start()

        await sched.create_task("track", "every 1s", "Track me")
        await asyncio.sleep(1.5)

        results = sched.get_task_results("track")
        assert len(results) >= 1
        assert results[0]["result"] == "Done"
        assert results[0]["error"] is None

        await sched.shutdown()

    def test_scheduler_requires_executor(self, fake_db):
        """A scheduler without an executor must fail loudly at construction."""
        with pytest.raises(ValueError, match="requires a callable executor"):
            Scheduler(db=fake_db, executor=None)

    def test_scheduler_rejects_non_callable_executor(self, fake_db):
        with pytest.raises(ValueError, match="requires a callable executor"):
            Scheduler(db=fake_db, executor="not-callable")

    def test_scheduler_rejects_zero_concurrency(self, fake_db):
        with pytest.raises(ValueError, match="max_concurrent_runs"):
            Scheduler(db=fake_db, executor=fake_executor, max_concurrent_runs=0)


# ── Scheduler shutdown tests ─────────────────────────────────────────────────


class TestSchedulerShutdown:
    """Test clean shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_timers(self, fake_db):
        sched = Scheduler(db=fake_db, executor=fake_executor)
        await sched.start()

        await sched.create_task("t1", "every 1h", "Long")
        await sched.create_task("t2", "every 2h", "Longer")

        # Both should have active timer tasks
        assert len(sched.tasks) == 2

        await sched.shutdown()
        assert not sched.running

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self, fake_db):
        """Calling shutdown twice should not error."""
        sched = Scheduler(db=fake_db, executor=fake_executor)
        await sched.start()
        await sched.shutdown()
        await sched.shutdown()  # Should not raise


# ── Scheduler persistence tests ──────────────────────────────────────────────


class TestSchedulerPersistence:
    """Test that tasks persist across scheduler restarts."""

    @pytest.mark.asyncio
    async def test_tasks_restored_on_start(self, fake_db):
        """Tasks saved to DB should be restored when scheduler starts."""
        # Create tasks with first scheduler
        sched1 = Scheduler(db=fake_db, executor=fake_executor)
        await sched1.start()
        await sched1.create_task("persist-1", "every 1h", "First")
        await sched1.create_task("persist-2", "every 2h", "Second")
        await sched1.shutdown()

        # New scheduler should load them
        sched2 = Scheduler(db=fake_db, executor=fake_executor)
        await sched2.start()
        tasks = sched2.list_tasks()
        assert len(tasks) == 2
        names = {t["name"] for t in tasks}
        assert names == {"persist-1", "persist-2"}
        await sched2.shutdown()

    @pytest.mark.asyncio
    async def test_paused_task_not_started_on_restore(self, fake_db):
        """Paused tasks should be loaded but not have active timers."""
        sched1 = Scheduler(db=fake_db, executor=fake_executor)
        await sched1.start()
        await sched1.create_task("paused-persist", "every 1h", "P")
        await sched1.pause_task("paused-persist")
        await sched1.shutdown()

        sched2 = Scheduler(db=fake_db, executor=fake_executor)
        await sched2.start()
        task = sched2.get_task("paused-persist")
        assert task["status"] == "paused"
        # The internal task object should not have an active timer
        internal = sched2._tasks.get("paused-persist")
        assert internal._timer_task is None
        await sched2.shutdown()


# ── Extended parse_interval tests ───────────────────────────────────────────


class TestParseIntervalExtended:
    """Test newly added interval formats: weekly alias, day names, and week units."""

    def test_weekly_alias(self):
        """'weekly' alias should map to 7 days (604800 seconds)."""
        assert parse_interval("weekly") == 604800

    def test_every_monday(self):
        """'every monday' should be treated as weekly (604800 seconds)."""
        assert parse_interval("every monday") == 604800

    def test_every_friday(self):
        """'every friday' should be treated as weekly (604800 seconds)."""
        assert parse_interval("every friday") == 604800

    def test_every_2_weeks(self):
        """'every 2 weeks' should be 2 * 604800 = 1209600 seconds."""
        assert parse_interval("every 2 weeks") == 1209600

    def test_every_2w(self):
        """'every 2w' shorthand should be 1209600 seconds."""
        assert parse_interval("every 2w") == 1209600

    def test_bare_1w(self):
        """Bare '1w' shorthand should be 604800 seconds."""
        assert parse_interval("1w") == 604800

    def test_invalid_day_name(self):
        """'every someday' is not a valid day name and should raise ValueError."""
        with pytest.raises(ValueError, match="Cannot parse interval"):
            parse_interval("every someday")

    def test_invalid_format(self):
        """'every minute' (no number, not a day name) should raise ValueError."""
        with pytest.raises(ValueError, match="Cannot parse interval"):
            parse_interval("every minute")


# ── ReDoS hardening tests (input bounds + unambiguous regexes) ──────────────


class TestScheduleInputBounds:
    """Hostile/oversized schedule strings must be rejected up front, and the
    window regex must stay linear on whitespace-heavy input."""

    def test_parse_interval_rejects_oversized_input(self):
        with pytest.raises(ValueError, match="too long"):
            parse_interval("every " + "0" * 10_000 + "m")

    def test_parse_interval_rejects_absurd_digit_runs(self):
        # Bounded quantifier: >9-digit values no longer parse.
        with pytest.raises(ValueError, match="Cannot parse interval"):
            parse_interval("1234567890s")

    def test_parse_schedule_input_rejects_oversized_input(self):
        config = parse_schedule_input("from 9" + "\t" * 10_000 + "to 5")
        assert config.interval_seconds == 0
        assert "too long" in config.description

    def test_window_parsing_still_works(self):
        config = parse_schedule_input("every hour from 8am to 6pm")
        assert config.interval_seconds == 3600
        assert config.start_hour == 8
        assert config.end_hour == 18

    def test_window_with_minutes_and_spaces(self):
        config = parse_schedule_input("every 30m from 9:30 am to 5 pm")
        assert config.interval_seconds == 1800
        assert config.start_hour == 9
        assert config.end_hour == 17

    def test_time_of_day_parsing_still_works(self):
        config = parse_schedule_input("daily at 9 pm")
        assert config.interval_seconds == 86400
        assert config.time_of_day == "21:00"


# ── ScheduleConfig fail-loudly tests ─────────────────────────────────────────


class TestScheduleConfigFromJson:
    """Malformed persisted configs must raise, not silently never fire."""

    def test_from_json_empty_returns_default(self):
        config = ScheduleConfig.from_json("")
        assert config.interval_seconds == 0

    def test_from_json_roundtrip(self):
        original = ScheduleConfig(interval_seconds=3600, time_of_day="09:00")
        restored = ScheduleConfig.from_json(original.to_json())
        assert restored == original

    def test_from_json_malformed_raises(self):
        with pytest.raises(ValueError, match="Invalid schedule_config"):
            ScheduleConfig.from_json("{not json")

    def test_from_json_unknown_key_raises(self):
        with pytest.raises(ValueError, match="Invalid schedule_config"):
            ScheduleConfig.from_json('{"bogus_key": 1}')
