# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2743 — one triage card that tells the user what to do.

``needs_you`` is a deterministic VIEW over the already-classified urgent/
actionable/needs_review buckets — never a second, independent classification
pass (Adversarial Reflection #1: a re-derivation would drop urgent mail that
isn't also a meeting/waiting-on-you match). These tests pin that regression,
the kind-then-age ordering, the 5-item cap with an honest ``needs_you_total``,
and ``bulk.filter_tests`` never being an unaudited bare count.
"""

from __future__ import annotations

import base64
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# parents[0]=tests/, [1]=python/, [2]=email/, [3]=agents/, [4]=hub/, [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.config import DEFAULT_INBOX_SCAN_MESSAGES  # noqa: E402
from gaia_agent_email.contract import NeedsYouItem  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    FILTER_TEST_FYI,
    FILTER_TEST_PROMOTIONAL,
    NEEDS_YOU_CAP,
    merge_pre_scan_backends,
    pre_scan_inbox_impl,
)
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    CATEGORY_NEEDS_RESPONSE,
    CATEGORY_URGENT,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str = "Neutral subject, no keyword signal",
    sender: str = "alice@example.com",
    label_ids: Optional[List[str]] = None,
    internal_date: str = "1750000000000",
    body: str = "Some neutral body content with no keyword signal at all.",
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": label_ids or ["INBOX"],
        "snippet": body[:200],
        "internalDate": internal_date,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"data": _b64url(body), "size": len(body.encode("utf-8"))},
        },
        "sizeEstimate": len(body),
    }


def _slm_by_id(mapping: Dict[str, str]):
    """Category-SLM stub: only messages named in ``mapping`` get a confident
    verdict — everything else falls through and stays heuristic-unconfident
    (i.e. lands in ``needs_review``), matching how the heuristic module can
    never assign URGENT/NEEDS_RESPONSE on its own.
    """

    def _classifier(*, subject, sender, body, message_id=""):
        category = mapping.get(message_id)
        if category is None:
            return None
        return {"category": category, "confidence": 0.9, "source": "slm"}

    return _classifier


class TestNeedsYouNeverDropsClassifiedMail:
    """AC#11 / Adversarial Reflection #1: every urgent/actionable/needs_review
    item must appear in needs_you — it is a VIEW, never a re-derivation that
    could silently miss one of them.
    """

    def _seed(self) -> FakeGmailBackend:
        gmail = FakeGmailBackend(user_email="me@example.com")
        gmail.add_message(_msg("urgent-1", internal_date="1700000000000"))
        gmail.add_message(
            _msg(
                "urgent-meeting-1",
                subject="can we meet tomorrow to go over the budget?",
                internal_date="1710000000000",
            )
        )
        gmail.add_message(_msg("actionable-1", internal_date="1720000000000"))
        gmail.add_message(_msg("needs-review-1", internal_date="1730000000000"))
        return gmail

    def test_every_urgent_actionable_needs_review_item_appears_in_needs_you(self):
        gmail = self._seed()
        out = pre_scan_inbox_impl(
            gmail,
            max_messages=10,
            slm_classifier=_slm_by_id(
                {
                    "urgent-1": CATEGORY_URGENT,
                    "urgent-meeting-1": CATEGORY_URGENT,
                    "actionable-1": CATEGORY_NEEDS_RESPONSE,
                }
            ),
        )
        source_ids = {
            item["message_id"]
            for section in ("urgent", "actionable", "needs_review")
            for item in out[section]
        }
        assert source_ids == {"urgent-1", "urgent-meeting-1", "actionable-1", "needs-review-1"}

        needs_you_ids = {item["message_id"] for item in out["needs_you"]}
        assert source_ids <= needs_you_ids, (
            "every urgent/actionable/needs_review item must appear in "
            f"needs_you; missing: {source_ids - needs_you_ids}"
        )

    def test_meeting_request_gets_meeting_kind_not_waiting_on_you(self):
        gmail = self._seed()
        out = pre_scan_inbox_impl(
            gmail,
            max_messages=10,
            slm_classifier=_slm_by_id(
                {"urgent-1": CATEGORY_URGENT, "urgent-meeting-1": CATEGORY_URGENT}
            ),
        )
        by_id = {item["message_id"]: item for item in out["needs_you"]}
        assert by_id["urgent-meeting-1"]["kind"] == "meeting_request"
        assert by_id["urgent-1"]["kind"] == "waiting_on_you"

    def test_needs_review_item_gets_needs_review_kind(self):
        gmail = self._seed()
        out = pre_scan_inbox_impl(
            gmail,
            max_messages=10,
            slm_classifier=_slm_by_id(
                {"urgent-1": CATEGORY_URGENT, "urgent-meeting-1": CATEGORY_URGENT}
            ),
        )
        by_id = {item["message_id"]: item for item in out["needs_you"]}
        assert by_id["needs-review-1"]["kind"] == "needs_review"


class TestNeedsYouOrdering:
    def test_ordered_by_kind_then_oldest_first_with_contiguous_ref(self):
        gmail = FakeGmailBackend(user_email="me@example.com")
        # Two waiting_on_you-kind items, newer one added first to prove sort
        # order isn't scan-order luck.
        gmail.add_message(_msg("urgent-new", internal_date="1760000000000"))
        gmail.add_message(_msg("urgent-old", internal_date="1700000000000"))
        out = pre_scan_inbox_impl(
            gmail,
            max_messages=10,
            slm_classifier=_slm_by_id(
                {"urgent-new": CATEGORY_URGENT, "urgent-old": CATEGORY_URGENT}
            ),
        )
        refs = [item["ref"] for item in out["needs_you"]]
        assert refs == list(range(1, len(refs) + 1)), "ref must be contiguous, 1-based"
        ids_in_order = [item["message_id"] for item in out["needs_you"]]
        assert ids_in_order.index("urgent-old") < ids_in_order.index("urgent-new"), (
            "oldest-first within the same kind"
        )


class TestNeedsYouCap:
    def test_capped_at_five_with_honest_total(self):
        gmail = FakeGmailBackend(user_email="me@example.com")
        mapping = {}
        for i in range(7):
            msg_id = f"urgent-{i}"
            gmail.add_message(_msg(msg_id, internal_date=str(1700000000000 + i)))
            mapping[msg_id] = CATEGORY_URGENT
        out = pre_scan_inbox_impl(
            gmail, max_messages=10, slm_classifier=_slm_by_id(mapping)
        )
        assert len(out["needs_you"]) == NEEDS_YOU_CAP == 5
        assert out["needs_you_total"] == 7


class TestBulkFilterTests:
    def test_filter_tests_non_empty_when_count_positive(self):
        gmail = FakeGmailBackend(user_email="me@example.com")
        gmail.add_message(
            _msg("promo-1", label_ids=["INBOX", "CATEGORY_PROMOTIONS"])
        )
        out = pre_scan_inbox_impl(gmail, max_messages=10)
        assert out["bulk"]["count"] > 0
        assert out["bulk"]["filter_tests"], "filter_tests must be non-empty when count > 0"
        assert FILTER_TEST_PROMOTIONAL in out["bulk"]["filter_tests"]

    def test_bulk_count_zero_and_no_filter_tests_when_nothing_filtered(self):
        gmail = FakeGmailBackend(user_email="me@example.com")
        gmail.add_message(_msg("urgent-1"))
        out = pre_scan_inbox_impl(
            gmail,
            max_messages=10,
            slm_classifier=_slm_by_id({"urgent-1": CATEGORY_URGENT}),
        )
        assert out["bulk"]["count"] == 0
        assert out["bulk"]["filter_tests"] == []

    def test_updates_label_maps_to_fyi_filter_test(self):
        gmail = FakeGmailBackend(user_email="me@example.com")
        gmail.add_message(_msg("update-1", label_ids=["INBOX", "CATEGORY_UPDATES"]))
        out = pre_scan_inbox_impl(gmail, max_messages=10, include_informational=True)
        assert out["bulk"]["count"] > 0
        assert FILTER_TEST_FYI in out["bulk"]["filter_tests"]


class TestMergeAcrossBackendsRenumbers:
    def test_merge_reassigns_contiguous_ref_across_mailboxes(self):
        gmail_a = FakeGmailBackend(user_email="me@example.com")
        gmail_a.add_message(_msg("a-urgent", internal_date="1700000000000"))
        gmail_b = FakeGmailBackend(user_email="me@example.com")
        gmail_b.add_message(_msg("b-urgent", internal_date="1710000000000"))

        merged = merge_pre_scan_backends(
            {"google": gmail_a, "microsoft": gmail_b},
            max_messages=10,
            slm_classifier=_slm_by_id(
                {"a-urgent": CATEGORY_URGENT, "b-urgent": CATEGORY_URGENT}
            ),
        )
        refs = [item["ref"] for item in merged["needs_you"]]
        assert refs == list(range(1, len(refs) + 1))
        ids = {item["message_id"] for item in merged["needs_you"]}
        assert ids == {"a-urgent", "b-urgent"}
        assert merged["needs_you_total"] == 2


class TestContractAdditivity:
    def test_detail_rejects_more_than_two_entries(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            NeedsYouItem(
                ref=1,
                kind="waiting_on_you",
                why="waiting",
                detail=["one", "two", "three"],
            )

    def test_detail_entry_rejects_over_240_chars(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            NeedsYouItem(
                ref=1,
                kind="waiting_on_you",
                why="waiting",
                detail=["x" * 241],
            )

    def test_v210_fields_still_present_and_unchanged(self):
        """Additive-only guarantee: every field EmailPreScanResult carried at
        schema 2.10 is still present with an unchanged annotation."""
        from gaia_agent_email.contract import EmailPreScanResult

        expected_210_fields = {
            "kind",
            "urgent",
            "actionable",
            "informational_count",
            "informational",
            "suggested_archives",
            "suggested_drafts",
            "preferences_applied",
            "totals",
            "needs_review",
            "scanned",
            "total_unread",
            "total_inbox",
            "degraded",
            "mailbox_errors",
        }
        current_fields = set(EmailPreScanResult.model_fields)
        missing = expected_210_fields - current_fields
        assert not missing, f"2.10 fields removed from EmailPreScanResult: {missing}"
        # New fields only ever ADD to the set — never replace/rename.
        assert current_fields - expected_210_fields == {
            "needs_you",
            "needs_you_total",
            "bulk",
        }


class TestScanDefaultUnification:
    """AC#2: every scan default resolves to the one shared constant."""

    def test_triage_inbox_impl_default_is_shared_constant(self):
        from gaia_agent_email.tools.read_tools import triage_inbox_impl

        default = inspect.signature(triage_inbox_impl).parameters["max_messages"].default
        assert default == DEFAULT_INBOX_SCAN_MESSAGES

    def test_pre_scan_inbox_impl_default_is_shared_constant(self):
        default = inspect.signature(pre_scan_inbox_impl).parameters["max_messages"].default
        assert default == DEFAULT_INBOX_SCAN_MESSAGES

    def test_merge_pre_scan_backends_default_is_shared_constant(self):
        default = inspect.signature(merge_pre_scan_backends).parameters["max_messages"].default
        assert default == DEFAULT_INBOX_SCAN_MESSAGES

    def test_attention_scan_default_is_shared_constant(self):
        from gaia_agent_email.tools.attention_tools import DEFAULT_ATTENTION_SCAN_MESSAGES

        assert DEFAULT_ATTENTION_SCAN_MESSAGES == DEFAULT_INBOX_SCAN_MESSAGES

    def test_prescan_request_default_matches_shared_constant(self):
        from gaia_agent_email.contract import EmailPreScanRequest

        req = EmailPreScanRequest()
        assert req.max_messages == DEFAULT_INBOX_SCAN_MESSAGES
