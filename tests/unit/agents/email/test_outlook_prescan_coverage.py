# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Failing acceptance tests for Outlook's pre-scan coverage honesty (#2584).

Bug: ``LiveOutlookBackend.list_messages`` (outlook_backend.py, around line
442) reports ``"resultSizeEstimate": len(messages)`` -- the PAGE size it just
fetched, not a real mailbox total. Graph's list endpoint carries no honest
total-count field the way Gmail's ``resultSizeEstimate`` does, so fabricating
one from the page length is a lie dressed as a number. The fix reports
``None`` -- unavailable, never fabricated -- and pre-scan's ``total_unread``
must reflect that (never a fabricated per-page number) when running against
an Outlook-shaped backend.

Harness mirrors ``tests/unit/agents/email/test_outlook_backend.py``: an
``httpx.MockTransport``-backed ``LiveOutlookBackend``, no live Graph or OAuth
calls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, List, Tuple

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.outlook_backend import LiveOutlookBackend  # noqa: E402
from gaia_agent_email.tools.read_tools import pre_scan_inbox_impl  # noqa: E402


class _Recorder:
    """Records every request the backend makes, hands back canned responses."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]):
        self.requests: List[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def _backend(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Tuple[LiveOutlookBackend, _Recorder]:
    rec = _Recorder(handler)
    transport = httpx.MockTransport(rec)
    client = httpx.Client(transport=transport)
    backend = LiveOutlookBackend(lambda: "GRAPH-TOKEN-1", http_client=client)
    return backend, rec


def _ok(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _graph_message(
    *,
    msg_id: str = "m1",
    conversation_id: str = "c1",
    subject: str = "Hello",
    from_addr: str = "alice@example.com",
    is_read: bool = False,
) -> dict:
    return {
        "id": msg_id,
        "conversationId": conversation_id,
        "subject": subject,
        "from": {"emailAddress": {"name": "", "address": from_addr}},
        "toRecipients": [],
        "receivedDateTime": "2026-06-01T09:30:00Z",
        "isRead": is_read,
        "isDraft": False,
        "flag": {"flagStatus": "notFlagged"},
        "bodyPreview": subject,
        "body": {"contentType": "text", "content": subject},
        "categories": [],
        "parentFolderId": "inbox",
    }


class TestOutlookResultSizeEstimateHonesty:
    def test_list_messages_result_size_estimate_is_none_not_page_length(self):
        """resultSizeEstimate today is fabricated as len(messages) (the page
        size, not a real mailbox total -- outlook_backend.py:442). Once fixed
        it must be None: reported as unavailable, never a lie.
        """
        backend, _rec = _backend(
            lambda r: _ok(
                {
                    "value": [
                        {"id": "m1", "conversationId": "c1"},
                        {"id": "m2", "conversationId": "c2"},
                    ]
                }
            )
        )

        out = backend.list_messages(label_ids=["INBOX"], max_results=10)

        assert out["resultSizeEstimate"] is None, (
            "resultSizeEstimate must be None (honestly unavailable), not a "
            f"fabricated page-length int; got {out['resultSizeEstimate']!r}"
        )


class TestOutlookPreScanTotalUnread:
    def test_pre_scan_total_unread_is_never_a_fabricated_number(self):
        msgs = {
            "m1": _graph_message(msg_id="m1", conversation_id="c1", subject="Hi"),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/me/mailFolders/inbox/messages"):
                return _ok({"value": [{"id": "m1", "conversationId": "c1"}]})
            mid = path.rsplit("/", 1)[-1]
            return _ok(msgs[mid])

        backend, _rec = _backend(handler)

        out: Any = pre_scan_inbox_impl(backend, max_messages=25)

        assert out["total_unread"] is None, (
            "Outlook cannot report a real unread total (no honest "
            "resultSizeEstimate); pre-scan must surface None, never a "
            f"fabricated page-size number. Got {out['total_unread']!r}"
        )
