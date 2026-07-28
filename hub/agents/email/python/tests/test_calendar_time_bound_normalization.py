# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Time-bound normalization for calendar list/conflict queries (#2517).

A date-only bound (``timeMin=2026-07-27``) reaches the live Google Calendar
API verbatim and 400s — proven on hardware (see the issue for the raw httpx
request/response). ``list_calendar_events_impl`` and
``detect_calendar_conflicts_impl`` must normalize ``time_min``/``time_max``
(and ``start_iso``/``end_iso``) to RFC 3339 before they reach the backend,
never forward an unparseable bound, and never swallow a backend error into
an empty success the agent can narrate as "let me try again".
"""

from datetime import datetime, timezone

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    detect_calendar_conflicts_impl,
    list_calendar_events_impl,
)

from gaia.connectors.errors import ConnectorsError  # noqa: E402


class RecordingCalendar:
    """Records the kwargs of every ``list_events`` call."""

    def __init__(self):
        self.calls = []

    def list_events(self, **kwargs):
        self.calls.append(kwargs)
        return {"items": []}


class RaisingCalendar:
    """Simulates a scope/permission failure from the live backend."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def list_events(self, **kwargs):
        raise self._exc


class TestListEventsDateOnlyBounds:
    def test_date_only_bounds_normalized_to_rfc3339_utc_midnight(self):
        cal = RecordingCalendar()

        list_calendar_events_impl(cal, time_min="2026-07-27", time_max="2026-08-26")

        assert cal.calls == [
            {
                "time_min": "2026-07-27T00:00:00+00:00",
                "time_max": "2026-08-26T00:00:00+00:00",
            }
        ]

    def test_naive_datetime_coerced_to_utc(self):
        cal = RecordingCalendar()

        list_calendar_events_impl(cal, time_min="2026-07-27T10:00:00", time_max=None)

        assert cal.calls == [
            {"time_min": "2026-07-27T10:00:00+00:00", "time_max": None}
        ]

    def test_valid_rfc3339_z_passes_through_byte_identical(self):
        cal = RecordingCalendar()
        time_min = "2026-07-27T10:00:00Z"
        time_max = "2026-08-26T10:00:00Z"

        list_calendar_events_impl(cal, time_min=time_min, time_max=time_max)

        assert cal.calls == [{"time_min": time_min, "time_max": time_max}]

    def test_valid_rfc3339_offset_passes_through_byte_identical(self):
        cal = RecordingCalendar()
        time_min = "2026-07-27T10:00:00+05:00"

        list_calendar_events_impl(cal, time_min=time_min, time_max=None)

        assert cal.calls == [{"time_min": time_min, "time_max": None}]

    def test_unparseable_bound_raises_actionable_error(self):
        cal = RecordingCalendar()

        with pytest.raises(ValueError) as exc_info:
            list_calendar_events_impl(cal, time_min="not-a-date", time_max=None)

        message = str(exc_info.value)
        assert "not-a-date" in message
        assert not cal.calls  # never reached the backend

    def test_backend_scope_error_propagates_not_swallowed(self):
        cal = RaisingCalendar(
            ConnectorsError(
                "Calendar API returned 401. The access token may have "
                "expired or scopes were revoked. Reconnect Google in "
                "Settings → Connectors."
            )
        )

        with pytest.raises(ConnectorsError):
            list_calendar_events_impl(
                cal, time_min="2026-07-27T00:00:00Z", time_max=None
            )


class TestDetectCalendarConflictsBounds:
    def test_date_only_bounds_normalized_before_backend_call(self):
        cal = RecordingCalendar()

        detect_calendar_conflicts_impl(
            cal, start_iso="2026-07-27", end_iso="2026-07-28"
        )

        assert cal.calls == [
            {
                "calendar_id": "primary",
                "time_min": "2026-07-27T00:00:00+00:00",
                "time_max": "2026-07-28T00:00:00+00:00",
            }
        ]

    def test_valid_rfc3339_passes_through_byte_identical(self):
        cal = RecordingCalendar()
        start_iso = "2026-07-27T14:00:00Z"
        end_iso = "2026-07-27T15:00:00Z"

        detect_calendar_conflicts_impl(cal, start_iso=start_iso, end_iso=end_iso)

        assert cal.calls == [
            {
                "calendar_id": "primary",
                "time_min": start_iso,
                "time_max": end_iso,
            }
        ]

    def test_backend_scope_error_propagates_not_swallowed(self):
        cal = RaisingCalendar(ConnectorsError("Calendar API returned 403."))

        with pytest.raises(ConnectorsError):
            detect_calendar_conflicts_impl(
                cal,
                start_iso="2026-07-27T14:00:00Z",
                end_iso="2026-07-27T15:00:00Z",
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
