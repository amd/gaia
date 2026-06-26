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
    ("post", "/v1/email/search"): "EmailSearchResponse",
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
