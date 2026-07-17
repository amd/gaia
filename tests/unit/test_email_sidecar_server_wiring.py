# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Email REST wiring in the UI server after the daemon-client cutover (#2142
T3): the /v1/email surface is still the out-of-process sidecar ONLY, but the
UI backend no longer owns a spawning manager on ``app.state`` — it acquires a
handle from the daemon per request (``daemon_client.acquire_handle()``).
"""

from fastapi.testclient import TestClient

from gaia.ui.server import create_app


def _assert_sidecar_surface(app):
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


def test_sidecar_router_mounted_dev_mode(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    app = create_app(db_path=":memory:")
    _assert_sidecar_surface(app)


def test_sidecar_router_mounted_flag_unset_defaults_to_user_mode(monkeypatch):
    # The cutover removed the in-process mount: with the flag unset the sidecar
    # is STILL the surface (user mode selects the frozen binary at ensure-time,
    # inside the daemon — the UI backend never resolves the mode itself here).
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    app = create_app(db_path=":memory:")
    _assert_sidecar_surface(app)


def test_no_email_sidecar_manager_on_app_state():
    # The daemon-client cutover means the UI backend is a pure HTTP client of
    # the daemon's /daemon/v1/agents control plane — it never owns a spawning
    # EmailSidecarManager instance, so nothing is smuggled onto app.state.
    app = create_app(db_path=":memory:")
    assert not hasattr(app.state, "email_sidecar_manager")


# NOTE: the live server->real-sidecar round-trip (formerly
# test_live_server_routes_v1_email_health_through_sidecar) now needs a real
# running daemon, not just the email agent + uvicorn — that belongs to T4's
# integration suite, not this unit-level wiring test.
