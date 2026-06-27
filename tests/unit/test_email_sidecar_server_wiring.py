# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA_EMAIL_AGENT_MODE wiring in the UI server: the flag swaps the /v1/email
backend from the in-process mount to the out-of-process sidecar."""

import importlib.util

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app


def _email_routes(app):
    return {
        r.path for r in app.routes if getattr(r, "path", "").startswith("/v1/email")
    }


def test_flag_set_mounts_sidecar_router_and_manager(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    app = create_app(db_path=":memory:")
    # The sidecar manager is attached for lazy spawn + lifespan tree-kill.
    from gaia.ui.email_sidecar.manager import EmailSidecarManager

    assert isinstance(app.state.email_sidecar_manager, EmailSidecarManager)
    routes = _email_routes(app)
    assert "/v1/email/triage" in routes
    # Security: connector write routes are NOT exposed via the sidecar surface.
    assert not any("connectors" in p for p in routes)


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
