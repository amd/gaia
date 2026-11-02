# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The provider-neutral calendar shape, and the time rules every path shares.

Each backend parses its provider's JSON into :class:`CalendarEvent` at the
boundary, so the tools never learn which calendar answered.

Time rule: a timed value always carries a UTC offset, and an all-day value is a
bare ``YYYY-MM-DD``. A datetime with no offset names no moment, so it is refused
rather than guessed. :attr:`CalendarEvent.end` and :attr:`EventSlot.end` are
exclusive for all-day events, which is how both providers store them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Protocol, Tuple

from gaia.agents.tools._calendar.errors import CalendarError

_BARE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Python 3.10 parses only 3 or 6 fractional digits; Graph sends 7.
_FRACTION_RE = re.compile(r"\.(\d+)")


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: str
    end: str
    all_day: bool
    time_zone: Optional[str]
    location: Optional[str]
    # Everyone but the person themself, as far as the provider lets us tell.
    attendees: Tuple[str, ...]
    organizer_is_self: bool
    html_link: Optional[str]
    recurring_instance: bool
    # The rule behind a recurring event rather than one occurrence of it.
    series_master: bool
    # False for an event marked free, one the person declined, or a cancelled one.
    blocks_time: bool
    cancelled: bool


@dataclass(frozen=True)
class CalendarWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise CalendarError("A calendar window needs UTC offsets on both ends.")
        if self.end <= self.start:
            raise CalendarError(
                f"The window end ({self.end.isoformat()}) must be after its start "
                f"({self.start.isoformat()}). Retry with a later end."
            )

    @property
    def start_utc(self) -> str:
        return _utc_z(self.start)

    @property
    def end_utc(self) -> str:
        return _utc_z(self.end)


@dataclass(frozen=True)
class CalendarPage:
    events: Tuple[CalendarEvent, ...]
    # The provider has more events in this window than were returned.
    truncated: bool


@dataclass(frozen=True)
class EventSlot:
    start: str
    end: str
    all_day: bool


@dataclass(frozen=True)
class NewEvent:
    title: str
    slot: EventSlot
    attendees: Tuple[str, ...] = ()
    location: Optional[str] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class EventPatch:
    """Only the fields being changed; ``None`` means leave it as it is."""

    title: Optional[str] = None
    slot: Optional[EventSlot] = None
    location: Optional[str] = None
    description: Optional[str] = None

    def is_empty(self) -> bool:
        return all(
            value is None
            for value in (self.title, self.slot, self.location, self.description)
        )


class CancelOutcome(str, Enum):
    CANCELLED_FOR_EVERYONE = "cancelled_for_everyone"
    REMOVED_FROM_YOUR_CALENDAR = "removed_from_your_calendar"


class CalendarBackend(Protocol):
    def list_events(self, window: CalendarWindow, *, limit: int) -> CalendarPage: ...

    def get_event(self, event_id: str) -> CalendarEvent: ...

    def create_event(self, event: NewEvent) -> CalendarEvent: ...

    def update_event(self, event: CalendarEvent, patch: EventPatch) -> CalendarEvent:
        """Patch *event* as read just now, so notifications follow its attendees."""
        ...

    def cancel_event(self, event: CalendarEvent) -> CancelOutcome:
        """Cancel or remove *event*; raises ``CalendarEventGone`` if already gone."""
        ...


def _utc_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_bare_date(value: str) -> bool:
    return bool(_BARE_DATE_RE.fullmatch(value.strip()))


def _no_offset_error(name: str, value: str) -> CalendarError:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return CalendarError(
        f"{name}={value!r} has no UTC offset, so GAIA can't tell which moment it "
        "means. Retry with an offset, for example '2026-11-03T09:00:00-05:00'. "
        f"This computer's time is {now}."
    )


def parse_instant(value: str, name: str = "time") -> datetime:
    """An aware datetime from an RFC 3339 value, or an actionable refusal."""
    text = (value or "").strip()
    if is_bare_date(text):
        raise _no_offset_error(name, value)
    normalized = text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text
    normalized = _FRACTION_RE.sub(
        lambda m: "." + m.group(1)[:6].ljust(6, "0"), normalized, count=1
    )
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CalendarError(
            f"{name}={value!r} is not a valid timestamp. Retry with one like "
            "'2026-11-03T09:00:00-05:00'."
        ) from exc
    if parsed.tzinfo is None:
        raise _no_offset_error(name, value)
    return parsed


def parse_date(value: str, name: str = "date") -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise CalendarError(
            f"{name}={value!r} is not a valid date. Retry with YYYY-MM-DD."
        ) from exc


def parse_slot(start: str, end: str) -> EventSlot:
    """A slot from tool arguments. For an all-day event ``end`` is the last day."""
    start = (start or "").strip()
    end = (end or "").strip()
    if is_bare_date(start) != is_bare_date(end):
        raise CalendarError(
            f"start={start!r} and end={end!r} must match: two YYYY-MM-DD dates for "
            "an all-day event, or two timestamps with UTC offsets for a timed one."
        )
    if is_bare_date(start):
        first = parse_date(start, "start")
        last = parse_date(end, "end")
        if last < first:
            raise CalendarError(
                f"end={end!r} is before start={start!r}. For an all-day event, end "
                "is the last day it covers."
            )
        return EventSlot(
            first.isoformat(), (last + timedelta(days=1)).isoformat(), True
        )
    begins = parse_instant(start, "start")
    ends = parse_instant(end, "end")
    if ends <= begins:
        raise CalendarError(f"end={end!r} must be after start={start!r}.")
    return EventSlot(begins.isoformat(), ends.isoformat(), False)


def event_span(
    event: CalendarEvent, window: CalendarWindow
) -> Tuple[datetime, datetime]:
    """When *event* occupies. An all-day date is read in the window's own offset."""
    if not event.all_day:
        return parse_instant(event.start, "start"), parse_instant(event.end, "end")
    zone = window.start.tzinfo
    return (
        datetime.combine(parse_date(event.start), datetime.min.time(), zone),
        datetime.combine(parse_date(event.end), datetime.min.time(), zone),
    )


__all__ = [
    "CalendarBackend",
    "CalendarEvent",
    "CalendarPage",
    "CalendarWindow",
    "CancelOutcome",
    "EventPatch",
    "EventSlot",
    "NewEvent",
    "event_span",
    "is_bare_date",
    "parse_date",
    "parse_instant",
    "parse_slot",
]
