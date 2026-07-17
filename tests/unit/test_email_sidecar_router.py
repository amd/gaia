# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The sidecar-backed /v1/email REST router after the daemon-client cutover
(#2142 T3): lazy per-request handle acquisition, proxy forwarding, error
passthrough, and the security allowlist (no connector write routes).

The router no longer reads a spawning manager off ``request.app.state`` — it
calls ``daemon_client.acquire_handle()`` (off the event loop) per request and
forwards through the returned handle's bound proxy. Tests patch the seam at
``gaia.ui.email_sidecar.daemon_client.acquire_handle``.
"""

import requests as _requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.ui.email_sidecar.daemon_client as daemon_client_module
from gaia.daemon.sidecars.errors import SidecarError, SidecarSpawnError
from gaia.ui.email_sidecar.errors import SidecarHTTPError
from gaia.ui.email_sidecar.router import router as email_router


class _FakeProxy:
    def __init__(self, *, error=None, init_result=None, provision_result=None):
        self._error = error
        self._init_result = init_result or (
            200,
            {"ready": True, "lemonade": {"base_url": "http://127.0.0.1:8000/api/v1"}},
        )
        self._provision_result = provision_result or (
            200,
            "text/plain; charset=utf-8",
            iter([b"provisioning\n", b"done\n"]),
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

    def provision(self):
        if self._error:
            raise self._error
        return self._provision_result


class _FakeHandle:
    def __init__(
        self,
        *,
        proxy_error=None,
        init_result=None,
        provision_result=None,
    ):
        self._proxy_error = proxy_error
        self._init_result = init_result
        self._provision_result = provision_result

    def proxy(self, **kwargs):
        return _FakeProxy(
            error=self._proxy_error,
            init_result=self._init_result,
            provision_result=self._provision_result,
        )


class _AcquireRecorder:
    """Records every ``acquire_handle()`` call; per-request acquisition is
    the new contract (no client-side cache), so N requests must count N
    acquire calls."""

    def __init__(self, handle_factory):
        self.calls = 0
        self._handle_factory = handle_factory

    def __call__(self, agent_id="email"):
        self.calls += 1
        return self._handle_factory()


def _client(acquire_fn, monkeypatch) -> TestClient:
    monkeypatch.setattr(daemon_client_module, "acquire_handle", acquire_fn)
    app = FastAPI()
    app.include_router(email_router)
    return TestClient(app)


def test_each_request_acquires_a_fresh_handle(monkeypatch):
    # Per-request acquisition is the new contract: no client-side handle
    # cache in the router, since the daemon itself owns the running sidecar.
    recorder = _AcquireRecorder(lambda: _FakeHandle())
    client = _client(recorder, monkeypatch)

    r1 = client.post("/v1/email/triage", json={"payload": {"kind": "single"}})
    assert r1.status_code == 200
    assert r1.json()["echo"] == {"payload": {"kind": "single"}}

    r2 = client.post("/v1/email/triage", json={})
    assert r2.status_code == 200

    assert recorder.calls == 2


def test_health_and_version_proxied(monkeypatch):
    client = _client(_AcquireRecorder(lambda: _FakeHandle()), monkeypatch)
    assert client.get("/v1/email/health").json()["service"] == "gaia-agent-email"
    assert client.get("/v1/email/version").json()["apiVersion"] == "2.0"


def test_init_ready_200_body_passthrough(monkeypatch):
    # #1888: GET /v1/email/init must be proxied (the docs' verify curl targets
    # the backend), preserving the sidecar's 200 + InitResponse body verbatim.
    ready_body = {
        "ready": True,
        "lemonade": {"reachable": True, "base_url": "http://127.0.0.1:8555/api/v1"},
        "model": {"id": "Gemma-4-E4B-it-GGUF", "present": True},
        "hint": None,
    }
    client = _client(
        _AcquireRecorder(lambda: _FakeHandle(init_result=(200, ready_body))),
        monkeypatch,
    )
    r = client.get("/v1/email/init")
    assert r.status_code == 200
    assert r.json() == ready_body


def test_init_not_ready_503_body_passthrough(monkeypatch):
    # The 503 is contract (full InitResponse with an actionable hint), not a
    # transport failure — the body must survive the proxy hop intact.
    not_ready_body = {
        "ready": False,
        "lemonade": {"reachable": False, "base_url": "http://127.0.0.1:9999/api/v1"},
        "model": {"id": "Gemma-4-E4B-it-GGUF", "present": False},
        "hint": "Lemonade Server not reachable — start it with `lemonade-server serve`",
    }
    client = _client(
        _AcquireRecorder(lambda: _FakeHandle(init_result=(503, not_ready_body))),
        monkeypatch,
    )
    r = client.get("/v1/email/init")
    assert r.status_code == 503
    assert r.json() == not_ready_body


def test_init_post_streams_provisioning_output_200(monkeypatch):
    # #2054: POST /v1/email/init forwards the sidecar's streamed provisioning
    # progress through chunk-by-chunk (not buffered to completion). TestClient
    # coalesces body chunks, so drive the ASGI app directly and capture the raw
    # http.response.body messages — one per chunk proves it streamed.
    import asyncio

    chunks = [
        "→ Pulling Gemma-4-E4B-it-GGUF via Lemonade…\n".encode(),
        "✓ Provisioning complete. Re-run GET /v1/email/init to confirm readiness.\n".encode(),
    ]
    monkeypatch.setattr(
        daemon_client_module,
        "acquire_handle",
        _AcquireRecorder(
            lambda: _FakeHandle(
                provision_result=(
                    200,
                    "text/plain; charset=utf-8",
                    iter(list(chunks)),
                )
            )
        ),
    )
    app = FastAPI()
    app.include_router(email_router)

    messages = []
    body_sent = False

    async def receive():
        # First call delivers the (empty) request body; later calls block so
        # starlette's disconnect-listener parks instead of spinning, and gets
        # cancelled cleanly when the stream finishes.
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.get_running_loop().create_future()

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/email/init",
        "raw_path": b"/v1/email/init",
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 4200),
    }
    asyncio.run(app(scope, receive, send))

    start = messages[0]
    assert start["type"] == "http.response.start"
    assert start["status"] == 200
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    assert headers["content-type"].startswith("text/plain")
    bodies = [
        m["body"] for m in messages if m["type"] == "http.response.body" and m["body"]
    ]
    assert bodies == chunks  # chunk boundaries survive — proof it streamed


def test_init_post_503_unreachable_streamed_body_passthrough(monkeypatch):
    # Lemonade-unreachable is a contract 503 with actionable streamed lines —
    # status and body must pass through unchanged.
    client = _client(
        _AcquireRecorder(
            lambda: _FakeHandle(
                provision_result=(
                    503,
                    "text/plain; charset=utf-8",
                    iter(["✗ Local Lemonade Server is not reachable\n".encode()]),
                )
            )
        ),
        monkeypatch,
    )
    r = client.post("/v1/email/init")
    assert r.status_code == 503
    assert "not reachable" in r.text


def test_init_post_sidecar_http_error_passthrough(monkeypatch):
    # Outside the 200/503 contract the loud SidecarHTTPError boundary holds.
    client = _client(
        _AcquireRecorder(
            lambda: _FakeHandle(
                proxy_error=SidecarHTTPError(
                    401, "Missing bearer token", path="/v1/email/init"
                )
            )
        ),
        monkeypatch,
    )
    r = client.post("/v1/email/init")
    assert r.status_code == 401
    assert "bearer token" in r.json()["detail"].lower()


def test_prescan_route_forwards_and_preserves_card_envelope(monkeypatch):
    # The card pipeline depends on /prescan returning the email_pre_scan envelope
    # shape unchanged through the sidecar router.
    client = _client(_AcquireRecorder(lambda: _FakeHandle()), monkeypatch)
    r = client.post("/v1/email/prescan", json={"max_messages": 10})
    assert r.status_code == 200
    assert r.json()["result"]["kind"] == "email_pre_scan"


def test_search_and_archive_routes_mounted(monkeypatch):
    client = _client(_AcquireRecorder(lambda: _FakeHandle()), monkeypatch)
    assert (
        client.post("/v1/email/search", json={"query": "is:unread"}).status_code == 200
    )
    assert (
        client.post("/v1/email/archive", json={"message_id": "m1"}).status_code == 200
    )


def test_calendar_events_get_forwards_query_params(monkeypatch):
    client = _client(_AcquireRecorder(lambda: _FakeHandle()), monkeypatch)
    r = client.get("/v1/email/calendar/events?time_min=2026-06-30T00:00:00Z")
    assert r.status_code == 200
    assert r.json()["params"] == {"time_min": "2026-06-30T00:00:00Z"}


def test_sidecar_http_error_status_and_detail_passthrough(monkeypatch):
    client = _client(
        _AcquireRecorder(
            lambda: _FakeHandle(
                proxy_error=SidecarHTTPError(
                    502, "local LLM triage failed", path="/v1/email/triage"
                )
            )
        ),
        monkeypatch,
    )
    r = client.post("/v1/email/triage", json={})
    assert r.status_code == 502
    assert "local LLM triage failed" in r.json()["detail"]


def test_start_failure_returns_503_with_remedy(monkeypatch):
    # acquire_handle itself raises when the daemon cannot spawn the sidecar
    # (dev env missing, port in use, ...) — the router surfaces it as 503.
    def _acquire(agent_id="email"):
        raise SidecarSpawnError(
            "dev mode needs the email source; uv pip install -e ..."
        )

    client = _client(_acquire, monkeypatch)
    r = client.post("/v1/email/triage", json={})
    assert r.status_code == 503
    assert "uv pip install -e" in r.json()["detail"]


def test_acquire_failure_returns_503(monkeypatch):
    # Replaces the old app.state-contract 500: with no app.state manager, a
    # daemon that cannot be reached at all surfaces through acquire_handle as
    # a SidecarError, which is still a loud 503, not a 500.
    def _acquire(agent_id="email"):
        raise SidecarError("daemon unreachable — gaia daemon status for details")

    client = _client(_acquire, monkeypatch)
    r = client.post("/v1/email/triage", json={})
    assert r.status_code == 503
    assert "gaia daemon status" in r.json()["detail"]


def test_connection_error_surfaces_as_503_with_actionable_detail(monkeypatch):
    # Fix B: a raw requests.ConnectionError (sidecar crashed mid-request, after
    # the handle was acquired) must surface as a loud 503, not an unhandled 500.
    client = _client(
        _AcquireRecorder(
            lambda: _FakeHandle(
                proxy_error=_requests.exceptions.ConnectionError(
                    "Connection refused to 127.0.0.1:9999"
                )
            )
        ),
        monkeypatch,
    )
    r = client.post("/v1/email/triage", json={})
    assert r.status_code == 503
    assert "email sidecar unreachable" in r.json()["detail"]
    assert "Connection refused" in r.json()["detail"]


def test_connector_routes_not_exposed(monkeypatch):
    # Security: the sidecar's connector WRITE routes must never be reachable via
    # the UI surface. The router allowlists only triage/draft/send/health/version.
    client = _client(_AcquireRecorder(lambda: _FakeHandle()), monkeypatch)
    assert (
        client.post("/v1/email/connectors/google/complete", json={}).status_code == 404
    )
    assert client.get("/v1/email/connectors").status_code == 404
