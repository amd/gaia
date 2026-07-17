# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Default forward window for calendar listing (#2162).

An unbounded ``list_calendar_events`` made the Calendar backend expand
recurring series from their first-ever instance, so "what's on my calendar?"
surfaced years-old events. When BOTH range bounds are absent the impl must
constrain the outgoing request to ``now → +DEFAULT_LIST_WINDOW_DAYS``;
explicit bounds must pass through to the backend unchanged.
"""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    DEFAULT_LIST_WINDOW_DAYS,
    list_calendar_events_impl,
)

FIXED_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)


class RecordingCalendar:
    """Records the kwargs of every ``list_events`` call."""

    def __init__(self):
        self.calls = []

    def list_events(self, **kwargs):
        self.calls.append(kwargs)
        return {"items": []}


class TestDefaultForwardWindow:
    def test_no_args_constrains_request_to_now_plus_30_days(self):
        cal = RecordingCalendar()

        list_calendar_events_impl(cal, time_min=None, time_max=None, now=FIXED_NOW)

        assert cal.calls == [
            {
                "time_min": FIXED_NOW.isoformat(),
                "time_max": (
                    FIXED_NOW + timedelta(days=DEFAULT_LIST_WINDOW_DAYS)
                ).isoformat(),
            }
        ]

    def test_default_window_is_30_days(self):
        assert DEFAULT_LIST_WINDOW_DAYS == 30

    def test_default_now_falls_back_to_current_utc_time(self):
        cal = RecordingCalendar()
        before = datetime.now(timezone.utc)

        list_calendar_events_impl(cal, time_min=None, time_max=None)

        after = datetime.now(timezone.utc)
        (call,) = cal.calls
        sent_min = datetime.fromisoformat(call["time_min"])
        sent_max = datetime.fromisoformat(call["time_max"])
        assert before <= sent_min <= after
        assert sent_max - sent_min == timedelta(days=DEFAULT_LIST_WINDOW_DAYS)

    def test_explicit_bounds_pass_through_unchanged(self):
        cal = RecordingCalendar()
        time_min = "2024-01-01T00:00:00Z"
        time_max = "2024-02-01T00:00:00Z"

        list_calendar_events_impl(cal, time_min=time_min, time_max=time_max)

        assert cal.calls == [{"time_min": time_min, "time_max": time_max}]

    def test_partial_bounds_pass_through_unchanged(self):
        # One explicit bound means the model chose a range — honor it verbatim.
        cal = RecordingCalendar()

        list_calendar_events_impl(cal, time_min="2026-08-01T00:00:00Z", time_max=None)
        list_calendar_events_impl(cal, time_min=None, time_max="2026-08-01T00:00:00Z")

        assert cal.calls == [
            {"time_min": "2026-08-01T00:00:00Z", "time_max": None},
            {"time_min": None, "time_max": "2026-08-01T00:00:00Z"},
        ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
