# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Async task scheduler for GAIA Agent UI.

Manages recurring scheduled tasks with asyncio timers. Tasks are persisted
in the ChatDatabase and automatically restarted on server startup.

Supports interval strings like "every 6h", "every 30m", "every 24h",
"daily at 9am", etc.
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def parse_interval(interval_str: str) -> int:
    """Parse a human-readable interval string into seconds.

    Supported formats:
        - "every 30m" or "every 30 minutes"
        - "every 6h" or "every 6 hours"
        - "every 2d" or "every 2 days"
        - "every 30s" or "every 30 seconds"
        - "daily" (alias for every 24h)
        - "hourly" (alias for every 1h)

    Args:
        interval_str: Human-readable interval string.

    Returns:
        Interval in seconds.

    Raises:
        ValueError: If the interval string cannot be parsed.
    """
    s = interval_str.strip().lower()

    # Handle aliases
    if s == "daily":
        return 86400
    if s == "hourly":
        return 3600

    # Try "every Xunit" pattern
    match = re.match(
        r"every\s+(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hr|hours?|d|days?)",
        s,
    )
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("s"):
            return value
        elif unit.startswith("m"):
            return value * 60
        elif unit.startswith("h"):
            return value * 3600
        elif unit.startswith("d"):
            return value * 86400

    # Try bare "Xh", "Xm", etc.
    match = re.match(r"(\d+)\s*(s|m|h|d)", s)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "s":
            return value
        elif unit == "m":
            return value * 60
        elif unit == "h":
            return value * 3600
        elif unit == "d":
            return value * 86400

    raise ValueError(
        f"Cannot parse interval: '{interval_str}'. "
        "Use formats like 'every 30m', 'every 6h', 'every 2d', 'daily', 'hourly'."
    )


class ScheduledTask:
    """Represents a single scheduled task with its timer state."""

    def __init__(
        self,
        task_id: str,
        name: str,
        interval_seconds: int,
        prompt: str,
        status: str = "active",
        created_at: str = None,
        last_run_at: str = None,
        next_run_at: str = None,
        last_result: str = None,
        run_count: int = 0,
        error_count: int = 0,
    ):
        self.id = task_id
        self.name = name
        self.interval_seconds = interval_seconds
        self.prompt = prompt
        self.status = status
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.last_run_at = last_run_at
        self.next_run_at = next_run_at
        self.last_result = last_result
        self.run_count = run_count
        self.error_count = error_count
        self._timer_task: Optional[asyncio.Task] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "interval_seconds": self.interval_seconds,
            "prompt": self.prompt,
            "status": self.status,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "last_result": self.last_result,
            "run_count": self.run_count,
            "error_count": self.error_count,
        }


class Scheduler:
    """Async scheduler that manages recurring tasks.

    The scheduler persists tasks in the ChatDatabase's scheduled_tasks table
    and uses asyncio timers to fire them at the configured intervals.

    Usage::

        scheduler = Scheduler(db)
        await scheduler.start()  # Load & start persisted tasks
        await scheduler.create_task("daily-report", "every 24h", "Summarize today's news")
        ...
        await scheduler.shutdown()  # Cancel all timers
    """

    def __init__(self, db, executor: Callable = None):
        """Initialize the scheduler.

        Args:
            db: ChatDatabase instance with scheduled_tasks table.
            executor: Async callable(prompt: str) -> str that executes a task.
                      If None, tasks log but don't execute.
        """
        self._db = db
        self._executor = executor
        self._tasks: Dict[str, ScheduledTask] = {}
        self._lock = asyncio.Lock()
        self._running = False
        logger.info("Scheduler initialized")

    @property
    def running(self) -> bool:
        """Whether the scheduler is currently running."""
        return self._running

    @property
    def tasks(self) -> Dict[str, ScheduledTask]:
        """Active scheduled tasks by name."""
        return dict(self._tasks)

    async def start(self):
        """Start the scheduler: load persisted tasks and start timers."""
        self._running = True
        await self._load_tasks()
        logger.info("Scheduler started with %d task(s)", len(self._tasks))

    async def shutdown(self):
        """Stop the scheduler: cancel all timers cleanly."""
        self._running = False
        async with self._lock:
            for task in self._tasks.values():
                if task._timer_task and not task._timer_task.done():
                    task._timer_task.cancel()
                    try:
                        await task._timer_task
                    except asyncio.CancelledError:
                        pass
                    task._timer_task = None
        logger.info("Scheduler shut down, all timers cancelled")

    async def create_task(
        self,
        name: str,
        interval: str,
        prompt: str,
    ) -> Dict[str, Any]:
        """Create a new scheduled task.

        Args:
            name: Unique task name.
            interval: Human-readable interval (e.g. "every 6h").
            prompt: The prompt to execute on each run.

        Returns:
            Task dict with status info.

        Raises:
            ValueError: If name is duplicate or interval is invalid.
        """
        interval_seconds = parse_interval(interval)

        async with self._lock:
            if name in self._tasks:
                raise ValueError(f"Task with name '{name}' already exists")

            task_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            next_run = now + timedelta(seconds=interval_seconds)

            task = ScheduledTask(
                task_id=task_id,
                name=name,
                interval_seconds=interval_seconds,
                prompt=prompt,
                status="active",
                created_at=now.isoformat(),
                next_run_at=next_run.isoformat(),
            )

            # Persist to database
            self._db_create_task(task)

            # Start timer
            self._tasks[name] = task
            if self._running:
                task._timer_task = asyncio.create_task(
                    self._run_loop(task), name=f"sched:{name}"
                )

        logger.info("Created scheduled task '%s' (every %ds)", name, interval_seconds)
        return task.to_dict()

    async def cancel_task(self, name: str) -> Dict[str, Any]:
        """Cancel a scheduled task.

        Args:
            name: Task name.

        Returns:
            Updated task dict.

        Raises:
            KeyError: If task not found.
        """
        async with self._lock:
            task = self._tasks.get(name)
            if not task:
                raise KeyError(f"Task '{name}' not found")

            # Cancel timer
            if task._timer_task and not task._timer_task.done():
                task._timer_task.cancel()
                try:
                    await task._timer_task
                except asyncio.CancelledError:
                    pass
                task._timer_task = None

            task.status = "cancelled"
            task.next_run_at = None
            self._db_update_task(task)

        logger.info("Cancelled scheduled task '%s'", name)
        return task.to_dict()

    async def pause_task(self, name: str) -> Dict[str, Any]:
        """Pause a scheduled task (keeps it in the list but stops timer).

        Args:
            name: Task name.

        Returns:
            Updated task dict.

        Raises:
            KeyError: If task not found.
        """
        async with self._lock:
            task = self._tasks.get(name)
            if not task:
                raise KeyError(f"Task '{name}' not found")

            if task.status != "active":
                raise ValueError(f"Task '{name}' is not active (status: {task.status})")

            # Cancel timer
            if task._timer_task and not task._timer_task.done():
                task._timer_task.cancel()
                try:
                    await task._timer_task
                except asyncio.CancelledError:
                    pass
                task._timer_task = None

            task.status = "paused"
            task.next_run_at = None
            self._db_update_task(task)

        logger.info("Paused scheduled task '%s'", name)
        return task.to_dict()

    async def resume_task(self, name: str) -> Dict[str, Any]:
        """Resume a paused scheduled task.

        Args:
            name: Task name.

        Returns:
            Updated task dict.

        Raises:
            KeyError: If task not found.
        """
        async with self._lock:
            task = self._tasks.get(name)
            if not task:
                raise KeyError(f"Task '{name}' not found")

            if task.status != "paused":
                raise ValueError(f"Task '{name}' is not paused (status: {task.status})")

            task.status = "active"
            next_run = datetime.now(timezone.utc) + timedelta(
                seconds=task.interval_seconds
            )
            task.next_run_at = next_run.isoformat()
            self._db_update_task(task)

            # Restart timer
            if self._running:
                task._timer_task = asyncio.create_task(
                    self._run_loop(task), name=f"sched:{name}"
                )

        logger.info("Resumed scheduled task '%s'", name)
        return task.to_dict()

    async def delete_task(self, name: str) -> bool:
        """Delete a scheduled task entirely.

        Args:
            name: Task name.

        Returns:
            True if deleted.

        Raises:
            KeyError: If task not found.
        """
        async with self._lock:
            task = self._tasks.get(name)
            if not task:
                raise KeyError(f"Task '{name}' not found")

            # Cancel timer
            if task._timer_task and not task._timer_task.done():
                task._timer_task.cancel()
                try:
                    await task._timer_task
                except asyncio.CancelledError:
                    pass

            self._db_delete_task(task.id)
            del self._tasks[name]

        logger.info("Deleted scheduled task '%s'", name)
        return True

    def get_task(self, name: str) -> Optional[Dict[str, Any]]:
        """Get task info by name.

        Args:
            name: Task name.

        Returns:
            Task dict or None.
        """
        task = self._tasks.get(name)
        return task.to_dict() if task else None

    def list_tasks(self) -> List[Dict[str, Any]]:
        """List all scheduled tasks.

        Returns:
            List of task dicts.
        """
        return [t.to_dict() for t in self._tasks.values()]

    def get_task_results(self, name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get past run results for a task.

        Args:
            name: Task name.
            limit: Maximum number of results to return.

        Returns:
            List of result dicts with timestamp and output.
        """
        task = self._tasks.get(name)
        if not task:
            return []

        return self._db_get_results(task.id, limit)

    # ── Internal: timer loop ──────────────────────────────────────────────

    async def _run_loop(self, task: ScheduledTask):
        """Run the timer loop for a single task."""
        try:
            while self._running and task.status == "active":
                await asyncio.sleep(task.interval_seconds)

                if not self._running or task.status != "active":
                    break

                await self._execute_task(task)
        except asyncio.CancelledError:
            logger.debug("Timer cancelled for task '%s'", task.name)
            raise

    async def _execute_task(self, task: ScheduledTask):
        """Execute a single task run."""
        now = datetime.now(timezone.utc)
        task.last_run_at = now.isoformat()
        task.run_count += 1

        logger.info(
            "Executing scheduled task '%s' (run #%d)", task.name, task.run_count
        )

        result = None
        error = None
        try:
            if self._executor:
                result = await self._executor(task.prompt)
            else:
                result = f"[dry-run] Would execute: {task.prompt}"
                logger.info("No executor configured, dry-run for '%s'", task.name)
        except Exception as e:
            error = str(e)
            task.error_count += 1
            logger.error(
                "Scheduled task '%s' failed (run #%d): %s",
                task.name,
                task.run_count,
                e,
                exc_info=True,
            )

        # Update next run
        next_run = datetime.now(timezone.utc) + timedelta(seconds=task.interval_seconds)
        task.next_run_at = next_run.isoformat()
        task.last_result = error if error else (result or "completed")

        # Persist state
        self._db_update_task(task)
        self._db_store_result(task.id, now.isoformat(), result, error)

    # ── Internal: database operations ─────────────────────────────────────

    async def _load_tasks(self):
        """Load persisted tasks from database and start active timers."""
        try:
            rows = self._db_list_tasks()
            for row in rows:
                task = ScheduledTask(
                    task_id=row["id"],
                    name=row["name"],
                    interval_seconds=row["interval_seconds"],
                    prompt=row["prompt"],
                    status=row["status"],
                    created_at=row.get("created_at"),
                    last_run_at=row.get("last_run_at"),
                    next_run_at=row.get("next_run_at"),
                    last_result=row.get("last_result"),
                    run_count=row.get("run_count", 0),
                    error_count=row.get("error_count", 0),
                )
                self._tasks[task.name] = task

                if task.status == "active" and self._running:
                    task._timer_task = asyncio.create_task(
                        self._run_loop(task), name=f"sched:{task.name}"
                    )
                    logger.info(
                        "Restored scheduled task '%s' (every %ds)",
                        task.name,
                        task.interval_seconds,
                    )
        except Exception as e:
            logger.error("Failed to load scheduled tasks: %s", e)

    def _db_create_task(self, task: ScheduledTask):
        """Insert a new task row."""
        with self._db._lock:
            self._db._conn.execute(
                """INSERT INTO scheduled_tasks
                   (id, name, interval_seconds, prompt, status,
                    created_at, next_run_at, run_count, error_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.id,
                    task.name,
                    task.interval_seconds,
                    task.prompt,
                    task.status,
                    task.created_at,
                    task.next_run_at,
                    task.run_count,
                    task.error_count,
                ),
            )
            self._db._conn.commit()

    def _db_update_task(self, task: ScheduledTask):
        """Update an existing task row."""
        with self._db._lock:
            self._db._conn.execute(
                """UPDATE scheduled_tasks
                   SET status = ?, last_run_at = ?, next_run_at = ?,
                       last_result = ?, run_count = ?, error_count = ?
                   WHERE id = ?""",
                (
                    task.status,
                    task.last_run_at,
                    task.next_run_at,
                    task.last_result,
                    task.run_count,
                    task.error_count,
                    task.id,
                ),
            )
            self._db._conn.commit()

    def _db_delete_task(self, task_id: str):
        """Delete a task row and its results."""
        with self._db._lock:
            self._db._conn.execute(
                "DELETE FROM schedule_results WHERE task_id = ?", (task_id,)
            )
            self._db._conn.execute(
                "DELETE FROM scheduled_tasks WHERE id = ?", (task_id,)
            )
            self._db._conn.commit()

    def _db_list_tasks(self) -> List[Dict[str, Any]]:
        """Load all tasks from database."""
        with self._db._lock:
            rows = self._db._conn.execute("SELECT * FROM scheduled_tasks").fetchall()
            return [dict(r) for r in rows]

    def _db_store_result(
        self, task_id: str, timestamp: str, result: str = None, error: str = None
    ):
        """Store a task execution result."""
        result_id = str(uuid.uuid4())
        with self._db._lock:
            self._db._conn.execute(
                """INSERT INTO schedule_results
                   (id, task_id, executed_at, result, error)
                   VALUES (?, ?, ?, ?, ?)""",
                (result_id, task_id, timestamp, result, error),
            )
            self._db._conn.commit()

    def _db_get_results(self, task_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get past results for a task."""
        with self._db._lock:
            rows = self._db._conn.execute(
                """SELECT * FROM schedule_results
                   WHERE task_id = ?
                   ORDER BY executed_at DESC
                   LIMIT ?""",
                (task_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
