# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Async task scheduler for GAIA Agent UI.

Manages recurring scheduled tasks with asyncio timers. Tasks are persisted
in the ChatDatabase and automatically restarted on server startup.

Supports interval strings like "every 6h", "every 30m", "every 24h",
"daily at 9am", "every monday at 3pm", "every hour from 8am to 6pm", etc.
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Day name mappings ────────────────────────────────────────────────────────

_DAY_FULL = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_DAY_ABBR = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
_ALL_DAY_NAMES = {**_DAY_FULL, **_DAY_ABBR}


def parse_interval(interval_str: str) -> int:
    """Parse a human-readable interval string into seconds.

    Supported formats:
        - "every 30m" or "every 30 minutes"
        - "every 6h" or "every 6 hours"
        - "every 2d" or "every 2 days"
        - "every 30s" or "every 30 seconds"
        - "every 2w" or "every 2 weeks"
        - "every monday", "every friday", etc. (weekly on that day)
        - "daily" (alias for every 24h)
        - "hourly" (alias for every 1h)
        - "weekly" (alias for every 7d)

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
    if s == "weekly":
        return 604800

    # Handle "every monday", "every tuesday", etc. (treat as weekly = 7 days)
    day_names = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    match_day = re.match(r"every\s+(" + "|".join(day_names) + r")\b", s)
    if match_day:
        return 604800  # 7 days in seconds

    # Try "every Xunit" pattern
    match = re.match(
        r"every\s+(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hr|hours?|d|days?|w|wk|weeks?)",
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
        elif unit.startswith("w"):
            return value * 604800

    # Try bare "Xh", "Xm", etc.
    match = re.match(r"(\d+)\s*(s|m|h|d|w)", s)
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
        elif unit == "w":
            return value * 604800

    raise ValueError(
        f"Cannot parse interval: '{interval_str}'. "
        "Use formats like 'every 30m', 'every 6h', 'every 2d', "
        "'every 2w', 'every monday', 'daily', 'hourly', 'weekly'."
    )


# ── ScheduleConfig ───────────────────────────────────────────────────────────


@dataclass
class ScheduleConfig:
    """Parsed schedule configuration from natural language input."""

    interval_seconds: int = 0
    time_of_day: Optional[str] = None  # "HH:MM" 24h format
    start_hour: Optional[int] = None  # window start (0-23)
    end_hour: Optional[int] = None  # window end (0-23)
    days_of_week: Optional[List[int]] = None  # 0=Mon..6=Sun
    description: str = ""
    raw_input: str = ""

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "ScheduleConfig":
        """Deserialize from JSON string."""
        if not s:
            return cls()
        return cls(**json.loads(s))


# ── Time parsing helpers ─────────────────────────────────────────────────────


def _parse_time(text: str) -> Optional[str]:
    """Parse a time string into HH:MM 24-hour format.

    Supports: "9pm", "9:30pm", "9am", "noon", "midnight", "21:00".

    Args:
        text: Time string to parse.

    Returns:
        "HH:MM" string or None if not parseable.
    """
    text = text.strip().lower()

    if text == "noon":
        return "12:00"
    if text == "midnight":
        return "00:00"

    # 24-hour format "HH:MM"
    m = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"

    # 12-hour format with optional minutes: "9pm", "9:30am"
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", text)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        period = m.group(3)
        if h == 12:
            h = 0 if period == "am" else 12
        elif period == "pm":
            h += 12
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"

    return None


def _format_time_12h(time_24: str) -> str:
    """Convert HH:MM to human-readable 12-hour format.

    Args:
        time_24: Time in "HH:MM" 24-hour format.

    Returns:
        Human-readable string like "9:00 AM" or "3:30 PM".
    """
    h, m = map(int, time_24.split(":"))
    if h == 0:
        return f"12:{m:02d} AM"
    elif h < 12:
        return f"{h}:{m:02d} AM"
    elif h == 12:
        return f"12:{m:02d} PM"
    else:
        return f"{h - 12}:{m:02d} PM"


def _format_interval_human(seconds: int) -> str:
    """Convert interval seconds to a human-readable string.

    Args:
        seconds: Interval in seconds.

    Returns:
        Human-readable string like "30 minutes", "1 hour", "2 hours".
    """
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins} minute{'s' if mins != 1 else ''}"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    elif seconds < 604800:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''}"
    else:
        weeks = seconds // 604800
        return f"{weeks} week{'s' if weeks != 1 else ''}"


_DAY_NAMES_DISPLAY = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _format_days(days: List[int]) -> str:
    """Format a list of day indices into a human-readable string.

    Args:
        days: List of day indices (0=Monday through 6=Sunday).

    Returns:
        Human-readable string like "Monday", "weekdays", "Mon, Wed, Fri".
    """
    days_sorted = sorted(days)
    if days_sorted == [0, 1, 2, 3, 4]:
        return "weekdays"
    if days_sorted == [5, 6]:
        return "weekends"
    if days_sorted == list(range(7)):
        return "every day"
    return ", ".join(_DAY_NAMES_DISPLAY[d] for d in days_sorted)


# ── Natural language schedule parser ─────────────────────────────────────────


def parse_schedule_input(text: str) -> ScheduleConfig:
    """Parse a natural language schedule description into a ScheduleConfig.

    Handles inputs such as:
        - Simple intervals: "every 30m", "daily", "hourly", "weekly"
        - Time-of-day: "daily at 9pm", "at 9:30am", "every day at 10am"
        - Day + time: "every monday at 3pm", "weekdays at 10am",
          "weekends at noon"
        - Windowed: "every hour from 8am to 6pm",
          "every 2 hours from 8am to 6pm on weekdays",
          "every 30m from 9am to 5pm"

    Args:
        text: Natural language schedule description.

    Returns:
        ScheduleConfig with parsed fields. If the input cannot be parsed,
        interval_seconds will be 0 and description will indicate the error.
    """
    config = ScheduleConfig(raw_input=text)
    s = text.strip().lower()

    if not s:
        config.description = "Could not parse schedule: empty input"
        return config

    # ── 1. Extract time-of-day: "at HH:MM", "at Ham/pm", "at noon" ──
    time_match = re.search(
        r"\bat\s+(noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d{1,2}:\d{2})\b", s
    )
    if time_match:
        parsed_time = _parse_time(time_match.group(1))
        if parsed_time:
            config.time_of_day = parsed_time
        # Remove the matched portion so it doesn't interfere with interval parsing
        s = s[: time_match.start()] + s[time_match.end() :]

    # ── 2. Extract window: "from Ham/pm to Ham/pm" ──
    window_match = re.search(
        r"\bfrom\s+(noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+"
        r"to\s+(noon|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
        s,
    )
    if window_match:
        start_time = _parse_time(window_match.group(1))
        end_time = _parse_time(window_match.group(2))
        if start_time and end_time:
            config.start_hour = int(start_time.split(":")[0])
            config.end_hour = int(end_time.split(":")[0])
        s = s[: window_match.start()] + s[window_match.end() :]

    # ── 3. Extract days ──
    # "weekdays"
    if re.search(r"\bweekdays?\b", s):
        config.days_of_week = [0, 1, 2, 3, 4]
        s = re.sub(r"\bon\s+weekdays?\b", "", s)
        s = re.sub(r"\bweekdays?\b", "", s)
    # "weekends"
    elif re.search(r"\bweekends?\b", s):
        config.days_of_week = [5, 6]
        s = re.sub(r"\bon\s+weekends?\b", "", s)
        s = re.sub(r"\bweekends?\b", "", s)
    # "mon-fri" style ranges
    elif re.search(r"\b(mon|tue|wed|thu|fri|sat|sun)-(mon|tue|wed|thu|fri|sat|sun)\b", s):
        range_match = re.search(
            r"\b(mon|tue|wed|thu|fri|sat|sun)-(mon|tue|wed|thu|fri|sat|sun)\b", s
        )
        if range_match:
            start_day = _ALL_DAY_NAMES[range_match.group(1)]
            end_day = _ALL_DAY_NAMES[range_match.group(2)]
            if start_day <= end_day:
                config.days_of_week = list(range(start_day, end_day + 1))
            else:
                config.days_of_week = list(range(start_day, 7)) + list(
                    range(0, end_day + 1)
                )
            s = s[: range_match.start()] + s[range_match.end() :]
    else:
        # Individual day names: "on monday and wednesday", "every monday"
        # Also handle "on monday, wednesday, and friday"
        found_days = []
        for name, idx in _ALL_DAY_NAMES.items():
            if re.search(r"\b" + name + r"\b", s):
                if idx not in found_days:
                    found_days.append(idx)
        if found_days:
            config.days_of_week = sorted(found_days)
            # Remove day references from remaining string
            for name in _ALL_DAY_NAMES:
                s = re.sub(r"\bevery\s+" + name + r"\b", "every", s)
                s = re.sub(r"\bon\s+" + name + r"\b", "", s)
                s = re.sub(r"\b" + name + r"\b", "", s)

    # Clean up residual connectors
    s = re.sub(r"\bon\s*$", "", s)
    s = re.sub(r"\band\b", "", s)
    s = s.strip().strip(",").strip()

    # ── 4. Extract interval ──
    # "every Xunit" pattern
    interval_match = re.match(
        r"every\s+(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hr|hours?|d|days?|w|wk|weeks?)",
        s,
    )
    if interval_match:
        value = int(interval_match.group(1))
        unit = interval_match.group(2)
        if unit.startswith("s"):
            config.interval_seconds = value
        elif unit.startswith("m"):
            config.interval_seconds = value * 60
        elif unit.startswith("h"):
            config.interval_seconds = value * 3600
        elif unit.startswith("d"):
            config.interval_seconds = value * 86400
        elif unit.startswith("w"):
            config.interval_seconds = value * 604800
    elif re.search(r"\bevery\s+second\b", s):
        config.interval_seconds = 1
    elif re.search(r"\bevery\s+minute\b", s):
        config.interval_seconds = 60
    elif re.search(r"\bevery\s+hour\b", s):
        config.interval_seconds = 3600
    elif re.search(r"\bevery\s+day\b", s) or re.search(r"\bdaily\b", s):
        config.interval_seconds = 86400
    elif re.search(r"\bevery\s+week\b", s):
        config.interval_seconds = 604800
    elif re.search(r"\bhourly\b", s):
        config.interval_seconds = 3600
    elif re.search(r"\bweekly\b", s):
        config.interval_seconds = 604800
    elif re.search(r"\bminutely\b", s):
        config.interval_seconds = 60
    elif re.search(r"\bevery\b", s) and config.days_of_week and len(config.days_of_week) == 1:
        # "every monday" style -> weekly
        config.interval_seconds = 604800
    else:
        # Try bare "Xh", "Xm" patterns
        bare_match = re.match(r"(\d+)\s*(s|m|h|d|w)", s)
        if bare_match:
            value = int(bare_match.group(1))
            unit = bare_match.group(2)
            if unit == "s":
                config.interval_seconds = value
            elif unit == "m":
                config.interval_seconds = value * 60
            elif unit == "h":
                config.interval_seconds = value * 3600
            elif unit == "d":
                config.interval_seconds = value * 86400
            elif unit == "w":
                config.interval_seconds = value * 604800

    # ── 5. Default: if time_of_day set but no interval, default to daily ──
    if config.time_of_day and config.interval_seconds == 0:
        if config.days_of_week and len(config.days_of_week) == 1:
            config.interval_seconds = 604800  # weekly for single day
        else:
            config.interval_seconds = 86400  # daily

    # ── 6. If days are set and interval is daily but only 1 day, use weekly ──
    if (
        config.days_of_week
        and len(config.days_of_week) == 1
        and config.interval_seconds == 86400
    ):
        config.interval_seconds = 604800

    # ── 7. Build human-readable description ──
    if config.interval_seconds > 0:
        config.description = _build_description(config)
    else:
        config.description = f"Could not parse schedule: '{text}'"

    return config


def _build_description(config: ScheduleConfig) -> str:
    """Build a human-readable description from a ScheduleConfig.

    Args:
        config: Parsed schedule configuration.

    Returns:
        Human-readable description string.
    """
    parts = []

    # Interval part
    if config.start_hour is not None:
        parts.append(f"Every {_format_interval_human(config.interval_seconds)}")
    elif config.time_of_day:
        if config.days_of_week and len(config.days_of_week) == 1:
            day_name = _DAY_NAMES_DISPLAY[config.days_of_week[0]]
            parts.append(f"Every {day_name}")
        elif config.interval_seconds == 86400 or (
            config.days_of_week and len(config.days_of_week) > 1
        ):
            parts.append("Daily")
        else:
            parts.append("Daily")
    else:
        parts.append(f"Every {_format_interval_human(config.interval_seconds)}")

    # Time part
    if config.time_of_day and config.start_hour is None:
        parts.append(f"at {_format_time_12h(config.time_of_day)}")

    # Window part
    if config.start_hour is not None:
        start_str = _format_time_12h(f"{config.start_hour:02d}:00")
        end_h = config.end_hour if config.end_hour is not None else 24
        end_str = _format_time_12h(f"{end_h:02d}:00") if end_h < 24 else "12:00 AM"
        parts.append(f"{start_str} - {end_str}")

    # Days part
    if config.days_of_week:
        days_str = _format_days(config.days_of_week)
        # Avoid duplicating if already in the interval part
        if config.time_of_day and len(config.days_of_week) == 1:
            pass  # Already handled above: "Every Monday at ..."
        else:
            parts.append(days_str)

    return ", ".join(parts)


# ── Next-run computation ─────────────────────────────────────────────────────


def compute_next_run(
    config: ScheduleConfig, after: datetime = None
) -> datetime:
    """Compute the next run time based on schedule config.

    Args:
        config: Parsed schedule configuration.
        after: Reference time (defaults to now UTC).

    Returns:
        Next run datetime in UTC.
    """
    now = after or datetime.now(timezone.utc)

    if config.time_of_day and config.start_hour is None:
        # Fixed time schedule: "daily at 9pm", "every monday at 3pm"
        hour, minute = map(int, config.time_of_day.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        # Advance to valid day
        if config.days_of_week:
            while candidate.weekday() not in config.days_of_week:
                candidate += timedelta(days=1)
        return candidate

    elif config.start_hour is not None:
        # Windowed schedule: "every hour from 8am to 6pm"
        candidate = now + timedelta(seconds=config.interval_seconds)
        end_h = config.end_hour if config.end_hour is not None else 24

        # If past end of window or before start, jump to start of next window
        if candidate.hour >= end_h or candidate.hour < config.start_hour:
            # Check if today's window hasn't started yet
            today_start = now.replace(
                hour=config.start_hour, minute=0, second=0, microsecond=0
            )
            if now < today_start:
                candidate = today_start
            else:
                candidate = (now + timedelta(days=1)).replace(
                    hour=config.start_hour, minute=0, second=0, microsecond=0
                )

        # Skip invalid days
        if config.days_of_week:
            while candidate.weekday() not in config.days_of_week:
                candidate += timedelta(days=1)
                candidate = candidate.replace(
                    hour=config.start_hour, minute=0, second=0, microsecond=0
                )

        return candidate

    elif config.days_of_week:
        # Day-specific without fixed time: find the next valid day
        candidate = now + timedelta(seconds=config.interval_seconds)
        # Advance to the next valid day of week if needed
        for _ in range(7):
            if candidate.weekday() in config.days_of_week:
                return candidate
            candidate += timedelta(days=1)
            # For day-based schedules, reset to same time-of-day
            if config.interval_seconds >= 86400:
                candidate = candidate.replace(
                    hour=now.hour, minute=now.minute, second=0, microsecond=0
                )
        return candidate

    else:
        # Simple interval
        return now + timedelta(seconds=config.interval_seconds)


# ── ScheduledTask ────────────────────────────────────────────────────────────


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
        session_id: str = None,
        schedule_config: str = None,
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
        self.session_id = session_id
        self.schedule_config = schedule_config
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
            "session_id": self.session_id,
            "schedule_config": self.schedule_config,
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

        Tries the natural-language parser first (``parse_schedule_input``).
        Falls back to the simpler ``parse_interval`` for backward
        compatibility.

        Args:
            name: Unique task name.
            interval: Human-readable interval (e.g. "every 6h",
                "daily at 9pm", "every monday at 3pm").
            prompt: The prompt to execute on each run.

        Returns:
            Task dict with status info.

        Raises:
            ValueError: If name is duplicate or interval is invalid.
        """
        # Try natural-language parser first
        config = parse_schedule_input(interval)
        if config.interval_seconds > 0:
            interval_seconds = config.interval_seconds
            schedule_config_json = config.to_json()
        else:
            # Fall back to legacy parse_interval
            interval_seconds = parse_interval(interval)
            config = None
            schedule_config_json = None

        async with self._lock:
            if name in self._tasks:
                raise ValueError(f"Task with name '{name}' already exists")

            task_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)

            if config:
                next_run = compute_next_run(config, after=now)
            else:
                next_run = now + timedelta(seconds=interval_seconds)

            task = ScheduledTask(
                task_id=task_id,
                name=name,
                interval_seconds=interval_seconds,
                prompt=prompt,
                status="active",
                created_at=now.isoformat(),
                next_run_at=next_run.isoformat(),
                schedule_config=schedule_config_json,
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
            config = (
                ScheduleConfig.from_json(task.schedule_config)
                if task.schedule_config
                else None
            )
            if config and (config.time_of_day or config.start_hour is not None):
                next_run = compute_next_run(config)
            else:
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
                config = (
                    ScheduleConfig.from_json(task.schedule_config)
                    if task.schedule_config
                    else None
                )
                if config and (
                    config.time_of_day or config.start_hour is not None
                ):
                    next_dt = compute_next_run(config)
                    sleep_secs = max(
                        0, (next_dt - datetime.now(timezone.utc)).total_seconds()
                    )
                else:
                    sleep_secs = task.interval_seconds

                await asyncio.sleep(sleep_secs)

                if not self._running or task.status != "active":
                    break

                await self._execute_task(task)
        except asyncio.CancelledError:
            logger.debug("Timer cancelled for task '%s'", task.name)
            raise

    async def _execute_task(self, task: ScheduledTask):
        """Execute a single task run.

        If the database supports sessions (i.e. is a full ChatDatabase),
        each schedule gets a dedicated chat session.  Every run adds a
        system divider, the prompt as a user message, and the LLM
        response as an assistant message -- so users can open the session
        and see the full history of scheduled runs.
        """
        now = datetime.now(timezone.utc)
        task.last_run_at = now.isoformat()
        task.run_count += 1

        logger.info(
            "Executing scheduled task '%s' (run #%d)", task.name, task.run_count
        )

        # ── Create / reuse chat session for this schedule ────────────
        has_sessions = hasattr(self._db, "create_session")
        if has_sessions and not task.session_id:
            try:
                session = self._db.create_session(
                    title=f"Schedule: {task.name}"
                )
                task.session_id = session["id"]
                logger.info(
                    "Created session %s for schedule '%s'",
                    task.session_id,
                    task.name,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to create session for schedule '%s': %s",
                    task.name,
                    exc,
                )

        # ── Add run divider + user message ───────────────────────────
        if task.session_id and has_sessions:
            try:
                ts = now.strftime("%Y-%m-%d %H:%M UTC")
                self._db.add_message(
                    task.session_id,
                    "system",
                    f"[schedule-run] Run #{task.run_count} \u00b7 {ts}",
                )
                self._db.add_message(task.session_id, "user", task.prompt)
            except Exception as exc:
                logger.warning(
                    "Failed to add session messages for schedule '%s': %s",
                    task.name,
                    exc,
                )

        # ── Execute ──────────────────────────────────────────────────
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

        # ── Store assistant response in session ──────────────────────
        if task.session_id and has_sessions:
            try:
                content = f"Error: {error}" if error else (result or "(no output)")
                self._db.add_message(task.session_id, "assistant", content)
            except Exception as exc:
                logger.warning(
                    "Failed to store response for schedule '%s': %s",
                    task.name,
                    exc,
                )

        # Update next run
        config = (
            ScheduleConfig.from_json(task.schedule_config)
            if task.schedule_config
            else None
        )
        if config:
            next_run = compute_next_run(config)
        else:
            next_run = datetime.now(timezone.utc) + timedelta(
                seconds=task.interval_seconds
            )
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
                    session_id=row.get("session_id"),
                    schedule_config=row.get("schedule_config"),
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
                    created_at, next_run_at, run_count, error_count,
                    session_id, schedule_config)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    task.session_id,
                    task.schedule_config,
                ),
            )
            self._db._conn.commit()

    def _db_update_task(self, task: ScheduledTask):
        """Update an existing task row."""
        with self._db._lock:
            self._db._conn.execute(
                """UPDATE scheduled_tasks
                   SET status = ?, last_run_at = ?, next_run_at = ?,
                       last_result = ?, run_count = ?, error_count = ?,
                       session_id = ?, schedule_config = ?
                   WHERE id = ?""",
                (
                    task.status,
                    task.last_run_at,
                    task.next_run_at,
                    task.last_result,
                    task.run_count,
                    task.error_count,
                    task.session_id,
                    task.schedule_config,
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
