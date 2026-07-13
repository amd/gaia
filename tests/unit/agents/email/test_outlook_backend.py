# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Behavioral tests for ``LiveOutlookBackend`` (the MS Graph mail backend, #1275)
against an ``httpx.MockTransport``.

The backend translates Microsoft Graph JSON into the Gmail-API-v1 shape so the
email agent's existing tools (read/organize/reply/delete) operate on it
interchangeably with ``LiveGmailBackend`` — the seam ``gmail_backend.py``'s
docstring anticipated for Outlook/Exchange. These tests therefore assert two
layers:

1. ``OutlookBackend`` satisfies the ``GmailBackend`` Protocol and hits the
   correct Graph endpoints, surfacing every non-2xx as an actionable
   ``ConnectorsError`` (NOT a silent empty result), with no Authorization-header
   leakage.
2. The agent's own read tools (``list_inbox_impl`` / ``triage_inbox_impl``) run
   unchanged against the Outlook backend and produce normal output — proving
   triage works against a connected Outlook mailbox.

All network + token resolution is mocked; there are NO live Graph or OAuth
calls.
"""

from __future__ import annotations

import json
from typing import Callable, List, Tuple
from urllib.parse import parse_qs, urlparse

import httpx

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.gmail_backend import GmailBackend, decode_message_body
from gaia_agent_email.outlook_backend import LiveOutlookBackend, _get_outlook_token
from gaia_agent_email.tools.read_tools import list_inbox_impl, triage_inbox_impl

from gaia.connectors.errors import AuthRequiredError, ConnectorsError

# ---------------------------------------------------------------------------
# Test harness — mirrors tests/unit/email/test_gmail_client.py
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
    token_fn: Callable[[], str] = lambda: "GRAPH-TOKEN-1",
) -> Tuple[LiveOutlookBackend, _Recorder, List[str]]:
    rec = _Recorder(handler)
    transport = httpx.MockTransport(rec)
    client = httpx.Client(transport=transport)

    token_calls: List[str] = []

    def _wrapped() -> str:
        tok = token_fn()
        token_calls.append(tok)
        return tok

    backend = LiveOutlookBackend(_wrapped, http_client=client)
    return backend, rec, token_calls


def _ok(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _graph_message(
    *,
    msg_id: str = "AAMkMSG1",
    conversation_id: str = "CONV1",
    subject: str = "Quarterly report",
    from_name: str = "Alice Example",
    from_addr: str = "alice@example.com",
    to_addr: str = "me@outlook.com",
    received: str = "2026-06-01T09:30:00Z",
    is_read: bool = False,
    flag_status: str = "notFlagged",
    body_content_type: str = "text",
    body_content: str = "Please review the attached numbers.",
    categories: list | None = None,
    parent_folder_id: str = "inbox",
) -> dict:
    """Build a minimal MS Graph ``message`` resource (the shape Graph returns)."""
    return {
        "id": msg_id,
        "conversationId": conversation_id,
        "subject": subject,
        "from": {"emailAddress": {"name": from_name, "address": from_addr}},
        "toRecipients": [{"emailAddress": {"name": "", "address": to_addr}}],
        "receivedDateTime": received,
        "isRead": is_read,
        "isDraft": False,
        "flag": {"flagStatus": flag_status},
        "bodyPreview": body_content[:80],
        "body": {"contentType": body_content_type, "content": body_content},
        "categories": categories or [],
        "parentFolderId": parent_folder_id,
    }


# ---------------------------------------------------------------------------
# Protocol conformance — the interchangeability contract
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_outlook_backend_satisfies_gmail_protocol(self):
        backend, _, _ = _backend(lambda r: _ok({}))
        # Structural runtime_checkable Protocol — the email tools depend on
        # this so they can use Outlook and Gmail interchangeably.
        assert isinstance(backend, GmailBackend)


# ---------------------------------------------------------------------------
# Read translation — Graph JSON -> Gmail-API-v1 shape
# ---------------------------------------------------------------------------


class TestReadTranslation:
    def test_get_user_email_hits_me_endpoint(self):
        backend, rec, _ = _backend(lambda r: _ok({"mail": "me@outlook.com"}))
        assert backend.get_user_email() == "me@outlook.com"
        assert rec.requests[0].url.path.endswith("/me")
        assert rec.requests[0].method == "GET"

    def test_get_user_email_falls_back_to_userPrincipalName(self):
        # Personal accounts sometimes return userPrincipalName, not mail.
        backend, _, _ = _backend(
            lambda r: _ok({"userPrincipalName": "me@outlook.com", "mail": None})
        )
        assert backend.get_user_email() == "me@outlook.com"

    def test_get_message_translates_to_gmail_shape(self):
        backend, rec, _ = _backend(lambda r: _ok(_graph_message()))
        msg = backend.get_message("AAMkMSG1")
        # Gmail-API-v1 top-level shape.
        assert msg["id"] == "AAMkMSG1"
        assert msg["threadId"] == "CONV1"
        assert "payload" in msg
        headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
        assert headers["subject"] == "Quarterly report"
        assert headers["from"] == "Alice Example <alice@example.com>"
        assert headers["to"] == "me@outlook.com"
        # Date header present (the read tool reads it; value passthrough is fine).
        assert "date" in headers
        # INBOX + UNREAD labels derived from MS flags.
        assert "INBOX" in msg["labelIds"]
        assert "UNREAD" in msg["labelIds"]

    def test_get_message_uses_select_to_pull_body(self):
        backend, rec, _ = _backend(lambda r: _ok(_graph_message()))
        backend.get_message("AAMkMSG1")
        # Must request the body (and the fields the translation needs).
        url = urlparse(str(rec.requests[0].url))
        params = parse_qs(url.query)
        select = params.get("$select", [""])[0]
        assert "body" in select

    def test_body_is_decodable_by_production_decoder(self):
        backend, _, _ = _backend(
            lambda r: _ok(
                _graph_message(
                    body_content_type="text",
                    body_content="Hello from Outlook",
                )
            )
        )
        msg = backend.get_message("AAMkMSG1")
        # The agent decodes via the production gmail decoder; it must round-trip.
        body, _attachments = decode_message_body(msg["payload"])
        assert body == "Hello from Outlook"

    def test_html_body_maps_to_text_html_mimetype(self):
        backend, _, _ = _backend(
            lambda r: _ok(
                _graph_message(
                    body_content_type="html",
                    body_content="<html><body><p>Hi <b>there</b></p>"
                    "<style>p{color:red}</style></body></html>",
                )
            )
        )
        msg = backend.get_message("AAMkMSG1")
        # Decoder strips tags + drops <style> bodies (prompt-injection defense).
        body, _ = decode_message_body(msg["payload"])
        assert "Hi" in body and "there" in body
        assert "color:red" not in body

    def test_read_message_has_no_unread_label(self):
        backend, _, _ = _backend(lambda r: _ok(_graph_message(is_read=True)))
        msg = backend.get_message("AAMkMSG1")
        assert "UNREAD" not in msg["labelIds"]

    def test_flagged_message_has_starred_label(self):
        backend, _, _ = _backend(lambda r: _ok(_graph_message(flag_status="flagged")))
        msg = backend.get_message("AAMkMSG1")
        assert "STARRED" in msg["labelIds"]

    def test_categories_become_labelids(self):
        backend, _, _ = _backend(
            lambda r: _ok(_graph_message(categories=["Receipts", "Travel"]))
        )
        msg = backend.get_message("AAMkMSG1")
        assert "Receipts" in msg["labelIds"]
        assert "Travel" in msg["labelIds"]

    def test_list_messages_inbox_hits_inbox_folder(self):
        backend, rec, _ = _backend(lambda r: _ok({"value": []}))
        backend.list_messages(label_ids=["INBOX"], max_results=5)
        path = urlparse(str(rec.requests[0].url)).path
        assert path.endswith("/me/mailFolders/inbox/messages")
        params = parse_qs(urlparse(str(rec.requests[0].url)).query)
        assert params["$top"] == ["5"]

    def test_list_messages_unread_filters_isread_false(self):
        backend, rec, _ = _backend(lambda r: _ok({"value": []}))
        backend.list_messages(label_ids=["UNREAD"], max_results=5)
        params = parse_qs(urlparse(str(rec.requests[0].url)).query)
        # MS Graph $filter for unread.
        assert any("isRead eq false" in v for v in params.get("$filter", []))

    def test_list_messages_sent_hits_sentitems_folder(self):
        # The follow-up tracker (#1606) scans SENT; falling through to the
        # inbox folder here would make it silently scan the wrong mail.
        backend, rec, _ = _backend(lambda r: _ok({"value": []}))
        backend.list_messages(label_ids=["SENT"], max_results=5)
        path = urlparse(str(rec.requests[0].url)).path
        assert path.endswith("/me/mailFolders/sentitems/messages")

    def test_list_messages_query_uses_search(self):
        backend, rec, _ = _backend(lambda r: _ok({"value": []}))
        backend.list_messages(query="invoice", max_results=5)
        params = parse_qs(urlparse(str(rec.requests[0].url)).query)
        assert "$search" in params

    def test_list_messages_normalizes_to_gmail_stub_shape(self):
        backend, _, _ = _backend(
            lambda r: _ok(
                {
                    "value": [
                        {"id": "m1", "conversationId": "c1"},
                        {"id": "m2", "conversationId": "c2"},
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/next?skip=2",
                }
            )
        )
        out = backend.list_messages(label_ids=["INBOX"], max_results=2)
        assert out["messages"] == [
            {"id": "m1", "threadId": "c1"},
            {"id": "m2", "threadId": "c2"},
        ]
        # nextPageToken carried so paginated callers don't break.
        assert out["nextPageToken"]

    def test_empty_inbox_returns_empty_messages_not_raise(self):
        backend, _, _ = _backend(lambda r: _ok({"value": []}))
        out = backend.list_messages(label_ids=["INBOX"])
        assert out["messages"] == []
        assert out["nextPageToken"] is None

    def test_page_token_follows_nextlink_verbatim(self):
        # A page_token is a Graph @odata.nextLink absolute URL; it must be
        # GET verbatim (NOT re-derived to page 1, which would silently loop).
        next_url = (
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
            "?$skiptoken=PAGE2CURSOR"
        )
        backend, rec, _ = _backend(
            lambda r: _ok({"value": [{"id": "p2", "conversationId": "c"}]})
        )
        out = backend.list_messages(page_token=next_url, max_results=10)
        assert out["messages"] == [{"id": "p2", "threadId": "c"}]
        # The exact nextLink URL (cursor preserved) was requested.
        assert "$skiptoken=PAGE2CURSOR" in str(rec.requests[0].url)

    def test_get_thread_filters_by_conversation(self):
        backend, rec, _ = _backend(
            lambda r: _ok({"value": [_graph_message(msg_id="t1")]})
        )
        thread = backend.get_thread("CONV1")
        assert thread["messages"]
        # Returns Gmail-thread shape with translated messages.
        assert thread["messages"][0]["id"] == "t1"
        params = parse_qs(urlparse(str(rec.requests[0].url)).query)
        assert any("conversationId" in v for v in params.get("$filter", []))


# ---------------------------------------------------------------------------
# Mutate verbs -> correct Graph endpoints
# ---------------------------------------------------------------------------


class TestMutateVerbs:
    def test_mark_read_patches_isread_true(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "m1", "isRead": True}))
        backend.mark_read("m1")
        assert rec.requests[0].method == "PATCH"
        assert rec.requests[0].url.path.endswith("/me/messages/m1")
        body = json.loads(rec.requests[0].content)
        assert body == {"isRead": True}

    def test_mark_unread_patches_isread_false(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "m1", "isRead": False}))
        backend.mark_unread("m1")
        body = json.loads(rec.requests[0].content)
        assert body == {"isRead": False}

    def test_add_star_sets_flag_flagged(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "m1"}))
        backend.add_star("m1")
        body = json.loads(rec.requests[0].content)
        assert body == {"flag": {"flagStatus": "flagged"}}

    def test_remove_star_clears_flag(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "m1"}))
        backend.remove_star("m1")
        body = json.loads(rec.requests[0].content)
        assert body == {"flag": {"flagStatus": "notFlagged"}}

    def test_archive_moves_to_archive_folder(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "m1"}))
        backend.archive_message("m1")
        assert rec.requests[0].method == "POST"
        assert rec.requests[0].url.path.endswith("/me/messages/m1/move")
        body = json.loads(rec.requests[0].content)
        assert body == {"destinationId": "archive"}

    def test_trash_moves_to_deleted_items(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "m1"}))
        backend.trash_message("m1")
        assert rec.requests[0].url.path.endswith("/me/messages/m1/move")
        body = json.loads(rec.requests[0].content)
        assert body == {"destinationId": "deleteditems"}

    def test_untrash_moves_back_to_inbox(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "m1"}))
        backend.untrash_message("m1")
        body = json.loads(rec.requests[0].content)
        assert body == {"destinationId": "inbox"}

    def test_permanent_delete_uses_delete_method(self):
        backend, rec, _ = _backend(lambda r: httpx.Response(204))
        backend.permanent_delete("m1")
        assert rec.requests[0].method == "DELETE"
        assert rec.requests[0].url.path.endswith("/me/messages/m1")

    def test_add_label_appends_category(self):
        # add_label reads current categories then PATCHes the union.
        responses = iter(
            [
                _ok(_graph_message(msg_id="m1", categories=["Existing"])),
                _ok({"id": "m1"}),
            ]
        )
        backend, rec, _ = _backend(lambda r: next(responses))
        backend.add_label("m1", "Receipts")
        patch_req = rec.requests[-1]
        assert patch_req.method == "PATCH"
        body = json.loads(patch_req.content)
        assert set(body["categories"]) == {"Existing", "Receipts"}

    def test_remove_label_drops_category(self):
        responses = iter(
            [
                _ok(_graph_message(msg_id="m1", categories=["Receipts", "Travel"])),
                _ok({"id": "m1"}),
            ]
        )
        backend, rec, _ = _backend(lambda r: next(responses))
        backend.remove_label("m1", "Travel")
        body = json.loads(rec.requests[-1].content)
        assert body["categories"] == ["Receipts"]


# ---------------------------------------------------------------------------
# Send / draft
# ---------------------------------------------------------------------------


class TestSend:
    def test_create_draft_posts_message_resource(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "draft_1"}))
        out = backend.create_draft(to="bob@example.com", subject="Hi", body="Hello")
        assert out["id"] == "draft_1"
        assert rec.requests[0].method == "POST"
        assert rec.requests[0].url.path.endswith("/me/messages")
        body = json.loads(rec.requests[0].content)
        assert body["subject"] == "Hi"
        assert body["body"]["content"] == "Hello"
        assert body["toRecipients"][0]["emailAddress"]["address"] == "bob@example.com"

    def test_send_draft_posts_to_send_endpoint(self):
        backend, rec, _ = _backend(lambda r: httpx.Response(202))
        out = backend.send_draft("draft_1")
        assert rec.requests[0].method == "POST"
        assert rec.requests[0].url.path.endswith("/me/messages/draft_1/send")
        assert out["id"] == "draft_1"

    def test_send_message_uses_sendmail(self):
        backend, rec, _ = _backend(lambda r: httpx.Response(202))
        backend.send_message(to="bob@example.com", subject="Hi", body="Hello")
        assert rec.requests[0].url.path.endswith("/me/sendMail")
        body = json.loads(rec.requests[0].content)
        assert body["message"]["subject"] == "Hi"
        assert (
            body["message"]["toRecipients"][0]["emailAddress"]["address"]
            == "bob@example.com"
        )
        assert body["saveToSentItems"] is True

    def test_send_message_rejects_crlf_header_injection(self):
        backend, _, _ = _backend(lambda r: httpx.Response(202))
        with pytest.raises(ValueError, match="injection"):
            backend.send_message(
                to="victim@example.com\r\nBcc: attacker@evil.com",
                subject="Hi",
                body="hello",
            )

    def test_create_draft_drops_standard_threading_headers(self):
        # The reply tools pass In-Reply-To/References; Graph rejects standard
        # headers in internetMessageHeaders (400), so they must be dropped, not
        # forwarded. The draft must still be created (no crash, no 400 header).
        backend, rec, _ = _backend(lambda r: _ok({"id": "draft_1"}))
        out = backend.create_draft(
            to="x@example.com",
            subject="Re: thing",
            body="reply",
            headers={
                "In-Reply-To": "<orig@example.com>",
                "References": "<a@example.com>",
            },
        )
        assert out["id"] == "draft_1"
        body = json.loads(rec.requests[0].content)
        # Standard threading headers are NOT sent as internetMessageHeaders.
        assert "internetMessageHeaders" not in body

    def test_create_draft_forwards_x_prefixed_custom_header(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "draft_1"}))
        backend.create_draft(
            to="x@example.com",
            subject="s",
            body="b",
            headers={"X-GAIA-Trace": "abc123"},
        )
        body = json.loads(rec.requests[0].content)
        names = {h["name"] for h in body["internetMessageHeaders"]}
        assert "X-GAIA-Trace" in names


# ---------------------------------------------------------------------------
# Token freshness — every request gets a fresh token
# ---------------------------------------------------------------------------


class TestTokenFreshness:
    def test_each_request_invokes_token_fn(self):
        backend, _, token_calls = _backend(lambda r: _ok({"value": []}))
        backend.list_messages(label_ids=["INBOX"])
        backend.list_messages(label_ids=["INBOX"])
        assert len(token_calls) == 2

    def test_authorization_header_uses_returned_token(self):
        backend, rec, _ = _backend(
            lambda r: _ok({"mail": "x@outlook.com"}),
            token_fn=lambda: "FRESH-GRAPH-TOKEN",
        )
        backend.get_user_email()
        assert rec.requests[0].headers["Authorization"] == "Bearer FRESH-GRAPH-TOKEN"


# ---------------------------------------------------------------------------
# Error surfacing — NO silent empty, NO token leakage (core AC)
# ---------------------------------------------------------------------------


class TestErrorSurfacing:
    def test_403_insufficient_scope_raises_actionable_not_empty(self):
        # A token that lacks Mail.Read at the Graph layer -> 403. This MUST
        # raise an actionable error, NOT return an empty message list.
        backend, _, _ = _backend(
            lambda r: httpx.Response(
                403,
                text=json.dumps(
                    {
                        "error": {
                            "code": "ErrorAccessDenied",
                            "message": "Access is denied. Check credentials and try again.",
                        }
                    }
                ),
            )
        )
        with pytest.raises(ConnectorsError) as exc:
            backend.list_messages(label_ids=["INBOX"])
        msg = str(exc.value)
        assert "403" in msg
        # Actionable: names what to do (reconnect) and which provider.
        assert "Microsoft" in msg or "Outlook" in msg
        assert "reconnect" in msg.lower() or "scope" in msg.lower()

    def test_401_raises_with_reconnect_guidance(self):
        backend, _, _ = _backend(lambda r: httpx.Response(401, text="Unauthorized"))
        with pytest.raises(ConnectorsError) as exc:
            backend.get_user_email()
        msg = str(exc.value)
        assert "401" in msg
        assert "reconnect" in msg.lower()

    def test_500_includes_body_excerpt(self):
        backend, _, _ = _backend(
            lambda r: httpx.Response(500, text="internal graph error xyz")
        )
        with pytest.raises(ConnectorsError) as exc:
            backend.list_messages(label_ids=["INBOX"])
        assert "500" in str(exc.value)
        assert "internal graph error" in str(exc.value)

    def test_error_does_not_leak_authorization_header(self):
        backend, _, _ = _backend(
            lambda r: httpx.Response(403, text="forbidden"),
            token_fn=lambda: "supersecretgraphtoken",
        )
        with pytest.raises(ConnectorsError) as exc:
            backend.get_message("m1")
        full = repr(exc.value) + " " + str(exc.value) + " " + str(exc.value.__cause__)
        assert "Bearer " not in full, f"token leaked: {full!r}"
        assert "supersecretgraphtoken" not in full


# ---------------------------------------------------------------------------
# Token resolver — grant gating raises (no silent empty), no live OAuth
# ---------------------------------------------------------------------------


class TestTokenResolver:
    def test_get_outlook_token_returns_access_token(self, monkeypatch):
        captured = {}

        def fake_get_credential_sync(connector_id, *, agent_id, required_scopes):
            captured["connector_id"] = connector_id
            captured["agent_id"] = agent_id
            captured["scopes"] = list(required_scopes)
            return {"access_token": "TOK-123", "scopes": list(required_scopes)}

        monkeypatch.setattr(
            "gaia_agent_email.outlook_backend.get_credential_sync",
            fake_get_credential_sync,
        )
        token = _get_outlook_token()
        assert token == "TOK-123"
        # Uses the microsoft connector + the email agent's namespaced id.
        assert captured["connector_id"] == "microsoft"
        assert captured["agent_id"] == "installed:email"
        # Requests at least Mail.Read / Mail.ReadWrite (the Graph mail scopes).
        assert any("graph.microsoft.com/Mail" in s for s in captured["scopes"])

    def test_get_outlook_token_propagates_grant_error_not_empty(self, monkeypatch):
        # When the user hasn't granted the scopes, the grant dispatcher raises
        # AuthRequiredError. The backend must let it propagate — never swallow
        # it into an empty token / empty inbox.
        def fake_get_credential_sync(connector_id, *, agent_id, required_scopes):
            raise AuthRequiredError(
                AuthRequiredError.Reason.AGENT_NOT_GRANTED,
                provider="microsoft",
                agent_id=agent_id,
                missing_scopes=required_scopes,
            )

        monkeypatch.setattr(
            "gaia_agent_email.outlook_backend.get_credential_sync",
            fake_get_credential_sync,
        )
        with pytest.raises(AuthRequiredError) as exc:
            _get_outlook_token()
        assert exc.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED


# ---------------------------------------------------------------------------
# Triage works against a connected Outlook mailbox (the agent-level AC)
# ---------------------------------------------------------------------------


class TestTriageAgainstOutlook:
    def _inbox_backend(self):
        """Backend whose Graph responses model a 2-message inbox."""
        msgs = {
            "m1": _graph_message(
                msg_id="m1",
                conversation_id="c1",
                subject="URGENT: server down",
                from_addr="ops@example.com",
                body_content="The prod server is down, please respond ASAP.",
                is_read=False,
            ),
            "m2": _graph_message(
                msg_id="m2",
                conversation_id="c2",
                subject="Your weekly newsletter",
                from_addr="news@promo.example.com",
                body_content="Check out this week's deals!",
                is_read=False,
            ),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/me/mailFolders/inbox/messages"):
                return _ok(
                    {
                        "value": [
                            {"id": "m1", "conversationId": "c1"},
                            {"id": "m2", "conversationId": "c2"},
                        ]
                    }
                )
            # /me/messages/{id}
            mid = path.rsplit("/", 1)[-1]
            return _ok(msgs[mid])

        backend, _, _ = _backend(handler)
        return backend

    def test_list_inbox_impl_runs_unchanged_on_outlook(self):
        backend = self._inbox_backend()
        out = list_inbox_impl(backend, max_results=10)
        assert len(out["messages"]) == 2
        subjects = {m["subject"] for m in out["messages"]}
        assert "URGENT: server down" in subjects
        # Body wrapped in the untrusted-input delimiter, same as Gmail.
        assert any("UNTRUSTED_EMAIL_BODY" in m["body"] for m in out["messages"])

    def test_triage_inbox_impl_categorizes_outlook_messages(self):
        backend = self._inbox_backend()
        # Heuristic-only (classifier=None) — proves triage runs end to end on
        # the translated Outlook payloads without an LLM.
        out = triage_inbox_impl(backend, max_messages=10, classifier=None)
        assert out["grouped"]["total"] == 2
        ids = {r["id"] for r in out["results"]}
        assert ids == {"m1", "m2"}


# ---------------------------------------------------------------------------
# send_message returns sent signal (D4 — Graph sendMail 202 fix)
# ---------------------------------------------------------------------------


class TestSendMessageSentSignal:
    """LiveOutlookBackend.send_message must return {"sent": True} (#1603, D4).

    Graph sendMail returns HTTP 202 with no body — no message id is echoed back.
    The REST handler previously raised 502 on empty sent_id; the fix is to
    include "sent": True in the return dict so the handler can distinguish
    "Outlook success (no id)" from "unknown failure (no id, no signal)".
    """

    def test_send_message_returns_sent_true(self):
        backend, _, _ = _backend(lambda r: httpx.Response(202))
        result = backend.send_message(
            to="bob@example.com", subject="Hello", body="World"
        )
        assert result.get("sent") is True

    def test_send_message_id_is_empty_string(self):
        """Graph does NOT return an id for sendMail — empty string, not None."""
        backend, _, _ = _backend(lambda r: httpx.Response(202))
        result = backend.send_message(to="x@example.com", subject="s", body="b")
        assert result.get("id") == ""

    def test_send_message_still_posts_to_sendmail_endpoint(self):
        """The change to the return value must not affect the actual HTTP call."""
        backend, rec, _ = _backend(lambda r: httpx.Response(202))
        backend.send_message(to="bob@example.com", subject="Hi", body="Hello")
        assert rec.requests[0].url.path.endswith("/me/sendMail")
