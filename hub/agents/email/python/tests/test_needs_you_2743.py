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
    _build_needs_you_view,
    merge_pre_scan_backends,
    pre_scan_inbox_impl,
)
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    CATEGORY_FYI,
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
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": thread_id or msg_id,
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

    def test_meeting_request_gets_meeting_kind_not_urgent(self):
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
        # #2743 redirect: a category-URGENT item is tagged "urgent" — NEVER
        # the detector's own "waiting_on_you" value (Adversarial Reflection's
        # kind-mislabeling fix).
        assert by_id["urgent-1"]["kind"] == "urgent"

    def test_actionable_item_gets_needs_response_kind(self):
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
        by_id = {item["message_id"]: item for item in out["needs_you"]}
        assert by_id["actionable-1"]["kind"] == "needs_response"

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

    def test_no_category_bucket_item_is_ever_tagged_waiting_on_you(self):
        """#2743 redirect: ``waiting_on_you`` is the detector's own signal —
        an item sourced from urgent/actionable/needs_review must never carry
        it, or the two provenances collide the moment the detector's real
        output is also wired into the same view.
        """
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
        category_bucket_ids = {
            item["message_id"]
            for section in ("urgent", "actionable", "needs_review")
            for item in out[section]
        }
        for item in out["needs_you"]:
            if item["message_id"] in category_bucket_ids:
                assert item["kind"] != "waiting_on_you", (
                    f"{item['message_id']} came from a category bucket but was "
                    "tagged waiting_on_you"
                )


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


# A known-good waiting-on-you fixture pair (mirrors
# test_waiting_on_you_tools.py / test_attention_tools.py): an earlier
# SUBSTANTIVE outbound reply from the user, then a later inbound direct-ask
# reply in the SAME thread — real back-and-forth, corroborated within the
# thread's own history, which is the detector's precision bar.
def _waiting_on_you_thread(
    *, inbound_id: str = "wonu-inbound-1", thread_id: str = "wonu-thread-1"
) -> List[Dict[str, Any]]:
    outbound = _msg(
        "wonu-outbound-1",
        thread_id=thread_id,
        sender="Me <me@example.com>",
        subject="Re: budget",
        body=(
            "Sure, I will take a look at the numbers and get back to you "
            "with any questions before the review."
        ),
        label_ids=["SENT"],
        internal_date="1690000000000",
    )
    inbound = _msg(
        inbound_id,
        thread_id=thread_id,
        sender="Dana <dana@example.com>",
        subject="Re: budget",
        body="Thanks! Could you please confirm the numbers by Friday?",
        label_ids=["INBOX"],
        internal_date="1700000000000",
    )
    return [outbound, inbound]


class TestWaitingOnYouDetectorWiring:
    """#2743 redirect fix 1: the waiting-on-you detector's own output must
    reach ``needs_you`` — it has no category bucket of its own, so before
    this fix it was invisible to the one view the card now renders from.
    """

    def test_waiting_on_you_detection_with_no_category_bucket_surfaces(self):
        gmail = FakeGmailBackend(user_email="me@example.com")
        for m in _waiting_on_you_thread():
            gmail.add_message(m)
        out = pre_scan_inbox_impl(
            gmail,
            max_messages=10,
            # Confidently FYI so the category router places it in
            # ``informational`` — proving the detector's own row is the
            # ONLY reason it appears in needs_you, not a category bucket.
            slm_classifier=_slm_by_id({"wonu-inbound-1": CATEGORY_FYI}),
        )
        assert out["informational_count"] == 1
        for section in ("urgent", "actionable", "needs_review"):
            assert not any(
                item["message_id"] == "wonu-inbound-1" for item in out[section]
            ), f"wonu-inbound-1 unexpectedly landed in {section}"

        by_id = {item["message_id"]: item for item in out["needs_you"]}
        assert "wonu-inbound-1" in by_id, (
            "a waiting-on-you detection with no category bucket must still "
            "surface in needs_you"
        )
        assert by_id["wonu-inbound-1"]["kind"] == "waiting_on_you"

    def test_waiting_on_you_detection_already_in_category_bucket_does_not_duplicate(
        self,
    ):
        gmail = FakeGmailBackend(user_email="me@example.com")
        for m in _waiting_on_you_thread():
            gmail.add_message(m)
        out = pre_scan_inbox_impl(
            gmail,
            max_messages=10,
            # This time the SAME message the detector independently
            # qualifies is ALSO confidently URGENT — it has a category
            # bucket, so the detector's signal must not add a second row.
            slm_classifier=_slm_by_id({"wonu-inbound-1": CATEGORY_URGENT}),
        )
        matches = [
            item for item in out["needs_you"] if item["message_id"] == "wonu-inbound-1"
        ]
        assert len(matches) == 1, (
            "a message classified urgent AND independently flagged "
            f"waiting-on-you must surface exactly once, not {len(matches)}"
        )
        assert matches[0]["kind"] == "urgent"


class TestActionItemWiring:
    """#2743 redirect fix 1: persisted action items have no category bucket
    of their own either — surfaced via ``action_db`` (mirrors
    ``build_attention_view_impl``'s own optional handle).
    """

    def test_open_action_item_surfaces_via_action_db(self):
        from gaia_agent_email import task_store
        from gaia_agent_email.contract import ActionItem

        from gaia.database.mixin import DatabaseMixin

        class _DB(DatabaseMixin):
            pass

        db = _DB()
        db.init_db(":memory:")
        task_store.init_schema(db)
        task_store.record_action_items(
            db,
            message_id="task-source-1",
            items=[ActionItem(description="Renew the SSL certificate", type="text")],
        )

        gmail = FakeGmailBackend(user_email="me@example.com")
        out = pre_scan_inbox_impl(gmail, max_messages=10, action_db=db)
        action_items = [item for item in out["needs_you"] if item["kind"] == "action_item"]
        assert len(action_items) == 1
        assert action_items[0]["subject"] == "Renew the SSL certificate"

    def test_no_action_db_means_no_action_items_and_no_error(self):
        gmail = FakeGmailBackend(user_email="me@example.com")
        out = pre_scan_inbox_impl(gmail, max_messages=10, action_db=None)
        assert [item for item in out["needs_you"] if item["kind"] == "action_item"] == []

    def test_action_item_with_message_id_none_surfaces(self):
        # A task carried from a prior triage with no recoverable source
        # message (``NeedsYouItem.message_id`` documents this explicitly,
        # ``AttentionItem`` too — "e.g. a pre-#1605 task row"). Exercised
        # directly against the view builder since today's task_store schema
        # enforces message_id NOT NULL at the DB layer, so this shape can
        # only arise from a row the DB itself would never produce.
        view = _build_needs_you_view(
            urgent=[],
            actionable=[],
            needs_review=[],
            action_items=[
                {
                    "message_id": None,
                    "description": "Follow up on the renewal",
                    "due_hint": "next week",
                }
            ],
        )
        assert len(view["needs_you"]) == 1
        item = view["needs_you"][0]
        assert item["message_id"] is None
        assert item["kind"] == "action_item"
        assert item["subject"] == "Follow up on the renewal"
        assert item["due_hint"] == "next week"


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
