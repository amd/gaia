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
    def __init__(self, *, error=None, init_result=None):
        self._error = error
        self._init_result = init_result or (
            200,
            {"ready": True, "lemonade": {"base_url": "http://127.0.0.1:8000/api/v1"}},
        )

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

    def pre_scan_inbox(self, body):
        return {"result": {"kind": "email_pre_scan", "urgent": [], "echo": body}}

    def search_inbox(self, body):
        return {"query": body.get("query"), "messages": []}

    def archive(self, body):
        return {"message_id": body.get("message_id"), "batch_id": "b1"}

    def calendar_events(self, params):
        return {"events": [], "params": params}

    def health(self):
        return {"status": "ok", "service": "gaia-agent-email"}

    def version(self):
        return {"apiVersion": "2.0", "agentVersion": "0.2.2"}

    def init(self):
        if self._error:
            raise self._error
        return self._init_result


class _FakeManager:
    def __init__(self, *, start_error=None, proxy_error=None, init_result=None):
        self.started = 0
        self._running = False
        self._start_error = start_error
        self._proxy_error = proxy_error
        self._init_result = init_result

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
        return _FakeProxy(error=self._proxy_error, init_result=self._init_result)


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


def test_init_ready_200_body_passthrough():
    # #1888: GET /v1/email/init must be proxied (the docs' verify curl targets
    # the backend), preserving the sidecar's 200 + InitResponse body verbatim.
    ready_body = {
        "ready": True,
        "lemonade": {"reachable": True, "base_url": "http://127.0.0.1:8555/api/v1"},
        "model": {"id": "Gemma-4-E4B-it-GGUF", "present": True},
        "hint": None,
    }
    client = _client(_FakeManager(init_result=(200, ready_body)))
    r = client.get("/v1/email/init")
    assert r.status_code == 200
    assert r.json() == ready_body


def test_init_not_ready_503_body_passthrough():
    # The 503 is contract (full InitResponse with an actionable hint), not a
    # transport failure — the body must survive the proxy hop intact.
    not_ready_body = {
        "ready": False,
        "lemonade": {"reachable": False, "base_url": "http://127.0.0.1:9999/api/v1"},
        "model": {"id": "Gemma-4-E4B-it-GGUF", "present": False},
        "hint": "Lemonade Server not reachable — start it with `lemonade-server serve`",
    }
    client = _client(_FakeManager(init_result=(503, not_ready_body)))
    r = client.get("/v1/email/init")
    assert r.status_code == 503
    assert r.json() == not_ready_body


def test_prescan_route_forwards_and_preserves_card_envelope():
    # The card pipeline depends on /prescan returning the email_pre_scan envelope
    # shape unchanged through the sidecar router.
    client = _client(_FakeManager())
    r = client.post("/v1/email/prescan", json={"max_messages": 10})
    assert r.status_code == 200
    assert r.json()["result"]["kind"] == "email_pre_scan"


def test_search_and_archive_routes_mounted():
    client = _client(_FakeManager())
    assert (
        client.post("/v1/email/search", json={"query": "is:unread"}).status_code == 200
    )
    assert (
        client.post("/v1/email/archive", json={"message_id": "m1"}).status_code == 200
    )


def test_calendar_events_get_forwards_query_params():
    client = _client(_FakeManager())
    r = client.get("/v1/email/calendar/events?time_min=2026-06-30T00:00:00Z")
    assert r.status_code == 200
    assert r.json()["params"] == {"time_min": "2026-06-30T00:00:00Z"}


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
