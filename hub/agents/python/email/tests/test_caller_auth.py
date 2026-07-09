# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Caller-authentication tests for the email sidecar's local REST API (#1706).

The sidecar binds 127.0.0.1 and exposes draft/send against the user's connected
mailbox. These tests lock in the three controls that make it safe to expose:

1. **Per-session bearer token** — a non-exempt request without a valid token is
   401; with the correct token it is served; with a wrong token it is 401.
2. **Host allowlist** — a non-loopback ``Host`` header is 400 (DNS-rebinding).
3. **Origin rejection** — a non-loopback browser ``Origin`` is 403 (drive-by).

``POST /v1/email/draft`` is the probe endpoint: it mints a confirmation token
with no mailbox/LLM dependency, so it exercises the auth layer in isolation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import caller_auth  # noqa: E402
from gaia_agent_email.api_routes import require_caller_token  # noqa: E402
from gaia_agent_email.api_routes import router as email_router  # noqa: E402

_TOKEN = "s3cret-session-token"
# Loopback base so the Host header passes TrustedHost by default; individual
# tests override Host/Origin to exercise rejection.
_BASE_URL = "http://127.0.0.1:8131"

_DRAFT_BODY = {
    "to": [{"email": "sarah@example.com"}],
    "subject": "Re: Prod incident follow-up",
    "body": "Reviewed — I'll reply by Friday.",
}


@pytest.fixture(autouse=True)
def _reset_auth():
    """Clear the process-wide auth policy before AND after each test so config
    never leaks between tests (or into other modules building the export app)."""
    caller_auth.reset()
    yield
    caller_auth.reset()


def _build_app(token) -> FastAPI:
    """Mirror the sidecar wiring (``packaging/server.py``): Host/Origin
    middleware + the token dependency on the email router."""
    caller_auth.configure(caller_auth.CallerAuthConfig(token=token))
    app = FastAPI()
    app.add_middleware(caller_auth.HostOriginMiddleware)
    app.include_router(email_router, dependencies=[Depends(require_caller_token)])
    return app


def _client(token) -> TestClient:
    return TestClient(_build_app(token), base_url=_BASE_URL)


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def test_generate_session_token_is_random_and_urlsafe():
    a = caller_auth.generate_session_token()
    b = caller_auth.generate_session_token()
    assert a != b
    assert len(a) >= 32
    # URL-safe alphabet only (survives an Authorization header verbatim).
    assert all(c.isalnum() or c in "-_" for c in a)


def test_token_ok_matches_only_the_exact_bearer_token():
    cfg = caller_auth.CallerAuthConfig(token=_TOKEN)
    assert caller_auth.token_ok(cfg, f"Bearer {_TOKEN}")
    assert caller_auth.token_ok(cfg, f"bearer {_TOKEN}")  # scheme is case-insensitive
    assert not caller_auth.token_ok(cfg, "Bearer wrong")
    assert not caller_auth.token_ok(cfg, _TOKEN)  # missing scheme
    assert not caller_auth.token_ok(cfg, "")
    assert not caller_auth.token_ok(cfg, "Basic abc")


def test_token_ok_is_open_when_no_token_configured():
    cfg = caller_auth.CallerAuthConfig(token=None)
    assert caller_auth.token_ok(cfg, "")  # dev mode — token check disabled


def test_host_only_strips_port_and_handles_ipv6():
    assert caller_auth._host_only("127.0.0.1:8131") == "127.0.0.1"
    assert caller_auth._host_only("localhost") == "localhost"
    assert caller_auth._host_only("[::1]:8131") == "::1"
    assert caller_auth._host_only("") == ""


# ---------------------------------------------------------------------------
# Token enforcement (the acceptance matrix)
# ---------------------------------------------------------------------------


def test_no_token_header_is_rejected_401():
    client = _client(_TOKEN)
    resp = client.post("/v1/email/draft", json=_DRAFT_BODY)
    assert resp.status_code == 401
    assert "bearer token" in resp.json()["detail"].lower()


def test_correct_token_is_accepted_200():
    client = _client(_TOKEN)
    resp = client.post(
        "/v1/email/draft",
        json=_DRAFT_BODY,
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.json()["confirmation_token"]


def test_wrong_token_is_rejected_401():
    client = _client(_TOKEN)
    resp = client.post(
        "/v1/email/draft",
        json=_DRAFT_BODY,
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert resp.status_code == 401


def test_health_and_version_are_exempt_from_token():
    client = _client(_TOKEN)
    assert client.get("/v1/email/health").status_code == 200
    assert client.get("/v1/email/version").status_code == 200


def test_open_mode_serves_without_a_token():
    # No token configured (dev standalone): draft/send are reachable without an
    # Authorization header — but Host/Origin protection still applies.
    client = _client(None)
    resp = client.post("/v1/email/draft", json=_DRAFT_BODY)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Host / Origin controls
# ---------------------------------------------------------------------------


def test_non_loopback_host_is_rejected_400():
    client = _client(_TOKEN)
    resp = client.post(
        "/v1/email/draft",
        json=_DRAFT_BODY,
        headers={"Authorization": f"Bearer {_TOKEN}", "Host": "evil.com"},
    )
    assert resp.status_code == 400
    assert "host" in resp.json()["detail"].lower()


def test_cross_origin_browser_request_is_rejected_403():
    client = _client(_TOKEN)
    resp = client.post(
        "/v1/email/draft",
        json=_DRAFT_BODY,
        headers={"Authorization": f"Bearer {_TOKEN}", "Origin": "https://evil.com"},
    )
    assert resp.status_code == 403
    assert "origin" in resp.json()["detail"].lower()


def test_loopback_origin_is_allowed():
    client = _client(_TOKEN)
    resp = client.post(
        "/v1/email/draft",
        json=_DRAFT_BODY,
        headers={
            "Authorization": f"Bearer {_TOKEN}",
            "Origin": "http://127.0.0.1:8131",
        },
    )
    assert resp.status_code == 200


def test_host_and_origin_apply_even_to_exempt_paths():
    # The transport-level controls must cover probes too — a rebinding attempt on
    # /v1/email/health must still be rejected even though the token is exempt.
    client = _client(_TOKEN)
    resp = client.get("/v1/email/health", headers={"Host": "evil.com"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# The real sidecar app wiring (packaging/server.py) — guards against the
# middleware/dependency not actually being installed on the shipped app.
# ---------------------------------------------------------------------------


def _load_sidecar_server():
    path = Path(__file__).resolve().parents[1] / "packaging" / "server.py"
    spec = importlib.util.spec_from_file_location(
        "email_sidecar_server_under_test", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shipped_sidecar_app_enforces_token(monkeypatch):
    monkeypatch.setenv(caller_auth.TOKEN_ENV_VAR, _TOKEN)
    server = _load_sidecar_server()
    app = server.build_app()  # rebuilds with the env token now set
    client = TestClient(app, base_url=_BASE_URL)

    assert client.post("/v1/email/draft", json=_DRAFT_BODY).status_code == 401
    ok = client.post(
        "/v1/email/draft",
        json=_DRAFT_BODY,
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    assert ok.status_code == 200
    # Root liveness probe stays open (readiness handshake needs it pre-token).
    assert client.get("/health").status_code == 200


def test_shipped_app_gates_connector_and_agent_routers(monkeypatch):
    # Every mailbox-touching router is gated, not just the email router: the
    # connector lifecycle (configure/complete/disconnect) and the stateful agent
    # surface can act on the mailbox, so an unauthenticated local caller is 401.
    monkeypatch.setenv(caller_auth.TOKEN_ENV_VAR, _TOKEN)
    server = _load_sidecar_server()
    client = TestClient(server.build_app(), base_url=_BASE_URL)

    # The gate is applied to both routers: an unauthenticated caller is 401
    # BEFORE the handler runs (so no live connector store / agent session is
    # touched). The with-token pass-through is proven via /draft in the sibling
    # test — the same require_caller_token dependency gates all three routers.
    assert client.get("/v1/email/connectors").status_code == 401
    assert client.post("/v1/email/agent/session", json={}).status_code == 401


def test_shipped_app_streams_provision_through_middleware(monkeypatch):
    # The pure-ASGI HostOriginMiddleware must NOT buffer/break the
    # StreamingResponse from POST /v1/email/init. With Lemonade unreachable the
    # verb returns a 503 stream of actionable lines; assert it arrives intact
    # through the middleware + token gate.
    import gaia_agent_email.api_routes as ar

    monkeypatch.setenv(caller_auth.TOKEN_ENV_VAR, _TOKEN)
    monkeypatch.setattr(
        ar, "_probe_lemonade_reachable", lambda *a, **k: (False, "http://x/api/v1")
    )
    server = _load_sidecar_server()
    client = TestClient(server.build_app(), base_url=_BASE_URL)

    resp = client.post("/v1/email/init", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 503
    assert "not reachable" in resp.text.lower()
    # Same request without the token is refused before streaming starts.
    assert client.post("/v1/email/init").status_code == 401
