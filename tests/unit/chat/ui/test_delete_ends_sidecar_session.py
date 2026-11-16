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
from gaia.ui.email_sidecar import daemon_client
from gaia.ui.routers import sessions as sessions_router
from gaia.ui.server import create_app


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
