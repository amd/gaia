# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Write email_tools_parity.json: what the Python email tools return for each case.

Each case names the connected accounts, the grants, the surface, the turn's
body budget and the mailbox's HTTP answers, then a run of tool calls.
test_email_tools.cpp builds the same accounts in a temp vault, serves the same
answers through a fake Transport, makes the same calls and expects the same
results. The tool schemas (descriptions and parameters) are recorded too.

Run from the repo root:  python cpp/tests/fixtures/mail/make_email_tools_parity.py
"""

import base64
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "src")]

import httpx  # noqa: E402

import gaia.connectors.api as connectors_api  # noqa: E402
import gaia.connectors.grants as connectors_grants  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.agents.tools import email_tools  # noqa: E402
from gaia.connectors import errors as connectors_errors  # noqa: E402

TOOLS = [
    "check_mailbox_access",
    "list_inbox",
    "search_email",
    "read_email",
    "list_mail_folders",
    "get_thread",
    "list_connected_mailboxes",
    "list_waiting_on_you",
]

G_READ = "https://www.googleapis.com/auth/gmail.readonly"
G_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
M_READ = "https://graph.microsoft.com/Mail.Read"
M_SEND = "https://graph.microsoft.com/Mail.Send"
G = "/gmail/v1/users/me"
M = "/v1.0/me"

# ---- mailbox answers ----


def b64url(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmsg(
    mid,
    tid,
    subject,
    frm,
    date_ms,
    snippet,
    body="",
    labels=("INBOX",),
    to="Me <me@example.com>",
):
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": frm},
        {"name": "To", "value": to},
    ]
    payload = {
        "mimeType": "text/plain",
        "headers": headers,
        "body": {"data": b64url(body)} if body else {"size": 0},
    }
    return {
        "id": mid,
        "threadId": tid,
        "labelIds": list(labels),
        "snippet": snippet,
        "internalDate": str(date_ms),
        "payload": payload,
    }


def omsg(mid, cid, subject, name, addr, received, preview, body=""):
    return {
        "id": mid,
        "conversationId": cid,
        "subject": subject,
        "from": {"emailAddress": {"name": name, "address": addr}},
        "toRecipients": [{"emailAddress": {"name": "Me", "address": "me@outlook.com"}}],
        "ccRecipients": [],
        "receivedDateTime": received,
        "isRead": False,
        "flag": {"flagStatus": "notFlagged"},
        "bodyPreview": preview,
        "hasAttachments": False,
        "attachments": [],
        "body": {"contentType": "text", "content": body},
    }


def ok(path, body, query=None, times=None):
    route = {"method": "GET", "path": path, "status": 200, "json": body}
    if query:
        route["query"] = query
    if times:
        route["times"] = times
    return route


def fail(path, status, query=None, times=None, body=None):
    route = ok(
        path, body or {"error": {"code": status, "message": "nope"}}, query, times
    )
    route["status"] = status
    return route


GMAIL_LABELS = [
    ok(
        f"{G}/labels",
        {
            "labels": [
                {"id": "INBOX", "name": "INBOX"},
                {"id": "Label_1", "name": "Receipts"},
            ]
        },
    ),
    ok(
        f"{G}/labels/INBOX",
        {"id": "INBOX", "name": "INBOX", "messagesUnread": 3, "messagesTotal": 40},
    ),
    ok(
        f"{G}/labels/Label_1",
        {"id": "Label_1", "name": "Receipts", "messagesUnread": 0, "messagesTotal": 7},
    ),
]
PROFILE = ok(f"{G}/profile", {"emailAddress": "me@example.com"})

DANA = gmsg(
    "m1",
    "t1",
    "Q3 budget",
    '"Ruiz, Dana" <dana@example.com>',
    1788336900000,
    "Numbers &amp; notes",
    "Here are the Q3 numbers.",
)
LURE = gmsg(
    "m2",
    "t2",
    "Account notice",
    "PayPal Security <alerts@paypa1-secure.com>",
    1788337900000,
    "Verify your account now or your funds will be frozen",
    "Verify your account now or your funds will be frozen. Click http://paypa1-secure.com/login",
)
ACME = gmsg(
    "m3",
    "t3",
    "Invoice 4471",
    "Acme Billing <billing@acme.com>",
    1788338900000,
    "Your quarterly invoice",
    "Invoice total $120.",
)
ACME_2 = gmsg(
    "m4",
    "t3",
    "Re: Invoice 4471",
    "Acme Billing <billing@acme.com>",
    1788339900000,
    "Reminder",
    "Reminder about the invoice.",
)
OTHER = gmsg(
    "m5",
    "t5",
    "Quarterly review",
    "Lee <lee@example.com>",
    1788340900000,
    "Agenda for the quarterly review",
    "Agenda.",
)
LONG = gmsg(
    "m6",
    "t6",
    "Long read",
    "Pat <pat@example.com>",
    1788341900000,
    "A long one",
    "é" * 12500,
)

GMAIL_MESSAGES = [
    ok(f"{G}/messages/{m['id']}", m) for m in (DANA, LURE, ACME, ACME_2, OTHER)
]
LONG_ROUTE = ok(f"{G}/messages/m6", LONG)


def gmail_list(*msgs, **query):
    return ok(
        f"{G}/messages",
        {"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in msgs]},
        query or None,
    )


def gmail_base():
    return [PROFILE, *GMAIL_LABELS, *GMAIL_MESSAGES]


GOOGLE = {"google": {"scopes": [G_READ, G_COMPOSE], "account_email": "me@example.com"}}
GOOGLE_GRANT = {"google": [G_READ, G_COMPOSE]}
MICROSOFT = {"microsoft": {"scopes": [M_READ], "account_email": "me@outlook.com"}}
MICROSOFT_GRANT = {"microsoft": [M_READ]}

O_DANA = omsg(
    "AAMk1",
    "conv1",
    "Q3 budget",
    "Dana Ruiz",
    "dana@example.com",
    "2026-09-01T10:00:00Z",
    "Here are the numbers",
    "Here are the Q3 numbers.",
)
O_REPLY = omsg(
    "AAMk2",
    "conv1",
    "Re: Q3 budget",
    "Me",
    "me@outlook.com",
    "2026-09-01T11:00:00Z",
    "Thanks",
    "Thanks Dana.",
)
OUTLOOK = [
    ok(f"{M}", {"mail": None, "userPrincipalName": "me@outlook.com"}),
    ok(
        f"{M}/mailFolders",
        {
            "value": [
                {
                    "id": "f1",
                    "displayName": "Inbox",
                    "unreadItemCount": 2,
                    "totalItemCount": 9,
                }
            ]
        },
    ),
    ok(f"{M}/mailFolders/inbox/messages", {"value": [O_DANA]}),
    ok(f"{M}/messages/AAMk1", O_DANA),
    ok(
        f"{M}/messages",
        {"value": [O_DANA, O_REPLY], "@odata.nextLink": "https://next"},
        {"$filter": "conversationId eq 'conv1'"},
    ),
]

# A conversation the user is part of, ending in a direct ask to them.
W_ME_1 = gmsg(
    "w1",
    "tw",
    "Venue contract",
    "Me <me@example.com>",
    1788000000000,
    "",
    "Hi Sam, I have reviewed the venue contract and the catering terms in detail and I am happy with both.",
    labels=("SENT",),
    to="Sam <sam@vendor.com>",
)
W_SAM = gmsg(
    "w2",
    "tw",
    "Re: Venue contract",
    "Sam <sam@vendor.com>",
    1788100000000,
    "Can you confirm",
    "Thanks. Can you confirm the final headcount by Friday?",
    labels=("INBOX",),
)
WAITING = [
    PROFILE,
    *GMAIL_LABELS,
    ok(
        f"{G}/messages",
        {"messages": [{"id": "w2", "threadId": "tw"}]},
        {"labelIds": "INBOX"},
    ),
    ok(
        f"{G}/messages",
        {"messages": [{"id": "w1", "threadId": "tw"}]},
        {"labelIds": "SENT"},
    ),
    ok(f"{G}/threads/tw", {"id": "tw", "messages": [W_SAM, W_ME_1]}),
]


def thread_answer(tid, *msgs):
    return ok(f"{G}/threads/{tid}", {"id": tid, "messages": list(msgs)})


THREE = [
    gmsg(
        f"x{i}",
        "tx",
        f"Part {i}",
        "Kim <kim@example.com>",
        1788000000000 + i,
        "",
        "word " * 200,
    )
    for i in range(3)
]

CASES = []


def case(
    name,
    steps,
    *,
    surface="cli",
    connections=None,
    grants=None,
    routes=(),
    budget=12000,
    token_errors=None,
    ignore=(),
):
    CASES.append(
        {
            "name": name,
            "surface": surface,
            "budget": budget,
            "connections": connections or {},
            "grants": grants or {},
            "token_errors": token_errors or {},
            "routes": list(routes),
            "steps": steps,
            "ignore_keys": list(ignore),
        }
    )


def call(tool, **args):
    return {"tool": tool, "args": args}


NEW_TURN = {"new_turn": True}

for surface in ("agent_ui", "tui", "cli", ""):
    case(
        f"no_mailbox_{surface or 'none'}",
        [
            call("check_mailbox_access"),
            call("list_connected_mailboxes"),
            call("list_inbox"),
        ],
        surface=surface,
    )

for surface in ("agent_ui", "cli"):
    case(
        f"grant_missing_{surface}",
        [call("list_inbox"), call("list_connected_mailboxes")],
        surface=surface,
        connections=GOOGLE,
        grants={"google": ["https://www.googleapis.com/auth/calendar.events"]},
    )
    case(
        f"read_scope_missing_{surface}",
        [call("list_inbox"), call("list_connected_mailboxes")],
        surface=surface,
        connections={
            "google": {"scopes": [G_COMPOSE], "account_email": "me@example.com"}
        },
        grants=GOOGLE_GRANT,
    )
    case(
        f"configuration_{surface}",
        [call("list_inbox"), call("list_connected_mailboxes")],
        surface=surface,
        connections={
            "google": {"scopes": [], "account_email": "", "error": "configuration"}
        },
        grants=GOOGLE_GRANT,
    )
    case(
        f"busy_{surface}",
        [call("check_mailbox_access"), call("list_connected_mailboxes")],
        surface=surface,
        connections={
            "google": {"scopes": [], "account_email": "me@example.com", "error": "busy"}
        },
        grants=GOOGLE_GRANT,
    )

case(
    "read_scope_missing_with_outlook_needing_a_grant_agent_ui",
    [call("list_inbox")],
    surface="agent_ui",
    connections={
        "google": {"scopes": [G_COMPOSE], "account_email": "me@example.com"},
        **MICROSOFT,
    },
    grants=GOOGLE_GRANT,
)

case(
    "gmail_reads",
    [
        call("check_mailbox_access"),
        call("list_inbox", limit=2),
        call("list_inbox", limit=500, unread_only=True),
        call("list_inbox", limit=0),
        call("list_mail_folders"),
        call("list_connected_mailboxes"),
    ],
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    routes=[gmail_list(DANA, LURE, labelIds="INBOX"), *gmail_base()],
)

case(
    "both_connected_gmail_wins",
    [call("check_mailbox_access"), call("list_connected_mailboxes")],
    surface="agent_ui",
    connections={**GOOGLE, **MICROSOFT},
    grants={**GOOGLE_GRANT, **MICROSOFT_GRANT},
    routes=gmail_base(),
)

case(
    "outlook_reads",
    [
        call("check_mailbox_access"),
        call("list_inbox"),
        call("read_email", message_id="AAMk1"),
        call("get_thread", thread_id="conv1"),
        call("list_mail_folders"),
        call("list_connected_mailboxes"),
    ],
    connections=MICROSOFT,
    grants=MICROSOFT_GRANT,
    routes=OUTLOOK,
)

case(
    "search_broadens_to_a_single_term",
    [
        call(
            "search_email",
            query="please find the Acme quarterly invoice from last week",
        )
    ],
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    routes=[gmail_list(ACME, ACME_2, q="quarterly"), gmail_list(), *gmail_base()],
)

case(
    "search_exact_hit_with_alternatives_and_thread_dedupe",
    [
        call("search_email", query="Acme invoice"),
        call("search_email", query="Acme  invoice ", limit=1),
    ],
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    routes=[
        gmail_list(ACME, ACME_2, q="Acme invoice"),
        gmail_list(ACME, OTHER, LURE, q="invoice"),
        gmail_list(),
        *gmail_base(),
    ],
)

case(
    "search_sweep_stops_on_a_backend_error",
    [call("search_email", query="Acme quarterly invoice")],
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    routes=[
        gmail_list(ACME, q="Acme quarterly invoice"),
        fail(f"{G}/messages", 500),
        *gmail_base(),
    ],
)

case(
    "search_nothing_matches",
    [
        call("search_email", query="is:unread"),
        call("search_email", query="zebra mango kiwi"),
        call("search_email", query="   "),
    ],
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    routes=[gmail_list(), *gmail_base()],
)

case(
    "search_first_rung_error_fails",
    [call("search_email", query="Acme invoice")],
    surface="agent_ui",
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    routes=[fail(f"{G}/messages", 403), *gmail_base()],
)

case(
    "read_email_bounds_and_budget",
    [
        call("read_email", message_id="m6"),
        call("read_email", message_id="m2"),
        call("read_email", message_id="m6"),
        call("read_email", message_id="m1"),
        NEW_TURN,
        call("read_email", message_id="m1"),
        call("read_email", message_id="bad id!"),
        call("read_email", message_id="nope"),
    ],
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    budget=20000,
    routes=[fail(f"{G}/messages/nope", 404), LONG_ROUTE, *gmail_base()],
)

case(
    "get_thread_budget",
    [
        call("get_thread", thread_id="tx"),
        call("get_thread", thread_id="tx"),
        NEW_TURN,
        call("get_thread", thread_id="tempty"),
        call("get_thread", thread_id="t2"),
        NEW_TURN,
        call("read_email", message_id="m6"),
        call("get_thread", thread_id="tx"),
    ],
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    budget=2000,
    routes=[
        thread_answer("tx", *reversed(THREE)),
        thread_answer("tempty"),
        thread_answer("t2", LURE),
        LONG_ROUTE,
        *gmail_base(),
    ],
)

case(
    "a_401_re_resolves_once",
    [call("check_mailbox_access")],
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    routes=[fail(f"{G}/profile", 401, times=1), *gmail_base()],
)

for surface in ("agent_ui", "cli"):
    case(
        f"a_lasting_401_is_reported_{surface}",
        [call("list_mail_folders"), call("check_mailbox_access")],
        surface=surface,
        connections=GOOGLE,
        grants=GOOGLE_GRANT,
        routes=[fail(f"{G}/profile", 401), fail(f"{G}/labels", 401)],
    )

for surface in ("agent_ui", "tui"):
    case(
        f"revoked_token_{surface}",
        [call("list_inbox")],
        surface=surface,
        connections=GOOGLE,
        grants=GOOGLE_GRANT,
        token_errors={"google": "ConnectionRevokedError"},
        routes=gmail_base(),
    )

case(
    "waiting_on_you",
    [
        call("list_waiting_on_you"),
        call("list_waiting_on_you", max_inbox=999, min_age_hours=1),
    ],
    connections=GOOGLE,
    grants=GOOGLE_GRANT,
    routes=WAITING,
    ignore=["age_days"],
)

case(
    "waiting_on_you_long_thread_unchecked",
    [call("list_waiting_on_you", max_inbox=5)],
    connections=MICROSOFT,
    grants=MICROSOFT_GRANT,
    routes=[
        ok(f"{M}", {"mail": "me@outlook.com"}),
        ok(
            f"{M}/mailFolders/inbox/messages",
            {"value": [{"id": "AAMk1", "conversationId": "conv1"}]},
        ),
        ok(f"{M}/mailFolders/sentitems/messages", {"value": []}),
        ok(f"{M}/messages", {"value": [O_DANA], "@odata.nextLink": "https://next"}),
    ],
)

# ---- replay ----


def router(routes):
    used = [0] * len(routes)

    def handle(request):
        params = request.url.params
        for i, route in enumerate(routes):
            if route["method"] != request.method or route["path"] != request.url.path:
                continue
            if any(params.get(k) != v for k, v in (route.get("query") or {}).items()):
                continue
            if route.get("times") and used[i] >= route["times"]:
                continue
            used[i] += 1
            return httpx.Response(route["status"], json=route["json"])
        raise AssertionError(f"no route for {request.method} {request.url}")

    return handle


REAL_CLIENT = httpx.Client


def run(spec):
    handler = router(spec["routes"])
    httpx.Client = lambda **_: REAL_CLIENT(transport=httpx.MockTransport(handler))
    connections_by_provider = spec["connections"]
    connectors_api.get_connection = lambda p: copy.deepcopy(
        connections_by_provider.get(p)
    )
    connectors_grants.list_agent_grants = lambda p: (
        {email_tools.EMAIL_AGENT_ID: list(spec["grants"][p])}
        if p in spec["grants"]
        else {}
    )

    def token(provider, scopes, agent_id):
        kind = spec["token_errors"].get(provider)
        if kind:
            raise getattr(connectors_errors, kind)("refused for the test")
        return "tok"

    connectors_api.get_access_token_sync = lambda *, provider, scopes, agent_id: token(
        provider, scopes, agent_id
    )

    class Agent(email_tools.EmailToolsMixin):
        config = SimpleNamespace(surface=spec["surface"] or None)
        _turn_seq = 0

        def _truncation_budget(self):
            return (spec["budget"], 0)

    agent = Agent()
    agent.register_email_tools()
    outputs = []
    for step in spec["steps"]:
        if step.get("new_turn"):
            agent._turn_seq += 1
            outputs.append(None)
            continue
        outputs.append(
            json.loads(_TOOL_REGISTRY[step["tool"]]["function"](**step["args"]))
        )
    return outputs


def schemas():
    out = {}
    for name in TOOLS:
        entry = _TOOL_REGISTRY[name]
        out[name] = {
            "description": entry["description"],
            "atomic": entry["atomic"],
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


def main(out_dir=None):
    cases = []
    for spec in CASES:
        spec = dict(spec, expected=run(spec))
        cases.append(spec)
    httpx.Client = REAL_CLIENT
    target = Path(out_dir or Path(__file__).parent) / "email_tools_parity.json"
    target.write_text(
        json.dumps({"schemas": schemas(), "cases": cases}, indent=1, ensure_ascii=False)
        + "\n"
    )
    print(f"wrote {len(cases)} cases to {target}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
