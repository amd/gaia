# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Behavioral tests for ``LiveGmailBackend`` against an ``httpx.MockTransport``.

These tests exercise the production code path (no mocks at the Backend
class level) using a fake HTTP transport — so request shapes, error
surfacing, token refresh discipline, and Authorization-header hygiene
are all observable.
"""

from __future__ import annotations

import json
from typing import Callable, List, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from gaia.agents.email.gmail_backend import (
    GMAIL_API_BASE,
    GmailBackend,
    LiveGmailBackend,
    _build_rfc822,
)
from gaia.connectors.errors import ConnectorsError

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class _Recorder:
    """Records every request the backend makes, hands back canned responses."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]):
        self.requests: List[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def _backend(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    token_fn: Callable[[], str] = lambda: "TOKEN-1",
) -> Tuple[LiveGmailBackend, _Recorder, List[str]]:
    """Construct a ``LiveGmailBackend`` wired to a mock transport.

    Returns ``(backend, recorder, token_calls)``. ``token_calls`` is a
    list that records every call to the token function so tests can
    assert per-request token freshness.
    """
    rec = _Recorder(handler)
    transport = httpx.MockTransport(rec)
    client = httpx.Client(transport=transport)

    token_calls: List[str] = []

    def _wrapped() -> str:
        tok = token_fn()
        token_calls.append(tok)
        return tok

    backend = LiveGmailBackend(_wrapped, http_client=client)
    return backend, rec, token_calls


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_live_backend_satisfies_protocol(self):
        backend, _, _ = _backend(lambda r: _ok({}))
        assert isinstance(backend, GmailBackend)

    def test_fake_backend_satisfies_protocol(self):
        from tests.fixtures.email.fake_gmail import FakeGmailBackend

        assert isinstance(FakeGmailBackend(), GmailBackend)


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


class TestRequestShape:
    def test_get_user_email_hits_profile_endpoint(self):
        backend, rec, _ = _backend(lambda r: _ok({"emailAddress": "me@example.com"}))
        assert backend.get_user_email() == "me@example.com"
        assert rec.requests[0].url.path.endswith("/profile")
        assert rec.requests[0].method == "GET"

    def test_list_messages_passes_query_and_labels(self):
        backend, rec, _ = _backend(lambda r: _ok({"messages": []}))
        backend.list_messages(query="is:unread", label_ids=["INBOX"], max_results=5)
        url = urlparse(str(rec.requests[0].url))
        params = parse_qs(url.query)
        assert params["q"] == ["is:unread"]
        assert "INBOX" in params["labelIds"]
        assert params["maxResults"] == ["5"]

    def test_get_message_uses_format_full(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "x"}))
        backend.get_message("x")
        params = parse_qs(urlparse(str(rec.requests[0].url)).query)
        assert params["format"] == ["full"]

    def test_archive_removes_inbox_label(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "x"}))
        backend.archive_message("x")
        body = json.loads(rec.requests[0].content)
        assert body == {"removeLabelIds": ["INBOX"]}

    def test_mark_read_removes_unread(self):
        backend, rec, _ = _backend(lambda r: _ok({}))
        backend.mark_read("m1")
        body = json.loads(rec.requests[0].content)
        assert body == {"removeLabelIds": ["UNREAD"]}

    def test_mark_unread_adds_unread(self):
        backend, rec, _ = _backend(lambda r: _ok({}))
        backend.mark_unread("m1")
        body = json.loads(rec.requests[0].content)
        assert body == {"addLabelIds": ["UNREAD"]}

    def test_add_star_adds_starred(self):
        backend, rec, _ = _backend(lambda r: _ok({}))
        backend.add_star("m1")
        body = json.loads(rec.requests[0].content)
        assert body == {"addLabelIds": ["STARRED"]}

    def test_remove_star_removes_starred(self):
        backend, rec, _ = _backend(lambda r: _ok({}))
        backend.remove_star("m1")
        body = json.loads(rec.requests[0].content)
        assert body == {"removeLabelIds": ["STARRED"]}

    def test_trash_endpoint(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "m1", "labelIds": ["TRASH"]}))
        backend.trash_message("m1")
        assert rec.requests[0].url.path.endswith("/messages/m1/trash")
        assert rec.requests[0].method == "POST"

    def test_untrash_endpoint(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "m1", "labelIds": ["INBOX"]}))
        backend.untrash_message("m1")
        assert rec.requests[0].url.path.endswith("/messages/m1/untrash")

    def test_permanent_delete_uses_delete_method(self):
        backend, rec, _ = _backend(lambda r: httpx.Response(204))
        backend.permanent_delete("m1")
        assert rec.requests[0].method == "DELETE"
        assert rec.requests[0].url.path.endswith("/messages/m1")

    def test_send_message_base64_encodes_rfc822(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "sent_1"}))
        backend.send_message(to="bob@example.com", subject="Hi", body="Hello there")
        sent = json.loads(rec.requests[0].content)
        assert "raw" in sent
        # Decode and look for the headers we set.
        import base64

        padded = sent["raw"] + "=" * (-len(sent["raw"]) % 4)
        rfc = base64.urlsafe_b64decode(padded).decode("utf-8")
        assert "To: bob@example.com" in rfc
        assert "Subject: Hi" in rfc
        assert "Hello there" in rfc

    def test_create_draft_wraps_message_envelope(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "draft_1"}))
        backend.create_draft(to="x@example.com", subject="s", body="b")
        body = json.loads(rec.requests[0].content)
        assert "message" in body
        assert "raw" in body["message"]


# ---------------------------------------------------------------------------
# Token freshness — CR-5: every HTTP request gets a fresh token
# ---------------------------------------------------------------------------


class TestTokenFreshness:
    def test_each_request_invokes_access_token_fn(self):
        page_responses = [
            _ok({"messages": [{"id": "1"}], "nextPageToken": "p2"}),
            _ok({"messages": [{"id": "2"}], "nextPageToken": "p3"}),
            _ok({"messages": [{"id": "3"}]}),
        ]
        idx = {"i": 0}

        def handler(request):
            r = page_responses[idx["i"]]
            idx["i"] += 1
            return r

        backend, _, token_calls = _backend(handler)
        backend.list_messages(max_results=10)
        backend.list_messages(max_results=10, page_token="p2")
        backend.list_messages(max_results=10, page_token="p3")
        # Three HTTP requests → three token fetches.
        assert len(token_calls) == 3

    def test_authorization_header_uses_returned_token(self):
        backend, rec, _ = _backend(
            lambda r: _ok({"emailAddress": "x@example.com"}),
            token_fn=lambda: "FRESH-TOKEN-XYZ",
        )
        backend.get_user_email()
        assert rec.requests[0].headers["Authorization"] == "Bearer FRESH-TOKEN-XYZ"


# ---------------------------------------------------------------------------
# Error surfacing — no silent fallback, no token leakage
# ---------------------------------------------------------------------------


class TestErrorSurfacing:
    def test_401_raises_with_actionable_message(self):
        backend, _, _ = _backend(lambda r: httpx.Response(401, text="Unauthorized"))
        with pytest.raises(ConnectorsError) as exc:
            backend.get_user_email()
        msg = str(exc.value)
        assert "401" in msg
        assert "Reconnect" in msg

    def test_500_includes_response_body_excerpt(self):
        backend, _, _ = _backend(
            lambda r: httpx.Response(500, text="internal server error wat")
        )
        with pytest.raises(ConnectorsError) as exc:
            backend.list_messages()
        assert "500" in str(exc.value)
        assert "internal server error" in str(exc.value)

    def test_error_does_not_leak_authorization_header(self):
        """
        S3a: bug-hunter flagged that ``httpx.HTTPStatusError`` exposes
        request headers. We construct our error from response only.
        """
        backend, _, _ = _backend(
            lambda r: httpx.Response(403, text="forbidden"),
            token_fn=lambda: "supersecrettoken",
        )
        with pytest.raises(ConnectorsError) as exc:
            backend.get_message("m1")
        full = repr(exc.value) + " " + str(exc.value) + " " + str(exc.value.__cause__)
        assert "Bearer " not in full, f"token leaked into error: {full!r}"
        assert "supersecrettoken" not in full


# ---------------------------------------------------------------------------
# Empty inbox — no raise
# ---------------------------------------------------------------------------


class TestEmptyInbox:
    def test_empty_list_returns_empty_messages(self):
        backend, _, _ = _backend(lambda r: _ok({}))
        result = backend.list_messages()
        assert result == {
            "messages": [],
            "nextPageToken": None,
            "resultSizeEstimate": 0,
        }


# ---------------------------------------------------------------------------
# RFC 2822 builder
# ---------------------------------------------------------------------------


class TestRfc822Builder:
    def test_includes_threading_headers(self):
        import base64

        raw = _build_rfc822(
            to="x@example.com",
            subject="Re: thing",
            body="hi",
            extra_headers={
                "In-Reply-To": "<orig@example.com>",
                "References": "<a@example.com> <b@example.com>",
            },
        )
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        assert "In-Reply-To: <orig@example.com>" in decoded
        assert "References: <a@example.com> <b@example.com>" in decoded
