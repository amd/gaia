# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Recurring prompts the paired user sets from their Discord DM.

The user asks in plain words ("summarize my PRs every 3 hours"); the
discord-automation skill turns that into ``gaia discord schedule-add``, and the
bridge runs the prompt through its own agent on that interval and posts the
answer to the DM. The bridge already
drives the flagship agent with its tools and its approval gate, which
``gaia schedule`` (a bare chat, no tools) cannot offer.

Stored in ``schedules.json`` beside the bridge's other files. A schedule that
came due while the bridge was off runs once when it is next up, not once per
missed interval.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from typing import List, Optional

from gaia.messaging.discord import state

#: Shorter than this, a schedule is a cost and noise problem, not a summary.
MIN_INTERVAL_SECONDS = 5 * 60
MAX_SCHEDULES = 10

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
_UNIT_WORDS = r"(m|mins?|minutes?|h|hrs?|hours?|d|days?)"


class ScheduleError(ValueError):
    """A schedule request the user can fix; the message says how."""


@dataclass
class Schedule:
    id: str
    every_seconds: int
    prompt: str
    next_at: float


def format_interval(seconds: int) -> str:
    for word, size in (("day", 86400), ("hour", 3600)):
        if seconds % size == 0:
            n = seconds // size
            return f"{n} {word}{'s' if n != 1 else ''}"
    n = seconds // 60
    return f"{n} minute{'s' if n != 1 else ''}"


_INTERVAL_RE = re.compile(rf"^(\d+)\s*{_UNIT_WORDS}$", re.IGNORECASE)


def parse_interval(text: str) -> int:
    """``"3h"`` / ``"3 hours"`` / ``"30m"`` → seconds, at least five minutes."""
    match = _INTERVAL_RE.match(text.strip())
    if not match:
        raise ScheduleError(
            f"{text!r} is not an interval. Use a number and a unit, like 3h, "
            "30m or 1d."
        )
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)[0].lower()]
    if seconds < MIN_INTERVAL_SECONDS:
        raise ScheduleError("The shortest interval is 5 minutes.")
    return seconds


def path():
    return state.discord_dir() / "schedules.json"


def load() -> List[Schedule]:
    data = state.read_json(path())
    if data is None:
        return []
    try:
        return [Schedule(**item) for item in data.get("schedules", [])]
    except TypeError as e:
        raise state.BridgeStateError(
            f"{path()} is not usable: {e}. Delete it to clear your schedules."
        ) from e


def save(items: List[Schedule]) -> None:
    state.write_json(path(), {"schedules": [asdict(s) for s in items]})


def _locked():
    from gaia.utils.file_lock import file_lock

    return file_lock(path())


def add(every_seconds: int, prompt: str, now: float) -> Schedule:
    with _locked():
        return _add(every_seconds, prompt, now)


def _add(every_seconds: int, prompt: str, now: float) -> Schedule:
    items = load()
    if len(items) >= MAX_SCHEDULES:
        raise ScheduleError(
            f"You already have {MAX_SCHEDULES} schedules. Send `list schedules` "
            "and stop one first."
        )
    item = Schedule(uuid.uuid4().hex[:12], every_seconds, prompt, now + every_seconds)
    items.append(item)
    save(items)
    return item


def remove(schedule_id: str) -> Optional[Schedule]:
    with _locked():
        return _remove(schedule_id)


def _remove(schedule_id: str) -> Optional[Schedule]:
    items = load()
    kept = [s for s in items if s.id != schedule_id]
    if len(kept) == len(items):
        return None
    save(kept)
    return next(s for s in items if s.id == schedule_id)


def clear() -> int:
    with _locked():
        count = len(load())
        save([])
    return count


def get(schedule_id: str) -> Optional[Schedule]:
    return next((s for s in load() if s.id == schedule_id), None)


def take_due(now: float) -> List[Schedule]:
    """Return the schedules due now and move each to its next slot."""
    with _locked():
        return _take_due(now)


def _take_due(now: float) -> List[Schedule]:
    items = load()
    due = [s for s in items if s.next_at <= now]
    for item in due:
        # Skip missed slots: one run on return, not one per interval missed.
        while item.next_at <= now:
            item.next_at += item.every_seconds
    if due:
        save(items)
    return due
