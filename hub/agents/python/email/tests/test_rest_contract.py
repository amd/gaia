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
    ("post", "/v1/email/prescan"): "EmailPreScanResponse",
    ("post", "/v1/email/draft"): "EmailDraftResponse",
    ("post", "/v1/email/send"): "EmailSendResponse",
    ("get", "/v1/email/health"): "HealthResponse",
    ("get", "/v1/email/version"): "VersionResponse",
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
# 6. Inbox pre-scan (#1778) — fake backend via app.dependency_overrides;
#    no live mailbox, no LLM (the heuristic path classifies these messages).
# ---------------------------------------------------------------------------


def _gmail_message(
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
            _gmail_message(
                "m1",
                subject="50% off this weekend!",
                sender="deals@shop.example",
                label_ids=["INBOX", "CATEGORY_PROMOTIONS"],
            ),
            _gmail_message(
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
