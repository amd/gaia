# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Write send_email_parity.json: what the Python send-email tools do for each case.

Each case names the connected accounts, the grants, the surface and the
mailbox's HTTP answers, then a run of tool calls. A call runs the way the agent
runs it: the preflight, then the approval card, then the tool. What each stage
returns is recorded, and so is every request that changes the mailbox, with
Gmail's raw MIME decoded. test_send_email_tools.cpp replays the same answers
through a fake Transport and expects the same output from the C++ tools.

Run from the repo root:  python cpp/tests/fixtures/mail/make_send_email_parity.py
"""

import base64
import copy
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "src")]

import httpx  # noqa: E402

import gaia.connectors.api as connectors_api  # noqa: E402
import gaia.connectors.grants as connectors_grants  # noqa: E402
from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.agents.base.call_risk import EXACT_TEXT_ARG  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.agents.tools import email_tools  # noqa: E402
from gaia.skills.manager import SkillManager  # noqa: E402

SKILL_DIR = ROOT / "hub" / "skills" / "send-email"
TOOLS = [
    "list_drafts",
    "create_draft",
    "update_draft",
    "discard_draft",
    "send_draft",
    "send_now",
    "reply_to_message",
    "forward_message",
]

G_READ = "https://www.googleapis.com/auth/gmail.readonly"
G_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
M_READWRITE = "https://graph.microsoft.com/Mail.ReadWrite"
M_SEND = "https://graph.microsoft.com/Mail.Send"
G = "/gmail/v1/users/me"
M = "/v1.0/me"
BOUNDARY_RE = re.compile(r"={15}\d{19}==")

# ---- mailbox answers ----


def b64url(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def route(method, path, status=200, body=None, *, times=None, text=None, error=None):
    r = {"method": method, "path": path, "status": status}
    if body is not None:
        r["json"] = body
    if text is not None:
        r["text"] = text
    if error:
        r["raise"] = error
    if times:
        r["times"] = times
    return r


def get(path, body, **kw):
    return route("GET", path, 200, body, **kw)


def headers(**values):
    return [{"name": k.replace("_", "-"), "value": v} for k, v in values.items()]


def text_part(body, mime="text/plain"):
    return {"mimeType": mime, "body": {"data": b64url(body)}}


GMAIL_SOURCE = {
    "id": "msg1",
    "threadId": "thr1",
    "internalDate": "1788000000000",
    "payload": {
        "mimeType": "text/plain",
        "headers": headers(
            From='"Ruiz, Dana" <dana@example.com>',
            To="me@example.com, Sam <sam@example.com>",
            Cc="kit@example.com, Me <ME@example.com>",
            Subject="Q3 numbers",
            Message_ID="<abc@mail.example>",
            References="<root@mail.example>",
        ),
        "body": {"data": b64url("Can you confirm?")},
    },
}
GMAIL_OWN = {
    "id": "msg2",
    "threadId": "thr2",
    "internalDate": "1788000100000",
    "payload": {
        "mimeType": "text/plain",
        "headers": headers(
            From="Me <me@example.com>",
            To="Lee <lee@example.com>",
            Subject="Re: plans",
            Message_ID="<own@mail.example>",
        ),
        "body": {"data": b64url("See you then.")},
    },
}
GMAIL_HTML = {
    "id": "msg3",
    "threadId": "thr3",
    "internalDate": "1788000200000",
    "payload": {
        "mimeType": "multipart/mixed",
        "headers": headers(
            From="Acme Billing <billing@acme.com>",
            Reply_To="ap@acme.com",
            To="me@example.com",
            Subject="Invoice 4471",
        ),
        "parts": [
            text_part(
                "<html><head><title>x</title><style>p{}</style></head><body><p>Total:"
                " &pound;120</p><div>Due&nbsp;Friday</div><br><script>evil()</script>"
                "<ul><li>One</li><li>Two</li></ul></body></html>",
                "text/html",
            ),
            {
                "mimeType": "application/pdf",
                "filename": "invoice.pdf",
                "body": {"size": 9},
            },
        ],
    },
}
GMAIL_HTML_ODD = {
    "id": "msg6",
    "threadId": "thr6",
    "internalDate": "1788000300000",
    "payload": {
        "mimeType": "multipart/mixed",
        "headers": headers(
            From="Kit <kit@example.com>", To="me@example.com", Subject="Odd markup"
        ),
        "parts": [
            text_part(
                "<p>Kept</ >text</>here</1>.<!-->x<!--->y<!-- c --!>z"
                "<xmp><b>&amp;</xmp><textarea>&lt;t&gt;</textarea >"
                "<title>T<p>U</title/><script src=x/>gone</script><p b=c/>w"
                "<a b'c>d'>e</p",
                "text/html",
            )
        ],
    },
}
GMAIL_THREAD = {
    "id": "msg5",
    "threadId": "thr5",
    "internalDate": "1788000400000",
    "payload": {
        "mimeType": "text/plain",
        "headers": headers(
            From='"O\'Brien \\ \\"Ops\\"" <ops@example.com>',
            To="me@example.com",
            Subject="RE: Fwd: Ünïcödé planning thread for the quarterly offsite and the budget review meeting",
            Message_ID="<m5.very.long.identifier.0123456789@mail.example.com>",
            References=" ".join(
                f"<ref{i}.long.identifier.0123456789@mail.example.com>"
                for i in range(5)
            ),
        ),
        "body": {"data": b64url("Long thread.")},
    },
}
GMAIL_NOBODY = {
    "id": "msg4",
    "threadId": "thr4",
    "internalDate": "1788000300000",
    "payload": {
        "mimeType": "text/plain",
        "headers": headers(From="Me <me@example.com>", Subject="note to self"),
        "body": {"data": b64url("x")},
    },
}


def gmail_draft(
    did, to="dana@example.com", subject="Saved", body="Draft text\r\n", extra=None
):
    payload = {
        "mimeType": "text/plain",
        "headers": headers(To=to, Subject=subject),
        "body": {"data": b64url(body)},
    }
    if extra:
        payload.update(extra)
    return {
        "id": did,
        "message": {"id": "m" + did, "threadId": "thr9", "payload": payload},
    }


GMAIL_MULTIPART = gmail_draft(
    "d3",
    to='"Ruiz, Dana" <dana@example.com>, =?utf-8?q?Jos=C3=A9?= <jose@example.com>',
    subject="=?utf-8?q?R=C3=A9sum=C3=A9?=",
    extra={
        "mimeType": "multipart/alternative",
        "body": {"size": 0},
        "parts": [
            text_part("Plain half\r\n"),
            text_part("<p>HTML half</p>\r\n", "text/html"),
        ],
    },
)
GMAIL_ATTACHED = gmail_draft(
    "d4",
    extra={
        "mimeType": "multipart/mixed",
        "body": {"size": 0},
        "parts": [
            text_part("With a file"),
            {"mimeType": "image/png", "filename": "chart.png", "body": {"size": 4}},
        ],
    },
)
GMAIL_EMPTY_DRAFT = gmail_draft("d5", to="", subject="")


def gmail_base():
    return [
        get(f"{G}/profile", {"emailAddress": "me@example.com"}),
        get(f"{G}/messages/msg1", GMAIL_SOURCE),
        get(f"{G}/messages/msg2", GMAIL_OWN),
        get(f"{G}/messages/msg3", GMAIL_HTML),
        get(f"{G}/messages/msg4", GMAIL_NOBODY),
        get(f"{G}/messages/msg5", GMAIL_THREAD),
        get(f"{G}/messages/msg6", GMAIL_HTML_ODD),
        get(f"{G}/drafts", {}),
        get(f"{G}/drafts/d1", gmail_draft("d1")),
        get(f"{G}/drafts/d3", GMAIL_MULTIPART),
        get(f"{G}/drafts/d4", GMAIL_ATTACHED),
        get(f"{G}/drafts/d5", GMAIL_EMPTY_DRAFT),
        route("GET", f"{G}/drafts/gone", 404, {"error": {"code": 404}}),
        route("POST", f"{G}/drafts", 200, {"id": "d9", "message": {"id": "m9"}}),
        route("PUT", f"{G}/drafts/d1", 200, {"id": "d1"}),
        route("PUT", f"{G}/drafts/d3", 200, {"id": "d3"}),
        route("DELETE", f"{G}/drafts/d1", 204),
        route("DELETE", f"{G}/drafts/d3", 204),
        route("POST", f"{G}/drafts/send", 200, {"id": "sent1", "threadId": "thr9"}),
        route("POST", f"{G}/messages/send", 200, {"id": "sent2", "threadId": "thr1"}),
    ]


def ograph(
    mid,
    subject,
    text,
    *,
    sender=("Dana Ruiz", "dana@example.com"),
    draft=False,
    **extra,
):
    msg = {
        "id": mid,
        "conversationId": "conv-" + mid,
        "subject": subject,
        "from": {"emailAddress": {"name": sender[0], "address": sender[1]}},
        "replyTo": [],
        "toRecipients": [{"emailAddress": {"name": "Me", "address": "me@outlook.com"}}],
        "ccRecipients": [
            {"emailAddress": {"name": "Kit", "address": "kit@example.com"}}
        ],
        "bccRecipients": [],
        "body": {"contentType": "text", "content": text},
        "internetMessageId": f"<{mid}@outlook.example>",
        "receivedDateTime": "2026-09-01T10:00:00Z",
        "isDraft": draft,
        "attachments": [],
    }
    msg.update(extra)
    return msg


O_SOURCE = ograph("AAMk1", "Q3 numbers", "Can you confirm?")
O_HTML = ograph(
    "AAMk2",
    "Newsletter",
    "<p>Hello</p><p>World</p>",
    body={"contentType": "html", "content": "<p>Hello</p><p>World</p>"},
    attachments=[{"name": "flyer.pdf"}],
)
O_DRAFT_ID = "AAMkAD/x+y=="
O_DRAFT = ograph(
    O_DRAFT_ID,
    "Plan",
    "Draft body",
    draft=True,
    toRecipients=[{"emailAddress": {"name": "Lee", "address": "lee@example.com"}}],
    attachments=[{"name": "agenda.docx"}],
)
O_NOT_DRAFT = ograph("AAMk9", "Received", "hi")


def outlook_base():
    return [
        get(M, {"mail": "me@outlook.com"}),
        get(f"{M}/messages/AAMk1", O_SOURCE),
        get(f"{M}/messages/AAMk2", O_HTML),
        get(f"{M}/messages/{O_DRAFT_ID}", O_DRAFT),
        get(f"{M}/messages/AAMk9", O_NOT_DRAFT),
        route("POST", f"{M}/messages/AAMk1/createReply", 201, {"id": "RD1"}),
        route("PATCH", f"{M}/messages/RD1", 200, {"id": "RD1"}),
        route("POST", f"{M}/messages/RD1/send", 202),
        route("POST", f"{M}/messages", 201, {"id": "ND1"}),
        route("PATCH", f"{M}/messages/{O_DRAFT_ID}", 200, {"id": O_DRAFT_ID}),
        route("DELETE", f"{M}/messages/{O_DRAFT_ID}", 204),
        route("POST", f"{M}/messages/{O_DRAFT_ID}/send", 202),
        route("POST", f"{M}/sendMail", 202),
    ]


GOOGLE = {"google": {"scopes": [G_READ, G_COMPOSE], "account_email": "me@example.com"}}
GOOGLE_GRANT = {"google": [G_READ, G_COMPOSE]}
GOOGLE_READ_ONLY = {"google": {"scopes": [G_READ], "account_email": "me@example.com"}}
GOOGLE_NO_EMAIL = {"google": {"scopes": [G_READ, G_COMPOSE], "account_email": ""}}
MICROSOFT = {
    "microsoft": {"scopes": [M_READWRITE, M_SEND], "account_email": "me@outlook.com"}
}
MICROSOFT_GRANT = {"microsoft": [M_READWRITE, M_SEND]}
MICROSOFT_DRAFT_ONLY = {
    "microsoft": {"scopes": [M_READWRITE], "account_email": "me@outlook.com"}
}

CASES = []


def case(name, steps, *, surface="cli", connections=None, grants=None, routes=()):
    CASES.append(
        {
            "name": name,
            "surface": surface,
            "connections": connections or {},
            "grants": grants or {},
            "routes": list(routes),
            "steps": steps,
        }
    )


def call(tool, stage="all", **args):
    return {"tool": tool, "args": args, "stage": stage}


def gmail_case(name, steps, **kw):
    kw.setdefault("connections", GOOGLE)
    kw.setdefault("grants", GOOGLE_GRANT)
    case(name, steps, routes=[*kw.pop("routes", ()), *gmail_base()], **kw)


def outlook_case(name, steps, **kw):
    kw.setdefault("connections", MICROSOFT)
    kw.setdefault("grants", MICROSOFT_GRANT)
    case(name, steps, routes=[*kw.pop("routes", ()), *outlook_base()], **kw)


LONG_BODY = "Résumé attached.\n" + "line of text " * 200
gmail_case(
    "gmail_send_now",
    [
        call(
            "send_now",
            to='"Ruiz, Dana" <dana@example.com>, sam@example.com',
            cc="kit@example.com",
            bcc="hidden@example.com",
            subject="Über Q3",
            body=LONG_BODY,
        ),
        call("send_now", to="dana@example.com", subject="Hi", body="Hello there"),
        call("send_now", to="Dana <dana@example.com>", subject="", body=""),
        call(
            "send_now",
            to="a@bücher.example, José Ruiz <jose@example.com>, x@example.com",
            subject="A subject line that keeps going well past the seventy-eight characters a header line may hold",
            body="short\n" * 12 + "é" * 90,
        ),
        call(
            "send_now",
            to=", ".join(
                f"Person Number {i} <person.number.{i}@example.com>" for i in range(6)
            ),
            subject="Ünïcödé subject that is long enough to need folding into several encoded words, surely",
            body="x" * 100 + "\n" + "y" * 10,
        ),
        call(
            "send_now",
            to="dana@example.com",
            subject="Tabs\tand ‮flip",
            body="bell\x07 and\r\nCRLF",
        ),
        call(
            "send_now",
            to="dana@example.com",
            subject="trailing ",
            body="ends in space \nand tab\t",
        ),
        call(
            "send_now",
            to="dana@example.com",
            subject="=?utf-8?q?not_decoded?=",
            body="ascii",
        ),
        call(
            "send_now",
            to="dana@example.com",
            subject="=?UTF-8?Q?two?= =?utf-8?b?d29yZHM=?= end",
            body="ascii",
        ),
        call(
            "send_now",
            to="dana@example.com",
            subject="bad =?utf-8?q?=ZZhex?= =?utf-8?q?caf=C3=A9?=",
            body="ascii",
        ),
        call(
            "send_now",
            to='"Smith," <u0@x.com>, '
            + "x" * 40
            + ' <u1@x.com>, "\\" '
            + "ü" * 30
            + '" <u2@x.com>',
            subject="a folded word that started on a line the folder had already left",
            body="b",
        ),
        call(
            "send_now",
            to="dana@example.com",
            bcc="x" * 80 + " <b1@z.net>",
            subject="a line already past the limit has no room left",
            body="b",
        ),
        call(
            "send_now",
            to="dana@example.com",
            subject='xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx 中文aa\') éa中文"é-    é:中文;,Bob:\\,\\("',
            body="two encoded words the folder wrote side by side",
        ),
        call(
            "send_now",
            to="dana@example.com",
            subject="=?utf-8?q?a=ZZ?= =?utf-8?b?SGk?= =?iso-8859-1?q?caf=E9?= =?UTF8?Q?x?= =?koi8-r?q?x?=",
            body="encoded words Python reads in a subject",
        ),
        call(
            "send_now",
            to="dana@example.com",
            subject="=?utf-8?q?no_space?=next a=?utf-8?q?x?=b \u00a0 =?utf-8?b?w6k=?=",
            body="encoded words touching other text",
        ),
    ],
)

gmail_case(
    "gmail_send_now_refusals",
    [
        call("send_now", subject="Hi", body="no one"),
        call("send_now", to="not-an-address", body="x"),
        call(
            "send_now",
            to="dana@example.com",
            subject="Hi\nBcc: evil@example.com",
            body="x",
        ),
        call("send_now", to="josé@example.com", body="x"),
        call("send_now", to="a@exa mple.com", body="x"),
        call("send_now", to='"‮evil" <a@example.com>', body="x"),
        call("send_now", to='"boss@corp.com" <a@example.com>', body="x"),
        call("send_now", to="dana@example.com", cc="a@b.com ", body="x"),
        call("send_now", to=",,", body="x"),
        call(
            "send_now",
            to="Dana@Example.com <dana@example.com>",
            body="names equal to the address drop",
        ),
        call("send_now", "body", to="dana@example.com", body="never shown"),
    ],
)

gmail_case(
    "gmail_send_failures",
    [
        call("send_now", to="dana@example.com", subject="flaky", body="maybe"),
        call(
            "send_now",
            "preflight",
            to="dana@example.com",
            subject="flaky",
            body="maybe",
        ),
        call("send_now", to="dana@example.com", subject="refused", body="403"),
        call("send_now", to="dana@example.com", subject="offline", body="never left"),
        call("send_now", to="dana@example.com", subject="slow", body="timed out"),
        call("send_now", to="dana@example.com", subject="garbled", body="unreadable"),
        call("send_now", to="dana@example.com", subject="expired", body="401"),
        call("send_now", to="dana@example.com", subject="bad request", body="400"),
    ],
    routes=[
        route("POST", f"{G}/messages/send", 503, {}, times=1),
        route(
            "POST",
            f"{G}/messages/send",
            403,
            {"error": {"errors": [{"reason": "insufficientPermissions"}]}},
            times=1,
        ),
        route("POST", f"{G}/messages/send", error="ConnectError", times=1),
        route("POST", f"{G}/messages/send", error="ReadTimeout", times=1),
        route("POST", f"{G}/messages/send", 200, text="not json", times=1),
        route("POST", f"{G}/messages/send", 401, {}, times=1),
        route(
            "POST",
            f"{G}/messages/send",
            400,
            {"error": {"status": "INVALID_ARGUMENT"}},
            times=1,
        ),
    ],
)

gmail_case(
    "gmail_reply_and_forward",
    [
        call("reply_to_message", message_id="msg1", body="Yes, confirmed."),
        call("reply_to_message", message_id="msg1", body="All of you.", reply_all=True),
        call(
            "reply_to_message",
            message_id="msg2",
            body="Following up.",
            reply_all="true",
        ),
        call("reply_to_message", message_id="msg4", body="nobody"),
        call("reply_to_message", message_id="msg5", body="Threaded."),
        call(
            "forward_message",
            message_id="msg5",
            to='"Smith, J. (Ops)" <j@example.com>',
            note="See the thread",
        ),
        call("reply_to_message", message_id="bad id!", body="x"),
        call("forward_message", message_id="msg3", to="boss@example.com", note="FYI"),
        call("forward_message", message_id="msg6", to="boss@example.com"),
        call(
            "forward_message",
            message_id="msg1",
            to="lee@example.com",
            cc="kit@example.com",
        ),
        call("forward_message", message_id="msg1", to=""),
    ],
)

gmail_case(
    "gmail_drafts",
    [
        call("list_drafts"),
        call("list_drafts", limit=500),
        call("create_draft", to="dana@example.com", subject="New", body="A new draft"),
        call("create_draft", reply_to_message_id="msg1", body="Reply draft"),
        call(
            "create_draft",
            reply_to_message_id="msg1",
            body="Reply draft, new cc",
            cc="lee@example.com",
        ),
        call(
            "create_draft",
            forward_message_id="msg3",
            to="boss@example.com",
            body="See below",
        ),
        call("create_draft", reply_to_message_id="msg1", forward_message_id="msg3"),
        call("create_draft", to="bad", body="x"),
        call("send_draft", draft_id="d1"),
        call("send_draft", draft_id="d5"),
        call("send_draft", draft_id="gone"),
        call("send_draft", draft_id="no/slash"),
        call("discard_draft", draft_id="d1"),
        call("update_draft", draft_id="d1", subject="Renamed"),
        call("update_draft", draft_id="d1", body="New body", cc=""),
        call(
            "update_draft",
            draft_id="d1",
            to="lee@example.com, kit@example.com",
            bcc="x@example.com",
        ),
        call("update_draft", draft_id="d1"),
        call("update_draft", draft_id="d4", subject="x"),
        call("update_draft", draft_id="d3", subject="Kept HTML"),
        call("update_draft", draft_id="d3", body="Plain only now"),
        call("discard_draft", draft_id="d3"),
    ],
    routes=[
        get(f"{G}/drafts", {"drafts": [{"id": "d1"}, {"id": "d3"}, {"id": ""}]}),
        get(
            f"{G}/drafts/d1",
            {
                "id": "d1",
                "message": {
                    "threadId": "thr9",
                    "snippet": "Draft &amp; text ",
                    "payload": {
                        "headers": headers(To="dana@example.com", Subject="Saved")
                    },
                },
            },
            times=2,
        ),
        get(
            f"{G}/drafts/d3",
            {"id": "d3", "message": {"payload": {"headers": []}}},
            times=2,
        ),
    ],
)

gmail_case(
    "gmail_draft_changed_after_approval",
    [
        call("send_draft", "preflight", draft_id="d1"),
        call("send_draft", "body", draft_id="d1"),
        call("update_draft", "preflight", draft_id="d1", subject="Mine"),
        call("update_draft", "body", draft_id="d1", subject="Mine"),
        call("update_draft", "body", draft_id="d1", subject="Mine"),
    ],
    routes=[
        get(f"{G}/drafts/d1", gmail_draft("d1"), times=1),
        get(f"{G}/drafts/d1", gmail_draft("d1", body="Edited elsewhere"), times=1),
        get(f"{G}/drafts/d1", gmail_draft("d1"), times=1),
        get(f"{G}/drafts/d1", gmail_draft("d1", subject="Edited elsewhere"), times=1),
    ],
)

gmail_case(
    "gmail_account_changed_after_approval",
    [
        call("send_now", "preflight", to="dana@example.com", subject="Hi", body="x"),
        {"set_connections": GOOGLE_NO_EMAIL, "set_grants": GOOGLE_GRANT},
        call("send_now", "body", to="dana@example.com", subject="Hi", body="x"),
        call("send_now", to="dana@example.com", subject="Hi", body="x"),
    ],
)

for surface in ("cli", "agent_ui"):
    gmail_case(
        f"gmail_read_only_connection_{surface}",
        [
            call("send_now", to="dana@example.com", subject="Hi", body="x"),
            call("create_draft", to="dana@example.com", subject="Hi", body="x"),
            call("discard_draft", draft_id="d1"),
            call("list_drafts"),
        ],
        surface=surface,
        connections=GOOGLE_READ_ONLY,
        grants={"google": [G_READ]},
    )
    gmail_case(
        f"gmail_no_write_grant_{surface}",
        [
            call("send_now", to="dana@example.com", subject="Hi", body="x"),
            call("update_draft", draft_id="d1", subject="x"),
        ],
        surface=surface,
        grants={"google": [G_READ]},
    )
    case(
        f"no_mailbox_{surface}",
        [
            call("send_now", to="dana@example.com", subject="Hi", body="x"),
            call("list_drafts"),
            call("create_draft", to="dana@example.com"),
        ],
        surface=surface,
    )

outlook_case(
    "outlook_writes",
    [
        call(
            "send_now",
            to="Dana Ruiz <dana@example.com>, sam@example.com",
            bcc="hidden@example.com",
            subject="Über Q3",
            body="Hello",
        ),
        call("reply_to_message", message_id="AAMk1", body="Confirmed."),
        call("reply_to_message", message_id="AAMk1", body="Everyone.", reply_all=True),
        call(
            "create_draft",
            reply_to_message_id="AAMk1",
            body="Draft reply",
            to="lee@example.com",
        ),
        call("create_draft", to="lee@example.com", subject="New", body="Plain"),
        call("forward_message", message_id="AAMk2", to="boss@example.com"),
        call("list_drafts", limit=3),
        call("send_draft", draft_id=O_DRAFT_ID),
        call("update_draft", draft_id=O_DRAFT_ID, body="Rewritten"),
        call("discard_draft", draft_id=O_DRAFT_ID),
        call("discard_draft", draft_id="AAMk9"),
        call("send_draft", draft_id="has space"),
    ],
    routes=[
        get(
            f"{M}/mailFolders/drafts/messages",
            {
                "value": [
                    {
                        "id": O_DRAFT_ID,
                        "conversationId": "c1",
                        "subject": "",
                        "toRecipients": [
                            {
                                "emailAddress": {
                                    "name": "Lee",
                                    "address": "lee@example.com",
                                }
                            }
                        ],
                        "bodyPreview": " Draft body ",
                    }
                ]
            },
        ),
    ],
)

outlook_case(
    "outlook_failures",
    [
        call("reply_to_message", message_id="AAMk1", body="fill fails"),
        call("send_now", to="dana@example.com", subject="Hi", body="forbidden"),
        call("send_now", to="dana@example.com", subject="Hi", body="down"),
        call("send_now", to="dana@example.com", subject="Hi", body="gone quiet"),
    ],
    routes=[
        route(
            "PATCH",
            f"{M}/messages/RD1",
            400,
            {"error": {"code": "ErrorInvalidRecipients"}},
            times=1,
        ),
        route(
            "POST",
            f"{M}/sendMail",
            403,
            {"error": {"code": "ErrorAccessDenied"}},
            times=1,
        ),
        route("POST", f"{M}/sendMail", 502, {}, times=1),
        route("POST", f"{M}/sendMail", error="WriteError", times=1),
    ],
)

outlook_case(
    "outlook_draft_only_connection",
    [
        call("create_draft", to="lee@example.com", subject="Ok", body="drafting works"),
        call(
            "send_now",
            to="lee@example.com",
            subject="No",
            body="sending needs Mail.Send",
        ),
    ],
    connections=MICROSOFT_DRAFT_ONLY,
    grants={"microsoft": [M_READWRITE]},
)

# ---- replay ----


def decoded(body):
    """A write's JSON body with Gmail's raw MIME as text, its boundary fixed."""
    body = copy.deepcopy(body)
    holder = body.get("message", body) if isinstance(body, dict) else None
    if isinstance(holder, dict) and "raw" in holder:
        raw = holder.pop("raw")
        mime = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode(
            "utf-8", "surrogateescape"
        )
        holder["mime"] = BOUNDARY_RE.sub("BOUNDARY", mime)
        holder["raw_is_base64url"] = not re.search(r"[=+/]", raw)
    return body


def router(routes, writes):
    used = [0] * len(routes)

    def handle(request):
        if request.method != "GET":
            body = json.loads(request.content) if request.content else None
            writes.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "json": decoded(body),
                }
            )
        for i, r in enumerate(routes):
            if r["method"] != request.method or r["path"] != request.url.path:
                continue
            if r.get("times") and used[i] >= r["times"]:
                continue
            used[i] += 1
            if r.get("raise"):
                raise getattr(httpx, r["raise"])("simulated", request=request)
            if "text" in r:
                return httpx.Response(r["status"], text=r["text"])
            if "json" in r:
                return httpx.Response(r["status"], json=r["json"])
            return httpx.Response(r["status"])
        raise AssertionError(f"no route for {request.method} {request.url}")

    return handle


REAL_CLIENT = httpx.Client


class MailAgent(Agent, email_tools.EmailToolsMixin):
    def _get_system_prompt(self):
        return "mail"

    def _register_tools(self):
        self.register_email_tools()


def make_agent(surface):
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = MailAgent(silent_mode=True, skip_lemonade=True)
    agent.config = SimpleNamespace(surface=surface or None)
    agent.load_skill(
        "send-email",
        manager=SkillManager(
            agent_skill_dirs=[SKILL_DIR.parent],
            user_skills_root=SKILL_DIR.parent / ".no-user-skills",
            include_claude_roots=False,
        ),
    )
    return agent


def run(spec):
    writes = []
    handler = router(spec["routes"], writes)
    httpx.Client = lambda **_: REAL_CLIENT(transport=httpx.MockTransport(handler))
    state = {"connections": spec["connections"], "grants": spec["grants"]}
    connectors_api.get_connection = lambda p: copy.deepcopy(state["connections"].get(p))
    connectors_grants.list_agent_grants = lambda p: (
        {email_tools.EMAIL_AGENT_ID: list(state["grants"][p])}
        if p in state["grants"]
        else {}
    )
    connectors_api.get_access_token_sync = lambda *, provider, scopes, agent_id: "tok"
    agent = make_agent(spec["surface"])
    outputs = []
    for step in spec["steps"]:
        if "set_connections" in step:
            state["connections"] = step["set_connections"]
            state["grants"] = step["set_grants"]
            outputs.append(None)
            continue
        name = f"send-email/{step['tool']}"
        entry = agent._tools_registry[name]
        preflight = entry.get("preflight")
        out = {}
        del writes[:]
        with agent._skill_tool_scope(name):
            if preflight is not None and step["stage"] in ("all", "preflight"):
                refusal = preflight(dict(step["args"]))
                out["preflight"] = refusal
                if refusal is None:
                    out["prompt"] = preflight.prompt_args_of(dict(step["args"]))
            if step["stage"] in ("all", "body") and out.get("preflight") is None:
                out["result"] = json.loads(entry["function"](**step["args"]))
        out["writes"] = list(writes)
        outputs.append(out)
    return outputs


def schemas():
    agent = make_agent("cli")
    out = {}
    for tool in TOOLS:
        entry = agent._tools_registry[f"send-email/{tool}"]
        out[tool] = {
            "description": entry["description"],
            "atomic": entry["atomic"],
            "confirm": bool(agent._tool_requires_confirmation(f"send-email/{tool}")),
            "parameters": [
                {
                    "name": p,
                    "type": info["type"],
                    "required": info["required"],
                    "description": info.get("description", ""),
                }
                for p, info in entry["parameters"].items()
            ],
        }
    return out


def main():
    saved = dict(_TOOL_REGISTRY)
    cases = [dict(spec, expected=run(spec)) for spec in CASES]
    httpx.Client = REAL_CLIENT
    schema = schemas()
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)
    target = Path(__file__).with_name("send_email_parity.json")
    target.write_text(
        json.dumps(
            {"exact_text_arg": EXACT_TEXT_ARG, "schemas": schema, "cases": cases},
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(f"wrote {len(cases)} cases to {target}")


if __name__ == "__main__":
    main()
