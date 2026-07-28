# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Waiting-on-you detection tests for EmailTriageAgent (#2581).

Acceptance criteria covered:
- A read-only tool returns inbound messages awaiting a reply, with sender,
  topic/subject, age, and thread id.
- Detects a direct question by phrasing, not by the presence of ``?``.
- Detects an informal meeting-time proposal, including the #2580 incident
  wording ("any chance to meet this Thursday at 9am") which the existing
  meeting heuristic does NOT catch.
- The two named adversarial corpus ids must NOT qualify even in isolation
  (no prior correspondence).
- A PERSONAL-labelled direct question is still detected.
- Threads the user already replied to are excluded.
- Bulk/automated senders are excluded (reusing the existing signal list).
- Any meeting-signal check gates on ``is_meeting_request and confidence ==
  "high"``, never confidence alone.
- The tool performs no mailbox mutation.

All tests are hermetic: FakeGmailBackend only, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

# parents[0] = tests/,  [1] = email/,  [2] = python/,  [3] = agents/,
# [4] = hub/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

import gaia_agent_email.tools.waiting_on_you_tools as waiting_on_you_tools  # noqa: E402
from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    detect_meeting_request_heuristic,
)
from gaia_agent_email.tools.waiting_on_you_tools import (  # noqa: E402
    CORROBORATION_KNOWN_CORRESPONDENT,
    CORROBORATION_THREAD_REPLY,
    SIGNAL_DIRECT_ASK,
    SIGNAL_MEETING_TIME,
    WaitingOnYouToolsMixin,
    _has_gated_meeting_signal,
    detect_waiting_on_you_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

USER_EMAIL = "user@example.com"
DAY_MS = 24 * 60 * 60 * 1000
NOW_MS = 1_750_000_000_000


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    thread_id: str,
    sender: str,
    to: str,
    subject: str,
    body: str = "",
    age_days: float = 0,
    label_ids: Optional[List[str]] = None,
    internal_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal Gmail API v1 message dict for the fake backend."""
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": list(label_ids or ["INBOX"]),
        "snippet": body[:80],
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
            "body": {"size": len(body), "data": _b64(body)},
        },
        "sizeEstimate": len(body),
    }


def _inbound(
    msg_id: str,
    *,
    thread_id: str,
    sender: str = "Alice <alice@example.com>",
    subject: str = "Quick question",
    body: str = "Can you take a look at this?",
    age_days: float = 3,
    label_ids: Optional[List[str]] = None,
    internal_date: Optional[str] = None,
) -> Dict[str, Any]:
    return _msg(
        msg_id,
        thread_id=thread_id,
        sender=sender,
        to=USER_EMAIL,
        subject=subject,
        body=body,
        age_days=age_days,
        label_ids=label_ids or ["INBOX"],
        internal_date=internal_date,
    )


def _outbound(
    msg_id: str,
    *,
    thread_id: str,
    to: str = "alice@example.com",
    subject: str = "Re: Quick question",
    body: str = "Sure, will do.",
    age_days: float = 2,
    label_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return _msg(
        msg_id,
        thread_id=thread_id,
        sender=f"Me <{USER_EMAIL}>",
        to=to,
        subject=subject,
        body=body,
        age_days=age_days,
        label_ids=label_ids or ["SENT"],
    )


def _backend(*messages: Dict[str, Any]) -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email=USER_EMAIL)
    for m in messages:
        gmail.add_message(m)
    return gmail


def _run(gmail: FakeGmailBackend, **kwargs) -> Dict[str, Any]:
    return detect_waiting_on_you_impl(gmail, now_ms=NOW_MS, **kwargs)


# ---------------------------------------------------------------------------
# Core qualification semantics
# ---------------------------------------------------------------------------


class TestDirectAskDetection:
    def test_direct_ask_with_thread_history_qualifies(self):
        # Real back-and-forth: user sent the FIRST message, then got a
        # direct-ask reply — corroborated by the thread's own history.
        gmail = _backend(
            _outbound("s1", thread_id="t1", age_days=10),
            _inbound(
                "r1",
                thread_id="t1",
                sender="Alice <alice@example.com>",
                subject="Re: budget",
                body="Thanks! Could you please confirm the numbers by Friday?",
                age_days=3,
            ),
        )
        out = _run(gmail)
        assert len(out["waiting_on_you"]) == 1
        item = out["waiting_on_you"][0]
        assert item["message_id"] == "r1"
        assert item["thread_id"] == "t1"
        assert item["sender"] == "alice@example.com"
        assert item["age_days"] == 3
        assert item["signal"] == SIGNAL_DIRECT_ASK
        assert item["corroboration"] == CORROBORATION_THREAD_REPLY

    def test_direct_ask_from_known_correspondent_qualifies(self):
        # New thread, but the user has emailed this address before (a
        # different thread) — corroborated by Sent-folder history.
        gmail = _backend(
            _outbound("s0", thread_id="t-old", to="bob@example.com", age_days=30),
            _inbound(
                "r1",
                thread_id="t-new",
                sender="Bob <bob@example.com>",
                subject="New topic",
                body="Any chance you could review this draft today?",
                age_days=1,
            ),
        )
        out = _run(gmail)
        assert len(out["waiting_on_you"]) == 1
        item = out["waiting_on_you"][0]
        assert item["message_id"] == "r1"
        assert item["corroboration"] == CORROBORATION_KNOWN_CORRESPONDENT

    def test_bare_question_mark_with_no_corroboration_does_not_qualify(self):
        # First-contact message, no prior correspondence at all. A bare "?"
        # is not phrasing our detector matches, and even if it were, there
        # is no corroboration — this must NOT qualify either way.
        gmail = _backend(
            _inbound(
                "r1",
                thread_id="t1",
                sender="Stranger <stranger@example.com>",
                subject="Are you interested?",
                body="Are you interested in our product?",
                age_days=1,
            )
        )
        out = _run(gmail)
        assert out["waiting_on_you"] == []

    def test_direct_ask_first_contact_no_corroboration_does_not_qualify(self):
        # Genuine ask phrasing, but cold — no thread history, no known
        # correspondent. Corroboration must gate this out.
        gmail = _backend(
            _inbound(
                "r1",
                thread_id="t1",
                sender="Stranger <stranger@example.com>",
                subject="Quick ask",
                body="Could you please confirm your budget by Friday?",
                age_days=1,
            )
        )
        out = _run(gmail)
        assert out["waiting_on_you"] == []


class TestMeetingTimeDetection:
    def test_incident_wording_qualifies_with_corroboration(self):
        # #2580 incident wording: the existing calendar heuristic does NOT
        # catch this phrasing (no _INVITE_PHRASES / _MEETING_NOUNS match).
        assert (
            detect_meeting_request_heuristic(
                "", "Any chance to meet this Thursday at 9am?"
            ).is_meeting_request
            is False
        ), "test setup assumption broken: the existing heuristic now catches this"

        gmail = _backend(
            _outbound("s1", thread_id="t1", age_days=10),
            _inbound(
                "r1",
                thread_id="t1",
                sender="Alice <alice@example.com>",
                subject="Catching up",
                body="Any chance to meet this Thursday at 9am?",
                age_days=2,
            ),
        )
        out = _run(gmail)
        assert len(out["waiting_on_you"]) == 1
        assert out["waiting_on_you"][0]["signal"] == SIGNAL_MEETING_TIME

    def test_meeting_noun_without_time_does_not_qualify(self):
        gmail = _backend(
            _outbound("s1", thread_id="t1", age_days=10),
            _inbound(
                "r1",
                thread_id="t1",
                sender="Alice <alice@example.com>",
                subject="Notes",
                body="The meeting notes are attached for your review.",
                age_days=2,
            ),
        )
        out = _run(gmail)
        assert out["waiting_on_you"] == []


class TestMeetingSignalGating:
    """AC: any meeting-signal check gates on is_meeting_request AND
    confidence == 'high', never confidence alone."""

    def test_confident_negative_is_not_treated_as_a_meeting_signal(self):
        # The existing heuristic's no-signal branch returns
        # is_meeting_request=False, confidence="high" — a confident
        # NEGATIVE. Gating on confidence alone would wrongly treat this as
        # a positive.
        detection = detect_meeting_request_heuristic("", "Just a normal update, nothing scheduled.")
        assert detection.is_meeting_request is False
        assert detection.confidence == "high"
        assert (
            _has_gated_meeting_signal("", "Just a normal update, nothing scheduled.")
            is False
        )

    def test_confident_positive_is_treated_as_a_meeting_signal(self):
        detection = detect_meeting_request_heuristic(
            "", "Are you free to meet Thursday at 2pm?"
        )
        assert detection.is_meeting_request is True
        assert detection.confidence == "high"
        assert (
            _has_gated_meeting_signal("", "Are you free to meet Thursday at 2pm?")
            is True
        )


# ---------------------------------------------------------------------------
# Regression: the two named adversarial corpus entries must never qualify
# ---------------------------------------------------------------------------


_CORPUS_PATH = _REPO_ROOT / "tests" / "fixtures" / "email" / "vendor_corpus_seed.jsonl"


def _load_corpus_record(record_id_prefix: str) -> Dict[str, Any]:
    with open(_CORPUS_PATH, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["id"].startswith(record_id_prefix):
                return rec
    raise AssertionError(f"corpus record {record_id_prefix!r} not found")


class TestAdversarialRegressions:
    """#2584-panel fact: rule 2 of the existing calendar heuristic
    (meeting noun + concrete time) false-positives on these two adversarial
    PROMOTIONAL corpus rows ("call" + "4PM"). Corroboration must keep them
    from qualifying regardless."""

    @pytest.mark.parametrize(
        "record_id",
        ["1677f2af-ab08-4d57-8d58-967228dc89a0", "5f720d3d-a91f-47b5-a5c7-4edbef57cc9c"],
    )
    def test_regression_corpus_entry_does_not_qualify(self, record_id):
        rec = _load_corpus_record(record_id)
        gmail = _backend(
            _msg(
                rec["id"],
                thread_id=rec["id"],  # each corpus row is its own thread root
                sender=rec["sender"],
                to=", ".join(rec["to"]),
                subject=rec["subject"],
                body=rec["body"],
                age_days=1,
                label_ids=["INBOX"],
            )
        )
        out = _run(gmail)
        assert out["waiting_on_you"] == [], (
            f"corpus entry {record_id} incorrectly qualified as waiting-on-you: "
            f"{out['waiting_on_you']}"
        )


# ---------------------------------------------------------------------------
# PERSONAL-category gap (#2584 cannot close this; this detector must)
# ---------------------------------------------------------------------------


class TestPersonalCategoryGap:
    def test_personal_labelled_direct_question_is_detected(self):
        # Gmail's CATEGORY_PERSONAL branch always returns confident=True in
        # triage_heuristics — invisible to any confidence-gated fix. This
        # detector operates on the message directly, not on triage
        # confidence, so it must still catch a genuine direct ask here.
        gmail = _backend(
            _outbound("s1", thread_id="t1", age_days=10),
            _inbound(
                "r1",
                thread_id="t1",
                sender="Alice <alice@example.com>",
                subject="Weekend plans",
                body="Could you let me know if you're free Saturday?",
                age_days=2,
                label_ids=["INBOX", "CATEGORY_PERSONAL"],
            ),
        )
        out = _run(gmail)
        assert len(out["waiting_on_you"]) == 1
        assert out["waiting_on_you"][0]["message_id"] == "r1"


# ---------------------------------------------------------------------------
# Already-replied exclusion
# ---------------------------------------------------------------------------


class TestAlreadyReplied:
    def test_thread_already_replied_to_is_excluded(self):
        # The user's own message is the LATEST in the thread — already
        # handled, must not qualify even though the earlier inbound message
        # had a direct ask.
        gmail = _backend(
            _inbound(
                "r1",
                thread_id="t1",
                sender="Alice <alice@example.com>",
                subject="Could you help?",
                body="Could you please review this by tomorrow?",
                age_days=5,
            ),
            _outbound("s1", thread_id="t1", age_days=4),
        )
        out = _run(gmail)
        assert out["waiting_on_you"] == []

    def test_reply_then_new_inbound_message_requalifies(self):
        # After the user replies, a NEW inbound message arrives — the
        # thread should qualify again (latest message is inbound again).
        gmail = _backend(
            _inbound("r1", thread_id="t1", age_days=6, body="Could you help with this?"),
            _outbound("s1", thread_id="t1", age_days=5),
            _inbound(
                "r2",
                thread_id="t1",
                sender="Alice <alice@example.com>",
                subject="Re: follow-up",
                body="Thanks — could you also confirm the date?",
                age_days=1,
            ),
        )
        out = _run(gmail)
        assert [i["message_id"] for i in out["waiting_on_you"]] == ["r2"]


# ---------------------------------------------------------------------------
# Bulk / automated sender exclusion (reuses existing signal, no new list)
# ---------------------------------------------------------------------------


class TestAutomatedSenderExclusion:
    def test_automated_sender_excluded_even_with_direct_ask_phrasing(self):
        gmail = _backend(
            _outbound("s1", thread_id="t1", age_days=10, to="alerts@example.com"),
            _inbound(
                "r1",
                thread_id="t1",
                sender="Alerts <alerts@example.com>",
                subject="Could you please confirm",
                body="Could you please confirm your subscription by Friday?",
                age_days=1,
            ),
        )
        out = _run(gmail)
        assert out["waiting_on_you"] == []

    def test_automated_sender_list_is_reused_not_reinvented(self):
        # Locks in that this module imports the SAME keyword tuple
        # triage_heuristics defines, rather than defining a parallel list.
        from gaia_agent_email.tools.triage_heuristics import (
            _AUTOMATED_SENDER_KEYWORDS as canonical,
        )

        assert waiting_on_you_tools._AUTOMATED_SENDER_KEYWORDS is canonical


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------


_ALLOWED_BACKEND_CALLS = {"get_user_email", "list_messages", "get_thread"}


class TestReadOnly:
    def test_detector_touches_no_mutating_backend_call(self):
        gmail = _backend(
            _outbound("s1", thread_id="t1", age_days=10),
            _inbound(
                "r1",
                thread_id="t1",
                body="Could you please take a look at this?",
                age_days=2,
            ),
        )
        _run(gmail)
        called = {method for method, _ in gmail.transport.calls}
        assert called <= _ALLOWED_BACKEND_CALLS, (
            f"read-only detector called mutating backend methods: "
            f"{sorted(called - _ALLOWED_BACKEND_CALLS)}"
        )

    def test_module_references_no_send_path(self):
        src = Path(waiting_on_you_tools.__file__).read_text(encoding="utf-8")
        assert not re.search(
            r"\bsend_message\b|\bsend_draft\b|\bsend_now\b|\bcreate_draft\b|"
            r"\barchive_message\b|\blabel_message\b|\btrash_message\b",
            src,
        ), "waiting_on_you_tools must never reference a mutating backend call"


# ---------------------------------------------------------------------------
# Tool registration + envelope (mixin surface)
# ---------------------------------------------------------------------------


def _inbound_real_now(msg_id: str, *, thread_id: str, age_days: float, **kwargs) -> Dict[str, Any]:
    import time as _time

    real_now_ms = int(_time.time() * 1000)
    kwargs.setdefault("body", "Could you please take a look at this?")
    return _inbound(
        msg_id,
        thread_id=thread_id,
        internal_date=str(int(real_now_ms - age_days * DAY_MS)),
        **kwargs,
    )


def _outbound_real_now(msg_id: str, *, thread_id: str, age_days: float, **kwargs) -> Dict[str, Any]:
    import time as _time

    real_now_ms = int(_time.time() * 1000)
    m = _outbound(msg_id, thread_id=thread_id, age_days=age_days, **kwargs)
    m["internalDate"] = str(int(real_now_ms - age_days * DAY_MS))
    return m


class _Host(WaitingOnYouToolsMixin):
    """Minimal stand-in for EmailTriageAgent's tool-hosting surface."""

    def __init__(self, backend: FakeGmailBackend):
        self._gmail = backend
        self._backends = {"google": backend}
        self._message_mailbox: Dict[str, str] = {}
        self.config = SimpleNamespace(debug=False)

    def _remember_message_mailbox(self, message_id, provider):
        if message_id:
            self._message_mailbox[message_id] = provider


def _registered_tool(host: _Host):
    _TOOL_REGISTRY.clear()
    host._register_waiting_on_you_tools()
    assert "list_waiting_on_you" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["list_waiting_on_you"]["function"]


class TestToolSurface:
    def test_tool_returns_ok_envelope_with_mailbox_tag(self):
        gmail = _backend(
            _outbound_real_now("s1", thread_id="t1", age_days=10),
            _inbound_real_now("r1", thread_id="t1", age_days=2),
        )
        host = _Host(gmail)
        list_waiting_on_you = _registered_tool(host)

        payload = json.loads(list_waiting_on_you())
        assert payload["ok"] is True
        data = payload["data"]
        assert len(data["waiting_on_you"]) == 1
        item = data["waiting_on_you"][0]
        assert item["mailbox"] == "google"
        assert host._message_mailbox["r1"] == "google"
        assert host._message_mailbox["t1"] == "google"

    def test_tool_no_qualifying_message_returns_empty_list(self):
        gmail = _backend(
            _inbound_real_now(
                "r1", thread_id="t1", age_days=1, body="Thanks for the update!"
            )
        )
        host = _Host(gmail)
        list_waiting_on_you = _registered_tool(host)

        payload = json.loads(list_waiting_on_you())
        assert payload["ok"] is True
        assert payload["data"]["waiting_on_you"] == []


# ---------------------------------------------------------------------------
# Full 104-row PROMOTIONAL corpus precision measurement
# ---------------------------------------------------------------------------


def _load_promotional_corpus() -> List[Dict[str, Any]]:
    records = []
    with open(_CORPUS_PATH, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("category") == "PROMOTIONAL":
                records.append(rec)
    return records


class TestPromotionalCorpusPrecision:
    def test_zero_false_positives_over_full_promotional_corpus(self, capsys):
        """Every PROMOTIONAL corpus row is a standalone thread root (no prior
        correspondence, per the corpus generator's is_thread_root=True) — the
        conservative, correct way to score them is as isolated first-contact
        messages: no thread reply history, no known correspondent. This is
        this issue's real gate, not the unit tests above."""
        records = _load_promotional_corpus()
        assert len(records) == 104, f"expected 104 PROMOTIONAL rows, got {len(records)}"

        false_positives = []
        for rec in records:
            gmail = _backend(
                _msg(
                    rec["id"],
                    thread_id=rec["id"],
                    sender=rec["sender"],
                    to=", ".join(rec.get("to") or ["user@example.com"]),
                    subject=rec["subject"],
                    body=rec["body"],
                    age_days=1,
                    label_ids=["INBOX"],
                )
            )
            out = _run(gmail)
            if out["waiting_on_you"]:
                false_positives.append(rec["id"])

        print(
            f"\nwaiting-on-you false positives over 104 PROMOTIONAL rows: "
            f"{len(false_positives)} {false_positives}"
        )
        assert false_positives == [], (
            f"detector incorrectly qualified {len(false_positives)} of 104 "
            f"PROMOTIONAL rows: {false_positives}"
        )
