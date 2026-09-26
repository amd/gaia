# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``ensure_lemonade_running`` — one verb, and who actually does the work.

The single-instance guarantee lives here, not in a lock: no process other than
the daemon may spawn a model server, so a CLI and a UI starting at the same
moment both end up talking to the same supervisor. These tests pin that split —
in the daemon, straight to the supervisor; anywhere else, over the daemon's
authenticated loopback API — and pin that neither branch is reached when a
server is already answering.
"""

from __future__ import annotations

import pytest

from gaia.llm import lemonade_service as service
from gaia.llm.lemonade_service import (
    LemonadeStartError,
    ensure_lemonade_running,
    install_supervisor,
)
from gaia.llm.lemonade_supervisor import LemonadeState


@pytest.fixture(autouse=True)
def _no_installed_supervisor():
    """This process is not the daemon unless a test says so."""
    install_supervisor(None)
    yield
    install_supervisor(None)


class FakeClient:
    def __init__(self, up=False):
        self.base_url = "http://localhost:13305/api/v1"
        self.host, self.port = "localhost", 13305
        self.up = up
        self.probes = 0

    # Mirrors LemonadeClient.health_check's signature: the supervisor passes a
    # (connect, read) timeout, and a fake that rejected it would make every
    # probe raise TypeError and be swallowed as "not answering".
    def health_check(self, timeout=None):
        self.probes += 1
        if not self.up:
            raise ConnectionError("connection refused")
        return {"status": "ok"}


class FakeSupervisor:
    def __init__(self):
        self.calls = []

    def ensure_running(self, ctx_size=None, timeout=None):
        self.calls.append(ctx_size)
        return LemonadeState(
            base_url="http://localhost:13305/api/v1",
            started=True,
            owned=True,
            pid=1,
            waited_seconds=1.0,
        )


@pytest.fixture
def down_client(monkeypatch):
    c = FakeClient(up=False)
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.LemonadeClient", lambda **kwargs: c, raising=True
    )
    return c


def test_inside_the_daemon_the_supervisor_is_used_directly(monkeypatch, down_client):
    """Posting to our own loopback port to reach an object in this address
    space would be a round-trip for nothing — and would deadlock a
    single-threaded caller."""
    monkeypatch.setattr(
        service,
        "_ask_the_daemon",
        lambda **_: pytest.fail("the daemon posted to itself"),
    )
    supervisor = FakeSupervisor()
    install_supervisor(supervisor)

    state = ensure_lemonade_running(ctx_size=65536)

    assert state.started is True
    assert supervisor.calls == [65536]


def test_outside_the_daemon_the_daemon_is_asked(monkeypatch, down_client):
    """No other process spawns a server. That is what makes one instance real
    rather than a race that usually works."""
    asked = {}

    def fake_post(payload, timeout):
        asked.update(payload)
        return {
            "status": "started",
            "base_url": "http://localhost:13305/api/v1",
            "pid": 77,
            "waited_seconds": 5.0,
        }

    monkeypatch.setattr(service, "_post_start", fake_post)

    state = ensure_lemonade_running(ctx_size=65536)

    assert asked == {"ctx_size": 65536}
    assert state.started is True
    assert state.pid == 77


def test_a_running_server_asks_nobody(monkeypatch):
    """The fast path must not start a daemon, or reach a supervisor, just to be
    told the server it wanted is already there."""
    up = FakeClient(up=True)
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.LemonadeClient", lambda **kwargs: up, raising=True
    )
    monkeypatch.setattr(
        service, "_post_start", lambda *a, **k: pytest.fail("asked the daemon")
    )

    state = ensure_lemonade_running(ctx_size=65536)

    assert state.started is False
    assert up.probes == 1


def test_no_daemon_is_a_loud_error_naming_how_to_start_it(monkeypatch, down_client):
    """The failure must name the background service, not Lemonade: a user sent
    to restart the wrong process gets nowhere."""
    monkeypatch.setattr("gaia.daemon.client.attach", lambda: None)

    with pytest.raises(LemonadeStartError) as e:
        ensure_lemonade_running(ctx_size=65536)

    assert "background service" in str(e.value)
    assert "gaia daemon start" in str(e.value)


def test_a_readiness_check_never_boots_a_daemon(monkeypatch, down_client):
    """The bug this rule exists to prevent, and it was real.

    An earlier revision start-or-attached here. Constructing an agent in a unit
    test then spawned a machine-wide daemon AND a model server, took 30 seconds
    a call, and pushed the email agent's CI job past its 10-minute ceiling —
    while every assertion still passed. Bringing up background infrastructure is
    a front-end's decision, made once and visibly; a readiness check may only
    attach to what is already there.
    """
    monkeypatch.setattr(
        "gaia.daemon.client.start_or_attach",
        lambda *a, **k: pytest.fail("a readiness check tried to start a daemon"),
    )
    monkeypatch.setattr("gaia.daemon.client.attach", lambda: None)

    with pytest.raises(LemonadeStartError):
        ensure_lemonade_running(ctx_size=65536)


def test_a_daemon_refusal_reaches_the_caller_verbatim(monkeypatch, down_client):
    """The daemon's detail is the most specific thing in the system about WHICH
    failure this was, so restating it here would lose information."""

    class FakeResponse:
        status_code = 503

        @staticmethod
        def json():
            return {"detail": "Port 13305 is held by something else. To fix: ..."}

    class FakeInstance:
        host, port, token = "127.0.0.1", 51234, "tok"

    monkeypatch.setattr("gaia.daemon.client.attach", lambda: FakeInstance())
    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())

    with pytest.raises(LemonadeStartError) as e:
        ensure_lemonade_running(ctx_size=65536)

    assert "Port 13305 is held by something else" in str(e.value)


def test_the_request_is_addressed_and_authorized_the_way_the_daemon_expects(
    monkeypatch, down_client
):
    """A mock proves "we called requests.post", never "the daemon would accept
    it" (CLAUDE.md). ``/daemon/v1/lemonade/start`` is token-guarded and would
    401 a request missing the header, with a green suite either side — so the
    shape of the outgoing call is asserted here, not just that it happened.
    """
    sent = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "started",
                "base_url": "http://localhost:13305/api/v1",
                "supervised": True,
                "pid": 5,
                "waited_seconds": 3.0,
            }

    class FakeInstance:
        host, port, token = "127.0.0.1", 51234, "sekrit"

    def capture(url, json=None, headers=None, timeout=None):
        sent.update(url=url, json=json, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr("gaia.daemon.client.attach", lambda: FakeInstance())
    monkeypatch.setattr("requests.post", capture)

    state = ensure_lemonade_running(ctx_size=65536, timeout=120.0)

    assert sent["url"] == "http://127.0.0.1:51234/daemon/v1/lemonade/start"
    assert sent["headers"] == {"Authorization": "Bearer sekrit"}
    assert sent["json"] == {"ctx_size": 65536}
    # Strictly longer than the daemon's own start budget, or a start that was
    # about to succeed gets aborted here and reported as a different failure.
    assert sent["timeout"] > 120.0
    # Ownership is reported by the daemon, never assumed by the caller.
    assert state.owned is True
