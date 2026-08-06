# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``get_thread`` table render-card tests for #2765.

A real 8-message thread came back from the agent with a duplicated message,
another replaced by a repeat of an earlier one, a misattributed sender, and
an invented timestamp (``11:40 AM +0000``) that existed nowhere in the
mailbox -- even though ``get_thread_impl``'s payload was already ordered,
numbered, and carried each message's real ``from``/``date`` (#2531, merged
2026-07-28, six days before this issue was filed). The fabrication happened
in the model's own free-composed prose, upstream of the tool payload -- so a
docstring instruction alone cannot fix it: the payload was already complete
and correct and the model invented anyway.

The fix: ``get_thread`` now hands the chat surface a ``kind: "table"`` card
(the pre-existing generic render primitive -- no new TUI/Go code, see
``tui/internal/ui/cards/primitives.go::renderTable``) built directly from
the SAME per-message ``from``/``date``/``index`` fields the model reads. The
surface draws the card straight from tool data, never from model prose, so
a fabricated sender or timestamp is structurally impossible in the
rendered card regardless of anything the model goes on to say.

Layer isolation: ``get_thread_impl`` itself is UNCHANGED by this issue (see
``test_get_thread_chronology_2531.py`` / ``test_read_tools_thread_budget.py``,
both still green, unmodified) -- every test here exercises either the new
pure ``_thread_table_card`` projection or the REGISTERED ``get_thread`` tool
closure, which is where the card fields are merged into the envelope.

Hermetic: ``FakeGmailBackend`` only, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/, [4] = hub/,
# [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    ReadToolsMixin,
    _thread_table_card,
    get_thread_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers (mirrors test_get_thread_chronology_2531.py)
# ---------------------------------------------------------------------------


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


_ALICE = "alice@example.com"
_BOB = "bob@example.org"
_THREAD_ID = "thread-verbatim-card"

# 5 messages, strict sent/received alternation -- the exact shape the issue
# says makes ordering/attribution errors obvious.
_BASE_MS = 1_800_000_000_000
_MESSAGES: List[Dict[str, Any]] = [
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
        ],
        start=1,
    )
]


def _seeded_backend(msgs: List[Dict[str, Any]]) -> FakeGmailBackend:
    gmail = FakeGmailBackend(user_email="user@example.com")
    for m in msgs:
        gmail.add_message(m)
    return gmail


# ---------------------------------------------------------------------------
# Minimal tool-hosting stand-in (copied pattern from
# test_read_tools_thread_budget.py / test_search_messages_count_2756.py)
# ---------------------------------------------------------------------------


class _Host(ReadToolsMixin):
    """Minimal stand-in for EmailTriageAgent's tool-hosting surface."""

    def __init__(self, backend: FakeGmailBackend):
        self._gmail = backend
        self._backends = {"google": backend}
        self._message_mailbox: Dict[str, str] = {}
        self.config = SimpleNamespace(debug=False)

    def _remember_message_mailbox(self, message_id, provider):
        if message_id:
            self._message_mailbox[message_id] = provider

    def _backend_for_message(self, message_id, explicit_mailbox=None):
        provider = explicit_mailbox or self._message_mailbox.get(message_id)
        if provider is None:
            if len(self._backends) == 1:
                return next(iter(self._backends.values()))
            raise ValueError("ambiguous mailbox in test stub")
        backend = self._backends.get(provider)
        if backend is None:
            raise ValueError("mailbox not connected in test stub")
        return backend


def _registered_get_thread(host: _Host):
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    assert "get_thread" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["get_thread"]["function"]


def _call(get_thread, **kwargs) -> Dict[str, Any]:
    payload = json.loads(get_thread(**kwargs))
    assert payload["ok"] is True, payload
    return payload["data"]


# ---------------------------------------------------------------------------
# _thread_table_card -- pure projection, no agent/tool machinery
# ---------------------------------------------------------------------------


class TestThreadTableCardProjection:
    def test_kind_is_table(self):
        gmail = _seeded_backend(_MESSAGES)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)

        card = _thread_table_card(result)

        assert card["kind"] == "table"

    def test_columns_are_hash_from_date(self):
        gmail = _seeded_backend(_MESSAGES)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)

        card = _thread_table_card(result)

        assert card["columns"] == ["#", "From", "Date"]

    def test_row_count_matches_message_count(self):
        gmail = _seeded_backend(_MESSAGES)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)

        card = _thread_table_card(result)

        assert len(card["rows"]) == 5

    def test_every_row_is_verbatim_from_the_source_message_no_reconstruction(self):
        """The load-bearing assertion: each row's (index, from, date) must be
        an EXACT copy of the corresponding message's own fields -- never a
        reformatted, converted, or otherwise recomputed value. This is the
        direct regression guard for #2765's invented '11:40 AM +0000': that
        value cannot appear here because nothing in this path reformats a
        date string, it is only ever copied.
        """
        gmail = _seeded_backend(_MESSAGES)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)

        card = _thread_table_card(result)

        for row, message in zip(card["rows"], result["messages"]):
            assert row == [message["index"], message["from"], message["date"]]

    def test_ground_truth_cross_check_against_the_raw_seeded_headers(self):
        """Cross-checks the card against the RAW seeded fixture headers (not
        just against get_thread_impl's own output) -- ground truth fetched
        independently of the code path under test, per the run's evidence
        protocol.
        """
        gmail = _seeded_backend(_MESSAGES)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)
        card = _thread_table_card(result)

        raw_by_id = {m["id"]: m for m in _MESSAGES}
        for row, message in zip(card["rows"], result["messages"]):
            raw = raw_by_id[message["id"]]
            raw_headers = {
                h["name"].lower(): h["value"] for h in raw["payload"]["headers"]
            }
            assert row[1] == raw_headers["from"]
            assert row[2] == raw_headers["date"]

    def test_title_names_subject_and_count(self):
        gmail = _seeded_backend(_MESSAGES)
        result = get_thread_impl(gmail, thread_id=_THREAD_ID)

        card = _thread_table_card(result)

        assert "Contributing to GAIA" in card["title"]
        assert "5" in card["title"]

    def test_empty_thread_produces_a_valid_zero_row_card(self):
        card = _thread_table_card({"thread_id": "empty", "messages": []})

        assert card["kind"] == "table"
        assert card["rows"] == []


# ---------------------------------------------------------------------------
# Registered tool surface -- envelope shape + docstring contract
# ---------------------------------------------------------------------------


class TestRegisteredGetThreadEnvelopeCarriesTheCard:
    def test_envelope_data_has_kind_table(self):
        gmail = _seeded_backend(_MESSAGES)
        host = _Host(gmail)
        get_thread = _registered_get_thread(host)

        data = _call(get_thread, thread_id=_THREAD_ID)

        assert data["kind"] == "table"
        assert data["columns"] == ["#", "From", "Date"]
        assert len(data["rows"]) == 5

    def test_full_messages_list_is_still_present_for_the_model(self):
        """The card is additive -- the model must still get the full
        messages (with bodies) it needs to actually answer the user's
        question, not just the card's skeleton."""
        gmail = _seeded_backend(_MESSAGES)
        host = _Host(gmail)
        get_thread = _registered_get_thread(host)

        data = _call(get_thread, thread_id=_THREAD_ID)

        assert "messages" in data
        assert len(data["messages"]) == 5
        assert all("body" in m for m in data["messages"])

    def test_card_rows_match_the_messages_list_verbatim(self):
        gmail = _seeded_backend(_MESSAGES)
        host = _Host(gmail)
        get_thread = _registered_get_thread(host)

        data = _call(get_thread, thread_id=_THREAD_ID)

        for row, message in zip(data["rows"], data["messages"]):
            assert row == [message["index"], message["from"], message["date"]]


class TestDocstringInstructsCardAwarenessAndVerbatimFields:
    def test_description_mentions_verbatim(self):
        _TOOL_REGISTRY.clear()
        host = _Host(_seeded_backend(_MESSAGES))
        host._register_read_tools()

        description = _TOOL_REGISTRY["get_thread"]["description"] or ""

        assert "VERBATIM" in description

    def test_description_tells_the_model_not_to_relist_the_card(self):
        _TOOL_REGISTRY.clear()
        host = _Host(_seeded_backend(_MESSAGES))
        host._register_read_tools()

        description = _TOOL_REGISTRY["get_thread"]["description"] or ""
        normalized = " ".join(description.split())

        assert "already visible to the user" in normalized

    def test_description_still_mentions_truncated_marker(self):
        """Regression guard for #2073's AC4 (test_read_tools_thread_budget.py
        S1) -- the pre-existing truncation-marker instruction must survive
        this docstring rewrite."""
        _TOOL_REGISTRY.clear()
        host = _Host(_seeded_backend(_MESSAGES))
        host._register_read_tools()

        description = _TOOL_REGISTRY["get_thread"]["description"] or ""

        assert "[truncated]" in description


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
