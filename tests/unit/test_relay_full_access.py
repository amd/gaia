# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A relayed chat runs in the permission mode the user picked.

The Agent UI's Full access mode only reached in-process agents. A
Hub-installed flagship runs as a daemon sidecar, so a relayed chat in Full
access still asked for every gated tool. The relay now sets the sidecar
session's bypass to the chat's mode before each turn, and on a mid-run
switch. It never runs a turn on a mode it could not set: switching back to Ask
must reach the sidecar before anything runs, or the turn is stopped.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui import _chat_helpers as helpers
from gaia.ui import permissions as session_permissions
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client as daemon_client_module
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.errors import SidecarError, SidecarHTTPError
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    full_access_supported,
)
from gaia.ui.models import ChatRequest
from gaia.ui.routers.chat import router as chat_router
from gaia.ui.sse_handler import SSEOutputHandler

pytestmark = pytest.mark.allow_network


@pytest.fixture(autouse=True)
def _fresh_permissions():
    session_permissions.reset_all()
    yield
    session_permissions.reset_all()


# ── Which sidecars take the mode ───────────────────────────────────────────


@pytest.mark.parametrize(
    "version, expected",
    [("2.15", True), ("2.14", True), ("2.13", False), (None, False), ("x", False)],
)
def test_only_a_flagship_with_the_bypass_route_takes_the_mode(version, expected):
    assert full_access_supported(GAIA_PROFILE, version) is expected


def test_email_has_no_mode_to_set():
    assert full_access_supported(EMAIL_PROFILE, "2.99") is False


# ── The dispatch reads the chat's mode ─────────────────────────────────────


class _FakeHandle:
    def __init__(self, api_version):
        self.api_version = api_version

    def proxy(self):
        class _Proxy:
            def init(self):
                return 200, {}

        return _Proxy()


def _dispatched_full_access(monkeypatch, api_version, mode):
    monkeypatch.setattr(
        daemon_client_module,
        "acquire_handle",
        lambda agent_id: _FakeHandle(api_version),
    )
    calls = []
    monkeypatch.setattr(relay, "relay_query", lambda *a, **k: calls.append(k))
    session_permissions.seed_new_session("sess-1", mode)
    request = ChatRequest(session_id="sess-1", message="hi", agent_type="gaia")
    helpers._dispatch_sidecar_query(
        SSEOutputHandler(), request, [], "m", "gaia", "sess-1"
    )
    [kwargs] = calls
    return kwargs["full_access"]


@pytest.mark.parametrize(
    "mode, expected",
    [
        (session_permissions.MODE_FULL_ACCESS, True),
        (session_permissions.MODE_ASK, False),
    ],
)
def test_the_relayed_turn_carries_the_chats_mode(monkeypatch, mode, expected):
    assert _dispatched_full_access(monkeypatch, "2.15", mode) is expected


def test_an_older_flagship_is_left_alone(monkeypatch):
    mode = session_permissions.MODE_FULL_ACCESS
    assert _dispatched_full_access(monkeypatch, "2.13", mode) is None


# ── relay_query sets the mode before the run ───────────────────────────────


class _Proxy:
    agent_id = "gaia"

    def __init__(self, bypass_errors=()):
        self.calls: list = []
        self._bypass_errors = list(bypass_errors)
        self.response_closed = False

    def set_bypass(self, session_id, enabled):
        self.calls.append(("bypass", session_id, enabled))
        if self._bypass_errors:
            error = self._bypass_errors.pop(0)
            if error is not None:
                raise error
        return {"session_id": session_id, "enabled": enabled}

    def query_stream(self, body, *, read_timeout, on_response):
        self.calls.append(("query",))
        proxy = self

        class _Resp:
            def close(self):
                proxy.response_closed = True

        on_response(_Resp())

        def _events():
            proxy.calls.append(("read",))
            yield {"type": "final", "answer": "done"}

        return _events()

    def cancel_query(self, run_id):
        self.calls.append(("cancel",))


def _relay(proxy, full_access, session_id="sess-1", query="hi"):
    handler = SSEOutputHandler()
    relay.relay_query(
        handler,
        proxy,
        query=query,
        context=[],
        full_access=full_access,
        profile=GAIA_PROFILE,
        session_id=session_id,
    )
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return handler, events


@pytest.mark.parametrize("enabled", [True, False])
def test_the_mode_is_set_before_the_run_starts(enabled):
    proxy = _Proxy()
    _, events = _relay(proxy, enabled)

    assert proxy.calls[:2] == [("bypass", "sess-1", enabled), ("query",)]
    assert events[-1] == {"type": "answer", "content": "done"}


def test_no_mode_means_the_sidecar_is_left_alone():
    proxy = _Proxy()
    _relay(proxy, None)
    assert [c for c in proxy.calls if c[0] == "bypass"] == []


def test_a_new_sidecar_session_gets_full_access_once_query_creates_it():
    proxy = _Proxy(bypass_errors=[SidecarHTTPError(404, "No session")])
    _, events = _relay(proxy, True)

    assert proxy.calls == [
        ("bypass", "sess-1", True),
        ("query",),
        ("bypass", "sess-1", True),
        ("read",),
    ]
    assert events[-1]["type"] == "answer"


def test_a_new_sidecar_session_in_ask_needs_nothing_more():
    """A sidecar session starts in ask, which is what was asked for."""
    proxy = _Proxy(bypass_errors=[SidecarHTTPError(404, "No session")])
    _relay(proxy, False)
    assert proxy.calls == [("bypass", "sess-1", False), ("query",), ("read",)]


@pytest.mark.parametrize(
    "error", [SidecarHTTPError(500, "boom"), SidecarError("connection refused")]
)
@pytest.mark.parametrize("enabled", [True, False])
def test_a_mode_that_cannot_be_set_stops_the_turn_before_it_runs(error, enabled):
    """Turning Ask back on must land first, or the turn could run unasked."""
    proxy = _Proxy(bypass_errors=[error])
    handler, events = _relay(proxy, enabled)

    assert proxy.calls == [("bypass", "sess-1", enabled)], "the run never started"
    assert events[-1]["type"] == "agent_error"
    assert "Full access" in events[-1]["content"]
    assert handler.active_relay_proxy is None


def test_full_access_that_cannot_reach_the_new_session_stops_the_turn():
    proxy = _Proxy(
        bypass_errors=[SidecarHTTPError(404, "No session"), SidecarError("gone")]
    )
    _, events = _relay(proxy, True)

    assert ("read",) not in proxy.calls, "no event of the run was relayed"
    assert proxy.response_closed
    assert ("cancel",) in proxy.calls
    assert events[-1]["type"] == "agent_error"
    assert "turn on Full access" in events[-1]["content"]


# ── A mid-run switch reaches the sidecar ───────────────────────────────────


@pytest.fixture
def client():
    db = ChatDatabase(":memory:")
    app = FastAPI()
    app.include_router(chat_router)
    app.state.db = db
    app.state.session_locks = {}
    session_id = db.create_session(title="relayed")["id"]
    yield TestClient(app), session_id
    helpers._active_sse_handlers.pop(session_id, None)
    db.close()


def _relaying(client_and_session, proxy):
    _, session_id = client_and_session
    handler = SSEOutputHandler()
    handler.active_relay_proxy = proxy
    handler.active_relay_run_id = "r1"
    handler.active_relay_mode_session = session_id
    helpers._active_sse_handlers[session_id] = handler
    return handler


@pytest.mark.parametrize(
    "mode, enabled",
    [
        (session_permissions.MODE_FULL_ACCESS, True),
        (session_permissions.MODE_ASK, False),
    ],
)
def test_switching_mode_mid_run_reaches_the_sidecar(client, mode, enabled):
    http, session_id = client
    proxy = _Proxy()
    _relaying(client, proxy)

    r = http.put(f"/api/chat/permissions/{session_id}", json={"mode": mode})

    assert r.status_code == 200, r.text
    assert proxy.calls == [("bypass", session_id, enabled)]


def test_ask_that_cannot_reach_the_sidecar_stops_the_run(client):
    http, session_id = client
    handler = _relaying(client, _Proxy(bypass_errors=[SidecarError("gone")]))

    r = http.put(
        f"/api/chat/permissions/{session_id}",
        json={"mode": session_permissions.MODE_ASK},
    )

    assert r.status_code == 502
    assert "stopped" in r.json()["detail"]
    assert handler.cancelled.is_set()
    assert session_permissions.get(session_id).mode == session_permissions.MODE_ASK


def test_a_chat_with_no_relayed_run_only_saves_the_mode(client):
    http, session_id = client
    r = http.put(
        f"/api/chat/permissions/{session_id}",
        json={"mode": session_permissions.MODE_FULL_ACCESS},
    )
    assert r.status_code == 200
    assert r.json()["mode"] == session_permissions.MODE_FULL_ACCESS


# ── End to end against the real flagship sidecar app ───────────────────────


class _GatedAgent:
    """Asks to run one gated tool when told to, and records the answer."""

    def __init__(self, **_kwargs):
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.approved: list = []

    def process_query(self, query, max_steps=None):
        if query == "gated":
            self.approved.append(
                self.console.confirm_tool_execution(
                    "run_shell_command", {"command": "mkdir out"}
                )
            )
        return {"answer": "ok"}

    def close(self):
        pass


@pytest.fixture
def live_gaia_sidecar(monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("gaia_agent")
    import requests
    from gaia_agent import caller_auth
    from gaia_agent import server as server_mod
    from gaia_agent import session_registry as sr
    from gaia_agent import stdio

    from gaia.daemon.sidecars.manager import find_free_port

    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    # An unanswered prompt is denied after this long instead of 15 minutes.
    monkeypatch.setattr(stdio, "ORPHANED_CONFIRM_TIMEOUT_SECONDS", 1.0)
    agents: list = []

    def build(**kw):
        agents.append(_GatedAgent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    monkeypatch.setattr(server_mod, "_warmup_blocking", lambda: None)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()

    port = find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            server_mod.build_app(), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                break
        except requests.exceptions.RequestException:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("flagship sidecar test server never became reachable")
    try:
        yield base_url, agents
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sr.registry.clear()
        caller_auth.reset()


def test_the_flagship_follows_the_chats_mode_turn_by_turn(live_gaia_sidecar):
    from gaia.ui.email_sidecar.proxy import SidecarProxy

    base_url, agents = live_gaia_sidecar
    proxy = SidecarProxy(base_url, agent_id="gaia")
    session_id = f"s-{uuid.uuid4().hex[:8]}"

    def turn(query, full_access):
        return _relay(proxy, full_access, session_id=session_id, query=query)[1]

    # A new sidecar session: full access lands once /query has created it.
    turn("hello", True)
    events = turn("gated", True)
    assert agents[-1].approved == [True]
    assert not [e for e in events if e.get("type") == "needs_confirmation"]

    # Back to Ask: the next gated tool asks, and unanswered it is denied.
    events = turn("gated", False)
    assert agents[-1].approved == [True, False]
    assert [e for e in events if e.get("type") == "needs_confirmation"]
