# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``search_messages`` precomputed count/truncated/operator_retry (#2756).

The model is asked today to count/enumerate ``search_messages``'s
``messages`` list itself and gets it wrong. This issue adds three fields
to the envelope, computed at the WRAPPER (registered ``@tool`` closure)
layer -- not ``search_messages_impl``, whose return value the wrapper
today discards except for its ``"messages"`` key:

- ``count``: exact, ``len(messages)`` in the same envelope.
- ``truncated``: derived ONLY from the Gmail provider's real pagination
  cursor (``nextPageToken``) -- NEVER from ``len(stubs) == max_results``.
  A sender with exactly ``max_results`` matches and no ``nextPageToken``
  must report ``truncated: False`` -- the anti-heuristic regression case
  and the most important test below.
- ``operator_retry``: already computed by ``search_messages_impl`` (the
  zero-hit-then-operatorized-retry path) but currently dropped by the
  wrapper before it reaches the model.

TDD split (red/green): none of ``count``, ``truncated``, or forwarded
``operator_retry`` exist on the registered ``search_messages`` tool today
-- every test in this file is RED, either via a missing-key ``KeyError``
(most tests) or an assertion that the naive ``len(stubs) == max_results``
heuristic would satisfy but the correct cursor-derived contract does not
(the AC2 test).

Deliberately tests through the REGISTERED tool closure (never
``search_messages_impl`` directly) -- the wrapper is what discards
``operator_retry`` and is where ``count``/``truncated`` must be computed,
so calling ``_impl`` alone would false-green the bug this issue fixes.

Hermetic: ``FakeGmailBackend`` (and small local subclasses of it) only,
no Lemonade, no network.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

# ---------------------------------------------------------------------------
# Path / import bootstrap (mirrors test_read_tools_list_inbox_budget_2514.py)
# ---------------------------------------------------------------------------

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/, [4] = hub/,
# [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    ReadToolsMixin,
    operatorize_query,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture helpers (adapted from test_read_tools_list_inbox_budget_2514.py)
# ---------------------------------------------------------------------------


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg_with_body(msg_id: str, body_text: str, **overrides: Any) -> Dict[str, Any]:
    """Minimal Gmail API v1 message dict with a single-part text/plain body.

    Bodies are kept short (unlike the #2514 budget-test fixtures) --
    count/truncated/operator_retry are orthogonal to envelope-budget
    shrinkage, so these fixtures stay well clear of that code path.
    """
    msg: Dict[str, Any] = {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": ["INBOX"],
        "snippet": body_text[:200],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "filename": "",
            "headers": [
                {"name": "Subject", "value": "Test"},
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "user@example.com"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
            ],
            "body": {
                "data": _b64url(body_text),
                "size": len(body_text.encode("utf-8")),
            },
        },
        "sizeEstimate": len(body_text),
    }
    msg.update(overrides)
    return msg


def _seed_backend(n: int, *, id_prefix: str = "m") -> FakeGmailBackend:
    """Seed a plain ``FakeGmailBackend`` with ``n`` distinct INBOX messages."""
    gmail = FakeGmailBackend(user_email="user@example.com")
    base_date = 1_800_000_000_000
    for i in range(n):
        msg_id = f"{id_prefix}{i}"
        gmail.add_message(
            _msg_with_body(
                msg_id,
                "hello world",
                threadId=msg_id,
                internalDate=str(base_date - i),
            )
        )
    return gmail


class _PagedGmailBackend(FakeGmailBackend):
    """Injects a truthy ``nextPageToken`` on every ``list_messages`` call.

    Mirrors ``_MorePagesGmailBackend`` in ``test_attention_tools.py`` --
    ``FakeGmailBackend`` itself always returns ``nextPageToken: None``, so a
    real "more results exist" signal needs a small local override.
    """

    def list_messages(self, **kwargs: Any) -> Dict[str, Any]:
        out = dict(super().list_messages(**kwargs))
        out["nextPageToken"] = "more-results-token"
        return out


class _ExactQueryGmailBackend(FakeGmailBackend):
    """Routes ``list_messages`` by exact query-string match.

    Mirrors ``_RecordingBackend`` in ``test_search_operator_retry.py`` --
    ``operatorize_query`` produces an ``from:(...) OR subject:(...)`` shape
    that ``FakeGmailBackend``'s real (AND-of-tokens) query tokenizer cannot
    parse, so these retry tests route by literal string instead.
    """

    def __init__(self, *args: Any, hits_for: Dict[str, List[str]], **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._hits_for = hits_for

    def list_messages(
        self, *, query: Any = None, max_results: int = 25, **kwargs: Any
    ) -> Dict[str, Any]:
        ids = self._hits_for.get(query, [])
        return {
            "messages": [{"id": mid, "threadId": mid} for mid in ids[:max_results]],
            "nextPageToken": None,
            "resultSizeEstimate": len(ids),
        }


# ---------------------------------------------------------------------------
# Minimal tool-hosting stand-in (copied as-is from
# test_read_tools_list_inbox_budget_2514.py's ``_Host`` -- the established
# pattern for exercising a registered read-tool closure)
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


def _registered_search_messages(host: _Host):
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    assert "search_messages" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["search_messages"]["function"]


def _call(search_messages, **kwargs) -> Dict[str, Any]:
    payload = json.loads(search_messages(**kwargs))
    assert payload["ok"] is True, payload
    return payload["data"]


# ---------------------------------------------------------------------------
# AC1 -- exact count under the ceiling
# ---------------------------------------------------------------------------


class TestCountUnderCeiling:
    def test_count_matches_len_messages_and_is_untruncated(self):
        gmail = _seed_backend(12)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(search_messages, query="", max_results=25)

        assert data["count"] == 12
        assert data["count"] == len(data["messages"])
        assert data["truncated"] is False


# ---------------------------------------------------------------------------
# AC2 -- anti-heuristic regression: hitting the ceiling with no real cursor
# must NOT be reported as truncated. This is the single most important
# assertion in this file: it fails under a naive
# ``len(stubs) == max_results`` implementation (which this exact fixture
# satisfies -- 25 seeded messages, max_results=25) and only passes when
# ``truncated`` is derived from the backend's real ``nextPageToken``, which
# plain ``FakeGmailBackend`` always reports as ``None``.
# ---------------------------------------------------------------------------


class TestTruncatedFalseWhenCeilingExactlyMatchesNoRealCursor:
    def test_ceiling_hit_with_no_next_page_token_is_not_truncated(self):
        gmail = _seed_backend(25)
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(search_messages, query="", max_results=25)

        assert data["count"] == 25
        # The anti-heuristic guard: len(messages) == max_results here, but
        # the backend never signaled a real next page -- truncated must be
        # False, not derived from the count/ceiling coincidence.
        assert data["truncated"] is False


# ---------------------------------------------------------------------------
# AC2 (positive case) -- a genuine backend-signaled cursor DOES mean
# truncated, even well under the requested ceiling.
# ---------------------------------------------------------------------------


class TestTruncatedTrueWhenProviderSignalsMorePages:
    def test_real_next_page_token_marks_truncated(self):
        gmail = _PagedGmailBackend(user_email="user@example.com")
        for i in range(5):
            msg_id = f"m{i}"
            gmail.add_message(_msg_with_body(msg_id, "hello world", threadId=msg_id))
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(search_messages, query="", max_results=25)

        assert data["count"] == 5
        assert data["truncated"] is True


# ---------------------------------------------------------------------------
# AC5 -- zero hits: count 0, empty messages, untruncated, and the retry
# mechanism still runs (and is forwarded) even when the retry itself finds
# nothing.
# ---------------------------------------------------------------------------


class TestZeroHits:
    def test_zero_hits_reports_zero_count_and_forwards_the_attempted_retry(self):
        literal_query = "totally absent phrase nobody sent"
        retry_query = operatorize_query(literal_query)
        gmail = _ExactQueryGmailBackend(user_email="user@example.com", hits_for={})
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(search_messages, query=literal_query, max_results=25)

        assert data["count"] == 0
        assert data["messages"] == []
        assert data["truncated"] is False
        # The retry mechanism ran (the literal query has no operator and
        # zero hits) even though it also found nothing -- the wrapper must
        # still forward that it was attempted, not silently drop it.
        assert data["operator_retry"] == retry_query


# ---------------------------------------------------------------------------
# AC3 -- operator_retry round-trips through the REGISTERED tool when the
# retry actually finds a hit the literal query missed.
# ---------------------------------------------------------------------------


class TestOperatorRetryRoundTripsThroughTheRegisteredTool:
    def test_operator_retry_forwarded_with_hits_found_on_retry(self):
        literal_query = "Netflix promotional email"
        retry_query = operatorize_query(literal_query)
        gmail = _ExactQueryGmailBackend(
            user_email="user@example.com", hits_for={retry_query: ["hit1"]}
        )
        gmail.add_message(_msg_with_body("hit1", "hello world", threadId="hit1"))
        host = _Host(gmail)
        search_messages = _registered_search_messages(host)

        data = _call(search_messages, query=literal_query, max_results=25)

        assert isinstance(data["operator_retry"], str)
        assert data["operator_retry"] == retry_query
        assert data["operator_retry"]
        assert data["count"] == 1
        assert len(data["messages"]) == 1


# ---------------------------------------------------------------------------
# AC4 -- the LLM-facing docstring must instruct the model to trust the
# precomputed count/enumeration rather than re-deriving it. Load-bearing:
# ``tools.py`` stores ``f.__doc__`` verbatim as the tool's description sent
# to the model, so this wording is functionally part of the contract.
# ---------------------------------------------------------------------------


class TestDocstringInstructsTrustingThePrecomputedFields:
    def test_description_mentions_state_this_number_verbatim(self):
        _TOOL_REGISTRY.clear()
        host = _Host(_seed_backend(1))
        host._register_read_tools()

        description = _TOOL_REGISTRY["search_messages"]["description"]

        assert "state this number verbatim" in description

    def test_description_mentions_report_every_entry(self):
        _TOOL_REGISTRY.clear()
        host = _Host(_seed_backend(1))
        host._register_read_tools()

        description = _TOOL_REGISTRY["search_messages"]["description"]

        assert "REPORT EVERY ENTRY" in description


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
