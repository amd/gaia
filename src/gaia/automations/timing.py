# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The time phrases an automation takes: "10m", "17:30", "every 6h", "22:00-07:00"."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

#: Shorter than this, a repeating automation is a cost and noise problem.
MIN_INTERVAL_SECONDS = 5 * 60
#: A one-shot further away than this is a calendar entry, not a reminder.
MAX_DELAY_SECONDS = 366 * 86400

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}
_UNIT_WORDS = r"(m|mins?|minutes?|h|hrs?|hours?|d|days?|w|wks?|weeks?)"
_DURATION_RE = re.compile(
    rf"^(?:in\s+|\+)?((?:\s*\d+\s*{_UNIT_WORDS}\s*(?:and\s*)?)+)$", re.IGNORECASE
)
_PART_RE = re.compile(rf"(\d+)\s*{_UNIT_WORDS}", re.IGNORECASE)
_CLOCK_RE = re.compile(
    r"^(?:at\s+)?(?P<day>today|tomorrow)?\s*(?:at\s+)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?$",
    re.IGNORECASE,
)
_QUIET_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


class TimingError(ValueError):
    """A time phrase GAIA cannot read; the message shows ones it can."""


def _seconds(text: str) -> Optional[int]:
    match = _DURATION_RE.match(text.strip())
    if not match:
        return None
    return sum(
        int(n) * _UNIT_SECONDS[unit[0].lower()]
        for n, unit in _PART_RE.findall(match.group(1))
    )


def parse_interval(text: str) -> int:
    """``"6h"``, ``"30 minutes"``, ``"1d"`` -> seconds, at least 5 minutes."""
    seconds = _seconds(re.sub(r"^every\s+", "", text.strip(), flags=re.IGNORECASE))
    if seconds is None:
        raise TimingError(
            f"{text!r} is not an interval. Use a number and a unit, like 30m, 6h "
            "or 1d."
        )
    if seconds < MIN_INTERVAL_SECONDS:
        raise TimingError("The shortest interval is 5 minutes.")
    return seconds


def parse_when(text: str, now: datetime) -> datetime:
    """When a one-shot fires: ``"10m"``, ``"in 2 hours"``, ``"17:30"``, ``"5pm"``,
    ``"tomorrow 9am"`` or an ISO time. *now* is timezone-aware local time; a clock
    time means its next occurrence.
    """
    examples = "like 10m, in 2 hours, 17:30, 5pm, tomorrow 9am or 2026-12-01T09:00"
    text = text.strip()
    seconds = _seconds(text)
    if seconds is not None:
        if seconds <= 0 or seconds > MAX_DELAY_SECONDS:
            raise TimingError(
                f"{text!r} is not within the next year; say it {examples}."
            )
        return now + timedelta(seconds=seconds)
    clock = _CLOCK_RE.match(text)
    if clock:
        hour, minute = int(clock.group("h")), int(clock.group("m") or 0)
        ampm = (clock.group("ampm") or "").lower()
        if ampm:
            if not 1 <= hour <= 12:
                raise TimingError(f"{text!r} is not a time; say it {examples}.")
            hour = hour % 12 + (12 if ampm == "pm" else 0)
        if hour > 23 or minute > 59:
            raise TimingError(f"{text!r} is not a time; say it {examples}.")
        wall = now.replace(
            tzinfo=None, hour=hour, minute=minute, second=0, microsecond=0
        )
        day = (clock.group("day") or "").lower()
        if day == "tomorrow":
            wall += timedelta(days=1)
        elif wall <= now.replace(tzinfo=None):
            if day == "today":
                raise TimingError(f"{text!r} has already passed today.")
            wall += timedelta(days=1)
        return _local(wall)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as e:
        raise TimingError(
            f"{text!r} is not a time GAIA can read; say it {examples}."
        ) from e
    if moment.tzinfo is None:
        moment = _local(moment)
    if moment <= now:
        raise TimingError(f"{text!r} is in the past.")
    if (moment - now).total_seconds() > MAX_DELAY_SECONDS:
        raise TimingError(f"{text!r} is more than a year away.")
    return moment


def _local(wall: datetime) -> datetime:
    """A naive local wall-clock time, with the UTC offset its own date has."""
    return datetime.fromtimestamp(wall.timestamp()).astimezone()


def parse_quiet(text: str) -> Tuple[int, int]:
    """``"22:00-07:00"`` -> minutes after midnight for its start and end."""
    match = _QUIET_RE.match(text.strip())
    if not match:
        raise TimingError(f"{text!r} is not quiet hours; write it like 22:00-07:00.")
    h1, m1, h2, m2 = (int(g) for g in match.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        raise TimingError(f"{text!r} is not quiet hours; write it like 22:00-07:00.")
    return h1 * 60 + m1, h2 * 60 + m2


_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_WORDS = {"weekdays": "mon-fri", "weekends": "sat,sun", "daily": "mon-sun"}


def parse_days(text: str) -> str:
    """``"mon-fri"``, ``"weekdays"``, ``"sat,sun"``, ``"mon,wed,fri"`` -> the
    canonical ``"mon,tue,…"`` the store keeps."""
    words = _DAY_WORDS.get(text.strip().lower(), text.strip().lower())
    days = set()
    for part in filter(None, (p.strip() for p in words.split(","))):
        ends = [e.strip()[:3] for e in part.split("-")]
        if any(e not in _DAY_NAMES for e in ends) or len(ends) > 2:
            raise TimingError(
                f"{text!r} is not a set of days; write it like mon-fri, weekends "
                "or mon,wed,fri."
            )
        first, last = _DAY_NAMES.index(ends[0]), _DAY_NAMES.index(ends[-1])
        span = (
            range(first, last + 1)
            if first <= last
            else [*range(first, 7), *range(0, last + 1)]
        )
        days.update(span)
    if not days:
        raise TimingError(f"{text!r} names no days.")
    return ",".join(_DAY_NAMES[d] for d in sorted(days))


def day_allowed(days: str, local: datetime) -> bool:
    """True when *local* falls on one of *days* (empty means every day)."""
    return not days or _DAY_NAMES[local.weekday()] in days.split(",")


def in_quiet_hours(quiet: str, local: datetime) -> bool:
    """True when *local* falls inside *quiet* (which may cross midnight)."""
    if not quiet:
        return False
    start, end = parse_quiet(quiet)
    if end == 23 * 60 + 59:
        end = 24 * 60  # "-23:59" means to the end of the day, not a loud last minute.
    minute = local.hour * 60 + local.minute
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end


def next_slot(slot: float, every: int) -> float:
    """*slot* plus *every* seconds; whole days are counted on the local clock."""
    if every % 86400:
        return slot + every
    wall = datetime.fromtimestamp(slot) + timedelta(days=every // 86400)
    return wall.timestamp()


def format_interval(seconds: int) -> str:
    for word, size in (("week", 7 * 86400), ("day", 86400), ("hour", 3600)):
        if seconds % size == 0:
            n = seconds // size
            return f"{n} {word}{'s' if n != 1 else ''}"
    n = seconds // 60
    return f"{n} minute{'s' if n != 1 else ''}"
