# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2114 — search operator handling.

A verbatim user phrase ("Netflix promotional email") passed literally to
Gmail returns zero hits even when the message is present; a ``from:``
operator finds it. ``search_messages_impl`` must retry a bare-phrase
zero-result query once as an operator query, and must NOT second-guess a
query that already uses operators.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    has_gmail_operator,
    operatorize_query,
    search_messages_impl,
)


class _RecordingBackend:
    """Fake Gmail backend that records queries and returns canned hits.

    ``hits_for`` maps a query string to the stub list returned for it; any
    query not in the map returns zero messages.
    """

    def __init__(self, hits_for: Dict[str, List[dict]]):
        self.hits_for = hits_for
        self.queries: List[str] = []

    def list_messages(self, *, query: str, max_results: int = 25) -> Dict[str, Any]:
        self.queries.append(query)
        return {"messages": self.hits_for.get(query, [])}

    def get_message(self, msg_id: str) -> Dict[str, Any]:
        return {
            "id": msg_id,
            "payload": {"headers": [{"name": "Subject", "value": "hit"}]},
        }


def test_has_gmail_operator():
    assert has_gmail_operator("from:netflix")
    assert has_gmail_operator("is:unread newer_than:7d")
    assert has_gmail_operator("subject:invoice")
    assert not has_gmail_operator("Netflix promotional email")
    assert not has_gmail_operator("budget exceeded alert")


def test_operatorize_query_shape():
    q = operatorize_query("Netflix promotional email")
    assert (
        q == "from:(Netflix promotional email) OR subject:(Netflix promotional email)"
    )


def test_literal_phrase_zero_hits_retries_as_operator():
    retry_q = operatorize_query("Netflix promo")
    backend = _RecordingBackend(hits_for={retry_q: [{"id": "m1"}]})
    result = search_messages_impl(backend, query="Netflix promo", max_results=25)
    # First the literal phrase (zero hits), then the operator retry (found it).
    assert backend.queries == ["Netflix promo", retry_q]
    assert result["operator_retry"] == retry_q
    assert len(result["messages"]) == 1


def test_operator_query_never_retried():
    """A query that already uses an operator is trusted — no retry."""
    backend = _RecordingBackend(hits_for={})  # from:netflix returns nothing
    result = search_messages_impl(backend, query="from:netflix", max_results=25)
    assert backend.queries == ["from:netflix"]  # only one call, no rewrite
    assert result["operator_retry"] is None
    assert result["messages"] == []


def test_literal_phrase_with_hits_does_not_retry():
    backend = _RecordingBackend(hits_for={"weekly digest": [{"id": "m9"}]})
    result = search_messages_impl(backend, query="weekly digest", max_results=25)
    assert backend.queries == ["weekly digest"]
    assert result["operator_retry"] is None
    assert len(result["messages"]) == 1


def test_retry_disabled_flag():
    backend = _RecordingBackend(hits_for={})
    result = search_messages_impl(
        backend, query="something", max_results=25, operator_retry=False
    )
    assert backend.queries == ["something"]
    assert result["operator_retry"] is None
