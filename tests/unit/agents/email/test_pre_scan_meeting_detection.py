# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Wiring tests for meeting-request detection during the inbox scan (#2583).

``detect_meeting_request_heuristic`` (#1272) has existed for over a year but
was never called from the triage/pre-scan path — it only ran when a caller
pointed at one specific message and asked. These tests lock in the wiring:

- ``triage_inbox_impl`` attaches ``is_meeting_request`` to every message's
  decision, gated on ``is_meeting_request and confidence == "high"`` (never
  on confidence alone — a confident NEGATIVE must not be flagged, trap 1).
- ``pre_scan_inbox_impl`` carries the flag through onto the ``PreScanItem``-
  shaped rows it emits.
- The scan stays cheap: detection reads the already-fetched snippet, never
  triggers a full-body MIME decode or an LLM call (#1265's contract, kept
  intact — this file adds new coverage rather than touching
  ``test_pre_scan_counts.py``, whose assertions must stay unmodified).
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.tools import read_tools  # noqa: E402
from gaia_agent_email.tools.attention_tools import (  # noqa: E402
    build_attention_view_impl,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    pre_scan_inbox_impl,
    triage_inbox_impl,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str,
    sender: str,
    label_ids: List[str],
    snippet: str,
    body: str = "",
) -> Dict[str, Any]:
    """A minimal Gmail API v1 message dict. ``snippet`` is what the scan path
    reads for meeting detection (cheap — no MIME decode); ``body`` (defaults
    to ``snippet`` when omitted) only matters if a test spies on the decoded
    full body.
    """
    full_body = body or snippet
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": list(label_ids),
        "snippet": snippet,
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"size": len(full_body), "data": _b64url(full_body)},
        },
        "sizeEstimate": len(full_body),
    }


@pytest.fixture
def gmail_with_meeting_and_plain_messages() -> FakeGmailBackend:
    gmail = FakeGmailBackend()
    gmail.add_message(
        _msg(
            "m_meeting",
            subject="Quick sync",
            sender="colleague@example.com",
            label_ids=["INBOX"],
            snippet="Can we meet Thursday at 2pm to go over the roadmap?",
        )
    )
    gmail.add_message(
        _msg(
            "m_plain",
            subject="Your order shipped",
            sender="ship@retailer.example.com",
            label_ids=["INBOX", "CATEGORY_UPDATES"],
            snippet="Your package is on its way and will arrive Tuesday.",
        )
    )
    gmail.add_message(
        _msg(
            "m_ambiguous",
            subject="Catching up",
            sender="colleague@example.com",
            label_ids=["INBOX"],
            snippet="Let's sync sometime soon, it's been a while.",
        )
    )
    return gmail


class TestTriageInboxAttachesMeetingFlag:
    def test_confident_positive_is_flagged(self, gmail_with_meeting_and_plain_messages):
        out = triage_inbox_impl(gmail_with_meeting_and_plain_messages, max_messages=50)
        by_id = {r["id"]: r for r in out["results"]}
        assert by_id["m_meeting"]["is_meeting_request"] is True

    def test_confident_negative_is_not_flagged(
        self, gmail_with_meeting_and_plain_messages
    ):
        # Trap 1: the heuristic's "no signal" branch returns
        # confidence="high" with is_meeting_request=False. Gating on
        # confidence alone (instead of "is_meeting_request AND
        # confidence == high") would wrongly flag this ordinary message.
        out = triage_inbox_impl(gmail_with_meeting_and_plain_messages, max_messages=50)
        by_id = {r["id"]: r for r in out["results"]}
        assert by_id["m_plain"]["is_meeting_request"] is False

    def test_ambiguous_low_confidence_is_not_flagged(
        self, gmail_with_meeting_and_plain_messages
    ):
        # Pre-scan never wires an LLM classifier (#1265) — a heuristic-
        # ambiguous message must surface as "not a meeting", never a guess.
        out = triage_inbox_impl(gmail_with_meeting_and_plain_messages, max_messages=50)
        by_id = {r["id"]: r for r in out["results"]}
        assert by_id["m_ambiguous"]["is_meeting_request"] is False

    def test_detection_reads_snippet_not_full_body(self, monkeypatch):
        """Meeting detection must use the already-fetched snippet, never
        trigger ``decode_message_body`` (the expensive full-MIME decode) —
        keeping the scan cheap (#1265)."""
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "m1",
                subject="Sync",
                sender="a@example.com",
                label_ids=["INBOX"],
                snippet="Can we meet Thursday at 2pm?",
                body="A much longer body that should never be decoded here.",
            )
        )
        decode_calls: List[Any] = []
        real_decode = read_tools.decode_message_body

        def _spy_decode(payload):
            decode_calls.append(payload)
            return real_decode(payload)

        monkeypatch.setattr(read_tools, "decode_message_body", _spy_decode)

        out = triage_inbox_impl(gmail, max_messages=10)
        assert out["results"][0]["is_meeting_request"] is True
        assert decode_calls == []


class TestPreScanCarriesMeetingFlag:
    def test_meeting_item_carries_flag_through_pre_scan(
        self, gmail_with_meeting_and_plain_messages
    ):
        out = pre_scan_inbox_impl(
            gmail_with_meeting_and_plain_messages, max_messages=50
        )
        # "Can we meet Thursday at 2pm?" has no category label signal, so the
        # heuristic is not confident about its category — it lands in
        # needs_review (#2584), not actionable. The meeting flag must still
        # be present on the row regardless of which bucket it landed in.
        all_items = (
            out["urgent"]
            + out["actionable"]
            + out["suggested_archives"]
            + out["needs_review"]
        )
        meeting_items = [i for i in all_items if i.get("message_id") == "m_meeting"]
        assert meeting_items, f"m_meeting not found in any bucket: {out}"
        assert meeting_items[0]["is_meeting_request"] is True

    def test_non_meeting_items_are_not_flagged(
        self, gmail_with_meeting_and_plain_messages
    ):
        out = pre_scan_inbox_impl(
            gmail_with_meeting_and_plain_messages, max_messages=50
        )
        all_items = (
            out["urgent"]
            + out["actionable"]
            + out["suggested_archives"]
            + out["needs_review"]
        )
        non_meeting = [i for i in all_items if i.get("message_id") != "m_meeting"]
        assert non_meeting
        for item in non_meeting:
            assert item.get("is_meeting_request") is False

    def test_pre_scan_stays_cheap_with_meeting_detection(self, monkeypatch):
        """Wiring meeting detection into pre-scan must not regress the
        cheap-scan guarantee: no body decode, no LLM classifier."""
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "m1",
                subject="Sync",
                sender="a@example.com",
                label_ids=["INBOX"],
                snippet="Can we meet Thursday at 2pm?",
            )
        )
        decode_calls: List[Any] = []
        real_decode = read_tools.decode_message_body

        def _spy_decode(payload):
            decode_calls.append(payload)
            return real_decode(payload)

        monkeypatch.setattr(read_tools, "decode_message_body", _spy_decode)

        seen_classifiers: List[Any] = []
        real_triage = read_tools.triage_inbox_impl

        def _recording_triage(*args, **kwargs):
            seen_classifiers.append(kwargs.get("classifier"))
            return real_triage(*args, **kwargs)

        monkeypatch.setattr(read_tools, "triage_inbox_impl", _recording_triage)

        out = pre_scan_inbox_impl(gmail, max_messages=10)

        assert decode_calls == []
        assert seen_classifiers == [None]
        all_items = (
            out["urgent"]
            + out["actionable"]
            + out["suggested_archives"]
            + out["needs_review"]
        )
        assert any(i.get("is_meeting_request") is True for i in all_items)


class TestGroundingIncidentSurfacesAsNeedingReply:
    """End-to-end regression for the #2580 epic's grounding incident.

    On 2026-07-28, on ``main``, a colleague's message combining a direct
    question with an informal meeting-time proposal — "Did you have a
    chance to look through the code and get a pull request? Any chance to
    meet this Thursday at 9am?" — was classified confidently FYI and
    reported under "0 actionable items" (12 scanned, 12 by heuristic, 0
    escalated to the LLM). #2589 wired ``detect_meeting_request_heuristic``
    into the scan; #2743 built the ``needs_you`` worklist the TUI actually
    renders on open (replacing the #2582 ``/attention`` fetch — see
    ``tui/internal/ui/chat/model.go``'s ``preScanFetchedMsg`` comment).

    That left a gap: ``_build_needs_you_view`` only relabels an item
    already routed into ``urgent``/``actionable`` by CATEGORY, and its
    ``needs_review`` loop dropped ``is_meeting_request`` entirely. A
    message the category heuristic confidently calls FYI/PERSONAL (e.g.
    Gmail's own ``CATEGORY_PERSONAL`` label — the plausible real shape of
    the incident message) kept ``is_meeting_request=True`` from the scan
    but was silently invisible in the view the TUI reads on open — the
    incident, reproduced verbatim on current ``main``.
    """

    INCIDENT_TEXT = (
        "Did you have a chance to look through the code and get a pull "
        "request? Any chance to meet this Thursday at 9am?"
    )

    def _gmail_with_incident_message(self, *, label_ids: List[str]) -> FakeGmailBackend:
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "incident_msg",
                subject="Quick check-in",
                sender="colleague@example.com",
                label_ids=label_ids,
                snippet=self.INCIDENT_TEXT,
            )
        )
        return gmail

    def test_incident_message_is_not_silently_informational(self):
        # Gmail's own Personal-tab label makes the category heuristic
        # commit confident=True unconditionally (triage_heuristics.py rule
        # 5) — isolating whether is_meeting_request alone can save the
        # message from the bare-count bucket.
        gmail = self._gmail_with_incident_message(
            label_ids=["INBOX", "CATEGORY_PERSONAL"]
        )
        out = pre_scan_inbox_impl(gmail, max_messages=50)
        assert out["informational_count"] == 0, (
            "the incident message must not be silently counted as "
            f"informational with no other trace: {out}"
        )

    def test_incident_message_surfaces_in_needs_you(self):
        gmail = self._gmail_with_incident_message(
            label_ids=["INBOX", "CATEGORY_PERSONAL"]
        )
        out = pre_scan_inbox_impl(gmail, max_messages=50)
        # The #2580 acceptance criterion, directly: it must surface as
        # needing a reply, not disappear under "0 actionable items".
        assert (
            out["needs_you_total"] >= 1
        ), f"expected the incident message to surface in needs_you: {out}"
        matches = [i for i in out["needs_you"] if i.get("message_id") == "incident_msg"]
        assert matches, f"incident_msg not present in needs_you: {out['needs_you']}"
        assert matches[0]["kind"] == "meeting_request", (
            "expected kind='meeting_request' so the TUI can name the "
            f"proposed time, got {matches[0]!r}"
        )

    def test_meeting_flag_survives_needs_review_routing(self):
        # No category-label signal at all -> unconfident FYI -> needs_review
        # by the pre-existing #2584/#2743 path. The meeting flag must not
        # be discarded there either (needs_review previously always tagged
        # kind="needs_review", regardless of is_meeting_request).
        gmail = self._gmail_with_incident_message(label_ids=["INBOX"])
        out = pre_scan_inbox_impl(gmail, max_messages=50)
        matches = [i for i in out["needs_you"] if i.get("message_id") == "incident_msg"]
        assert matches, f"incident_msg not present in needs_you: {out['needs_you']}"
        assert (
            matches[0]["kind"] == "meeting_request"
        ), f"needs_review routing must not discard the meeting flag: {matches[0]!r}"

    def test_incident_message_via_attention_view(self):
        # The originally-shipped #2582 mechanism — still reachable via
        # GET /v1/email/attention even though the TUI no longer calls it on
        # open (#2743) — must keep catching this. Regression guard so a
        # future change to attention_tools.py can't quietly reopen it.
        gmail = self._gmail_with_incident_message(
            label_ids=["INBOX", "CATEGORY_PERSONAL"]
        )
        out = build_attention_view_impl({"google": gmail}, max_messages=50)
        matches = [i for i in out["items"] if i.get("message_id") == "incident_msg"]
        assert matches, f"incident_msg missing from attention view: {out['items']}"
        assert matches[0]["kind"] == "meeting_request"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
