# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``search_messages``'s automatic body-escalation selector (#3773).

#2763 made ``include_bodies`` default to ``False`` because a 4B-class local
model would not reliably opt IN to bodies for a content question. But that
left the opposite failure standing: a content question ("who signed this",
"what date was agreed") routed through the metadata-only default came back
unanswerable even when the answer was in the mailbox, because nothing ever
escalates to a body without the model setting ``include_bodies=True`` by
hand.

#3773 replaces the model's opt-in with a deterministic selector over the
query the model already had to write: ``_query_has_free_text`` treats a
query carrying a term beyond Gmail's structural operators (``from:``,
``is:``, ``label:``, date operators, ...) as a content search -- a bare
term only matches because Gmail searched subject+body text -- and
``search_messages_impl`` escalates that candidate set to full bodies
automatically, but ONLY when it is already narrowed to
``SEARCH_AUTO_BODY_CAP`` messages or fewer (never reproducing the #2763
overflow for a content-shaped query that still matches many messages).

This file pins the SELECTOR, not just a value:
- WHICH queries escalate (free-text-bearing, small result set) and which
  do not (pure operator filter; free-text but too many hits).
- That a metadata-answerable (pure-filter/counting) request escalates
  NONE of its results, ever, regardless of how few hits it has.
- That an explicit ``include_bodies=True``/``False`` always overrides the
  selector and is never capped by ``SEARCH_AUTO_BODY_CAP``.

Hermetic: ``FakeGmailBackend`` only, no Lemonade, no network.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    SEARCH_AUTO_BODY_CAP,
    ReadToolsMixin,
    _query_has_free_text,
    search_messages_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(msg_id: str, *, subject: str, sender: str, body: str, **overrides: Any):
    m: Dict[str, Any] = {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": body[:200],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "user@example.com"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
            ],
            "body": {"data": _b64url(body), "size": len(body.encode("utf-8"))},
        },
        "sizeEstimate": len(body),
    }
    m.update(overrides)
    return m


def _build_inbox(n: int, *, body_text: str = "The contract was signed by Dana."):
    gmail = FakeGmailBackend(user_email="user@example.com")
    base_date = 1_800_000_000_000
    msgs: List[Dict[str, Any]] = []
    for i in range(n):
        m = _msg(
            f"m{i}",
            subject=f"Re: contract renewal {i}",
            sender="vendor@example.com",
            body=body_text,
            threadId=f"m{i}",
            internalDate=str(base_date - i),
        )
        gmail.add_message(m)
        msgs.append(m)
    return gmail, msgs


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
        backend = self._backends.get(provider)
        if backend is None:
            raise ValueError("mailbox not connected in test stub")
        return backend


def _registered_search_messages(host: _Host):
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    return _TOOL_REGISTRY["search_messages"]["function"]


def _call(search_messages, **kwargs) -> Dict[str, Any]:
    payload = json.loads(search_messages(**kwargs))
    assert payload["ok"] is True, payload
    return payload["data"]


def _payload_plain_text(msg: Dict[str, Any]) -> str:
    """Flatten inline text/plain/html payload body content into one string."""
    payload = (msg.get("payload") or {})
    body = payload.get("body") or {}
    parts = payload.get("parts") or []

    def walk(part: Dict[str, Any]) -> str:
        mime = (part.get("mimeType") or "").lower()
        part_body = part.get("body") or {}
        if mime.startswith("multipart/"):
            chunks = [walk(child) for child in part.get("parts") or []]
            return "\n".join(c for c in chunks if c)
        if mime in {"text/plain", "text/html"}:
            raw_b64 = part_body.get("data")
            if not raw_b64:
                return ""
            raw = base64.urlsafe_b64decode(raw_b64 + "=" * (-len(raw_b64) % 4))
            return raw.decode("utf-8", errors="replace")
        return ""

    return walk(payload) if payload else ""


def test_vendor_corpus_treats_quoted_and_unquoted_phrases_the_same(
    corpus_inbox_path,
):
    """A quoted phrase must behave like the bare phrase, and body-only hits
    must be reachable by the fake Gmail backend used in offline evals."""
    gmail = FakeGmailBackend(corpus_inbox_path)

    bare_hits = gmail.list_messages(query="kernel fusion", max_results=100)
    quoted_hits = gmail.list_messages(query='"kernel fusion"', max_results=100)

    assert bare_hits["resultSizeEstimate"] > 0
    assert {m["id"] for m in bare_hits["messages"]} == {
        m["id"] for m in quoted_hits["messages"]
    }

    body_only_for_phrase = [
        msg
        for msg in gmail._messages.values()
        if "kernel fusion" in _payload_plain_text(msg).lower()
        and "kernel fusion" not in (msg.get("payload") or {}).get("headers", [])
    ]
    assert body_only_for_phrase
    assert {m["id"] for m in bare_hits["messages"]}.issuperset(
        {msg["id"] for msg in body_only_for_phrase}
    )


# ---------------------------------------------------------------------------
# _query_has_free_text: the deterministic classifier itself
# ---------------------------------------------------------------------------


class TestQueryHasFreeText:
    @pytest.mark.parametrize(
        "query",
        [
            "from:every",
            "from:boss@example.com is:unread newer_than:7d",
            "subject:invoice",
            "label:promotions is:read",
            "from:(john smith) is:unread",
            "-from:noreply is:unread",
            "",
        ],
    )
    def test_pure_operator_queries_have_no_free_text(self, query: str):
        assert _query_has_free_text(query) is False

    @pytest.mark.parametrize(
        "query",
        [
            "contract renewal",
            "from:acme contract renewal",
            '"Netflix promotional email"',
            "who signed the agreement",
        ],
    )
    def test_bare_terms_are_free_text(self, query: str):
        assert _query_has_free_text(query) is True


# ---------------------------------------------------------------------------
# search_messages_impl: the selector applied to a real candidate set
# ---------------------------------------------------------------------------


class TestAutoEscalationSelector:
    def test_content_query_on_a_small_result_set_escalates_all_of_it(self):
        gmail, msgs = _build_inbox(3, body_text="The contract was signed by Dana.")

        result = search_messages_impl(gmail, query="contract renewal", max_results=25)

        assert result["include_bodies_auto"] is True
        assert len(result["messages"]) == 3
        for m in result["messages"]:
            assert "body" in m
            assert "Dana" in m["body"]

    def test_pure_filter_query_never_escalates_even_with_one_hit(self):
        """The metadata-answerable case (AC): a counting/listing request
        escalates NONE of its results, regardless of candidate-set size."""
        gmail, msgs = _build_inbox(1)

        result = search_messages_impl(gmail, query="from:vendor", max_results=25)

        assert result["include_bodies_auto"] is False
        assert len(result["messages"]) == 1
        assert "body" not in result["messages"][0]

    def test_content_query_above_the_cap_falls_back_to_metadata_only(self):
        n = SEARCH_AUTO_BODY_CAP + 1
        gmail, msgs = _build_inbox(n)

        result = search_messages_impl(gmail, query="contract renewal", max_results=25)

        assert result["include_bodies_auto"] is False
        assert len(result["messages"]) == n
        for m in result["messages"]:
            assert "body" not in m

    def test_content_query_at_exactly_the_cap_still_escalates(self):
        n = SEARCH_AUTO_BODY_CAP
        gmail, msgs = _build_inbox(n)

        result = search_messages_impl(gmail, query="contract renewal", max_results=25)

        assert result["include_bodies_auto"] is True
        assert len(result["messages"]) == n
        for m in result["messages"]:
            assert "body" in m

    def test_explicit_include_bodies_true_overrides_selector_uncapped(self):
        """An explicit True bypasses the cap entirely -- pre-#3773 behavior
        for a caller that already knows it wants full bodies."""
        n = SEARCH_AUTO_BODY_CAP + 5
        gmail, msgs = _build_inbox(n)

        result = search_messages_impl(
            gmail, query="from:vendor", max_results=25, include_bodies=True
        )

        assert result["include_bodies_auto"] is False  # caller asked, not the heuristic
        assert len(result["messages"]) == n
        for m in result["messages"]:
            assert "body" in m

    def test_explicit_include_bodies_false_overrides_a_content_query(self):
        gmail, msgs = _build_inbox(2, body_text="The contract was signed by Dana.")

        result = search_messages_impl(
            gmail,
            query="contract renewal",
            max_results=25,
            include_bodies=False,
        )

        assert result["include_bodies_auto"] is False
        for m in result["messages"]:
            assert "body" not in m


# ---------------------------------------------------------------------------
# Wire-level: the registered @tool wrapper honors the same selector
# ---------------------------------------------------------------------------


class TestRegisteredToolAppliesTheSameSelector:
    def test_content_question_answered_without_the_model_passing_include_bodies(self):
        gmail, msgs = _build_inbox(2, body_text="The contract was signed by Dana.")
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(search_messages, query="contract renewal", max_results=25)

        assert data["include_bodies_auto"] is True
        assert any("Dana" in m.get("body", "") for m in data["messages"])

    def test_counting_question_never_escalates_at_the_wrapper(self):
        gmail, msgs = _build_inbox(2)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(search_messages, query="from:vendor", max_results=25)

        assert data["include_bodies_auto"] is False
        for m in data["messages"]:
            assert "body" not in m


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
