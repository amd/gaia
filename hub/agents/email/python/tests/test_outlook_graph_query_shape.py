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

from gaia_agent_email.outlook_backend import LiveOutlookBackend


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
