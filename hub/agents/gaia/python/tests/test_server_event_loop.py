# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Starting a session or switching its model must not stall other runs.

``/query`` set up its turn on the event loop: building a session's agent, or a
``/model`` switch that waits for a model to load, froze every other run's SSE
stream, heartbeats included, and the TUI and relay idle timers kept counting.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest

pytest.importorskip("gaia_agent")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import session_registry as sr  # noqa: E402

_HEARTBEAT_S = 0.1
_SLOW_SETUP_S = 1.5


class _Agent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.release = threading.Event()

    def process_query(self, query, max_steps=None):
        self.release.wait(timeout=20)
        return {"answer": f"answered: {query}"}

    def close(self):
        pass


@pytest.fixture
def app(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(server_mod, "_HEARTBEAT_SECONDS", _HEARTBEAT_S)

    agents = {}

    def build(**kw):
        if kw.get("ui_session_id") == "slow-session":
            time.sleep(_SLOW_SETUP_S)  # a cold agent build
        agent = _Agent(**kw)
        if kw.get("ui_session_id") != "busy-session":
            agent.release.set()  # only the streaming session parks its turn
        agents[kw.get("ui_session_id")] = agent
        return agent

    def slow_switch(self, target):
        time.sleep(_SLOW_SETUP_S)  # a model load behind someone else's lease
        self.model_id = target
        return target

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    monkeypatch.setattr(sr._AgentSession, "switch_model", slow_switch)
    monkeypatch.setattr(server_mod, "_warmup_blocking", lambda: None)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()

    # A real server: TestClient hands back a stream only once it has ended.
    # An ephemeral port, so parallel runs in other checkouts can't collide.
    from gaia.daemon.sidecars.manager import find_free_port

    port = find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            server_mod.build_app(), host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, f"test sidecar never started on port {port}"
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30)
    try:
        yield client, agents
    finally:
        for agent in agents.values():
            agent.release.set()
        client.close()
        server.should_exit = True
        thread.join(10)
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _body(session_id, **extra):
    return {
        "query": "hello",
        "run_id": str(uuid.uuid4()),
        "context": [],
        "session_id": session_id,
        **extra,
    }


def _stream_heartbeats(client, session_id, beats, started):
    with client.stream("POST", "/v1/gaia/query", json=_body(session_id)) as r:
        assert r.status_code == 200
        started.set()
        for line in r.iter_lines():  # httpx yields each SSE line as it lands
            if line.startswith(": keepalive"):
                beats.append(time.monotonic())


def _longest_gap(beats, start, end):
    window = [t for t in beats if start <= t <= end]
    points = [start, *window, end]
    return max(b - a for a, b in zip(points, points[1:]))


def _measure_while(client, agents, slow_request):
    beats: list = []
    started = threading.Event()
    streamer = threading.Thread(
        target=_stream_heartbeats,
        args=(client, "busy-session", beats, started),
        daemon=True,
    )
    streamer.start()
    assert started.wait(5)
    time.sleep(3 * _HEARTBEAT_S)

    t0 = time.monotonic()
    response = slow_request()
    t1 = time.monotonic()

    agents["busy-session"].release.set()
    streamer.join(10)
    return response, t1 - t0, _longest_gap(beats, t0, t1)


def test_building_a_new_session_does_not_stall_other_streams(app):
    client, agents = app

    def start_slow_session():
        with client.stream("POST", "/v1/gaia/query", json=_body("slow-session")) as r:
            r.read()
        return r.status_code

    status, took, gap = _measure_while(client, agents, start_slow_session)

    assert status == 200
    assert took >= _SLOW_SETUP_S
    assert gap < 1.0, f"heartbeats stopped for {gap:.2f}s while a session was built"


def test_switching_a_sessions_model_does_not_stall_other_streams(app):
    client, agents = app
    warm = client.post("/v1/gaia/query", json=_body("switching"))
    assert warm.status_code == 200

    def switch_model():
        with client.stream(
            "POST", "/v1/gaia/query", json=_body("switching", model="other-model")
        ) as r:
            r.read()
        return r.status_code

    status, took, gap = _measure_while(client, agents, switch_model)

    assert status == 200
    assert took >= _SLOW_SETUP_S
    assert gap < 1.0, f"heartbeats stopped for {gap:.2f}s during a model switch"


def test_an_abandoned_setup_call_still_releases_what_it_took():
    """A request cancelled mid-switch must not leave the session's lock held."""
    import asyncio

    lock = threading.Lock()
    lock.acquire()
    released = threading.Event()

    def slow_failing_switch():
        time.sleep(0.2)
        raise RuntimeError("model would not load")

    def release(_result):
        lock.release()
        released.set()

    async def cancelled_request():
        task = asyncio.ensure_future(
            server_mod._off_loop(slow_failing_switch, on_abandon=release)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The loop must outlive the worker for its done-callback to run.
        await asyncio.sleep(0.5)

    asyncio.run(cancelled_request())

    assert released.is_set()
    assert not lock.locked()
