# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read-only Gmail backend for the flagship agent.

Like the Graph tests next door, these drive a real ``httpx`` client over a
``MockTransport`` so they assert the *shape of the outgoing Gmail request* —
path, ``format``, ``metadataHeaders``, ``labelIds`` — not merely that a stub
was called. Gmail 400s on a label *name* where an ID is required and ignores
``metadataHeaders`` unless ``format=metadata`` is sent with it; a protocol
double accepts both.
"""

import httpx
import pytest

from gaia.agents.tools._email.gmail import (
    GMAIL_API_BASE,
    GmailReadBackend,
)
from gaia.agents.tools._email.errors import MailboxAuthError, MailboxError


def make_backend(handler, **kwargs):
    """A GmailReadBackend whose HTTP goes to `handler`, with a fixed token."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GmailReadBackend(lambda: "test-token", http_client=client, **kwargs)


def json_response(payload, status=200):
    return httpx.Response(status, json=payload)


def gmail_error(status, reason, message="boom", extended_help=None):
    err = {"message": message, "domain": "global", "reason": reason}
    if extended_help:
        err["extendedHelp"] = extended_help
    return httpx.Response(
        status,
        json={"error": {"code": status, "message": message, "errors": [err]}},
    )


# --------------------------------------------------------------------------
# Inc 1 — HTTP layer + get_user_email
# --------------------------------------------------------------------------


def test_gmail_profile_path_and_actionable_errors():
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return json_response({"emailAddress": "me@example.com"})

    assert make_backend(handler).get_user_email() == "me@example.com"
    assert seen["url"].path == "/gmail/v1/users/me/profile"
    assert str(seen["url"]).startswith(GMAIL_API_BASE)


def test_gmail_profile_without_an_address_fails_loudly():
    backend = make_backend(lambda r: json_response({"emailAddress": ""}))
    with pytest.raises(MailboxError, match="unusable state"):
        backend.get_user_email()


@pytest.mark.parametrize(
    "status,reason,needle",
    [
        (401, "authError", "gaia connectors"),
        (403, "insufficientPermissions", "gmail"),
    ],
)
def test_auth_failures_name_what_to_do(status, reason, needle):
    backend = make_backend(lambda r: gmail_error(status, reason))
    with pytest.raises(MailboxAuthError) as err:
        backend.get_user_email()
    assert needle in str(err.value).lower()


def test_access_not_configured_surfaces_the_enable_url():
    """Gmail's own remedy link is the only actionable part of this 403."""
    url = "https://console.developers.google.com/apis/api/gmail.googleapis.com/overview"
    backend = make_backend(
        lambda r: gmail_error(403, "accessNotConfigured", extended_help=url)
    )
    with pytest.raises(MailboxAuthError) as err:
        backend.get_user_email()
    assert url in str(err.value)


def test_429_surfaces_retry_after():
    backend = make_backend(
        lambda r: httpx.Response(429, text="slow down", headers={"Retry-After": "30"})
    )
    with pytest.raises(MailboxError, match="30"):
        backend.get_user_email()


def test_gmail_error_never_contains_the_bearer_token():
    backend = make_backend(lambda r: httpx.Response(500, text="test-token leaked"))
    with pytest.raises(MailboxError) as err:
        backend.get_user_email()
    assert "test-token" not in str(err.value)
    assert "Bearer" not in str(err.value)


def test_network_failure_is_actionable():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(MailboxError, match="Check network connectivity"):
        make_backend(handler).get_user_email()


def test_token_is_reminted_per_single_request():
    calls = []
    seen = []

    def token():
        calls.append(1)
        return f"token-{len(calls)}"

    def handler(request):
        seen.append(request.headers["Authorization"])
        return json_response({"emailAddress": "me@example.com"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = GmailReadBackend(token, http_client=client)
    backend.get_user_email()
    backend.get_user_email()

    assert seen == ["Bearer token-1", "Bearer token-2"]
