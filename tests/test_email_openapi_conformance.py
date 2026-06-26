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
``hub/agents/python/email/tests/test_rest_contract.py``:

- ``test_rest_contract.py`` checks that the committed artifact matches a freshly
  generated spec (drift detection) and that ``version.py`` constants agree with
  ``contract.py`` constants.
- **This file** checks that the running server actually conforms to the spec it
  declares — i.e. that each endpoint returns a body whose keys match the
  documented response schema's required fields.  A route that is documented but
  broken (bad implementation) fails here, not in the drift test.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip the whole module cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email.contract import (  # noqa: E402
    EmailCategory,
    EmailTriageResponse,
    EmailTriageResult,
)
from gaia_agent_email.export_openapi import ARTIFACT_PATH, build_app  # noqa: E402
from gaia_agent_email.version import AGENT_VERSION, API_VERSION  # noqa: E402

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
    assert body["status"] == "declined"


def test_calendar_create_invalid_payload_returns_422(client):
    """A start with neither date_time nor date is a documented validation error."""
    resp = client.post(
        "/v1/email/calendar/events/preview",
        json=_calendar_event_body(start={}, end={}),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 6. All documented paths are covered
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
        ("post", "/v1/email/draft"),
        ("post", "/v1/email/send"),
        ("get", "/v1/email/health"),
        ("get", "/v1/email/version"),
        # Calendar surface (schema 2.1, #1780).
        ("get", "/v1/email/calendar/events"),
        ("post", "/v1/email/calendar/events"),
        ("post", "/v1/email/calendar/events/preview"),
        ("post", "/v1/email/calendar/events/respond"),
    }
    assert documented == expected, (
        f"Spec has routes not covered by conformance tests: "
        f"{documented - expected}. Add a conformance test for each new route."
    )
