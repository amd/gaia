# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Email REST wiring in the UI server: the /v1/email surface is the
out-of-process sidecar ONLY (#1767 cutover). GAIA_EMAIL_AGENT_MODE only selects
which process answers (user default / dev); it never re-mounts the wheel."""

import importlib.util

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app


def _assert_sidecar_surface(app):
    from gaia.ui.email_sidecar.manager import EmailSidecarManager

    # The sidecar manager is attached for lazy spawn + lifespan tree-kill.
    assert isinstance(app.state.email_sidecar_manager, EmailSidecarManager)
    # Use a TestClient probe instead of inspecting app.routes directly: older
    # Starlette versions expose _IncludedRouter objects without a .path attribute,
    # so HTTP reachability is the robust cross-version assertion.
    client = TestClient(app, raise_server_exceptions=False)
    # The full schema-2.1 surface is mounted: NOT 404 (422/405/503/200 all prove
    # the route exists). /prescan + /calendar/events are the routes the in-process
    # mount used to serve — they must survive the cutover via the sidecar router.
    assert client.post("/v1/email/triage", json={}).status_code != 404
    assert client.post("/v1/email/prescan", json={}).status_code != 404
    assert client.get("/v1/email/calendar/events").status_code != 404
    # Security: connector write routes are NOT handled by the sidecar surface.
    connector_post = client.post("/v1/email/connectors/google/complete", json={})
    assert connector_post.status_code in (404, 405)


def test_dev_mode_mounts_sidecar_router_and_manager(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    app = create_app(db_path=":memory:")
    _assert_sidecar_surface(app)
    assert app.state.email_sidecar_manager.mode == "dev"


def test_flag_unset_defaults_to_user_mode_sidecar(monkeypatch):
    # The cutover removed the in-process mount: with the flag unset the sidecar is
    # STILL the surface, in user mode (frozen binary). No silent in-process fallback.
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    app = create_app(db_path=":memory:")
    _assert_sidecar_surface(app)
    assert app.state.email_sidecar_manager.mode == "user"


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
