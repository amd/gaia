# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read-only email tools for the flagship agent (Phase 0 of the email skill).

The backend tests drive a real ``httpx`` client over a ``MockTransport``, so
they assert the *shape of the outgoing Graph request* — path, ``$select``,
``$filter``, ``$top`` — not merely that a stub was called. A hand-rolled fake
would happily accept a request Graph itself would 400.
"""

import json

import httpx
import pytest

from gaia.agents.tools._email.graph import (
    MailboxAuthError,
    MailboxError,
    OutlookReadBackend,
    message_summary,
)
from gaia.agents.tools.email_tools import (
    EMAIL_AGENT_ID,
    MAIL_SCOPES,
    EmailToolsMixin,
)

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

GRAPH_MESSAGE = {
    "id": "AAMk-1",
    "conversationId": "conv-1",
    "subject": "Q3 numbers",
    "from": {"emailAddress": {"name": "Dana Ruiz", "address": "dana@example.com"}},
    "toRecipients": [{"emailAddress": {"name": "Me", "address": "me@example.com"}}],
    "ccRecipients": [],
    "receivedDateTime": "2026-09-02T08:15:00Z",
    "isRead": False,
    "flag": {"flagStatus": "flagged"},
    "categories": ["Work"],
    "bodyPreview": "  Can you confirm the Q3 figures?  ",
    "body": {"contentType": "html", "content": "<p>Can you confirm?</p>"},
}


def make_backend(handler):
    """An OutlookReadBackend whose HTTP goes to `handler`, with a fixed token."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OutlookReadBackend(lambda: "test-token", http_client=client)


def json_response(payload, status=200):
    return httpx.Response(status, json=payload)


def unfenced(message):
    """The body a read returns, with the untrusted-content fence stripped.

    ``read_email`` wraps every body in the fence (#4150); these tests are about
    what is inside it — truncation, and the per-turn budget it is charged to.
    """
    from gaia.agents.tools._email.phishing import (
        UNTRUSTED_BODY_CLOSE,
        UNTRUSTED_BODY_OPEN,
    )

    body = message["body"]
    assert body.startswith(UNTRUSTED_BODY_OPEN) and body.endswith(UNTRUSTED_BODY_CLOSE)
    return body[len(UNTRUSTED_BODY_OPEN) : -len(UNTRUSTED_BODY_CLOSE)].strip("\n")


def graph_error(status, code=None, body=None):
    """A Graph error response: either a structured `{error: {code}}` body, or
    a raw text body (HTML, plain text) to simulate what Graph and proxies
    actually send on failure."""
    if body is not None:
        return httpx.Response(status, text=body)
    payload = {"error": {"code": code, "message": "Access is denied."}}
    return httpx.Response(status, json=payload)


# --------------------------------------------------------------------------
# message_summary — provider-neutral flattening
# --------------------------------------------------------------------------


def test_summary_flattens_graph_shape():
    out = message_summary(GRAPH_MESSAGE)
    assert out["id"] == "AAMk-1"
    assert out["thread_id"] == "conv-1"
    assert out["from"] == "Dana Ruiz <dana@example.com>"
    assert out["unread"] is True
    assert out["flagged"] is True
    assert out["preview"] == "Can you confirm the Q3 figures?"


def test_summary_omits_body_unless_asked():
    assert "body" not in message_summary(GRAPH_MESSAGE)
    assert message_summary(GRAPH_MESSAGE, include_body=True)["body"] == (
        "<p>Can you confirm?</p>"
    )


def test_summary_falls_back_to_message_id_when_no_conversation():
    out = message_summary({"id": "solo", "subject": "x"})
    assert out["thread_id"] == "solo"


def test_summary_bare_address_when_name_missing():
    msg = {"id": "1", "from": {"emailAddress": {"address": "a@b.com"}}}
    assert message_summary(msg)["from"] == "a@b.com"


def test_summary_subject_placeholder():
    assert message_summary({"id": "1"})["subject"] == "(no subject)"


# --------------------------------------------------------------------------
# request validity — the calls must be ones Graph would actually accept
# --------------------------------------------------------------------------


def test_list_inbox_requests_inbox_folder_newest_first():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return json_response({"value": [GRAPH_MESSAGE]})

    messages = make_backend(handler).list_inbox(limit=10)

    url = seen["url"]
    assert url.path == "/v1.0/me/mailFolders/inbox/messages"
    assert url.params["$top"] == "10"
    assert url.params["$orderby"] == "receivedDateTime desc"
    # Bodies are the expensive field; a listing must not fetch them.
    assert "body" not in url.params["$select"].split(",")
    assert "bodyPreview" in url.params["$select"]
    assert len(messages) == 1


def test_list_inbox_unread_only_sets_filter():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return json_response({"value": []})

    make_backend(handler).list_inbox(unread_only=True)
    assert seen["url"].params["$filter"] == "isRead eq false"


def test_list_inbox_without_unread_sends_no_filter():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return json_response({"value": []})

    make_backend(handler).list_inbox()
    assert "$filter" not in seen["url"].params


def test_search_quotes_the_term_and_omits_orderby():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return json_response({"value": []})

    make_backend(handler).search("invoice")

    url = seen["url"]
    assert url.path == "/v1.0/me/messages"
    assert url.params["$search"] == '"invoice"'
    # Graph rejects $search combined with $orderby — sending both is a 400.
    assert "$orderby" not in url.params


def test_get_message_selects_body():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return json_response(GRAPH_MESSAGE)

    out = make_backend(handler).get_message("AAMk-1")

    assert seen["url"].path == "/v1.0/me/messages/AAMk-1"
    assert "body" in seen["url"].params["$select"].split(",")
    assert out["body"] == "<p>Can you confirm?</p>"


# A real-shaped Graph message id: standard base64, padded.
REAL_GRAPH_ID = (
    "AAMkADYyMTBjZGZjLTNmNGEtNDU4Yy04MTIxLTgwZDRkZGI4ZmY0NABGAAAAAAB"
    "b1n5Ct_yWQ4XpZ0ueZLRLBwC0vORhuAAAAAAAEMAAC0vORhuAAACAQwAAA="
)


def test_get_message_accepts_a_real_shaped_graph_id():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return json_response(GRAPH_MESSAGE)

    make_backend(handler).get_message(REAL_GRAPH_ID)

    assert seen["url"].path == f"/v1.0/me/messages/{REAL_GRAPH_ID}"


def test_an_id_containing_a_separator_stays_one_path_segment():
    """Graph issues standard base64 ids, so `/` and `+` are legitimate."""
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return json_response(GRAPH_MESSAGE)

    make_backend(handler).get_message("AAMk/oQ+Dw==")

    # `.path` is the decoded view; `.raw_path` is what actually goes on the wire.
    sent = seen["url"].raw_path.split(b"?")[0]
    assert sent == b"/v1.0/me/messages/AAMk%2FoQ%2BDw%3D%3D"


@pytest.mark.parametrize(
    "bad",
    [
        "../mailFolders/inbox",
        "AAMk-1/../../mailFolders",
        "AAMk-1?$select=body",
        "AAMk-1#frag",
        "AAMk 1",
    ],
)
def test_a_message_id_outside_the_graph_alphabet_is_refused(bad):
    """The id comes from a model and lands in the URL path."""
    backend = make_backend(lambda request: json_response(GRAPH_MESSAGE))
    with pytest.raises(ValueError, match="not a Microsoft Graph message id"):
        backend.get_message(bad)


@pytest.mark.parametrize("bad", ["", "   "])
def test_an_empty_message_id_is_refused(bad):
    backend = make_backend(lambda request: json_response(GRAPH_MESSAGE))
    with pytest.raises(ValueError, match="non-empty message id"):
        backend.get_message(bad)


def test_top_is_clamped_to_graph_maximum():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return json_response({"value": []})

    make_backend(handler).list_inbox(limit=5000)
    # Graph 400s on $top > 999 rather than truncating.
    assert seen["url"].params["$top"] == "999"


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_limit_is_rejected(bad):
    backend = make_backend(lambda r: json_response({"value": []}))
    with pytest.raises(ValueError, match="limit must be >= 1"):
        backend.list_inbox(limit=bad)


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_search_query_is_rejected(bad):
    backend = make_backend(lambda r: json_response({"value": []}))
    with pytest.raises(ValueError, match="non-empty search string"):
        backend.search(bad)


def test_token_is_reminted_per_request():
    """A cached token would let a mid-scan revoke look like success."""
    calls = []

    def token():
        calls.append(1)
        return f"token-{len(calls)}"

    seen = []

    def handler(request):
        seen.append(request.headers["Authorization"])
        return json_response({"value": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = OutlookReadBackend(token, http_client=client)
    backend.list_inbox()
    backend.list_inbox()

    assert seen == ["Bearer token-1", "Bearer token-2"]


# --------------------------------------------------------------------------
# errors are actionable and never leak the token
# --------------------------------------------------------------------------


def test_401_names_the_fix():
    backend = make_backend(lambda r: httpx.Response(401, text="expired"))
    with pytest.raises(MailboxAuthError) as err:
        backend.list_inbox()
    assert "gaia connectors" in str(err.value)


def test_403_names_the_missing_scope():
    backend = make_backend(lambda r: httpx.Response(403, text="denied"))
    with pytest.raises(MailboxAuthError) as err:
        backend.list_inbox()
    assert "Mail.ReadWrite" in str(err.value)


def test_429_surfaces_retry_after():
    backend = make_backend(
        lambda r: httpx.Response(429, text="slow down", headers={"Retry-After": "30"})
    )
    with pytest.raises(MailboxError, match="30"):
        backend.list_inbox()


def test_error_message_never_contains_the_bearer_token():
    backend = make_backend(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(MailboxError) as err:
        backend.list_inbox()
    assert "test-token" not in str(err.value)
    assert "Bearer" not in str(err.value)


def test_403_names_the_structured_error_code():
    backend = make_backend(lambda r: graph_error(403, code="ErrorAccessDenied"))
    with pytest.raises(MailboxAuthError) as err:
        backend.list_inbox()
    assert "ErrorAccessDenied" in str(err.value)
    assert '{"error"' not in str(err.value)


def test_403_html_body_is_dropped_not_echoed():
    backend = make_backend(
        lambda r: graph_error(403, body="<html><body>Blocked by proxy</body></html>")
    )
    with pytest.raises(MailboxAuthError) as err:
        backend.list_inbox()
    msg = str(err.value)
    assert "forbidden" in msg
    assert "Blocked by proxy" not in msg
    assert "<html" not in msg


@pytest.mark.parametrize("body", ["[1,2]", '{"error": "denied"}', '"just a string"'])
def test_403_non_object_json_body_is_dropped_not_a_crash(body):
    """A body that parses as JSON but isn't the documented `{error: {code}}`
    shape must not surface as an AttributeError."""
    backend = make_backend(lambda r: graph_error(403, body=body))
    with pytest.raises(MailboxAuthError) as err:
        backend.list_inbox()
    assert "forbidden" in str(err.value)


def test_generic_error_surfaces_structured_code_not_body():
    backend = make_backend(lambda r: graph_error(500, code="InternalServerError"))
    with pytest.raises(MailboxError) as err:
        backend.list_inbox()
    assert "InternalServerError" in str(err.value)
    assert '{"error"' not in str(err.value)


def test_network_failure_is_actionable():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(MailboxError, match="Check network connectivity"):
        make_backend(handler).list_inbox()


def test_empty_account_address_fails_loudly():
    backend = make_backend(
        lambda r: json_response({"mail": None, "userPrincipalName": ""})
    )
    with pytest.raises(MailboxError, match="unusable state"):
        backend.get_user_email()


def test_user_email_falls_back_to_principal_name():
    backend = make_backend(
        lambda r: json_response({"mail": None, "userPrincipalName": "me@example.com"})
    )
    assert backend.get_user_email() == "me@example.com"


# --------------------------------------------------------------------------
# the mixin surface
# --------------------------------------------------------------------------


class _Harness(EmailToolsMixin):
    """Minimal host for the mixin — no Agent machinery needed."""

    def __init__(self, backend):
        self._email_backend = backend
        self.tools = {}

    def _tool(self, name):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        return _TOOL_REGISTRY[name]["function"]


@pytest.fixture
def harness_factory():
    def build(handler):
        h = _Harness(make_backend(handler))
        h.register_email_tools()
        return h

    return build


def test_grant_identity_is_the_namespaced_flagship_id():
    # Must match gaia.connectors.grants' namespacing for a wheel-installed agent.
    assert EMAIL_AGENT_ID == "installed:gaia"
    assert MAIL_SCOPES == ("https://graph.microsoft.com/Mail.ReadWrite",)


def test_list_inbox_tool_returns_structured_success(harness_factory):
    h = harness_factory(lambda r: json_response({"value": [GRAPH_MESSAGE]}))
    out = json.loads(h._tool("list_inbox")(limit=5))
    assert out["success"] is True
    assert out["count"] == 1
    assert out["messages"][0]["subject"] == "Q3 numbers"


def test_search_tool_reports_relevance_ordering(harness_factory):
    """The model must not describe relevance-ordered hits as 'most recent'."""
    h = harness_factory(lambda r: json_response({"value": [GRAPH_MESSAGE]}))
    out = json.loads(h._tool("search_email")(query="q3"))
    assert out["order"] == "relevance"


def test_tool_failure_is_reported_not_swallowed(harness_factory):
    h = harness_factory(lambda r: httpx.Response(401, text="expired"))
    out = json.loads(h._tool("list_inbox")())
    assert out["success"] is False
    assert "gaia connectors" in out["error"]
    # An empty list here would read to the model as "your inbox is empty".
    assert "messages" not in out


def test_check_mailbox_access_reports_inbox_counts(harness_factory):
    def handler(request):
        if request.url.path.endswith("/me"):
            return json_response({"mail": "me@example.com"})
        return json_response(
            {
                "value": [
                    {
                        "id": "f1",
                        "displayName": "Inbox",
                        "unreadItemCount": 4,
                        "totalItemCount": 120,
                    }
                ]
            }
        )

    out = json.loads(harness_factory(handler)._tool("check_mailbox_access")())
    assert out["success"] is True
    assert out["address"] == "me@example.com"
    assert out["inbox_unread"] == 4


def test_limit_is_clamped_at_the_tool_boundary(harness_factory):
    seen = {}

    def handler(request):
        seen["top"] = request.url.params["$top"]
        return json_response({"value": []})

    harness_factory(handler)._tool("list_inbox")(limit=99999)
    assert seen["top"] == "100"


# --------------------------------------------------------------------------
# backend selection — which mailbox, and what to say when there isn't one
# --------------------------------------------------------------------------

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
MAIL_READWRITE = "https://graph.microsoft.com/Mail.ReadWrite"


@pytest.fixture
def mailbox_env(monkeypatch):
    """Point the mixin at a synthetic connector state."""

    def apply(connections, grants):
        import gaia.connectors.api as api
        import gaia.connectors.grants as grants_mod

        minted = {}
        monkeypatch.setattr(api, "get_connection", lambda p: connections.get(p))
        monkeypatch.setattr(
            grants_mod, "list_agent_grants", lambda p: dict(grants.get(p) or {})
        )

        def fake_token(*, provider, scopes, agent_id, **_):
            minted["provider"] = provider
            minted["scopes"] = list(scopes)
            minted["agent_id"] = agent_id
            return "test-token"

        monkeypatch.setattr(api, "get_access_token_sync", fake_token)
        return minted

    return apply


def connection(scopes, **extra):
    return {
        "provider": "x",
        "account_email": "me@example.com",
        "scopes": list(scopes),
        **extra,
    }


class _Bare(EmailToolsMixin):
    """The mixin with nothing pre-wired, so selection actually runs."""


@pytest.mark.parametrize(
    "connections,grants,provider,scope",
    [
        # Google connected read-only and granted -> Gmail, readonly.
        (
            {"google": connection([GMAIL_READONLY])},
            {"google": {"installed:gaia": [GMAIL_READONLY]}},
            "google",
            GMAIL_READONLY,
        ),
        # The measured box: the connection carries modify, so modify is what
        # gets requested -- asking for readonly would force a reconnect.
        (
            {"google": connection([GMAIL_MODIFY])},
            {"google": {"installed:gaia": [GMAIL_MODIFY]}},
            "google",
            GMAIL_MODIFY,
        ),
        # Microsoft only.
        (
            {"microsoft": connection([MAIL_READWRITE])},
            {"microsoft": {"installed:gaia": [MAIL_READWRITE]}},
            "microsoft",
            MAIL_READWRITE,
        ),
        # Google connected but not granted; Microsoft usable -> Microsoft.
        (
            {
                "google": connection([GMAIL_MODIFY]),
                "microsoft": connection([MAIL_READWRITE]),
            },
            {"microsoft": {"installed:gaia": [MAIL_READWRITE]}},
            "microsoft",
            MAIL_READWRITE,
        ),
    ],
)
def test_backend_selected_from_resolved_read_capability(
    mailbox_env, connections, grants, provider, scope
):
    minted = mailbox_env(connections, grants)
    backend = _Bare()._build_email_backend()

    expected = "GmailReadBackend" if provider == "google" else "OutlookReadBackend"
    assert type(backend).__name__ == expected

    backend._access_token_fn()
    assert minted["provider"] == provider
    # Exactly the one resolved scope, never the pair.
    assert minted["scopes"] == [scope]
    assert minted["agent_id"] == EMAIL_AGENT_ID


def test_both_usable_prefers_google_and_announces_the_alternative(mailbox_env):
    mailbox_env(
        {
            "google": connection([GMAIL_READONLY]),
            "microsoft": connection([MAIL_READWRITE]),
        },
        {
            "google": {"installed:gaia": [GMAIL_READONLY]},
            "microsoft": {"installed:gaia": [MAIL_READWRITE]},
        },
    )
    mixin = _Bare()
    mixin._build_email_backend()
    assert mixin._email_provider == "google"
    assert mixin._email_provider_source == "precedence"
    assert mixin._email_alternatives == ["microsoft"]


def test_revoking_the_grant_switches_the_selected_mailbox(mailbox_env):
    """The only way to change the pick, and the one `check_mailbox_access` names.

    Selection is connector-derived on purpose: an env-var override would choose
    a mailbox without consulting the grant ledger, which is the gate that makes
    the choice auditable in the first place.
    """
    both_connected = {
        "google": connection([GMAIL_READONLY]),
        "microsoft": connection([MAIL_READWRITE]),
    }
    mailbox_env(
        both_connected,
        {
            "google": {"installed:gaia": [GMAIL_READONLY]},
            "microsoft": {"installed:gaia": [MAIL_READWRITE]},
        },
    )
    mixin = _Bare()
    mixin._build_email_backend()
    assert mixin._email_provider == "google"

    # `gaia connectors grants revoke google installed:gaia` — the google row
    # is gone, so microsoft becomes the only eligible mailbox.
    mailbox_env(both_connected, {"microsoft": {"installed:gaia": [MAIL_READWRITE]}})
    switched = _Bare()
    switched._build_email_backend()
    assert switched._email_provider == "microsoft"
    assert switched._email_provider_source == "only-granted"
    assert switched._email_alternatives == []


def test_no_environment_variable_can_choose_the_mailbox(mailbox_env, monkeypatch):
    """A hidden env override would bypass the grant gate entirely."""
    mailbox_env(
        {
            "google": connection([GMAIL_READONLY]),
            "microsoft": connection([MAIL_READWRITE]),
        },
        {
            "google": {"installed:gaia": [GMAIL_READONLY]},
            "microsoft": {"installed:gaia": [MAIL_READWRITE]},
        },
    )
    for name in ("GAIA_MAIL_PROVIDER", "GAIA_EMAIL_PROVIDER", "MAIL_PROVIDER"):
        monkeypatch.setenv(name, "microsoft")

    mixin = _Bare()
    mixin._build_email_backend()
    assert mixin._email_provider == "google"
    assert mixin._email_provider_source == "precedence"


@pytest.mark.parametrize(
    "connections,grants,needle",
    [
        # NOT_CONNECTED
        ({}, {}, "gaia connectors connect google"),
        # MISSING_SCOPES — remedy must carry granted UNION needed, because
        # `--scopes` REPLACES a connection's scopes rather than adding to them.
        (
            {
                "google": connection(
                    ["https://www.googleapis.com/auth/calendar.readonly"]
                )
            },
            {},
            "calendar.readonly",
        ),
        # NOT_GRANTED — a ledger write, not a browser reconnect.
        (
            {"google": connection([GMAIL_MODIFY])},
            {},
            "gaia connectors grants grant google installed:gaia",
        ),
        # REAUTH_REQUIRED
        (
            {"google": {"provider": "google", "scopes": [], "error": "configuration"}},
            {},
            "OAuth client",
        ),
    ],
)
def test_no_mailbox_error_names_each_providers_own_state(
    mailbox_env, connections, grants, needle
):
    from gaia.agents.tools._email import MailboxError

    mailbox_env(connections, grants)
    with pytest.raises(MailboxError) as err:
        _Bare()._build_email_backend()
    message = str(err.value)
    assert needle in message
    # Both mailboxes are named, each with its own state.
    assert "google" in message and "microsoft" in message


def test_missing_scopes_remedy_never_names_only_the_gap(mailbox_env):
    """`--scopes` replaces, so a gap-only remedy strips what the user had."""
    from gaia.agents.tools._email import MailboxError

    mailbox_env(
        {"google": connection(["https://www.googleapis.com/auth/calendar.events"])},
        {},
    )
    with pytest.raises(MailboxError) as err:
        _Bare()._build_email_backend()
    message = str(err.value)
    assert "calendar.events" in message
    assert GMAIL_READONLY in message


def test_the_full_mailbox_scope_is_never_requested(mailbox_env):
    from gaia.agents.tools._email.scopes import SCOPE_GMAIL_FULL_MAILBOX

    minted = mailbox_env(
        {"google": connection([GMAIL_READONLY, SCOPE_GMAIL_FULL_MAILBOX])},
        {"google": {"installed:gaia": [GMAIL_READONLY, SCOPE_GMAIL_FULL_MAILBOX]}},
    )
    _Bare()._build_email_backend()._access_token_fn()
    assert minted["scopes"] == [GMAIL_READONLY]


def test_backend_reresolves_once_after_an_auth_failure(harness_factory):
    """A grant made mid-session must not need a restart to take effect."""
    builds = []

    class Flaky(_Harness):
        def _build_email_backend(self):
            builds.append(1)
            if len(builds) == 1:
                return make_backend(lambda r: httpx.Response(401, text="expired"))
            return make_backend(lambda r: json_response({"value": [GRAPH_MESSAGE]}))

    h = Flaky(backend=None)
    h._email_backend = None
    h.register_email_tools()
    out = json.loads(h._tool("list_inbox")())

    assert out["success"] is True
    assert len(builds) == 2


def test_a_second_auth_failure_is_surfaced_not_retried_forever(harness_factory):
    builds = []

    class AlwaysDead(_Harness):
        def _build_email_backend(self):
            builds.append(1)
            return make_backend(lambda r: httpx.Response(401, text="expired"))

    h = AlwaysDead(backend=None)
    h._email_backend = None
    h.register_email_tools()
    out = json.loads(h._tool("list_inbox")())

    assert out["success"] is False
    assert len(builds) == 2


def test_check_mailbox_access_reports_the_resolved_provider(harness_factory):
    def handler(request):
        if request.url.path.endswith("/me"):
            return json_response({"mail": "me@example.com"})
        return json_response({"value": []})

    h = harness_factory(handler)
    h._email_provider = "google"
    h._email_provider_source = "precedence"
    h._email_alternatives = ["microsoft"]
    out = json.loads(h._tool("check_mailbox_access")())

    assert out["provider"] == "google"
    assert out["provider_source"] == "precedence"
    assert out["alternatives"] == ["microsoft"]


def test_read_email_body_is_bounded_and_truncation_is_visible(harness_factory):
    """Quoted thread history is unbounded; the NPU profile runs a 32K window."""
    from gaia.agents.tools.email_tools import _MAX_BODY_CHARS

    huge = "x" * (_MAX_BODY_CHARS + 5000)
    message = dict(GRAPH_MESSAGE, body={"contentType": "text", "content": huge})
    h = harness_factory(lambda r: json_response(message))

    out = json.loads(h._tool("read_email")(message_id="AAMk-1"))["message"]
    assert len(unfenced(out)) == _MAX_BODY_CHARS
    assert out["body_truncated"] is True
    assert out["body_original_chars"] == len(huge)


def test_a_short_body_is_not_marked_truncated(harness_factory):
    h = harness_factory(lambda r: json_response(GRAPH_MESSAGE))
    out = json.loads(h._tool("read_email")(message_id="AAMk-1"))["message"]
    assert "body_truncated" not in out
    assert unfenced(out) == "<p>Can you confirm?</p>"


def test_backend_is_not_built_until_a_tool_runs():
    """Composing the mixin must not touch the connectors layer."""

    class Eager(EmailToolsMixin):
        def _build_email_backend(self):
            raise AssertionError("backend built too early")

    Eager().register_email_tools()  # must not raise


# --------------------------------------------------------------------------
# skill <-> mixin drift
# --------------------------------------------------------------------------


def _inbox_triage_skill():
    from pathlib import Path

    from gaia.skills.format import parse_skill

    root = Path(__file__).resolve().parents[4]
    path = root / "hub" / "skills" / "inbox-triage" / "SKILL.md"
    return parse_skill(path.read_text(encoding="utf-8"), source=str(path))


def test_skill_tools_required_match_the_registered_tools():
    """A tool rename must not leave the skill silently pointing at nothing.

    ``tools_required`` is what feeds ToolLoader's SKILL term, and semantic
    selection alone does not reliably reach these tools (see the PR notes), so
    a stale name here is the difference between the skill working and quietly
    doing nothing.
    """
    harness = _Harness(backend=None)
    harness.register_email_tools()

    from gaia.agents.base.tools import _TOOL_REGISTRY

    registered = {
        "check_mailbox_access",
        "list_inbox",
        "search_email",
        "read_email",
        "list_mail_folders",
    }
    assert registered <= set(_TOOL_REGISTRY)
    assert set(_inbox_triage_skill().gaia.tools_required) == registered


def test_skill_declares_no_permissions():
    """The capability is agent code, so the skill needs no permission grant.

    This is what keeps the work off #2863's critical path: a skill declaring a
    local-capability domain is refused at load, and this one declares none.
    """
    from gaia.skills.permissions import refuse_unbridged_permissions

    skill = _inbox_triage_skill()
    assert skill.gaia.permissions == []
    refuse_unbridged_permissions(skill.parsed_permissions(), skill_name=skill.name)


def test_email_tools_are_bundled_for_the_loader():
    """An unbundled tool can never be pulled in with its cohort."""
    # The chat agent is a separate hub package; the core unit-test job does not
    # install it. "Test Chat Agent" and "Test Gaia Agent" do, and run this there.
    pytest.importorskip("gaia_agent_chat")
    from gaia_agent_chat.tool_bundles import PROFILE_TOOL_CONFIGS

    bundles = PROFILE_TOOL_CONFIGS["full"].bundles
    email = next(
        (b for b in bundles if b.name == "email"),
        None,
    )
    assert email is not None, (
        "the 'email' bundle is missing from the full profile: add it to "
        "gaia_agent_chat.tool_bundles.FULL_BUNDLES or the loader can never "
        "pull the email tools in as a cohort"
    )
    assert email.members == set(_inbox_triage_skill().gaia.tools_required)


# --------------------------------------------------------------------------
# per-turn read budget
# --------------------------------------------------------------------------


def _big_body_message(chars):
    return dict(GRAPH_MESSAGE, body={"contentType": "text", "content": "x" * chars})


def test_turn_budget_refuses_once_the_turn_is_full(harness_factory):
    from gaia.agents.tools.email_tools import _MAX_BODY_CHARS

    h = harness_factory(lambda r: json_response(_big_body_message(_MAX_BODY_CHARS)))
    h._turn_seq = 1

    results = []
    for _ in range(6):
        results.append(json.loads(h._tool("read_email")(message_id="AAMk-1")))
        if results[-1]["success"] is False:
            break

    successes = [r for r in results if r["success"] is True]
    refusal = results[-1]
    assert len(successes) >= 2
    assert refusal["success"] is False
    assert refusal["turn_budget_exhausted"] is True
    assert "message" not in refusal

    budget = h._email_turn_budget_chars()
    total_charged = sum(len(unfenced(r["message"])) for r in successes)
    assert total_charged == h._email_turn_body_chars
    assert total_charged <= budget + _MAX_BODY_CHARS


def test_turn_budget_refusal_tells_the_model_to_stop_and_say_so(harness_factory):
    from gaia.agents.tools.email_tools import _MAX_BODY_CHARS

    h = harness_factory(lambda r: json_response(_big_body_message(_MAX_BODY_CHARS)))
    h._turn_seq = 1
    h._email_turn_body_chars = h._email_turn_budget_chars()
    h._email_turn_token = 1
    h._email_turn_reads = 3

    out = json.loads(h._tool("read_email")(message_id="AAMk-1"))
    assert out["success"] is False
    error = out["error"].lower()
    assert "tell the user" in error
    assert "stopped" in error
    assert "new turn" in error or "narrow" in error


def test_turn_budget_resets_on_a_new_turn(harness_factory):
    from gaia.agents.tools.email_tools import _MAX_BODY_CHARS

    h = harness_factory(lambda r: json_response(_big_body_message(_MAX_BODY_CHARS)))
    h._turn_seq = 1
    h._email_turn_body_chars = h._email_turn_budget_chars()
    h._email_turn_token = 1
    h._email_turn_reads = 3

    refused = json.loads(h._tool("read_email")(message_id="AAMk-1"))
    assert refused["success"] is False

    h._turn_seq = 2
    admitted = json.loads(h._tool("read_email")(message_id="AAMk-1"))
    assert admitted["success"] is True


def test_turn_budget_is_derived_from_the_device_profile():
    from gaia.llm.lemonade_client import (
        GPU_CTX_SIZE,
        NPU_CTX_SIZE,
        budget_for_ctx,
        truncation_budget,
    )

    class _ProfileHost(EmailToolsMixin):
        def __init__(self, ctx_size):
            self._ctx_size = ctx_size

        def _truncation_budget(self):
            return budget_for_ctx(self._ctx_size)

    npu_budget = _ProfileHost(NPU_CTX_SIZE)._email_turn_budget_chars()
    gpu_budget = _ProfileHost(GPU_CTX_SIZE)._email_turn_budget_chars()
    assert npu_budget == budget_for_ctx(NPU_CTX_SIZE)[0]
    assert gpu_budget == budget_for_ctx(GPU_CTX_SIZE)[0]
    assert npu_budget != gpu_budget

    class _DeviceHost(EmailToolsMixin):
        def __init__(self, device):
            self.device = device

    assert _DeviceHost("npu")._email_turn_budget_chars() == truncation_budget("npu")[0]
    assert _DeviceHost("gpu")._email_turn_budget_chars() == truncation_budget("gpu")[0]
    assert _DeviceHost(None)._email_turn_budget_chars() == truncation_budget(None)[0]


def test_turn_budget_guard_runs_before_the_backend_call():
    """The refusal must not depend on which backend is behind the mailbox."""

    class _AssertingHost(EmailToolsMixin):
        _email_backend = object()  # any truthy sentinel; must never be used

        def _email_call(self, *args, **kwargs):
            raise AssertionError("backend must not be called once the turn is full")

        def _tool(self, name):
            from gaia.agents.base.tools import _TOOL_REGISTRY

            return _TOOL_REGISTRY[name]["function"]

    h = _AssertingHost()
    h.register_email_tools()
    h._turn_seq = 1
    budget = h._email_turn_budget_chars()
    h._email_turn_token = 1
    h._email_turn_body_chars = budget
    h._email_turn_reads = 1

    out = json.loads(h._tool("read_email")(message_id="AAMk-1"))
    assert out["success"] is False
    assert out["turn_budget_exhausted"] is True


def test_turn_budget_guard_would_fail_if_the_admission_check_were_removed(
    harness_factory,
):
    """Documents the invariant: deleting the ``used >= budget`` check breaks this."""
    from gaia.agents.tools.email_tools import _MAX_BODY_CHARS

    h = harness_factory(lambda r: json_response(_big_body_message(_MAX_BODY_CHARS)))
    h._turn_seq = 1
    budget = h._email_turn_budget_chars()
    h._email_turn_token = 1
    h._email_turn_body_chars = budget
    h._email_turn_reads = 5

    out = json.loads(h._tool("read_email")(message_id="AAMk-1"))
    # Without the admission check this would be a success carrying a body —
    # the assertion below is the one a deleted guard would fail.
    assert out["success"] is False
    assert "message" not in out


def test_turn_counter_increments_without_agent_init():
    """A subclass that never runs ``Agent.__init__`` must still count turns.

    Test doubles and lightweight subclasses build instances directly; a
    counter that only exists after ``__init__`` raises ``AttributeError``
    in the turn-setup path for all of them.
    """
    from gaia.agents.base.agent import Agent

    assert Agent._turn_seq == 0

    class _NoInit(Agent):
        def __init__(self):  # pylint: disable=super-init-not-called
            pass

        def _register_tools(self):
            pass

    bare = _NoInit()
    bare._turn_seq += 1
    assert bare._turn_seq == 1
    assert Agent._turn_seq == 0


# --------------------------------------------------------------------------
# recollection search — broadening a query the provider ANDs down to zero
# --------------------------------------------------------------------------


def _mailbox_matching(*matching_queries):
    """A Graph handler whose mailbox only answers the given `$search` terms.

    Models the real failure: every term is ANDed, so the long paraphrase the
    model builds from a user's recollection matches nothing while two words
    from the message match immediately.
    """
    wanted = {q.lower() for q in matching_queries}
    seen = []

    def handler(request):
        term = (request.url.params.get("$search") or "").strip('"')
        seen.append(term)
        hit = term.lower() in wanted
        return json_response({"value": [GRAPH_MESSAGE] if hit else []})

    return handler, seen


def test_search_docstring_warns_that_terms_are_anded(harness_factory):
    """The model must be told a longer query is a narrower one.

    Without this the natural translation of a sentence into keywords is
    always wrong, and the zero result reads as "no such mail".
    """
    h = harness_factory(lambda r: json_response({"value": []}))
    doc = h._tool("search_email").__doc__.lower()

    assert "anded" in doc and "narrower" in doc


def test_zero_result_query_is_broadened_until_it_hits(harness_factory):
    handler, seen = _mailbox_matching("argument cameras")
    h = harness_factory(handler)

    out = json.loads(
        h._tool("search_email")(query="the argument over cameras police use")
    )

    assert out["count"] == 1
    assert out["broadened"] is True
    assert out["query_used"] == "argument cameras"
    assert seen[0] == "the argument over cameras police use"  # full query tried first
    assert "argument cameras" in seen


def test_broadened_result_names_the_query_that_produced_it(harness_factory):
    handler, _ = _mailbox_matching("contract counter-signature")
    h = harness_factory(handler)

    out = json.loads(
        h._tool("search_email")(
            query="please sign off on the contract counter-signature schedule"
        )
    )

    assert out["query_requested"] == (
        "please sign off on the contract counter-signature schedule"
    )
    assert out["query_used"] == "contract counter-signature"
    assert [a["count"] for a in out["attempts"]][:3] == [0, 0, 1]
    # A hit on rung three is not a reason to stop looking for better ones.
    assert len(out["attempts"]) > 3
    # The model must not present a looser match as an exact one.
    assert out["exact_match"] is False
    assert out["unverified"] is True


def test_absent_message_still_reports_zero_after_broadening(harness_factory):
    """The case a naive retry loop gets wrong: broadening must not invent a hit."""
    handler, seen = _mailbox_matching()  # nothing matches, ever
    h = harness_factory(handler)

    out = json.loads(h._tool("search_email")(query="quarterly budget from Dana"))

    assert out["success"] is True
    assert out["count"] == 0
    assert out["messages"] == []
    assert len(seen) == len(out["attempts"]) >= 2
    assert len(seen) <= 6  # broadening is bounded, not an unbounded retry loop
    assert all(a["count"] == 0 for a in out["attempts"])
    assert "No message matched" in out["note"]


def test_a_query_that_hits_as_sent_keeps_its_own_results(harness_factory):
    """Broader rungs may still run, but they never displace an exact hit.

    The sweep buys candidates for the case the exact hit is the wrong mail;
    it must not cost precision when the exact hit is the right mail.
    """
    handler, seen = _mailbox_matching("flock newsletter")
    h = harness_factory(handler)

    out = json.loads(h._tool("search_email")(query="flock newsletter"))

    assert out["count"] == 1
    assert out["broadened"] is False
    assert out["exact_match"] is True
    assert out["query_used"] == "flock newsletter"
    assert seen[0] == "flock newsletter"
    assert "alternatives" not in out  # no looser rung matched anything


def test_empty_query_still_fails_loudly(harness_factory):
    """Broadening must not turn a rejected query into a silent empty result."""
    h = harness_factory(lambda r: json_response({"value": []}))

    out = json.loads(h._tool("search_email")(query="   "))

    assert out["success"] is False
    assert "non-empty search string" in out["error"]


@pytest.mark.parametrize(
    "query, expected",
    [
        # Nothing to drop — the pair rung is the query itself, so it is skipped.
        ("flock cameras", ["flock cameras", "cameras", "flock"]),
        # Stopwords go first, then the two longest terms, then one at a time.
        (
            "the argument over surveillance cameras police",
            [
                "the argument over surveillance cameras police",
                "argument surveillance cameras police",
                "argument surveillance",
                "surveillance",
                "argument",
                "cameras",
            ],
        ),
        # A boolean the model emitted is operator noise, not a keyword.
        (
            "camera debate OR argument policy",
            [
                "camera debate OR argument policy",
                "camera debate argument policy",
                "camera argument",
                "argument",
                "camera",
                "debate",
            ],
        ),
        # A quoted phrase stays one term, whole, at every rung.
        (
            '"Fieldstone MSA" needs a counter-signature',
            [
                '"Fieldstone MSA" needs a counter-signature',
                '"Fieldstone MSA" counter-signature',
                "counter-signature",
                '"Fieldstone MSA"',
            ],
        ),
        # An all-stopword query has no content rung to fall back to.
        ("did you get it", ["did you get it"]),
    ],
)
def test_broadening_ladder_is_deterministic(query, expected):
    from gaia.agents.tools.email_tools import _broadening_ladder

    assert _broadening_ladder(query) == expected


def test_no_broadened_rung_is_a_bare_mailbox_filter():
    """A rung of only operators returns a slice of the mailbox, not a match.

    ``is:unread`` alone is non-empty for almost every mailbox, so the ladder
    would stop there and hand the model 25 unrelated messages labelled as a
    broadened hit — the confident wrong answer this feature exists to prevent.
    """
    from gaia.agents.tools.email_tools import _broadening_ladder

    def names_content(term):
        """Independent of the implementation: does this term say what to find?"""
        field, separator, _ = term.partition(":")
        return not term.startswith("-") and not (separator and field.isidentifier())

    for query in (
        "is:unread flock",
        "invoice newer_than:7d",
        "from:dana invoice",
        "label:work contract signature",
        "-promo receipt",
    ):
        for rung in _broadening_ladder(query):
            assert any(
                names_content(t) for t in rung.split()
            ), f"{query!r} produced an operator-only rung: {rung!r}"

    # An operator-only query is still run once, exactly as asked.
    assert _broadening_ladder("is:unread") == ["is:unread"]


def test_surrounding_whitespace_is_not_a_broadening(harness_factory):
    """``broadened`` must track terms, not spacing.

    Small models emit trailing and doubled spaces constantly. Reporting one
    as a broadening tells the model to hedge about an exact first-try hit.
    """
    handler, seen = _mailbox_matching("Acme invoice")
    h = harness_factory(handler)

    out = json.loads(h._tool("search_email")(query=" Acme  invoice "))

    assert out["count"] == 1
    assert out["broadened"] is False
    assert "note" not in out  # nothing to hedge about
    assert seen[0] == "Acme invoice"  # the query as sent is tried first


def test_mid_ladder_backend_failure_is_reported_not_swallowed(harness_factory):
    """A rung that errors must surface the error, not read as 'no such mail'."""
    calls = []

    def handler(request):
        calls.append((request.url.params.get("$search") or "").strip('"'))
        if len(calls) == 1:
            return json_response({"value": []})
        return httpx.Response(401, text="expired")

    h = harness_factory(handler)

    out = json.loads(h._tool("search_email")(query="quarterly budget Dana"))

    assert out["success"] is False
    assert "gaia connectors" in out["error"]
    # An empty result here would read to the model as a genuine miss.
    assert "messages" not in out and "count" not in out
    assert len(calls) == 2


def test_limit_is_clamped_on_every_broadened_rung(harness_factory):
    """The clamp lives inside the loop; a rung outside it would send limit raw."""
    from gaia.agents.tools.email_tools import _MAX_LIMIT

    tops = []

    def handler(request):
        tops.append(int(request.url.params["$top"]))
        return json_response({"value": []})

    h = harness_factory(handler)

    json.loads(h._tool("search_email")(query="quarterly budget from Dana", limit=500))

    assert len(tops) >= 3  # the ladder really did run several rungs
    assert set(tops) == {_MAX_LIMIT}


# --------------------------------------------------------------------------
# recollection search — a non-empty result is not evidence of a correct one
# --------------------------------------------------------------------------


def _graph_message(mid, *, thread=None, subject="Subject"):
    msg = dict(GRAPH_MESSAGE)
    msg["id"] = mid
    msg["conversationId"] = thread or mid
    msg["subject"] = subject
    return msg


FIELDSTONE = _graph_message(
    "AAMk-fieldstone",
    thread="conv-fieldstone",
    subject="Re: Fieldstone MSA - counter-signature needed before Friday",
)
WRONG_ONE = _graph_message(
    "AAMk-wrong1", thread="conv-wrong1", subject="Contract schedule v2"
)
WRONG_TWO = _graph_message(
    "AAMk-wrong2", thread="conv-wrong2", subject="Schedule of contract exhibits"
)


LURE = _graph_message(
    "AAMk-lure",
    thread="conv-lure",
    subject="Verify your account now - click the link to avoid suspension",
)


def _mailbox_by_term(index):
    """A Graph handler whose ``$search`` term maps to a fixed hit list.

    Honours ``$top`` the way Graph does, so an over-fetch a real mailbox would
    truncate is truncated here too.
    """
    seen = []

    def handler(request):
        term = (request.url.params.get("$search") or "").strip('"')
        seen.append(term)
        top = int(request.url.params.get("$top") or 25)
        return json_response({"value": index.get(term.lower(), [])[:top]})

    return handler, seen


def test_a_non_empty_first_rung_is_not_the_end_of_the_search(harness_factory):
    """The bug: 2-3 plausible-but-wrong hits look like success and end the search.

    The user's own words match mail they did not mean, so the ladder never
    runs and the model answers confidently from the wrong thread.
    """
    handler, _ = _mailbox_by_term(
        {
            "contract schedule": [WRONG_ONE, WRONG_TWO],
            "contract": [WRONG_ONE, FIELDSTONE],
            "schedule": [WRONG_TWO],
        }
    )
    h = harness_factory(handler)

    out = json.loads(h._tool("search_email")(query="contract schedule"))

    assert len(out["attempts"]) > 1
    assert [m["subject"] for m in out["messages"]] == [
        "Contract schedule v2",
        "Schedule of contract exhibits",
    ]
    assert any("Fieldstone" in m["subject"] for m in out["alternatives"])


def test_broadening_does_not_stop_at_the_first_non_empty_rung(harness_factory):
    """A rung that returns *something* is not a rung that returns the message."""
    handler, _ = _mailbox_by_term(
        {
            "contract schedule": [WRONG_ONE, WRONG_TWO],
            "contract": [FIELDSTONE],
        }
    )
    h = harness_factory(handler)

    out = json.loads(
        h._tool("search_email")(
            query="sign off on a contract schedule before the end of the week"
        )
    )

    assert any("Fieldstone" in m["subject"] for m in out["messages"])
    assert out["unverified"] is True


def test_results_are_one_hit_per_thread(harness_factory):
    """One conversation must not spend every result slot."""
    one_thread = [
        _graph_message(f"AAMk-t{i}", thread="conv-one", subject="Re: counter-signature")
        for i in range(5)
    ]
    others = [
        _graph_message(f"AAMk-o{i}", thread=f"conv-o{i}", subject=f"Other {i}")
        for i in range(4)
    ]
    handler, _ = _mailbox_by_term({"counter-signature": one_thread + others})
    h = harness_factory(handler)

    out = json.loads(h._tool("search_email")(query="counter-signature", limit=5))

    threads = [m["thread_id"] for m in out["messages"]]
    assert len(threads) == len(set(threads)) == 5
    assert out["messages"][0]["thread_message_matches"] == 5


def test_a_message_found_by_two_rungs_is_counted_once(harness_factory):
    """Rungs overlap; re-finding one message is not a second message."""
    handler, _ = _mailbox_by_term({"acme invoice": [WRONG_ONE], "invoice": [WRONG_ONE]})
    h = harness_factory(handler)

    out = json.loads(h._tool("search_email")(query="Acme invoice"))

    assert out["count"] == 1
    assert "thread_message_matches" not in out["messages"][0]
    assert "alternatives" not in out


def test_results_the_query_never_matched_are_marked_unverified(harness_factory):
    """A set the tool cannot vouch for says so, and says what to do instead."""
    handler, _ = _mailbox_by_term({"contract": [WRONG_ONE]})
    h = harness_factory(handler)

    out = json.loads(
        h._tool("search_email")(
            query="sign off on a contract schedule before the end of the week"
        )
    )

    assert out["unverified"] is True
    assert out["exact_match"] is False
    assert out["messages"][0]["matched_query"] == "contract"
    note = out["note"].lower()
    assert "ask the user" in note
    # The lexical gap this bug lives in closes only if the model re-queries
    # with the sender's vocabulary, so the note has to ask for exactly that.
    assert "search again" in note


def test_a_healthy_exact_result_costs_one_round_trip(harness_factory):
    """The sweep is for thin results; a full first rung must not pay for it."""
    plenty = [
        _graph_message(f"AAMk-p{i}", thread=f"conv-p{i}", subject=f"Invoice {i}")
        for i in range(6)
    ]
    handler, seen = _mailbox_by_term({"acme invoice": plenty})
    h = harness_factory(handler)

    out = json.loads(h._tool("search_email")(query="Acme invoice"))

    assert out["count"] == 6
    assert seen == ["Acme invoice"]


def test_a_lure_reached_only_by_broadening_is_still_screened(harness_factory):
    """Broadened hits are returned to the model, so they are screened too.

    Neither half of this behaviour covers it alone: the screen ran on the one
    result list that used to exist, and broadening invented a second one.
    """
    handler, _ = _mailbox_by_term({"contract": [LURE]})
    h = harness_factory(handler)

    out = json.loads(
        h._tool("search_email")(
            query="sign off on a contract schedule before the end of the week"
        )
    )

    assert out["unverified"] is True
    assert out["messages"][0]["suspicious"] is True
    assert out["messages"][0]["suspicious_reasons"]
    assert out["suspicious_count"] == 1
    assert "suspicious_guidance" in out


def test_a_flagged_hit_is_never_a_source_of_search_vocabulary(harness_factory):
    """The re-query instruction must not point at attacker-written text.

    A mixed set is the realistic shape: a guard that only fires when every hit
    is flagged would pass a test built from lures alone and fail here.
    """
    handler, _ = _mailbox_by_term({"contract": [WRONG_ONE, LURE]})
    h = harness_factory(handler)

    out = json.loads(
        h._tool("search_email")(
            query="sign off on a contract schedule before the end of the week"
        )
    )

    assert out["suspicious_count"] == 1
    assert "suspicious" not in out["messages"][0]
    assert out["messages"][1]["suspicious"] is True
    note = out["note"]
    assert "search again with the words the sender would have written" in note
    assert "only from hits NOT marked `suspicious`" in note


def test_an_exact_hit_is_never_labelled_unverified(harness_factory):
    handler, _ = _mailbox_by_term({"flock newsletter": [FIELDSTONE]})
    h = harness_factory(handler)

    out = json.loads(h._tool("search_email")(query="flock newsletter"))

    assert out["exact_match"] is True
    assert out["broadened"] is False
    assert "unverified" not in out
