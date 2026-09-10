# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A conflict past the provider's first page must not read as "free" (#3610).

``detect_calendar_conflicts`` answers "is this slot free?" from one page of
the calendar. When the provider hands back a continuation token, the window
was only partially scanned and ``has_conflict: False`` is unverified, not
negative — the result must say so.
"""

import pytest

pytest.importorskip("gaia_agent_email")

import httpx  # noqa: E402
from gaia_agent_email.outlook_calendar_backend import (  # noqa: E402
    LiveOutlookCalendarBackend,
)
from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    detect_calendar_conflicts_impl,
)

WINDOW = {"start_iso": "2026-07-01T10:00:00Z", "end_iso": "2026-07-01T11:00:00Z"}


def _event(event_id: str, start: str, end: str):
    return {
        "id": event_id,
        "summary": event_id,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }


class PagedCalendar:
    """Returns one page plus the provider's continuation token."""

    def __init__(self, items, next_page_token=None):
        self._items = items
        self._token = next_page_token

    def list_events(self, **_kwargs):
        data = {"items": list(self._items)}
        if self._token is not None:
            data["nextPageToken"] = self._token
        return data


class TestTruncatedConflictScan:
    def test_conflict_beyond_first_page_is_not_reported_as_free(self):
        # The page the provider returned holds no overlap; the conflicting
        # meeting sits on page two, which this tool never sees.
        cal = PagedCalendar(
            [_event("earlier", "2026-07-01T08:00:00Z", "2026-07-01T09:00:00Z")],
            next_page_token="page-2",
        )

        result = detect_calendar_conflicts_impl(cal, **WINDOW)

        assert result["has_conflict"] is False
        assert result["truncated"] is True

    def test_fully_scanned_window_reports_a_verified_answer(self):
        cal = PagedCalendar(
            [_event("earlier", "2026-07-01T08:00:00Z", "2026-07-01T09:00:00Z")]
        )

        result = detect_calendar_conflicts_impl(cal, **WINDOW)

        assert result["has_conflict"] is False
        assert result["truncated"] is False

    def test_truncation_comes_from_the_token_not_the_page_size(self):
        # A full last page with no token is complete — inferring truncation
        # from ``len(items)`` would flag it as unverified.
        cal = PagedCalendar(
            [
                _event(f"e{i}", "2026-07-01T08:00:00Z", "2026-07-01T09:00:00Z")
                for i in range(25)
            ]
        )

        assert detect_calendar_conflicts_impl(cal, **WINDOW)["truncated"] is False

    def test_outlook_next_link_reaches_the_conflict_check(self):
        # Graph signals "more pages" with @odata.nextLink; without the
        # mapping the Outlook envelope has no token and every partial scan
        # reads as complete.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "value": [],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/"
                    "calendarView?$skip=25",
                },
            )

        cal = LiveOutlookCalendarBackend(
            lambda: "fake-token",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        assert detect_calendar_conflicts_impl(cal, **WINDOW)["truncated"] is True

    def test_found_conflicts_still_report_the_partial_scan(self):
        cal = PagedCalendar(
            [_event("standup", "2026-07-01T10:30:00Z", "2026-07-01T11:30:00Z")],
            next_page_token="page-2",
        )

        result = detect_calendar_conflicts_impl(cal, **WINDOW)

        assert result["has_conflict"] is True
        assert [c["id"] for c in result["conflicts"]] == ["standup"]
        assert result["truncated"] is True
