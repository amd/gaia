# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A chat's Internet switch reaches a relayed GAIA agent (#5883).

On a release install the flagship runs as its own program and every turn is
relayed to it. The relay used to drop the chat's Internet setting, so the
switch was locked in every GAIA chat. These check what leaves for the sidecar,
what a sidecar too old to take it is told, what the session panel reports, and
that a switch flipped mid-turn reaches the turn already running.
"""

from __future__ import annotations

import asyncio
import functools
import json
import threading
import time
from types import SimpleNamespace

import pytest
import requests

import gaia.ui._chat_helpers as helpers
from gaia.agents.base.agent import Agent
from gaia.ui import session_environment, turn_insights
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar import daemon_client, relay
from gaia.ui.email_sidecar.errors import SidecarError
from gaia.ui.email_sidecar.profiles import (
    EMAIL_PROFILE,
    GAIA_PROFILE,
    internet_supported,
)
from gaia.ui.email_sidecar.proxy import SidecarProxy
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler


@pytest.mark.parametrize(
    "version, expected", [("2.22", True), ("3.0", True), ("2.21", False), (None, False)]
)
def test_only_a_flagship_at_2_22_takes_the_switch(version, expected):
    assert internet_supported(GAIA_PROFILE, version) is expected


def test_email_never_takes_the_switch():
    assert EMAIL_PROFILE.min_internet_api_version is None
    assert internet_supported(EMAIL_PROFILE, "9.9") is False


class _Proxy:
    agent_id = "gaia"

    def __init__(self):
        self.bodies: list = []

    def init(self):
        return 200, {}

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        yield {"type": "final", "answer": "ok"}

    def cancel_query(self, _rid):
        pass


def _events(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _dispatch(monkeypatch, version, internet_off, agent_type="gaia"):
    proxy = _Proxy()
    handle = SimpleNamespace(api_version=version, proxy=lambda: proxy)
    monkeypatch.setattr(daemon_client, "acquire_handle", lambda _id: handle)
    monkeypatch.setattr(helpers, "_replay_cloud_key_for_relay", lambda *_a: None)
    handler = SSEOutputHandler()
    helpers._dispatch_sidecar_query(
        handler,
        ChatRequest(session_id="s", message="weather today", agent_type=agent_type),
        [],
        None,
        agent_type,
        internet_off=internet_off,
    )
    return proxy.bodies, _events(handler)


@pytest.mark.parametrize("internet_off", [True, False])
def test_a_current_flagship_is_told_the_switch_every_turn(monkeypatch, internet_off):
    bodies, events = _dispatch(monkeypatch, "2.22", internet_off)
    [body] = bodies
    assert body["internet_off"] is internet_off
    assert not [e for e in events if e["type"] == "agent_error"]


def test_an_internet_off_turn_is_stopped_before_a_flagship_too_old_to_honour_it(
    monkeypatch,
):
    bodies, events = _dispatch(monkeypatch, "2.21", True)
    assert bodies == []
    assert events == [
        {"type": "agent_error", "content": GAIA_PROFILE.internet_upgrade_message}
    ]


def test_an_internet_on_turn_still_runs_on_an_older_flagship(monkeypatch):
    bodies, events = _dispatch(monkeypatch, "2.21", False)
    [body] = bodies
    assert "internet_off" not in body
    assert not [e for e in events if e["type"] == "agent_error"]


def test_the_email_sidecar_is_never_sent_the_switch(monkeypatch):
    proxy = _Proxy()
    proxy.agent_id = "email"
    relay.relay_query(
        SSEOutputHandler(), proxy, query="q", context=[], profile=EMAIL_PROFILE
    )
    [body] = proxy.bodies
    assert "internet_off" not in body


def test_the_relay_omits_a_switch_it_was_not_given():
    proxy = _Proxy()
    relay.relay_query(
        SSEOutputHandler(), proxy, query="q", context=[], profile=GAIA_PROFILE
    )
    assert "internet_off" not in proxy.bodies[0]


# ── The chat's setting reaches the relay ───────────────────────────────────


@pytest.fixture
def sidecar_flagship(tmp_path, monkeypatch):
    """The registry after a binary install of the flagship: every turn relays."""
    from gaia.agents.registry import AgentRegistry
    from gaia.hub.installer import register_installed_sidecars

    root = tmp_path / "agents"
    (root / "gaia").mkdir(parents=True)
    (root / "gaia" / ".installed").write_text(
        json.dumps(
            {
                "id": "gaia",
                "version": "0.2.0",
                "language": "python",
                "installed_at": "2026-10-22T00:00:00Z",
                "artifact_sha256": "0" * 64,
                "artifact_kind": "binary",
                "executable": "gaia-agent-gaia",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.hub.installer.default_install_root", lambda: root, raising=False
    )
    registry = AgentRegistry()
    registry._agents.clear()
    register_installed_sidecars(registry)
    monkeypatch.setattr(helpers, "_agent_registry", registry)


@pytest.mark.parametrize("off", [True, False])
def test_a_streamed_chat_relays_the_switch_the_panel_saved(
    monkeypatch, sidecar_flagship, off
):
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:18767/api/v1")
    monkeypatch.setenv("LEMONADE_API_KEY", "test-lemonade-key")
    relayed = {}

    def _fake_dispatch(sse_handler, request, history_pairs, model_id, agent, **kw):
        relayed.update(kw)
        sse_handler._emit({"type": "answer", "content": "done"})

    monkeypatch.setattr(helpers, "_dispatch_sidecar_query", _fake_dispatch)
    db = ChatDatabase(":memory:")
    session = db.create_session(title="Weather", agent_type="gaia")
    session_environment.set_internet(db, session["id"], not off)
    db.begin_turn(session["id"], "weather today")
    request = ChatRequest(
        session_id=session["id"],
        message="weather today",
        stream=True,
        agent_type="gaia",
    )

    async def _drive():
        async for _ in helpers._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass
        await asyncio.gather(*list(helpers._background_tasks))

    asyncio.run(_drive())

    assert relayed["internet_off"] is off
    with relayed["hold_internet_off"]() as held:
        assert held is off


# ── The session panel ──────────────────────────────────────────────────────


@pytest.fixture
def relayed_db(monkeypatch):
    monkeypatch.setattr(session_environment, "relayed", lambda _session: True)
    monkeypatch.setattr(session_environment, "_live_agent", lambda _sid: None)
    return ChatDatabase(":memory:")


def test_a_relayed_flagship_chat_can_switch_the_internet(relayed_db):
    session = relayed_db.create_session(agent_type="gaia")

    body = session_environment.describe(relayed_db, session)
    assert body["internet_enforced"] is True
    assert body["enforced"] is False

    session_environment.set_internet(relayed_db, session["id"], False)
    assert session_environment.load(relayed_db, session["id"])["internet_off"] is True
    assert session_environment.describe(relayed_db, session)["internet"] is False


def test_a_relayed_flagship_chat_still_refuses_folder_changes(relayed_db, tmp_path):
    session = relayed_db.create_session(agent_type="gaia")
    with pytest.raises(session_environment.EnvironmentChangeError):
        session_environment.add_folder(relayed_db, session["id"], str(tmp_path))


def test_a_relayed_email_chat_keeps_its_own_network_rules(relayed_db, running_relays):
    session = relayed_db.create_session(agent_type="email")
    http = _running_relay(session["id"])

    assert (
        session_environment.describe(relayed_db, session)["internet_enforced"] is False
    )

    with pytest.raises(session_environment.EnvironmentChangeError) as refusal:
        session_environment.set_internet(relayed_db, session["id"], False)
    assert "network rules" in str(refusal.value)
    assert "start a chat" not in str(refusal.value).lower()
    assert session_environment.load(relayed_db, session["id"])["internet_off"] is False
    assert http.posts == []


def test_an_in_process_chat_enforces_both(monkeypatch):
    monkeypatch.setattr(session_environment, "relayed", lambda _session: False)
    monkeypatch.setattr(session_environment, "_live_agent", lambda _sid: None)
    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="gaia")

    body = session_environment.describe(db, session)

    assert body["internet_enforced"] is True
    assert body["enforced"] is True


# ── A switch flipped while a relayed turn is running ───────────────────────


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _Http:
    """Stands in for the proxy's ``requests`` session and keeps what it sent."""

    def __init__(self, status_code=200, error=None):
        self.headers: dict = {}
        self.posts: list = []
        self._status_code = status_code
        self._error = error

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        if self._error is not None:
            raise self._error
        if self._status_code >= 400:
            return _Response(self._status_code, {"detail": "No session exists."})
        return _Response(200, {"session_id": "x", **json})


@pytest.fixture
def running_relays():
    yield
    helpers._active_sse_handlers.clear()


def _running_relay(session_id, http=None, internet_session=True):
    """Registers a relayed turn of *session_id* in flight on the GAIA sidecar."""
    http = http or _Http()
    handler = SSEOutputHandler()
    handler.active_relay_proxy = SidecarProxy(
        "http://127.0.0.1:9", agent_id="gaia", session=http
    )
    handler.active_relay_run_id = "r1"
    handler.active_relay_internet_session = session_id if internet_session else None
    helpers._active_sse_handlers[session_id] = handler
    return http


def _no_sidecar_start(monkeypatch):
    def refuse(_agent_id):
        raise AssertionError("a settings change must not start a sidecar")

    monkeypatch.setattr(daemon_client, "acquire_handle", refuse)


@pytest.mark.parametrize("on", [False, True])
def test_a_running_flagship_turn_is_told_the_switch(relayed_db, running_relays, on):
    session = relayed_db.create_session(agent_type="gaia")
    http = _running_relay(session["id"])

    session_environment.set_internet(relayed_db, session["id"], on)

    assert http.posts == [
        (
            f"http://127.0.0.1:9/v1/gaia/sessions/{session['id']}/internet",
            {"internet_off": not on},
        )
    ]


def test_with_no_turn_running_the_switch_waits_for_the_next_one(
    relayed_db, running_relays, monkeypatch
):
    _no_sidecar_start(monkeypatch)
    session = relayed_db.create_session(agent_type="gaia")

    session_environment.set_internet(relayed_db, session["id"], False)

    assert session_environment.load(relayed_db, session["id"])["internet_off"] is True


def test_a_running_turn_that_never_took_the_switch_is_not_sent_it(
    relayed_db, running_relays, monkeypatch
):
    _no_sidecar_start(monkeypatch)
    session = relayed_db.create_session(agent_type="gaia")
    http = _running_relay(session["id"], internet_session=False)

    session_environment.set_internet(relayed_db, session["id"], False)

    assert http.posts == []


@pytest.mark.parametrize(
    "http",
    [
        pytest.param(_Http(status_code=404), id="sidecar-404"),
        pytest.param(
            _Http(error=requests.ConnectionError("refused")), id="sidecar-gone"
        ),
    ],
)
def test_a_running_turn_that_cannot_be_told_offline_is_stopped(
    relayed_db, running_relays, http
):
    session = relayed_db.create_session(agent_type="gaia")
    _running_relay(session["id"], http)
    handler = helpers._active_sse_handlers[session["id"]]

    with pytest.raises(session_environment.RelayedSwitchError) as failure:
        session_environment.set_internet(relayed_db, session["id"], False)

    assert "off from the next message" in str(failure.value)
    assert "the Agent UI ended its turn" in str(failure.value)
    assert handler.cancelled.is_set()
    assert session_environment.load(relayed_db, session["id"])["internet_off"] is True


def test_a_running_turn_that_cannot_be_told_online_stays_offline(
    relayed_db, running_relays
):
    session = relayed_db.create_session(agent_type="gaia")
    session_environment.set_internet(relayed_db, session["id"], False)
    _running_relay(session["id"], _Http(status_code=404))
    handler = helpers._active_sse_handlers[session["id"]]

    with pytest.raises(session_environment.RelayedSwitchError) as failure:
        session_environment.set_internet(relayed_db, session["id"], True)

    assert "on from the next message" in str(failure.value)
    assert "stays offline" in str(failure.value)
    assert not handler.cancelled.is_set()
    assert session_environment.load(relayed_db, session["id"])["internet_off"] is False


def test_the_relay_names_its_session_only_while_a_turn_that_took_the_switch_runs(
    relayed_db,
):
    session = relayed_db.create_session(agent_type="gaia")
    hold = functools.partial(
        session_environment.held_internet_off, relayed_db, session["id"]
    )
    seen = {}

    class _Watching(_Proxy):
        def query_stream(self, body, read_timeout, on_response):
            seen["at_post"] = self.handler.active_relay_internet_session
            return self._events()

        def _events(self):
            seen["running"] = self.handler.active_relay_internet_session
            yield {"type": "final", "answer": "ok"}

    for internet_off, held, expected in (
        (False, hold, session["id"]),
        (None, hold, None),
        (False, None, None),
    ):
        proxy = _Watching()
        proxy.handler = SSEOutputHandler()
        relay.relay_query(
            proxy.handler,
            proxy,
            query="q",
            context=[],
            profile=GAIA_PROFILE,
            session_id=session["id"],
            internet_off=internet_off,
            hold_internet_off=held,
        )
        # Not before the sidecar has applied the body: a switch sent then is lost.
        assert seen["at_post"] is None
        assert seen["running"] == expected
        assert proxy.handler.active_relay_internet_session is None


class _Sidecar(_Proxy):
    """Applies the body before /query answers, as the flagship does."""

    def __init__(self, fail=None):
        super().__init__()
        self.calls: list = []
        self.internet_off = None
        self._fail = fail

    def query_stream(self, body, read_timeout, on_response):
        self.calls.append("query")
        self.internet_off = body.get("internet_off")
        return iter([{"type": "final", "answer": "ok"}])

    def set_internet(self, session_id, internet_off):
        self.calls.append(("internet", session_id, internet_off))
        if self._fail is not None:
            raise SidecarError(self._fail)
        self.internet_off = internet_off


def _start_turn(db, session_id, sidecar, sent_internet_off):
    """Runs a relayed turn that read *sent_internet_off* when it was dispatched."""
    handler = SSEOutputHandler()
    relay.relay_query(
        handler,
        sidecar,
        query="weather today",
        context=[],
        profile=GAIA_PROFILE,
        session_id=session_id,
        internet_off=sent_internet_off,
        hold_internet_off=functools.partial(
            session_environment.held_internet_off, db, session_id
        ),
    )
    return _events(handler)


@pytest.mark.parametrize("on", [False, True])
def test_a_switch_flipped_while_the_turn_started_reaches_it(relayed_db, on):
    session = relayed_db.create_session(agent_type="gaia")
    session_environment.set_internet(relayed_db, session["id"], not on)
    sidecar = _Sidecar()
    # Dispatch read the old value; the user flipped it before the turn was running.
    session_environment.set_internet(relayed_db, session["id"], on)

    events = _start_turn(relayed_db, session["id"], sidecar, sent_internet_off=on)

    assert sidecar.calls == ["query", ("internet", session["id"], not on)]
    assert sidecar.internet_off is (not on)
    assert not [e for e in events if e["type"] == "agent_error"]


def test_a_turn_that_started_on_the_saved_switch_is_not_told_again(relayed_db):
    session = relayed_db.create_session(agent_type="gaia")
    sidecar = _Sidecar()

    _start_turn(relayed_db, session["id"], sidecar, sent_internet_off=False)

    assert sidecar.calls == ["query"]


def test_a_turn_that_cannot_be_told_it_went_offline_while_starting_is_ended(
    relayed_db,
):
    session = relayed_db.create_session(agent_type="gaia")
    session_environment.set_internet(relayed_db, session["id"], False)
    sidecar = _Sidecar(fail="gone")

    events = _start_turn(relayed_db, session["id"], sidecar, sent_internet_off=False)

    assert not [e for e in events if e["type"] == "answer"]
    [error] = [e for e in events if e["type"] == "agent_error"]
    assert "turned off as this message started" in error["content"]
    assert "the Agent UI ended this turn" in error["content"]


def test_a_turn_that_cannot_be_told_it_went_online_while_starting_stays_offline(
    relayed_db,
):
    session = relayed_db.create_session(agent_type="gaia")
    session_environment.set_internet(relayed_db, session["id"], False)
    sidecar = _Sidecar(fail="gone")
    session_environment.set_internet(relayed_db, session["id"], True)

    events = _start_turn(relayed_db, session["id"], sidecar, sent_internet_off=True)

    assert not [e for e in events if e["type"] == "agent_error"]
    [warning] = [e for e in events if e.get("status") == "warning"]
    assert "stays offline" in warning["message"]


def test_two_quick_opposite_flips_leave_the_running_turn_on_the_saved_one(
    relayed_db, running_relays
):
    session = relayed_db.create_session(agent_type="gaia")
    http = _running_relay(session["id"])
    second: list = []
    real_save = relayed_db.set_session_environment

    def save_then_let_the_second_flip_race(sid, **kw):
        real_save(sid, **kw)
        if not second:
            second.append(
                threading.Thread(
                    target=session_environment.set_internet,
                    args=(relayed_db, sid, False),
                )
            )
            second[0].start()
            # Long enough for an unguarded second flip to save and send first.
            second[0].join(timeout=0.5)

    relayed_db.set_session_environment = save_then_let_the_second_flip_race
    session_environment.set_internet(relayed_db, session["id"], True)
    second[0].join(timeout=5)

    saved = session_environment.load(relayed_db, session["id"])["internet_off"]
    assert saved is True
    assert http.posts[-1][1] == {"internet_off": saved}


def test_a_switch_that_fails_after_the_turn_ended_stops_nothing(
    relayed_db, running_relays, caplog
):
    session = relayed_db.create_session(agent_type="gaia")
    _running_relay(session["id"])
    handler = helpers._active_sse_handlers[session["id"]]

    class _EndsTheTurnThenFails(_Http):
        def post(self, url, json=None, timeout=None):
            handler.active_relay_internet_session = None
            helpers._active_sse_handlers.pop(session["id"], None)
            raise requests.ConnectionError("refused")

    handler.active_relay_proxy._session = _EndsTheTurnThenFails()

    with caplog.at_level("INFO"):
        session_environment.set_internet(relayed_db, session["id"], False)

    assert not handler.cancelled.is_set()
    assert "ended before it could be told" in caplog.text
    assert session_environment.load(relayed_db, session["id"])["internet_off"] is True


class _ParkedWebAgent:
    """Parks mid-turn, then tries a web search through the base agent's guard."""

    internet_off = False

    def __init__(self, gate, **_kwargs):
        self.gate = gate
        self.started = threading.Event()
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._tools_registry = {}
        self.refusals: list = []

    def process_query(self, query, max_steps=None):
        self.started.set()
        self.gate.wait(timeout=10)
        refusal = Agent._policy_refusal(self, "search_web", {"query": "weather"})
        self.refusals.append(refusal and refusal["error_type"])
        return {"answer": "ok"}

    def close(self):
        pass


@pytest.fixture
def live_flagship(monkeypatch):
    """The real flagship sidecar app on a local port, with a parked web agent."""
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("gaia_agent")
    from gaia_agent import caller_auth
    from gaia_agent import server as server_mod
    from gaia_agent import session_registry as sr

    from gaia.daemon.sidecars.manager import find_free_port

    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    gate = threading.Event()
    agents: list = []

    def build(**kw):
        agents.append(_ParkedWebAgent(gate, **kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    monkeypatch.setattr(server_mod, "_warmup_blocking", lambda: None)
    # The turn's context meter would ask the Lemonade Server on this machine.
    monkeypatch.setattr(
        turn_insights, "panel_context", lambda *_a, **_k: {"window": 65536}
    )
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
        yield SidecarProxy(base_url, agent_id="gaia"), gate, agents
    finally:
        gate.set()
        server.should_exit = True
        thread.join(timeout=5)
        sr.registry.clear()
        caller_auth.reset()


@pytest.mark.allow_network
def test_switching_off_mid_turn_stops_the_flagships_next_web_search(
    relayed_db, running_relays, live_flagship
):
    proxy, gate, agents = live_flagship
    session = relayed_db.create_session(agent_type="gaia")
    handler = SSEOutputHandler()
    helpers._active_sse_handlers[session["id"]] = handler
    turn = threading.Thread(
        target=relay.relay_query,
        kwargs=dict(
            handler=handler,
            proxy=proxy,
            query="weather today",
            context=[],
            profile=GAIA_PROFILE,
            session_id=session["id"],
            internet_off=False,
            hold_internet_off=functools.partial(
                session_environment.held_internet_off, relayed_db, session["id"]
            ),
        ),
        daemon=True,
    )
    turn.start()
    try:
        deadline = time.monotonic() + 10
        while not (agents and agents[0].started.is_set()):
            assert time.monotonic() < deadline, "the relayed turn never started"
            time.sleep(0.02)

        session_environment.set_internet(relayed_db, session["id"], False)
    finally:
        gate.set()
        turn.join(timeout=10)

    assert agents[0].refusals == ["internet_off"]
    errors = [e for e in _events(handler) if e["type"] == "agent_error"]
    assert errors == []


@pytest.mark.allow_network
@pytest.mark.parametrize(
    "flip_when",
    [
        "before_the_relay_starts",
        "during_a_first_turn_setup",
        "during_a_later_turn_setup",
    ],
)
def test_switching_off_while_a_turn_starts_stops_the_flagships_web_search(
    relayed_db, running_relays, live_flagship, monkeypatch, flip_when
):
    from gaia_agent import server as server_mod
    from gaia_agent import session_registry as sr

    proxy, gate, agents = live_flagship
    session = relayed_db.create_session(agent_type="gaia")
    if flip_when == "during_a_later_turn_setup":
        sr.registry.get_or_create(session["id"])
    in_setup = threading.Event()
    leave_setup = threading.Event()
    real_checkout = server_mod._checkout_turn

    def parked_checkout(*args, **kwargs):
        in_setup.set()
        leave_setup.wait(timeout=10)
        return real_checkout(*args, **kwargs)

    monkeypatch.setattr(server_mod, "_checkout_turn", parked_checkout)
    handler = SSEOutputHandler()
    helpers._active_sse_handlers[session["id"]] = handler
    turn = threading.Thread(
        target=relay.relay_query,
        kwargs=dict(
            handler=handler,
            proxy=proxy,
            query="weather today",
            context=[],
            profile=GAIA_PROFILE,
            session_id=session["id"],
            internet_off=False,
            hold_internet_off=functools.partial(
                session_environment.held_internet_off, relayed_db, session["id"]
            ),
        ),
        daemon=True,
    )
    try:
        if flip_when == "before_the_relay_starts":
            session_environment.set_internet(relayed_db, session["id"], False)
            turn.start()
        else:
            turn.start()
            assert in_setup.wait(timeout=10), "the sidecar never began the turn"
            session_environment.set_internet(relayed_db, session["id"], False)
        leave_setup.set()
        deadline = time.monotonic() + 10
        while not (agents and agents[0].started.is_set()):
            assert time.monotonic() < deadline, "the relayed turn never started"
            time.sleep(0.02)
    finally:
        leave_setup.set()
        gate.set()
        turn.join(timeout=10)

    assert agents[0].refusals == ["internet_off"]
    errors = [e for e in _events(handler) if e["type"] == "agent_error"]
    assert errors == []
