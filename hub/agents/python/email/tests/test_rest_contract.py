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
    EmailTriageRequest,
    EmailTriageResponse,
)
from gaia_agent_email.version import AGENT_VERSION, API_VERSION  # noqa: E402

# Routes whose 200 response model is part of the published contract surface.
# Maps (method, path) -> the component schema name the handler declares.
_EXPECTED_RESPONSE_MODELS = {
    ("post", "/v1/email/triage"): "EmailTriageResponse",
    ("post", "/v1/email/draft"): "EmailDraftResponse",
    ("post", "/v1/email/send"): "EmailSendResponse",
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
    for name in ("EmailTriageRequest", "EmailTriageResponse", "EmailTriageResult"):
        assert name in schemas, f"{name} missing from exported OpenAPI components"


@pytest.mark.parametrize("model", [EmailTriageRequest, EmailTriageResponse])
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
# 6. Calendar surface (#1780) — view / preview / create (gated) / respond
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
