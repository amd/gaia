# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
REST contract-surface tests for the Email Triage agent (#1645).

These productionize the cross-implementation contract: the committed
``openapi.email.json`` is what the ``@amd-gaia/agent-email`` npm client and the
future native build conform to, and these tests fail CI if any of the three
sources drift apart:

1. ``version.py`` constants — ``API_VERSION`` must equal the frozen contract's
   ``SCHEMA_VERSION`` (a contract bump is an API bump), and ``AGENT_VERSION``
   must match the installed package metadata.
2. ``api_routes.py`` response models — every documented route's 200 schema must
   reference the contract/local model the handler declares.
3. The exported ``openapi.email.json`` — must be byte-identical to a freshly
   generated spec (otherwise it is stale and must be regenerated).

The runtime ``/health`` and ``/version`` endpoints are exercised through a
FastAPI ``TestClient`` against the same minimal app the exporter builds — no live
mailbox, no LLM.
"""

from __future__ import annotations

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import __version__ as package_version  # noqa: E402
from gaia_agent_email import export_openapi  # noqa: E402
from gaia_agent_email.contract import (  # noqa: E402
    SCHEMA_VERSION,
    BatchTriageRequest,
    BatchTriageResponse,
    EmailTriageRequest,
    EmailTriageResponse,
)
from gaia_agent_email.version import AGENT_VERSION, API_VERSION  # noqa: E402

# Routes whose 200 response model is part of the published contract surface.
# Maps (method, path) -> the component schema name the handler declares.
_EXPECTED_RESPONSE_MODELS = {
    ("post", "/v1/email/triage"): "EmailTriageResponse",
    ("post", "/v1/email/triage/batch"): "BatchTriageResponse",  # #1887 additive
    ("post", "/v1/email/search"): "EmailSearchResponse",
    ("post", "/v1/email/prescan"): "EmailPreScanResponse",
    # Scheduled daily briefing (#1608 additive) — the pull surface for the
    # sidecar's scheduled pre-scan runs.
    ("get", "/v1/email/briefing"): "EmailBriefingResponse",
    ("post", "/v1/email/draft"): "EmailDraftResponse",
    ("post", "/v1/email/send"): "EmailSendResponse",
    # Mailbox actions (schema 2.1, #1779).
    ("post", "/v1/email/confirm"): "EmailActionConfirmResponse",
    ("post", "/v1/email/archive"): "EmailArchiveResponse",
    ("post", "/v1/email/unarchive"): "EmailUnarchiveResponse",
    ("post", "/v1/email/quarantine"): "EmailQuarantineResponse",
    ("post", "/v1/email/unquarantine"): "EmailUnquarantineResponse",
    ("get", "/v1/email/health"): "HealthResponse",
    ("get", "/v1/email/version"): "VersionResponse",
    # Calendar surface (schema 2.1, #1780).
    ("get", "/v1/email/calendar/events"): "CalendarEventsResponse",
    ("post", "/v1/email/calendar/events"): "CalendarEventResponse",
    ("post", "/v1/email/calendar/events/preview"): "CalendarEventPreviewResponse",
    ("post", "/v1/email/calendar/events/respond"): "CalendarRespondResponse",
}


@pytest.fixture(scope="module")
def spec() -> dict:
    """The freshly built OpenAPI spec (what the committed artifact should be)."""
    return export_openapi.build_spec()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(export_openapi.build_app())


# ---------------------------------------------------------------------------
# 1. Version constants — single source of truth
# ---------------------------------------------------------------------------


def test_api_version_is_the_contract_version():
    # apiVersion MUST be the frozen contract version so bumping the contract
    # bumps the API — they cannot drift. This is the constant the freeze server
    # can import instead of carrying its own copy (#1648).
    assert API_VERSION == SCHEMA_VERSION


def test_agent_version_matches_package_export():
    assert AGENT_VERSION == package_version


def test_agent_version_matches_package_metadata():
    # The pyproject ``version`` and the in-code ``AGENT_VERSION`` must agree, or
    # a published wheel reports a build number its own code denies.
    from importlib.metadata import version as dist_version

    assert dist_version("gaia-agent-email") == AGENT_VERSION


# ---------------------------------------------------------------------------
# 2. Spec ↔ contract.py consistency
# ---------------------------------------------------------------------------


def test_spec_info_version_is_api_version(spec):
    assert spec["info"]["version"] == API_VERSION


def test_contract_models_present_in_spec(spec):
    schemas = spec["components"]["schemas"]
    for name in (
        "EmailTriageRequest",
        "EmailTriageResponse",
        "EmailTriageResult",
        # Batch models (#1887 additive)
        "BatchTriageRequest",
        "BatchTriageResponse",
        "BatchItemResult",
    ):
        assert name in schemas, f"{name} missing from exported OpenAPI components"


@pytest.mark.parametrize(
    "model",
    [EmailTriageRequest, EmailTriageResponse, BatchTriageRequest, BatchTriageResponse],
)
def test_spec_schema_matches_contract_model(spec, model):
    """Field names + required set in the exported spec must match the pydantic
    contract model — drift between contract.py and the published spec fails."""
    component = spec["components"]["schemas"][model.__name__]
    pyd = model.model_json_schema()
    assert set(component.get("properties", {})) == set(pyd.get("properties", {}))
    assert set(component.get("required", [])) == set(pyd.get("required", []))


# ---------------------------------------------------------------------------
# 3. Spec ↔ api_routes.py response-model consistency
# ---------------------------------------------------------------------------


def test_documented_routes_match_expected_set(spec):
    documented = {
        (method, path) for path, ops in spec["paths"].items() for method in ops
    }
    assert documented == set(_EXPECTED_RESPONSE_MODELS)


@pytest.mark.parametrize(
    ("method", "path", "model_name"),
    [(m, p, n) for (m, p), n in _EXPECTED_RESPONSE_MODELS.items()],
)
def test_route_response_model_in_spec(spec, method, path, model_name):
    schema = spec["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema == {"$ref": f"#/components/schemas/{model_name}"}


# ---------------------------------------------------------------------------
# 4. Committed artifact is not stale
# ---------------------------------------------------------------------------


def test_committed_openapi_artifact_is_up_to_date():
    assert export_openapi.check_artifact(), (
        "openapi.email.json is stale. Regenerate it with:\n"
        "  python -m gaia_agent_email.export_openapi"
    )


# ---------------------------------------------------------------------------
# 5. Runtime /health and /version (dependency-light — no mail, no LLM)
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    resp = client.get("/v1/email/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "gaia-agent-email"}


def test_version_endpoint_reports_constants(client):
    resp = client.get("/v1/email/version")
    assert resp.status_code == 200
    assert resp.json() == {
        "apiVersion": API_VERSION,
        "agentVersion": AGENT_VERSION,
    }


def test_version_endpoint_rejects_unknown_field_loudly(client):
    # _Strict models forbid extras; a GET has no body, but confirm the response
    # shape carries exactly the two documented keys (no silent extras).
    body = client.get("/v1/email/version").json()
    assert set(body) == {"apiVersion", "agentVersion"}


# ---------------------------------------------------------------------------
# 6. Inbox search (#1781) — read-only; backend injected via dependency_overrides
# ---------------------------------------------------------------------------

import base64  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from gaia_agent_email import api_routes  # noqa: E402


def _gmail_message(
    mid: str, *, subject: str, frm: str, to: str, snippet: str, labels
) -> dict:
    """A minimal Gmail-API-v1-shaped message the production header/body
    decoder (via ``_format_message_for_llm``) can parse."""
    data = base64.urlsafe_b64encode(b"Body text the search list drops.").decode()
    return {
        "id": mid,
        "threadId": f"t-{mid}",
        "snippet": snippet,
        "labelIds": list(labels),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": frm},
                {"name": "To", "value": to},
                {"name": "Date", "value": "Mon, 01 Jan 2026 10:00:00 +0000"},
            ],
            "body": {"data": data.rstrip("=")},
        },
    }


class _FakeSearchBackend:
    """Inject-only fake exposing the two read methods the search route uses.

    Records the exact ``list_messages`` call so a test can assert the route
    forwards query/labels/max_results/page_token to the backend
    (boundary-validity, not just invocation).
    """

    def __init__(self, messages):
        self._messages = {m["id"]: m for m in messages}
        self.calls: list[dict] = []

    def list_messages(
        self, *, query=None, label_ids=None, max_results=25, page_token=None
    ):
        self.calls.append(
            {
                "query": query,
                "label_ids": list(label_ids) if label_ids else None,
                "max_results": max_results,
                "page_token": page_token,
            }
        )
        ids = list(self._messages)
        page = ids[:max_results]
        return {
            "messages": [
                {"id": i, "threadId": self._messages[i]["threadId"]} for i in page
            ],
            "nextPageToken": "next-tok" if len(ids) > max_results else None,
        }

    def get_message(self, message_id: str):
        return self._messages[message_id]


def test_search_returns_messages_via_injected_backend(client):
    fake = _FakeSearchBackend(
        [
            _gmail_message(
                "m1",
                subject="Prod incident",
                frm="Sarah Chen <sarah@example.com>",
                to="me@example.com",
                snippet="please review",
                labels=["INBOX", "UNREAD"],
            )
        ]
    )
    client.app.dependency_overrides[api_routes.get_search_backend] = lambda: fake
    try:
        resp = client.post(
            "/v1/email/search", json={"query": "is:unread", "max_results": 10}
        )
    finally:
        client.app.dependency_overrides.pop(api_routes.get_search_backend, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["query"] == "is:unread"
    assert body["count"] == 1
    msg = body["messages"][0]
    assert msg["id"] == "m1"
    assert msg["thread_id"] == "t-m1"
    assert msg["subject"] == "Prod incident"
    # Wire alias: raw 'From' header under the key `from`, never `from_`.
    assert msg["from"] == "Sarah Chen <sarah@example.com>"
    assert "from_" not in msg
    assert msg["snippet"] == "please review"
    assert msg["label_ids"] == ["INBOX", "UNREAD"]
    # The route must forward the query + max_results to the backend verbatim.
    assert fake.calls == [
        {
            "query": "is:unread",
            "label_ids": None,
            "max_results": 10,
            "page_token": None,
        }
    ]


def test_search_empty_body_lists_inbox(client):
    fake = _FakeSearchBackend([])
    client.app.dependency_overrides[api_routes.get_search_backend] = lambda: fake
    try:
        resp = client.post("/v1/email/search", json={})
    finally:
        client.app.dependency_overrides.pop(api_routes.get_search_backend, None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 0
    assert body["messages"] == []
    # No query/labels → the route forces INBOX so the default lists the inbox.
    # (Live Gmail with no labelIds returns ALL mail — the route must not rely on
    # the fake's INBOX default, which would mask that divergence.)
    assert fake.calls == [
        {"query": None, "label_ids": ["INBOX"], "max_results": 25, "page_token": None}
    ]


def test_search_with_query_is_not_inbox_scoped(client):
    # A query searches ALL mail (Gmail search semantics / agent parity) — the
    # route must NOT silently inject an INBOX label when a query is present.
    fake = _FakeSearchBackend([])
    client.app.dependency_overrides[api_routes.get_search_backend] = lambda: fake
    try:
        resp = client.post("/v1/email/search", json={"query": "from:alice"})
    finally:
        client.app.dependency_overrides.pop(api_routes.get_search_backend, None)
    assert resp.status_code == 200, resp.text
    assert fake.calls == [
        {
            "query": "from:alice",
            "label_ids": None,
            "max_results": 25,
            "page_token": None,
        }
    ]


def test_search_forwards_labels_and_caps_max_results(client):
    fake = _FakeSearchBackend([])
    client.app.dependency_overrides[api_routes.get_search_backend] = lambda: fake
    try:
        resp = client.post(
            "/v1/email/search",
            json={"labels": ["STARRED"], "max_results": 5},
        )
    finally:
        client.app.dependency_overrides.pop(api_routes.get_search_backend, None)
    assert resp.status_code == 200, resp.text
    assert fake.calls == [
        {
            "query": None,
            "label_ids": ["STARRED"],
            "max_results": 5,
            "page_token": None,
        }
    ]


def test_search_forwards_page_token_for_pagination(client):
    # The next_page_token a response returns must be usable as the next
    # request's page_token — otherwise pagination is a dead-end.
    fake = _FakeSearchBackend([])
    client.app.dependency_overrides[api_routes.get_search_backend] = lambda: fake
    try:
        resp = client.post(
            "/v1/email/search",
            json={"query": "is:unread", "page_token": "next-tok"},
        )
    finally:
        client.app.dependency_overrides.pop(api_routes.get_search_backend, None)
    assert resp.status_code == 200, resp.text
    assert fake.calls == [
        {
            "query": "is:unread",
            "label_ids": None,
            "max_results": 25,
            "page_token": "next-tok",
        }
    ]


def test_search_rejects_unknown_field_loudly(client):
    # _Strict contract: an unknown field is a 422, never silently dropped.
    fake = _FakeSearchBackend([])
    client.app.dependency_overrides[api_routes.get_search_backend] = lambda: fake
    try:
        resp = client.post("/v1/email/search", json={"q": "oops"})
    finally:
        client.app.dependency_overrides.pop(api_routes.get_search_backend, None)
    assert resp.status_code == 422


def test_search_rejects_out_of_range_max_results(client):
    fake = _FakeSearchBackend([])
    client.app.dependency_overrides[api_routes.get_search_backend] = lambda: fake
    try:
        resp = client.post("/v1/email/search", json={"max_results": 0})
        resp_hi = client.post("/v1/email/search", json={"max_results": 101})
    finally:
        client.app.dependency_overrides.pop(api_routes.get_search_backend, None)
    assert resp.status_code == 422
    assert resp_hi.status_code == 422


def test_get_search_backend_no_mailbox_fails_loud_503(monkeypatch):
    monkeypatch.setattr(api_routes, "connected_mailbox_providers", lambda: [])
    with pytest.raises(HTTPException) as ei:
        api_routes.get_search_backend()
    assert ei.value.status_code == 503


def test_get_search_backend_ambiguous_fails_loud_400(monkeypatch):
    monkeypatch.setattr(
        api_routes, "connected_mailbox_providers", lambda: ["google", "microsoft"]
    )
    with pytest.raises(HTTPException) as ei:
        api_routes.get_search_backend()
    assert ei.value.status_code == 400


# ---------------------------------------------------------------------------
# 7. Mailbox actions — archive / quarantine (schema 2.1, #1779)
#
# The confirmation-token gate is the safety property under test: a destructive
# action without a valid, action-bound token must be rejected with 403 BEFORE
# any backend call. The happy paths use an in-memory fake Gmail backend +
# real in-memory action log (injected via the module-level resolvers the
# handlers call — the same seam ``send`` uses), so no live mailbox is touched.
# ---------------------------------------------------------------------------


class _FakeMailbox:
    """Minimal in-memory, label-based (Gmail-style) backend for the action impls.

    Tracks a label set per message id; archive removes INBOX (stable id, like
    Gmail), quarantine adds the quarantine label and archives. Enough surface
    for ``archive_message_impl`` / ``undo_archive_batch_impl`` /
    ``quarantine_phishing_impl`` / ``unquarantine_impl`` to round-trip.
    """

    def __init__(self):
        self.messages = {
            "m1": {"labelIds": ["INBOX", "UNREAD"], "threadId": "t1"},
            "m2": {"labelIds": ["INBOX"], "threadId": "t2"},
        }
        self.labels = [{"id": "INBOX", "name": "INBOX"}]
        self._next_label = 1

    def get_message(self, message_id):
        msg = self.messages[message_id]
        return {"labelIds": list(msg["labelIds"]), "threadId": msg["threadId"]}

    def archive_message(self, message_id):
        labels = self.messages[message_id]["labelIds"]
        if "INBOX" in labels:
            labels.remove("INBOX")
        return {"id": message_id}

    def unarchive_message(self, message_id, prior_labels):
        labels = self.messages[message_id]["labelIds"]
        for lab in prior_labels or ["INBOX"]:
            if lab not in labels:
                labels.append(lab)
        return {"id": message_id}

    def list_labels(self):
        return list(self.labels)

    def create_label(self, *, name):
        self._next_label += 1
        entry = {"id": f"Label_{self._next_label}", "name": name}
        self.labels.append(entry)
        return entry

    def add_label(self, message_id, label_id):
        labels = self.messages[message_id]["labelIds"]
        if label_id not in labels:
            labels.append(label_id)
        return {"id": message_id}

    def remove_label(self, message_id, label_id):
        labels = self.messages[message_id]["labelIds"]
        if label_id in labels:
            labels.remove(label_id)
        return {"id": message_id}


@pytest.fixture
def action_env(monkeypatch):
    """Wire the action handlers to a fake backend + in-memory action log.

    Returns ``(client, mailbox)`` — the mailbox is inspectable so a test can
    assert inbox membership after archive/undo.
    """
    from gaia_agent_email import action_store
    from gaia_agent_email import api_routes as email_routes

    from gaia.database.mixin import DatabaseMixin

    class _DB(DatabaseMixin):
        pass

    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)

    mailbox = _FakeMailbox()
    monkeypatch.setattr(email_routes, "resolve_action_db", lambda: db)
    monkeypatch.setattr(
        email_routes, "_resolve_mutate_backend", lambda provider: (mailbox, "google")
    )
    monkeypatch.setattr(
        email_routes, "_resolve_backend_for_provider", lambda provider: mailbox
    )
    client = TestClient(export_openapi.build_app())
    return client, mailbox


def test_archive_without_token_is_rejected(client):
    # The gate fires before any backend resolution — no fake needed.
    resp = client.post("/v1/email/archive", json={"message_id": "m1"})
    assert resp.status_code == 403
    assert "confirmation token" in resp.json()["detail"].lower()


def test_quarantine_without_token_is_rejected(client):
    resp = client.post(
        "/v1/email/quarantine", json={"message_id": "m1", "is_phishing": True}
    )
    assert resp.status_code == 403
    assert "confirmation token" in resp.json()["detail"].lower()


def test_confirm_then_archive_round_trips(action_env):
    client, mailbox = action_env
    tok = client.post(
        "/v1/email/confirm", json={"action": "archive", "message_id": "m1"}
    ).json()["confirmation_token"]

    resp = client.post(
        "/v1/email/archive",
        json={"message_id": "m1", "confirmation_token": tok},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["archived"] is True
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["post_archive_id"] == "m1"  # label-based backend keeps the id
    assert body["undo_window_seconds"] == 30
    assert "INBOX" not in mailbox.messages["m1"]["labelIds"]

    # The same token cannot be replayed (single-use).
    replay = client.post(
        "/v1/email/archive", json={"message_id": "m1", "confirmation_token": tok}
    )
    assert replay.status_code == 403

    # Undo (ungated) restores it to the inbox via the batch handle.
    undo = client.post("/v1/email/unarchive", json={"batch_id": body["batch_id"]})
    assert undo.status_code == 200, undo.text
    assert undo.json()["restored"] == 1
    assert "INBOX" in mailbox.messages["m1"]["labelIds"]


def test_archive_token_cannot_authorize_quarantine(action_env):
    # A token minted for archive must not authorize quarantine of the same id.
    client, _ = action_env
    tok = client.post(
        "/v1/email/confirm", json={"action": "archive", "message_id": "m1"}
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/quarantine",
        json={"message_id": "m1", "is_phishing": True, "confirmation_token": tok},
    )
    assert resp.status_code == 403


def test_confirm_then_quarantine_round_trips(action_env):
    client, mailbox = action_env
    tok = client.post(
        "/v1/email/confirm", json={"action": "quarantine", "message_id": "m2"}
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/quarantine",
        json={"message_id": "m2", "is_phishing": True, "confirmation_token": tok},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["quarantined"] is True
    qlabel = body["quarantine_label_id"]
    assert qlabel in mailbox.messages["m2"]["labelIds"]
    assert "INBOX" not in mailbox.messages["m2"]["labelIds"]

    undo = client.post("/v1/email/unquarantine", json={"action_id": body["action_id"]})
    assert undo.status_code == 200, undo.text
    assert undo.json()["message_id"] == "m2"
    assert qlabel not in mailbox.messages["m2"]["labelIds"]
    assert "INBOX" in mailbox.messages["m2"]["labelIds"]


def test_quarantine_refuses_non_phishing(action_env):
    client, _ = action_env
    tok = client.post(
        "/v1/email/confirm", json={"action": "quarantine", "message_id": "m2"}
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/quarantine",
        json={"message_id": "m2", "is_phishing": False, "confirmation_token": tok},
    )
    assert resp.status_code == 400
    assert "is_phishing" in resp.json()["detail"].lower()


def test_unquarantine_unknown_action_fails_loudly(action_env):
    # Unknown/expired action_id → 409 short-circuit, even before backend resolution.
    client, _ = action_env
    resp = client.post("/v1/email/unquarantine", json={"action_id": "nope"})
    assert resp.status_code == 409
    assert "undo window" in resp.json()["detail"].lower()


def test_unarchive_expired_window_fails_loudly(action_env):
    client, _ = action_env
    resp = client.post("/v1/email/unarchive", json={"batch_id": "does-not-exist"})
    assert resp.status_code == 409
    assert "undo window" in resp.json()["detail"].lower()


def test_action_request_rejects_unknown_field(client):
    # _Strict contract models forbid extras → 422, never silently dropped.
    resp = client.post(
        "/v1/email/confirm",
        json={"action": "archive", "message_id": "m1", "bogus": 1},
    )
    assert resp.status_code == 422


class _FolderMailbox:
    """Outlook-style fake: archive is a folder MOVE that mints a NEW id (#1738).

    The whole point is to prove archive→unarchive undo uses the post-archive id,
    not the stale request id — undo against the original id would 404 here.
    """

    def __init__(self):
        self.inbox = {"m1"}
        self._archived = {}  # new_id -> original_id
        self._n = 0
        self.unarchive_calls = []

    def get_message(self, message_id):
        return {"labelIds": ["INBOX"], "threadId": "t1"}

    def archive_message(self, message_id):
        self._n += 1
        new_id = f"{message_id}-arch{self._n}"
        self.inbox.discard(message_id)
        self._archived[new_id] = message_id
        return {"id": new_id}

    def unarchive_message(self, message_id, prior_labels):
        self.unarchive_calls.append(message_id)
        if message_id not in self._archived:
            # Mirrors the live #1738 failure: a stale (pre-move) id 404s.
            raise RuntimeError(f"unknown id {message_id!r} (folder move changed it)")
        self.inbox.add(self._archived.pop(message_id))
        return {"id": message_id}


def test_archive_undo_uses_post_archive_id_after_folder_move(monkeypatch):
    # #1738: a folder-based backend mints a new id on archive. Undo MUST restore
    # by that post-archive id (surfaced in the response), not the request id.
    from gaia_agent_email import action_store
    from gaia_agent_email import api_routes as email_routes

    from gaia.database.mixin import DatabaseMixin

    class _DB(DatabaseMixin):
        pass

    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)
    mailbox = _FolderMailbox()
    monkeypatch.setattr(email_routes, "resolve_action_db", lambda: db)
    monkeypatch.setattr(
        email_routes, "_resolve_mutate_backend", lambda p: (mailbox, "microsoft")
    )
    monkeypatch.setattr(
        email_routes, "_resolve_backend_for_provider", lambda p: mailbox
    )
    client = TestClient(export_openapi.build_app())

    tok = client.post(
        "/v1/email/confirm", json={"action": "archive", "message_id": "m1"}
    ).json()["confirmation_token"]
    body = client.post(
        "/v1/email/archive", json={"message_id": "m1", "confirmation_token": tok}
    ).json()
    assert body["post_archive_id"] != "m1"  # the move changed the id
    assert "m1" not in mailbox.inbox

    undo = client.post("/v1/email/unarchive", json={"batch_id": body["batch_id"]})
    assert undo.status_code == 200, undo.text
    # Undo restored by the NEW id, not the stale request id — the #1738 fix.
    assert mailbox.unarchive_calls == [body["post_archive_id"]]
    assert "m1" in mailbox.inbox


def test_quarantine_rejected_for_outlook(monkeypatch):
    # Quarantine is Gmail-only: an Outlook mailbox is refused with an actionable
    # 400 rather than performing a folder move its undo cannot reverse.
    from gaia_agent_email import api_routes as email_routes

    monkeypatch.setattr(
        email_routes, "_resolve_mutate_backend", lambda p: (object(), "microsoft")
    )
    monkeypatch.setattr(email_routes, "resolve_action_db", lambda: None)
    client = TestClient(export_openapi.build_app())

    tok = client.post(
        "/v1/email/confirm", json={"action": "quarantine", "message_id": "m1"}
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/quarantine",
        json={"message_id": "m1", "is_phishing": True, "confirmation_token": tok},
    )
    assert resp.status_code == 400
    assert "gmail" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 8. Calendar surface (#1780) — view / preview / create (gated) / respond
# ---------------------------------------------------------------------------


class _FakeCalendarBackend:
    """In-memory calendar backend matching the ``CalendarBackend`` Protocol.

    Records calls so a test can assert the create gate fired (or didn't) without
    touching a live calendar. Injected via ``resolve_calendar_backend``.
    """

    def __init__(self) -> None:
        self.created: list = []
        self.rsvps: list = []

    def list_events(
        self, *, calendar_id="primary", time_min=None, time_max=None, max_results=25
    ):
        return {
            "items": [
                {
                    "id": "evt-1",
                    "summary": "Standup",
                    "start": {"dateTime": "2026-07-01T09:00:00Z"},
                    "end": {"dateTime": "2026-07-01T09:15:00Z"},
                    "location": "Zoom",
                    "organizer": {"email": "lead@example.com"},
                }
            ]
        }

    def create_event(
        self,
        *,
        calendar_id="primary",
        summary,
        start,
        end,
        attendees=None,
        location=None,
        description=None,
    ):
        self.created.append(
            {"summary": summary, "start": start, "end": end, "attendees": attendees}
        )
        return {"id": "evt-created-1", "summary": summary}

    def update_event_rsvp(
        self, *, calendar_id="primary", event_id, attendee_email, response_status
    ):
        self.rsvps.append((event_id, attendee_email, response_status))
        return {"id": event_id, "responseStatus": response_status}


@pytest.fixture
def fake_calendar(client, monkeypatch) -> _FakeCalendarBackend:
    """Inject an in-memory calendar backend so calendar routes never hit a live
    account. Patches the module-level ``resolve_calendar_backend`` indirection."""
    from gaia_agent_email import api_routes as email_routes

    backend = _FakeCalendarBackend()
    monkeypatch.setattr(email_routes, "resolve_calendar_backend", lambda: backend)
    return backend


def _event_payload(**overrides) -> dict:
    payload = {
        "summary": "Project sync",
        "start": {"date_time": "2026-07-01T14:00:00Z"},
        "end": {"date_time": "2026-07-01T15:00:00Z"},
        "attendees": ["alice@example.com"],
    }
    payload.update(overrides)
    return payload


def test_calendar_view_returns_events(client, fake_calendar):
    resp = client.get("/v1/email/calendar/events")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["events"][0]["id"] == "evt-1"
    assert body["events"][0]["organizer"] == "lead@example.com"


def test_calendar_create_without_token_is_403(client, fake_calendar):
    """The mutation gate fires FIRST: no confirmation token → 403, no create."""
    resp = client.post("/v1/email/calendar/events", json=_event_payload())
    assert resp.status_code == 403
    detail = resp.json()["detail"].lower()
    assert "confirmation_token" in detail or "preview" in detail
    assert fake_calendar.created == []  # gate preempted the backend


def test_calendar_create_with_invalid_token_is_403(client, fake_calendar):
    resp = client.post(
        "/v1/email/calendar/events",
        json=_event_payload(confirmation_token="not-a-real-token"),
    )
    assert resp.status_code == 403
    assert fake_calendar.created == []


def test_calendar_preview_then_create_succeeds(client, fake_calendar):
    """Golden path: preview mints a payload-bound token; echoing it creates."""
    preview = client.post("/v1/email/calendar/events/preview", json=_event_payload())
    assert preview.status_code == 200
    token = preview.json()["confirmation_token"]
    assert token

    created = client.post(
        "/v1/email/calendar/events",
        json=_event_payload(confirmation_token=token),
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["event_id"] == "evt-created-1"
    assert body["created"] is True
    assert len(fake_calendar.created) == 1

    # Single-use: replaying the same token is rejected.
    replay = client.post(
        "/v1/email/calendar/events",
        json=_event_payload(confirmation_token=token),
    )
    assert replay.status_code == 403


def test_calendar_create_token_is_payload_bound(client, fake_calendar):
    """A token minted for one event cannot authorize a different event."""
    preview = client.post("/v1/email/calendar/events/preview", json=_event_payload())
    token = preview.json()["confirmation_token"]
    # Same token, different summary → fingerprint mismatch → rejected.
    resp = client.post(
        "/v1/email/calendar/events",
        json=_event_payload(
            summary="A totally different meeting", confirmation_token=token
        ),
    )
    assert resp.status_code == 403
    assert fake_calendar.created == []


def test_calendar_respond_records_rsvp(client, fake_calendar):
    resp = client.post(
        "/v1/email/calendar/events/respond",
        json={
            "event_id": "evt-1",
            "status": "accepted",
            "attendee_email": "me@example.com",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["responded"] is True
    assert fake_calendar.rsvps == [("evt-1", "me@example.com", "accepted")]


def test_calendar_create_rejects_all_day_without_time_loudly(client):
    """A start/end with neither date_time nor date is a 422 (contract validation)."""
    bad = _event_payload(start={}, end={})
    resp = client.post("/v1/email/calendar/events/preview", json=bad)
    assert resp.status_code == 422


def test_calendar_view_missing_scope_fails_loud_with_reconnect_cta(client, monkeypatch):
    """AC2 (#1780/#1770): a missing calendar.events scope → 403 + reconnect CTA.

    The token resolver raises ``ScopeMismatchError`` (a ``ConnectorsError``) when
    the connected account never granted the calendar scope; the route must map it
    to HTTP 403 and surface the actionable reconnect message, not a silent empty
    calendar or an opaque 500.
    """
    from gaia_agent_email import api_routes as email_routes

    from gaia.connectors.errors import ScopeMismatchError

    class _ScopeDeniedCalendar:
        def list_events(
            self, *, calendar_id="primary", time_min=None, time_max=None, max_results=25
        ):
            raise ScopeMismatchError(
                required=["https://www.googleapis.com/auth/calendar.events"],
                granted=[],
                provider="google",
            )

    monkeypatch.setattr(
        email_routes, "resolve_calendar_backend", lambda: _ScopeDeniedCalendar()
    )
    resp = client.get("/v1/email/calendar/events")
    assert resp.status_code == 403
    detail = resp.json()["detail"].lower()
    assert "scope" in detail and "reconnect" in detail


def test_calendar_create_config_error_is_503(client, monkeypatch):
    """A ConfigurationError from the backend maps to 503 (after the gate passes)."""
    from gaia_agent_email import api_routes as email_routes

    from gaia.connectors.errors import ConfigurationError

    class _MisconfiguredCalendar:
        def create_event(
            self,
            *,
            calendar_id="primary",
            summary,
            start,
            end,
            attendees=None,
            location=None,
            description=None,
        ):
            raise ConfigurationError("calendar connector is not configured")

    monkeypatch.setattr(
        email_routes, "resolve_calendar_backend", lambda: _MisconfiguredCalendar()
    )
    token = client.post(
        "/v1/email/calendar/events/preview", json=_event_payload()
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/calendar/events",
        json=_event_payload(confirmation_token=token),
    )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 9. Inbox pre-scan (#1778) — fake backend via app.dependency_overrides;
#    no live mailbox, no LLM (the heuristic path classifies these messages).
# ---------------------------------------------------------------------------


def _prescan_gmail_message(
    msg_id: str,
    *,
    subject: str,
    sender: str,
    label_ids: list[str],
    snippet: str = "",
) -> dict:
    """Build a minimal Gmail-API-v1-shaped message the pre-scan path reads."""
    return {
        "id": msg_id,
        "threadId": f"t-{msg_id}",
        "labelIds": label_ids,
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "mimeType": "text/plain",
            "body": {"data": ""},
        },
    }


class _FakePreScanBackend:
    """In-memory backend exposing just the read calls pre_scan_inbox_impl uses."""

    def __init__(self, messages: list[dict]):
        self._messages = {m["id"]: m for m in messages}

    def list_messages(self, *, label_ids=None, max_results=25, **_):  # noqa: ANN001
        ids = list(self._messages)[:max_results]
        return {
            "messages": [
                {"id": i, "threadId": self._messages[i]["threadId"]} for i in ids
            ],
            "nextPageToken": None,
        }

    def get_message(self, message_id: str) -> dict:
        return self._messages[message_id]


@pytest.fixture
def prescan_client() -> TestClient:
    """A client whose pre-scan backend is a fake (a promotional message that
    the heuristic confidently buckets as a suggested archive, plus a plain
    informational message)."""
    from gaia_agent_email.api_routes import get_prescan_backend

    app = export_openapi.build_app()
    backend = _FakePreScanBackend(
        [
            _prescan_gmail_message(
                "m1",
                subject="50% off this weekend!",
                sender="deals@shop.example",
                label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
            ),
            _prescan_gmail_message(
                "m2",
                subject="Project sync notes",
                sender="alice@corp.example",
                label_ids=["INBOX"],
                snippet="Sharing the notes from today's sync.",
            ),
        ]
    )
    app.dependency_overrides[get_prescan_backend] = lambda: backend
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_prescan_returns_card_envelope_shape(prescan_client):
    resp = prescan_client.post("/v1/email/prescan", json={"max_messages": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schema_version"] == SCHEMA_VERSION
    result = body["result"]
    # The envelope is exactly what EmailPreScanCard consumes.
    assert result["kind"] == "email_pre_scan"
    assert set(result) == {
        "kind",
        "urgent",
        "actionable",
        "informational_count",
        "suggested_archives",
        "suggested_drafts",
        "preferences_applied",
        "totals",
    }
    for section in ("urgent", "actionable", "suggested_archives"):
        assert isinstance(result[section], list)
    assert isinstance(result["informational_count"], int)
    assert result["suggested_drafts"] == []
    # The promotional message is surfaced as a suggested archive with a reason;
    # the plain message lands in the informational count (not listed).
    archives = result["suggested_archives"]
    assert any(item["message_id"] == "m1" for item in archives)
    archived = next(item for item in archives if item["message_id"] == "m1")
    assert archived["reason"]  # heuristic rationale present
    assert archived["thread_id"] == "t-m1"
    assert result["totals"]["suggested_archives"] >= 1


def test_prescan_rejects_unknown_request_field_loudly(prescan_client):
    # _Strict request model forbids extras → 422, never silently ignored.
    resp = prescan_client.post(
        "/v1/email/prescan", json={"max_messages": 5, "bogus": True}
    )
    assert resp.status_code == 422


def test_prescan_no_mailbox_connected_fails_loud(monkeypatch):
    # The real resolver must fail loud (503) when no mailbox is connected —
    # never a silent empty pre-scan.
    from fastapi import HTTPException
    from gaia_agent_email.api_routes import get_prescan_backend

    monkeypatch.setattr(
        "gaia_agent_email.api_routes.connected_mailbox_providers", lambda: []
    )
    with pytest.raises(HTTPException) as exc:
        get_prescan_backend()
    assert exc.value.status_code == 503


def test_prescan_ambiguous_mailbox_fails_loud(monkeypatch):
    # Two connected mailboxes → 400, never a silent guess of which to scan.
    from fastapi import HTTPException
    from gaia_agent_email.api_routes import get_prescan_backend

    monkeypatch.setattr(
        "gaia_agent_email.api_routes.connected_mailbox_providers",
        lambda: ["google", "microsoft"],
    )
    with pytest.raises(HTTPException) as exc:
        get_prescan_backend()
    assert exc.value.status_code == 400
