# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for #3556: Outlook messages must carry a real ``internalDate``.

Graph's ``receivedDateTime`` is ISO-8601, but every consumer of the Gmail-shaped
dict reads ``internalDate`` as epoch milliseconds and falls back to 0 on
anything else. On Outlook that collapsed every age to "none", made the
newest-message-wins reply target arbitrary, and flattened the recency
tie-break in the needs-review ordering. The Outlook backend now normalises the
timestamp at its boundary; these tests feed Outlook-shaped messages through the
consumers that read it.
"""

from __future__ import annotations

import httpx
import pytest
from gaia_agent_email.outlook_backend import (
    LiveOutlookBackend,
    graph_message_to_gmail,
)
from gaia_agent_email.timestamps import parse_timestamp_ms
from gaia_agent_email.tools import read_tools, reply_tools

# 2026-07-16T12:00:00Z and 2026-07-15T09:00:00Z
NEWER_MS = 1784203200000
OLDER_MS = 1784106000000


def _graph(message_id: str, received: str) -> dict:
    return {
        "id": message_id,
        "conversationId": "c1",
        "receivedDateTime": received,
        "subject": "s",
    }


def _backend(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="")
    return LiveOutlookBackend(lambda: "fake-token", http_client=client)


@pytest.mark.parametrize(
    "received, expected",
    [
        ("2026-07-16T12:00:00Z", NEWER_MS),
        ("2026-07-15T09:00:00Z", OLDER_MS),
        # Graph can send 7-digit (100 ns) fractions, which py3.10 rejects.
        ("2026-07-16T12:00:00.1234567Z", NEWER_MS + 123),
        ("2026-07-16T12:00:00+00:00", NEWER_MS),
        ("1784203200000", NEWER_MS),
    ],
)
def test_parse_timestamp_ms(received, expected):
    assert parse_timestamp_ms(received) == expected


def test_parse_timestamp_ms_rejects_garbage():
    with pytest.raises(ValueError):
        parse_timestamp_ms("yesterday")


def test_translation_emits_epoch_millis_internal_date():
    msg = graph_message_to_gmail(_graph("m1", "2026-07-16T12:00:00Z"))

    assert msg["internalDate"] == str(NEWER_MS)


def test_translation_without_a_timestamp_leaves_internal_date_empty():
    msg = graph_message_to_gmail({"id": "m1", "conversationId": "c1"})

    assert msg["internalDate"] == ""


def test_get_thread_orders_oldest_first_by_the_normalised_timestamp():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    _graph("newer", "2026-07-16T12:00:00Z"),
                    _graph("older", "2026-07-15T09:00:00Z"),
                ]
            },
        )

    thread = _backend(handler).get_thread("c1")

    assert [m["id"] for m in thread["messages"]] == ["older", "newer"]
    assert [m["internalDate"] for m in thread["messages"]] == [
        str(OLDER_MS),
        str(NEWER_MS),
    ]


def test_thread_sort_key_distinguishes_outlook_messages():
    newer = graph_message_to_gmail(_graph("newer", "2026-07-16T12:00:00Z"))
    older = graph_message_to_gmail(_graph("older", "2026-07-15T09:00:00Z"))

    assert read_tools._thread_message_sort_key(newer) == NEWER_MS
    assert read_tools._thread_message_sort_key(older) == OLDER_MS


def test_parse_epoch_millis_reads_an_outlook_message():
    msg = graph_message_to_gmail(_graph("m1", "2026-07-16T12:00:00Z"))

    assert read_tools._parse_epoch_millis(msg["internalDate"]) == NEWER_MS


def test_reply_target_ordering_key_is_not_zero_for_outlook():
    msg = graph_message_to_gmail(_graph("m1", "2026-07-16T12:00:00Z"))

    assert reply_tools._internal_ms(msg) == NEWER_MS


def test_reply_latency_is_computed_for_an_outlook_message():
    msg = graph_message_to_gmail(_graph("m1", "2026-07-16T12:00:00.1234567Z"))

    latency = reply_tools._compute_reply_latency_seconds(msg)

    assert latency is not None
