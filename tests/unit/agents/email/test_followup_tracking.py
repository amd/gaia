# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for follow-up tracking — ``find_awaiting_reply`` (#1606).

Locks the issue's acceptance criteria:

1. A sent message whose thread later received an inbound reply is NOT
   flagged; one without a reply IS flagged once older than the window.
2. The detector is read-only — it triggers no ``send_*`` / draft side
   effects (asserted against the fake backend's transport log AND the
   module source, which must not reference any send path).
3. The window is configurable, and the awaiting-reply set carries
   message_id, recipient, subject, and age.

All detection is deterministic (no LLM); no Lemonade server is involved.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools import followup_tools  # noqa: E402
from gaia_agent_email.tools.followup_tools import (  # noqa: E402
    FollowupToolsMixin,
    find_awaiting_reply_impl,
)

from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

ME = "user@example.com"
NOW_MS = 1_760_000_000_000  # fixed "now" so ages are deterministic
DAY_MS = 86_400_000


def _days_ago(days: float) -> int:
    return int(NOW_MS - days * DAY_MS)


def _make_msg(
    *,
    msg_id: str,
    thread_id: str,
    from_: str,
    to: str,
    subject: str,
    date_ms: int,
    labels: list,
) -> Dict[str, Any]:
    """Minimal Gmail-API-v1-shape message for FakeGmailBackend.add_message."""
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": list(labels),
        "snippet": f"snippet of {msg_id}",
        "internalDate": str(date_ms),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": from_},
                {"name": "To", "value": to},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 1 Jun 2026 09:00:00 +0000"},
            ],
            "body": {"data": "", "size": 0},
        },
    }


def _backend() -> FakeGmailBackend:
    return FakeGmailBackend(user_email=ME)


def _add_sent(gmail, *, msg_id, thread_id, to, subject, date_ms):
    gmail.add_message(
        _make_msg(
            msg_id=msg_id,
            thread_id=thread_id,
            from_=f"User <{ME}>",
            to=to,
            subject=subject,
            date_ms=date_ms,
            labels=["SENT"],
        )
    )


def _add_inbound(gmail, *, msg_id, thread_id, from_, subject, date_ms):
    gmail.add_message(
        _make_msg(
            msg_id=msg_id,
            thread_id=thread_id,
            from_=from_,
            to=f"User <{ME}>",
            subject=subject,
            date_ms=date_ms,
            labels=["INBOX"],
        )
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 1 — flagged vs. not flagged
# ---------------------------------------------------------------------------


def test_replied_thread_is_not_flagged():
    gmail = _backend()
    _add_sent(
        gmail,
        msg_id="s1",
        thread_id="t1",
        to="alice@example.com",
        subject="Q3 numbers",
        date_ms=_days_ago(5),
    )
    _add_inbound(
        gmail,
        msg_id="r1",
        thread_id="t1",
        from_="Alice <alice@example.com>",
        subject="Re: Q3 numbers",
        date_ms=_days_ago(4),
    )

    out = find_awaiting_reply_impl(gmail, window_days=3, now_ms=NOW_MS)

    assert out["count"] == 0
    assert out["awaiting_reply"] == []
    assert out["threads_scanned"] == 1


def test_unreplied_sent_message_is_flagged_after_window():
    gmail = _backend()
    _add_sent(
        gmail,
        msg_id="s1",
        thread_id="t1",
        to="alice@example.com",
        subject="Q3 numbers",
        date_ms=_days_ago(5),
    )

    out = find_awaiting_reply_impl(gmail, window_days=3, now_ms=NOW_MS)

    assert out["count"] == 1
    assert out["scan_truncated"] is False
    item = out["awaiting_reply"][0]
    assert item["message_id"] == "s1"
    assert item["thread_id"] == "t1"
    assert item["recipient"] == "alice@example.com"
    assert item["subject"] == "Q3 numbers"
    assert item["age_days"] == pytest.approx(5.0, abs=0.1)


def test_unreplied_but_inside_window_is_not_flagged():
    gmail = _backend()
    _add_sent(
        gmail,
        msg_id="s1",
        thread_id="t1",
        to="alice@example.com",
        subject="Quick question",
        date_ms=_days_ago(1),
    )

    out = find_awaiting_reply_impl(gmail, window_days=3, now_ms=NOW_MS)

    assert out["count"] == 0


def test_multiple_own_sends_flag_only_the_latest():
    """Two of my sends on one thread, no reply -> one flag, on the newest."""
    gmail = _backend()
    _add_sent(
        gmail,
        msg_id="s1",
        thread_id="t1",
        to="bob@example.com",
        subject="Contract draft",
        date_ms=_days_ago(10),
    )
    _add_sent(
        gmail,
        msg_id="s2",
        thread_id="t1",
        to="bob@example.com",
        subject="Re: Contract draft",
        date_ms=_days_ago(6),
    )

    out = find_awaiting_reply_impl(gmail, window_days=3, now_ms=NOW_MS)

    assert out["count"] == 1
    item = out["awaiting_reply"][0]
    assert item["message_id"] == "s2"
    assert item["age_days"] == pytest.approx(6.0, abs=0.1)


def test_results_sorted_most_overdue_first():
    gmail = _backend()
    _add_sent(
        gmail,
        msg_id="s1",
        thread_id="t1",
        to="a@example.com",
        subject="older",
        date_ms=_days_ago(9),
    )
    _add_sent(
        gmail,
        msg_id="s2",
        thread_id="t2",
        to="b@example.com",
        subject="newer",
        date_ms=_days_ago(4),
    )

    out = find_awaiting_reply_impl(gmail, window_days=3, now_ms=NOW_MS)

    assert [i["message_id"] for i in out["awaiting_reply"]] == ["s1", "s2"]


def test_scan_truncation_is_surfaced_not_silent():
    """Newest-first listing means the ceilings hide the OLDEST sends —
    the result must say the scan was partial, never read as exhaustive."""
    gmail = _backend()
    for i in range(3):
        _add_sent(
            gmail,
            msg_id=f"s{i}",
            thread_id=f"t{i}",
            to=f"p{i}@example.com",
            subject=f"thread {i}",
            date_ms=_days_ago(5 + i),
        )

    # max_threads ceiling: 3 sent threads, only 2 inspected.
    out = find_awaiting_reply_impl(gmail, window_days=3, max_threads=2, now_ms=NOW_MS)
    assert out["threads_scanned"] == 2
    assert out["scan_truncated"] is True

    # Listing ceiling: a full page of sent stubs means older ones may exist.
    from gaia_agent_email.tools.followup_tools import DEFAULT_SENT_SCAN_CEILING

    gmail2 = _backend()
    for i in range(DEFAULT_SENT_SCAN_CEILING):
        _add_sent(
            gmail2,
            msg_id=f"s{i}",
            thread_id=f"t{i}",
            to="x@example.com",
            subject=f"thread {i}",
            date_ms=_days_ago(4) + i,  # unique dates, all past the window
        )
    out2 = find_awaiting_reply_impl(
        gmail2, window_days=3, max_threads=DEFAULT_SENT_SCAN_CEILING, now_ms=NOW_MS
    )
    assert out2["scan_truncated"] is True


# ---------------------------------------------------------------------------
# Acceptance criterion 2 — strictly read-only, no send path
# ---------------------------------------------------------------------------

_READ_ONLY_BACKEND_CALLS = {"get_user_email", "list_messages", "get_thread"}


def test_detector_triggers_no_send_side_effects():
    gmail = _backend()
    _add_sent(
        gmail,
        msg_id="s1",
        thread_id="t1",
        to="alice@example.com",
        subject="Q3 numbers",
        date_ms=_days_ago(5),
    )
    _add_inbound(
        gmail,
        msg_id="r2",
        thread_id="t2",
        from_="Carol <carol@example.com>",
        subject="FYI",
        date_ms=_days_ago(2),
    )

    find_awaiting_reply_impl(gmail, window_days=3, now_ms=NOW_MS)

    called = {method for method, _ in gmail.transport.calls}
    assert called <= _READ_ONLY_BACKEND_CALLS, (
        f"find_awaiting_reply_impl called non-read-only backend methods: "
        f"{sorted(called - _READ_ONLY_BACKEND_CALLS)}"
    )


def test_module_references_no_send_path():
    """The module must not touch drafts/sends even by reference (#1606 AC)."""
    source = inspect.getsource(followup_tools)
    for forbidden in (
        "send_message",
        "send_draft",
        "send_now",
        "create_draft",
        "forward_message",
        "reply_tools",
    ):
        assert forbidden not in source, (
            f"followup_tools references send-path symbol {forbidden!r}; "
            "follow-up tracking must stay detection-only (see #555 for "
            "autonomous follow-up sending)"
        )


# ---------------------------------------------------------------------------
# Fail-loudly invariants
# ---------------------------------------------------------------------------


def test_empty_user_email_fails_loudly():
    gmail = FakeGmailBackend(user_email="")
    with pytest.raises(ValueError, match="empty user email"):
        find_awaiting_reply_impl(gmail, window_days=3, now_ms=NOW_MS)


def test_negative_window_fails_loudly():
    with pytest.raises(ValueError, match="window_days"):
        find_awaiting_reply_impl(_backend(), window_days=-1, now_ms=NOW_MS)


def test_unparseable_internal_date_fails_loudly():
    gmail = _backend()
    _add_sent(
        gmail,
        msg_id="s1",
        thread_id="t1",
        to="alice@example.com",
        subject="broken thread",
        date_ms=_days_ago(5),
    )
    # Corrupt a thread member that is NOT in the SENT listing, so the
    # detector's own date parsing (not the fake's listing sort) hits it.
    reply = _make_msg(
        msg_id="r1",
        thread_id="t1",
        from_="Alice <alice@example.com>",
        to=f"User <{ME}>",
        subject="Re: broken thread",
        date_ms=_days_ago(4),
        labels=["INBOX"],
    )
    reply["internalDate"] = "not-a-number"
    gmail.add_message(reply)

    with pytest.raises(ValueError, match="internalDate"):
        find_awaiting_reply_impl(gmail, window_days=3, now_ms=NOW_MS)


# ---------------------------------------------------------------------------
# Production tool wiring (mixin registration)
# ---------------------------------------------------------------------------


class _Host(FollowupToolsMixin):
    """Minimal agent stand-in satisfying the mixin's stated contract."""

    def __init__(self, backends: Dict[str, Any], *, followup_window_days: int = 3):
        self._backends = backends
        self._gmail = next(iter(backends.values()), None)
        self.config = SimpleNamespace(
            debug=False, followup_window_days=followup_window_days
        )
        self.remembered: list = []

    def _remember_message_mailbox(self, message_id, provider):
        self.remembered.append((message_id, provider))


def _registered_tool(backends: Dict[str, Any], **host_kwargs):
    _TOOL_REGISTRY.clear()
    host = _Host(backends, **host_kwargs)
    host._register_followup_tools()
    return _TOOL_REGISTRY["find_awaiting_reply"]["function"]


def test_tool_envelope_and_config_window_via_registration():
    gmail = _backend()
    # 40 days old — far past any window, so real wall-clock "now" stays valid.
    _add_sent(
        gmail,
        msg_id="s1",
        thread_id="t1",
        to="alice@example.com",
        subject="Overdue thing",
        date_ms=_days_ago(40),
    )
    tool_fn = _registered_tool({"google": gmail}, followup_window_days=3)

    out = json.loads(tool_fn())

    assert out["ok"] is True
    data = out["data"]
    assert data["window_days"] == 3
    assert data["count"] == 1
    assert data["awaiting_reply"][0]["message_id"] == "s1"
    assert data["awaiting_reply"][0]["mailbox"] == "google"


def test_tool_is_not_confirmation_gated():
    """Read-only detection must never require a confirmation token."""
    assert "find_awaiting_reply" not in TOOLS_REQUIRING_CONFIRMATION


def test_microsoft_only_setup_is_refused_loudly():
    """Graph has no SENT label mapping — scanning it would silently serve
    the inbox folder, so the tool must refuse instead of degrade."""
    tool_fn = _registered_tool({"microsoft": object()})

    out = json.loads(tool_fn())

    assert out["ok"] is False
    assert "Gmail only" in out["error"]


def test_mixed_mailboxes_scan_gmail_and_name_the_skip():
    gmail = _backend()
    _add_sent(
        gmail,
        msg_id="s1",
        thread_id="t1",
        to="alice@example.com",
        subject="Overdue thing",
        date_ms=_days_ago(40),
    )
    tool_fn = _registered_tool({"google": gmail, "microsoft": object()})

    out = json.loads(tool_fn())

    assert out["ok"] is True
    assert out["data"]["count"] == 1
    assert "microsoft" in out["data"]["skipped_mailboxes"]
