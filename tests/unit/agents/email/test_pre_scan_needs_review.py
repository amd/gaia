# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Failing acceptance tests for the pre-scan "needs_review" honesty fix (#2584).

Bug: when the heuristic classifier is NOT confident about a message's
category (``confident=False`` in the per-message triage result), today's
``pre_scan_inbox_impl`` still files the message under whatever bucket its
placeholder guess maps to (``informational_count``, or ``suggested_archives``
for an unconfident PROMOTIONAL guess) instead of surfacing the doubt. This is
silent for ~97% of a typical unlabeled corpus.

The fix adds a ``needs_review`` bucket: the spam/phishing safety check still
runs FIRST and unconditionally routes to ``actionable`` (unchanged). After
that, ``confident is False`` overrides routing into the two LOW-SIGNAL
buckets ONLY -- ``informational`` and ``suggested_archives`` -- sending the
message to ``needs_review`` instead. It does NOT override ``urgent`` or
``actionable``: an unconfident guess toward a HIGH-signal category (e.g. an
IMPORTANT/STARRED-flagged message the heuristic can only tell is
NEEDS_RESPONSE, not yet urgent vs. merely actionable) already errs toward
surfacing, which is the correct direction to err -- pulling it into
needs_review would instead bury a message that needed attention behind a
5-of-295 arbitrary slice, a worse version of the bug this issue fixes.
``needs_review`` is capped like the other three buckets via a new
``PRE_SCAN_NEEDS_REVIEW_CAP`` constant and ordered newest-first (human
senders before automated ones on a timestamp tie), while
``totals.needs_review`` reports the full, uncapped count.

Every test in this module is expected to FAIL against the current
(unfixed) code -- most immediately at import time, because
``PRE_SCAN_NEEDS_REVIEW_CAP`` does not exist yet.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Make tests.fixtures importable (mirrors test_pre_scan_counts.py).
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")  # noqa: E402

# NOTE: this import is expected to raise ImportError against today's code --
# PRE_SCAN_NEEDS_REVIEW_CAP does not exist yet. That failure (not a typo, not
# an unrelated error) is the RED signal for the whole module.
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    PRE_SCAN_NEEDS_REVIEW_CAP,
    pre_scan_inbox_impl,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    """URL-safe base64 with stripped padding -- Gmail's body.data wire format."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str,
    sender: str,
    label_ids: List[str],
    body: str = "Body text for the fixture message.",
    internal_date: str = "1700000000000",
) -> Dict[str, Any]:
    """Build a minimal Gmail API v1 message dict (single text/plain part).

    Mirrors the ``_msg`` helper in ``test_pre_scan_counts.py`` -- duplicated
    here per that file's "leave it untouched" constraint.
    """
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": list(label_ids),
        "snippet": body[:120],
        "internalDate": internal_date,
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"size": len(body), "data": _b64url(body)},
        },
        "sizeEstimate": len(body),
    }


# ---------------------------------------------------------------------------
# Routing: confident=False overrides ALL category-based routing
# ---------------------------------------------------------------------------


class TestNeedsReviewRoutingOverridesCategoryGuess:
    def test_unlabeled_no_match_message_routes_to_needs_review(self):
        """The reported incident (#2584): an unlabeled human sender with a
        bare direct question falls through every heuristic rule to the
        terminal fallback (category=FYI, confident=False -- see
        ``triage_heuristics.py``'s final ``return`` in
        ``classify_category_heuristic``). It must land in ``needs_review``,
        never be silently counted as informational.
        """
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "m_meeting_ask",
                subject="Any chance to meet this Thursday at 9am?",
                sender="colleague@example.com",
                label_ids=["INBOX"],
            )
        )

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        needs_review_ids = {item["message_id"] for item in out["needs_review"]}
        assert "m_meeting_ask" in needs_review_ids, (
            "unconfident no-match message must surface in needs_review; "
            f"got needs_review={out['needs_review']!r}"
        )
        assert out["informational_count"] == 0, (
            "the unconfident message must NOT be silently counted as "
            f"informational; informational_count={out['informational_count']!r}"
        )

    def test_unconfident_important_label_stays_in_actionable_not_needs_review(self):
        """An IMPORTANT-labeled message is confident=False,
        category=NEEDS_RESPONSE under the current heuristic — the heuristic
        can't yet tell urgent from merely actionable, but it already knows
        this needs a reply. confident=False only overrides routing into the
        two LOW-SIGNAL buckets (informational / suggested_archives); it must
        NOT pull a message out of a high-signal bucket like actionable — an
        unconfident guess toward high signal already errs toward surfacing,
        which is the direction to err in. Burying a starred/important
        message in a 5-of-295 needs_review slice would be a worse version of
        the bug this issue exists to fix.
        """
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "m_important",
                subject="Please review the Q3 numbers",
                sender="boss@example.com",
                label_ids=["INBOX", "IMPORTANT"],
            )
        )

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        needs_review_ids = {item["message_id"] for item in out["needs_review"]}
        actionable_ids = {item["message_id"] for item in out["actionable"]}
        assert "m_important" in actionable_ids
        assert "m_important" not in needs_review_ids

    def test_unconfident_promotional_guess_routes_to_needs_review_not_archive(self):
        """A CATEGORY_PROMOTIONS-labeled message with a commitment/deadline
        signal in the body is confident=False, category=PROMOTIONAL (the
        #2113 commitment veto in ``triage_heuristics.classify_category_heuristic``).
        Today that lands in ``suggested_archives``; the fix must route it to
        ``needs_review`` instead.
        """
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "m_promo_with_deadline",
                subject="Your membership renewal",
                sender="billing@club.example.com",
                label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
                body="Please note: payment due by the end of the month.",
            )
        )

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        needs_review_ids = {item["message_id"] for item in out["needs_review"]}
        archive_ids = {item["message_id"] for item in out["suggested_archives"]}
        assert "m_promo_with_deadline" in needs_review_ids
        assert "m_promo_with_deadline" not in archive_ids


class TestNeedsReviewRowShape:
    def test_needs_review_items_have_the_same_row_shape_as_other_sections(self):
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "m_meeting_ask",
                subject="Any chance to meet this Thursday at 9am?",
                sender="colleague@example.com",
                label_ids=["INBOX"],
            )
        )

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        assert len(out["needs_review"]) == 1
        item = out["needs_review"][0]
        assert item["message_id"] == "m_meeting_ask"
        assert item["thread_id"] == "m_meeting_ask"
        assert item["sender"] == "colleague@example.com"
        assert item["subject"] == "Any chance to meet this Thursday at 9am?"
        assert "why" in item and item["why"]


# ---------------------------------------------------------------------------
# Cap: needs_review is capped like the other 3 buckets, but totals report the
# full uncapped count (the #2584 real-corpus regression this issue exists to
# prevent: ~290 unconfident rows rendered for a 305-message corpus).
# ---------------------------------------------------------------------------


class TestNeedsReviewCap:
    def test_needs_review_is_capped_but_totals_report_the_full_uncapped_count(self):
        gmail = FakeGmailBackend()
        n_unconfident = 30
        for i in range(n_unconfident):
            gmail.add_message(
                _msg(
                    f"m_unconfident_{i}",
                    subject=f"Quick question #{i}",
                    sender=f"person{i}@example.com",
                    label_ids=["INBOX"],
                )
            )

        out = pre_scan_inbox_impl(gmail, max_messages=50)

        assert len(out["needs_review"]) == PRE_SCAN_NEEDS_REVIEW_CAP, (
            "needs_review list must be capped at PRE_SCAN_NEEDS_REVIEW_CAP="
            f"{PRE_SCAN_NEEDS_REVIEW_CAP}; got {len(out['needs_review'])}"
        )
        assert out["totals"]["needs_review"] == n_unconfident, (
            "totals.needs_review must report the FULL uncapped count "
            f"({n_unconfident}), not the capped list length; got "
            f"{out['totals'].get('needs_review')!r}"
        )


# ---------------------------------------------------------------------------
# New top-level output fields
# ---------------------------------------------------------------------------


class TestPreScanNewTopLevelFields:
    def test_scanned_field_counts_every_bucket_including_needs_review(self):
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg(
                "m_important",
                subject="Please review the Q3 numbers",
                sender="boss@example.com",
                label_ids=["INBOX", "IMPORTANT"],
            )
        )
        gmail.add_message(
            _msg(
                "m_promo_label",
                subject="Weekend getaway ideas",
                sender="travel@deals.example.com",
                label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
            )
        )

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        total_across_buckets = (
            len(out["urgent"])
            + len(out["actionable"])
            + out["informational_count"]
            + len(out["suggested_archives"])
            + out["totals"]["needs_review"]
        )
        assert out["scanned"] == total_across_buckets == 2

    def test_degraded_false_and_mailbox_errors_absent_from_single_backend_call(self):
        """``degraded`` and ``mailbox_errors`` are a partial-failure concept
        that only exists at the multi-mailbox ``merge_pre_scan_backends``
        layer -- a direct ``pre_scan_inbox_impl`` call (single backend) must
        report ``degraded=False`` and omit/null ``mailbox_errors``.
        """
        gmail = FakeGmailBackend()
        gmail.add_message(
            _msg("m1", subject="hi", sender="a@example.com", label_ids=["INBOX"])
        )

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        assert out["degraded"] is False
        assert out.get("mailbox_errors") is None


# ---------------------------------------------------------------------------
# Deterministic needs_review ordering (#2584 redirect): a 5-of-295 slice is
# useless to a reader unless which 5 surface is a stated, defensible policy
# rather than an accident of backend scan order. newest-first, with a
# human-sender-before-automated-sender tiebreak on same-timestamp messages.
# ---------------------------------------------------------------------------


class TestNeedsReviewDeterministicOrdering:
    def test_needs_review_is_ordered_newest_first(self):
        gmail = FakeGmailBackend()
        # All three fall through to the terminal fallback (confident=False,
        # no label, no automated-sender/promo keyword match).
        gmail.add_message(
            _msg(
                "m_oldest",
                subject="Quick question A",
                sender="alice@example.com",
                label_ids=["INBOX"],
                internal_date="1600000000000",
            )
        )
        gmail.add_message(
            _msg(
                "m_newest",
                subject="Quick question B",
                sender="bob@example.com",
                label_ids=["INBOX"],
                internal_date="1800000000000",
            )
        )
        gmail.add_message(
            _msg(
                "m_middle",
                subject="Quick question C",
                sender="carol@example.com",
                label_ids=["INBOX"],
                internal_date="1700000000000",
            )
        )

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        ordered_ids = [item["message_id"] for item in out["needs_review"]]
        assert ordered_ids == ["m_newest", "m_middle", "m_oldest"], (
            "needs_review must be ordered newest-first; got " f"{ordered_ids!r}"
        )

    def test_needs_review_prefers_human_sender_over_automated_on_a_timestamp_tie(
        self,
    ):
        gmail = FakeGmailBackend()
        same_timestamp = "1700000000000"
        # "noreply" is an existing automated-sender signal
        # (triage_heuristics._AUTOMATED_SENDER_KEYWORDS) -- reused read-only
        # for ordering, not a new phrase list. "[SEV1]" trips the automated-
        # sender rule's own urgent-subject exception (rule 7), which is what
        # makes THIS automated message confident=False -- an automated
        # sender's message is normally confident=True (-> informational) and
        # would never reach needs_review at all.
        gmail.add_message(
            _msg(
                "m_automated",
                subject="[SEV1] Quick question A",
                sender="noreply@service.example.com",
                label_ids=["INBOX"],
                internal_date=same_timestamp,
            )
        )
        gmail.add_message(
            _msg(
                "m_human",
                subject="Quick question B",
                sender="dave@example.com",
                label_ids=["INBOX"],
                internal_date=same_timestamp,
            )
        )

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        ordered_ids = [item["message_id"] for item in out["needs_review"]]
        assert ordered_ids == ["m_human", "m_automated"], (
            "on a timestamp tie, a human sender must sort before an "
            f"automated one; got {ordered_ids!r}"
        )
