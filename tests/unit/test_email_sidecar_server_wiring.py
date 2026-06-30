# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA_EMAIL_AGENT_MODE wiring in the UI server: the flag swaps the /v1/email
backend from the in-process mount to the out-of-process sidecar."""

import importlib.util

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app


def test_flag_set_mounts_sidecar_router_and_manager(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    app = create_app(db_path=":memory:")
    # The sidecar manager is attached for lazy spawn + lifespan tree-kill.
    from gaia.ui.email_sidecar.manager import EmailSidecarManager

    assert isinstance(app.state.email_sidecar_manager, EmailSidecarManager)
    # Use a TestClient probe instead of inspecting app.routes directly: older
    # Starlette versions expose _IncludedRouter objects without a .path attribute,
    # so HTTP reachability is the robust cross-version assertion.
    client = TestClient(app, raise_server_exceptions=False)
    # /v1/email/triage is mounted: NOT 404 (422/405/503/200 all prove route exists).
    assert client.post("/v1/email/triage", json={}).status_code != 404
    # Security: connector write routes are NOT handled by the sidecar surface.
    # They may return 404 (no route) or 405 (method not allowed on a read-only
    # connector route elsewhere in the app). Neither is a sidecar proxy forward.
    connector_post = client.post("/v1/email/connectors/google/complete", json={})
    assert connector_post.status_code in (404, 405)


def test_flag_unset_uses_in_process_mount(monkeypatch):
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    app = create_app(db_path=":memory:")
    # No sidecar manager when the flag is unset (default, non-breaking path).
    assert getattr(app.state, "email_sidecar_manager", None) is None


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="email agent + uvicorn required for live server->sidecar round-trip",
)
def test_live_server_routes_v1_email_health_through_sidecar(monkeypatch):
    # Full chain: UI server route -> EmailSidecarManager -> real dev sidecar.
    # The TestClient context runs the lifespan, so the sidecar is tree-killed on
    # exit. Generous health timeout for the first uvicorn boot.
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    app = create_app(db_path=":memory:")
    app.state.email_sidecar_manager.health_timeout = 60.0
    with TestClient(app) as client:
        r = client.get("/v1/email/health")
        assert r.status_code == 200, r.text
        assert r.json()["service"] == "gaia-agent-email"
    # Lifespan shutdown ran on context exit; the sidecar must be reaped.
    assert not app.state.email_sidecar_manager.is_running
