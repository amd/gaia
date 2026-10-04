# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The committed ``openapi.gaia.json`` matches the live ``/v1/gaia/*`` schema (#4605).

#3201 was a client rejected with 422 because the docs never named ``query``
and ``context`` as required or said ``run_id`` must be a UUID. These tests pin
the machine-readable contract that fixes that: the committed artifact is not
stale, the request body documents exactly its required fields, the UUID
constraint on ``run_id`` is visible in the schema (not just the field
description), and the strict (``extra="forbid"``) contract models reject
unknown fields in the schema the same way they do at runtime.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent import export_openapi  # noqa: E402


@pytest.fixture(scope="module")
def spec() -> dict:
    """The freshly built OpenAPI spec (what the committed artifact should be)."""
    return export_openapi.build_spec()


def test_committed_openapi_artifact_is_up_to_date():
    assert export_openapi.check_artifact(), (
        "openapi.gaia.json is stale. Regenerate it with:\n"
        "  python -m gaia_agent.export_openapi"
    )


def test_query_request_required_fields(spec):
    # #3201: a client written from the prose sent run_id/session_id/message and
    # was 422'd for missing query/context. The schema must say exactly this.
    component = spec["components"]["schemas"]["QueryRequest"]
    assert set(component["required"]) == {"query", "run_id", "context"}


def test_query_request_run_id_is_documented_as_a_uuid(spec):
    # The field description is the ONLY place the UUID constraint can live —
    # Pydantic's field_validator does not change the emitted JSON Schema type —
    # so the description is load-bearing, not decoration.
    run_id = spec["components"]["schemas"]["QueryRequest"]["properties"]["run_id"]
    assert "UUID" in run_id.get("description", "")


def test_query_request_optional_fields_are_documented(spec):
    props = spec["components"]["schemas"]["QueryRequest"]["properties"]
    for field in (
        "query",
        "model",
        "provider",
        "max_steps",
        "session_id",
        "can_answer_questions",
    ):
        assert props[field].get("description"), f"{field} has no description"


def test_query_request_forbids_unknown_fields(spec):
    # _Strict (extra="forbid") must surface as additionalProperties: false, or a
    # client reading only the schema would believe extra fields are tolerated.
    component = spec["components"]["schemas"]["QueryRequest"]
    assert component.get("additionalProperties") is False


def test_bearer_auth_is_declared(spec):
    schemes = spec["components"]["securitySchemes"]
    assert schemes["bearerAuth"] == {"type": "http", "scheme": "bearer"}


def test_query_route_declares_bearer_or_none_security(spec):
    # Gated route: bearer OR no requirement (no token configured is dev-only,
    # never a documented "always required" claim).
    op = spec["paths"]["/v1/gaia/query"]["post"]
    assert op["security"] == [{"bearerAuth": []}, {}]


def test_probe_routes_declare_no_security_requirement(spec):
    # /health, /version, /v1/gaia/version are registered on the app, outside
    # the token-guarded router — they must read as deliberately public.
    for path in ("/health", "/version", "/v1/gaia/version"):
        op = spec["paths"][path]["get"]
        assert op["security"] == []


def test_tool_decision_and_bypass_routes_are_documented(spec):
    # Both shipped in contract >= 2.14; a committed artifact missing either is
    # exactly the kind of drift this test exists to catch.
    assert "/v1/gaia/query/{run_id}/tool_decision" in spec["paths"]
    assert "/v1/gaia/sessions/{session_id}/bypass" in spec["paths"]


def test_docs_url_is_disabled_but_openapi_json_is_reachable():
    # Swagger UI pulls its JS from a CDN — an unexpected network call for an
    # embedder running this sidecar offline. /docs must be gone; /openapi.json
    # (the machine-readable contract itself) must still be served.
    app = export_openapi.build_app()
    assert app.docs_url is None
    assert app.openapi_url == "/openapi.json"


def test_exported_operations_match_the_real_server(spec, monkeypatch):
    # The export app copies the probe routes by hand; a route added only to
    # server.build_app must not be missing from the published contract.
    from gaia_agent import caller_auth
    from gaia_agent.server import build_app

    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    caller_auth.reset()
    try:
        live = build_app().openapi()
    finally:
        caller_auth.reset()

    def operations(doc):
        return {(path, method) for path, ops in doc["paths"].items() for method in ops}

    assert set(spec["paths"]) == set(live["paths"])
    assert operations(spec) == operations(live)
