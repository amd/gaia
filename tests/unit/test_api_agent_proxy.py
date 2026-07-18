# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deterministic suite for the ``gaia api`` agent /query proxy
(``gaia.api.agent_proxy``, issue #2178 / V2-17).

The proxy makes ``gaia api`` a thin client of the always-on daemon relay
(#2150): it forwards ``/v1/<agent>/query`` to the daemon carrying only the
DAEMON client token, gated by ``GAIA_API_KEY``. It never learns sidecar
coordinates nor holds a sidecar bearer.

Part 1 (helpers): the API-key dependency + synthetic-frame shape (pure unit).

Part 2 (routes): TestClient over the real ``gaia.api.openai_server:app`` — the
API-key 503/401 contract and the daemon-down loud 503, with ``start_or_attach``
monkeypatched (no upstream involved). Also asserts the scoped ``/query`` relay
does NOT shadow ``/v1/chat/completions`` / ``/v1/models`` or their 404/405.

Part 3 (integration): a REAL uvicorn fake *daemon* (mimicking the #2150 relay
surface) + the REAL ``gaia api`` app on ephemeral ports (never 4001). The fake
daemon's stream is gated on explicit events, never wall-clock timing, so the
load-bearing assertions are deterministic:

  (a) no buffering — the first SSE event reaches the REST client while the fake
      daemon's stream body is provably still open,
  (b) cancel-on-disconnect — closing the REST client connection mid-stream tears
      down the proxy→daemon connection (the daemon then owns sidecar cancel),
  (c) the daemon-authored synthetic terminal ``error`` (source ``daemon_relay``,
      §0.13) is forwarded VERBATIM,
  (d) a daemon connection that drops mid-stream yields the proxy's OWN synthetic
      terminal ``error`` (source ``gaia_api``) — the consumer never hangs,
  (e) the DAEMON client token — never the API key — crosses to the daemon.
"""

# NO `from __future__ import annotations`: the fake daemon's FastAPI endpoints
# annotate `request: Request` with Request imported inside build_app(); a
# stringified annotation could not resolve it (PEP 563).

import importlib.util
import json
import os
import threading
import time

import pytest

from gaia.api.agent_proxy import (
    API_KEY_ENV,
    _synthetic_error_frame,
    daemon_stream_dropped_detail,
)

_HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None
_HAS_UVICORN = importlib.util.find_spec("uvicorn") is not None
_HAS_HTTPX = importlib.util.find_spec("httpx") is not None
_HAS_REQUESTS = importlib.util.find_spec("requests") is not None

needs_fastapi = pytest.mark.skipif(
    not (_HAS_FASTAPI and _HAS_HTTPX),
    reason="fastapi + httpx must be importable for proxy route tests",
)
needs_live_servers = pytest.mark.skipif(
    not (_HAS_FASTAPI and _HAS_HTTPX and _HAS_UVICORN and _HAS_REQUESTS),
    reason=(
        "fastapi, httpx, uvicorn, and requests must all be importable for the "
        "real-server proxy integration tests"
    ),
)

_API_KEY = "test-api-key-abc123"
_DAEMON_TOKEN = "daemon-client-tok"


def _frame(event: dict) -> bytes:
    return b"data: " + json.dumps(event).encode() + b"\n\n"


# ---------------------------------------------------------------------------
# Part 1 — helpers (pure unit, no HTTP)
# ---------------------------------------------------------------------------


def test_synthetic_error_frame_shape():
    raw = _synthetic_error_frame("it broke")
    assert raw.startswith(b"data: ") and raw.endswith(b"\n\n")
    event = json.loads(raw[len(b"data: ") : -2])
    assert event == {"type": "error", "detail": "it broke", "source": "gaia_api"}


def test_daemon_stream_dropped_detail_is_actionable():
    detail = daemon_stream_dropped_detail("email")
    assert "email" in detail
    assert "daemon status" in detail  # names where to look


# ---------------------------------------------------------------------------
# Part 2 — route-level auth + daemon-down (TestClient, no upstream)
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_client():
    if not (_HAS_FASTAPI and _HAS_HTTPX):
        pytest.skip("fastapi/httpx not importable")
    from fastapi.testclient import TestClient

    from gaia.api.openai_server import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def no_api_key(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)


@pytest.fixture()
def with_api_key(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, _API_KEY)


@needs_fastapi
def test_query_disabled_without_api_key_configured(api_client, no_api_key):
    r = api_client.post("/v1/toy/query", json={"query": "hi"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert API_KEY_ENV in detail  # names the remedy


@needs_fastapi
def test_query_missing_api_key_header_is_401(api_client, with_api_key):
    r = api_client.post("/v1/toy/query", json={"query": "hi"})
    assert r.status_code == 401
    assert API_KEY_ENV in r.json()["detail"]


@needs_fastapi
def test_query_wrong_api_key_is_401(api_client, with_api_key):
    r = api_client.post(
        "/v1/toy/query",
        json={"query": "hi"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert r.status_code == 401


@needs_fastapi
def test_query_malformed_auth_header_is_401(api_client, with_api_key):
    r = api_client.post(
        "/v1/toy/query",
        json={"query": "hi"},
        headers={"Authorization": _API_KEY},  # no "Bearer " scheme
    )
    assert r.status_code == 401


@needs_fastapi
def test_daemon_down_maps_to_loud_503(api_client, with_api_key, monkeypatch):
    """Acceptance: daemon down → loud actionable error, no silent fallback."""
    from gaia.daemon.errors import DaemonStartError

    def _boom():
        raise DaemonStartError("the daemon did not become healthy within 30s.")

    monkeypatch.setattr("gaia.daemon.client.start_or_attach", _boom)
    r = api_client.post(
        "/v1/toy/query",
        json={"query": "hi"},
        headers={"Authorization": f"Bearer {_API_KEY}"},
    )
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "daemon" in detail.lower()
    assert "gaia daemon status" in detail  # names where to look


@needs_fastapi
def test_reserved_chat_query_not_relayed_as_agent(api_client, with_api_key):
    """A stray /v1/chat/query must not become an "agent chat" relay."""
    r = api_client.post(
        "/v1/chat/query",
        json={"query": "hi"},
        headers={"Authorization": f"Bearer {_API_KEY}"},
    )
    assert r.status_code == 404
    assert "not an agent route" in r.json()["detail"]


@needs_fastapi
def test_fixed_function_route_claimed_by_relay_not_404(api_client, no_api_key):
    """Regression for #2176: a fixed-function agent route (e.g. the email
    agent's POST /v1/email/triage) must be CLAIMED by the relay on ``gaia api``,
    not answered with FastAPI's 404. With no API key configured it hits the
    relay's own 503 (surface disabled) — proving the route reaches the relay
    (before the fix it 404'd because the relay only claimed /query)."""
    r = api_client.post("/v1/email/triage", json={"payload": {}})
    assert r.status_code == 503
    assert API_KEY_ENV in r.json()["detail"]  # names the remedy


@needs_fastapi
def test_fixed_function_route_daemon_down_is_loud_503(
    api_client, with_api_key, monkeypatch
):
    """A fixed-function route with the daemon down fails loud (503), never a
    silent in-process fallback (#2176)."""
    from gaia.daemon.errors import DaemonStartError

    def _boom():
        raise DaemonStartError("the daemon did not become healthy within 30s.")

    monkeypatch.setattr("gaia.daemon.client.start_or_attach", _boom)
    r = api_client.post(
        "/v1/email/triage",
        json={"payload": {}},
        headers={"Authorization": f"Bearer {_API_KEY}"},
    )
    assert r.status_code == 503
    assert "gaia daemon status" in r.json()["detail"]  # names where to look


@needs_fastapi
def test_fixed_function_reserved_id_not_relayed(api_client, with_api_key):
    """The fixed-function catch-all must not relay the reserved OpenAI ids: a
    two-segment /v1/chat/<x> is not an agent route (never reaches the daemon)."""
    r = api_client.post(
        "/v1/chat/completions/extra",
        json={},
        headers={"Authorization": f"Bearer {_API_KEY}"},
    )
    # 'chat' is refused by the relayagent convertor, so nothing matches → 404.
    assert r.status_code == 404


@needs_fastapi
def test_openai_routes_not_shadowed_by_relay(api_client, with_api_key):
    """/v1/models and /v1/chat/completions keep their own handlers even with the
    query relay mounted and an API key set."""
    r = api_client.get("/v1/models")
    assert r.status_code == 200
    assert r.json()["object"] == "list"
    # /v1/chat/completions is declared before the relay → its handler wins; an
    # unknown model is the OpenAI handler's own 404, not the relay's.
    r = api_client.post(
        "/v1/chat/completions",
        json={
            "model": "does-not-exist",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


@needs_fastapi
@pytest.mark.parametrize("has_key", [False, True])
def test_query_relay_does_not_shadow_openai_404_405(api_client, monkeypatch, has_key):
    """The scoped /query relay must NOT swallow the OpenAI surface's own 404/405
    (regression for the generic-catch-all shadowing that reached CI: GET
    /v1/chat/completions returned the relay's 503 instead of FastAPI's 405).
    Holds whether or not the API key is configured — these paths never reach the
    relay's API-key dependency."""
    if has_key:
        monkeypatch.setenv(API_KEY_ENV, _API_KEY)
    else:
        monkeypatch.delenv(API_KEY_ENV, raising=False)
    # Wrong method on a real OpenAI route → 405, not the relay's 503.
    assert api_client.get("/v1/chat/completions").status_code == 405
    assert api_client.post("/v1/models", json={}).status_code == 405
    # Unknown single-segment path → FastAPI 404, not the relay's 503.
    assert api_client.get("/v1/nonexistent").status_code == 404


# ---------------------------------------------------------------------------
# Part 3 — real-server integration: fake daemon + gaia api proxy over TCP
# ---------------------------------------------------------------------------


class _FakeDaemon:
    """Scripted stand-in for the #2150 daemon relay surface.

    ``/v1/toy/query`` streams SSE gated on explicit events (never sleeps for
    effect): one ``status`` event, then — mode ``hold`` — waits for ``release``
    before the terminal ``final``; mode ``daemon_crash`` — one ``status`` then
    the stream generator RAISES, so uvicorn aborts the response mid-stream (the
    proxy's httpx read then errors → the proxy authors its own terminal);
    mode ``relay_synthetic`` — one ``status`` then the daemon-authored §0.13
    terminal error (source ``daemon_relay``), which the proxy must forward
    verbatim. ``stream_body_done`` is set only when the generator has fully
    exited, which makes the no-buffering assertion deterministic.
    """

    def __init__(self):
        self.release = threading.Event()
        self.stream_body_done = threading.Event()
        self.mode = "hold"
        self.seen_auth = []  # Authorization header values the daemon received

    def build_app(self):
        import asyncio

        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, StreamingResponse

        app = FastAPI()
        daemon = self

        @app.api_route(
            "/v1/{agent_id}/{path:path}",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        )
        async def relay(agent_id: str, path: str, request: Request):
            daemon.seen_auth.append(request.headers.get("authorization"))

            # Fixed-function (non-/query) routes answer buffered JSON, mimicking
            # the daemon relaying a sidecar's e.g. /v1/email/triage response.
            if path != "query" and not path.endswith("/cancel"):
                return JSONResponse(
                    {"agent": agent_id, "path": path, "relayed": True},
                    status_code=200,
                )

            async def _events():
                try:
                    yield _frame({"type": "status", "message": "starting"})
                    if daemon.mode == "daemon_crash":
                        # Abort the response mid-stream: the client's httpx read
                        # sees a truncated body → transport error.
                        raise RuntimeError("simulated daemon crash mid-stream")
                    if daemon.mode == "relay_synthetic":
                        # The daemon relay's own §0.13 terminal (sidecar died).
                        yield _frame(
                            {
                                "type": "error",
                                "detail": "sidecar crashed",
                                "source": "daemon_relay",
                            }
                        )
                        return
                    while not daemon.release.is_set():
                        await asyncio.sleep(0.02)
                    yield _frame({"type": "final", "answer": "done"})
                finally:
                    daemon.stream_body_done.set()

            return StreamingResponse(_events(), media_type="text/event-stream")

        return app


def _serve(app):
    """Run *app* under uvicorn on an ephemeral port (never 4001) in a
    background thread. Returns (server, thread, port)."""
    import uvicorn

    from gaia.daemon.sidecars.manager import find_free_port

    port = find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("test uvicorn server never started")
    return server, thread, port


@pytest.fixture()
def live_proxy(monkeypatch):
    """A live fake daemon + the real gaia api app whose start_or_attach points
    at it. GAIA_API_KEY is set so the surface is enabled."""
    if not (_HAS_FASTAPI and _HAS_HTTPX and _HAS_UVICORN and _HAS_REQUESTS):
        pytest.skip("fastapi/httpx/uvicorn/requests not importable")

    from gaia.api.openai_server import app as api_app
    from gaia.daemon.instance import DaemonInstance

    monkeypatch.setenv(API_KEY_ENV, _API_KEY)

    daemon = _FakeDaemon()
    d_server, d_thread, d_port = _serve(daemon.build_app())

    def _fake_start_or_attach(*_a, **_k):
        return DaemonInstance(
            pid=os.getpid(), port=d_port, token=_DAEMON_TOKEN, host="127.0.0.1"
        )

    # Patched where agent_proxy imports it (lazily, from gaia.daemon.client).
    monkeypatch.setattr("gaia.daemon.client.start_or_attach", _fake_start_or_attach)

    api_server, api_thread, api_port = _serve(api_app)

    yield daemon, f"http://127.0.0.1:{api_port}"

    daemon.release.set()  # unblock any still-held stream
    api_server.should_exit = True
    d_server.should_exit = True
    api_thread.join(timeout=10)
    d_thread.join(timeout=10)


def _iter_data_events(resp):
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data:"):
            continue
        yield json.loads(line[len("data:") :].strip())


def _wait_for(predicate, timeout=5.0, message="condition never became true"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def _auth():
    return {"Authorization": f"Bearer {_API_KEY}"}


@needs_live_servers
def test_sse_relayed_unbuffered_first_event_before_upstream_completes(live_proxy):
    import requests

    daemon, api_url = live_proxy
    with requests.post(
        f"{api_url}/v1/toy/query",
        json={"query": "q", "run_id": "run-nobuf"},
        headers=_auth(),
        stream=True,
        timeout=(5, 15),
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _iter_data_events(resp)
        first = next(events)
        assert first["type"] == "status"
        # THE no-buffering assertion: the first event reached the REST client
        # while the fake daemon's stream body is provably still open (it only
        # completes after `release`, which has not been set).
        assert not daemon.stream_body_done.is_set()

        daemon.release.set()
        rest = list(events)
    assert [e["type"] for e in rest] == ["final"]


@needs_live_servers
def test_fixed_function_relayed_buffered_through_core_server(live_proxy):
    """End-to-end regression for #2176: a fixed-function agent route relays
    through the REAL ``gaia api`` server (buffered passthrough) to the daemon —
    status, body, and the DAEMON token all cross. This is the coverage that
    exercises the email fixed-function surface via the core server, not only the
    wheel's own app."""
    import requests

    daemon, api_url = live_proxy
    r = requests.post(
        f"{api_url}/v1/email/triage",
        json={"payload": {"kind": "single"}},
        headers=_auth(),
        timeout=(5, 15),
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"agent": "email", "path": "triage", "relayed": True}
    # A GET fixed-function route relays too (email health/version/init are GET).
    rg = requests.get(f"{api_url}/v1/email/health", headers=_auth(), timeout=(5, 15))
    assert rg.status_code == 200
    assert rg.json()["path"] == "health"
    # The daemon saw the DAEMON client token, never the API key.
    assert daemon.seen_auth[-1] == f"Bearer {_DAEMON_TOKEN}"
    assert f"Bearer {_API_KEY}" not in daemon.seen_auth


@needs_live_servers
def test_daemon_client_token_crosses_never_the_api_key(live_proxy):
    import requests

    daemon, api_url = live_proxy
    daemon.mode = "relay_synthetic"  # terminates fast, no release needed
    with requests.post(
        f"{api_url}/v1/toy/query",
        json={"query": "q"},
        headers=_auth(),
        stream=True,
        timeout=(5, 15),
    ) as resp:
        list(_iter_data_events(resp))

    # The daemon saw the DAEMON client token; the API key never crossed.
    assert daemon.seen_auth[-1] == f"Bearer {_DAEMON_TOKEN}"
    assert daemon.seen_auth[-1] != f"Bearer {_API_KEY}"


@needs_live_servers
def test_client_disconnect_tears_down_upstream(live_proxy):
    import requests

    daemon, api_url = live_proxy
    resp = requests.post(
        f"{api_url}/v1/toy/query",
        json={"query": "q", "run_id": "run-cancel"},
        headers=_auth(),
        stream=True,
        timeout=(5, 15),
    )
    events = _iter_data_events(resp)
    assert next(events)["type"] == "status"
    resp.close()  # REST client walks away mid-stream

    # Closing the REST connection must tear down the proxy→daemon connection,
    # ending the fake daemon's held stream body (the daemon then owns the
    # sidecar cancel, tested in test_daemon_relay.py). Deterministic: release
    # is NOT set, so a completed `final` cannot be the cause.
    assert not daemon.release.is_set()
    _wait_for(
        daemon.stream_body_done.is_set,
        message="proxy never tore down the upstream connection on client disconnect",
    )


@needs_live_servers
def test_daemon_authored_synthetic_error_forwarded_verbatim(live_proxy):
    import requests

    daemon, api_url = live_proxy
    daemon.mode = "relay_synthetic"
    with requests.post(
        f"{api_url}/v1/toy/query",
        json={"query": "q", "run_id": "run-relaysyn"},
        headers=_auth(),
        stream=True,
        timeout=(5, 15),
    ) as resp:
        assert resp.status_code == 200
        events = list(_iter_data_events(resp))

    assert [e["type"] for e in events] == ["status", "error"]
    # The daemon-authored §0.13 frame passes through UNTOUCHED — the proxy did
    # not re-stamp its source.
    assert events[-1]["source"] == "daemon_relay"
    assert events[-1]["detail"] == "sidecar crashed"


@needs_live_servers
def test_daemon_crash_mid_stream_yields_proxy_synthetic_terminal_error(live_proxy):
    import requests

    daemon, api_url = live_proxy
    daemon.mode = "daemon_crash"
    with requests.post(
        f"{api_url}/v1/toy/query",
        json={"query": "q", "run_id": "run-dcrash"},
        headers=_auth(),
        stream=True,
        timeout=(5, 15),
    ) as resp:
        assert resp.status_code == 200
        events = list(_iter_data_events(resp))  # stream must end CLEANLY

    assert [e["type"] for e in events] == ["status", "error"]
    synthetic = events[-1]
    # The daemon died, so the PROXY authored the terminal — distinct source.
    assert synthetic["source"] == "gaia_api"
    assert synthetic["detail"] == daemon_stream_dropped_detail("toy")
