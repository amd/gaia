# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OpenAPI conformance tests for the Email Triage agent REST surface (#1645).

These tests drive a RUNNING in-process ASGI server (FastAPI TestClient) against
the committed ``openapi.email.json`` spec and validate that every documented
path+method returns a response whose shape matches the spec's declared response
schema.  No live mailbox, no live LLM — the LLM-backed triage path is covered by
patching ``EmailTriageService.triage_request`` to return a valid canned result.

This is distinct from the static-analysis tests in
``hub/agents/email/python/tests/test_rest_contract.py``:

- ``test_rest_contract.py`` checks that the committed artifact matches a freshly
  generated spec (drift detection) and that ``version.py`` constants agree with
  ``contract.py`` constants.
- **This file** checks that the running server actually conforms to the spec it
  declares — i.e. that each endpoint returns a body whose keys match the
  documented response schema's required fields.  A route that is documented but
  broken (bad implementation) fails here, not in the drift test.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip the whole module cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

import jsonschema  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import api_routes  # noqa: E402
from gaia_agent_email.contract import (  # noqa: E402
    BatchTriageResponse,
    EmailCategory,
    EmailTriageResponse,
    EmailTriageResult,
)
from gaia_agent_email.export_openapi import ARTIFACT_PATH, build_app  # noqa: E402
from gaia_agent_email.version import AGENT_VERSION, API_VERSION  # noqa: E402
from referencing import Registry, Resource  # noqa: E402
from referencing.jsonschema import DRAFT202012  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema_name_from_response(spec: dict, method: str, path: str) -> str:
    """Extract the component schema name from a route's 200 response $ref."""
    schema = spec["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    ref = schema["$ref"]
    return ref.rsplit("/", 1)[-1]


def _required_keys(spec: dict, schema_name: str) -> set:
    """Return the ``required`` field-names for a top-level component schema."""
    schema = spec["components"]["schemas"][schema_name]
    return set(schema.get("required", []))


_SPEC_URI = "urn:gaia-email-openapi"


def _assert_body_conforms(spec: dict, schema_name: str, body: dict) -> None:
    """Validate ``body`` against ``components.schemas.<schema_name>`` with a
    real JSON-Schema validator.

    Key-presence checks (the ``_required_keys`` loop) prove a field exists but
    not that its TYPE or enum value is right — ``{"count": "five"}`` passes
    them. This helper runs jsonschema with a registry seeded from the WHOLE
    committed spec so nested ``$ref``/``anyOf`` (e.g.
    ``category -> #/components/schemas/EmailCategory``) resolve — never an
    extracted sub-schema, which would break those refs.
    """
    registry = Registry().with_resource(
        uri=_SPEC_URI,
        resource=Resource(contents=spec, specification=DRAFT202012),
    )
    validator = jsonschema.Draft202012Validator(
        schema={"$ref": f"{_SPEC_URI}#/components/schemas/{schema_name}"},
        registry=registry,
    )
    validator.validate(body)


def _derive_error_codes_by_route() -> dict:
    """Derive each route's raisable HTTPException codes from api_routes.py.

    Source-derived on purpose (#1897): a hand-maintained code map would
    reintroduce exactly the spec/handler drift these tests exist to close.
    AST-walks every function for ``HTTPException(status_code=<const>)``, then
    propagates codes through the module-level call graph (helpers like
    ``_resolve_mutate_backend`` raise on behalf of many handlers) and through
    FastAPI ``Depends()`` targets (their raises surface on the route too).
    """
    tree = ast.parse(Path(inspect.getfile(api_routes)).read_text(encoding="utf-8"))

    def _call_name(node: ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    direct: dict = {}
    calls: dict = {}
    routes: dict = {}  # handler name -> (method, decorator path)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        codes, called = set(), set()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            name = _call_name(sub)
            if name == "HTTPException":
                for kw in sub.keywords:
                    if kw.arg == "status_code" and isinstance(kw.value, ast.Constant):
                        codes.add(kw.value.value)
            elif name == "Depends":
                if sub.args and isinstance(sub.args[0], ast.Name):
                    called.add(sub.args[0].id)
            elif name:
                called.add(name)
        direct[node.name] = codes
        calls[node.name] = called
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in ("get", "post")
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
            ):
                hidden = any(
                    kw.arg == "include_in_schema"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                    for kw in dec.keywords
                )
                if not hidden:
                    routes[node.name] = (dec.func.attr, dec.args[0].value)

    # Fixpoint: a handler can raise everything its (transitive) callees raise.
    effective = {name: set(codes) for name, codes in direct.items()}
    changed = True
    while changed:
        changed = False
        for name in effective:
            for callee in calls[name]:
                if callee in effective and not effective[callee] <= effective[name]:
                    effective[name] |= effective[callee]
                    changed = True

    return {
        (method, path): effective[handler] for handler, (method, path) in routes.items()
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def committed_spec() -> dict:
    """The committed ``openapi.email.json`` (the cross-implementation contract)."""
    return json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client() -> TestClient:
    """In-process ASGI test client — a real running ASGI server, no network."""
    return TestClient(build_app())


def _canned_triage_response() -> EmailTriageResponse:
    """Return a valid schema-2.0 EmailTriageResponse for LLM mock use."""
    result = EmailTriageResult(
        category=EmailCategory.NEEDS_RESPONSE,
        is_spam=False,
        is_phishing=False,
        summary="Alice is asking Bob to join her for lunch tomorrow.",
        action_items=[],
        draft=None,
        message_id="msg-conformance-001",
    )
    return EmailTriageResponse(request_kind="single", result=result)


def _minimal_triage_payload() -> dict:
    """Minimal valid EmailTriageRequest body for POST /triage."""
    return {
        "schema_version": "2.0",
        "payload": {
            "kind": "single",
            "principal": {"email": "bob@example.com"},
            "message": {
                "message_id": "msg-conformance-001",
                "subject": "Lunch tomorrow?",
                "from": {"email": "alice@example.com"},
                "body": "Hey, join us for lunch tomorrow at noon?",
            },
        },
    }


# ---------------------------------------------------------------------------
# 1. /health — conformance (running server, no LLM)
# ---------------------------------------------------------------------------


def test_health_conforms_to_spec(client, committed_spec):
    """GET /health returns a body with all required fields from the spec."""
    resp = client.get("/v1/email/health")
    assert resp.status_code == 200

    schema_name = _schema_name_from_response(committed_spec, "get", "/v1/email/health")
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()

    for key in required:
        assert key in body, f"required key {key!r} missing from /health response"
    _assert_body_conforms(committed_spec, schema_name, body)

    assert body["status"] == "ok"
    assert body["service"] == "gaia-agent-email"


# ---------------------------------------------------------------------------
# 2. /version — conformance (running server, no LLM)
# ---------------------------------------------------------------------------


def test_version_conforms_to_spec(client, committed_spec):
    """GET /version returns apiVersion and agentVersion matching the constants."""
    resp = client.get("/v1/email/version")
    assert resp.status_code == 200

    schema_name = _schema_name_from_response(committed_spec, "get", "/v1/email/version")
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()

    for key in required:
        assert key in body, f"required key {key!r} missing from /version response"
    _assert_body_conforms(committed_spec, schema_name, body)

    assert body["apiVersion"] == API_VERSION, (
        f"apiVersion mismatch: server reports {body['apiVersion']!r}, "
        f"version.API_VERSION is {API_VERSION!r}"
    )
    assert body["agentVersion"] == AGENT_VERSION, (
        f"agentVersion mismatch: server reports {body['agentVersion']!r}, "
        f"version.AGENT_VERSION is {AGENT_VERSION!r}"
    )


def test_version_has_no_undocumented_fields(client, committed_spec):
    """GET /version response must not include fields absent from the spec schema."""
    resp = client.get("/v1/email/version")
    assert resp.status_code == 200
    body = resp.json()

    schema_name = _schema_name_from_response(committed_spec, "get", "/v1/email/version")
    documented_keys = set(
        committed_spec["components"]["schemas"][schema_name].get("properties", {})
    )
    for key in body:
        assert (
            key in documented_keys
        ), f"response field {key!r} is not documented in the spec schema"


# ---------------------------------------------------------------------------
# 2b. /init — readiness conformance (#1795). Probes patched so no live LLM.
# ---------------------------------------------------------------------------


def test_init_conforms_to_spec_when_not_ready(client, committed_spec):
    """GET /init returns 503 + an InitResponse-shaped body when Lemonade is down.

    The running server returns the SAME structured model under 503 as under 200,
    so the body must carry every required key from the documented schema.
    """
    with patch(
        "gaia_agent_email.api_routes._probe_lemonade_health",
        return_value=(False, "http://localhost:8000/api/v1", None, []),
    ):
        resp = client.get("/v1/email/init")

    assert resp.status_code == 503
    schema_name = _schema_name_from_response(committed_spec, "get", "/v1/email/init")
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()
    for key in required:
        assert key in body, f"required key {key!r} missing from /init response"
    _assert_body_conforms(committed_spec, schema_name, body)
    assert body["ready"] is False
    assert body["hint"]  # actionable, non-empty


def test_init_conforms_to_spec_when_ready(client, committed_spec):
    """GET /init returns 200 + ready=True when both probes pass."""
    with (
        patch(
            "gaia_agent_email.api_routes._probe_lemonade_health",
            return_value=(True, "http://localhost:8000/api/v1", "10.2.0", []),
        ),
        patch(
            "gaia_agent_email.api_routes._probe_model_present",
            return_value=True,
        ),
    ):
        resp = client.get("/v1/email/init")

    assert resp.status_code == 200
    body = resp.json()
    _assert_body_conforms(committed_spec, "InitResponse", body)
    assert body["ready"] is True
    # No undocumented fields leak from the running server.
    documented = set(
        committed_spec["components"]["schemas"]["InitResponse"].get("properties", {})
    )
    for key in body:
        assert key in documented, f"undocumented field {key!r} in /init response"


# ---------------------------------------------------------------------------
# 3. /triage — conformance (LLM mocked; real HTTP layer running)
# ---------------------------------------------------------------------------


def test_triage_conforms_to_spec(client, committed_spec):
    """POST /triage with a mocked LLM returns a body conforming to the spec schema."""
    canned = _canned_triage_response()

    with patch(
        "gaia_agent_email.api_routes.EmailTriageService.triage_request",
        return_value=canned,
    ):
        resp = client.post("/v1/email/triage", json=_minimal_triage_payload())

    assert resp.status_code == 200

    schema_name = _schema_name_from_response(committed_spec, "post", "/v1/email/triage")
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()

    for key in required:
        assert key in body, f"required key {key!r} missing from /triage response"
    _assert_body_conforms(committed_spec, schema_name, body)

    assert body.get("schema_version") == API_VERSION
    assert body.get("request_kind") == "single"
    assert "result" in body
    result = body["result"]
    assert "category" in result
    assert "summary" in result


def test_triage_invalid_payload_returns_422(client):
    """POST /triage with a malformed body returns 422 (documented validation error)."""
    resp = client.post("/v1/email/triage", json={"schema_version": "2.0"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 3.5 /search — conformance (mailbox backend injected; no live mail)
# ---------------------------------------------------------------------------


def _gmail_search_message() -> dict:
    """A minimal Gmail-API-v1-shaped message the search route can hydrate."""
    import base64

    data = base64.urlsafe_b64encode(b"Body the search list drops.").decode().rstrip("=")
    return {
        "id": "s1",
        "threadId": "t-s1",
        "snippet": "please review",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Prod incident"},
                {"name": "From", "value": "Sarah Chen <sarah@example.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Date", "value": "Mon, 01 Jan 2026 10:00:00 +0000"},
            ],
            "body": {"data": data},
        },
    }


class _FakeSearchBackend:
    """Inject-only fake exposing the two read methods the search route uses."""

    def __init__(self, messages):
        self._messages = {m["id"]: m for m in messages}

    def list_messages(
        self, *, query=None, label_ids=None, max_results=25, page_token=None
    ):
        ids = list(self._messages)[:max_results]
        return {
            "messages": [
                {"id": i, "threadId": self._messages[i]["threadId"]} for i in ids
            ],
            "nextPageToken": None,
        }

    def get_message(self, message_id):
        return self._messages[message_id]


def test_search_conforms_to_spec(client, committed_spec):
    """POST /search returns a body conforming to the EmailSearchResponse schema.

    The mailbox backend is injected via ``app.dependency_overrides`` so no live
    mail is touched — the running server still exercises the real route.
    """
    from gaia_agent_email.api_routes import get_search_backend

    fake = _FakeSearchBackend([_gmail_search_message()])
    client.app.dependency_overrides[get_search_backend] = lambda: fake
    try:
        resp = client.post(
            "/v1/email/search", json={"query": "is:unread", "max_results": 10}
        )
    finally:
        client.app.dependency_overrides.pop(get_search_backend, None)

    assert resp.status_code == 200, resp.text

    schema_name = _schema_name_from_response(committed_spec, "post", "/v1/email/search")
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()

    for key in required:
        assert key in body, f"required key {key!r} missing from /search response"
    _assert_body_conforms(committed_spec, schema_name, body)

    assert body["schema_version"] == API_VERSION
    assert body["count"] == 1
    item = body["messages"][0]
    assert item["id"] == "s1"
    # Wire alias: the sender is `from`, not `from_`.
    assert item["from"] == "Sarah Chen <sarah@example.com>"
    assert item["label_ids"] == ["INBOX", "UNREAD"]


def test_search_invalid_payload_returns_422(client):
    """POST /search with an unknown field returns 422 (strict contract)."""
    from gaia_agent_email.api_routes import get_search_backend

    fake = _FakeSearchBackend([])
    client.app.dependency_overrides[get_search_backend] = lambda: fake
    try:
        resp = client.post("/v1/email/search", json={"q": "oops"})
    finally:
        client.app.dependency_overrides.pop(get_search_backend, None)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 4. /draft — conformance (no LLM needed; draft only mints a token)
# ---------------------------------------------------------------------------


def test_draft_conforms_to_spec(client, committed_spec):
    """POST /draft returns a body conforming to the EmailDraftResponse spec schema."""
    resp = client.post(
        "/v1/email/draft",
        json={
            "to": [{"email": "carol@example.com"}],
            "subject": "Following up",
            "body": "Just checking in — any update?",
        },
    )
    assert resp.status_code == 200

    schema_name = _schema_name_from_response(committed_spec, "post", "/v1/email/draft")
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()

    for key in required:
        assert key in body, f"required key {key!r} missing from /draft response"
    _assert_body_conforms(committed_spec, schema_name, body)

    assert "draft" in body
    assert "confirmation_token" in body
    assert isinstance(body["confirmation_token"], str)
    assert len(body["confirmation_token"]) > 0


def test_draft_invalid_payload_returns_422(client):
    """POST /draft with an empty 'to' list returns 422."""
    resp = client.post(
        "/v1/email/draft",
        json={"to": [], "subject": "Hi", "body": "Hello"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. /send — conformance (documented error shape for missing token)
# ---------------------------------------------------------------------------


def test_send_without_token_returns_403(client):
    """POST /send without a confirmation token returns 403 (documented behavior).

    The spec documents 200 (EmailSendResponse) as the success path.  A missing
    or invalid token is a documented gating failure — the detail must guide the
    caller to POST /draft.
    """
    resp = client.post(
        "/v1/email/send",
        json={
            "to": [{"email": "dave@example.com"}],
            "subject": "Send me",
            "body": "Without a token.",
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert "detail" in body
    detail_lower = body["detail"].lower()
    assert (
        "confirmation_token" in detail_lower or "draft" in detail_lower
    ), "403 detail should guide the caller to POST /draft to obtain a token"


def test_send_invalid_payload_returns_422(client):
    """POST /send with a missing required field returns 422."""
    resp = client.post("/v1/email/send", json={"subject": "Hi", "body": "Hello"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5b. Calendar surface (#1780) — view / preview / create (gated) / respond
# ---------------------------------------------------------------------------


class _FakeCalendarBackend:
    """In-memory calendar backend (CalendarBackend Protocol) for conformance."""

    def list_events(
        self, *, calendar_id="primary", time_min=None, time_max=None, max_results=25
    ):
        return {
            "items": [
                {
                    "id": "evt-conf-1",
                    "summary": "Conformance sync",
                    "start": {"dateTime": "2026-07-01T10:00:00Z"},
                    "end": {"dateTime": "2026-07-01T10:30:00Z"},
                    "location": None,
                    "organizer": {"email": "host@example.com"},
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
        return {"id": "evt-conf-created", "summary": summary}

    def update_event_rsvp(
        self, *, calendar_id="primary", event_id, attendee_email, response_status
    ):
        return {"id": event_id, "responseStatus": response_status}


@pytest.fixture
def fake_calendar(monkeypatch):
    from gaia_agent_email import api_routes as email_routes

    backend = _FakeCalendarBackend()
    monkeypatch.setattr(email_routes, "resolve_calendar_backend", lambda: backend)
    return backend


def _calendar_event_body(**overrides) -> dict:
    body = {
        "summary": "Conformance meeting",
        "start": {"date_time": "2026-07-01T14:00:00Z"},
        "end": {"date_time": "2026-07-01T15:00:00Z"},
        "attendees": ["alice@example.com"],
    }
    body.update(overrides)
    return body


def test_calendar_view_conforms_to_spec(client, committed_spec, fake_calendar):
    resp = client.get("/v1/email/calendar/events")
    assert resp.status_code == 200
    schema_name = _schema_name_from_response(
        committed_spec, "get", "/v1/email/calendar/events"
    )
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()
    for key in required:
        assert key in body, f"required key {key!r} missing from calendar view response"
    _assert_body_conforms(committed_spec, schema_name, body)
    assert body["events"][0]["id"] == "evt-conf-1"


def test_calendar_create_without_token_returns_403(client, fake_calendar):
    """A create without a confirmation token is a documented 403 (gate)."""
    resp = client.post("/v1/email/calendar/events", json=_calendar_event_body())
    assert resp.status_code == 403
    detail = resp.json()["detail"].lower()
    assert "confirmation_token" in detail or "preview" in detail


def test_calendar_preview_then_create_conforms(client, committed_spec, fake_calendar):
    preview = client.post(
        "/v1/email/calendar/events/preview", json=_calendar_event_body()
    )
    assert preview.status_code == 200
    token = preview.json()["confirmation_token"]

    resp = client.post(
        "/v1/email/calendar/events",
        json=_calendar_event_body(confirmation_token=token),
    )
    assert resp.status_code == 200, resp.text
    schema_name = _schema_name_from_response(
        committed_spec, "post", "/v1/email/calendar/events"
    )
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()
    for key in required:
        assert (
            key in body
        ), f"required key {key!r} missing from calendar create response"
    _assert_body_conforms(committed_spec, schema_name, body)
    assert body["event_id"] == "evt-conf-created"


def test_calendar_respond_conforms_to_spec(client, committed_spec, fake_calendar):
    resp = client.post(
        "/v1/email/calendar/events/respond",
        json={
            "event_id": "evt-conf-1",
            "status": "declined",
            "attendee_email": "me@example.com",
        },
    )
    assert resp.status_code == 200
    schema_name = _schema_name_from_response(
        committed_spec, "post", "/v1/email/calendar/events/respond"
    )
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()
    for key in required:
        assert (
            key in body
        ), f"required key {key!r} missing from calendar respond response"
    _assert_body_conforms(committed_spec, schema_name, body)
    assert body["status"] == "declined"


def test_calendar_create_invalid_payload_returns_422(client):
    """A start with neither date_time nor date is a documented validation error."""
    resp = client.post(
        "/v1/email/calendar/events/preview",
        json=_calendar_event_body(start={}, end={}),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 6. /triage/batch — conformance (LLM mocked; real HTTP layer running, #1887)
# ---------------------------------------------------------------------------


def _minimal_batch_payload() -> dict:
    """Minimal valid BatchTriageRequest body for POST /triage/batch."""
    return {
        "schema_version": "2.0",
        "items": [
            {
                "kind": "single",
                "principal": {"email": "bob@example.com"},
                "message": {
                    "message_id": "msg-batch-001",
                    "subject": "Batch test",
                    "from": {"email": "alice@example.com"},
                    "body": "Can you review this before Monday?",
                },
            }
        ],
    }


def _canned_batch_response() -> BatchTriageResponse:
    """Return a valid BatchTriageResponse for LLM mock use."""
    from gaia_agent_email.contract import BatchItemResult

    result = EmailTriageResult(
        category=EmailCategory.NEEDS_RESPONSE,
        is_spam=False,
        is_phishing=False,
        summary="Review request for Monday.",
        action_items=[],
        draft=None,
    )
    return BatchTriageResponse(results=[BatchItemResult(index=0, result=result)])


def test_triage_batch_conforms_to_spec(client, committed_spec):
    """POST /triage/batch with a mocked LLM returns a body conforming to the spec."""
    canned = _canned_batch_response()

    with patch(
        "gaia_agent_email.api_routes.EmailTriageService.triage_batch",
        return_value=canned,
    ):
        resp = client.post("/v1/email/triage/batch", json=_minimal_batch_payload())

    assert resp.status_code == 200

    schema_name = _schema_name_from_response(
        committed_spec, "post", "/v1/email/triage/batch"
    )
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()

    for key in required:
        assert key in body, f"required key {key!r} missing from /triage/batch response"
    _assert_body_conforms(committed_spec, schema_name, body)

    assert body.get("schema_version") == API_VERSION
    assert "results" in body
    assert len(body["results"]) == 1
    assert body["results"][0]["index"] == 0
    assert "result" in body["results"][0]


def test_triage_batch_invalid_payload_returns_422(client):
    """POST /triage/batch with empty items returns 422."""
    resp = client.post("/v1/email/triage/batch", json={"items": []})
    assert resp.status_code == 422


def test_triage_batch_payload_shape_returns_422(client):
    """POST /triage/batch with the single-email 'payload' shape returns 422."""
    resp = client.post(
        "/v1/email/triage/batch",
        json={
            "payload": {
                "kind": "single",
                "principal": {"email": "alice@example.com"},
                "message": {
                    "message_id": "msg-x",
                    "from": {"email": "bob@example.com"},
                    "subject": "Wrong shape",
                    "body": "This belongs on /triage, not /triage/batch.",
                },
            }
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Mailbox actions — archive / quarantine + reversal (schema 2.1, #1779)
# ---------------------------------------------------------------------------


def test_confirm_conforms_to_spec(client, committed_spec):
    """POST /confirm returns a body conforming to EmailActionConfirmResponse."""
    resp = client.post(
        "/v1/email/confirm", json={"action": "archive", "message_id": "m-conf-1"}
    )
    assert resp.status_code == 200

    schema_name = _schema_name_from_response(
        committed_spec, "post", "/v1/email/confirm"
    )
    required = _required_keys(committed_spec, schema_name)
    body = resp.json()
    for key in required:
        assert key in body, f"required key {key!r} missing from /confirm response"
    _assert_body_conforms(committed_spec, schema_name, body)
    assert body["action"] == "archive"
    assert body["message_id"] == "m-conf-1"
    assert isinstance(body["confirmation_token"], str) and body["confirmation_token"]


def test_archive_without_token_returns_403(client):
    """POST /archive without a confirmation token returns the documented 403 gate."""
    resp = client.post("/v1/email/archive", json={"message_id": "m-1"})
    assert resp.status_code == 403
    detail = resp.json()["detail"].lower()
    assert "confirm" in detail or "token" in detail


def test_quarantine_without_token_returns_403(client):
    """POST /quarantine without a confirmation token returns the documented 403 gate."""
    resp = client.post(
        "/v1/email/quarantine", json={"message_id": "m-1", "is_phishing": True}
    )
    assert resp.status_code == 403
    detail = resp.json()["detail"].lower()
    assert "confirm" in detail or "token" in detail


def test_unarchive_invalid_payload_returns_422(client):
    """POST /unarchive without the required batch_id returns 422 (no DB touched)."""
    resp = client.post("/v1/email/unarchive", json={})
    assert resp.status_code == 422


def test_unquarantine_invalid_payload_returns_422(client):
    """POST /unquarantine without the required action_id returns 422 (no DB touched)."""
    resp = client.post("/v1/email/unquarantine", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Inbox pre-scan (schema 2.1, #1778)
# ---------------------------------------------------------------------------


def test_prescan_without_mailbox_returns_503(client, in_memory_keyring):
    """POST /prescan with no mailbox connected fails loud with the documented 503.

    Pre-scan reads the live inbox, so its backend dependency resolves first and
    rejects an unconnected host with 503 (never a silent empty card).

    Unlike the other endpoints, this test exercises the real
    ``connected_mailbox_providers()`` path (no dependency override), so it needs
    the in-memory keyring — Linux CI runners ship without a system credential
    store, and the real backend would otherwise raise instead of reporting an
    empty (zero-mailbox) state.
    """
    resp = client.post("/v1/email/prescan", json={"max_messages": 5})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Scheduled daily briefing (#1608 additive)
# ---------------------------------------------------------------------------


def test_briefing_conforms_to_spec(client, committed_spec, tmp_path, monkeypatch):
    """GET /briefing returns the documented EmailBriefingResponse shape once a
    scheduled run has persisted a briefing, and a documented 404 before one."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    resp = client.get("/v1/email/briefing")
    assert resp.status_code == 404  # no scheduled run has happened yet

    from gaia_agent_email.briefing import run_briefing_job

    class _FakeBackend:
        def list_messages(self, **_):
            return {
                "messages": [{"id": "m1", "threadId": "t-m1"}],
                "nextPageToken": None,
            }

        def get_message(self, message_id):
            return {
                "id": message_id,
                "threadId": f"t-{message_id}",
                "labelIds": ["INBOX", "CATEGORY_PROMOTIONS"],
                "snippet": "",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "50% off this weekend!"},
                        {"name": "From", "value": "deals@shop.example"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": ""},
                },
            }

    run_briefing_job(_FakeBackend(), max_messages=5)

    resp = client.get("/v1/email/briefing")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    schema_name = _schema_name_from_response(
        committed_spec, "get", "/v1/email/briefing"
    )
    assert schema_name == "EmailBriefingResponse"
    for key in _required_keys(committed_spec, schema_name):
        assert key in body, f"required key {key!r} missing from /briefing response"
    _assert_body_conforms(committed_spec, schema_name, body)
    assert body["schema_version"] == API_VERSION
    assert body["briefing"]["kind"] == "email_pre_scan"


# ---------------------------------------------------------------------------
# 7. All documented paths are covered
# ---------------------------------------------------------------------------


def test_all_documented_paths_covered(committed_spec):
    """Every path+method in the committed spec is covered by a conformance test.

    This test fails if a new route is added to the spec but no conformance test
    is written for it — preventing silent gaps.
    """
    documented = {
        (method, path)
        for path, ops in committed_spec["paths"].items()
        for method in ops
    }
    expected = {
        ("post", "/v1/email/triage"),
        ("post", "/v1/email/triage/batch"),  # #1887 additive
        ("post", "/v1/email/search"),
        ("post", "/v1/email/prescan"),
        ("get", "/v1/email/briefing"),  # #1608 additive
        ("post", "/v1/email/draft"),
        ("post", "/v1/email/send"),
        ("get", "/v1/email/health"),
        ("get", "/v1/email/version"),
        ("get", "/v1/email/init"),
        # Mailbox actions (schema 2.1, #1779).
        ("post", "/v1/email/confirm"),
        ("post", "/v1/email/archive"),
        ("post", "/v1/email/unarchive"),
        ("post", "/v1/email/quarantine"),
        ("post", "/v1/email/unquarantine"),
        # Calendar surface (schema 2.1, #1780).
        ("get", "/v1/email/calendar/events"),
        ("post", "/v1/email/calendar/events"),
        ("post", "/v1/email/calendar/events/preview"),
        ("post", "/v1/email/calendar/events/respond"),
        # SSE agent-loop surface (schema 2.4, #2016). /query streams
        # text/event-stream (not a JSON body schema), so its behavioral
        # conformance lives in hub/agents/email/python/tests/test_query_route.py
        # + test_sse_translation.py rather than the body-schema pattern above;
        # the cancel route is covered there too.
        ("post", "/v1/email/query"),
        ("post", "/v1/email/query/{run_id}/cancel"),
    }
    assert documented == expected, (
        f"Spec has routes not covered by conformance tests: "
        f"{documented - expected}. Add a conformance test for each new route."
    )


# ---------------------------------------------------------------------------
# 8. Error contract — the spec must document every raisable error code (#1897)
# ---------------------------------------------------------------------------


def test_spec_documents_every_raisable_error_code(committed_spec):
    """Every HTTPException status code a handler can raise is documented in
    that route's spec ``responses``.

    The expected set is DERIVED from api_routes.py (AST + call-graph), never
    hand-listed — so adding a new ``raise HTTPException(status_code=...)``
    without documenting it in the route's ``responses=`` fails here.
    """
    derived = _derive_error_codes_by_route()
    assert derived, "AST derivation found no routes — api_routes.py moved?"

    missing = {}
    for (method, route_path), codes in sorted(derived.items()):
        spec_path = f"/v1/email{route_path}"
        assert (
            spec_path in committed_spec["paths"]
        ), f"route {spec_path} not in committed spec"
        documented = set(committed_spec["paths"][spec_path][method]["responses"])
        undocumented = {str(c) for c in codes} - documented
        if undocumented:
            missing[f"{method.upper()} {spec_path}"] = sorted(undocumented)

    assert not missing, (
        "handlers raise error codes the spec does not document — add them to "
        f"the route's responses= and regenerate openapi.email.json: {missing}"
    )


# ---------------------------------------------------------------------------
# 9. jsonschema helper self-test — a wrong-typed body MUST fail validation.
#    Direct calls on hand-built dicts: TestClient/response_model would reject
#    a bad body before the validator ever saw it (false confidence otherwise).
# ---------------------------------------------------------------------------


def test_body_validator_accepts_a_valid_triage_body(committed_spec):
    body = json.loads(_canned_triage_response().model_dump_json(by_alias=True))
    _assert_body_conforms(committed_spec, "EmailTriageResponse", body)


def test_body_validator_rejects_out_of_enum_category(committed_spec):
    body = json.loads(_canned_triage_response().model_dump_json(by_alias=True))
    body["result"]["category"] = "NOT_A_CATEGORY"
    with pytest.raises(jsonschema.ValidationError):
        _assert_body_conforms(committed_spec, "EmailTriageResponse", body)


def test_body_validator_rejects_wrong_typed_field(committed_spec):
    bad = {
        "schema_version": API_VERSION,
        "query": None,
        "count": "five",  # int in the contract — key-presence checks miss this
        "messages": [],
        "next_page_token": None,
    }
    with pytest.raises(jsonschema.ValidationError):
        _assert_body_conforms(committed_spec, "EmailSearchResponse", bad)


def test_body_validator_rejects_missing_required_field(committed_spec):
    # ``count`` is the schema's only required field — omit exactly it.
    required = _required_keys(committed_spec, "EmailSearchResponse")
    assert "count" in required  # guard: the contract still requires it
    with pytest.raises(jsonschema.ValidationError):
        _assert_body_conforms(committed_spec, "EmailSearchResponse", {"messages": []})
