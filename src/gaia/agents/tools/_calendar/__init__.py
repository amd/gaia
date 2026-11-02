# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Calendar backends behind the flagship's calendar tools."""

from gaia.agents.tools._calendar.errors import (
    CalendarAuthError,
    CalendarError,
    CalendarEventGone,
)
from gaia.agents.tools._calendar.google import GoogleCalendarBackend
from gaia.agents.tools._calendar.graph import GraphCalendarBackend
from gaia.agents.tools._calendar.model import (
    CalendarBackend,
    CalendarEvent,
    CalendarPage,
    CalendarWindow,
    CancelOutcome,
    EventPatch,
    EventSlot,
    NewEvent,
)

__all__ = [
    "CalendarAuthError",
    "CalendarBackend",
    "CalendarError",
    "CalendarEvent",
    "CalendarEventGone",
    "CalendarPage",
    "CalendarWindow",
    "CancelOutcome",
    "EventPatch",
    "EventSlot",
    "GoogleCalendarBackend",
    "GraphCalendarBackend",
    "NewEvent",
]
