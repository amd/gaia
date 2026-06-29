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
