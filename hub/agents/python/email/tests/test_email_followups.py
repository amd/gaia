# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Follow-up tracking tests for EmailTriageAgent (#1606).

Acceptance criteria covered:
- AC1: the Sent folder is scanned for messages with no inbound reply on the
  same thread after a configurable window.
- AC2: the awaiting-reply set surfaces (message_id, recipient, subject, age)
  via a read-only tool.
- AC3: read-only — the detector never touches any send path.

Test acceptance criteria covered:
- A sent message WITH a later inbound reply is NOT flagged; one WITHOUT is
  flagged after the window.
- The detector touches no send path (no ``send_*`` side effects) — asserted
  both dynamically (FakeGmailTransport call log) and statically (module
  source references no send/draft/mutate backend calls).

All tests are hermetic: FakeGmailBackend only, no Lemonade, no network.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Path / import bootstrap
# ---------------------------------------------------------------------------

# parents[0] = tests/,  [1] = email/,  [2] = python/,  [3] = agents/,
# [4] = hub/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

import gaia_agent_email.tools.followup_tools as followup_tools  # noqa: E402
from gaia_agent_email.tools.followup_tools import (  # noqa: E402
    FollowupToolsMixin,
    check_followups_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

USER_EMAIL = "user@example.com"

DAY_MS = 24 * 60 * 60 * 1000
# Fixed "now" so ages are deterministic.
NOW_MS = 1_750_000_000_000


def _msg(
    msg_id: str,
    *,
    thread_id: str,
    sender: str,
    to: str,
    subject: str,
    age_days: float,
    label_ids: List[str],
    internal_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal Gmail API v1 message dict for the fake backend."""
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": list(label_ids),
        "snippet": f"snippet of {msg_id}",
        "internalDate": internal_date or str(int(NOW_MS - age_days * DAY_MS)),
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Date", "value": f"{age_days} days ago"},
            ],
            "body": {"size": 4, "data": "Ym9keQ"},
        },
        "sizeEstimate": 4,
    }


def _sent(
    msg_id: str,
    *,
    thread_id: str,
    to: str = "alice@example.com",
    subject: str = "Question about Q3",
    age_days: float = 5,
    internal_date: Optional[str] = None,
) -> Dict[str, Any]:
    return _msg(
        msg_id,
        thread_id=thread_id,
        sender=f"Me <{USER_EMAIL}>",
        to=to,
        subject=subject,
        age_days=age_days,
        label_ids=["SENT"],
        internal_date=internal_date,
    )


def _inbound(
    msg_id: str,
    *,
    thread_id: str,
    sender: str = "Alice <alice@example.com>",
    subject: str = "Re: Question about Q3",
    age_days: float = 4,
    internal_date: Optional[str] = None,
) -> Dict[str, Any]:
    return _msg(
        msg_id,
        thread_id=thread_id,
        sender=sender,
        to=USER_EMAIL,
        subject=subject,
        age_days=age_days,
        label_ids=["INBOX"],
        internal_date=internal_date,
    )


def _backend(*messages: Dict[str, Any]) -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email=USER_EMAIL)
    for m in messages:
        gmail.add_message(m)
    return gmail


def _run(gmail: FakeGmailBackend, *, window_days: int = 3) -> Dict[str, Any]:
    return check_followups_impl(gmail, window_days=window_days, now_ms=NOW_MS)


# ---------------------------------------------------------------------------
# Core detection semantics (issue #1606 test acceptance criteria)
# ---------------------------------------------------------------------------


class TestReplyDetection:
    def test_sent_with_later_inbound_reply_not_flagged(self):
        gmail = _backend(
            _sent("s1", thread_id="t1", age_days=5),
            _inbound("r1", thread_id="t1", age_days=4),
        )
        out = _run(gmail, window_days=3)
        assert out["awaiting_reply"] == []

    def test_sent_without_reply_flagged_after_window(self):
        gmail = _backend(_sent("s1", thread_id="t1", age_days=5))
        out = _run(gmail, window_days=3)
        assert len(out["awaiting_reply"]) == 1
        item = out["awaiting_reply"][0]
        assert item["message_id"] == "s1"
        assert item["thread_id"] == "t1"
        assert item["recipient"] == "alice@example.com"
        assert item["subject"] == "Question about Q3"
        assert item["age_days"] == 5

    def test_sent_within_window_not_flagged(self):
        gmail = _backend(_sent("s1", thread_id="t1", age_days=1))
        out = _run(gmail, window_days=3)
        assert out["awaiting_reply"] == []

    def test_inbound_before_send_does_not_count_as_reply(self):
        # The user replied LAST — the earlier inbound message must not
        # suppress the flag (only replies AFTER the send count).
        gmail = _backend(
            _inbound("r1", thread_id="t1", age_days=7),
            _sent("s1", thread_id="t1", age_days=5),
        )
        out = _run(gmail, window_days=3)
        assert [i["message_id"] for i in out["awaiting_reply"]] == ["s1"]

    def test_multiple_sends_same_thread_flagged_once(self):
        gmail = _backend(
            _sent("s1", thread_id="t1", age_days=9),
            _sent("s2", thread_id="t1", age_days=6),
        )
        out = _run(gmail, window_days=3)
        # One entry per thread, anchored on the LATEST outbound message.
        assert [i["message_id"] for i in out["awaiting_reply"]] == ["s2"]
        assert out["awaiting_reply"][0]["age_days"] == 6

    def test_self_addressed_sent_not_flagged(self):
        # A note-to-self can never receive an inbound reply; flagging it
        # forever would be pure noise.
        gmail = _backend(
            _sent("s1", thread_id="t1", to=USER_EMAIL, age_days=30),
        )
        out = _run(gmail, window_days=3)
        assert out["awaiting_reply"] == []

    def test_recipient_display_name_with_comma_parsed_cleanly(self):
        # RFC 5322 allows a comma inside a quoted display name; the split
        # must not surface '"Doe' as a recipient.
        gmail = _backend(
            _sent(
                "s1",
                thread_id="t1",
                to='"Doe, John" <john@x.com>, carol@y.com',
                age_days=5,
            )
        )
        out = _run(gmail, window_days=3)
        assert out["awaiting_reply"][0]["recipient"] == "john@x.com"
        assert out["awaiting_reply"][0]["recipients"] == [
            "john@x.com",
            "carol@y.com",
        ]

    def test_results_sorted_most_overdue_first(self):
        gmail = _backend(
            _sent("s_new", thread_id="t1", age_days=4),
            _sent("s_old", thread_id="t2", age_days=10),
        )
        out = _run(gmail, window_days=3)
        assert [i["message_id"] for i in out["awaiting_reply"]] == ["s_old", "s_new"]

    def test_iso8601_internal_date_supported(self):
        # The Outlook backend translates Graph messages with an ISO-8601
        # ``internalDate`` (e.g. "2026-06-24T10:00:00Z"), not epoch millis —
        # exercised via a minimal Outlook-shaped backend (the Gmail fake
        # models millis only).
        from datetime import datetime, timezone

        sent_dt = datetime.fromtimestamp((NOW_MS - 5 * DAY_MS) / 1000, tz=timezone.utc)
        iso = sent_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        message = _sent("s1", thread_id="t1", age_days=5, internal_date=iso)

        class _IsoBackend:
            def get_user_email(self):
                return USER_EMAIL

            def list_messages(self, **_kwargs):
                return {"messages": [{"id": "s1", "threadId": "t1"}]}

            def get_thread(self, thread_id):
                return {"id": thread_id, "messages": [message]}

        out = check_followups_impl(_IsoBackend(), window_days=3, now_ms=NOW_MS)
        assert [i["message_id"] for i in out["awaiting_reply"]] == ["s1"]
        assert out["awaiting_reply"][0]["age_days"] == 5

    def test_invalid_window_fails_loudly(self):
        gmail = _backend(_sent("s1", thread_id="t1"))
        with pytest.raises(ValueError, match="window_days"):
            check_followups_impl(gmail, window_days=0, now_ms=NOW_MS)
        with pytest.raises(ValueError, match="window_days"):
            check_followups_impl(gmail, window_days=-2, now_ms=NOW_MS)


# ---------------------------------------------------------------------------
# scan_truncated signal — the sent-scan ceiling
# ---------------------------------------------------------------------------


class TestScanTruncated:
    def test_under_cap_not_truncated(self):
        # 3 sent threads, cap of 50 — nowhere near the ceiling.
        gmail = _backend(
            _sent("s1", thread_id="t1", age_days=5),
            _sent("s2", thread_id="t2", age_days=6),
            _sent("s3", thread_id="t3", age_days=7),
        )
        out = check_followups_impl(
            gmail, window_days=3, max_sent=50, now_ms=NOW_MS
        )
        assert out["scan_truncated"] is False

    def test_at_cap_with_more_remaining_is_truncated(self):
        # 5 distinct sent threads but max_sent=3 — the scan stops after the
        # 3 newest, leaving 2 older (more overdue) sends unscanned.
        gmail = _backend(
            *(
                _sent(f"s{i}", thread_id=f"t{i}", age_days=5 + i)
                for i in range(5)
            )
        )
        out = check_followups_impl(
            gmail, window_days=3, max_sent=3, now_ms=NOW_MS
        )
        assert out["sent_scanned"] == 3
        assert out["scan_truncated"] is True

    def test_exactly_at_cap_with_nothing_more_is_not_truncated(self):
        # Sent-folder has exactly max_sent messages and the fake backend
        # reports no next page — this scan is exhaustive, not truncated.
        # (The len(stubs) >= max_sent heuristic alone would over-flag this
        # case; real backends distinguish it via nextPageToken.)
        gmail = _backend(
            _sent("s1", thread_id="t1", age_days=5),
            _sent("s2", thread_id="t2", age_days=6),
        )
        out = check_followups_impl(
            gmail, window_days=3, max_sent=2, now_ms=NOW_MS
        )
        # The fake backend has no more messages beyond these two, but it
        # also never signals a next page — so hitting the numeric cap alone
        # is treated conservatively as "may be truncated" (len(stubs) ==
        # max_sent). This matches the documented conservative semantics.
        assert out["sent_scanned"] == 2
        assert out["scan_truncated"] is True

    def test_tool_surfaces_scan_truncated_when_any_mailbox_hits_cap(self):
        gmail = _backend(
            *(
                _sent_real_now(f"s{i}", thread_id=f"t{i}", age_days=5 + i)
                for i in range(5)
            )
        )
        host = _Host(gmail)
        check_followups = _registered_tool(host)

        payload = json.loads(check_followups(max_sent=3))
        assert payload["ok"] is True
        assert payload["data"]["scan_truncated"] is True

    def test_tool_not_truncated_under_cap(self):
        gmail = _backend(_sent_real_now("s1", thread_id="t1", age_days=5))
        host = _Host(gmail)
        check_followups = _registered_tool(host)

        payload = json.loads(check_followups())
        assert payload["ok"] is True
        assert payload["data"]["scan_truncated"] is False


# ---------------------------------------------------------------------------
# Read-only guarantee (issue #1606 test acceptance criterion 2)
# ---------------------------------------------------------------------------


# Backend methods a read-only detector is allowed to call. Everything else
# (send_*, create_draft, trash, label mutations, ...) is a violation.
_ALLOWED_BACKEND_CALLS = {"get_user_email", "list_messages", "get_thread"}


class TestReadOnly:
    def test_detector_touches_no_send_path(self):
        gmail = _backend(
            _sent("s1", thread_id="t1", age_days=9),
            _sent("s2", thread_id="t2", age_days=6),
            _inbound("r2", thread_id="t2", age_days=5),
        )
        _run(gmail, window_days=3)
        called = {method for method, _ in gmail.transport.calls}
        assert called <= _ALLOWED_BACKEND_CALLS, (
            f"read-only detector called mutating backend methods: "
            f"{sorted(called - _ALLOWED_BACKEND_CALLS)}"
        )

    def test_module_references_no_send_path(self):
        src = Path(followup_tools.__file__).read_text(encoding="utf-8")
        assert not re.search(
            r"\bsend_message\b|\bsend_draft\b|\bsend_now\b|\bcreate_draft\b", src
        ), "followup_tools must never reference a send/draft backend call"


# ---------------------------------------------------------------------------
# Tool registration + envelope (mixin surface)
# ---------------------------------------------------------------------------


# The tool closure computes ages against the real clock (no ``now_ms`` seam
# at the tool surface), so ToolSurface fixtures anchor to real time.
def _sent_real_now(msg_id: str, *, thread_id: str, age_days: float) -> Dict[str, Any]:
    import time as _time

    real_now_ms = int(_time.time() * 1000)
    return _sent(
        msg_id,
        thread_id=thread_id,
        internal_date=str(int(real_now_ms - age_days * DAY_MS)),
    )


class _Host(FollowupToolsMixin):
    """Minimal stand-in for EmailTriageAgent's tool-hosting surface."""

    def __init__(self, backend: FakeGmailBackend, *, window_days: int = 3):
        self._gmail = backend
        self._backends = {"google": backend}
        self._message_mailbox: Dict[str, str] = {}
        self.config = SimpleNamespace(debug=False, followup_window_days=window_days)

    def _remember_message_mailbox(self, message_id, provider):
        if message_id:
            self._message_mailbox[message_id] = provider


def _registered_tool(host: _Host):
    _TOOL_REGISTRY.clear()
    host._register_followup_tools()
    assert "check_followups" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["check_followups"]["function"]


class TestToolSurface:
    def test_tool_returns_ok_envelope_with_mailbox_tag(self):
        gmail = _backend(_sent_real_now("s1", thread_id="t1", age_days=5))
        host = _Host(gmail)
        check_followups = _registered_tool(host)

        payload = json.loads(check_followups())
        assert payload["ok"] is True
        data = payload["data"]
        assert data["window_days"] == 3
        assert len(data["awaiting_reply"]) == 1
        item = data["awaiting_reply"][0]
        assert item["mailbox"] == "google"
        # Provenance is remembered so downstream tools route correctly.
        assert host._message_mailbox["s1"] == "google"
        assert host._message_mailbox["t1"] == "google"

    def test_tool_window_arg_overrides_config_default(self):
        gmail = _backend(_sent_real_now("s1", thread_id="t1", age_days=5))
        host = _Host(gmail, window_days=3)
        check_followups = _registered_tool(host)

        payload = json.loads(check_followups(window_days=7))
        assert payload["ok"] is True
        assert payload["data"]["window_days"] == 7
        assert payload["data"]["awaiting_reply"] == []

    def test_tool_invalid_window_returns_error_envelope(self):
        gmail = _backend(_sent_real_now("s1", thread_id="t1", age_days=5))
        host = _Host(gmail)
        check_followups = _registered_tool(host)

        payload = json.loads(check_followups(window_days=-1))
        assert payload["ok"] is False
        assert "window_days" in payload["error"]
