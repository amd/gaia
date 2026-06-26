# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration tests for the email REST router mounted in the UI backend (#1768).

Verifies that ``gaia.ui.server.create_app`` conditionally mounts the
``gaia_agent_email`` router when the wheel is installed, and that the mounted
surface responds correctly to health, version, and triage requests.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# Skip the whole module if FastAPI's TestClient is unavailable (bare [dev]-only env).
pytest.importorskip("fastapi")
# Skip if the email wheel is not installed.
pytest.importorskip("gaia_agent_email")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email.contract import (  # noqa: E402
    EmailCategory,
    EmailTriageResponse,
    EmailTriageResult,
)
from gaia_agent_email.version import API_VERSION  # noqa: E402

from gaia.ui.server import create_app  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixture: UI app with in-memory DB (no filesystem side effects)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ui_client():
    """TestClient for the UI backend with gaia_agent_email wheel present."""
    app = create_app(db_path=":memory:")
    # The email routes are self-contained; skip the UI server's lifespan startup
    # (connectors sync / MCP reload), which can hang in a bare test env (#1297).
    yield TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# AC1 — router is mounted when the wheel is present
# ---------------------------------------------------------------------------


def test_email_health_mounted(ui_client):
    """GET /v1/email/health -> 200 with status='ok' when the wheel is installed."""
    resp = ui_client.get("/v1/email/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("status") == "ok"


def test_email_version_mounted(ui_client):
    """GET /v1/email/version -> 200 with apiVersion == '2.0'."""
    resp = ui_client.get("/v1/email/version")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("apiVersion") == API_VERSION


# ---------------------------------------------------------------------------
# AC2 — triage endpoint returns 200 with the schema-2.0 shape (LLM mocked)
# ---------------------------------------------------------------------------


def _stub_triage_response() -> EmailTriageResponse:
    """A minimal valid schema-2.0 response that doesn't touch the LLM."""
    result = EmailTriageResult(
        category=EmailCategory.FYI,
        summary="Test summary produced by stub.",
        action_items=[],
    )
    return EmailTriageResponse(request_kind="single", result=result)


def test_triage_returns_200_schema_2_shape(ui_client):
    """POST /v1/email/triage -> 200 whose body matches the schema-2.0 shape.

    The LLM layer is mocked so this runs without a Lemonade server.
    """
    stub_resp = _stub_triage_response()

    with patch(
        "gaia_agent_email.api_routes.EmailTriageService.triage_request",
        return_value=stub_resp,
    ):
        payload = {
            "schema_version": "2.0",
            "payload": {
                "kind": "single",
                "principal": {"name": "Test User", "email": "user@example.com"},
                "message": {
                    "message_id": "msg-001",
                    "from": {"name": "Sender", "email": "sender@example.com"},
                    "subject": "Hello",
                    "body": "Just a quick note.",
                },
            },
        }
        resp = ui_client.post("/v1/email/triage", json=payload)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Contract envelope shape
    assert "schema_version" in body
    assert body.get("schema_version") == API_VERSION
    assert body.get("request_kind") == "single"

    result = body.get("result", {})
    assert "category" in result
    assert "summary" in result
    assert result["summary"] == "Test summary produced by stub."


# ---------------------------------------------------------------------------
# AC3 — fail-loud guard: absent wheel -> clean skip (no exception, routes unmounted)
#        present-but-broken wheel -> error propagates (not swallowed)
# ---------------------------------------------------------------------------


def test_mount_block_absent_wheel_does_not_mount():
    """When gaia_agent_email is absent, the email paths are not served by the router.

    Simulates the absent-wheel path by making only ``gaia_agent_email`` look
    absent to ``find_spec``. Proves the mount block does a clean skip and does
    NOT raise an ImportError when the wheel is simply not installed.
    """
    import importlib.util as ilu

    real_find_spec = ilu.find_spec

    def _absent_email(name, *args, **kwargs):
        # Only the email wheel looks absent; every other optional dependency
        # resolves normally so create_app builds the rest of the app unchanged.
        if name == "gaia_agent_email":
            return None
        return real_find_spec(name, *args, **kwargs)

    # find_spec is evaluated at create_app() runtime, so patching it (no module
    # reload needed) makes the mount block take its clean-skip branch.
    with patch("importlib.util.find_spec", side_effect=_absent_email):
        app = create_app(db_path=":memory:")

    client = TestClient(app)
    # The mount is skipped, so these paths fall through to the UI's SPA catch-all
    # (HTML 200) rather than 404 — assert they are NOT served by the email router,
    # i.e. they do not return its JSON contract.
    for path in ("/v1/email/version", "/v1/email/health"):
        resp = client.get(path)
        assert "application/json" not in resp.headers.get(
            "content-type", ""
        ), f"{path} unexpectedly served by the email router when the wheel is absent"
