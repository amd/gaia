# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``get_thread`` chronology / dedupe tests for #2531.

Reproduces the reported defect: a real 8-message, 2-participant alternating
thread came back from the agent with the right message COUNT (8) but the
wrong CONTENTS — a 4/2/2 sender split instead of the true 4/4, and the last
two entries (98 seconds apart) inverted. A total-count-only assertion is
exactly what let this ship, so every test here asserts per-sender counts and
distinct message ids, never just ``len(...)``.

Layer isolation (see the PR description for the full raw-vs-formatted
writeup): these tests exercise ``get_thread_impl`` directly against
``FakeGmailBackend`` — the TOOL/assembly layer, with no LLM involved. They
prove two things about that layer, pre-fix:

1. Given a well-behaved backend, ``get_thread_impl``'s assembly code does
   NOT structurally drop or duplicate messages — every distinct id fetched
   from the backend is formatted exactly once. So the reported 4/2/2 split
   is not explained by a dedupe/drop bug at this layer.
2. ``get_thread_impl`` documented and tested "backend order preserved (no
   sort)" — inconsistent with Gmail's own documented non-guarantee of
   thread-message order, and inconsistent with this same file's established
   defensive pattern for ``summarize_thread`` (``_thread_message_sort_key``).
   A misordered backend response reproduces the reported ordering inversion
   at THIS layer, independent of any LLM. That is the proven, fixed bug.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import get_thread_impl  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    thread_id: str,
    sender: str,
    internal_date_ms: int,
    date_header: str,
    subject: str = "Contributing to GAIA",
) -> Dict[str, Any]:
    body = f"body of {msg_id}"
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": ["INBOX"],
        "snippet": body[:200],
        "internalDate": str(internal_date_ms),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "user@example.com"},
                {"name": "Date", "value": date_header},
            ],
            "body": {"data": _b64url(body), "size": len(body)},
        },
        "sizeEstimate": len(body),
    }


# One evening, 8 messages, strictly alternating between two participants.
# Chronological (true) order is m1..m8. The last pair is 98 seconds apart —
# the exact gap the real reproduction observed inverted (18:53:03 / 18:54:41).
_ALICE = "alice@example.com"
_BOB = "bob@example.org"
_THREAD_ID = "thread-contributing-to-gaia"

_BASE_MS = 1_800_000_000_000  # arbitrary fixed epoch-millis anchor
_TRUE_ORDER: List[Dict[str, Any]] = [
    _msg(
        f"m{i}",
        thread_id=_THREAD_ID,
        sender=_ALICE if i % 2 == 1 else _BOB,
        internal_date_ms=_BASE_MS + offset_ms,
        date_header=date_header,
    )
    for i, (offset_ms, date_header) in enumerate(
        [
            (0, "Mon, 27 Jul 2026 18:00:00 -0700"),
            (10 * 60_000, "Mon, 27 Jul 2026 18:10:00 -0700"),
            (25 * 60_000, "Mon, 27 Jul 2026 18:25:00 -0700"),
            (40 * 60_000, "Mon, 27 Jul 2026 18:40:00 -0700"),
            (48 * 60_000, "Mon, 27 Jul 2026 18:48:00 -0700"),
            (51 * 60_000, "Mon, 27 Jul 2026 18:51:00 -0700"),
            (53 * 60_000 + 3_000, "Mon, 27 Jul 2026 18:53:03 -0700"),
            (54 * 60_000 + 41_000, "Mon, 27 Jul 2026 18:54:41 -0700"),
        ],
        start=1,
    )
]


def _build_backend(insertion_order: List[Dict[str, Any]]) -> FakeGmailBackend:
    """Seed a FakeGmailBackend, inserted in ``insertion_order``.

    ``FakeGmailBackend.get_thread`` returns messages in dict-insertion order
    (no sort of its own — see ``fake_gmail.py``), so this directly controls
    what "raw backend order" ``get_thread_impl`` sees, letting us simulate
    Gmail's own documented non-guarantee of in-order thread results.
    """
    gmail = FakeGmailBackend(user_email="user@example.com")
    for msg in insertion_order:
        gmail.add_message(msg)
    return gmail


class TestSenderDistributionAndDedup:
    """Per-sender counts and distinct ids — the assertion shape the issue
    says a total-count-only test would have missed."""

    def test_8_message_thread_returns_all_distinct_ids_true_4_4_split(self):
        gmail = _build_backend(_TRUE_ORDER)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)
        messages = result["messages"]

        assert len(messages) == 8
        ids = [m["id"] for m in messages]
        assert len(set(ids)) == 8, f"duplicate message ids in result: {ids}"

        senders = [m["from"] for m in messages]
        assert senders.count(_ALICE) == 4
        assert senders.count(_BOB) == 4

    def test_no_duplicated_message_ids_even_when_backend_order_is_scrambled(self):
        scrambled = [_TRUE_ORDER[i] for i in (2, 0, 4, 1, 7, 3, 6, 5)]
        gmail = _build_backend(scrambled)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)
        ids = [m["id"] for m in result["messages"]]
        assert sorted(ids) == [f"m{i}" for i in range(1, 9)]
        assert len(set(ids)) == 8


class TestChronologicalOrdering:
    """Ordering must be correct even when the backend hands messages back
    out of order — the exact failure mode Gmail's own API docs warn about
    and that ``_thread_message_sort_key`` already defends against for
    ``summarize_thread``."""

    def test_strict_chronological_order_when_backend_is_well_ordered(self):
        gmail = _build_backend(_TRUE_ORDER)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)
        ids = [m["id"] for m in result["messages"]]
        assert ids == [f"m{i}" for i in range(1, 9)]

    def test_last_two_close_messages_are_not_inverted_when_backend_inverts_them(self):
        """Reproduces the reported defect directly: backend returns the last
        two messages (98s apart) in reverse order; the tool must still
        present them chronologically.
        """
        insertion_order = _TRUE_ORDER[:6] + [_TRUE_ORDER[7], _TRUE_ORDER[6]]
        gmail = _build_backend(insertion_order)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)
        ids = [m["id"] for m in result["messages"]]
        assert ids == [f"m{i}" for i in range(1, 9)], (
            "get_thread_impl must sort defensively — a misordered backend "
            "must not leak an out-of-order thread to the caller"
        )
        # The specific close pair the real run inverted.
        assert ids.index("m7") < ids.index("m8")

    def test_fully_reversed_backend_order_is_corrected(self):
        gmail = _build_backend(list(reversed(_TRUE_ORDER)))
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)
        ids = [m["id"] for m in result["messages"]]
        assert ids == [f"m{i}" for i in range(1, 9)]


class TestSingleMessageThreadGuard:
    """Guard against over-correcting: a genuinely single-message thread
    (e.g. a newsletter) must still return exactly that one message."""

    def test_single_message_thread_returns_exactly_one_message(self):
        solo = _msg(
            "solo1",
            thread_id="solo-thread",
            sender="newsletter@example.com",
            internal_date_ms=_BASE_MS,
            date_header="Mon, 27 Jul 2026 09:00:00 -0700",
        )
        gmail = _build_backend([solo])
        result = get_thread_impl(gmail, thread_id="solo-thread")
        assert len(result["messages"]) == 1
        assert result["messages"][0]["id"] == "solo1"
