# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/lemonade/*`` — the daemon starts the model server on request.

These routes exist so the Go TUI never spawns a server itself and never shells
out to the Python CLI: it asks the one machine-wide supervisor. The tests pin
what a front-end depends on — that the verb is guarded like every other
client-plane route, that a refusal is a 503 carrying the supervisor's own
actionable message (never a 200 that quietly means "no LLM"), and that a
request without a ctx_size gets the machine's pinned profile window rather than
the server's own small default.
"""

from __future__ import annotations

import pytest

from gaia.llm.lemonade_supervisor import LemonadeStartError, LemonadeState

# FastAPI's TestClient drives the app through an asyncio loop whose self-pipe is
# a loopback socket, which the conftest's _block_network guard refuses. Nothing
# here reaches the network: the supervisor is a fake in every test.
pytestmark = pytest.mark.allow_network

TOKEN = "daemon-client-token"
START = "/daemon/v1/lemonade/start"
STATUS = "/daemon/v1/lemonade/status"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeSupervisor:
    """Stands in for the daemon's real supervisor; records what it was asked."""

    def __init__(self, state=None, error=None):
        self.calls = []
        self._state = state or LemonadeState(
            base_url="http://localhost:13305/api/v1",
            started=True,
            owned=True,
            pid=4242,
            waited_seconds=4.2,
        )
        self._error = error
        self.is_running = True
        self.pid = 4242

    def ensure_running(self, ctx_size=None, timeout=None):
        self.calls.append(ctx_size)
        if self._error is not None:
            raise self._error
        return self._state

    def log_path(self):
        return "/tmp/lemonade.log"


def _client(supervisor):
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    return TestClient(
        create_app(
            token=TOKEN, port=51234, pid=999, started_at=0.0, lemonade=supervisor
        )
    )


@pytest.fixture()
def supervisor():
    return FakeSupervisor()


@pytest.fixture()
def client(supervisor):
    return _client(supervisor)


def test_the_route_requires_the_client_token(client):
    resp = client.post(START)
    assert resp.status_code == 401
    assert "token" in resp.json()["detail"].lower()


def test_a_stopped_server_is_started_and_reported_as_started(client, supervisor):
    resp = client.post(START, headers=AUTH, json={"ctx_size": 65536})

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "started",
        "base_url": "http://localhost:13305/api/v1",
        "ctx_size": 65536,
        "supervised": True,
        "pid": 4242,
        "waited_seconds": 4.2,
    }
    assert supervisor.calls == [65536]


def test_a_server_the_daemon_only_found_is_reported_as_unsupervised(supervisor):
    """``supervised`` is what tells a caller whether stopping the daemon takes
    the server down with it. Blurring the two would make that surprising."""
    supervisor._state = LemonadeState(
        base_url="http://localhost:13305/api/v1",
        started=False,
        owned=False,
        pid=None,
        waited_seconds=0.0,
    )
    resp = _client(supervisor).post(START, headers=AUTH, json={"ctx_size": 65536})

    assert resp.status_code == 200
    assert resp.json()["status"] == "already_running"
    assert resp.json()["supervised"] is False


def test_an_omitted_ctx_size_becomes_this_machines_profile_window(
    client, supervisor, monkeypatch
):
    """A server started without the profile's window answers /health and then
    fails every long request — so "no ctx_size given" must mean the machine's
    pinned window, never the server's own small default."""
    monkeypatch.setattr("gaia.daemon.lemonade_routes._profile_ctx_size", lambda: 32768)

    resp = client.post(START, headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["ctx_size"] == 32768
    assert supervisor.calls == [32768]


def test_a_refusal_is_a_503_carrying_the_supervisors_own_message():
    """No silent fallback: the front-end must get the actionable text verbatim,
    because it is the only thing that knows WHICH failure this was."""
    message = (
        "Port 13305 on localhost is held by a process that does not answer "
        "Lemonade's health endpoint.\nTo fix: stop whatever holds that port..."
    )
    resp = _client(FakeSupervisor(error=LemonadeStartError(message))).post(
        START, headers=AUTH, json={}
    )

    assert resp.status_code == 503
    assert resp.json()["detail"] == message


def test_a_corrupt_config_is_a_503_not_a_guessed_context_window(client, monkeypatch):
    """The NPU profile's ceiling is half the GPU one, so guessing past an
    unreadable config would fail the model load outright."""
    from gaia.config import GaiaConfigError

    def broken():
        raise GaiaConfigError(
            "config.json is not valid JSON; run `gaia config set ...`"
        )

    monkeypatch.setattr("gaia.daemon.lemonade_routes._profile_ctx_size", broken)

    resp = client.post(START, headers=AUTH)

    assert resp.status_code == 503
    assert "gaia config set" in resp.json()["detail"]


def test_a_non_positive_ctx_size_is_rejected_rather_than_silently_replaced(client):
    resp = client.post(START, headers=AUTH, json={"ctx_size": 0})
    assert resp.status_code == 422


def test_status_reports_supervision_without_starting_anything(client, supervisor):
    resp = client.get(STATUS, headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["supervised"] is True
    assert resp.json()["pid"] == 4242
    assert supervisor.calls == [], "a status read started a server"


def test_a_daemon_without_a_supervisor_does_not_mount_the_routes():
    """The skeleton/test daemon has no model server to offer, and a 404 is an
    honest answer — better than a route that pretends and then fails."""
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    c = TestClient(create_app(token=TOKEN, port=1, pid=2, started_at=0.0))
    assert c.post(START, headers=AUTH, json={}).status_code == 404
