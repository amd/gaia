# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2638 — pre-scan triage covers read mail, matching the attention view and
waiting-on-you (both already scanned all of INBOX regardless of read state).

The old rationale for ``_PRE_SCAN_LABEL_IDS = ["INBOX", "UNREAD"]`` was that
narrowing the listing query made ``resultSizeEstimate`` mean "how many
unread". #2584 already stopped using that field for the coverage number
(it's sourced from ``labels().get("INBOX")`` instead) — so narrowing the
query bought nothing, while making the single highest-value triage bucket
(read-but-unanswered mail) permanently invisible. The decision (#2638): drop
the UNREAD narrowing. Pre-scan now scans the whole INBOX, same as the
attention view and waiting-on-you.

Coverage honesty (AC2/AC3): now that the scan covers read+unread, reporting
ONLY ``total_unread`` as the denominator would itself be misleading (a
100-message scan of an 800-message inbox with 500 unread can't honestly
frame coverage as "100 of 500 unread" when 300 of the inbox's messages are
neither scanned nor unread). ``total_inbox`` (Gmail's exact
``messagesTotal``, sourced from the SAME ``get_label`` call already made for
``total_unread`` -- no extra round-trip) is the new, honest whole-population
denominator; ``total_unread`` remains as a secondary "how many of these are
still unread" figure.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.attention_tools import (  # noqa: E402
    build_attention_view_impl,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    _PRE_SCAN_LABEL_IDS,
    pre_scan_inbox_impl,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str, *, subject: str, sender: str, label_ids: List[str], body: str
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": list(label_ids),
        "snippet": body[:200],
        "internalDate": "1700000000000",
        "sizeEstimate": len(body),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"size": len(body), "data": _b64url(body)},
        },
    }


class TestLabelIdsNoLongerNarrowToUnread:
    def test_pre_scan_label_ids_is_inbox_only(self):
        assert "UNREAD" not in _PRE_SCAN_LABEL_IDS
        assert "INBOX" in _PRE_SCAN_LABEL_IDS


class TestReadButUnansweredMailIsScanned:
    """The issue's own worked example: a READ human message that still
    needs a reply, plus an unread newsletter. Both must be scanned."""

    def _seed(self) -> FakeGmailBackend:
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "read-but-unanswered-001",
                subject="Re: contract draft — need your sign-off",
                sender="Dana Whitfield <dana@northbay-supply.example>",
                # Read: INBOX only, no UNREAD.
                label_ids=["INBOX"],
                body="Sending the draft back with my changes. Can you confirm by Thursday?",
            )
        )
        gmail.add_message(
            _msg(
                "unread-newsletter-001",
                subject="Tuesday briefing",
                sender="Example Daily <news@example-daily.invalid>",
                label_ids=["INBOX", "UNREAD", "CATEGORY_UPDATES"],
                body="Your Tuesday news roundup.",
            )
        )
        return gmail

    def test_read_message_is_scanned_and_classified_needs_response(self):
        gmail = self._seed()
        out = pre_scan_inbox_impl(gmail, max_messages=25)
        assert out["scanned"] == 2, (
            "both the read human message and the unread newsletter must be "
            f"scanned; got scanned={out['scanned']}"
        )
        scanned_ids = {
            item["message_id"]
            for section in ("urgent", "actionable", "suggested_archives", "needs_review")
            for item in out[section]
        } | {
            item["message_id"]
            for item in out["needs_review"]
        }
        # informational messages aren't individually surfaced by id (only a
        # count), so check every bucket that DOES carry ids for the target.
        all_ids = set()
        for section in ("urgent", "actionable", "suggested_archives", "needs_review"):
            all_ids.update(item["message_id"] for item in out[section])
        assert "read-but-unanswered-001" in all_ids, (
            "the read-but-unanswered message must appear in one of pre-scan's "
            f"id-carrying sections; sections were: {out}"
        )

    def test_unread_only_scan_would_have_missed_it(self):
        """Sanity check on the OLD behavior, proving the fix matters: an
        explicit UNREAD-narrowed scan does NOT see the read message."""
        gmail = self._seed()
        from gaia_agent_email.tools.read_tools import triage_inbox_impl

        old_style = triage_inbox_impl(
            gmail, max_messages=25, label_ids=["INBOX", "UNREAD"]
        )
        scanned_ids = {r["id"] for r in old_style["results"]}
        assert "read-but-unanswered-001" not in scanned_ids
        assert "unread-newsletter-001" in scanned_ids


class TestCrossSurfaceConsistency:
    """The issue's real point: pre-scan and the attention view must scan
    the SAME set of messages for the same mailbox and ceiling -- two
    surfaces answering "what's in my inbox" must agree. Verified by
    comparing the label_ids each surface actually queries with, since that
    query is what determines the scanned population: pre_scan_inbox_impl
    delegates to triage_inbox_impl(label_ids=_PRE_SCAN_LABEL_IDS), and the
    attention view's _scan_one_backend calls triage_inbox_impl(label_ids=
    ["INBOX"]) directly -- both must now be plain ["INBOX"]."""

    def test_prescan_and_attention_view_query_the_same_label_ids(self):
        gmail = FakeGmailBackend()
        for i in range(10):
            labels = ["INBOX"] if i % 2 == 0 else ["INBOX", "UNREAD"]
            gmail.add_message(
                _msg(
                    f"m{i}",
                    subject=f"Message {i}",
                    sender=f"person{i}@example.com",
                    label_ids=labels,
                    body=f"Body of message {i}.",
                )
            )
        pre_scan_inbox_impl(gmail, max_messages=10)
        list_calls = [c for c in gmail.transport.calls if c[0] == "list_messages"]
        assert list_calls, "pre-scan never called list_messages"
        prescan_label_ids = set(list_calls[0][1].get("label_ids") or [])

        gmail2 = FakeGmailBackend()
        for i in range(10):
            labels = ["INBOX"] if i % 2 == 0 else ["INBOX", "UNREAD"]
            gmail2.add_message(
                _msg(
                    f"m{i}",
                    subject=f"Message {i}",
                    sender=f"person{i}@example.com",
                    label_ids=labels,
                    body=f"Body of message {i}.",
                )
            )
        build_attention_view_impl({"google": gmail2}, max_messages=10)
        attention_list_calls = [
            c for c in gmail2.transport.calls if c[0] == "list_messages"
        ]
        attention_label_ids = set(attention_list_calls[0][1].get("label_ids") or [])

        assert prescan_label_ids == attention_label_ids == {"INBOX"}, (
            f"pre-scan queried {prescan_label_ids!r}, attention view queried "
            f"{attention_label_ids!r} -- both must be the plain INBOX query "
            "now (#2638)"
        )


class TestTotalInboxField:
    def test_total_inbox_reports_exact_messages_total(self):
        gmail = FakeGmailBackend()
        for i in range(7):
            labels = ["INBOX"] if i % 2 else ["INBOX", "UNREAD"]
            gmail.add_message(
                _msg(
                    f"m{i}",
                    subject=f"m{i}",
                    sender="a@example.com",
                    label_ids=labels,
                    body="hi",
                )
            )
        out = pre_scan_inbox_impl(gmail, max_messages=25)
        assert out["total_inbox"] == 7
        # 4 have UNREAD (i=0,2,4,6), matching the fixture's alternation.
        assert out["total_unread"] == 4

    def test_total_inbox_and_total_unread_come_from_one_get_label_call(self):
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg("m1", subject="hi", sender="a@example.com", label_ids=["INBOX"], body="hi")
        )
        pre_scan_inbox_impl(gmail, max_messages=25)
        label_calls = [c for c in gmail.transport.calls if c[0] == "get_label"]
        assert len(label_calls) == 1, (
            "total_inbox must not cost a SECOND get_label round-trip beyond "
            f"the one total_unread already made; got {len(label_calls)} calls"
        )

    def test_total_inbox_is_none_when_backend_cant_report_it(self):
        """Mirrors total_unread's Outlook honesty rule (#2584): unavailable
        must report None, never a fabricated number."""

        class _NoGetLabelBackend:
            def list_messages(self, *, query=None, label_ids=None, max_results=25, page_token=None):
                return {"messages": [], "nextPageToken": None, "resultSizeEstimate": 0}

        out = pre_scan_inbox_impl(_NoGetLabelBackend(), max_messages=25)
        assert out["total_inbox"] is None
        assert out["total_unread"] is None
