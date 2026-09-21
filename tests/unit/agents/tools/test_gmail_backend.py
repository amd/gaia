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


# --------------------------------------------------------------------------
# Inc 2 — normalization, asserted by value type and format
# --------------------------------------------------------------------------

RAW_MESSAGE = (
    "Message-ID: <abc@example.com>\r\n"
    "Date: Wed, 02 Sep 2026 08:15:00 +0000\r\n"
    'From: "Ruiz, Dana" <dana@example.com>\r\n'
    "To: Me <me@example.com>, Other <other@example.com>\r\n"
    "Cc: cc@example.com\r\n"
    "Subject: Q3 numbers\r\n"
    "X-Gmail-Labels: Inbox,Promotions\r\n"
    "MIME-Version: 1.0\r\n"
    'Content-Type: multipart/alternative; boundary="B"\r\n'
    "\r\n"
    "--B\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "\r\n"
    "Can you confirm the Q3 figures?\r\n"
    "--B\r\n"
    "Content-Type: text/html; charset=utf-8\r\n"
    "\r\n"
    "<p>Can you confirm?</p>\r\n"
    "--B--\r\n"
)


def gmail_message(raw=RAW_MESSAGE, **overrides):
    """A Gmail-API-shape payload built by the real mbox translator."""
    import email

    from tests.fixtures.email.fake_gmail import mbox_message_to_gmail_payload

    payload = mbox_message_to_gmail_payload(email.message_from_string(raw))
    payload.update(overrides)
    return payload


def set_header(msg, name, value):
    """Replace one header value with the raw wire form Gmail returns."""
    for header in msg["payload"]["headers"]:
        if header["name"].lower() == name.lower():
            header["value"] = value
            return msg
    msg["payload"]["headers"].append({"name": name, "value": value})
    return msg


def test_gmail_summary_matches_graph_value_contract():
    from gaia.agents.tools._email.gmail import message_summary

    out = message_summary(gmail_message(), label_names={"Label_17": "Receipts"})

    # `internalDate` is epoch MILLIS as a string. Rendering it without
    # tz=utc produces a local-naive stamp Graph never emits, which is how
    # "what came in today" goes wrong.
    assert out["received"] == "2026-09-02T08:15:00Z"

    assert out["unread"] is True and isinstance(out["unread"], bool)
    assert out["flagged"] is False and isinstance(out["flagged"], bool)

    # One raw RFC 5322 header, not Graph's structured list. A naive comma
    # split breaks a quoted display name containing a comma.
    assert out["from"] == "Ruiz, Dana <dana@example.com>"
    assert out["to"] == "Me <me@example.com>, Other <other@example.com>"
    assert out["cc"] == "cc@example.com"

    assert out["subject"] == "Q3 numbers"
    assert isinstance(out["id"], str) and out["id"]
    assert isinstance(out["thread_id"], str) and out["thread_id"]

    # CATEGORY_* is Gmail's own tab bucketing, not a tag the user applied.
    assert out["categories"] == []

    # A listing must not carry a body.
    assert "body" not in out


def test_summary_key_set_is_identical_to_graphs():
    from gaia.agents.tools._email.gmail import message_summary as gmail_summary
    from gaia.agents.tools._email.graph import message_summary as graph_summary

    graph_msg = {"id": "1", "body": {"contentType": "html", "content": "x"}}
    for include_body in (False, True):
        assert set(gmail_summary(gmail_message(), include_body=include_body)) == set(
            graph_summary(graph_msg, include_body=include_body)
        )


def test_encoded_word_headers_are_decoded():
    """Gmail returns the header as it appeared on the wire."""
    from gaia.agents.tools._email.gmail import message_summary

    msg = set_header(
        gmail_message(), "From", "=?UTF-8?B?RGFuYSBSdcOteg==?= <dana@example.com>"
    )
    assert message_summary(msg)["from"] == "Dana Ruíz <dana@example.com>"


def test_snippet_is_html_unescaped():
    """Gmail's snippet is entity-encoded where Graph's bodyPreview is plain."""
    from gaia.agents.tools._email.gmail import message_summary

    msg = gmail_message(snippet="  Q3 &amp; Q4 &quot;figures&quot;  ")
    assert message_summary(msg)["preview"] == 'Q3 & Q4 "figures"'


def test_user_labels_are_named_and_system_labels_excluded():
    from gaia.agents.tools._email.gmail import message_summary

    msg = gmail_message(
        labelIds=["INBOX", "UNREAD", "IMPORTANT", "CATEGORY_PROMOTIONS", "Label_17"]
    )
    out = message_summary(msg, label_names={"Label_17": "Receipts"})
    assert out["categories"] == ["Receipts"]


def test_unknown_label_id_is_kept_verbatim_not_dropped():
    from gaia.agents.tools._email.gmail import message_summary

    out = message_summary(gmail_message(labelIds=["INBOX", "Label_99"]))
    assert out["categories"] == ["Label_99"]


def test_read_and_starred_state_come_from_labels():
    from gaia.agents.tools._email.gmail import message_summary

    out = message_summary(gmail_message(labelIds=["INBOX", "STARRED"]))
    assert out["unread"] is False
    assert out["flagged"] is True


def test_body_is_raw_plain_text_with_its_content_type():
    from gaia.agents.tools._email.gmail import message_summary

    out = message_summary(gmail_message(), include_body=True)
    # text/plain wins over text/html, and the text is NOT stripped or
    # rewritten -- same contract as the Graph backend.
    assert out["body"].strip() == "Can you confirm the Q3 figures?"
    assert out["body_content_type"] == "text"


def test_html_only_body_is_returned_raw_as_html():
    from gaia.agents.tools._email.gmail import message_summary

    raw = (
        "Message-ID: <html@example.com>\r\n"
        "Date: Wed, 02 Sep 2026 08:15:00 +0000\r\n"
        "Subject: h\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<p>Hello <b>there</b></p>\r\n"
    )
    out = message_summary(gmail_message(raw), include_body=True)
    assert out["body_content_type"] == "html"
    assert "<b>there</b>" in out["body"]


def test_binary_parts_are_never_decoded_into_the_body():
    """An inline base64 attachment must not reach the model's context."""
    from gaia.agents.tools._email.gmail import message_summary

    raw = (
        "Message-ID: <att@example.com>\r\n"
        "Date: Wed, 02 Sep 2026 08:15:00 +0000\r\n"
        "Subject: with attachment\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="M"\r\n'
        "\r\n"
        "--M\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "See attached.\r\n"
        "--M\r\n"
        "Content-Type: application/pdf\r\n"
        'Content-Disposition: attachment; filename="report.pdf"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "JVBERi0xLjQKJSVFT0Y=\r\n"
        "--M--\r\n"
    )
    out = message_summary(gmail_message(raw), include_body=True)
    assert out["body"].strip() == "See attached."
    assert "JVBERi" not in out["body"]
    assert "%PDF" not in out["body"]


def test_body_is_empty_string_when_no_text_part_exists():
    from gaia.agents.tools._email.gmail import message_summary

    msg = gmail_message()
    msg["payload"] = {"mimeType": "application/octet-stream", "body": {"size": 0}}
    out = message_summary(msg, include_body=True)
    assert out["body"] == ""
    assert out["body_content_type"] == "text"


def test_missing_subject_gets_the_same_placeholder_as_graph():
    from gaia.agents.tools._email.gmail import message_summary
    from gaia.agents.tools._email.graph import message_summary as graph_summary

    msg = gmail_message()
    msg["payload"]["headers"] = [
        h for h in msg["payload"]["headers"] if h["name"].lower() != "subject"
    ]
    assert message_summary(msg)["subject"] == graph_summary({"id": "1"})["subject"]


def test_unparseable_internal_date_does_not_crash_a_listing():
    from gaia.agents.tools._email.gmail import message_summary

    assert message_summary(gmail_message(internalDate="not-a-number"))["received"] == ""
