# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deleting a relayed chat ends its sidecar session, stopping its MCP servers.

Each chat on the flagship sidecar holds its own agent, and the agent holds the
MCP servers it started. Without the call the servers ran until the session
idled out, hours after the chat was gone.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gaia.daemon.sidecars.errors import SidecarError, SidecarHTTPError
from gaia.ui import session_environment
from gaia.ui.routers import sessions as sessions_router
from gaia.ui.server import create_app
from gaia.ui.sidecar import daemon_client


class _Proxy:
    def __init__(self, raises=None):
        self.ended = []
        self.raises = raises

    def end_session(self, session_id):
        self.ended.append(session_id)
        if self.raises is not None:
            raise self.raises
        return {"session_id": session_id, "deleted": True}


def _handle(proxy, api_version="2.30"):
    return SimpleNamespace(api_version=api_version, proxy=lambda: proxy)


@pytest.fixture
def running(monkeypatch):
    """Patch the attach-only lookup; returns the setter."""
    calls = []

    def use(handle):
        def running_handle(agent_id):
            calls.append(agent_id)
            return handle

        monkeypatch.setattr(daemon_client, "running_handle", running_handle)
        return calls

    return use


def test_ends_the_session_on_a_running_sidecar(running):
    proxy = _Proxy()
    calls = running(_handle(proxy))

    sessions_router._end_sidecar_session("chat-1", "gaia")

    assert calls == ["gaia"]
    assert proxy.ended == ["chat-1"]


def test_no_running_sidecar_is_left_alone(running):
    calls = running(None)
    sessions_router._end_sidecar_session("chat-1", "gaia")
    assert calls == ["gaia"]


def test_an_older_sidecar_is_not_asked(running):
    proxy = _Proxy()
    running(_handle(proxy, api_version="2.29"))
    sessions_router._end_sidecar_session("chat-1", "gaia")
    assert proxy.ended == []


def test_an_agent_without_the_route_is_not_looked_up(running):
    calls = running(_handle(_Proxy()))
    sessions_router._end_sidecar_session("chat-1", "email")
    assert calls == []


def test_a_session_the_sidecar_never_had_is_quiet(running, caplog):
    proxy = _Proxy(raises=SidecarHTTPError(404, "gone"))
    running(_handle(proxy))
    sessions_router._end_sidecar_session("chat-1", "gaia")
    assert "Could not end" not in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        SidecarHTTPError(409, "running a turn"),
        SidecarError("could not reach the gaia sidecar"),
    ],
)
def test_a_failure_is_logged_not_raised(running, caplog, error):
    running(_handle(_Proxy(raises=error)))
    sessions_router._end_sidecar_session("chat-1", "gaia")
    assert "Could not end deleted chat chat-1" in caplog.text


@pytest.mark.parametrize("relayed", [True, False])
def test_the_delete_route_ends_only_a_relayed_chat(monkeypatch, relayed):
    ended = []
    monkeypatch.setattr(session_environment, "relayed", lambda session: relayed)
    monkeypatch.setattr(
        sessions_router,
        "_end_sidecar_session",
        lambda sid, agent_type: ended.append((sid, agent_type)),
    )
    client = TestClient(create_app(db_path=":memory:"))
    sid = client.post("/api/sessions", json={"title": "t"}).json()["id"]

    r = client.delete(f"/api/sessions/{sid}")

    assert r.status_code == 200, r.text
    assert len(ended) == (1 if relayed else 0)
    if relayed:
        assert ended[0][0] == sid


def test_running_handle_never_starts_a_daemon(monkeypatch):
    monkeypatch.setattr(daemon_client, "attach", lambda: None)
    monkeypatch.setattr(
        daemon_client,
        "start_or_attach",
        lambda: pytest.fail("a lookup must not start the daemon"),
    )
    assert daemon_client.running_handle("gaia") is None


def test_running_handle_skips_a_stopped_sidecar(monkeypatch):
    inst = SimpleNamespace(base_url="http://127.0.0.1:1", token="t", api_version="1.4")
    monkeypatch.setattr(daemon_client, "attach", lambda: inst)
    listing = SimpleNamespace(
        status_code=200,
        json=lambda: {"agents": [{"agent_id": "gaia", "state": "stopped"}]},
    )
    monkeypatch.setattr(daemon_client.requests, "get", lambda *a, **k: listing)
    monkeypatch.setattr(
        daemon_client,
        "_ensure",
        lambda *a: pytest.fail("a stopped sidecar must not be started"),
    )
    assert daemon_client.running_handle("gaia") is None


def _daemon(monkeypatch, agents=None, get_raises=None):
    inst = SimpleNamespace(base_url="http://127.0.0.1:1", token="t", api_version="1.4")
    monkeypatch.setattr(daemon_client, "attach", lambda: inst)

    def get(*a, **k):
        if get_raises is not None:
            raise get_raises
        return SimpleNamespace(status_code=200, json=lambda: {"agents": agents})

    monkeypatch.setattr(daemon_client.requests, "get", get)
    ensured = []
    monkeypatch.setattr(
        daemon_client, "_ensure", lambda inst, agent_id: ensured.append(agent_id)
    )
    return ensured


def test_running_handle_ignores_another_running_agent(monkeypatch):
    ensured = _daemon(
        monkeypatch,
        agents=[
            {"agent_id": "email", "state": "running"},
            {"agent_id": "gaia-other", "state": "running"},
        ],
    )
    assert daemon_client.running_handle("gaia") is None
    assert ensured == []


def test_running_handle_routes_through_the_relay_without_ensure(monkeypatch):
    ensured = _daemon(
        monkeypatch,
        agents=[{"agent_id": "gaia", "state": "running", "api_version": "2.30"}],
    )
    handle = daemon_client.running_handle("gaia")
    # ensure would start a stopped agent or restart one on the wrong Lemonade.
    assert ensured == []
    assert (handle.base_url, handle.token, handle.api_version) == (
        "http://127.0.0.1:1",
        "t",
        "2.30",
    )


def test_running_handle_raises_a_sidecar_error_when_the_daemon_drops(monkeypatch):
    import requests

    _daemon(monkeypatch, get_raises=requests.exceptions.ConnectionError("refused"))
    with pytest.raises(SidecarError, match="could not reach the daemon"):
        daemon_client.running_handle("gaia")


def test_end_session_targets_only_this_chat():
    from gaia.ui.sidecar.proxy import SidecarProxy

    seen = []

    class _Session:
        def delete(self, url, timeout):
            seen.append(url)
            return SimpleNamespace(
                status_code=200, json=lambda: {"session_id": "c", "deleted": True}
            )

    SidecarProxy(
        "http://127.0.0.1:1/", agent_id="gaia", session=_Session()
    ).end_session("chat-a")
    assert seen == ["http://127.0.0.1:1/v1/gaia/sessions/chat-a"]


def _relayed_app(monkeypatch):
    monkeypatch.setattr(session_environment, "relayed", lambda session: True)
    client = TestClient(create_app(db_path=":memory:"))
    sid = client.post("/api/sessions", json={"title": "t", "agent_type": "gaia"})
    return client, sid.json()["id"]


@pytest.mark.parametrize(
    "error",
    [
        SidecarError("could not reach the daemon"),
        SidecarHTTPError(409, "running a turn"),
        SidecarHTTPError(500, "boom"),
    ],
)
def test_a_sidecar_failure_never_fails_the_chat_delete(monkeypatch, caplog, error):
    def running_handle(agent_id):
        raise error

    monkeypatch.setattr(daemon_client, "running_handle", running_handle)
    client, sid = _relayed_app(monkeypatch)

    r = client.delete(f"/api/sessions/{sid}")

    assert r.status_code == 200, r.text
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert f"Could not end deleted chat {sid}" in caplog.text


def test_the_sidecar_is_asked_only_after_the_chat_is_gone(monkeypatch):
    seen = []

    def end(sid, agent_type):
        seen.append(client.get(f"/api/sessions/{sid}").status_code)

    monkeypatch.setattr(sessions_router, "_end_sidecar_session", end)
    client, sid = _relayed_app(monkeypatch)

    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert seen == [404]


def test_deleting_one_chat_never_ends_another(monkeypatch):
    proxy = _Proxy()
    monkeypatch.setattr(daemon_client, "running_handle", lambda a: _handle(proxy))
    client, keep = _relayed_app(monkeypatch)
    gone = client.post("/api/sessions", json={"title": "u", "agent_type": "gaia"})
    gone = gone.json()["id"]

    assert client.delete(f"/api/sessions/{gone}").status_code == 200

    assert proxy.ended == [gone]
    assert client.get(f"/api/sessions/{keep}").status_code == 200


# --- A delete must never start, stop or restart the agent -------------------


def _listing(agents):
    return SimpleNamespace(status_code=200, json=lambda: {"agents": agents})


def _response(status_code, body=None, text=""):
    def json():
        if body is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return body

    return SimpleNamespace(status_code=status_code, json=json, text=text)


_RUNNING = [{"agent_id": "gaia", "state": "running", "api_version": "2.30"}]


@pytest.fixture
def live_daemon(monkeypatch):
    """A live daemon whose start/ensure/stop calls all fail the test.

    Returns ``(set_state, deletes)``. ``set_state`` picks the agents listing and
    the end-session reply; ``deletes`` records each DELETE the backend sends as
    ``(url, timeout, authorization)``.
    """
    import requests

    daemon = {"base_url": "http://127.0.0.1:1"}
    monkeypatch.setattr(
        daemon_client,
        "attach",
        lambda: SimpleNamespace(
            base_url=daemon["base_url"], token="dt", api_version="1.4"
        ),
    )
    monkeypatch.setattr(
        daemon_client,
        "start_or_attach",
        lambda: pytest.fail("a chat delete must not start the daemon"),
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, *a, **k: pytest.fail(f"a chat delete must not POST {url}"),
    )
    state = {
        "listing": _listing([]),
        "reply": _response(200, {"session_id": "x", "deleted": True}),
    }
    monkeypatch.setattr(requests, "get", lambda *a, **k: state["listing"])
    deletes = []

    def delete(self, url, timeout=None, **kwargs):
        deletes.append((url, timeout, self.headers.get("Authorization")))
        reply = state["reply"]
        if callable(reply):
            return reply(self, url, timeout=timeout, **kwargs)
        return reply

    monkeypatch.setattr(requests.Session, "delete", delete)

    def set_state(agents=None, listing=None, reply=None, base_url=None):
        if listing is not None:
            state["listing"] = listing
        elif agents is not None:
            state["listing"] = _listing(agents)
        if reply is not None:
            state["reply"] = reply
        if base_url is not None:
            daemon["base_url"] = base_url

    return set_state, deletes


def _unended(caplog, sid):
    records = [
        r
        for r in caplog.records
        if f"Could not end deleted chat {sid}" in r.getMessage()
    ]
    assert len(records) == 1, caplog.text
    return records[0]


@pytest.mark.parametrize(
    "agents",
    [
        [],
        [{"agent_id": "gaia", "state": "stopped", "api_version": None}],
        [{"agent_id": "gaia", "state": "starting", "api_version": "2.30"}],
        [{"agent_id": "email", "state": "running", "api_version": "2.30"}],
    ],
)
def test_a_delete_never_starts_an_agent_that_is_not_running(
    monkeypatch, live_daemon, agents
):
    set_state, deletes = live_daemon
    set_state(agents=agents)
    client, sid = _relayed_app(monkeypatch)

    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert deletes == []


def test_a_delete_goes_through_the_relay_never_ensure(monkeypatch, live_daemon):
    # ensure is the only path that restarts an agent on another Lemonade, so a
    # delete that never POSTs to the daemon cannot restart it.
    from gaia.ui.sidecar import proxy as proxy_mod

    set_state, deletes = live_daemon
    set_state(agents=_RUNNING)
    client, sid = _relayed_app(monkeypatch)

    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert deletes == [
        (
            f"http://127.0.0.1:1/v1/gaia/sessions/{sid}",
            proxy_mod._END_SESSION_TIMEOUT,
            "Bearer dt",
        )
    ]


def test_end_session_timeout_matches_the_other_best_effort_calls():
    from gaia.ui.sidecar import proxy as proxy_mod

    assert proxy_mod._END_SESSION_TIMEOUT == proxy_mod._CANCEL_TIMEOUT
    assert proxy_mod._END_SESSION_TIMEOUT == proxy_mod._SWITCH_TIMEOUT


def test_an_agent_that_exits_before_the_call_is_not_restarted(
    monkeypatch, live_daemon, caplog
):
    set_state, deletes = live_daemon
    # The relay's answer once the listed sidecar is gone.
    set_state(
        agents=_RUNNING,
        reply=_response(503, {"detail": "agent 'gaia' has no running sidecar"}),
    )
    client, sid = _relayed_app(monkeypatch)

    with caplog.at_level("INFO"):
        assert client.delete(f"/api/sessions/{sid}").status_code == 200

    assert len(deletes) == 1
    assert _unended(caplog, sid).levelname == "WARNING"


def test_a_delete_mid_answer_is_logged_below_error(monkeypatch, live_daemon, caplog):
    set_state, _ = live_daemon
    set_state(agents=_RUNNING, reply=_response(409, {"detail": "running a turn"}))
    client, sid = _relayed_app(monkeypatch)

    with caplog.at_level("INFO"):
        assert client.delete(f"/api/sessions/{sid}").status_code == 200

    assert _unended(caplog, sid).levelname == "WARNING"
    assert client.get(f"/api/sessions/{sid}").status_code == 404


@pytest.mark.parametrize(
    "listing, reply, expected",
    [
        (_response(200, None, text="<html>proxy</html>"), None, "not JSON"),
        (_response(200, ["not", "an", "object"]), None, "unexpected shape"),
        (_response(200, {"agents": "gaia"}), None, "unexpected shape"),
        (_response(200, {"agents": ["gaia"]}), None, "unexpected shape"),
        (None, _response(200, None, text="<html>ok</html>"), "not JSON"),
        (None, _response(200, ["deleted"]), "not a JSON object"),
    ],
)
def test_a_malformed_reply_never_fails_the_delete(
    monkeypatch, live_daemon, caplog, listing, reply, expected
):
    set_state, _ = live_daemon
    set_state(agents=_RUNNING, listing=listing, reply=reply)
    client, sid = _relayed_app(monkeypatch)

    r = client.delete(f"/api/sessions/{sid}")

    assert r.status_code == 200, r.text
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    record = _unended(caplog, sid)
    assert record.levelname == "ERROR"
    assert expected in record.getMessage()


@pytest.mark.allow_network
def test_a_hung_agent_cannot_stall_the_delete(monkeypatch, live_daemon, caplog):
    import socket
    import threading
    import time

    import requests

    from gaia.ui.sidecar import proxy as proxy_mod

    # A real socket that accepts and never answers.
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    server.settimeout(0.2)
    accepted = []
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                accepted.append(server.accept()[0])
            except OSError:
                continue

    threading.Thread(target=accept, daemon=True).start()
    set_state, deletes = live_daemon
    set_state(
        agents=_RUNNING,
        base_url=f"http://127.0.0.1:{server.getsockname()[1]}",
        # The real transport, so only the timeout can end the call.
        reply=lambda self, url, **k: requests.Session.request(self, "DELETE", url, **k),
    )
    monkeypatch.setattr(proxy_mod, "_END_SESSION_TIMEOUT", 0.5)
    client, sid = _relayed_app(monkeypatch)

    try:
        with caplog.at_level("INFO"):
            started = time.monotonic()
            r = client.delete(f"/api/sessions/{sid}")
            elapsed = time.monotonic() - started
    finally:
        stop.set()
        for conn in accepted:
            conn.close()
        server.close()

    assert r.status_code == 200, r.text
    assert accepted, "the end-session call never reached the hung agent"
    assert deletes[0][1] == 0.5
    assert elapsed < 5.0
    assert _unended(caplog, sid).levelname == "WARNING"
