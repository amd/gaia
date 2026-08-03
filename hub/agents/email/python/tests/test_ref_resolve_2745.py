# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2745 — resolve positional references ("reply to 1") to the message a
triage card actually shows.

The ``needs_you`` card's ``ref`` field (``contract.NeedsYouItem.ref``, kept
as-is by this issue) is a 1-based row number that is stable within ONE card
render only — a rescan re-orders and renumbers by design. Before this issue
the agent had no deterministic way to turn "1" back into a real message; it
had to guess from context, and a wrong guess on a reply/archive/accept is
visible to someone else and can't be undone. This issue adds a small,
verb-agnostic resolver: ``ref number -> {message_id, subject, sender, kind,
thread_id, mailbox}`` from the CURRENT card, refusing (never guessing) on
anything stale, out of range, or otherwise unresolvable.

Scope note (already decided by the sibling #2743 correction, not
re-litigated here): ``NeedsYouItem.detail`` ships as an always-empty list on
this branch's base, so none of the fixtures below populate it.

TDD split: every symbol imported below (``gaia_agent_email.tools.ref_resolve``
and its exports) does not exist yet on this branch -- the whole module is RED
until a later commit implements it.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

# parents[0]=tests/, [1]=python/, [2]=email/, [3]=agents/, [4]=hub/, [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.ref_resolve import (  # noqa: E402
    RefResolutionError,
    RefResolveToolsMixin,
    resolve_needs_you_ref,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _needs_you_row(
    ref: int,
    message_id: Optional[str],
    *,
    subject: str = "",
    sender: str = "",
    kind: str = "needs_review",
    thread_id: Optional[str] = None,
    mailbox: Optional[str] = None,
) -> Dict[str, Any]:
    """A plain dict shaped like ``NeedsYouItem`` -- ``detail`` always empty
    per the #2743 correction this issue's tests must not contradict."""
    return {
        "ref": ref,
        "kind": kind,
        "message_id": message_id,
        "thread_id": thread_id,
        "sender": sender,
        "subject": subject,
        "age_seconds": None,
        "why": "test fixture row",
        "detail": [],
        "due_hint": None,
        "mailbox": mailbox,
    }


# Mirrors the issue's own JSON example.
THREE_ROW_CARD: List[Dict[str, Any]] = [
    _needs_you_row(
        1,
        "MSG_A",
        subject="Re: Q3 contract review",
        sender="Sarah Chen",
        kind="waiting_on_you",
        thread_id="THREAD_A",
    ),
    _needs_you_row(
        2,
        "MSG_B",
        subject="Meeting Thu 9am?",
        sender="Marcus Webb",
        kind="meeting_request",
        thread_id="THREAD_B",
    ),
    _needs_you_row(
        3,
        "MSG_C",
        subject="F-Bombs",
        sender="NOTUS",
        kind="needs_review",
        thread_id="THREAD_C",
    ),
]


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str = "Neutral subject, no keyword signal",
    sender: str = "alice@example.com",
    internal_date: str = "1750000000000",
    body: str = "Some neutral body content with no keyword signal at all.",
) -> Dict[str, Any]:
    """Minimal Gmail v1 message that lands in ``needs_review`` -> ``needs_you``
    under pure heuristic classification (no SLM configured) -- see
    ``test_needs_you_2743.py``'s identically-shaped helper and its
    ``_seed()`` fixture, which pins this exact fall-through behavior."""
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": body[:200],
        "internalDate": internal_date,
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


def _registered_tool(name: str):
    return _TOOL_REGISTRY[name]["function"]


# ---------------------------------------------------------------------------
# AC1 -- happy path: resolve a ref against the current card
# ---------------------------------------------------------------------------


class TestResolveHappyPath:
    def test_resolves_ref_1_to_the_matching_item(self):
        resolved = resolve_needs_you_ref(THREE_ROW_CARD, "1")
        assert resolved["message_id"] == "MSG_A"
        assert resolved["subject"] == "Re: Q3 contract review"
        assert resolved["sender"] == "Sarah Chen"
        assert resolved["ref"] == 1

    def test_resolves_ref_2_and_ref_3_to_their_own_rows_not_ref_1s(self):
        resolved_2 = resolve_needs_you_ref(THREE_ROW_CARD, "2")
        assert resolved_2["message_id"] == "MSG_B"
        assert resolved_2["subject"] == "Meeting Thu 9am?"
        assert resolved_2["sender"] == "Marcus Webb"

        resolved_3 = resolve_needs_you_ref(THREE_ROW_CARD, "3")
        assert resolved_3["message_id"] == "MSG_C"
        assert resolved_3["subject"] == "F-Bombs"
        assert resolved_3["sender"] == "NOTUS"

    def test_resolved_dict_carries_kind_thread_id_and_mailbox(self):
        resolved = resolve_needs_you_ref(THREE_ROW_CARD, "1")
        assert resolved["kind"] == "waiting_on_you"
        assert resolved["thread_id"] == "THREAD_A"
        assert resolved["mailbox"] is None


# ---------------------------------------------------------------------------
# AC2 -- unresolvable references ask, never guess
# ---------------------------------------------------------------------------


class TestRefusalNeverGuesses:
    def test_out_of_range_ref_raises(self):
        with pytest.raises(RefResolutionError):
            resolve_needs_you_ref(THREE_ROW_CARD, "9")

    def test_no_card_none_raises(self):
        with pytest.raises(RefResolutionError):
            resolve_needs_you_ref(None, "1")

    def test_no_card_empty_list_raises(self):
        with pytest.raises(RefResolutionError):
            resolve_needs_you_ref([], "1")

    def test_non_numeric_ref_raises(self):
        with pytest.raises(RefResolutionError):
            resolve_needs_you_ref(THREE_ROW_CARD, "abc")

    def test_zero_ref_raises(self):
        with pytest.raises(RefResolutionError):
            resolve_needs_you_ref(THREE_ROW_CARD, "0")

    def test_negative_ref_raises(self):
        with pytest.raises(RefResolutionError):
            resolve_needs_you_ref(THREE_ROW_CARD, "-1")

    def test_matching_row_with_no_message_id_refuses_rather_than_returning_none(
        self,
    ):
        """A carried-over action item with no recoverable source message
        (``NeedsYouItem.message_id`` documents this shape explicitly) must
        refuse, never hand back ``message_id: None`` as if it were a valid
        resolution a caller could act on."""
        card = [
            _needs_you_row(
                1,
                None,
                subject="Follow up on the renewal",
                kind="action_item",
            )
        ]
        with pytest.raises(RefResolutionError):
            resolve_needs_you_ref(card, "1")


# ---------------------------------------------------------------------------
# AC3 -- resolution always uses the CURRENT card, never a stale one
# ---------------------------------------------------------------------------


class TestCurrentCardOnly:
    def test_same_ref_resolves_differently_against_a_rescanned_card(self):
        first = resolve_needs_you_ref(THREE_ROW_CARD, "1")
        assert first["message_id"] == "MSG_A"

        # Simulates a rescan that found older mail and re-numbered: what was
        # ref 3 is now ref 1.
        rescanned_card = [
            _needs_you_row(
                1,
                "MSG_C",
                subject="F-Bombs",
                sender="NOTUS",
                kind="needs_review",
                thread_id="THREAD_C",
            ),
            _needs_you_row(
                2,
                "MSG_A",
                subject="Re: Q3 contract review",
                sender="Sarah Chen",
                kind="waiting_on_you",
                thread_id="THREAD_A",
            ),
            _needs_you_row(
                3,
                "MSG_B",
                subject="Meeting Thu 9am?",
                sender="Marcus Webb",
                kind="meeting_request",
                thread_id="THREAD_B",
            ),
        ]
        second = resolve_needs_you_ref(rescanned_card, "1")
        assert second["message_id"] == "MSG_C"
        assert second["message_id"] != first["message_id"], (
            "resolving the same ref number against a different (rescanned) "
            "card must never return the stale card's match"
        )


# ---------------------------------------------------------------------------
# AC4 -- tool-level: the registered `resolve_needs_you_reference` tool
# ---------------------------------------------------------------------------


class _RefHost(RefResolveToolsMixin):
    """Minimal stand-in for EmailTriageAgent's tool-hosting surface -- the
    ``_Host(ReadToolsMixin)`` pattern from
    ``test_read_tools_thread_budget.py``, mirrored for the ref-resolve
    mixin's much smaller state surface."""

    def __init__(self):
        self._last_needs_you_card = None
        self.config = SimpleNamespace(debug=False)


def _registered_resolve_tool(host: "_RefHost"):
    _TOOL_REGISTRY.clear()
    host._register_ref_resolve_tools()
    assert "resolve_needs_you_reference" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["resolve_needs_you_reference"]["function"]


class TestResolveToolRegistration:
    def test_tool_resolves_against_the_hosts_current_card(self):
        host = _RefHost()
        host._last_needs_you_card = THREE_ROW_CARD
        resolve_tool = _registered_resolve_tool(host)

        envelope = json.loads(resolve_tool(ref=1))
        assert envelope["ok"] is True
        assert envelope["data"]["message_id"] == "MSG_A"

    def test_tool_refuses_when_no_card_is_set(self):
        host = _RefHost()
        assert host._last_needs_you_card is None
        resolve_tool = _registered_resolve_tool(host)

        envelope = json.loads(resolve_tool(ref=1))
        assert envelope["ok"] is False
        assert isinstance(envelope.get("error"), str) and envelope["error"], (
            "a refusal must carry an actionable error string, never a bare "
            "False with no explanation"
        )


# ---------------------------------------------------------------------------
# AC5 -- integration: pre_scan_inbox stores the card it just built
# ---------------------------------------------------------------------------


def _make_agent(tmp_path, gmail: FakeGmailBackend):
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=gmail,
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
    )
    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


class TestPreScanStoresCardForResolve:
    def test_pre_scan_inbox_tool_updates_agents_last_needs_you_card(self, tmp_path):
        gmail = FakeGmailBackend(user_email="me@example.com")
        gmail.add_message(_msg("row-1", internal_date="1700000000000"))
        gmail.add_message(_msg("row-2", internal_date="1710000000000"))

        agent = _make_agent(tmp_path, gmail)
        try:
            assert agent._last_needs_you_card is None, (
                "must start unset until the first pre_scan_inbox call this " "session"
            )

            pre_scan = _registered_tool("pre_scan_inbox")
            envelope = json.loads(pre_scan())
            assert envelope["ok"] is True
            needs_you = envelope["data"]["needs_you"]
            assert len(needs_you) >= 2, (
                "test setup assumption violated: both seeded messages must "
                f"land in needs_you, got {needs_you!r}"
            )

            assert agent._last_needs_you_card is not None
            stored_ids = {item["message_id"] for item in agent._last_needs_you_card}
            envelope_ids = {item["message_id"] for item in needs_you}
            assert stored_ids == envelope_ids, (
                "agent._last_needs_you_card must match the needs_you list "
                "the tool just returned, not a stale/partial snapshot"
            )
        finally:
            agent.close_db()

    def test_resolve_after_pre_scan_finds_a_message_from_that_scan(self, tmp_path):
        """End-to-end: scan, then resolve ref 1 against the card the scan
        just produced -- the message_id must be one this scan actually
        surfaced, not a guess."""
        gmail = FakeGmailBackend(user_email="me@example.com")
        gmail.add_message(_msg("row-1", internal_date="1700000000000"))
        gmail.add_message(_msg("row-2", internal_date="1710000000000"))

        agent = _make_agent(tmp_path, gmail)
        try:
            pre_scan = _registered_tool("pre_scan_inbox")
            json.loads(pre_scan())

            resolved = resolve_needs_you_ref(agent._last_needs_you_card, "1")
            surfaced_ids = {item["message_id"] for item in agent._last_needs_you_card}
            assert resolved["message_id"] in surfaced_ids
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# AC6 -- system-prompt guidance sanity check (substring only, not semantic)
# ---------------------------------------------------------------------------


class TestSystemPromptMentionsResolveTool:
    def test_system_prompt_mentions_resolve_needs_you_reference(self):
        from gaia_agent_email import agent as agent_module

        assert "resolve_needs_you_reference" in agent_module._SYSTEM_PROMPT, (
            "the system prompt must tell the model to call "
            "resolve_needs_you_reference for positional card references "
            "(e.g. 'reply to 1') instead of guessing"
        )
