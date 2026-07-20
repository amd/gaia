# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deterministic suite for the daemon's streaming SSE reverse-proxy
(``gaia.daemon.relay``, issue #2150 / V2-7).

Part 1 (unit): the passive ``_SSEWatcher`` (terminal/run_id tracking across
arbitrary chunk boundaries), body run_id extraction, and the synthetic
terminal frame shape.

Part 2 (unit): ``SidecarRegistry.connection`` — the relay's single server-side
source for the sidecar bearer.

Part 3 (routes): TestClient over ``create_app`` with a stub registry — the
401/404/503 loud-error contract, no upstream involved.

Part 4 (integration): a REAL uvicorn fake sidecar + a REAL uvicorn daemon app
on ephemeral ports (never 4001 — ``find_free_port`` excludes it). The fake
sidecar's stream is gated on explicit events, never wall-clock timing, so the
load-bearing assertions are deterministic:

  (a) no buffering — the first SSE event reaches the client while the
      upstream body is provably still open,
  (b) cancel-on-disconnect — closing the client connection mid-stream makes
      the relay POST ``/v1/email/query/{run_id}/cancel`` upstream with the
      SIDECAR bearer,
  (c) sidecar crash mid-stream (EOF, no terminal event) — the client receives
      a synthetic terminal ``error`` event and a cleanly-ended response,
  (d) buffered passthrough for fixed-function routes — status, headers, and
      body preserved; the daemon token never reaches the sidecar, the sidecar
      bearer never left the daemon.
"""

# NO `from __future__ import annotations` here: the fake sidecar's FastAPI
# endpoints annotate `request: Request` with Request imported inside
# build_app(); stringified annotations could not resolve it (PEP 563).

import importlib.util
import json
import threading
import time

import pytest

from gaia.daemon.relay import (
    TERMINAL_TYPES,
    _run_id_from_body,
    _SSEWatcher,
    _synthetic_error_frame,
    stream_ended_unexpectedly_detail,
)
from gaia.daemon.sidecars.errors import SidecarNotRunningError, UnknownAgentError

_HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None
_HAS_UVICORN = importlib.util.find_spec("uvicorn") is not None
_HAS_HTTPX = importlib.util.find_spec("httpx") is not None
_HAS_REQUESTS = importlib.util.find_spec("requests") is not None

needs_fastapi = pytest.mark.skipif(
    not (_HAS_FASTAPI and _HAS_HTTPX),
    reason="fastapi + httpx must be importable for relay route tests",
)
needs_live_servers = pytest.mark.skipif(
    not (_HAS_FASTAPI and _HAS_HTTPX and _HAS_UVICORN and _HAS_REQUESTS),
    reason=(
        "fastapi, httpx, uvicorn, and requests must all be importable for the "
        "real-server relay integration tests"
    ),
)

_DAEMON_TOKEN = "daemon-client-tok"
_SIDECAR_TOKEN = "sidecar-bearer-tok"


# ---------------------------------------------------------------------------
# Part 1 — _SSEWatcher / helpers (pure unit, no HTTP)
# ---------------------------------------------------------------------------


def _frame(event: dict) -> bytes:
    return b"data: " + json.dumps(event).encode() + b"\n\n"


def test_terminal_types_match_frozen_contract():
    assert TERMINAL_TYPES == frozenset({"final", "error"})


def test_watcher_sees_terminal_final():
    w = _SSEWatcher()
    w.feed(_frame({"type": "status", "message": "hi"}))
    assert not w.terminal_seen
    w.feed(_frame({"type": "final", "answer": "done"}))
    assert w.terminal_seen


def test_watcher_sees_terminal_error():
    w = _SSEWatcher()
    w.feed(_frame({"type": "error", "detail": "boom"}))
    assert w.terminal_seen


def test_watcher_handles_arbitrary_chunk_boundaries():
    """A frame split mid-line across feeds must still be recognized."""
    w = _SSEWatcher()
    raw = _frame({"type": "status", "message": "x"}) + _frame(
        {"type": "final", "answer": "y"}
    )
    for i in range(0, len(raw), 3):
        w.feed(raw[i : i + 3])
    assert w.terminal_seen
    assert not w.mid_frame


def test_watcher_captures_run_id_from_events():
    w = _SSEWatcher()
    w.feed(_frame({"type": "needs_confirmation", "run_id": "run-77"}))
    assert w.run_id == "run-77"


def test_watcher_seeded_run_id_survives_events_without_one():
    w = _SSEWatcher(run_id="seed-run")
    w.feed(_frame({"type": "status", "message": "no run id here"}))
    assert w.run_id == "seed-run"


def test_watcher_mid_frame_true_for_partial_frame():
    w = _SSEWatcher()
    w.feed(b'data: {"type": "tok')
    assert w.mid_frame


def test_watcher_malformed_data_payload_does_not_raise():
    w = _SSEWatcher()
    w.feed(b"data: {not json}\n\n")
    assert not w.terminal_seen


def test_watcher_ignores_non_data_lines_and_crlf():
    w = _SSEWatcher()
    w.feed(
        b": keep-alive\r\n\r\ndata: "
        + json.dumps({"type": "final"}).encode()
        + b"\r\n\r\n"
    )
    # \r\n\r\n framing: the \n\n split leaves \r remnants that must be stripped.
    assert w.terminal_seen


def test_watcher_buffer_overflow_capped_without_crash(monkeypatch):
    from gaia.daemon import relay as relay_mod

    monkeypatch.setattr(relay_mod, "_WATCHER_BUFFER_CAP", 1024)
    w = _SSEWatcher()
    w.feed(b"x" * 4096)  # exceeds the (patched) cap, no frame separator
    assert w.mid_frame
    assert w.degraded  # sticky: terminal classification is unknown from here
    assert not w.terminal_seen
    # A later well-formed terminal is still recognized...
    w.feed(b"\n\n" + _frame({"type": "final", "answer": "y"}))
    assert w.terminal_seen
    # ...but degradation never resets (bytes were dropped earlier).
    assert w.degraded


def test_watcher_not_degraded_below_cap():
    w = _SSEWatcher()
    w.feed(_frame({"type": "status", "message": "x"}))
    assert not w.degraded


def test_run_id_from_body_extracts_string_run_id():
    assert _run_id_from_body(b'{"query": "q", "run_id": "r-1"}') == "r-1"


@pytest.mark.parametrize(
    "body", [b"", b"not json", b'{"run_id": 7}', b'{"query": "no id"}', b'["r-1"]']
)
def test_run_id_from_body_returns_none_when_absent(body):
    assert _run_id_from_body(body) is None


def test_synthetic_error_frame_shape():
    raw = _synthetic_error_frame("it broke", terminate_partial=False)
    assert raw.startswith(b"data: ") and raw.endswith(b"\n\n")
    event = json.loads(raw[len(b"data: ") : -2])
    assert event == {"type": "error", "detail": "it broke", "source": "daemon_relay"}


def test_synthetic_error_frame_terminates_partial_frame_first():
    raw = _synthetic_error_frame("it broke", terminate_partial=True)
    assert raw.startswith(b"\n\ndata: ")


def test_stream_ended_detail_is_actionable():
    detail = stream_ended_unexpectedly_detail("email")
    assert "email" in detail
    assert "ensure" in detail  # names the remedy
    assert "logs" in detail  # names where to look


# ---------------------------------------------------------------------------
# Part 2 — SidecarRegistry.connection
# ---------------------------------------------------------------------------


class _FakeManager:
    def __init__(self, spec, mode=None, **kwargs):
        self.spec = spec
        self._running = False
        self.port = None
        self.base_url = None
        self.api_version = "1.0"
        self.agent_version = "0.1.0"
        self.resolved_mode = None
        self.auth_token = f"tok-{spec.agent_id}"
        self.pid = None
        self.started_at = None

    @property
    def is_running(self):
        return self._running

    def start(self):
        self.resolved_mode = "user"
        self.pid = 4321
        self.port = 54321
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.started_at = time.time()
        self._running = True
        return self.base_url

    def shutdown(self):
        self._running = False


def _toy_registry():
    from gaia.daemon.sidecars.registry import SidecarRegistry
    from gaia.daemon.sidecars.spec import AgentSidecarSpec

    spec = AgentSidecarSpec(
        agent_id="toy",
        service_id="gaia-agent-toy",
        display_name="Toy Agent",
        expected_api_major="1",
        token_env_var="GAIA_TOY_SIDECAR_TOKEN",
        mode_env_var="GAIA_TOY_AGENT_MODE",
        cache_dir_name="toy",
    )
    reg = SidecarRegistry({"toy": spec})
    reg._manager_factory = _FakeManager
    return reg


def test_connection_unknown_agent_raises_listing_registered_ids():
    reg = _toy_registry()
    with pytest.raises(UnknownAgentError) as exc_info:
        reg.connection("bogus")
    assert "toy" in str(exc_info.value)


def test_connection_not_running_raises_with_ensure_remedy():
    reg = _toy_registry()
    with pytest.raises(SidecarNotRunningError) as exc_info:
        reg.connection("toy")
    msg = str(exc_info.value)
    assert "toy" in msg
    assert "ensure" in msg  # names the remedy


def test_connection_running_returns_base_url_and_bearer():
    reg = _toy_registry()
    ensured = reg.ensure("toy")
    base_url, bearer = reg.connection("toy")
    assert base_url == ensured["base_url"]
    assert bearer == ensured["token"]


def test_connection_stopped_after_running_raises_again(monkeypatch):
    # The fake manager reports a hardcoded pid but owns no real OS process, so
    # its shutdown() can't make that pid disappear. registry.stop() verifies the
    # pid is gone via psutil.pid_exists against the real process table, which
    # flakes when the fake pid collides with a live process on the runner. Model
    # the fake sidecar as fully terminated so the check reflects the double's
    # intent, not the host's process table.
    monkeypatch.setattr(
        "gaia.daemon.sidecars.registry.psutil.pid_exists", lambda pid: False
    )
    reg = _toy_registry()
    reg.ensure("toy")
    reg.stop("toy")
    with pytest.raises(SidecarNotRunningError):
        reg.connection("toy")


# ---------------------------------------------------------------------------
# Part 3 — route-level loud errors (TestClient, stub registry, no upstream)
# ---------------------------------------------------------------------------


class _StubRegistry:
    """connection()-only registry stub for route tests."""

    def __init__(self, base_url=None, bearer=_SIDECAR_TOKEN, known=("email",)):
        self._base_url = base_url
        self._bearer = bearer
        self._known = set(known)

    def connection(self, agent_id):
        if agent_id not in self._known:
            raise UnknownAgentError(
                f"unknown agent '{agent_id}'; registered agents: "
                + ", ".join(sorted(self._known))
            )
        if self._base_url is None:
            raise SidecarNotRunningError(
                f"agent '{agent_id}' has no running sidecar to relay to. "
                f"Start it first (`gaia daemon start-agent {agent_id}` or "
                f"POST /daemon/v1/agents/{agent_id}/ensure), then retry."
            )
        return self._base_url, self._bearer

    # create_app's agents router probes these; keep them inert.
    def list_agents(self):
        return []

    def ensure(self, agent_id, mode=None):  # pragma: no cover - unused
        raise AssertionError("relay tests must not call ensure()")

    def stop(self, agent_id):  # pragma: no cover - unused
        raise AssertionError("relay tests must not call stop()")


def _daemon_test_client(registry):
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    app = create_app(
        token=_DAEMON_TOKEN,
        port=55555,
        pid=1,
        started_at=time.time(),
        registry=registry,
    )
    return TestClient(app, raise_server_exceptions=False)


def _auth(token=_DAEMON_TOKEN):
    return {"Authorization": f"Bearer {token}"}


@needs_fastapi
def test_relay_requires_daemon_client_token():
    client = _daemon_test_client(_StubRegistry())
    r = client.post("/v1/email/query", json={"query": "hi"})  # no auth header
    assert r.status_code == 401
    assert "Authorization" in r.json()["detail"]


@needs_fastapi
def test_relay_rejects_wrong_daemon_client_token():
    client = _daemon_test_client(_StubRegistry())
    r = client.post(
        "/v1/email/query", json={"query": "hi"}, headers=_auth("wrong-token")
    )
    assert r.status_code == 401


@needs_fastapi
def test_relay_unknown_agent_maps_to_404_listing_registered_ids():
    client = _daemon_test_client(_StubRegistry())
    r = client.post("/v1/bogus/query", json={"query": "hi"}, headers=_auth())
    assert r.status_code == 404
    assert "email" in r.json()["detail"]


@needs_fastapi
def test_relay_not_running_sidecar_maps_to_503_with_ensure_remedy():
    client = _daemon_test_client(_StubRegistry(base_url=None))
    r = client.post("/v1/email/query", json={"query": "hi"}, headers=_auth())
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "email" in detail
    assert "ensure" in detail


@needs_fastapi
def test_relay_dead_upstream_maps_to_502_with_reensure_remedy():
    # Registered + "running" per the registry, but nothing listens on the port
    # (the sidecar died after registration): connect refused → loud 502.
    from gaia.daemon.sidecars.manager import find_free_port

    dead_port = find_free_port()
    client = _daemon_test_client(
        _StubRegistry(base_url=f"http://127.0.0.1:{dead_port}")
    )
    r = client.post("/v1/email/query", json={"query": "hi"}, headers=_auth())
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "email" in detail
    assert "ensure" in detail


# ---------------------------------------------------------------------------
# Part 4 — real-server integration: fake sidecar + daemon relay over TCP
# ---------------------------------------------------------------------------


class _FakeSidecar:
    """Scripted stand-in for a sidecar agent server.

    ``/v1/email/query`` streams SSE gated on explicit events (never sleeps for
    effect): one ``status`` event, then — mode ``hold`` — waits for
    ``release`` before the terminal ``final``; mode ``crash`` — returns with
    NO terminal event (EOF mid-contract). ``stream_body_done`` is set only
    when the stream generator has fully exited, which is what makes the
    no-buffering assertion deterministic.
    """

    def __init__(self):
        self.release = threading.Event()
        self.stream_body_done = threading.Event()
        self.mode = "hold"
        self.cancels = []  # (run_id, authorization header)
        self.seen = []  # (path, authorization header, extra header)

    def build_app(self):
        import asyncio

        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, StreamingResponse

        app = FastAPI()
        sidecar = self

        @app.post("/v1/email/query")
        async def query(request: Request):
            body = await request.json()
            sidecar.seen.append(
                (
                    "/v1/email/query",
                    request.headers.get("authorization"),
                    request.headers.get("x-client-extra"),
                )
            )
            run_id = body.get("run_id", "")

            async def _events():
                try:
                    if sidecar.mode == "giant_final":
                        # ONE valid terminal frame far larger than the (test-
                        # patched) watcher cap, split so the first piece
                        # overflows the watcher BEFORE the frame can complete
                        # (the tail is release-gated → deterministic).
                        full = _frame({"type": "final", "answer": "x" * 4096})
                        yield full[:3000]
                        while not sidecar.release.is_set():
                            await asyncio.sleep(0.02)
                        yield full[3000:]
                        return
                    yield _frame(
                        {"type": "status", "message": "starting", "run_id": run_id}
                    )
                    if sidecar.mode == "crash":
                        return  # EOF without a terminal event
                    while not sidecar.release.is_set():
                        await asyncio.sleep(0.02)
                    yield _frame({"type": "final", "answer": "done"})
                finally:
                    sidecar.stream_body_done.set()

            return StreamingResponse(_events(), media_type="text/event-stream")

        @app.post("/v1/email/query/{run_id}/cancel")
        async def cancel(run_id: str, request: Request):
            sidecar.cancels.append((run_id, request.headers.get("authorization")))
            sidecar.release.set()  # a held stream ends on cancel
            return {"status": "cancelled", "run_id": run_id}

        @app.post("/v1/email/triage")
        async def triage(request: Request):
            body = await request.json()
            sidecar.seen.append(
                (
                    "/v1/email/triage",
                    request.headers.get("authorization"),
                    request.headers.get("x-client-extra"),
                )
            )
            return JSONResponse(
                {"result": {"echo": body}},
                headers={"X-Sidecar-Custom": "yes"},
            )

        return app


def _serve(app):
    """Run *app* under uvicorn on an ephemeral port (never 4001) in a
    background thread. Returns (server, thread, base_url)."""
    import uvicorn

    from gaia.daemon.sidecars.manager import find_free_port

    port = find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
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
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.fixture()
def live_relay():
    """A live fake sidecar + a live daemon app relaying to it."""
    if not (_HAS_FASTAPI and _HAS_HTTPX and _HAS_UVICORN and _HAS_REQUESTS):
        pytest.skip("fastapi/httpx/uvicorn/requests not importable")

    from gaia.daemon.app import create_app

    sidecar = _FakeSidecar()
    sc_server, sc_thread, sc_url = _serve(sidecar.build_app())
    daemon_app = create_app(
        token=_DAEMON_TOKEN,
        port=55555,
        pid=1,
        started_at=time.time(),
        registry=_StubRegistry(base_url=sc_url),
    )
    d_server, d_thread, d_url = _serve(daemon_app)

    yield sidecar, d_url

    sidecar.release.set()  # unblock any still-held stream
    d_server.should_exit = True
    sc_server.should_exit = True
    d_thread.join(timeout=10)
    sc_thread.join(timeout=10)


def _iter_data_events(resp):
    """Yield parsed ``data:`` payloads from a requests streaming response."""
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


@needs_live_servers
def test_sse_relayed_unbuffered_first_event_before_upstream_completes(live_relay):
    import requests

    sidecar, daemon_url = live_relay
    with requests.post(
        f"{daemon_url}/v1/email/query",
        json={"query": "q", "run_id": "run-nobuf", "context": []},
        headers=_auth(),
        stream=True,
        timeout=(5, 15),
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _iter_data_events(resp)
        first = next(events)
        assert first["type"] == "status"
        # THE no-buffering assertion: the first event has reached the client
        # while the upstream stream body is provably still open (the fake
        # only completes after `release`, which has not been set).
        assert not sidecar.stream_body_done.is_set()

        sidecar.release.set()
        rest = list(events)
    assert [e["type"] for e in rest] == ["final"]
    _wait_for(sidecar.stream_body_done.is_set)
    # Terminal event arrived → no cancel must have been propagated upstream.
    assert sidecar.cancels == []


@needs_live_servers
def test_client_disconnect_propagates_cancel_upstream(live_relay):
    import requests

    sidecar, daemon_url = live_relay
    resp = requests.post(
        f"{daemon_url}/v1/email/query",
        json={"query": "q", "run_id": "run-cancel", "context": []},
        headers=_auth(),
        stream=True,
        timeout=(5, 15),
    )
    events = _iter_data_events(resp)
    assert next(events)["type"] == "status"
    resp.close()  # client walks away mid-stream

    _wait_for(
        lambda: sidecar.cancels,
        message="relay never POSTed the upstream cancel after client disconnect",
    )
    run_id, auth = sidecar.cancels[0]
    assert run_id == "run-cancel"
    # The cancel carries the SIDECAR bearer (injected server-side), never the
    # daemon client token.
    assert auth == f"Bearer {_SIDECAR_TOKEN}"


@needs_live_servers
def test_sidecar_crash_mid_stream_yields_synthetic_terminal_error(live_relay):
    import requests

    sidecar, daemon_url = live_relay
    sidecar.mode = "crash"
    with requests.post(
        f"{daemon_url}/v1/email/query",
        json={"query": "q", "run_id": "run-crash", "context": []},
        headers=_auth(),
        stream=True,
        timeout=(5, 15),
    ) as resp:
        assert resp.status_code == 200
        events = list(_iter_data_events(resp))  # stream must end CLEANLY

    assert [e["type"] for e in events] == ["status", "error"]
    synthetic = events[-1]
    assert synthetic["source"] == "daemon_relay"
    assert synthetic["detail"] == stream_ended_unexpectedly_detail("email")
    # The abandoned run is cancelled upstream too (single-tenant LLM slot).
    _wait_for(
        lambda: sidecar.cancels,
        message="relay never cancelled the crashed run upstream",
    )
    assert sidecar.cancels[0][0] == "run-crash"


@needs_live_servers
def test_oversized_terminal_frame_relayed_verbatim_without_synthetic_error(
    live_relay, monkeypatch
):
    """W1 guard: a single valid terminal frame larger than the watcher cap
    degrades terminal DETECTION only — the bytes still relay verbatim and no
    spurious synthetic error may double-terminate the completed run."""
    import requests

    from gaia.daemon import relay as relay_mod

    monkeypatch.setattr(relay_mod, "_WATCHER_BUFFER_CAP", 1024)
    sidecar, daemon_url = live_relay
    sidecar.mode = "giant_final"
    with requests.post(
        f"{daemon_url}/v1/email/query",
        json={"query": "q", "run_id": "run-giant", "context": []},
        headers=_auth(),
        stream=True,
        timeout=(5, 15),
    ) as resp:
        assert resp.status_code == 200
        chunks = []
        for chunk in resp.iter_content(chunk_size=None):
            chunks.append(chunk)
            if not sidecar.release.is_set() and sum(map(len, chunks)) >= 2048:
                # The oversized head (past the patched cap) has arrived —
                # now let the sidecar finish the frame.
                sidecar.release.set()
        raw = b"".join(chunks)

    frames = [f for f in raw.split(b"\n\n") if f]
    assert len(frames) == 1  # the giant final only — nothing appended
    event = json.loads(frames[0][len(b"data: ") :])
    assert event["type"] == "final"
    assert event["answer"] == "x" * 4096
    assert b"daemon_relay" not in raw  # no synthetic error frame


@needs_live_servers
def test_fixed_function_route_buffered_passthrough(live_relay):
    import requests

    sidecar, daemon_url = live_relay
    r = requests.post(
        f"{daemon_url}/v1/email/triage",
        json={"subject": "hello"},
        headers={**_auth(), "X-Client-Extra": "abc"},
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json() == {"result": {"echo": {"subject": "hello"}}}
    assert r.headers["X-Sidecar-Custom"] == "yes"  # upstream headers preserved

    path, auth, extra = sidecar.seen[-1]
    assert path == "/v1/email/triage"
    # Bearer swap: the sidecar sees ITS token; the daemon client token never
    # crosses the relay boundary.
    assert auth == f"Bearer {_SIDECAR_TOKEN}"
    assert f"Bearer {_DAEMON_TOKEN}" not in (auth or "")
    assert extra == "abc"  # non-auth client headers pass through


@needs_live_servers
def test_upstream_non_2xx_passes_through_with_body(live_relay):
    import requests

    _, daemon_url = live_relay
    r = requests.get(
        f"{daemon_url}/v1/email/no-such-route", headers=_auth(), timeout=10
    )
    # The SIDECAR's own 404 envelope, not the daemon's unknown-agent 404.
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


@needs_live_servers
def test_control_plane_status_route_unchanged(live_relay):
    import requests

    _, daemon_url = live_relay
    r = requests.get(f"{daemon_url}/daemon/v1/status", headers=_auth(), timeout=10)
    assert r.status_code == 200
    assert r.json()["service"]
