# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Calendar conflict-detection tests for the Email Triage Agent (issue #1273).

Conflict detection is deterministic interval arithmetic — no Lemonade, no
``gaia eval``. Every event is the half-open interval ``[start, end)``; two
intervals overlap iff ``a.start < b.end and b.start < a.end``. The boundary
case the issue calls out explicitly — ``2:00–3:00`` vs ``3:00–4:00`` — is
NOT a conflict, because abutting half-open intervals do not overlap.

Layers exercised here:

- ``intervals_overlap`` — the pure boolean half-open test, incl. the abut
  boundary in both directions and clear non-overlap.
- ``detect_calendar_conflicts_impl`` — queries the calendar backend for the
  candidate window and returns the overlapping events. Integration-style:
  runs against the in-memory ``FakeCalendarBackend`` fixture (no live
  calendar). Asserts the windowed ``list_events`` query is issued and that a
  backend error propagates (fail-loud — never a silent "no conflicts").
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

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    detect_calendar_conflicts_impl,
    intervals_overlap,
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


def _event(event_id: str, start_iso: str, end_iso: str, summary: str = "") -> dict:
    """Build a Google-Calendar-shaped event with timed start/end."""
    return {
        "id": event_id,
        "summary": summary or event_id,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
    }


# ---------------------------------------------------------------------------
# intervals_overlap — the pure half-open boundary math
# ---------------------------------------------------------------------------


class TestIntervalsOverlap:
    def test_clear_overlap(self):
        # 2:00-3:00 vs 2:30-3:30 — the second starts inside the first.
        assert intervals_overlap(120, 180, 150, 210) is True

    def test_abut_end_equals_start_is_not_a_conflict(self):
        # 2:00-3:00 vs 3:00-4:00 — they touch at 3:00 but do not overlap.
        # This is THE boundary case the issue calls out explicitly.
        assert intervals_overlap(120, 180, 180, 240) is False

    def test_abut_other_direction_is_not_a_conflict(self):
        # 3:00-4:00 vs 2:00-3:00 — same boundary, candidate after the busy
        # block. Order must not matter.
        assert intervals_overlap(180, 240, 120, 180) is False

    def test_clear_non_overlap_gap_between(self):
        # 2:00-3:00 vs 4:00-5:00 — an hour gap, plainly no conflict.
        assert intervals_overlap(120, 180, 240, 300) is False

    def test_containment_is_a_conflict(self):
        # 2:00-5:00 fully contains 3:00-4:00.
        assert intervals_overlap(120, 300, 180, 240) is True

    def test_identical_intervals_conflict(self):
        assert intervals_overlap(120, 180, 120, 180) is True

    def test_partial_overlap_at_start(self):
        # candidate 2:30-3:30 overlaps busy 2:00-3:00 on the [2:30,3:00) slice.
        assert intervals_overlap(150, 210, 120, 180) is True


# ---------------------------------------------------------------------------
# detect_calendar_conflicts_impl — against the fixture calendar
# ---------------------------------------------------------------------------


class TestDetectCalendarConflictsImpl:
    def test_overlap_is_flagged(self, fake_calendar):
        # Existing busy block 2:00-3:00; candidate 2:30-3:30 overlaps.
        fake_calendar.events["busy-1"] = _event(
            "busy-1", "2026-05-06T14:00:00Z", "2026-05-06T15:00:00Z", "Existing"
        )
        out = detect_calendar_conflicts_impl(
            fake_calendar,
            start_iso="2026-05-06T14:30:00Z",
            end_iso="2026-05-06T15:30:00Z",
        )
        assert out["has_conflict"] is True
        ids = [c["id"] for c in out["conflicts"]]
        assert "busy-1" in ids

    def test_abut_boundary_is_not_flagged(self, fake_calendar):
        # Existing 2:00-3:00; candidate 3:00-4:00 abuts but must NOT conflict.
        fake_calendar.events["busy-1"] = _event(
            "busy-1", "2026-05-06T14:00:00Z", "2026-05-06T15:00:00Z"
        )
        out = detect_calendar_conflicts_impl(
            fake_calendar,
            start_iso="2026-05-06T15:00:00Z",
            end_iso="2026-05-06T16:00:00Z",
        )
        assert out["has_conflict"] is False
        assert out["conflicts"] == []

    def test_clear_non_overlap_is_not_flagged(self, fake_calendar):
        # Existing 2:00-3:00; candidate 4:00-5:00 — an hour gap.
        fake_calendar.events["busy-1"] = _event(
            "busy-1", "2026-05-06T14:00:00Z", "2026-05-06T15:00:00Z"
        )
        out = detect_calendar_conflicts_impl(
            fake_calendar,
            start_iso="2026-05-06T16:00:00Z",
            end_iso="2026-05-06T17:00:00Z",
        )
        assert out["has_conflict"] is False

    def test_empty_calendar_has_no_conflict(self, fake_calendar):
        out = detect_calendar_conflicts_impl(
            fake_calendar,
            start_iso="2026-05-06T14:00:00Z",
            end_iso="2026-05-06T15:00:00Z",
        )
        assert out["has_conflict"] is False
        assert out["conflicts"] == []

    def test_multiple_events_only_overlapping_ones_flagged(self, fake_calendar):
        # Three events; only the middle one overlaps the candidate window.
        fake_calendar.events["before"] = _event(
            "before", "2026-05-06T12:00:00Z", "2026-05-06T13:00:00Z"
        )
        fake_calendar.events["overlap"] = _event(
            "overlap", "2026-05-06T14:30:00Z", "2026-05-06T15:30:00Z"
        )
        fake_calendar.events["after"] = _event(
            "after", "2026-05-06T18:00:00Z", "2026-05-06T19:00:00Z"
        )
        out = detect_calendar_conflicts_impl(
            fake_calendar,
            start_iso="2026-05-06T14:00:00Z",
            end_iso="2026-05-06T15:00:00Z",
        )
        ids = sorted(c["id"] for c in out["conflicts"])
        assert ids == ["overlap"]

    def test_queries_backend_with_the_candidate_window(self, fake_calendar):
        # The impl must hand the candidate window to the backend so the live
        # Google backend can filter server-side (the fake just records it).
        detect_calendar_conflicts_impl(
            fake_calendar,
            start_iso="2026-05-06T14:00:00Z",
            end_iso="2026-05-06T15:00:00Z",
        )
        list_calls = [c for c in fake_calendar.calls if c[0] == "list_events"]
        assert len(list_calls) == 1
        _, kwargs = list_calls[0]
        assert kwargs["time_min"] == "2026-05-06T14:00:00Z"
        assert kwargs["time_max"] == "2026-05-06T15:00:00Z"

    def test_z_and_offset_suffix_parse_equivalently(self, fake_calendar):
        # The busy block uses a +00:00 offset; the candidate uses Z. They
        # name the same instant and must still overlap.
        fake_calendar.events["busy-1"] = _event(
            "busy-1", "2026-05-06T14:00:00+00:00", "2026-05-06T15:00:00+00:00"
        )
        out = detect_calendar_conflicts_impl(
            fake_calendar,
            start_iso="2026-05-06T14:30:00Z",
            end_iso="2026-05-06T15:30:00Z",
        )
        assert out["has_conflict"] is True

    def test_all_day_event_overlapping_window_is_flagged(self, fake_calendar):
        # All-day events carry a `date` (no time); they span the whole day.
        fake_calendar.events["allday"] = {
            "id": "allday",
            "summary": "Company holiday",
            "start": {"date": "2026-05-06"},
            "end": {"date": "2026-05-07"},
        }
        out = detect_calendar_conflicts_impl(
            fake_calendar,
            start_iso="2026-05-06T14:00:00Z",
            end_iso="2026-05-06T15:00:00Z",
        )
        assert out["has_conflict"] is True
        assert out["conflicts"][0]["id"] == "allday"

    def test_event_with_unparseable_times_is_skipped_not_crashed(self, fake_calendar):
        # A malformed existing event must not crash detection, nor be counted
        # as a (spurious) conflict — it simply can't be compared.
        fake_calendar.events["bad"] = {
            "id": "bad",
            "summary": "broken",
            "start": {"dateTime": "not-a-timestamp"},
            "end": {"dateTime": "also-bad"},
        }
        fake_calendar.events["good"] = _event(
            "good", "2026-05-06T14:30:00Z", "2026-05-06T15:30:00Z"
        )
        out = detect_calendar_conflicts_impl(
            fake_calendar,
            start_iso="2026-05-06T14:00:00Z",
            end_iso="2026-05-06T15:00:00Z",
        )
        ids = [c["id"] for c in out["conflicts"]]
        assert ids == ["good"]

    def test_backend_error_propagates_never_silent_no_conflict(self):
        # Fail-loud: a backend that raises must surface the error, not return
        # a reassuring "no conflicts".
        class _BoomCalendar:
            def list_events(self, **kwargs):
                raise RuntimeError("calendar API exploded")

        with pytest.raises(RuntimeError, match="exploded"):
            detect_calendar_conflicts_impl(
                _BoomCalendar(),
                start_iso="2026-05-06T14:00:00Z",
                end_iso="2026-05-06T15:00:00Z",
            )

    def test_candidate_start_after_end_raises(self, fake_calendar):
        # A nonsensical window (end <= start) is a caller error, surfaced
        # loudly rather than silently returning no conflicts.
        with pytest.raises(ValueError):
            detect_calendar_conflicts_impl(
                fake_calendar,
                start_iso="2026-05-06T15:00:00Z",
                end_iso="2026-05-06T14:00:00Z",
            )


# ---------------------------------------------------------------------------
# Through the registered @tool — the real agent-facing surface
# ---------------------------------------------------------------------------


def _make_email_agent(fake_gmail, fake_calendar, tmp_path):
    """Construct an EmailTriageAgent with backends injected and the AgentSDK
    mocked (no live LLM). Mirrors the helper in test_email_agent_tools.py.
    """
    from unittest.mock import MagicMock, patch

    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

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


class TestDetectCalendarConflictsTool:
    """Exercise the registered ``detect_calendar_conflicts`` tool end-to-end:
    JSON envelope in, JSON envelope out, real backend behind it.
    """

    def _tool(self, name):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        return _TOOL_REGISTRY[name]["function"]

    def test_tool_flags_overlap_against_the_calendar(
        self, fake_gmail, fake_calendar, tmp_path
    ):

        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            fake_calendar.events["busy-1"] = _event(
                "busy-1", "2026-05-06T14:00:00Z", "2026-05-06T15:00:00Z", "Existing"
            )
            out = json.loads(
                self._tool("detect_calendar_conflicts")(
                    "2026-05-06T14:30:00Z", "2026-05-06T15:30:00Z"
                )
            )
            assert out["ok"] is True
            assert out["data"]["has_conflict"] is True
            assert out["data"]["conflicts"][0]["id"] == "busy-1"
        finally:
            agent.close_db()

    def test_tool_abut_boundary_reports_no_conflict(
        self, fake_gmail, fake_calendar, tmp_path
    ):

        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            fake_calendar.events["busy-1"] = _event(
                "busy-1", "2026-05-06T14:00:00Z", "2026-05-06T15:00:00Z"
            )
            # 3:00-4:00 abuts the 2:00-3:00 block — must be clean.
            out = json.loads(
                self._tool("detect_calendar_conflicts")(
                    "2026-05-06T15:00:00Z", "2026-05-06T16:00:00Z"
                )
            )
            assert out["ok"] is True
            assert out["data"]["has_conflict"] is False
        finally:
            agent.close_db()

    def test_tool_returns_error_envelope_on_inverted_window(
        self, fake_gmail, fake_calendar, tmp_path
    ):

        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            # Inverted window — the tool boundary turns the ValueError into a
            # clean error envelope (ok=False), never a silent success.
            out = json.loads(
                self._tool("detect_calendar_conflicts")(
                    "2026-05-06T15:00:00Z", "2026-05-06T14:00:00Z"
                )
            )
            assert out["ok"] is False
            assert "error" in out
        finally:
            agent.close_db()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
