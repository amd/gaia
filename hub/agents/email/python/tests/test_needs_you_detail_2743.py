# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2743 Increment 3 — per-item ``needs_you[].detail`` extraction.

Covers ``needs_you_detail.py`` in isolation (extraction per kind, injection
defense, graceful degradation) and the actual wiring into the
``pre_scan_inbox`` agent tool. #2571 is the precedent this file pins
directly: a DECIDE row must never state a calendar verdict the tool did not
compute.
"""

from __future__ import annotations

import base64
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# parents[0]=tests/, [1]=python/, [2]=email/, [3]=agents/, [4]=hub/, [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.needs_you_detail import (  # noqa: E402
    MAX_DETAIL_CHARS,
    MAX_DETAIL_LINES,
    extract_needs_you_detail,
    fill_needs_you_detail,
    rewrap_detail_for_tool_result,
    rewrap_due_hint_for_tool_result,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    UNTRUSTED_BODY_CLOSE,
    UNTRUSTED_BODY_OPEN,
    wrap_untrusted_body,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str = "m1",
    *,
    subject: str = "Re: rollback",
    sender: str = "alice@example.com",
    body: str = "Can you confirm the rollback completed?",
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": body[:200],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"data": _b64url(body), "size": len(body.encode("utf-8"))},
        },
        "sizeEstimate": len(body),
    }


class _RecordingGmail:
    """Fails the test if touched -- proves an extraction path short-circuits
    BEFORE any message fetch (action_item / no message_id cases)."""

    def get_message(self, message_id, *, format: str = "full"):
        raise AssertionError(
            f"get_message({message_id!r}) should never be called for this item"
        )


class _FakeGmail:
    def __init__(self, messages: Dict[str, Dict[str, Any]]):
        self._messages = messages
        self.calls: List[str] = []

    def get_message(self, message_id, *, format: str = "full"):
        self.calls.append(message_id)
        return self._messages[message_id]


class _FailingGmail:
    def get_message(self, message_id, *, format: str = "full"):
        raise ConnectionError("mailbox unreachable")


def _fake_chat(text: str) -> Any:
    class _Chat:
        def __init__(self):
            self.calls: List[Dict[str, Any]] = []

        def send_messages(self, messages, system_prompt="", **kwargs):
            self.calls.append({"messages": messages, "system_prompt": system_prompt})
            resp = types.SimpleNamespace()
            resp.text = text
            return resp

    return _Chat()


class _RaisingChat:
    def send_messages(self, messages, system_prompt="", **kwargs):
        raise RuntimeError("LLM backend unreachable")


class _FakeCalendar:
    """Records the window it was asked about; conflict-or-not is fixed."""

    def __init__(self, *, has_conflict: bool):
        self._has_conflict = has_conflict
        self.calls: List[Dict[str, Any]] = []

    def list_events(self, *, calendar_id="primary", time_min=None, time_max=None):
        self.calls.append({"time_min": time_min, "time_max": time_max})
        if not self._has_conflict:
            return {"items": []}
        return {
            "items": [
                {
                    "id": "ev1",
                    "summary": "Existing meeting",
                    "start": {"dateTime": time_min},
                    "end": {"dateTime": time_max},
                }
            ]
        }


class _FailingCalendar:
    def list_events(self, **kwargs):
        raise ConnectionError("calendar unreachable")


# ---------------------------------------------------------------------------
# extract_needs_you_detail: short-circuits (no LLM/mailbox work at all)
# ---------------------------------------------------------------------------


def test_action_item_kind_is_never_extracted():
    item = {"kind": "action_item", "message_id": "m1"}
    out = extract_needs_you_detail(_RecordingGmail(), item, chat=_RaisingChat())
    assert out == []


def test_missing_message_id_short_circuits():
    item = {"kind": "urgent", "message_id": None}
    out = extract_needs_you_detail(_RecordingGmail(), item, chat=_RaisingChat())
    assert out == []


# ---------------------------------------------------------------------------
# REPLY kind (urgent / waiting_on_you / needs_response)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["urgent", "waiting_on_you", "needs_response"])
def test_reply_kinds_extract_the_question(kind):
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat(
        json.dumps(["Can you confirm the rollback completed?", "Does Q3 need a re-run?"])
    )
    item = {"kind": kind, "message_id": "m1"}
    out = extract_needs_you_detail(gmail, item, chat=chat)
    assert out == [
        "Can you confirm the rollback completed?",
        "Does Q3 need a re-run?",
    ]
    assert gmail.calls == ["m1"]


def test_reply_detail_bounded_to_max_lines():
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat(json.dumps(["one", "two", "three", "four"]))
    out = extract_needs_you_detail(gmail, {"kind": "urgent", "message_id": "m1"}, chat=chat)
    assert len(out) <= MAX_DETAIL_LINES


def test_reply_detail_clipped_to_max_chars():
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat(json.dumps(["x" * 500]))
    out = extract_needs_you_detail(gmail, {"kind": "urgent", "message_id": "m1"}, chat=chat)
    assert len(out) == 1
    assert len(out[0]) <= MAX_DETAIL_CHARS


def test_reply_malformed_json_degrades_to_empty():
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat("not json at all")
    out = extract_needs_you_detail(gmail, {"kind": "urgent", "message_id": "m1"}, chat=chat)
    assert out == []


def test_llm_transport_failure_degrades_to_empty():
    gmail = _FakeGmail({"m1": _msg()})
    out = extract_needs_you_detail(
        gmail, {"kind": "urgent", "message_id": "m1"}, chat=_RaisingChat()
    )
    assert out == []


def test_message_fetch_failure_degrades_to_empty():
    out = extract_needs_you_detail(
        _FailingGmail(), {"kind": "urgent", "message_id": "m1"}, chat=_fake_chat("[]")
    )
    assert out == []


# ---------------------------------------------------------------------------
# CHECK kind (needs_review) -- the quoted deadline
# ---------------------------------------------------------------------------


def test_check_kind_extracts_the_deadline():
    gmail = _FakeGmail({"m1": _msg(body="Please respond by EOD Thursday.")})
    chat = _fake_chat('"due Friday EOD"')
    out = extract_needs_you_detail(gmail, {"kind": "needs_review", "message_id": "m1"}, chat=chat)
    assert out == ["due Friday EOD"]


def test_check_kind_empty_response_degrades():
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat('""')
    out = extract_needs_you_detail(gmail, {"kind": "needs_review", "message_id": "m1"}, chat=chat)
    assert out == []


# ---------------------------------------------------------------------------
# DECIDE kind (meeting_request) -- #2571: never state a verdict not computed
# ---------------------------------------------------------------------------


def test_decide_kind_with_conflict_states_not_free():
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat(
        json.dumps(
            {
                "proposed_time": "Thursday 2pm-2:30pm",
                "start_iso": "2026-08-06T14:00:00-07:00",
                "end_iso": "2026-08-06T14:30:00-07:00",
            }
        )
    )
    cal = _FakeCalendar(has_conflict=True)
    out = extract_needs_you_detail(
        gmail, {"kind": "meeting_request", "message_id": "m1"}, chat=chat, calendar_backend=cal
    )
    assert out[0] == "Proposed: Thursday 2pm-2:30pm"
    assert out[1] == "Your calendar is NOT free at that time."
    assert cal.calls  # the verdict was actually computed


def test_decide_kind_with_no_conflict_states_free():
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat(
        json.dumps(
            {
                "proposed_time": "Thursday 2pm-2:30pm",
                "start_iso": "2026-08-06T14:00:00-07:00",
                "end_iso": "2026-08-06T14:30:00-07:00",
            }
        )
    )
    cal = _FakeCalendar(has_conflict=False)
    out = extract_needs_you_detail(
        gmail, {"kind": "meeting_request", "message_id": "m1"}, chat=chat, calendar_backend=cal
    )
    assert out[1] == "Your calendar shows that time is free."


def test_decide_kind_without_calendar_backend_never_guesses_availability():
    """#2571 precedent: no calendar_backend wired in -> the proposed-time
    line is still extracted, but NO availability verdict is invented."""
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat(
        json.dumps(
            {
                "proposed_time": "Thursday 2pm-2:30pm",
                "start_iso": "2026-08-06T14:00:00-07:00",
                "end_iso": "2026-08-06T14:30:00-07:00",
            }
        )
    )
    out = extract_needs_you_detail(
        gmail,
        {"kind": "meeting_request", "message_id": "m1"},
        chat=chat,
        calendar_backend=None,
    )
    assert out == ["Proposed: Thursday 2pm-2:30pm"]
    assert not any("free" in line.lower() for line in out)


def test_decide_kind_without_resolved_iso_never_guesses_availability():
    """The model could not resolve a concrete window -- omit the verdict
    rather than compute-on-nothing (#2571)."""
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat(
        json.dumps({"proposed_time": "asked to pick a time", "start_iso": None, "end_iso": None})
    )
    cal = _FakeCalendar(has_conflict=False)
    out = extract_needs_you_detail(
        gmail, {"kind": "meeting_request", "message_id": "m1"}, chat=chat, calendar_backend=cal
    )
    assert out == ["Proposed: asked to pick a time"]
    assert not cal.calls  # never even asked -- nothing to compute a verdict from


def test_decide_kind_calendar_lookup_failure_omits_verdict_not_a_guess():
    gmail = _FakeGmail({"m1": _msg()})
    chat = _fake_chat(
        json.dumps(
            {
                "proposed_time": "Thursday 2pm",
                "start_iso": "2026-08-06T14:00:00-07:00",
                "end_iso": "2026-08-06T14:30:00-07:00",
            }
        )
    )
    out = extract_needs_you_detail(
        gmail,
        {"kind": "meeting_request", "message_id": "m1"},
        chat=chat,
        calendar_backend=_FailingCalendar(),
    )
    assert out == ["Proposed: Thursday 2pm"]


# ---------------------------------------------------------------------------
# Injection defense: wrap / re-wrap round trip
# ---------------------------------------------------------------------------


def test_rewrap_detail_wraps_every_line_in_the_untrusted_delimiters():
    out = rewrap_detail_for_tool_result(["Can you confirm?", "Does Q3 need a re-run?"])
    assert len(out) == 2
    for line, original in zip(out, ["Can you confirm?", "Does Q3 need a re-run?"]):
        assert line.startswith(UNTRUSTED_BODY_OPEN)
        assert line.endswith(UNTRUSTED_BODY_CLOSE)
        assert original in line


def test_rewrap_due_hint_none_passes_through():
    assert rewrap_due_hint_for_tool_result(None) is None


def test_rewrap_due_hint_wraps():
    out = rewrap_due_hint_for_tool_result("Friday EOD")
    assert out == wrap_untrusted_body("Friday EOD")


def test_max_detail_chars_leaves_room_for_the_wrapper():
    """Regression: MAX_DETAIL_CHARS bounds the RAW text, but
    contract.py's NeedsYouItem.detail validator checks the WRAPPED string
    (rewrap runs before assignment). Clipping to 240 and then wrapping used
    to overflow the contract's 240-char-per-entry bound and turn a single
    item's extraction into a whole-scan ValidationError."""
    worst_case = "x" * MAX_DETAIL_CHARS
    wrapped = rewrap_detail_for_tool_result([worst_case])[0]
    assert len(wrapped) <= 240


def test_wrapped_detail_satisfies_the_live_contract_validator():
    from gaia_agent_email.contract import NeedsYouItem

    worst_case = "x" * MAX_DETAIL_CHARS
    wrapped = rewrap_detail_for_tool_result([worst_case, worst_case])
    # Must not raise -- this is the actual pydantic model the tool result is
    # validated against on the REST path and in the openapi conformance test.
    item = NeedsYouItem(ref=1, kind="urgent", why="test", detail=wrapped)
    assert item.detail == wrapped


# ---------------------------------------------------------------------------
# fill_needs_you_detail: the orchestration entry point
# ---------------------------------------------------------------------------


def _resolver(messages: Dict[str, Dict[str, Any]]):
    gmail = _FakeGmail(messages)

    def _resolve(message_id, mailbox):
        return gmail

    return gmail, _resolve


def test_fill_populates_detail_only_for_extractable_rows():
    gmail, resolve = _resolver({"m1": _msg()})
    chat = _fake_chat(json.dumps(["Can you confirm the rollback completed?"]))
    needs_you = [
        {"kind": "urgent", "message_id": "m1", "detail": [], "due_hint": None},
        {"kind": "action_item", "message_id": None, "detail": [], "due_hint": "Friday"},
    ]
    fill_needs_you_detail(needs_you, resolve_backend=resolve, chat=chat)

    assert needs_you[0]["detail"]  # REPLY row got detail
    assert needs_you[0]["detail"][0].startswith(UNTRUSTED_BODY_OPEN)
    assert needs_you[1]["detail"] == []  # action_item is never extracted
    # due_hint is re-wrapped for EVERY item, including ones extraction skips.
    assert needs_you[1]["due_hint"] == wrap_untrusted_body("Friday")


def test_fill_skips_extraction_entirely_when_chat_is_none_but_still_wraps_due_hint():
    _, resolve = _resolver({"m1": _msg()})
    needs_you = [
        {"kind": "urgent", "message_id": "m1", "detail": [], "due_hint": None},
        {"kind": "action_item", "message_id": None, "detail": [], "due_hint": "Monday"},
    ]
    fill_needs_you_detail(needs_you, resolve_backend=resolve, chat=None)

    assert needs_you[0]["detail"] == []
    assert needs_you[1]["due_hint"] == wrap_untrusted_body("Monday")


def test_fill_degrades_one_item_when_backend_resolution_fails():
    """A multi-mailbox ambiguity (ValueError from _backend_for_message) on
    one item must not abort the rest of the list."""
    gmail, _ = _resolver({"m2": _msg("m2")})

    def _flaky_resolve(message_id, mailbox):
        if message_id == "m1":
            raise ValueError("ambiguous mailbox")
        return gmail

    chat = _fake_chat(json.dumps(["Can you confirm?"]))
    needs_you = [
        {"kind": "urgent", "message_id": "m1", "detail": [], "due_hint": None},
        {"kind": "urgent", "message_id": "m2", "detail": [], "due_hint": None},
    ]
    fill_needs_you_detail(needs_you, resolve_backend=_flaky_resolve, chat=chat)

    assert needs_you[0]["detail"] == []  # degraded, not raised
    assert needs_you[1]["detail"]  # the other item still got filled


# ---------------------------------------------------------------------------
# Wiring: the actual pre_scan_inbox agent tool
# ---------------------------------------------------------------------------


def _build_agent(tmp_path, monkeypatch, *, mail_backend):
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=mail_backend,
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        start_scheduler=False,
    )
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


def test_pre_scan_inbox_tool_fills_detail_and_wraps_it(tmp_path, monkeypatch):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    msg = _msg(
        "urgent1",
        subject="URGENT: prod incident",
        sender="oncall@example.com",
        body="Can you confirm the rollback completed?",
    )
    msg["labelIds"] = ["INBOX", "IMPORTANT"]
    backend = FakeGmailBackend(user_email="me@example.com")
    backend.add_message(msg)
    agent = _build_agent(tmp_path, monkeypatch, mail_backend=backend)
    try:
        agent.chat = _fake_chat(
            json.dumps(["Can you confirm the rollback completed?"])
        )
        fn = _TOOL_REGISTRY["pre_scan_inbox"]["function"]
        raw = fn(max_messages=10)
        envelope = json.loads(raw)
        assert envelope["ok"] is True
        needs_you = envelope["data"]["needs_you"]
        assert needs_you, "expected the urgent message to surface in needs_you"
        item = next((it for it in needs_you if it["message_id"] == "urgent1"), None)
        assert item is not None, needs_you
        assert item["detail"], "detail should have been filled by the LLM extraction pass"
        # #2743 Increment 3: the extracted text is re-wrapped in the same
        # untrusted-input delimiters that cover a raw body read, since it
        # re-enters THIS agent's own tool-result context.
        assert item["detail"][0].startswith(UNTRUSTED_BODY_OPEN)
        assert item["detail"][0].endswith(UNTRUSTED_BODY_CLOSE)
    finally:
        agent.close_db()


def test_pre_scan_inbox_tool_never_fails_the_scan_on_extraction_failure(tmp_path, monkeypatch):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    msg = _msg("urgent1", subject="URGENT: prod incident", sender="oncall@example.com")
    msg["labelIds"] = ["INBOX", "IMPORTANT"]
    backend = FakeGmailBackend(user_email="me@example.com")
    backend.add_message(msg)
    agent = _build_agent(tmp_path, monkeypatch, mail_backend=backend)
    try:
        agent.chat = _RaisingChat()
        fn = _TOOL_REGISTRY["pre_scan_inbox"]["function"]
        raw = fn(max_messages=10)
        envelope = json.loads(raw)
        assert envelope["ok"] is True
        needs_you = envelope["data"]["needs_you"]
        item = next((it for it in needs_you if it["message_id"] == "urgent1"), None)
        assert item is not None
        assert item["detail"] == []
    finally:
        agent.close_db()
