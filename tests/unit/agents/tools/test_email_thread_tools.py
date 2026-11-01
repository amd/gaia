# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``get_thread``, ``list_connected_mailboxes`` and ``list_waiting_on_you``.

Every backend test asserts the outgoing request — path and query params — not
just that a stub answered, because a fake that accepts anything proves only
that the method ran. The fakes refuse what the real API refuses.
"""

import json

import httpx
import pytest
from _fake_mail_api import (
    NOW_MS,
    USER,
    FakeGmailApi,
    FakeGraphApi,
    Mail,
    outbound,
)

from gaia.agents.tools._email.errors import MailboxError
from gaia.agents.tools._email.phishing import UNTRUSTED_BODY_OPEN
from gaia.agents.tools.email_tools import EmailToolsMixin

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
MAIL_READWRITE = "https://graph.microsoft.com/Mail.ReadWrite"

ASK = "Could you please confirm the Q3 numbers before the review on Friday?"
REPLY = "Sure, I will pull the numbers together and send them over before the review."


def _conversation(thread="t1"):
    return [
        Mail(
            "a1", thread, sender="Dana <dana@example.com>", body="Kickoff.", age_days=9
        ),
        outbound("s1", thread, to="dana@example.com", body=REPLY, age_days=8),
        Mail("a2", thread, sender="Dana <dana@example.com>", body=ASK, age_days=3),
    ]


# --------------------------------------------------------------------------
# Gmail request shape
# --------------------------------------------------------------------------


def test_gmail_get_thread_calls_threads_get_with_full_format():
    api = FakeGmailApi(_conversation())
    thread = api.backend().get_thread("t1")

    sent = [r for r in api.requests if "/threads/" in r.url.path]
    assert len(sent) == 1
    assert sent[0].method == "GET"
    assert sent[0].url.path == "/gmail/v1/users/me/threads/t1"
    assert sent[0].url.params["format"] == "full"
    assert [m["id"] for m in thread["messages"]] == ["a1", "s1", "a2"]
    assert thread["messages"][2]["body"] == ASK
    assert thread["truncated"] is False


def test_gmail_get_thread_flags_drafts_and_keeps_raw_labels_private():
    mails = _conversation() + [
        Mail("d1", "t1", sender=f"Me <{USER}>", body="half", age_days=1, draft=True)
    ]
    messages = FakeGmailApi(mails).backend().get_thread("t1")["messages"]
    assert messages[-1]["draft"] is True
    assert all("_label_ids" in m for m in messages)
    assert "DRAFT" in messages[-1]["_label_ids"]


@pytest.mark.parametrize(
    "bad", ["", "t 1", "../labels", "t1/../../profile", "t1?x=1", "x" * 129]
)
def test_gmail_get_thread_refuses_a_malformed_id_before_any_request(bad):
    api = FakeGmailApi(_conversation())
    with pytest.raises(ValueError, match="thread_id"):
        api.backend().get_thread(bad)
    assert api.requests == []


def test_gmail_unknown_thread_404_names_the_fix():
    with pytest.raises(MailboxError, match="404.*list_inbox"):
        FakeGmailApi(_conversation()).backend().get_thread("nope")


@pytest.mark.parametrize("folder,label", [("inbox", "INBOX"), ("sent", "SENT")])
def test_gmail_list_thread_ids_lists_by_system_label(folder, label):
    api = FakeGmailApi(_conversation())
    out = api.backend().list_thread_ids(folder, limit=7)
    req = api.requests[0]
    assert req.url.path == "/gmail/v1/users/me/messages"
    assert req.url.params.get_list("labelIds") == [label]
    assert req.url.params["maxResults"] == "7"
    assert out["thread_ids"] == ["t1"]


def test_gmail_list_thread_ids_dedupes_and_reports_truncation():
    mails = [Mail(f"m{i}", f"t{i % 2}", sender="a@x.com", age_days=i) for i in range(5)]
    out = FakeGmailApi(mails).backend().list_thread_ids("inbox", limit=3)
    assert out["thread_ids"] == ["t0", "t1"]
    assert out["messages_listed"] == 3
    assert out["truncated"] is True


def test_gmail_list_thread_ids_is_not_truncated_when_the_folder_fits():
    out = FakeGmailApi(_conversation()).backend().list_thread_ids("inbox", limit=10)
    assert out["truncated"] is False


def test_gmail_list_thread_ids_without_thread_id_fails_loudly():
    def handler(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        return httpx.Response(404)

    from gaia.agents.tools._email.gmail import GmailReadBackend

    backend = GmailReadBackend(
        lambda: "t", http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MailboxError, match="threadId"):
        backend.list_thread_ids("inbox")


def test_list_thread_ids_rejects_an_unknown_folder():
    with pytest.raises(ValueError, match="folder"):
        FakeGmailApi([]).backend().list_thread_ids("archive")
    with pytest.raises(ValueError, match="folder"):
        FakeGraphApi([]).backend().list_thread_ids("archive")


# --------------------------------------------------------------------------
# Graph request shape
# --------------------------------------------------------------------------


def test_graph_get_thread_filters_on_conversation_without_orderby():
    api = FakeGraphApi(_conversation("conv-1"))
    thread = api.backend().get_thread("conv-1")

    req = api.requests[0]
    assert req.url.path == "/v1.0/me/messages"
    assert req.url.params["$filter"] == "conversationId eq 'conv-1'"
    # $orderby with this filter is Graph's InefficientFilter 400.
    assert "$orderby" not in req.url.params
    assert req.url.params["$top"] == "100"
    select = req.url.params["$select"].split(",")
    assert {"body", "conversationId", "receivedDateTime", "isDraft"} <= set(select)
    # Auto-reply detection in waiting-on-you needs the real Graph headers.
    assert "internetMessageHeaders" in select
    assert [m["id"] for m in thread["messages"]] == ["a1", "s1", "a2"]


def test_graph_get_thread_reports_a_conversation_longer_than_one_page():
    mails = [
        Mail(f"m{i}", "conv", sender="a@x.com", body="x", age_days=200 - i)
        for i in range(101)
    ]
    thread = FakeGraphApi(mails).backend().get_thread("conv")
    assert thread["truncated"] is True


@pytest.mark.parametrize(
    "bad", ["conv' or isRead eq true or conversationId eq '", "a b", "x'y", ""]
)
def test_graph_get_thread_keeps_quotes_out_of_the_odata_literal(bad):
    api = FakeGraphApi(_conversation("conv-1"))
    with pytest.raises(ValueError, match="thread_id"):
        api.backend().get_thread(bad)
    assert api.requests == []


def test_graph_get_thread_accepts_a_real_shaped_conversation_id():
    conv = "AAQkADAwATM0MDAAMS1iNWU2LTg0ZjYtMDACLTAwCgAQAJ+xZ/8=="
    api = FakeGraphApi(_conversation(conv))
    assert len(api.backend().get_thread(conv)["messages"]) == 3


@pytest.mark.parametrize(
    "folder,path",
    [
        ("inbox", "/v1.0/me/mailFolders/inbox/messages"),
        ("sent", "/v1.0/me/mailFolders/sentitems/messages"),
    ],
)
def test_graph_list_thread_ids_uses_well_known_folders(folder, path):
    api = FakeGraphApi(_conversation())
    api.backend().list_thread_ids(folder, limit=5)
    req = api.requests[0]
    assert req.url.path == path
    assert req.url.params["$top"] == "5"
    assert req.url.params["$select"] == "id,conversationId"
    assert req.url.params["$orderby"] == "receivedDateTime desc"


def test_graph_list_thread_ids_reports_a_next_page():
    mails = [Mail(f"m{i}", f"c{i}", sender="a@x.com", age_days=i) for i in range(4)]
    out = FakeGraphApi(mails).backend().list_thread_ids("inbox", limit=2)
    assert out["truncated"] is True
    assert out["thread_ids"] == ["c0", "c1"]


def test_graph_unknown_item_404_names_the_fix():
    def handler(request):
        return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})

    from gaia.agents.tools._email.graph import OutlookReadBackend

    backend = OutlookReadBackend(
        lambda: "t", http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(MailboxError, match="404.*list_inbox"):
        backend.get_thread("conv-1")


def test_both_backends_return_the_same_thread_shape():
    gmail = FakeGmailApi(_conversation()).backend().get_thread("t1")["messages"]
    graph = FakeGraphApi(_conversation("t1")).backend().get_thread("t1")["messages"]
    assert [sorted(m) for m in gmail] == [sorted(m) for m in graph]
    assert [m["from"] for m in gmail] == [m["from"] for m in graph]
    assert [m["received"] for m in gmail] == [m["received"] for m in graph]


# --------------------------------------------------------------------------
# get_thread tool
# --------------------------------------------------------------------------


class _Harness(EmailToolsMixin):
    def __init__(self, backend, provider="google"):
        self._email_backend = backend
        self._email_provider = provider

    def tool(self, name):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        return _TOOL_REGISTRY[name]["function"]


def _harness(backend, provider="google"):
    h = _Harness(backend, provider)
    h.register_email_tools()
    return h


@pytest.fixture(params=["gmail", "graph"])
def provider_api(request):
    return FakeGmailApi if request.param == "gmail" else FakeGraphApi


def test_get_thread_tool_numbers_messages_and_fences_bodies(provider_api):
    h = _harness(provider_api(_conversation()).backend())
    out = json.loads(h.tool("get_thread")(thread_id="t1"))
    assert out["success"] is True
    assert out["count"] == 3
    assert [(m["index"], m["of_total"]) for m in out["messages"]] == [
        (1, 3),
        (2, 3),
        (3, 3),
    ]
    assert all(m["body"].startswith(UNTRUSTED_BODY_OPEN) for m in out["messages"])
    # Backend-private keys never reach the model.
    assert not any(k.startswith("_") for m in out["messages"] for k in m)


def test_get_thread_tool_screens_a_lure_inside_a_thread():
    lure = Mail(
        "x1",
        "t1",
        sender="Security <alerts@paypa1-secure.tk>",
        subject="Your account is suspended - verify now",
        body="Verify your password here or lose access.",
        age_days=1,
    )
    h = _harness(FakeGmailApi(_conversation() + [lure]).backend())
    out = json.loads(h.tool("get_thread")(thread_id="t1"))
    assert out["suspicious_count"] >= 1
    assert "suspicious_guidance" in out
    assert out["messages"][-1]["suspicious"] is True


def test_get_thread_tool_clips_long_bodies_but_never_drops_a_message():
    mails = [
        Mail(f"m{i}", "t1", sender="a@x.com", body="word " * 20_000, age_days=50 - i)
        for i in range(30)
    ]
    h = _harness(FakeGmailApi(mails).backend())
    out = json.loads(h.tool("get_thread")(thread_id="t1"))
    assert out["count"] == 30
    assert out["bodies_truncated"] == 30
    assert all(m["body_truncated"] for m in out["messages"])


def test_get_thread_tool_charges_the_turn_budget_and_then_refuses():
    mails = [
        Mail(f"m{i}", "t1", sender="a@x.com", body="word " * 20_000, age_days=9 - i)
        for i in range(3)
    ]
    h = _harness(FakeGmailApi(mails).backend())
    h._email_turn_budget_chars = lambda: 6_000
    first = json.loads(h.tool("get_thread")(thread_id="t1"))
    assert first["success"] is True
    # Three messages share the whole budget: 2,000 chars each, none dropped.
    assert first["count"] == 3
    assert h._email_turn_body_chars == 6_000

    second = json.loads(h.tool("get_thread")(thread_id="t1"))
    assert second["success"] is False
    assert second["turn_budget_exhausted"] is True


def test_get_thread_tool_refusal_happens_before_any_request():
    api = FakeGmailApi(_conversation())
    h = _harness(api.backend())
    h._email_turn_budget_chars = lambda: 10
    h._email_turn_reset_if_stale()
    h._email_turn_body_chars = 10
    out = json.loads(h.tool("get_thread")(thread_id="t1"))
    assert out["turn_budget_exhausted"] is True
    assert api.requests == []


def test_a_thread_too_big_for_the_remaining_budget_is_refused_whole():
    """NPU-sized budget: 75 messages at the 400-char floor need 30,000+ chars,
    so reading it would overflow. Refused before anything is charged."""
    mails = [
        Mail(f"m{i}", "t1", sender="a@x.com", body="word " * 500, age_days=99 - i)
        for i in range(76)
    ]
    h = _harness(FakeGmailApi(mails).backend())
    h._email_turn_budget_chars = lambda: 30_000
    out = json.loads(h.tool("get_thread")(thread_id="t1"))
    assert out["success"] is False
    assert out["turn_budget_exhausted"] is True
    assert out["thread_message_count"] == 76
    assert out["budget_remaining_chars"] == 30_000
    assert "76 messages" in out["error"] and "read_email" in out["error"]
    assert "messages" not in out
    assert h._email_turn_body_chars == 0


def test_a_short_thread_after_earlier_reads_is_refused_not_overflowed():
    """Earlier read_email calls left less than 3 x 400 chars; the 3-message
    thread must not be read past the budget."""
    h = _harness(FakeGmailApi(_conversation()).backend())
    h._email_turn_budget_chars = lambda: 30_000
    h._email_turn_reset_if_stale()
    h._email_turn_body_chars = 29_000
    out = json.loads(h.tool("get_thread")(thread_id="t1"))
    assert out["turn_budget_exhausted"] is True
    assert out["budget_remaining_chars"] == 1_000
    assert h._email_turn_body_chars == 29_000


def test_a_thread_that_just_fits_never_exceeds_the_budget():
    """At exactly n x floor the read succeeds and spends no more than is left."""
    mails = [
        Mail(f"m{i}", "t1", sender="a@x.com", body="word " * 500, age_days=9 - i)
        for i in range(5)
    ]
    h = _harness(FakeGmailApi(mails).backend())
    h._email_turn_budget_chars = lambda: 3_000
    h._email_turn_reset_if_stale()
    h._email_turn_body_chars = 1_000
    out = json.loads(h.tool("get_thread")(thread_id="t1"))
    assert out["success"] is True
    assert out["count"] == 5
    assert h._email_turn_body_chars <= 3_000


def test_get_thread_tool_says_when_the_thread_is_incomplete():
    mails = [
        Mail(f"m{i}", "conv", sender="a@x.com", body="x", age_days=200 - i)
        for i in range(101)
    ]
    h = _harness(FakeGraphApi(mails).backend(), provider="microsoft")
    h._email_turn_budget_chars = lambda: 100_000  # room for 100 x 400
    out = json.loads(h.tool("get_thread")(thread_id="conv"))
    assert out["thread_incomplete"] is True
    assert "longer" in out["note"]


def test_get_thread_tool_surfaces_a_bad_id_as_an_error_not_an_empty_thread():
    h = _harness(FakeGmailApi(_conversation()).backend())
    out = json.loads(h.tool("get_thread")(thread_id="not an id"))
    assert out["success"] is False
    assert "messages" not in out


def test_get_thread_tool_reports_an_empty_thread_loudly():
    def handler(request):
        if request.url.path.endswith("/labels"):
            return httpx.Response(200, json={"labels": []})
        return httpx.Response(200, json={"id": "t1", "messages": []})

    from gaia.agents.tools._email.gmail import GmailReadBackend

    backend = GmailReadBackend(
        lambda: "t", http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    out = json.loads(_harness(backend).tool("get_thread")(thread_id="t1"))
    assert out["success"] is False
    assert "no messages" in out["error"]


# --------------------------------------------------------------------------
# list_connected_mailboxes
# --------------------------------------------------------------------------


@pytest.fixture
def connectors(monkeypatch):
    """Synthetic connector + grant state; minting a token is a test failure."""
    from gaia.agents.tools._email import fixture

    monkeypatch.delenv(fixture.EVAL_MAILBOX_ENV, raising=False)

    def apply(connections, grants):
        import gaia.connectors.api as api
        import gaia.connectors.grants as grants_mod

        monkeypatch.setattr(api, "get_connection", lambda p: connections.get(p))
        monkeypatch.setattr(
            grants_mod, "list_agent_grants", lambda p: dict(grants.get(p) or {})
        )

        def no_network(**_):
            raise AssertionError("list_connected_mailboxes must not mint a token")

        monkeypatch.setattr(api, "get_access_token_sync", no_network)

    return apply


def _conn(scopes, email="me@example.com"):
    return {"provider": "x", "account_email": email, "scopes": list(scopes)}


def _status_tool():
    h = EmailToolsMixin()
    h.register_email_tools()
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY["list_connected_mailboxes"]["function"]


def test_connected_mailboxes_names_the_active_one(connectors):
    connectors(
        {"google": _conn([GMAIL_READONLY])},
        {"google": {"installed:gaia": [GMAIL_READONLY]}},
    )
    out = json.loads(_status_tool()())
    assert out["success"] is True
    assert out["connected"] is True
    assert out["active"] == "google"
    by = {m["provider"]: m for m in out["mailboxes"]}
    assert by["google"]["usable"] is True
    assert by["google"]["account_email"] == "me@example.com"
    assert by["microsoft"]["connected"] is False
    assert "gaia connectors connect microsoft" in by["microsoft"]["status"]


def test_connected_but_ungranted_is_connected_not_active(connectors):
    connectors(
        {
            "google": _conn([GMAIL_READONLY]),
            "microsoft": _conn([MAIL_READWRITE], email="me@corp.com"),
        },
        {"microsoft": {"installed:gaia": [MAIL_READWRITE]}},
    )
    out = json.loads(_status_tool()())
    by = {m["provider"]: m for m in out["mailboxes"]}
    assert by["google"]["connected"] is True
    assert by["google"]["usable"] is False
    assert "grants grant google installed:gaia" in by["google"]["status"]
    assert out["active"] == "microsoft"


def test_active_matches_what_the_mail_tools_would_select(connectors):
    """Both usable: Gmail wins on registry order, as _resolve_mailbox does."""
    connectors(
        {"google": _conn([GMAIL_READONLY]), "microsoft": _conn([MAIL_READWRITE])},
        {
            "google": {"installed:gaia": [GMAIL_READONLY]},
            "microsoft": {"installed:gaia": [MAIL_READWRITE]},
        },
    )
    out = json.loads(_status_tool()())
    chosen, *_ = EmailToolsMixin()._resolve_mailbox()
    assert out["active"] == chosen == "google"


def test_nothing_connected_says_how_to_connect(connectors):
    connectors({}, {})
    out = json.loads(_status_tool()())
    assert out["connected"] is False
    assert out["active"] is None
    assert "gaia connectors connect" in out["message"]


def test_the_no_address_sentinel_is_never_shown_as_an_address(connectors):
    from gaia.connectors.store import DEFAULT_ACCOUNT

    connectors(
        {"google": _conn([GMAIL_READONLY], email=DEFAULT_ACCOUNT)},
        {"google": {"installed:gaia": [GMAIL_READONLY]}},
    )
    by = {m["provider"]: m for m in json.loads(_status_tool()())["mailboxes"]}
    assert by["google"]["account_email"] is None


def test_a_connector_failure_is_an_error_not_a_disconnected_mailbox(monkeypatch):
    from gaia.agents.tools._email import fixture

    monkeypatch.delenv(fixture.EVAL_MAILBOX_ENV, raising=False)
    import gaia.connectors.api as api

    def boom(_):
        raise RuntimeError("keyring locked")

    monkeypatch.setattr(api, "get_connection", boom)
    out = json.loads(_status_tool()())
    assert out["success"] is False
    assert "keyring locked" in out["error"]
    assert "connected" not in out


@pytest.mark.parametrize("attached", [True, False])
def test_eval_fixture_never_consults_the_connectors(monkeypatch, tmp_path, attached):
    from gaia.agents.tools._email import fixture

    monkeypatch.setenv(fixture.EVAL_MAILBOX_ENV, str(tmp_path / "inbox.mbox"))
    monkeypatch.setattr(fixture, "_attached", attached)
    import gaia.connectors.api as api

    def leak(_):
        raise AssertionError("a real mailbox must not leak into an eval")

    monkeypatch.setattr(api, "get_connection", leak)
    out = json.loads(_status_tool()())
    assert out["success"] is True
    assert out["connected"] is attached
    assert out["active"] == ("google" if attached else None)


# --------------------------------------------------------------------------
# list_waiting_on_you tool
# --------------------------------------------------------------------------


def _waiting(api_cls, mails, provider="google", **kwargs):
    import gaia.agents.tools._email.waiting as waiting

    real = waiting.detect_waiting_on_you

    def pinned(backend, **kw):
        return real(backend, now_ms=NOW_MS, **kw)

    h = _harness(api_cls(mails).backend(), provider)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("gaia.agents.tools.email_tools.detect_waiting_on_you", pinned)
        return json.loads(h.tool("list_waiting_on_you")(**kwargs))


def test_waiting_tool_finds_the_thread_on_both_providers(provider_api):
    out = _waiting(provider_api, _conversation())
    assert out["success"] is True
    assert out["count"] == 1
    item = out["waiting_on_you"][0]
    assert item["message_id"] == "a2"
    assert item["thread_id"] == "t1"
    assert item["from"] == "Dana <dana@example.com>"
    assert item["signal"] == "direct_ask"
    assert item["age_days"] == 3
    assert out["scan_truncated"] is False


def test_waiting_tool_orders_most_overdue_first(provider_api):
    mails = _conversation("t1") + [
        Mail("b1", "t2", sender="Ben <ben@example.com>", body="Kickoff.", age_days=30),
        outbound("s2", "t2", to="ben@example.com", body=REPLY, age_days=20),
        Mail("b2", "t2", sender="Ben <ben@example.com>", body=ASK, age_days=12),
    ]
    out = _waiting(provider_api, mails)
    assert [i["thread_id"] for i in out["waiting_on_you"]] == ["t2", "t1"]


def test_waiting_tool_empty_result_is_success_not_error(provider_api):
    cold = [Mail("c1", "t9", sender="Vendor <sales@vendor.com>", body=ASK)]
    out = _waiting(provider_api, cold)
    assert out["success"] is True
    assert out["waiting_on_you"] == []


def test_waiting_tool_flags_a_lure_that_qualifies():
    mails = [
        Mail("a1", "t1", sender="IT <it@paypa1-secure.tk>", body="Hi", age_days=9),
        outbound("s1", "t1", to="it@paypa1-secure.tk", body=REPLY, age_days=8),
        Mail(
            "a2",
            "t1",
            sender="IT <it@paypa1-secure.tk>",
            subject="Your account is suspended - verify now",
            body="Could you please verify your password here?",
            age_days=2,
        ),
    ]
    out = _waiting(FakeGmailApi, mails)
    assert out["count"] == 1
    assert out["waiting_on_you"][0]["suspicious"] is True
    assert out["suspicious_count"] == 1
    assert "suspicious_guidance" in out


def test_waiting_tool_clamps_the_scan_budget():
    api = FakeGmailApi(_conversation())
    h = _harness(api.backend())
    h.tool("list_waiting_on_you")(max_inbox=99_999)
    inbox = [
        r
        for r in api.requests
        if r.url.path.endswith("/messages")
        and "INBOX" in r.url.params.get_list("labelIds")
    ]
    assert inbox[0].url.params["maxResults"] == "100"  # Gmail backend's own cap


def test_waiting_tool_reports_a_truncated_scan(provider_api):
    mails = _conversation() + [
        Mail(f"n{i}", f"n{i}", sender="a@x.com", age_days=0.1) for i in range(5)
    ]
    out = _waiting(provider_api, mails, max_inbox=3)
    assert out["scan_truncated"] is True


def test_waiting_tool_reports_an_unreadable_long_thread():
    long = [
        Mail(f"m{i}", "conv", sender="a@x.com", body="x", age_days=200 - i)
        for i in range(101)
    ]
    out = _waiting(FakeGraphApi, long, provider="microsoft")
    assert out["threads_unchecked"] == ["conv"]
    assert "incomplete" in out["note"]


def test_waiting_tool_surfaces_a_backend_failure():
    def handler(request):
        return httpx.Response(401)

    from gaia.agents.tools._email.gmail import GmailReadBackend

    backend = GmailReadBackend(
        lambda: "t", http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    h = _harness(backend)
    out = json.loads(h.tool("list_waiting_on_you")())
    assert out["success"] is False
    assert "gaia connectors connect google" in out["error"]
    # An empty list here would read as "nothing is waiting on you".
    assert "waiting_on_you" not in out


def test_waiting_tool_rejects_a_negative_age():
    h = _harness(FakeGmailApi(_conversation()).backend())
    out = json.loads(h.tool("list_waiting_on_you")(min_age_hours=-1))
    assert out["success"] is False
    assert "min_age_hours" in out["error"]


def test_waiting_tool_reresolves_once_after_an_auth_failure(monkeypatch):
    """Same one-shot re-resolve as every other mail tool."""
    from gaia.agents.tools._email.gmail import GmailReadBackend

    def dead(request):
        return httpx.Response(401)

    stale = GmailReadBackend(
        lambda: "t", http_client=httpx.Client(transport=httpx.MockTransport(dead))
    )
    fresh = FakeGmailApi(_conversation()).backend()
    h = _harness(stale)
    h._email_backend_is_owned = True
    monkeypatch.setattr(h, "_build_email_backend", lambda: fresh)
    out = json.loads(h.tool("list_waiting_on_you")())
    assert out["success"] is True


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


NEW_TOOLS = {"get_thread", "list_connected_mailboxes", "list_waiting_on_you"}


def test_new_tools_register_alongside_the_existing_five():
    from gaia.agents.base.tools import _TOOL_REGISTRY

    EmailToolsMixin().register_email_tools()
    assert NEW_TOOLS <= set(_TOOL_REGISTRY)


def test_online_tools_are_refused_offline_and_status_is_not():
    """get_thread and the waiting scan reach the mailbox; the status read is
    local connector state, so it stays answerable with the internet off."""
    from gaia.agents.base.offline import ONLINE_SERVICE_TOOLS

    assert {"get_thread", "list_waiting_on_you"} <= ONLINE_SERVICE_TOOLS
    assert "list_connected_mailboxes" not in ONLINE_SERVICE_TOOLS


def test_new_tools_have_a_plain_language_label_in_the_flagship_ui():
    from gaia.ui.email_sidecar.profiles import _GAIA_TOOL_LABELS

    assert NEW_TOOLS <= set(_GAIA_TOOL_LABELS)


def test_tool_docstrings_name_the_ids_the_model_must_pass():
    from gaia.agents.base.tools import _TOOL_REGISTRY

    EmailToolsMixin().register_email_tools()
    doc = _TOOL_REGISTRY["get_thread"]["description"]
    assert "thread_id" in doc and "list_waiting_on_you" in doc
    waiting = " ".join(_TOOL_REGISTRY["list_waiting_on_you"]["description"].split())
    assert "empty list is a real answer" in waiting
