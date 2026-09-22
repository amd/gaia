# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Corpus-backed regressions for the offline Gmail query matcher."""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import search_messages_impl

from tests.fixtures.email.fake_gmail import _payload_text, _query_tokens


def test_quoted_phrase_results_are_a_subset_of_unquoted_terms(synthetic_inbox):
    bare = {
        message["id"]
        for message in synthetic_inbox.list_messages(
            query="kernel fusion", max_results=500
        )["messages"]
    }
    quoted = {
        message["id"]
        for message in synthetic_inbox.list_messages(
            query='"kernel fusion"', max_results=500
        )["messages"]
    }

    assert quoted
    assert quoted <= bare


def test_body_only_corpus_query_auto_escalates(synthetic_inbox):
    listing = synthetic_inbox.list_messages(
        query="activate-reserved-tier", max_results=25
    )
    assert len(listing["messages"]) == 1
    candidate = synthetic_inbox.get_message(listing["messages"][0]["id"])
    headers = {
        (header.get("name") or "").lower(): header.get("value", "")
        for header in (candidate.get("payload") or {}).get("headers", [])
    }
    body = _payload_text(candidate.get("payload") or {}).lower()
    metadata = (
        (headers.get("subject") or "").lower()
        + " "
        + (candidate.get("snippet") or "").lower()
    )
    assert "activate-reserved-tier" in body
    assert "activate-reserved-tier" not in metadata

    result = search_messages_impl(
        synthetic_inbox, query="activate-reserved-tier", max_results=25
    )

    assert result["include_bodies_auto"] is True
    assert len(result["messages"]) == 1
    assert "activate-reserved-tier" in result["messages"][0]["body"]


def test_query_tokens_preserve_terms_and_apostrophes():
    assert _query_tokens("budget report Q3") == ["budget", "report", "Q3"]
    assert _query_tokens('"budget report" Q3') == ["budget report", "Q3"]
    assert _query_tokens("O'Brien from:vendor@example.com") == [
        "O'Brien",
        "from:vendor@example.com",
    ]
