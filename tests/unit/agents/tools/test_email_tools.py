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
    from gaia_agent_chat.tool_bundles import PROFILE_TOOL_CONFIGS

    bundles = PROFILE_TOOL_CONFIGS["full"].bundles
    email = next(b for b in bundles if b.name == "email")
    assert email.members == set(_inbox_triage_skill().gaia.tools_required)
