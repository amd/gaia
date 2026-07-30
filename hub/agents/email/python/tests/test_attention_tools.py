# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Attention-view aggregation tests (#2582).

Acceptance criteria covered:
- Meeting proposals reach the view even for messages that would land in
  ``informational`` under the pre-scan envelope — the constraint-2 case this
  whole aggregator exists to fix.
- The four signals (waiting-on-you, meeting requests, needs-review, action
  items) merge into one item list.
- Coverage totals (scanned / total_unread / scan_truncated) are honest.
- A partial mailbox failure is recorded in mailbox_errors/degraded, without
  losing the surviving mailbox's results; a TOTAL failure raises instead of
  reading as "nothing needs you".
- The aggregator performs no mailbox mutation.

All tests are hermetic: FakeGmailBackend only, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

from gaia_agent_email.tools.attention_tools import (  # noqa: E402
    build_attention_view_impl,
)

from gaia.connectors.errors import ConnectorsError  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

USER_EMAIL = "user@example.com"
NOW_MS = 1_750_000_000_000
DAY_MS = 24 * 60 * 60 * 1000

_ALLOWED_BACKEND_CALLS = {
    "get_user_email",
    "list_messages",
    "get_thread",
    "get_message",
    "get_label",
}


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    thread_id: Optional[str] = None,
    sender: str = "colleague@example.com",
    to: str = USER_EMAIL,
    subject: str,
    body: str = "",
    label_ids: Optional[List[str]] = None,
    age_days: float = 1,
) -> Dict[str, Any]:
    tid = thread_id or msg_id
    return {
        "id": msg_id,
        "threadId": tid,
        "labelIds": list(label_ids or ["INBOX"]),
        "snippet": body[:200],
        "internalDate": str(int(NOW_MS - age_days * DAY_MS)),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
            ],
            "body": {"size": len(body), "data": _b64(body)},
        },
        "sizeEstimate": len(body),
    }


def _backend(*messages: Dict[str, Any]) -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email=USER_EMAIL)
    for m in messages:
        gmail.add_message(m)
    return gmail


# A confidently-informational message (Gmail CATEGORY_UPDATES) that is ALSO a
# meeting proposal — the exact case #2582 exists to catch: the pre-scan
# envelope would collapse this into informational_count with no row.
_MEETING_IN_INFORMATIONAL = _msg(
    "m_meeting_informational",
    subject="Team sync",
    body="Can we meet Thursday at 2pm to go over the roadmap?",
    label_ids=["INBOX", "CATEGORY_UPDATES"],
)

# A plain message with no label/category signal at all -- the heuristic
# cannot commit, so it needs review (#2584).
_PLAIN_UNCONFIDENT = _msg(
    "m_plain_unconfident",
    subject="Random note",
    body="Just circling back on something from last week.",
    label_ids=["INBOX"],
)

# A confidently-promotional message with no meeting signal -- must not
# surface anywhere in the attention view.
_CONFIDENT_PROMOTIONAL = _msg(
    "m_promo",
    subject="50% off everything",
    body="Big sale this week only!",
    label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
)


class TestMeetingRequestsSurfaceAcrossAllCategories:
    def test_meeting_in_confidently_informational_message_is_surfaced(self):
        # Constraint 2: this message would be a bare informational_count under
        # the pre-scan envelope. The aggregator must still surface it.
        gmail = _backend(_MEETING_IN_INFORMATIONAL)
        out = build_attention_view_impl({"google": gmail})
        meeting_items = [i for i in out["items"] if i["kind"] == "meeting_request"]
        assert len(meeting_items) == 1
        assert meeting_items[0]["message_id"] == "m_meeting_informational"
        assert meeting_items[0]["why"]

    def test_confidently_promotional_non_meeting_message_is_not_surfaced(self):
        gmail = _backend(_CONFIDENT_PROMOTIONAL)
        out = build_attention_view_impl({"google": gmail})
        assert out["items"] == []


class TestNeedsReviewSurfacing:
    def test_unconfident_message_becomes_needs_review_item(self):
        gmail = _backend(_PLAIN_UNCONFIDENT)
        out = build_attention_view_impl({"google": gmail})
        needs_review_items = [i for i in out["items"] if i["kind"] == "needs_review"]
        assert len(needs_review_items) == 1
        assert needs_review_items[0]["message_id"] == "m_plain_unconfident"

    def test_message_is_never_double_surfaced_as_both_kinds(self):
        # A message that is both meeting-flagged AND would independently
        # qualify for needs_review is surfaced exactly once, as a meeting
        # request (the higher-priority reason).
        gmail = _backend(_MEETING_IN_INFORMATIONAL)
        out = build_attention_view_impl({"google": gmail})
        matches = [
            i for i in out["items"] if i["message_id"] == "m_meeting_informational"
        ]
        assert len(matches) == 1


class TestWaitingOnYouSurfacing:
    def test_genuine_exchange_surfaces_as_waiting_on_you(self):
        outbound = _msg(
            "s1",
            thread_id="t1",
            sender=f"Me <{USER_EMAIL}>",
            to="alice@example.com",
            subject="Re: budget",
            body=(
                "Sure, I will take a look at the numbers and get back to "
                "you with any questions before the review."
            ),
            label_ids=["SENT"],
            age_days=10,
        )
        inbound = _msg(
            "r1",
            thread_id="t1",
            sender="Alice <alice@example.com>",
            subject="Re: budget",
            body="Thanks! Could you please confirm the numbers by Friday?",
            label_ids=["INBOX"],
            age_days=3,
        )
        gmail = _backend(outbound, inbound)
        out = build_attention_view_impl({"google": gmail})
        waiting_items = [i for i in out["items"] if i["kind"] == "waiting_on_you"]
        assert len(waiting_items) == 1
        assert waiting_items[0]["message_id"] == "r1"
        assert "waiting" in waiting_items[0]["why"]


class TestActionItems:
    def test_open_action_items_are_included_when_db_given(self):
        from gaia.database.mixin import DatabaseMixin
        from gaia_agent_email import task_store
        from gaia_agent_email.contract import ActionItem

        class _DB(DatabaseMixin):
            pass

        db = _DB()
        db.init_db(":memory:")
        task_store.init_schema(db)
        task_store.record_action_items(
            db,
            message_id="m1",
            items=[ActionItem(description="Send the Q3 report", type="text")],
        )

        gmail = _backend()
        out = build_attention_view_impl({"google": gmail}, action_db=db)
        action_items = [i for i in out["items"] if i["kind"] == "action_item"]
        assert len(action_items) == 1
        assert action_items[0]["subject"] == "Send the Q3 report"

    def test_no_db_means_no_action_items_and_no_error(self):
        gmail = _backend()
        out = build_attention_view_impl({"google": gmail}, action_db=None)
        assert [i for i in out["items"] if i["kind"] == "action_item"] == []


class TestCoverageHonesty:
    def test_scanned_and_total_unread_reported(self):
        gmail = _backend(_MEETING_IN_INFORMATIONAL, _PLAIN_UNCONFIDENT)
        out = build_attention_view_impl({"google": gmail})
        assert out["coverage"]["scanned"] == 2
        assert isinstance(out["coverage"]["total_unread"], int)
        assert out["coverage"]["degraded"] is False
        assert out["coverage"].get("mailbox_errors") is None

    def test_scan_truncated_false_when_ceiling_exactly_matches_mailbox_size(self):
        """Regression guard (#2634): hitting the ceiling is NOT the same as
        mail remaining unseen. This mailbox has exactly 1 message and the
        ceiling is 1 -- nothing was missed, so scan_truncated must be False.
        The pre-fix formula (``len(results) >= max_messages``) said True
        here for the wrong reason; FakeGmailBackend never signals a real
        next page, so this is also the only honest answer it can give.
        """
        gmail = _backend(_PLAIN_UNCONFIDENT)
        out = build_attention_view_impl({"google": gmail}, max_messages=1)
        assert out["coverage"]["scan_truncated"] is False

    def test_scan_truncated_true_when_backend_signals_more_pages(self):
        """FakeGmailBackend itself never pages (#2634's R1c keeps the
        shared fixture single-page-only), so this minimal test-local
        override reports a truthy ``nextPageToken`` on its first call to
        prove ``build_attention_view_impl`` surfaces a genuine "more mail
        exists" signal end to end, not just "did we hit the ceiling".
        """
        gmail = _MorePagesGmailBackend(user_email=USER_EMAIL)
        gmail.add_message(_PLAIN_UNCONFIDENT)
        out = build_attention_view_impl({"google": gmail}, max_messages=1)
        assert out["coverage"]["scan_truncated"] is True

    def test_generated_at_and_cache_fields_present_and_fresh(self):
        gmail = _backend()
        out = build_attention_view_impl({"google": gmail})
        assert out["generated_at"]
        assert out["cache_age_seconds"] == 0.0
        assert out["stale"] is False
        assert out["kind"] == "email_attention"


class _RaisingGmailBackend(FakeGmailBackend):
    def list_messages(self, **kwargs):
        raise ConnectorsError("token expired")


class _MorePagesGmailBackend(FakeGmailBackend):
    """Reports a truthy ``nextPageToken`` on its first ``list_messages``
    call only (#2634). ``FakeGmailBackend`` itself always returns
    ``nextPageToken: None`` -- it is shared by ~13 test files and is
    deliberately NOT taught real pagination here. Returning the token only
    once (regardless of what ``page_token`` a caller passes back) means a
    caller that never needs a second page never triggers one; a caller
    that DOES ask again gets an honestly-exhausted response instead of the
    same page forever.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._paged_once = False

    def list_messages(self, **kwargs: Any) -> Dict[str, Any]:
        out = super().list_messages(**kwargs)
        if not self._paged_once and out["messages"]:
            out = dict(out)
            out["nextPageToken"] = "more"
            self._paged_once = True
        return out


class TestPartialAndTotalMailboxFailure:
    def test_one_of_two_mailboxes_failing_is_recorded_not_fatal(self):
        good = _backend(_MEETING_IN_INFORMATIONAL)
        bad = _RaisingGmailBackend(user_email=USER_EMAIL)
        out = build_attention_view_impl({"google": good, "microsoft": bad})
        assert out["coverage"]["degraded"] is True
        mailbox_errors = out["coverage"]["mailbox_errors"]
        assert mailbox_errors and mailbox_errors[0]["mailbox"] == "microsoft"
        # The surviving mailbox's item is still present, tagged with its
        # source since more than one mailbox is connected.
        meeting_items = [i for i in out["items"] if i["kind"] == "meeting_request"]
        assert len(meeting_items) == 1
        assert meeting_items[0]["mailbox"] == "google"

    def test_every_mailbox_failing_raises_rather_than_reading_as_empty(self):
        bad1 = _RaisingGmailBackend(user_email=USER_EMAIL)
        bad2 = _RaisingGmailBackend(user_email=USER_EMAIL)
        with pytest.raises(ConnectorsError):
            build_attention_view_impl({"google": bad1, "microsoft": bad2})

    def test_single_mailbox_item_has_no_mailbox_tag(self):
        gmail = _backend(_MEETING_IN_INFORMATIONAL)
        out = build_attention_view_impl({"google": gmail})
        assert out["items"][0].get("mailbox") is None

    def test_no_backends_raises_value_error(self):
        with pytest.raises(ValueError):
            build_attention_view_impl({})


class TestZeroMutationGuarantee:
    def test_aggregator_touches_no_mutating_backend_call(self):
        gmail = _backend(_MEETING_IN_INFORMATIONAL, _PLAIN_UNCONFIDENT)
        build_attention_view_impl({"google": gmail})
        called = {method for method, _ in gmail.transport.calls}
        assert called <= _ALLOWED_BACKEND_CALLS, (
            "read-only attention aggregator called mutating backend methods: "
            f"{sorted(called - _ALLOWED_BACKEND_CALLS)}"
        )
