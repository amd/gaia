# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Create-event-from-email tests for the Email Triage Agent (issue #1274).

Event creation derives details from an email — title (subject), time, and
attendees (sender) — and writes them to the calendar. The two halves the
acceptance criteria call out:

- Extraction: a fixture email with a clear date/time/attendees yields the
  expected title / attendees and is recognised as carrying a concrete time.
- No-datetime negative case: an email with no parseable date/time must NOT
  silently create a bogus event. Creation surfaces that no time was found
  (fail-loud) rather than POSTing an event with an empty start/end.

Extraction is deterministic (reuses this module's ``_TIME_RE`` and
``read_tools.extract_sender_email`` — no parallel parser, no LLM), so the
real parser is exercised here. The calendar backend is the in-memory
``FakeCalendarBackend`` fixture — no live calendar, no Lemonade, no
``gaia eval``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make tests.fixtures importable.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.agents.email.tools.calendar_tools import (  # noqa: E402
    NoEventDateTimeError,
    create_event_from_email_impl,
    extract_event_details,
)
from tests.fixtures.email.fake_gmail import (  # noqa: E402
    FakeCalendarBackend,
    FakeGmailBackend,
)


@pytest.fixture
def fake_calendar():
    return FakeCalendarBackend()


@pytest.fixture
def fake_gmail():
    return FakeGmailBackend(
        _REPO_ROOT / "tests" / "fixtures" / "email" / "_stub_inbox.mbox"
    )


# A fixture email that clearly proposes a time and names a sender. The body
# is the kind of text the agent reads off the inbox (already HTML-stripped).
CLEAR_TIME_EMAIL = {
    "subject": "Q2 roadmap sync",
    "from": "Alice Researcher <alice@example.com>",
    "body": "Hi, can we meet Thursday at 2pm to go over the Q2 roadmap? Thanks!",
}

# A fixture email with no parseable date/time anywhere — scheduling-adjacent
# wording but nothing the agent can turn into a calendar slot.
NO_TIME_EMAIL = {
    "subject": "Let's catch up",
    "from": "Bob Manager <bob@example.com>",
    "body": "Hey, we should catch up at some point — it's been a while.",
}


# ---------------------------------------------------------------------------
# extract_event_details — the deterministic extractor
# ---------------------------------------------------------------------------


class TestExtractEventDetails:
    def test_title_attendees_and_time_extracted_from_clear_email(self):
        details = extract_event_details(
            subject=CLEAR_TIME_EMAIL["subject"],
            body=CLEAR_TIME_EMAIL["body"],
            sender=CLEAR_TIME_EMAIL["from"],
        )
        # Title comes from the subject line.
        assert details["title"] == "Q2 roadmap sync"
        # Attendees are parsed from the From header down to the bare address.
        assert details["attendees"] == ["alice@example.com"]
        # A concrete time signal is present in the body.
        assert details["has_datetime"] is True
        # The matched signal is surfaced for verbose logging / auditing.
        assert details["time_signal"]

    def test_no_datetime_email_reports_no_time(self):
        details = extract_event_details(
            subject=NO_TIME_EMAIL["subject"],
            body=NO_TIME_EMAIL["body"],
            sender=NO_TIME_EMAIL["from"],
        )
        # No concrete time anywhere — the extractor must say so, not guess.
        assert details["has_datetime"] is False
        assert not details["time_signal"]
        # Title/attendees are still extracted (only the time is missing).
        assert details["title"] == "Let's catch up"
        assert details["attendees"] == ["bob@example.com"]

    def test_time_signal_in_subject_counts(self):
        details = extract_event_details(
            subject="Budget review at 3pm",
            body="See you there.",
            sender="carol@example.com",
        )
        assert details["has_datetime"] is True

    def test_blank_subject_falls_back_to_generic_title(self):
        details = extract_event_details(
            subject="", body="Are you free tomorrow at 10:00?", sender="d@example.com"
        )
        # A blank subject must not produce a blank event title.
        assert details["title"]

    def test_bare_address_sender_is_handled(self):
        details = extract_event_details(
            subject="x", body="meet at noon", sender="dave@example.com"
        )
        assert details["attendees"] == ["dave@example.com"]

    def test_empty_sender_yields_no_attendees(self):
        details = extract_event_details(subject="x", body="meet at noon", sender="")
        assert details["attendees"] == []

    def test_none_inputs_do_not_crash(self):
        details = extract_event_details(
            subject=None, body=None, sender=None  # type: ignore[arg-type]
        )
        assert details["title"]  # generic fallback, never blank
        assert details["attendees"] == []
        assert details["has_datetime"] is False


# ---------------------------------------------------------------------------
# create_event_from_email_impl — happy path + fail-loud on no datetime
# ---------------------------------------------------------------------------


class TestCreateEventFromEmailImpl:
    def test_creates_event_with_extracted_details(self, fake_calendar):
        out = create_event_from_email_impl(
            fake_calendar,
            summary="Q2 roadmap sync",
            start={"dateTime": "2026-05-07T14:00:00Z"},
            end={"dateTime": "2026-05-07T15:00:00Z"},
            attendees=["alice@example.com"],
        )
        assert out["summary"] == "Q2 roadmap sync"
        assert out["event_id"]
        # The backend recorded a create_event call carrying the extracted
        # title / time / attendees verbatim.
        create_calls = [c for c in fake_calendar.calls if c[0] == "create_event"]
        assert len(create_calls) == 1
        _, kwargs = create_calls[0]
        assert kwargs["summary"] == "Q2 roadmap sync"
        assert kwargs["start"] == {"dateTime": "2026-05-07T14:00:00Z"}
        assert kwargs["end"] == {"dateTime": "2026-05-07T15:00:00Z"}
        assert kwargs["attendees"] == ["alice@example.com"]

    def test_blank_start_raises_and_creates_nothing(self, fake_calendar):
        # The no-datetime negative case at the impl layer: a blank start must
        # raise rather than POST an event with an empty time.
        with pytest.raises(NoEventDateTimeError):
            create_event_from_email_impl(
                fake_calendar,
                summary="Let's catch up",
                start={"dateTime": ""},
                end={"dateTime": ""},
            )
        # Fail-loud means NO bogus event was created.
        assert [c for c in fake_calendar.calls if c[0] == "create_event"] == []

    def test_missing_datetime_key_raises_and_creates_nothing(self, fake_calendar):
        with pytest.raises(NoEventDateTimeError):
            create_event_from_email_impl(
                fake_calendar,
                summary="Let's catch up",
                start={},
                end={},
            )
        assert [c for c in fake_calendar.calls if c[0] == "create_event"] == []

    def test_whitespace_only_start_raises_and_creates_nothing(self, fake_calendar):
        # A whitespace-only time is not a real time — must be rejected, not
        # POSTed as a blank-ish slot.
        with pytest.raises(NoEventDateTimeError):
            create_event_from_email_impl(
                fake_calendar,
                summary="Let's catch up",
                start={"dateTime": "   "},
                end={"dateTime": "   "},
            )
        assert [c for c in fake_calendar.calls if c[0] == "create_event"] == []

    def test_partial_blank_end_raises_and_creates_nothing(self, fake_calendar):
        # A valid start but a blank end is still no usable window.
        with pytest.raises(NoEventDateTimeError):
            create_event_from_email_impl(
                fake_calendar,
                summary="Half a time",
                start={"dateTime": "2026-05-07T14:00:00Z"},
                end={"dateTime": ""},
            )
        assert [c for c in fake_calendar.calls if c[0] == "create_event"] == []

    def test_inverted_window_raises_and_creates_nothing(self, fake_calendar):
        # end <= start is a nonsensical event — surfaced loudly.
        with pytest.raises(ValueError):
            create_event_from_email_impl(
                fake_calendar,
                summary="Backwards",
                start={"dateTime": "2026-05-07T15:00:00Z"},
                end={"dateTime": "2026-05-07T14:00:00Z"},
            )
        assert [c for c in fake_calendar.calls if c[0] == "create_event"] == []

    def test_zero_length_window_raises_and_creates_nothing(self, fake_calendar):
        # start == end is a zero-length event — also nonsensical.
        with pytest.raises(ValueError):
            create_event_from_email_impl(
                fake_calendar,
                summary="Instant",
                start={"dateTime": "2026-05-07T14:00:00Z"},
                end={"dateTime": "2026-05-07T14:00:00Z"},
            )
        assert [c for c in fake_calendar.calls if c[0] == "create_event"] == []

    def test_all_day_event_with_date_is_allowed(self, fake_calendar):
        # All-day events carry `date` instead of `dateTime` — that is a valid
        # time and must NOT trip the no-datetime guard.
        out = create_event_from_email_impl(
            fake_calendar,
            summary="Company holiday",
            start={"date": "2026-05-07"},
            end={"date": "2026-05-08"},
        )
        assert out["event_id"]


# ---------------------------------------------------------------------------
# Through the registered @tool — the real agent-facing surface
# ---------------------------------------------------------------------------


def _make_email_agent(fake_gmail, fake_calendar, tmp_path):
    """Construct an EmailTriageAgent with backends injected and the AgentSDK
    mocked (no live LLM). Mirrors the helper in test_calendar_conflicts.py.
    """
    from unittest.mock import MagicMock, patch

    from gaia.agents.email.agent import EmailTriageAgent
    from gaia.agents.email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=fake_gmail,
        calendar_backend=fake_calendar,
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
    )
    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


class TestCreateEventFromEmailTool:
    """Exercise the registered ``create_event_from_email`` tool end-to-end:
    args in, JSON envelope out, real backend behind it.
    """

    def _tool(self, name):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        return _TOOL_REGISTRY[name]["function"]

    def test_tool_creates_event_with_extracted_details(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            out = json.loads(
                self._tool("create_event_from_email")(
                    "Q2 roadmap sync",
                    "2026-05-07T14:00:00Z",
                    "2026-05-07T15:00:00Z",
                    "alice@example.com",
                )
            )
            assert out["ok"] is True
            assert out["data"]["summary"] == "Q2 roadmap sync"
            create_calls = [c for c in fake_calendar.calls if c[0] == "create_event"]
            assert len(create_calls) == 1
            assert create_calls[0][1]["attendees"] == ["alice@example.com"]
        finally:
            agent.close_db()

    def test_tool_no_datetime_returns_error_and_creates_nothing(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        # The no-datetime negative case at the tool boundary: empty start/end
        # must yield an ok=False envelope, never a silent bogus event.
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            out = json.loads(
                self._tool("create_event_from_email")(
                    "Let's catch up",
                    "",
                    "",
                )
            )
            assert out["ok"] is False
            assert "error" in out
            # No event was created on the calendar.
            assert [c for c in fake_calendar.calls if c[0] == "create_event"] == []
        finally:
            agent.close_db()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
