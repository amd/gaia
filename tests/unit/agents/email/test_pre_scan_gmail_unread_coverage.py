# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Acceptance tests for pre-scan's Gmail coverage honesty (#2584, revised #2638).

Two bugs, one field -- #2584's original fix, #2638 revised the first:

1. #2584: ``triage_inbox_impl`` (called by ``pre_scan_inbox_impl``) used to
   list messages with ``label_ids=["INBOX"]`` only -- never ``UNREAD`` -- so
   whatever coverage number came off that listing described total inbox
   size, not the unread count a "what's new" pre-scan actually wanted to
   report. #2584 added ``UNREAD`` to the scanning query's label filter.

   #2638 REVERSED this specific piece: narrowing to unread-only made the
   single highest-value triage bucket (read-but-unanswered mail) invisible,
   and the rationale for the narrowing (below) had already been made moot by
   point 2 -- so pre-scan's query is back to plain ``["INBOX"]`` (read +
   unread), matching the attention view and waiting-on-you, which never
   added the UNREAD narrowing in the first place.

2. ``total_unread`` was originally sourced from that same listing call's
   ``resultSizeEstimate``. Measured against a real mailbox, that field
   reported 201 while full pagination of the identical query found 523 real
   message ids -- Google documents ``resultSizeEstimate`` as approximate,
   and 2.6x off is not an honest scan-coverage denominator. Fixed by
   sourcing ``total_unread`` from Gmail's ``labels().get(id="INBOX")``
   instead, whose ``messagesUnread`` is an exact integer -- one call per
   scan, not per message, never derived from ``list_messages`` at all. This
   part of #2584 is UNCHANGED by #2638 -- only the listing query's label
   filter reversed; the get_label-based count sourcing (this class) did not.
   #2638 additionally adds ``total_inbox`` (``messagesTotal``, same call) as
   the honest denominator now that the scan itself isn't unread-only; see
   ``test_prescan_read_mail_2638.py`` for that field's own tests.
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
from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str, *, subject: str, sender: str, label_ids: List[str]
) -> Dict[str, Any]:
    body = "Body text for the fixture message."
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": list(label_ids),
        "snippet": body[:120],
        "internalDate": "1700000000000",
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


# All 6 fixture messages carry BOTH INBOX and UNREAD, so the assertions below
# hold regardless of which exact label combination the fix ends up passing
# (INBOX+UNREAD or UNREAD alone) -- what matters is that UNREAD is present,
# and that the resulting count is the distinctive, assertable value 6.
_N_UNREAD_FIXTURE_MESSAGES = 6


def _unread_inbox(n: int = _N_UNREAD_FIXTURE_MESSAGES) -> FakeGmailBackend:
    gmail = FakeGmailBackend()
    for i in range(n):
        gmail.add_message(
            _msg(
                f"m{i}",
                subject=f"msg {i}",
                sender="a@example.com",
                label_ids=["INBOX", "UNREAD"],
            )
        )
    return gmail


class TestGmailInboxLabelFilterCoversReadMail:
    def test_list_messages_called_without_an_unread_label_filter(self):
        """#2638 reversed #2584's UNREAD narrowing: pre-scan now queries
        plain INBOX so read-but-unanswered mail is scanned too, matching
        the attention view and waiting-on-you (neither ever narrowed to
        UNREAD)."""
        gmail = _unread_inbox()

        pre_scan_inbox_impl(gmail, max_messages=25)

        list_calls = [c for c in gmail.transport.calls if c[0] == "list_messages"]
        assert list_calls, "pre-scan never called list_messages"
        label_ids_seen = list_calls[0][1].get("label_ids") or []
        assert "UNREAD" not in label_ids_seen, (
            "pre-scan must NOT filter by UNREAD (#2638) -- narrowing the "
            "query makes read-but-unanswered mail permanently invisible; "
            f"label_ids passed were {label_ids_seen!r}"
        )
        assert "INBOX" in label_ids_seen


class TestTotalUnreadField:
    def test_total_unread_is_sourced_from_get_label_not_list_messages(self):
        gmail = _unread_inbox()

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        label_calls = [c for c in gmail.transport.calls if c[0] == "get_label"]
        assert label_calls, (
            "pre-scan must call get_label to source total_unread -- "
            "list_messages's resultSizeEstimate is documented as "
            "approximate and measured 2.6x off on a real mailbox (#2584)"
        )
        assert label_calls[0][1].get("label_id") == "INBOX"
        assert out["total_unread"] == _N_UNREAD_FIXTURE_MESSAGES, (
            "total_unread must reflect the exact labels().get(INBOX) "
            f"messagesUnread count ({_N_UNREAD_FIXTURE_MESSAGES}); got "
            f"{out.get('total_unread')!r}"
        )

    def test_total_unread_never_calls_list_labels(self):
        """``list_labels()`` returns Gmail's MINIMAL label form (no counts)
        -- it must be ``get_label``, not ``list_labels``, or total_unread
        would silently have no source at all.
        """
        gmail = _unread_inbox()

        pre_scan_inbox_impl(gmail, max_messages=25)

        list_labels_calls = [c for c in gmail.transport.calls if c[0] == "list_labels"]
        assert not list_labels_calls, (
            "pre-scan must not call list_labels for total_unread -- that "
            "returns the minimal label form with no messagesUnread count"
        )
