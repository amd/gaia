# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Starting a session or switching its model must not stall other runs.

``/query`` set up its turn on the event loop: building a session's agent, or a
``/model`` switch building its new client, froze every other run's SSE stream
for as long as it took, heartbeats included, and the TUI and relay idle timers
kept counting.
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
        if kw.get("ui_session_id") in ("slow-session", None):
            time.sleep(_SLOW_SETUP_S)  # a cold agent build, or a one-shot
        agent = _Agent(**kw)
        if kw.get("ui_session_id") != "busy-session":
            agent.release.set()  # only the streaming session parks its turn
        agents[kw.get("ui_session_id")] = agent
        return agent

    def slow_switch(self, target):
        time.sleep(_SLOW_SETUP_S)  # a slow client build or server check
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


def test_a_one_shot_build_does_not_stall_other_streams(app):
    client, agents = app

    def one_shot():
        body = _body(None)
        del body["session_id"]
        with client.stream("POST", "/v1/gaia/query", json=body) as r:
            r.read()
        return r.status_code

    status, took, gap = _measure_while(client, agents, one_shot)

    assert status == 200
    assert took >= _SLOW_SETUP_S
    assert gap < 1.0, f"heartbeats stopped for {gap:.2f}s during a one-shot build"


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


def test_a_failed_release_of_an_abandoned_setup_is_logged(monkeypatch):
    """A release that raises on its cleanup thread must reach the sidecar log."""
    import asyncio

    logged = threading.Event()
    messages = []

    class _Logger:
        def exception(self, msg, *args):
            messages.append(msg)
            logged.set()

    monkeypatch.setattr(server_mod, "logger", _Logger())

    def broken_release(_result):
        raise RuntimeError("lock already released")

    async def cancelled_request():
        task = asyncio.ensure_future(
            server_mod._off_loop(lambda: time.sleep(0.2), on_abandon=broken_release)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.5)

    asyncio.run(cancelled_request())

    assert logged.wait(timeout=5)
    assert "abandoned" in messages[0]


# ---------------------------------------------------------------------------
# The window between finding a session and taking its turn
# ---------------------------------------------------------------------------


class _Quiet:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self.closed = 0

    def process_query(self, query, max_steps=None):
        return {"answer": "ok"}

    def close(self):
        self.closed += 1


@pytest.fixture
def capped(monkeypatch):
    """A one-session registry and a route test client."""
    from fastapi.testclient import TestClient

    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setattr(sr, "build_session_agent", lambda **kw: _Quiet(**kw))
    monkeypatch.setattr(server_mod, "build_query_agent", lambda **kw: _Quiet(**kw))
    reg = sr._SessionRegistry(max_sessions=1)
    monkeypatch.setattr(server_mod, "session_registry", reg)
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield reg, TestClient(server_mod.build_app(), base_url="http://127.0.0.1:8141")
    finally:
        reg.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def test_another_session_cannot_evict_this_one_mid_setup(capped, monkeypatch):
    """At the cap, a newcomer arriving while this turn sets up must not evict it.

    The newcomer is injected right after the route resolves its session, which
    is where a worker thread's await let it in.
    """
    reg, client = capped
    reg.get_or_create("mine")
    intruder: dict = {}

    def arrive():
        try:
            reg.get_or_create("newcomer")
            intruder["result"] = "evicted mine"
        except sr.SessionCapacityError:
            intruder["result"] = "refused"

    for name in ("get_or_create", "checkout"):
        original = getattr(reg, name, None)
        if original is None:  # a registry without checkout() (before the fix)
            continue

        def wrapped(*args, _original=original, **kwargs):
            found = _original(*args, **kwargs)
            if args and args[0] == "mine" and "result" not in intruder:
                arrive()
            return found

        monkeypatch.setattr(reg, name, wrapped)

    r = client.post("/v1/gaia/query", json=_body("mine"))

    assert r.status_code == 200, r.text
    assert intruder["result"] == "refused"


def _run_query_then_cancel(request_body, cancel_after_s):
    """Drive the route coroutine directly and cancel it mid-setup."""
    import asyncio

    async def go():
        task = asyncio.ensure_future(
            server_mod.query(server_mod.QueryRequest(**request_body))
        )
        await asyncio.sleep(cancel_after_s)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Outlive the worker so its cleanup can run.
        await asyncio.sleep(_SLOW_SETUP_S + 0.5)

    asyncio.run(go())


def test_a_cancelled_model_switch_releases_the_session(capped, monkeypatch):
    reg, client = capped
    session = reg.get_or_create("switching")

    def slow_switch(self, target):
        time.sleep(_SLOW_SETUP_S)
        self.model_id = target
        return target

    monkeypatch.setattr(sr._AgentSession, "switch_model", slow_switch)

    _run_query_then_cancel(_body("switching", model="other-model"), 0.3)

    assert not session.run_lock.locked(), "a cancelled switch kept the turn"
    assert client.post("/v1/gaia/query", json=_body("switching")).status_code == 200


def test_a_cancelled_one_shot_build_closes_the_agent(capped, monkeypatch):
    built = []

    def slow_build(**kw):
        time.sleep(_SLOW_SETUP_S)
        agent = _Quiet(**kw)
        built.append(agent)
        return agent

    monkeypatch.setattr(server_mod, "build_query_agent", slow_build)
    body = _body(None)
    del body["session_id"]

    _run_query_then_cancel(body, 0.3)

    assert built and built[0].closed == 1, "the abandoned one-shot agent leaked"


def test_a_cancelled_wait_for_a_stopped_turn_releases_the_lock(capped):
    reg, client = capped
    session = reg.get_or_create("stopping")
    stopped = threading.Event()
    stopped.set()
    session.active_cancel = stopped
    session.run_lock.acquire()  # the stopped turn, still winding down
    threading.Timer(1.0, session.run_lock.release).start()

    _run_query_then_cancel(_body("stopping"), 0.3)

    assert not session.run_lock.locked(), "the abandoned wait kept the turn"
