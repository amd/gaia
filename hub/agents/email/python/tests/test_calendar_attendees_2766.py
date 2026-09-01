# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Real ``attendees`` surfaced on calendar events (#2766).

``list_calendar_events_impl`` and ``detect_calendar_conflicts_impl`` used to
discard the Calendar API's own ``attendees`` field entirely — a caller (the
model) had no structured way to know a listed event had no attendees, only
an organizer. Asked "what meetings are coming up" or "did anyone send me an
invite", the model then had nothing to ground an attendee/invite claim in
except its own free composition, and #2766's live probes show it invented
names and invite claims that appear nowhere in the mailbox.

Google's Calendar API omits the ``attendees`` key entirely once an event has
no one beyond the organizer (verified against the live API, #2766) — so
``event.get("attendees")`` is ``None``, not ``[]``, for the common case.
``_extract_attendees`` normalizes that to ``[]`` and passes through only
``email``/``response_status`` for anyone who *is* listed — never invents,
never re-derives from ``organizer``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    _extract_attendees,
    detect_calendar_conflicts_impl,
    list_calendar_events_impl,
)

# ---------------------------------------------------------------------------
# _extract_attendees — the pure extraction helper
# ---------------------------------------------------------------------------


class TestExtractAttendees:
    def test_missing_attendees_key_returns_empty_list(self):
        assert _extract_attendees({"summary": "Board meeting"}) == []

    def test_none_attendees_value_returns_empty_list(self):
        assert _extract_attendees({"attendees": None}) == []

    def test_empty_attendees_list_returns_empty_list(self):
        assert _extract_attendees({"attendees": []}) == []

    def test_real_attendees_are_passed_through(self):
        event = {
            "attendees": [
                {"email": "jane@example.com", "responseStatus": "accepted"},
                {"email": "john@example.com", "responseStatus": "needsAction"},
            ]
        }
        assert _extract_attendees(event) == [
            {"email": "jane@example.com", "response_status": "accepted"},
            {"email": "john@example.com", "response_status": "needsAction"},
        ]

    def test_entries_missing_email_are_dropped_not_synthesized(self):
        event = {"attendees": [{"displayName": "no email here"}]}
        assert _extract_attendees(event) == []

    def test_non_mapping_entries_are_skipped(self):
        event = {"attendees": ["not-a-dict", {"email": "jane@example.com"}]}
        assert _extract_attendees(event) == [
            {"email": "jane@example.com", "response_status": None}
        ]


# ---------------------------------------------------------------------------
# list_calendar_events_impl — attendees surfaced per event
# ---------------------------------------------------------------------------


class _FakeCalendar:
    def __init__(self, items):
        self._items = items

    def list_events(self, **_kwargs):
        return {"items": self._items}


class TestListCalendarEventsAttendees:
    def test_event_with_no_attendees_key_reports_empty_list(self):
        cal = _FakeCalendar(
            [
                {
                    "id": "evt1",
                    "summary": "Board meeting",
                    "start": {"dateTime": "2026-08-13T14:00:00-04:00"},
                    "end": {"dateTime": "2026-08-13T16:00:00-04:00"},
                    "organizer": {"email": "me@example.com", "self": True},
                }
            ]
        )
        out = list_calendar_events_impl(cal, time_min=None, time_max=None)
        (event,) = out["events"]
        assert event["attendees"] == []
        assert event["organizer_self"] is True

    def test_event_with_real_attendees_reports_them(self):
        cal = _FakeCalendar(
            [
                {
                    "id": "evt1",
                    "summary": "Design review",
                    "start": {"dateTime": "2026-08-06T09:00:00-04:00"},
                    "end": {"dateTime": "2026-08-06T10:00:00-04:00"},
                    "attendees": [
                        {"email": "jane@example.com", "responseStatus": "accepted"}
                    ],
                }
            ]
        )
        out = list_calendar_events_impl(cal, time_min=None, time_max=None)
        (event,) = out["events"]
        assert event["attendees"] == [
            {"email": "jane@example.com", "response_status": "accepted"}
        ]

    def test_multiple_events_each_report_their_own_attendees(self):
        cal = _FakeCalendar(
            [
                {
                    "id": "evt1",
                    "summary": "Northgate migration sync",
                    "start": {"dateTime": "2026-08-05T10:30:00-04:00"},
                    "end": {"dateTime": "2026-08-05T11:30:00-04:00"},
                },
                {
                    "id": "evt2",
                    "summary": "Design review",
                    "start": {"dateTime": "2026-08-06T09:00:00-04:00"},
                    "end": {"dateTime": "2026-08-06T10:00:00-04:00"},
                    "attendees": [{"email": "jane@example.com"}],
                },
            ]
        )
        out = list_calendar_events_impl(cal, time_min=None, time_max=None)
        assert out["events"][0]["attendees"] == []
        assert out["events"][1]["attendees"] == [
            {"email": "jane@example.com", "response_status": None}
        ]


# ---------------------------------------------------------------------------
# detect_calendar_conflicts_impl — attendees surfaced per conflicting event
# ---------------------------------------------------------------------------


class TestDetectCalendarConflictsAttendees:
    def test_conflicting_event_with_no_attendees_reports_empty_list(self):
        cal = _FakeCalendar(
            [
                {
                    "id": "evt1",
                    "summary": "Budget sync",
                    "start": {"dateTime": "2026-08-06T09:00:00Z"},
                    "end": {"dateTime": "2026-08-06T10:00:00Z"},
                }
            ]
        )
        out = detect_calendar_conflicts_impl(
            cal,
            start_iso="2026-08-06T09:30:00Z",
            end_iso="2026-08-06T10:30:00Z",
        )
        assert out["has_conflict"] is True
        (conflict,) = out["conflicts"]
        assert conflict["attendees"] == []

    def test_conflicting_event_surfaces_external_organizer(self):
        cal = _FakeCalendar(
            [
                {
                    "id": "evt1",
                    "summary": "Vendor meeting",
                    "start": {"dateTime": "2026-08-06T09:00:00Z"},
                    "end": {"dateTime": "2026-08-06T10:00:00Z"},
                    "organizer": {"email": "vendor@example.com", "self": False},
                }
            ]
        )
        out = detect_calendar_conflicts_impl(
            cal,
            start_iso="2026-08-06T09:30:00Z",
            end_iso="2026-08-06T10:30:00Z",
        )

        assert out["conflicts"][0]["organizer_self"] is False

    def test_conflicting_event_with_real_attendees_reports_them(self):
        cal = _FakeCalendar(
            [
                {
                    "id": "evt1",
                    "summary": "Budget sync",
                    "start": {"dateTime": "2026-08-06T09:00:00Z"},
                    "end": {"dateTime": "2026-08-06T10:00:00Z"},
                    "attendees": [{"email": "jane@example.com"}],
                }
            ]
        )
        out = detect_calendar_conflicts_impl(
            cal,
            start_iso="2026-08-06T09:30:00Z",
            end_iso="2026-08-06T10:30:00Z",
        )
        (conflict,) = out["conflicts"]
        assert conflict["attendees"] == [
            {"email": "jane@example.com", "response_status": None}
        ]


# ---------------------------------------------------------------------------
# Tool-docstring guidance (the schema actually sent to the model) — pins
# against the real _TOOL_REGISTRY descriptions, mirroring
# test_calendar_conflict_grounding_2571.py's pattern.
# ---------------------------------------------------------------------------


class _RecordingMailBackend:
    """GmailBackend-protocol fake that answers every call with ``{}``."""

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            return {}

        return _record


class _MinimalCalendarBackend:
    """Satisfies the CalendarBackend protocol just enough to construct."""


@pytest.fixture
def agent(tmp_path, monkeypatch):
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=_RecordingMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        start_scheduler=False,
    )
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        a = EmailTriageAgent(config=cfg)
    try:
        yield a
    finally:
        a.close_db()


def test_list_calendar_events_docstring_warns_against_inventing_attendees(agent):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    desc = _TOOL_REGISTRY["list_calendar_events"]["description"]
    assert "attendees" in desc
    assert "sent an invite" in desc


def test_detect_calendar_conflicts_docstring_warns_against_inventing_attendees(agent):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    desc = _TOOL_REGISTRY["detect_calendar_conflicts"]["description"]
    assert "attendees" in desc


def test_detect_meeting_request_docstring_disclaims_confirmed_invites(agent):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    desc = _TOOL_REGISTRY["detect_meeting_request"]["description"]
    assert "never a confirmed invite" in desc.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
