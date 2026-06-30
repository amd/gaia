# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The sidecar-backed /v1/email REST router: lazy-start, proxy, error passthrough,
and the security allowlist (no connector write routes)."""

import requests as _requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui.email_sidecar.errors import SidecarHTTPError, SidecarSpawnError
from gaia.ui.email_sidecar.router import router as email_router


class _FakeProxy:
    def __init__(self, *, error=None):
        self._error = error

    def triage(self, body):
        if self._error:
            raise self._error
        return {"request_kind": "single", "echo": body}

    def draft(self, body):
        return {"draft": body, "confirmation_token": "tok"}

    def send(self, body):
        if self._error:
            raise self._error
        return {"sent": True}

    def health(self):
        return {"status": "ok", "service": "gaia-agent-email"}

    def version(self):
        return {"apiVersion": "2.0", "agentVersion": "0.2.2"}


class _FakeManager:
    def __init__(self, *, start_error=None, proxy_error=None):
        self.started = 0
        self._running = False
        self._start_error = start_error
        self._proxy_error = proxy_error

    @property
    def is_running(self):
        return self._running

    def start(self):
        self.started += 1
        if self._start_error:
            raise self._start_error
        self._running = True
        return "http://127.0.0.1:9999"

    def proxy(self, **kwargs):
        return _FakeProxy(error=self._proxy_error)


def _client(manager) -> TestClient:
    app = FastAPI()
    app.state.email_sidecar_manager = manager
    app.include_router(email_router)
    return TestClient(app)


def test_triage_lazily_starts_then_proxies():
    mgr = _FakeManager()
    client = _client(mgr)
    r = client.post("/v1/email/triage", json={"payload": {"kind": "single"}})
    assert r.status_code == 200
    assert r.json()["echo"] == {"payload": {"kind": "single"}}
    assert mgr.started == 1


def test_second_call_reuses_running_sidecar():
    mgr = _FakeManager()
    client = _client(mgr)
    client.post("/v1/email/triage", json={})
    client.post("/v1/email/triage", json={})
    assert mgr.started == 1  # not restarted


def test_health_and_version_proxied():
    client = _client(_FakeManager())
    assert client.get("/v1/email/health").json()["service"] == "gaia-agent-email"
    assert client.get("/v1/email/version").json()["apiVersion"] == "2.0"


def test_sidecar_http_error_status_and_detail_passthrough():
    mgr = _FakeManager(
        proxy_error=SidecarHTTPError(
            502, "local LLM triage failed", path="/v1/email/triage"
        )
    )
    client = _client(mgr)
    r = client.post("/v1/email/triage", json={})
    assert r.status_code == 502
    assert "local LLM triage failed" in r.json()["detail"]


def test_start_failure_returns_503_with_remedy():
    mgr = _FakeManager(
        start_error=SidecarSpawnError(
            "dev mode needs the email source; uv pip install -e ..."
        )
    )
    client = _client(mgr)
    r = client.post("/v1/email/triage", json={})
    assert r.status_code == 503
    assert "uv pip install -e" in r.json()["detail"]


def test_missing_manager_returns_500():
    app = FastAPI()
    app.include_router(email_router)
    r = TestClient(app, raise_server_exceptions=False).post("/v1/email/triage", json={})
    assert r.status_code == 500


def test_connection_error_surfaces_as_503_with_actionable_detail():
    # Fix B: a raw requests.ConnectionError (sidecar crashed mid-request, after
    # the is_running pre-check) must surface as a loud 503, not an unhandled 500.
    mgr = _FakeManager(
        proxy_error=_requests.exceptions.ConnectionError(
            "Connection refused to 127.0.0.1:9999"
        )
    )
    client = _client(mgr)
    r = client.post("/v1/email/triage", json={})
    assert r.status_code == 503
    assert "email sidecar unreachable" in r.json()["detail"]
    assert "Connection refused" in r.json()["detail"]


def test_connector_routes_not_exposed():
    # Security: the sidecar's connector WRITE routes must never be reachable via
    # the UI surface. The router allowlists only triage/draft/send/health/version.
    client = _client(_FakeManager())
    assert (
        client.post("/v1/email/connectors/google/complete", json={}).status_code == 404
    )
    assert client.get("/v1/email/connectors").status_code == 404
