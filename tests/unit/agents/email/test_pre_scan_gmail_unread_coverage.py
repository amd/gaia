# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Failing acceptance tests for pre-scan's Gmail coverage honesty (#2584).

Bug: pre-scan's "how much did we actually cover" story conflates INBOX size
with UNREAD count. ``triage_inbox_impl`` (called by ``pre_scan_inbox_impl``)
lists messages with ``label_ids=["INBOX"]`` only -- never ``UNREAD`` -- so
Gmail's ``resultSizeEstimate`` on that call describes the total inbox size,
not the unread count a "what's new" pre-scan actually wants to report.

The fix adds ``UNREAD`` to the label filter and surfaces the resulting
``resultSizeEstimate`` as the new ``total_unread`` top-level field.
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


class TestGmailUnreadLabelFilter:
    def test_list_messages_called_with_unread_label_filter(self):
        gmail = _unread_inbox()

        pre_scan_inbox_impl(gmail, max_messages=25)

        list_calls = [c for c in gmail.transport.calls if c[0] == "list_messages"]
        assert list_calls, "pre-scan never called list_messages"
        label_ids_seen = list_calls[0][1].get("label_ids") or []
        assert "UNREAD" in label_ids_seen, (
            "pre-scan must filter by UNREAD (not just INBOX) so total_unread "
            f"means unread count, not inbox size; label_ids passed were "
            f"{label_ids_seen!r}"
        )


class TestTotalUnreadField:
    def test_total_unread_reflects_result_size_estimate(self):
        gmail = _unread_inbox()

        out = pre_scan_inbox_impl(gmail, max_messages=25)

        assert out["total_unread"] == _N_UNREAD_FIXTURE_MESSAGES, (
            "total_unread must reflect the backend's reported "
            f"resultSizeEstimate ({_N_UNREAD_FIXTURE_MESSAGES}); got "
            f"{out.get('total_unread')!r}"
        )
