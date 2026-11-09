# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Attachment metadata in mailbox summaries, on both backends.

One fixture message — a body, a PDF and an inline signature logo — is fed
through Graph and Gmail, and both must name the same attachment under the
same keys. The listing tests also check the outgoing request: the metadata
has to arrive on the request that already lists the mail, and the file bytes
must never be asked for.
"""

import email
import json
import re

import httpx

from gaia.agents.tools._email import gmail, graph
from tests.fixtures.email.fake_gmail import mbox_message_to_gmail_payload

PDF = {"name": "contract.pdf", "mime_type": "application/pdf", "size": 84213}

RAW_WITH_ATTACHMENTS = (
    "Message-ID: <contract@example.com>\r\n"
    "Date: Wed, 02 Sep 2026 08:15:00 +0000\r\n"
    "From: Dana <dana@example.com>\r\n"
    "To: me@example.com\r\n"
    "Subject: Signed contract\r\n"
    "MIME-Version: 1.0\r\n"
    'Content-Type: multipart/mixed; boundary="M"\r\n'
    "\r\n"
    "--M\r\n"
    'Content-Type: multipart/related; boundary="R"\r\n'
    "\r\n"
    "--R\r\n"
    "Content-Type: text/html; charset=utf-8\r\n"
    "\r\n"
    '<p>Signed copy attached.</p><img src="cid:logo">\r\n'
    "--R\r\n"
    'Content-Type: image/png; name="logo.png"\r\n'
    'Content-Disposition: inline; filename="logo.png"\r\n'
    "Content-ID: <logo>\r\n"
    "Content-Transfer-Encoding: base64\r\n"
    "\r\n"
    "iVBORw0KGgo=\r\n"
    "--R--\r\n"
    "--M\r\n"
    'Content-Type: application/pdf; name="contract.pdf"\r\n'
    'Content-Disposition: attachment; filename="contract.pdf"\r\n'
    "Content-Transfer-Encoding: base64\r\n"
    "\r\n"
    "JVBERi0xLjQK\r\n"
    "--M--\r\n"
)

RAW_INLINE_ONLY = (
    "Message-ID: <logo@example.com>\r\n"
    "From: Dana <dana@example.com>\r\n"
    "Subject: Hi\r\n"
    "MIME-Version: 1.0\r\n"
    'Content-Type: multipart/related; boundary="R"\r\n'
    "\r\n"
    "--R\r\n"
    "Content-Type: text/html; charset=utf-8\r\n"
    "\r\n"
    '<p>Hi</p><img src="cid:logo">\r\n'
    "--R\r\n"
    'Content-Type: image/png; name="logo.png"\r\n'
    "Content-ID: <logo>\r\n"
    "Content-Transfer-Encoding: base64\r\n"
    "\r\n"
    "iVBORw0KGgo=\r\n"
    "--R--\r\n"
)

RAW_PLAIN = (
    "Message-ID: <plain@example.com>\r\n"
    "From: Dana <dana@example.com>\r\n"
    "Subject: No files\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "\r\n"
    "Nothing attached.\r\n"
)


def gmail_message(raw, *, pdf_size=PDF["size"]):
    """The Gmail API shape for `raw`, with the PDF given its real wire size."""
    msg = mbox_message_to_gmail_payload(email.message_from_string(raw))

    def walk(part):
        if part.get("filename") == PDF["name"]:
            part["body"]["size"] = pdf_size
        for child in part.get("parts") or []:
            walk(child)

    walk(msg["payload"])
    return msg


# The Graph view of the same messages. Graph reports the inline logo as an
# attachment with isInline=true and leaves hasAttachments false for it.
GRAPH_LOGO = {
    "@odata.type": "#microsoft.graph.fileAttachment",
    "name": "logo.png",
    "contentType": "image/png",
    "size": 1200,
    "isInline": True,
}
GRAPH_PDF = {
    "@odata.type": "#microsoft.graph.fileAttachment",
    "name": "contract.pdf",
    "contentType": "application/pdf",
    "size": PDF["size"],
    "isInline": False,
}


def graph_message(attachments, has_attachments):
    return {
        "id": "AAMk-1",
        "conversationId": "conv-1",
        "subject": "Signed contract",
        "hasAttachments": has_attachments,
        "attachments": attachments,
    }


def attachment_keys(summary):
    return {k: summary[k] for k in ("has_attachments", "attachments")}


# --------------------------------------------------------------------------
# summary shape, both backends against the same fixture
# --------------------------------------------------------------------------


def test_both_backends_name_the_same_pdf_under_identical_keys():
    from_gmail = gmail.message_summary(gmail_message(RAW_WITH_ATTACHMENTS))
    from_graph = graph.message_summary(graph_message([GRAPH_LOGO, GRAPH_PDF], True))

    expected = {"has_attachments": True, "attachments": [PDF]}
    assert attachment_keys(from_gmail) == expected
    assert attachment_keys(from_graph) == expected
    assert json.dumps(attachment_keys(from_gmail), sort_keys=True) == json.dumps(
        attachment_keys(from_graph), sort_keys=True
    )
    assert set(from_gmail) == set(from_graph)


def test_a_message_without_attachments_still_carries_both_keys():
    expected = {"has_attachments": False, "attachments": []}
    assert attachment_keys(gmail.message_summary(gmail_message(RAW_PLAIN))) == (
        expected
    )
    assert attachment_keys(graph.message_summary({"id": "1"})) == expected


def test_an_inline_signature_logo_is_not_an_attachment():
    expected = {"has_attachments": False, "attachments": []}
    gmail_out = gmail.message_summary(gmail_message(RAW_INLINE_ONLY))
    graph_out = graph.message_summary(graph_message([GRAPH_LOGO], False))
    assert attachment_keys(gmail_out) == expected
    assert attachment_keys(graph_out) == expected


def test_gmail_keeps_a_file_that_also_carries_a_content_id():
    """Some mailers stamp a Content-ID on every part; the disposition wins."""
    raw = RAW_WITH_ATTACHMENTS.replace(
        'Content-Disposition: attachment; filename="contract.pdf"\r\n',
        'Content-Disposition: attachment; filename="contract.pdf"\r\n'
        "Content-ID: <pdf>\r\n",
    )
    out = gmail.message_summary(gmail_message(raw))
    assert out["attachments"] == [PDF]


def test_gmail_decodes_an_encoded_word_filename():
    raw = RAW_WITH_ATTACHMENTS.replace(
        'filename="contract.pdf"', 'filename="=?utf-8?q?V=C3=A9rtrag.pdf?="'
    )
    out = gmail.message_summary(gmail_message(raw))
    assert [a["name"] for a in out["attachments"]] == ["Vértrag.pdf"]


# --------------------------------------------------------------------------
# listing requests: one round-trip, no file bytes
# --------------------------------------------------------------------------


def _parse_fields(spec):
    """Parse Google's partial-response `fields` grammar into a nested dict."""
    pos = 0

    def parse_list():
        nonlocal pos
        tree = {}
        while pos < len(spec) and spec[pos] != ")":
            match = re.compile(r"[A-Za-z0-9_/]+").match(spec, pos)
            assert match, f"bad fields mask at {pos}: {spec[pos:pos + 20]!r}"
            pos = match.end()
            node = tree
            names = match.group().split("/")
            for name in names[:-1]:
                node = node.setdefault(name, {})
            leaf = node.setdefault(names[-1], {})
            if pos < len(spec) and spec[pos] == "(":
                pos += 1
                leaf.update(parse_list())
                assert spec[pos] == ")"
                pos += 1
            if pos < len(spec) and spec[pos] == ",":
                pos += 1
        return tree

    tree = parse_list()
    assert pos == len(spec), f"unbalanced fields mask: {spec!r}"
    return tree


def _apply_fields(value, tree):
    """What Gmail returns for `value` under a parsed `fields` mask."""
    if not tree:
        return value
    if isinstance(value, list):
        return [_apply_fields(v, tree) for v in value]
    if not isinstance(value, dict):
        return value
    return {k: _apply_fields(value[k], sub) for k, sub in tree.items() if k in value}


def test_gmail_listing_returns_attachments_through_its_field_mask():
    """The fake honours `fields` the way Gmail does, so a mask that drops
    `parts` would drop the attachments here too."""
    seen = []
    full = gmail_message(RAW_WITH_ATTACHMENTS)

    def handler(request):
        seen.append(request.url)
        path = request.url.path
        if path.endswith("/users/me/labels"):
            return httpx.Response(200, json={"labels": []})
        if path.endswith("/users/me/messages"):
            return httpx.Response(
                200, json={"messages": [{"id": full["id"], "threadId": "t"}]}
            )
        assert request.url.params["format"] == "full"
        masked = _apply_fields(full, _parse_fields(request.url.params["fields"]))
        assert "data" not in json.dumps(masked), "a listing must not carry bodies"
        return httpx.Response(200, json=masked)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = gmail.GmailReadBackend(lambda: "t", http_client=client)
    [out] = backend.list_inbox(limit=5)

    assert out["attachments"] == [PDF]
    assert out["subject"] == "Signed contract"
    gets = [u for u in seen if "/users/me/messages/" in u.path]
    assert len(gets) == 1, "attachment names must not cost a second request"


def test_graph_listing_expands_attachment_metadata_without_content():
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(
            200, json={"value": [graph_message([GRAPH_LOGO, GRAPH_PDF], True)]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = graph.OutlookReadBackend(lambda: "t", http_client=client)

    assert backend.list_inbox()[0]["attachments"] == [PDF]
    assert backend.search("contract")[0]["attachments"] == [PDF]
    for url in seen:
        assert "hasAttachments" in url.params["$select"].split(",")
        expand = url.params["$expand"]
        assert expand.startswith("attachments($select=")
        # Without the inner $select Graph returns contentBytes — the file.
        assert "contentBytes" not in expand
        assert set(expand[len("attachments($select=") : -1].split(",")) == {
            "name",
            "contentType",
            "size",
            "isInline",
        }
    assert len(seen) == 2, "one request per listing, none per message"


def test_graph_reads_expand_attachments_too():
    seen = []

    def handler(request):
        seen.append(request.url)
        msg = graph_message([GRAPH_PDF], True)
        msg["body"] = {"contentType": "text", "content": "x"}
        if request.url.path.endswith("/me/messages"):
            return httpx.Response(200, json={"value": [msg]})
        return httpx.Response(200, json=msg)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    backend = graph.OutlookReadBackend(lambda: "t", http_client=client)

    assert backend.get_message("AAMk-1")["attachments"] == [PDF]
    assert backend.get_thread("conv-1")["messages"][0]["attachments"] == [PDF]
    assert all("$expand" in url.params for url in seen)
