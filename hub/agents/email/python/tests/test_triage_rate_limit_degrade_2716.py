# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Message-level rate-limit degrade path (#2716 D6).

``triage_inbox_impl(..., on_rate_limit="skip")`` must not crash when a
message is rate-limited away: the two bare dict lookups this issue's review
found (``metadata_by_id[stub["id"]]`` and ``full_by_id[item["stub_id"]]``)
would otherwise raise an unhandled ``KeyError`` the instant a message is
skipped -- worse than the pre-#2716 ``ConnectorsError``, and on the primary
scan path. Only ``build_attention_view_impl`` opts into "skip"; the two
other ``triage_inbox_impl`` callers (the LLM-facing tool in ``agent.py``,
and ``pre_scan_inbox_impl``) keep the fail-loud default unmodified.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")  # noqa: E402

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    _fetch_messages,
    pre_scan_inbox_impl,
    triage_inbox_impl,
)

from gaia.connectors.errors import RateLimitedError  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

USER_EMAIL = "user@example.com"


def _msg(msg_id: str, *, subject: str = "Random note") -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": "Just circling back on something.",
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


class _OneShotRateLimitedBackend(FakeGmailBackend):
    """Rate-limits exactly the ids in ``fail_ids`` on ``get_messages_batch``,
    once, then behaves normally -- lets a test target a specific phase
    (metadata vs. full-body) without the whole-chunk-ceiling fixture."""

    def __init__(self, *args: Any, fail_ids: Iterable[str] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._fail_ids = set(fail_ids)
        self._already_failed: set = set()

    def get_messages_batch(
        self, message_ids: Iterable[str], *, format: str = "full"
    ) -> Dict[str, Dict[str, Any]]:
        ids = list(message_ids)
        to_fail = (self._fail_ids - self._already_failed) & set(ids)
        if to_fail:
            self._already_failed |= to_fail
            succeeded = {
                mid: super(_OneShotRateLimitedBackend, self).get_messages_batch(
                    [mid], format=format
                )[mid]
                for mid in ids
                if mid not in to_fail
            }
            raise RateLimitedError(
                "google",
                message_ids=sorted(to_fail),
                partial_results=succeeded,
                message=f"rate-limited {sorted(to_fail)}; try again in a minute",
            )
        return super().get_messages_batch(ids, format=format)


class TestFetchMessagesSkipMode:
    def test_raise_mode_propagates_rate_limited_error(self):
        gmail = _OneShotRateLimitedBackend(user_email=USER_EMAIL, fail_ids=["m1"])
        gmail.add_message(_msg("m1"))
        gmail.add_message(_msg("m2"))
        with pytest.raises(RateLimitedError):
            _fetch_messages(gmail, ["m1", "m2"], format="metadata")

    def test_skip_mode_returns_dropped_ids_and_surviving_messages(self):
        gmail = _OneShotRateLimitedBackend(user_email=USER_EMAIL, fail_ids=["m1"])
        gmail.add_message(_msg("m1"))
        gmail.add_message(_msg("m2"))
        out, dropped = _fetch_messages(
            gmail, ["m1", "m2"], format="metadata", on_rate_limit="skip"
        )
        assert dropped == ["m1"]
        assert set(out) == {"m2"}

    def test_skip_mode_still_raises_for_a_genuinely_missing_id(self):
        """Known-skipped != missing (D6.3) -- a real backend bug (an id
        that's neither fetched nor reported dropped) must still raise."""

        class _SilentlyDropsIds(FakeGmailBackend):
            def get_messages_batch(self, message_ids, *, format="full"):
                out = super().get_messages_batch(message_ids, format=format)
                out.pop(next(iter(out)), None)
                return out

        gmail = _SilentlyDropsIds(user_email=USER_EMAIL)
        gmail.add_message(_msg("m1"))
        gmail.add_message(_msg("m2"))
        with pytest.raises(RuntimeError, match="missing"):
            _fetch_messages(
                gmail, ["m1", "m2"], format="metadata", on_rate_limit="skip"
            )


class TestTriageInboxImplDegrade:
    def test_default_raise_mode_unchanged(self):
        gmail = _OneShotRateLimitedBackend(user_email=USER_EMAIL, fail_ids=["m1"])
        gmail.add_message(_msg("m1"))
        gmail.add_message(_msg("m2"))
        with pytest.raises(RateLimitedError):
            triage_inbox_impl(gmail, max_messages=2)

    def test_skip_mode_drops_the_rate_limited_message_not_the_scan(self):
        gmail = _OneShotRateLimitedBackend(user_email=USER_EMAIL, fail_ids=["m1"])
        gmail.add_message(_msg("m1"))
        gmail.add_message(_msg("m2"))
        result = triage_inbox_impl(gmail, max_messages=2, on_rate_limit="skip")
        ids = {r["id"] for r in result["results"]}
        assert ids == {"m2"}
        assert result["dropped_ids"] == ["m1"]

    def test_pre_scan_inbox_impl_keeps_the_raise_default(self):
        """D6.5 -- pre_scan_inbox_impl is NOT one of the callers that opts
        into degradation; it must still raise unmodified."""
        gmail = _OneShotRateLimitedBackend(user_email=USER_EMAIL, fail_ids=["m1"])
        gmail.add_message(_msg("m1"))
        gmail.add_message(_msg("m2"))
        with pytest.raises(RateLimitedError):
            pre_scan_inbox_impl(gmail, max_messages=2)
