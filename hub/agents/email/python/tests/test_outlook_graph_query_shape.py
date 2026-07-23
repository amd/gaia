# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests pinning the Microsoft Graph query shapes ``LiveOutlookBackend``
sends, so follow-up scans never re-trip the ``InefficientFilter`` error (#2138).

``get_thread`` must NOT combine ``$filter=conversationId`` with an
``$orderby`` — Graph rejects that pairing (the two properties aren't
co-indexed) with ``InefficientFilter``. It filters server-side and sorts the
returned messages client-side instead.
"""

from __future__ import annotations

import httpx
import pytest
from gaia_agent_email.outlook_backend import (
    LiveOutlookBackend,
    graph_message_to_gmail,
)


def _backend(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="")
    return LiveOutlookBackend(lambda: "fake-token", http_client=client)


def test_get_thread_query_omits_orderby():
    """get_thread must filter on conversationId without an $orderby (the
    combination Graph rejects with InefficientFilter)."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m2",
                        "conversationId": "c1",
                        "receivedDateTime": "2026-07-16T12:00:00Z",
                    },
                    {
                        "id": "m1",
                        "conversationId": "c1",
                        "receivedDateTime": "2026-07-15T09:00:00Z",
                    },
                ]
            },
        )

    backend = _backend(handler)
    backend.get_thread("c1")

    params = captured["params"]
    assert params.get("$filter") == "conversationId eq 'c1'"
    assert "$orderby" not in params, (
        "get_thread must not pair $filter=conversationId with $orderby — Graph "
        "rejects it with InefficientFilter (#2138)"
    )


def test_get_thread_sorts_messages_ascending_client_side():
    """With $orderby dropped, get_thread sorts by receivedDateTime itself so
    downstream thread analysis still sees oldest-first ordering."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "newer",
                        "conversationId": "c1",
                        "receivedDateTime": "2026-07-16T12:00:00Z",
                    },
                    {
                        "id": "older",
                        "conversationId": "c1",
                        "receivedDateTime": "2026-07-15T09:00:00Z",
                    },
                ]
            },
        )

    backend = _backend(handler)
    thread = backend.get_thread("c1")

    ids = [m["id"] for m in thread["messages"]]
    assert ids == ["older", "newer"]


@pytest.mark.parametrize(
    "importance, expect_important",
    [
        ("high", True),
        ("High", True),
        ("normal", False),
        ("low", False),
        (None, False),
    ],
)
def test_graph_high_importance_maps_to_important_label(importance, expect_important):
    """#2426 (AC-1, Outlook equivalent): a Graph high-importance message carries
    the ``IMPORTANT`` label the auto-archive guard keys off; normal/low/absent
    importance does not."""
    msg = {
        "id": "m1",
        "conversationId": "c1",
        "isRead": True,
        "receivedDateTime": "2026-07-16T12:00:00Z",
    }
    if importance is not None:
        msg["importance"] = importance
    label_ids = graph_message_to_gmail(msg)["labelIds"]
    assert ("IMPORTANT" in label_ids) is expect_important


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
