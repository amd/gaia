# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Issue #1604 — mailbox search as a first-class capability: query correctness
and in-agent parity for ``POST /v1/email/search``.

The existing REST tests (``test_rest_contract.py`` §6, the OpenAPI conformance
suite) drive the route through an inject-only stub that IGNORES the query — they
prove the route calls the backend, not that a query actually filters. Per the
repo testing rule ("mocks prove 'we called it', not 'the call is valid'"), these
tests close that gap against the real ``FakeGmailBackend`` fixture (mbox-shaped
store + the Gmail-query matcher), pinning the issue's two test acceptance
criteria:

1. **Unit** — a keyword / Gmail-syntax query returns the expected message ids,
   and each hit carries the documented metadata (id, from, subject, date,
   snippet).
2. **Parity** — the REST path returns the same messages as the agent's in-loop
   ``search_messages`` tool (``search_messages_impl``), in the same order, for
   the same query. The REST surface restores that tool on the contract (#1781);
   this test fails if the two paths ever drift.

No live mailbox, no LLM.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make tests.fixtures importable.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("fastapi")
# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

import gaia_agent_email.api_routes as api_routes  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email.export_openapi import build_app  # noqa: E402
from gaia_agent_email.tools.read_tools import search_messages_impl  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _make_inbox_message(
    idx: int, *, subject: str, sender: str = "Alice Example <alice@example.com>"
) -> dict:
    """Build a minimal Gmail-API-shape INBOX message."""
    return {
        "id": f"msg-{idx:03d}",
        "threadId": f"thread-{idx:03d}",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": f"snippet for {subject}",
        "internalDate": str(1_700_000_000_000 + idx),
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "user@example.com"},
                {"name": "Date", "value": f"Mon, 1 Jun 2026 10:{idx:02d}:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": ""},
        },
    }


@pytest.fixture()
def fake_gmail() -> FakeGmailBackend:
    backend = FakeGmailBackend(user_email="user@example.com")
    backend.add_message(_make_inbox_message(1, subject="Invoice for May"))
    backend.add_message(_make_inbox_message(2, subject="Lunch tomorrow?"))
    backend.add_message(_make_inbox_message(3, subject="Invoice overdue"))
    backend.add_message(
        _make_inbox_message(
            4, subject="Weekly digest", sender="Bob <bob@newsletter.example.com>"
        )
    )
    return backend


@pytest.fixture()
def client(fake_gmail) -> TestClient:
    """A REST client whose search backend is the real FakeGmailBackend."""
    c = TestClient(build_app())
    c.app.dependency_overrides[api_routes.get_search_backend] = lambda: fake_gmail
    try:
        yield c
    finally:
        c.app.dependency_overrides.pop(api_routes.get_search_backend, None)


def _rest_search(client: TestClient, **payload) -> dict:
    resp = client.post("/v1/email/search", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Unit — a query returns the expected message ids (issue #1604 test AC)
# ---------------------------------------------------------------------------


class TestSearchQueryReturnsExpectedIds:
    def test_keyword_query_returns_matching_ids_only(self, client):
        body = _rest_search(client, query="invoice")
        ids = sorted(m["id"] for m in body["messages"])
        assert ids == ["msg-001", "msg-003"]
        assert body["count"] == 2

    def test_from_query_filters_by_sender(self, client):
        body = _rest_search(client, query="from:bob@newsletter.example.com")
        assert [m["id"] for m in body["messages"]] == ["msg-004"]

    def test_subject_query_filters_by_subject(self, client):
        body = _rest_search(client, query="subject:lunch")
        assert [m["id"] for m in body["messages"]] == ["msg-002"]

    def test_hit_carries_documented_metadata(self, client):
        """AC: matching messages return id, from, subject, date, snippet."""
        (hit,) = _rest_search(client, query="subject:lunch")["messages"]
        assert hit["id"] == "msg-002"
        assert hit["thread_id"] == "thread-002"
        assert hit["from"] == "Alice Example <alice@example.com>"
        assert hit["subject"] == "Lunch tomorrow?"
        assert hit["date"] == "Mon, 1 Jun 2026 10:02:00 +0000"
        assert hit["snippet"] == "snippet for Lunch tomorrow?"
        # Search is a list view: metadata + snippet only, never the
        # (untrusted) message body.
        assert "body" not in hit

    def test_no_matches_is_empty_list_not_an_error(self, client):
        body = _rest_search(client, query="zebra-unicorn")
        assert body["messages"] == []
        assert body["count"] == 0

    def test_max_results_bounds_the_hits(self, client):
        body = _rest_search(client, query="invoice", max_results=1)
        assert len(body["messages"]) == 1


# ---------------------------------------------------------------------------
# 2. Parity — REST path == in-agent search_messages tool (issue #1604 test AC)
# ---------------------------------------------------------------------------


class TestSearchParityWithInAgentTool:
    @pytest.mark.parametrize(
        "query",
        ["invoice", "subject:lunch", "from:bob@newsletter.example.com", "is:unread"],
    )
    def test_same_ids_in_same_order_as_the_tool(self, client, fake_gmail, query):
        tool_ids = [
            m["id"]
            for m in search_messages_impl(fake_gmail, query=query, max_results=25)[
                "messages"
            ]
        ]
        rest_ids = [
            m["id"]
            for m in _rest_search(client, query=query, max_results=25)["messages"]
        ]
        assert rest_ids == tool_ids

    def test_same_metadata_as_the_tool(self, client, fake_gmail):
        """Both paths hydrate through ``_format_message_for_llm`` — the headers
        the REST surface returns must be byte-identical to the tool's."""
        tool_msgs = search_messages_impl(fake_gmail, query="invoice", max_results=25)[
            "messages"
        ]
        rest_msgs = _rest_search(client, query="invoice", max_results=25)["messages"]
        assert len(rest_msgs) == len(tool_msgs)
        for rest_hit, tool_msg in zip(rest_msgs, tool_msgs):
            for key in ("id", "thread_id", "subject", "from", "date", "snippet"):
                assert rest_hit[key] == tool_msg[key], (
                    f"REST /v1/email/search and the in-agent search_messages "
                    f"tool disagree on {key!r} for message {tool_msg['id']}"
                )

    def test_same_result_cap_as_the_tool(self, client, fake_gmail):
        tool_ids = [
            m["id"]
            for m in search_messages_impl(fake_gmail, query="invoice", max_results=1)[
                "messages"
            ]
        ]
        rest_ids = [
            m["id"]
            for m in _rest_search(client, query="invoice", max_results=1)["messages"]
        ]
        assert rest_ids == tool_ids
