# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Write python_parity.json: what the Python mailbox backends answer for each case.

test_mail_backends.cpp feeds the same inputs to the C++ backends and expects
the same output, so a drift in either runtime shows up as a failing case.

Run from the repo root:  python cpp/tests/fixtures/mail/make_python_parity.py
"""

import base64
import email
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from gaia.agents.tools._email import gmail, graph  # noqa: E402
from tests.fixtures.email.fake_gmail import mbox_message_to_gmail_payload  # noqa: E402

ADDRESS_HEADERS = [
    '"Ruiz, Dana" <dana@example.com>',
    "Me <me@example.com>, Other <other@example.com>",
    "cc@example.com",
    "=?UTF-8?B?RGFuYSBSdcOteg==?= <dana@example.com>",
    "a@x.com, ",
    "Undisclosed recipients:;",
    "a@x.com (Doe, J)",
    "Team: a@x.com, b@x.com;",
    "dana@example.com <dana@example.com>",
    '"Dana" <DANA@example.com>',
    "<bare@example.com>",
    "Name Only",
    "x@[127.0.0.1]",
    "a@b@c.com",
    '"Quote \\" inside" <q@x.com>',
    "(comment) a@x.com",
    "Dana (work) <dana@x.com>",
    "Dana (work <dana@x.com>",
    "=?utf-8?q?Jos=C3=A9?= <j@x.com>, =?iso-8859-1?q?Andr=E9?= <a@x.com>",
    "",
    "José <j@x.com>",
    "  spaced@x.com  ",
    "first.last@sub.example.co.uk",
    "a@x.com,,b@x.com",
]

ENCODED_HEADERS = [
    "=?utf-8?q?Hello?= =?utf-8?q?_World?=",
    "Re: =?utf-8?b?w6k=?=x",
    "=?iso-8859-1?q?caf=E9?= ok",
    "Hello =?us-ascii?q?World?=",
    "=?utf-8?q?V=C3=A9rtrag.pdf?=",
    "=?x-unknown?q?abc?=",
    "=?utf-8?b?/w==?=",
    "plain text",
    "Café =?utf-8?q?x?=",
    "=?windows-1252?q?=93quoted=94?=",
    "=?UTF-8?Q?a?= b =?UTF-8?Q?c?=",
    "(=?utf-8?q?x?=)",
    "=?utf-8?b?YWJj?=",
    "=?utf-8?q?a_b?=c",
    "=?utf-8?q?a?=\r\n =?utf-8?q?b?=",
    "=?latin-1?q?=E9?= and =?utf-8?q?=C3=A9?=",
]

SNIPPETS = [
    "  Q3 &amp; Q4 &quot;figures&quot;  ",
    "&ampfoo; &#39; &#x26; &nbsp;x &bogus; &copy &#150;",
    "&#0; &#128512; &#xD800; &#1114112; &#11; &#x80;",
    "&lt;b&gt; &eacute;t&eacute; &hellip; &rsquo;",
    "&#; &#x; & alone &;",
    " lead nbsp ",
    "no entities",
]

INTERNAL_DATES = ["1788336900000", "not-a-number", "-1", "0", " 42 ", "1_000", 1788336900000, None, "", "+5"]

RAW_MESSAGES = {
    "alternative": (
        "Message-ID: <abc@example.com>\r\n"
        "Date: Wed, 02 Sep 2026 08:15:00 +0000\r\n"
        'From: "Ruiz, Dana" <dana@example.com>\r\n'
        "To: Me <me@example.com>, Other <other@example.com>\r\n"
        "Cc: cc@example.com\r\n"
        "Subject: Q3 numbers\r\n"
        "Auto-Submitted: auto-replied\r\n"
        "X-Gmail-Labels: Inbox,Unread,Receipts\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/alternative; boundary="B"\r\n'
        "\r\n"
        "--B\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        + base64.b64encode("Can you confirm the Q3 figures? Café.\r\n".encode()).decode()
        + "\r\n"
        "--B\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<p>Can you confirm?</p>\r\n"
        "--B--\r\n"
    ),
    "html_only": (
        "Message-ID: <html@example.com>\r\n"
        "Date: Wed, 02 Sep 2026 08:15:00 +0000\r\n"
        "Subject: h\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "\r\n"
        "<p>Hello <b>there</b></p>\r\n"
    ),
    "attachments": (
        "Message-ID: <contract@example.com>\r\n"
        "Date: Wed, 02 Sep 2026 08:15:00 +0000\r\n"
        "From: Dana <dana@example.com>\r\n"
        "To: me@example.com\r\n"
        "Subject: =?utf-8?q?Signed_contract_=E2=9C=93?=\r\n"
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
        'Content-Disposition: attachment; filename="=?utf-8?q?V=C3=A9rtrag.pdf?="\r\n'
        "Content-ID: <pdf>\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "JVBERi0xLjQK\r\n"
        "--M\r\n"
        'Content-Type: application/pdf; name="apple.pdf"\r\n'
        'Content-Disposition: inline; filename="apple.pdf"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "JVBERi0xLjQK\r\n"
        "--M\r\n"
        'Content-Type: image/jpeg; name="banner.jpg"\r\n'
        'Content-Disposition: inline; filename="banner.jpg"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "JVBERi0xLjQK\r\n"
        "--M\r\n"
        'Content-Type: image/jpeg; name="IMG_0412.jpg"\r\n'
        'Content-Disposition: attachment; filename="IMG_0412.jpg"\r\n'
        "Content-ID: <img>\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "JVBERi0xLjQK\r\n"
        "--M\r\n"
        'Content-Type: image/png; name="chart.png"\r\n'
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "JVBERi0xLjQK\r\n"
        "--M\r\n"
        "Content-Type: application/octet-stream\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "JVBERi0xLjQK\r\n"
        "--M--\r\n"
    ),
    "binary_only": (
        "Message-ID: <bin@example.com>\r\n"
        "Subject: \r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: application/octet-stream\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "JVBERi0xLjQK\r\n"
    ),
}

GRAPH_MESSAGES = [
    {
        "id": "AAMk-1",
        "conversationId": "conv-1",
        "subject": "Q3 numbers",
        "from": {"emailAddress": {"name": "Dana Ruiz", "address": "dana@example.com"}},
        "toRecipients": [
            {"emailAddress": {"name": "Me", "address": "me@example.com"}},
            {"emailAddress": {"name": "", "address": "x@example.com"}},
            {"emailAddress": {"name": "OTHER@example.com", "address": "other@example.com"}},
        ],
        "ccRecipients": [],
        "receivedDateTime": "2026-09-02T08:15:00Z",
        "isRead": False,
        "flag": {"flagStatus": "flagged"},
        "categories": ["Work"],
        "bodyPreview": "  Can you confirm the Q3 figures?  ",
        "body": {"contentType": "HTML", "content": "<p>Can you confirm?</p>"},
        "hasAttachments": True,
        "attachments": [
            {"name": "logo.png", "contentType": "image/PNG", "size": 1200, "isInline": True},
            {"name": "contract.pdf", "contentType": "Application/PDF", "size": 84213, "isInline": False},
        ],
        "internetMessageHeaders": [
            {"name": "Auto-Submitted", "value": "auto-generated"},
            {"name": "X-Autoreply", "value": "yes"},
            {"name": "x-autoreply", "value": "last-wins"},
        ],
    },
    {"id": "solo", "subject": "x"},
    {"id": "1", "from": {"emailAddress": {"address": "a@b.com"}}, "isRead": True, "flag": {"flagStatus": "complete"}},
    {"id": "2", "isRead": None, "flag": {"flagStatus": "notFlagged"}, "hasAttachments": False, "body": {}},
]


def main():
    cases = {
        "address_list": [[h, gmail._address_list(h)] for h in ADDRESS_HEADERS],
        "decode": [[h, gmail._decode(h)] for h in ENCODED_HEADERS],
        "preview": [[s, gmail.message_summary({"snippet": s})["preview"]] for s in SNIPPETS],
        "received": [[d, gmail._received(d)] for d in INTERNAL_DATES],
        "gmail_summary": [],
        "graph_summary": [],
    }
    labels = {"Label_17": "Receipts"}
    for name, raw in RAW_MESSAGES.items():
        msg = mbox_message_to_gmail_payload(email.message_from_string(raw))
        msg["labelIds"] = ["INBOX", "UNREAD", "STARRED", "CATEGORY_UPDATES", "Label_17", "Label_99"]
        for include_body in (False, True):
            out = gmail.message_summary(msg, label_names=labels, include_body=include_body)
            cases["gmail_summary"].append([name, msg, labels, include_body, out])
    for msg in GRAPH_MESSAGES:
        for include_body in (False, True):
            out = graph.message_summary(msg, include_body=include_body)
            cases["graph_summary"].append([msg.get("id"), msg, include_body, out])
    target = Path(__file__).with_name("python_parity.json")
    target.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
