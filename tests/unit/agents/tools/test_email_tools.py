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


def test_env_override_picks_the_other_mailbox(mailbox_env, monkeypatch):
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
    monkeypatch.setenv("GAIA_MAIL_PROVIDER", "microsoft")
    mixin = _Bare()
    mixin._build_email_backend()
    assert mixin._email_provider == "microsoft"
    assert mixin._email_provider_source == "env-override"


def test_env_override_naming_an_unusable_mailbox_fails_loudly(mailbox_env, monkeypatch):
    from gaia.agents.tools._email import MailboxError

    mailbox_env(
        {"google": connection([GMAIL_READONLY])},
        {"google": {"installed:gaia": [GMAIL_READONLY]}},
    )
    monkeypatch.setenv("GAIA_MAIL_PROVIDER", "microsoft")
    with pytest.raises(MailboxError) as err:
        _Bare()._build_email_backend()
    assert "GAIA_MAIL_PROVIDER" in str(err.value)
    assert "microsoft" in str(err.value)


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
    assert len(out["body"]) == _MAX_BODY_CHARS
    assert out["body_truncated"] is True
    assert out["body_original_chars"] == len(huge)


def test_a_short_body_is_not_marked_truncated(harness_factory):
    h = harness_factory(lambda r: json_response(GRAPH_MESSAGE))
    out = json.loads(h._tool("read_email")(message_id="AAMk-1"))["message"]
    assert "body_truncated" not in out
    assert out["body"] == "<p>Can you confirm?</p>"


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
