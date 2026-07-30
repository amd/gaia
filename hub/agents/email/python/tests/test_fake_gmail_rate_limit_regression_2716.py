# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
FakeGmailBackend rate-limit regression coverage (#2716 Correction 2 & 3).

The wire-level tests in ``test_gmail_batch_429_retry_2716.py`` prove
``LiveGmailBackend`` retries a real Gmail 429 correctly, but
``FakeGmailBackend`` never routed through that code and had no way to
represent a 429 at all -- a mock proving "we called it", never "the call is
valid" (CLAUDE.md's #1655 rule). That gap is exactly how the original
100-subrequest batch shipped: nothing in CI could catch a batch size that
would 429 against real Gmail.

``FakeGmailBackend`` now accepts an optional ``rate_limit_subrequest_ceiling``
that makes ``get_messages_batch`` raise the same ``RateLimitedError`` shape a
real oversized batch would. Set to a Gmail-like safe value, a full scan must
still complete with ZERO 429s -- proving the production chunk size actually
stays under it. If ``_BATCH_MAX_SUBREQUESTS`` ever regresses upward, this is
the test that catches it, independent of the two-constants-must-match check
in ``test_gmail_batch_429_retry_2716.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

from gaia_agent_email.gmail_backend import _BATCH_MAX_SUBREQUESTS  # noqa: E402
from gaia_agent_email.tools.attention_tools import (  # noqa: E402
    build_attention_view_impl,
)
from gaia_agent_email.tools.read_tools import triage_inbox_impl  # noqa: E402

from gaia.connectors.errors import RateLimitedError  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

USER_EMAIL = "user@example.com"


def _msg(msg_id: str, *, subject: str = "Newsletter") -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": "Nothing to see here.",
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": USER_EMAIL},
            ],
            "body": {"size": 0, "data": ""},
        },
        "sizeEstimate": 0,
    }


def _mailbox_of(n: int, ceiling: Optional[int]) -> FakeGmailBackend:
    gmail = FakeGmailBackend(
        user_email=USER_EMAIL, rate_limit_subrequest_ceiling=ceiling
    )
    for i in range(n):
        gmail.add_message(_msg(f"m{i}"))
    return gmail


class TestFakeBackendRaisesRealErrorShape:
    def test_batch_over_ceiling_raises_rate_limited_error(self):
        gmail = _mailbox_of(5, ceiling=2)
        with pytest.raises(RateLimitedError) as exc:
            gmail.get_messages_batch([f"m{i}" for i in range(5)])
        assert exc.value.provider == "google"
        assert exc.value.message_ids
        assert (
            "retry" in str(exc.value).lower() or "try again" in str(exc.value).lower()
        )

    def test_batch_at_or_under_ceiling_succeeds(self):
        gmail = _mailbox_of(2, ceiling=2)
        out = gmail.get_messages_batch(["m0", "m1"])
        assert set(out) == {"m0", "m1"}


class TestFullScanStaysUnderGmailLikeCeiling:
    """Correction 2 -- the regression guard: with the ceiling pinned at a
    real Gmail-safe value, independent of whatever _BATCH_MAX_SUBREQUESTS is
    set to, a full scan must complete with zero 429s."""

    GMAIL_LIKE_CEILING = 25

    def test_triage_scan_completes(self):
        n = 60  # forces multiple chunks at the current 25-cap
        gmail = _mailbox_of(n, ceiling=self.GMAIL_LIKE_CEILING)
        result = triage_inbox_impl(gmail, max_messages=n)
        assert len(result["results"]) == n

    def test_attention_scan_completes(self):
        n = 60
        gmail = _mailbox_of(n, ceiling=self.GMAIL_LIKE_CEILING)
        out = build_attention_view_impl({"google": gmail}, max_messages=n)
        assert out["coverage"]["scanned"] == n
        assert out["coverage"]["degraded"] is False


class TestChainedScansSucceed:
    """Correction 3 (#2720 AC-6) -- an attention-view scan followed
    immediately by a triage run must both succeed; nothing about the first
    scan should leave the backend, transport, or retry state such that the
    second scan fails."""

    def test_attention_scan_then_triage_scan_both_succeed(self):
        n = 40
        gmail = _mailbox_of(n, ceiling=_BATCH_MAX_SUBREQUESTS)

        attention = build_attention_view_impl({"google": gmail}, max_messages=n)
        assert attention["coverage"]["degraded"] is False

        triage = triage_inbox_impl(gmail, max_messages=n)
        assert len(triage["results"]) == n
