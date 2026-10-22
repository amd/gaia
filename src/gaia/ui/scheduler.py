# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Async task scheduler for GAIA Agent UI.

Manages recurring scheduled tasks with asyncio timers. Tasks are persisted
in the ChatDatabase and automatically restarted on server startup.

Supports interval strings like "every 6h", "every 30m", "every 24h",
"daily at 9am", "every monday at 3pm", "every hour from 8am to 6pm", etc.

Times of day, hour windows and weekdays are wall-clock values in the
schedule's IANA time zone (``ScheduleConfig.timezone``), so "daily at 9am"
stays at 9am local across DST changes. ``next_run_at`` is stored in UTC.
"""

import asyncio
import calendar
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

#: A run that came due while the PC was off or asleep (or GAIA was closed)
#: still runs if GAIA is back within this long of its time. Older ones are
#: recorded in the history as missed, not run late.
MISSED_RUN_GRACE_SECONDS = 3600
#: Longest a waiting timer goes without re-reading the wall clock, so a sleep
#: or a clock change is noticed this soon after waking.
WAKE_CHECK_SECONDS = 30.0
#: Most clock-face due times walked one by one for a missed stretch; past it
#: the history entry says "at least" and the latest due time is found from
#: the last week instead. Interval tasks are counted exactly, by arithmetic.
_MAX_MISSED_COUNTED = 100_000
#: Clock-face schedules repeat at least weekly, so the latest due time is in
#: this window before now.
_LATEST_DUE_LOOKBACK = timedelta(days=8)
#: Settings key: when this build first started counting missed runs. Due
#: times before it were left by an older build that did not keep a task's
#: time across restarts, so they are not blamed on the PC being off.
MISSED_SINCE_SETTING = "scheduler_missed_runs_since"


@dataclass
class _DuePlan:
    """What came due between a task's stored due time and now."""

    missed: int
    first_missed: Optional[datetime]
    last_missed: Optional[datetime]
    at_least: bool
    run_now: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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

# Upper bound on schedule/interval input length before regex parsing —
# keeps user-provided strings from driving pathological regex backtracking.
_MAX_SCHEDULE_INPUT_LEN = 256


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
    # IANA zone the wall-clock fields above are in; None means this machine's.
    timezone: Optional[str] = None

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "ScheduleConfig":
        """Deserialize from JSON string.

        Raises:
            ValueError: if the JSON is malformed or contains unknown keys —
                a silently-empty config would mean a schedule that never fires.
        """
        if not s:
            return cls()
        try:
            data = json.loads(s)
            return cls(**data)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(
                f"Invalid schedule_config JSON ({e}); raw value: {s!r}. "
                "Delete and recreate the schedule, or fix the row in "
                "the chat database (scheduled_tasks.schedule_config)."
            ) from e


# ── Natural language schedule parser ─────────────────────────────────────────

#: Shortest gap between runs. Each run is a full agent turn on a model, and
#: ``gaia schedule`` (cron) can't run more often than once a minute either.
MIN_INTERVAL_SECONDS = 60
#: Longest gap between runs.
MAX_INTERVAL_SECONDS = 366 * 86400

_MINUTE = 60
_HOUR = 3600
_DAY = 86400
_WEEK = 604800

_EXAMPLES = (
    "'every 30m', 'daily at 9am', 'every monday at 3pm' or "
    "'every hour from 8am to 6pm on weekdays'"
)
_RECURRING_HINT = "Try a repeating form like 'daily at 9am' or 'every friday at 9am'."

_DAY_ALIASES = {"tues": 1, "weds": 2, "thur": 3, "thurs": 3}
_DAY_WORDS = {**_ALL_DAY_NAMES, **_DAY_ALIASES}
# Longest first, so "sunday" wins over "sun".
_DAY_RE = "|".join(sorted(_DAY_WORDS, key=len, reverse=True))

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_MONTH_DISPLAY = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

# Input is whitespace-collapsed before matching, so single spaces are literal.
_TIME_RE = r"(?:noon|midnight|\d{1,2}(?::\d{2})?(?: ?(?:am|pm))?)"
_UNIT_RE = r"(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w)"
_UNIT_SECONDS = {"s": 1, "m": _MINUTE, "h": _HOUR, "d": _DAY, "w": _WEEK}
_SINGLE_UNITS = {
    "second": 1,
    "minute": _MINUTE,
    "hour": _HOUR,
    "day": _DAY,
    "week": _WEEK,
}
_INTERVAL_WORDS = {
    "minutely": _MINUTE,
    "hourly": _HOUR,
    "daily": _DAY,
    "weekly": _WEEK,
}


class _ScheduleInputError(ValueError):
    """The schedule text can't be read exactly as written."""


def _parse_time(text: str) -> str:
    """Parse "9pm", "9:30am", "21:00", "noon" or "midnight" into "HH:MM".

    Raises:
        _ScheduleInputError: the text is not a valid time of day.
    """
    t = text.strip().lower()
    if t == "noon":
        return "12:00"
    if t == "midnight":
        return "00:00"
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?: ?(am|pm))?", t)
    if not m:
        raise _ScheduleInputError(
            f"'{text}' isn't a time — use a form like 9am, 9:30pm or 21:00."
        )
    hour, minute, period = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if period:
        if not 1 <= hour <= 12 or minute > 59:
            raise _ScheduleInputError(
                f"{text} isn't a time — use 1–12 with am or pm (like 9:30am), "
                "or 0:00–23:59."
            )
        return f"{hour % 12 + (12 if period == 'pm' else 0):02d}:{minute:02d}"
    if hour > 23 or minute > 59:
        raise _ScheduleInputError(f"{text} isn't a time — use 0:00–23:59.")
    if m.group(2) is None:
        if hour > 12:
            raise _ScheduleInputError(f"Write the time as {hour}:00.")
        raise _ScheduleInputError(
            f"'{text}' could be morning or evening — say {hour}am, {hour}pm "
            f"or {hour}:00."
        )
    return f"{hour:02d}:{minute:02d}"


def _format_time_12h(time_24: str) -> str:
    """Convert "HH:MM" to "9:00 AM" / "3:30 PM"."""
    h, m = map(int, time_24.split(":"))
    if h == 0:
        return f"12:{m:02d} AM"
    elif h < 12:
        return f"{h}:{m:02d} AM"
    elif h == 12:
        return f"12:{m:02d} PM"
    else:
        return f"{h - 12}:{m:02d} PM"


def _format_every(seconds: int) -> str:
    """ "Every 30 minutes", "Every hour", "Every 90 minutes" — never rounded."""
    for size, unit in ((_WEEK, "week"), (_DAY, "day"), (_HOUR, "hour")):
        if seconds >= size and seconds % size == 0:
            break
    else:
        size, unit = (_MINUTE, "minute") if seconds % _MINUTE == 0 else (1, "second")
    count = seconds // size
    return f"Every {unit}" if count == 1 else f"Every {count} {unit}s"


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
    """ "weekdays", "weekends", "every day", or "Monday, Wednesday"."""
    days_sorted = sorted(days)
    if days_sorted == [0, 1, 2, 3, 4]:
        return "weekdays"
    if days_sorted == [5, 6]:
        return "weekends"
    if days_sorted == list(range(7)):
        return "every day"
    return ", ".join(_DAY_NAMES_DISPLAY[d] for d in days_sorted)


def _take(pattern: str, s: str):
    """First match of *pattern* in *s*, and *s* with that match removed."""
    m = re.search(pattern, s)
    if not m:
        return None, s
    return m, f"{s[: m.start()]} {s[m.end() :]}"


def _reject_dates_and_one_offs(s: str) -> None:
    """Reject calendar dates and one-time phrasing: every schedule repeats."""
    one_time = (
        "One-time tasks aren't supported yet — a schedule repeats. " + _RECURRING_HINT
    )
    for pattern, month_group, day_group in (
        (rf"\b({_MONTH_RE})\.? (\d{{1,2}})(?:st|nd|rd|th)?\b", 1, 2),
        (rf"\b(\d{{1,2}})(?:st|nd|rd|th)? (?:of )?({_MONTH_RE})\b", 2, 1),
    ):
        m = re.search(pattern, s)
        if m:
            month = _MONTHS[m.group(month_group)]
            day = int(m.group(day_group))
            # 2024 is a leap year, so only dates no year has are refused.
            if not 1 <= day <= calendar.monthrange(2024, month)[1]:
                raise _ScheduleInputError(
                    f"{_MONTH_DISPLAY[month]} {day} isn't a date. "
                    "One-time tasks aren't supported yet either — a schedule "
                    "repeats. " + _RECURRING_HINT
                )
            raise _ScheduleInputError(one_time)
    if re.search(r"\b(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{1,2}-\d{1,2})\b", s):
        raise _ScheduleInputError(one_time)
    if re.search(
        r"\b(?:monthly|yearly|annually|every (?:other |\d{1,9} )?(?:months?|years?))\b",
        s,
    ):
        raise _ScheduleInputError(
            "Monthly and yearly schedules aren't supported yet — use days or "
            "weeks, like 'every 4 weeks' or 'every monday at 9am'."
        )
    if re.search(
        rf"\b(?:tomorrow|today|tonight|once(?! (?:a|an|every|per)\b)|"
        rf"(?:next|this) (?:week|month|year|morning|afternoon|evening|{_DAY_RE})|"
        rf"in (?:a|an|\d{{1,9}}) ?{_UNIT_RE})\b",
        s,
    ):
        raise _ScheduleInputError(one_time)


def _take_days(s: str):
    """Pull every weekday reference out of *s*; returns (days, rest)."""
    found = set()
    prefix = r"\b(?:(?:every|on) )?"

    def group(match):
        found.update(range(0, 5) if match.group(1) == "weekday" else (5, 6))
        return " "

    s = re.sub(prefix + r"(weekday|weekend)s?\b", group, s)

    def span(match):
        start, end = _DAY_WORDS[match.group(1)], _DAY_WORDS[match.group(2)]
        found.update(
            range(start, end + 1)
            if start <= end
            else [*range(start, 7), *range(0, end + 1)]
        )
        return " "

    s = re.sub(
        prefix + rf"({_DAY_RE})s? ?(?:-|to|through|thru) ?({_DAY_RE})s?\b", span, s
    )

    def single(match):
        found.add(_DAY_WORDS[match.group(1)])
        return " "

    s = re.sub(prefix + rf"({_DAY_RE})s?\b", single, s)
    return sorted(found), s


def _take_interval(s: str):
    """Pull one interval out of *s*; returns (seconds, phrase, rest)."""
    m, rest = _take(rf"\bevery (\d{{1,9}}) ?{_UNIT_RE}\b", s)
    if m is None:
        m, rest = _take(rf"\b(\d{{1,9}}) ?{_UNIT_RE}\b", s)
    if m is not None:
        unit = m.group(2)
        # "m" is minutes; the month forms were rejected earlier.
        seconds = int(m.group(1)) * _UNIT_SECONDS[unit[0]]
        return seconds, m.group(0), rest
    m, rest = _take(r"\bevery (second|minute|hour|day|week)\b", s)
    if m is not None:
        return _SINGLE_UNITS[m.group(1)], m.group(0), rest
    m, rest = _take(r"\b(minutely|hourly|daily|weekly)\b", s)
    if m is not None:
        return _INTERVAL_WORDS[m.group(1)], m.group(0), rest
    return None, None, s


def _parse_schedule(text: str) -> ScheduleConfig:
    """Strict parse; raises :class:`_ScheduleInputError` with what to fix."""
    if len(text) > _MAX_SCHEDULE_INPUT_LEN:
        raise _ScheduleInputError(
            f"That schedule is too long ({len(text)} characters, max "
            f"{_MAX_SCHEDULE_INPUT_LEN}). Use a short form like {_EXAMPLES}."
        )
    s = " ".join(text.lower().split()).rstrip(".")
    if not s:
        raise _ScheduleInputError(f"Describe when to run it, for example {_EXAMPLES}.")

    _reject_dates_and_one_offs(s)

    config = ScheduleConfig(raw_input=text)

    times = re.findall(rf"\bat ({_TIME_RE})\b", s)
    if len(times) > 1:
        raise _ScheduleInputError(
            "Give one time per schedule — for two times a day, create two schedules."
        )
    if times:
        config.time_of_day = _parse_time(times[0])
        _, s = _take(rf"\bat {_TIME_RE}\b", s)

    window, s = _take(
        rf"\b(?:from|between) ({_TIME_RE})(?: (?:to|until|till|and) | ?- ?)"
        rf"({_TIME_RE})\b",
        s,
    )
    if window:
        start, end = _parse_time(window.group(1)), _parse_time(window.group(2))
        if not (start.endswith(":00") and end.endswith(":00")):
            raise _ScheduleInputError(
                "A time window starts and ends on the hour — use a form like "
                "'from 9am to 5pm'."
            )
        config.start_hour, config.end_hour = int(start[:2]), int(end[:2])

    other = re.search(rf"\bevery other ((?:{_DAY_RE})s?|weekdays?|weekends?)\b", s)
    if other:
        day = other.group(1)
        raise _ScheduleInputError(
            f"'every other {day}' isn't supported yet — use 'every {day}' to "
            "run weekly, or 'every 2 weeks' to run every two weeks from now."
        )
    s = re.sub(r"\bevery other (?=(?:second|minute|hour|day|week)s?\b)", "every 2 ", s)

    days, s = _take_days(s)
    interval, phrase, s = _take_interval(s)

    leftover = " ".join(re.sub(r"[,&]|\band\b", " ", s).split())
    if leftover:
        stray_time = re.search(rf"(?<![\w:]){_TIME_RE}(?![\w:])", leftover)
        if stray_time and re.search(r"[:a-z]", stray_time.group(0)):
            _parse_time(stray_time.group(0))
            raise _ScheduleInputError(
                f"Put 'at' before the time, like 'daily at {stray_time.group(0)}'."
            )
        raise _ScheduleInputError(
            f"GAIA couldn't read '{leftover}'. Use a form like {_EXAMPLES}."
        )

    if days:
        config.days_of_week = days
    if window and config.time_of_day:
        raise _ScheduleInputError(
            "Use either a time ('at 9am') or a window ('from 8am to 6pm'), not both."
        )
    if window:
        if interval is None:
            raise _ScheduleInputError(
                "Say how often to run inside the window, like 'every hour from "
                "8am to 6pm'."
            )
        if interval >= _DAY:
            raise _ScheduleInputError(
                f"'{phrase}' doesn't fit inside a daily window — use a shorter "
                "interval, like 'every 2 hours from 8am to 6pm'."
            )
    elif config.time_of_day or days:
        if interval is None:
            interval = _WEEK if len(days) == 1 else _DAY
        elif interval == _DAY and len(days) == 1:
            interval = _WEEK
        if interval == _WEEK and not days:
            raise _ScheduleInputError(
                "Say which day to run on, like 'every monday at 9am'."
            )
        if config.time_of_day and interval not in (_DAY, _WEEK):
            raise _ScheduleInputError(
                f"'{phrase}' can't be combined with a time of day yet — use "
                f"'daily at {times[0]}', or drop the time to run "
                f"{phrase.lower()} from now."
            )
        if days and interval > _DAY and interval != _WEEK:
            raise _ScheduleInputError(
                f"'{phrase}' can't be combined with set days yet — name the "
                f"days alone ('every monday' runs weekly), or use '{phrase}' "
                "alone to count from now."
            )
    elif interval is None:
        raise _ScheduleInputError(f"Say how often to run it, like {_EXAMPLES}.")

    if interval < MIN_INTERVAL_SECONDS:
        raise _ScheduleInputError(
            f"'{phrase}' is too often — each run is a full agent turn, so a "
            "schedule can run at most once a minute. Use 'every 1m' or longer."
        )
    if interval > MAX_INTERVAL_SECONDS:
        raise _ScheduleInputError(
            f"'{phrase}' is too far apart — a schedule must run at least once a "
            "year."
        )
    config.interval_seconds = interval
    config.description = _build_description(config)
    return config


def parse_schedule_input(text: str) -> ScheduleConfig:
    """Parse a natural language schedule description into a ScheduleConfig.

    Handles inputs such as:
        - Intervals: "every 30m", "every 2 hours", "every other day", "daily",
          "hourly", "weekly"
        - Time of day: "daily at 9pm", "at 9:30am", "every day at 10am"
        - Days and time: "every monday at 3pm", "weekdays at 10am",
          "every weekday", "mon-fri at noon"
        - Windows: "every hour from 8am to 6pm",
          "every 2 hours from 8am to 6pm on weekdays"

    Every word must be understood. Anything else — an invalid time, a date,
    one-time phrasing ("tomorrow"), an interval under
    :data:`MIN_INTERVAL_SECONDS`, or leftover words — is rejected rather
    than read as something close.

    Returns:
        ScheduleConfig with parsed fields. If the input is rejected,
        interval_seconds is 0 and description says what to change.
    """
    try:
        return _parse_schedule(text)
    except _ScheduleInputError as e:
        return ScheduleConfig(raw_input=text, description=str(e))


def _build_description(config: ScheduleConfig) -> str:
    """Human-readable description of a parsed schedule."""
    days = config.days_of_week
    if config.start_hour is not None:
        start = _format_time_12h(f"{config.start_hour:02d}:00")
        end_h = config.end_hour if config.end_hour is not None else 0
        text = f"{_format_every(config.interval_seconds)}, {start} - "
        text += _format_time_12h(f"{end_h:02d}:00")
        return f"{text}, {_format_days(days)}" if days else text

    if days and config.interval_seconds in (_DAY, _WEEK):
        named = _format_days(days)
        if named == "every day":
            text = "Daily"
        elif named in ("weekdays", "weekends"):
            text = named.capitalize()
        else:
            text = f"Every {named}"
    elif config.time_of_day:
        text = "Daily"
    else:
        text = _format_every(config.interval_seconds)
        if days:
            text += f" on {_format_days(days)}"

    if config.time_of_day:
        text += f" at {_format_time_12h(config.time_of_day)}"
    return text


# ── Next-run computation ─────────────────────────────────────────────────────


def local_timezone_name() -> str:
    """IANA name of this machine's time zone (honours the ``TZ`` variable)."""
    import tzlocal

    name = tzlocal.get_localzone_name()
    if not name:
        raise RuntimeError(
            "Could not determine this PC's time zone, so a schedule like "
            "'daily at 9am' has no local 9am to run at. Set the TZ environment "
            "variable to an IANA zone (for example America/Los_Angeles) and "
            "restart GAIA."
        )
    return name


def resolve_zone(name: Optional[str]) -> ZoneInfo:
    """Return the zone a schedule's wall-clock fields are in.

    Raises:
        ValueError: if ``name`` is not a known IANA time zone.
    """
    name = name or local_timezone_name()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(
            f"Unknown time zone {name!r}. Use an IANA name such as "
            "'America/Los_Angeles' or 'Europe/Berlin'."
        ) from e


def _is_wall_clock(config: ScheduleConfig) -> bool:
    """True when the schedule depends on the clock face, not just elapsed time."""
    return bool(
        config.time_of_day or config.start_hour is not None or config.days_of_week
    )


def _at_local(
    day: date, hour: int, minute: int, zone: ZoneInfo, second: int = 0
) -> datetime:
    """Wall-clock time on ``day`` in ``zone``, returned in UTC.

    A time skipped by a DST jump lands just after the jump; a time that
    repeats when clocks fall back runs at its first occurrence only.
    """
    return datetime(
        day.year, day.month, day.day, hour, minute, second, tzinfo=zone
    ).astimezone(timezone.utc)


def compute_next_run(config: ScheduleConfig, after: datetime = None) -> datetime:
    """Compute the next run time based on schedule config.

    Times of day, windows and weekdays are read in ``config.timezone``.

    Args:
        config: Parsed schedule configuration.
        after: Reference time (defaults to now UTC).

    Returns:
        Next run datetime in UTC.
    """
    now = (after or datetime.now(timezone.utc)).astimezone(timezone.utc)
    interval = timedelta(seconds=config.interval_seconds)

    if not _is_wall_clock(config):
        return now + interval

    zone = resolve_zone(config.timezone)
    local_now = now.astimezone(zone)
    days = config.days_of_week

    if config.time_of_day and config.start_hour is None:
        # Fixed time schedule: "daily at 9pm", "every monday at 3pm"
        hour, minute = map(int, config.time_of_day.split(":"))
        day = local_now.date()
        for _ in range(9):
            candidate = _at_local(day, hour, minute, zone)
            if candidate > now and (not days or day.weekday() in days):
                return candidate
            day += timedelta(days=1)
        raise ValueError(f"No valid run day in schedule config {config!r}")

    if config.start_hour is not None:
        # Windowed schedule: "every hour from 8am to 6pm"
        candidate = now + interval
        start_h = config.start_hour
        end_h = config.end_hour if config.end_hour is not None else 24
        # "10pm to 2am" (or same start and end) wraps past midnight.
        wraps = end_h <= start_h
        local = candidate.astimezone(zone)

        if wraps:
            inside = local.hour >= start_h or local.hour < end_h
        else:
            inside = start_h <= local.hour < end_h

        if not inside:
            start_on_day = _at_local(local.date(), start_h, 0, zone)
            if candidate < start_on_day:
                candidate = start_on_day
            else:
                candidate = _at_local(
                    local.date() + timedelta(days=1), start_h, 0, zone
                )

        if days:
            local = candidate.astimezone(zone)
            # The early-morning part of a wrapping window belongs to the day it opened.
            window_day = local.date()
            if wraps and local.hour < start_h:
                window_day -= timedelta(days=1)
            if window_day.weekday() not in days:
                while window_day.weekday() not in days:
                    window_day += timedelta(days=1)
                candidate = _at_local(window_day, start_h, 0, zone)

        return candidate

    if config.interval_seconds in (_DAY, _WEEK):
        # "every tuesday" with no time: the next listed day, at this clock time.
        day = local_now.date()
        for _ in range(7):
            day += timedelta(days=1)
            if day.weekday() in days:
                return _at_local(day, local_now.hour, local_now.minute, zone)
        raise ValueError(f"No valid run day in schedule config {config!r}")

    # Day-specific without fixed time: find the next valid day
    candidate = now + interval
    for _ in range(7):
        local = candidate.astimezone(zone)
        if local.weekday() in days:
            return candidate
        if config.interval_seconds >= 86400:
            # Day-based schedules keep the same local time-of-day
            candidate = _at_local(
                local.date() + timedelta(days=1), local_now.hour, local_now.minute, zone
            )
        else:
            candidate = _at_local(
                local.date() + timedelta(days=1),
                local.hour,
                local.minute,
                zone,
                local.second,
            )
    return candidate


def _parse_due(task: "ScheduledTask") -> datetime:
    """``task.next_run_at`` as an aware UTC datetime.

    Raises:
        RuntimeError: the stored value is not an ISO timestamp.
    """
    try:
        due = datetime.fromisoformat(task.next_run_at)
    except ValueError as e:
        raise RuntimeError(
            f"Scheduled task '{task.name}' has an unreadable next_run_at "
            f"{task.next_run_at!r}. Fix or clear the row in the chat database "
            "(scheduled_tasks.next_run_at); a cleared value is recomputed."
        ) from e
    return due if due.tzinfo else due.replace(tzinfo=timezone.utc)


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

        scheduler = Scheduler(db, executor=run_prompt)
        await scheduler.start()  # Load & start persisted tasks
        await scheduler.create_task("daily-report", "every 24h", "Summarize today's news")
        ...
        await scheduler.shutdown()  # Cancel all timers
    """

    def __init__(self, db, executor: Callable, max_concurrent_runs: int = 1):
        """Initialize the scheduler.

        Args:
            db: ChatDatabase instance with scheduled_tasks table.
            executor: Async callable(prompt: str) -> str that executes a task.
            max_concurrent_runs: Maximum number of task executions allowed to
                run at the same time (default 1 — the local LLM backend is
                single-tenant per model slot).

        Raises:
            ValueError: if executor is missing or not callable — a scheduler
                without an executor would silently no-op every run.
        """
        if executor is None or not callable(executor):
            raise ValueError(
                "Scheduler requires a callable executor(prompt) -> str. "
                "Pass the agent-execution coroutine from the server lifespan "
                "(src/gaia/ui/server.py) or a fake executor in tests."
            )
        if max_concurrent_runs < 1:
            raise ValueError(
                f"max_concurrent_runs must be >= 1, got {max_concurrent_runs}"
            )
        self._db = db
        self._executor = executor
        self._max_concurrent_runs = max_concurrent_runs
        self._run_semaphore: Optional[asyncio.Semaphore] = None
        self._tasks: Dict[str, ScheduledTask] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._missed_since: Optional[datetime] = None
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
        """Start the scheduler: load persisted tasks and start timers.

        Raises:
            Exception: any database/schema error from loading persisted
                tasks propagates — a scheduler that silently boots with zero
                tasks would hide data loss.
        """
        self._running = True
        # Semaphore must be created on the running event loop.
        self._run_semaphore = asyncio.Semaphore(self._max_concurrent_runs)
        self._missed_since = self._load_missed_since()
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
        tz: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new scheduled task.

        The schedule is read by ``parse_schedule_input``; anything it
        rejects is rejected here too, with its explanation.

        Args:
            name: Unique task name.
            interval: Human-readable interval (e.g. "every 6h",
                "daily at 9pm", "every monday at 3pm").
            prompt: The prompt to execute on each run.
            tz: IANA time zone the user meant the schedule in; defaults to
                this machine's zone.

        Returns:
            Task dict with status info.

        Raises:
            ValueError: If name is duplicate, interval is invalid, or ``tz``
                is not a known time zone.
        """
        config = parse_schedule_input(interval)
        if config.interval_seconds <= 0:
            raise ValueError(config.description)
        config.timezone = resolve_zone(tz).key
        interval_seconds = config.interval_seconds
        schedule_config_json = config.to_json()

        async with self._lock:
            if name in self._tasks:
                raise ValueError(f"Task with name '{name}' already exists")

            task_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)

            next_run = compute_next_run(config, after=now)

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
            if config and (
                config.time_of_day
                or config.start_hour is not None
                or config.days_of_week
            ):
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
        """Run the timer loop for a single task.

        The task runs at its stored ``next_run_at``, so a restart keeps its
        time. Each due run is claimed in the database before it runs, so
        servers sharing one database run it once between them; runs whose
        time passed while nothing could run them are recorded as missed (see
        :meth:`_plan`).
        """
        try:
            while self._running and task.status == "active":
                due = self._due_at(task)
                await self._sleep_until(due)

                if not self._running or task.status != "active":
                    break
                try:
                    await self._take_due_run(task, due)
                except Exception:  # pylint: disable=broad-except
                    # One bad pass must not stop the schedule until a restart.
                    logger.exception(
                        "Scheduled task '%s': could not handle its run due %s; "
                        "trying again in %ds",
                        task.name,
                        due.isoformat(),
                        WAKE_CHECK_SECONDS,
                    )
                    await asyncio.sleep(WAKE_CHECK_SECONDS)
        except asyncio.CancelledError:
            logger.debug("Timer cancelled for task '%s'", task.name)
            raise

    async def _take_due_run(self, task: ScheduledTask, due: datetime) -> None:
        """Claim the run due at *due*, record what was missed, and run it."""
        now = _utcnow()
        plan = self._plan(task, due, now)
        claimed = self._following(task, now).isoformat()
        if not self._db.claim_scheduled_run(task.id, task.next_run_at, claimed):
            # Another server sharing this database took this run, or paused it.
            stored = self._db.scheduled_task_timing(task.id)
            logger.info(
                "Scheduled task '%s': run due %s was taken by another GAIA "
                "server, or the task is now %s",
                task.name,
                due.isoformat(),
                stored["status"],
            )
            # A pause elsewhere stops this timer too.
            task.status = stored["status"]
            task.next_run_at = stored["next_run_at"]
            return
        task.next_run_at = claimed
        if plan.missed:
            self._record_missed(task, plan)
        if plan.run_now:
            await self._execute_task(task)

    @staticmethod
    def _config(task: ScheduledTask) -> Optional[ScheduleConfig]:
        return (
            ScheduleConfig.from_json(task.schedule_config)
            if task.schedule_config
            else None
        )

    def _following(self, task: ScheduledTask, after: datetime) -> datetime:
        """The task's first due time after *after*."""
        config = self._config(task)
        if config:
            return compute_next_run(config, after=after)
        return after + timedelta(seconds=task.interval_seconds)

    def _due_at(self, task: ScheduledTask) -> datetime:
        if task.next_run_at:
            return _parse_due(task)
        return self._following(task, _utcnow())

    @staticmethod
    async def _sleep_until(due: datetime) -> None:
        # Short naps, so waking from a sleep is noticed within one of them.
        while True:
            left = (due - _utcnow()).total_seconds()
            if left <= 0:
                return
            await asyncio.sleep(min(left, WAKE_CHECK_SECONDS))

    def _load_missed_since(self) -> Optional[datetime]:
        if not hasattr(self._db, "get_setting"):
            return None
        stamp = self._db.get_setting(MISSED_SINCE_SETTING)
        if stamp is None:
            stamp = _utcnow().isoformat()
            self._db.set_setting(MISSED_SINCE_SETTING, stamp)
        return datetime.fromisoformat(stamp)

    def _missed_floor(self, task: ScheduledTask) -> Optional[datetime]:
        """Due times before this are not counted as missed."""
        floors = [self._missed_since]
        if task.created_at:
            created = datetime.fromisoformat(task.created_at)
            floors.append(
                created if created.tzinfo else created.replace(tzinfo=timezone.utc)
            )
        floors = [f for f in floors if f is not None]
        return max(floors) if floors else None

    def _plan(self, task: ScheduledTask, due: datetime, now: datetime) -> _DuePlan:
        """Which due times between *due* and *now* nothing ran.

        The latest still runs if it is within :data:`MISSED_RUN_GRACE_SECONDS`;
        every other one counts as missed, except those before
        :meth:`_missed_floor`. Nothing is run twice to catch up.
        """
        floor = self._missed_floor(task)
        config = self._config(task)
        grace = timedelta(seconds=MISSED_RUN_GRACE_SECONDS)
        if config is None or not _is_wall_clock(config):
            return self._plan_interval(task, due, now, floor, grace)

        # Walk the clock-face due times that have passed since the floor; the
        # ones before it are never counted, so they are not walked either.
        moment = due
        if floor is not None and floor > due:
            moment = compute_next_run(config, after=floor - timedelta(microseconds=1))
        counted = 0
        first: Optional[datetime] = None
        last_walked: Optional[datetime] = None
        before_last: Optional[datetime] = None
        latest: Optional[datetime] = None
        previous: Optional[datetime] = None
        at_least = False
        while moment <= now:
            counted += 1
            first = first or moment
            before_last, last_walked = last_walked, moment
            if counted >= _MAX_MISSED_COUNTED:
                at_least = True
                break
            moment = compute_next_run(config, after=moment)
        if at_least:
            previous, latest = self._latest_due(config, now)
            latest = max(latest or last_walked, last_walked)
        elif last_walked is not None:
            latest = last_walked
        if latest is None:
            # Nothing came due since the floor; an older due time may still run.
            _, latest = self._latest_due(config, now)
            return _DuePlan(
                missed=0,
                first_missed=None,
                last_missed=None,
                at_least=False,
                run_now=latest is not None and now - latest <= grace,
            )
        run_now = now - latest <= grace
        beyond_walk = latest != last_walked  # found by the lookback, not walked
        missed = counted + (1 if beyond_walk else 0) - (1 if run_now else 0)
        if not run_now:
            last = latest
        elif beyond_walk:
            last = max(previous or last_walked, last_walked)
        else:
            last = before_last
        return _DuePlan(
            missed=missed,
            first_missed=first if missed > 0 else None,
            last_missed=last if missed > 0 else None,
            at_least=at_least,
            run_now=run_now,
        )

    @staticmethod
    def _latest_due(config: ScheduleConfig, now: datetime):
        """The last two due times at or before *now*, from the last week."""
        previous = None
        latest = None
        moment = compute_next_run(config, after=now - _LATEST_DUE_LOOKBACK)
        while moment <= now:
            previous, latest = latest, moment
            moment = compute_next_run(config, after=moment)
        return previous, latest

    @staticmethod
    def _plan_interval(task, due, now, floor, grace) -> _DuePlan:
        step = timedelta(seconds=task.interval_seconds)
        periods = int((now - due) / step) + 1  # due times from due to now
        latest = due + (periods - 1) * step
        run_now = now - latest <= grace
        last_index = periods - 2 if run_now else periods - 1
        first_index = 0
        if floor is not None and floor > due:
            first_index = -(-(floor - due) // step)  # ceil
        missed = max(0, last_index - first_index + 1)
        return _DuePlan(
            missed=missed,
            first_missed=due + first_index * step if missed else None,
            last_missed=due + last_index * step if missed else None,
            at_least=False,
            run_now=run_now,
        )

    def _record_missed(self, task: ScheduledTask, plan: _DuePlan) -> None:
        config = self._config(task)
        zone = resolve_zone(config.timezone if config else None)

        def when(moment: datetime) -> str:
            return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")

        reason = "while the PC was off or asleep, or GAIA was closed"
        if plan.missed == 1 and not plan.at_least:
            message = f"Missed the run due {when(plan.first_missed)} {reason}."
        else:
            count = f"at least {plan.missed}" if plan.at_least else str(plan.missed)
            message = (
                f"Missed {count} runs due {when(plan.first_missed)} to "
                f"{when(plan.last_missed)} {reason}."
            )
        logger.warning("Scheduled task '%s': %s", task.name, message)
        self._db_store_result(task.id, plan.last_missed.isoformat(), error=message)

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
        if (
            has_sessions
            and task.session_id
            and not self._db.get_session(task.session_id)
        ):
            logger.warning(
                "Chat for schedule '%s' was deleted; starting a new one", task.name
            )
            task.session_id = None
        if has_sessions and not task.session_id:
            try:
                session = self._db.create_session(
                    title=f"Schedule: {task.name}", kind="scheduled"
                )
                task.session_id = session["id"]
                logger.info(
                    "Created session %s for schedule '%s'",
                    task.session_id,
                    task.name,
                )
            except Exception as exc:
                # Non-fatal by design: a failed history write must not kill
                # the run — but it must be loud.
                logger.error(
                    "Failed to create session for schedule '%s': %s",
                    task.name,
                    exc,
                    exc_info=True,
                )

        # ── Add run divider + user message ───────────────────────────
        if task.session_id and has_sessions:
            try:
                zone = resolve_zone(
                    ScheduleConfig.from_json(task.schedule_config).timezone
                    if task.schedule_config
                    else None
                )
                ts = now.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")
                self._db.add_message(
                    task.session_id,
                    "system",
                    f"[schedule-run] Run #{task.run_count} \u00b7 {ts}",
                )
                # Marked as an unfinished turn until its reply is saved, so a
                # restart mid-run says the run was interrupted.
                self._db.begin_turn(task.session_id, task.prompt)
            except Exception as exc:
                logger.error(
                    "Failed to add session messages for schedule '%s': %s",
                    task.name,
                    exc,
                    exc_info=True,
                )

        # ── Execute (bounded by the concurrency semaphore) ───────────
        result = None
        error = None
        try:
            async with self._run_semaphore:
                result = await self._executor(task.prompt)
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
                self._db.add_message(
                    task.session_id, "assistant", content, ends_turn=True
                )
            except Exception as exc:
                logger.error(
                    "Failed to store response for schedule '%s': %s",
                    task.name,
                    exc,
                    exc_info=True,
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
        """Load persisted tasks from database and start active timers.

        Database/schema errors propagate — a scheduler that silently boots
        with zero tasks hides data loss (CLAUDE.md: no silent fallbacks).
        """
        rows = self._db_list_tasks()
        for row in rows:
            try:
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
            except KeyError as e:
                raise RuntimeError(
                    f"scheduled_tasks row {row.get('id')!r} is missing column {e}; "
                    "the schedule schema is out of date — check "
                    "src/gaia/ui/database.py."
                ) from e
            if task.next_run_at:
                # Same reason: the timer reads it on its first pass.
                _parse_due(task)
            if task.schedule_config:
                # Validate eagerly so a corrupt row fails the boot loudly
                # instead of killing the timer coroutine unobserved later.
                config = ScheduleConfig.from_json(task.schedule_config)
                if config.timezone is None and _is_wall_clock(config):
                    self._adopt_local_zone(task, config)
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

    def _adopt_local_zone(self, task: ScheduledTask, config: ScheduleConfig):
        """Pin a pre-time-zone schedule to this machine's zone and persist it.

        Older builds computed "daily at 9am" in UTC while showing "9:00 AM",
        so the local reading is the one the user saw and asked for.
        """
        config.timezone = resolve_zone(None).key
        task.schedule_config = config.to_json()
        if task.status == "active":
            task.next_run_at = compute_next_run(config).isoformat()
        self._db_update_task(task)
        logger.info(
            "Schedule '%s' now runs in local time zone %s (was UTC)",
            task.name,
            config.timezone,
        )

    def _db_create_task(self, task: ScheduledTask):
        """Insert a new task row via the public ChatDatabase API."""
        self._db.create_scheduled_task(
            {
                "id": task.id,
                "name": task.name,
                "interval_seconds": task.interval_seconds,
                "prompt": task.prompt,
                "status": task.status,
                "created_at": task.created_at,
                "next_run_at": task.next_run_at,
                "run_count": task.run_count,
                "error_count": task.error_count,
                "session_id": task.session_id,
                "schedule_config": task.schedule_config,
            }
        )

    def _db_update_task(self, task: ScheduledTask):
        """Update an existing task row via the public ChatDatabase API."""
        self._db.update_scheduled_task(
            task.id,
            status=task.status,
            last_run_at=task.last_run_at,
            next_run_at=task.next_run_at,
            last_result=task.last_result,
            run_count=task.run_count,
            error_count=task.error_count,
            session_id=task.session_id,
            schedule_config=task.schedule_config,
        )

    def _db_delete_task(self, task_id: str):
        """Delete a task row and its results."""
        self._db.delete_scheduled_task(task_id)

    def _db_list_tasks(self) -> List[Dict[str, Any]]:
        """Load all tasks from database."""
        return self._db.list_scheduled_tasks()

    def _db_store_result(
        self, task_id: str, timestamp: str, result: str = None, error: str = None
    ):
        """Store a task execution result."""
        self._db.store_schedule_result(
            str(uuid.uuid4()), task_id, timestamp, result, error
        )

    def _db_get_results(self, task_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get past results for a task, newest first."""
        return self._db.get_schedule_results(task_id, limit)
