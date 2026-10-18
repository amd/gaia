# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A queued model-slot request is waiting, not dead — and is shown as waiting.

Covers the broker's queue semantics end to end against a real daemon app on an
ephemeral port:

- a wait lasts as long as the holder ahead is alive, with live queue updates,
  and ends early only on an explicit limit (no fixed 300 s 504);
- a requester that goes away leaves the queue instead of being granted a lease
  nobody uses;
- a holder that renews keeps its lease past the old fixed TTL;
- the user's turn leases at interactive priority, and the queued state reaches
  the turn's own event stream while it waits;
- a wait on this process's own per-model lock is reported and cancellable too.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import List

import pytest
import requests

from gaia.daemon import broker_client, broker_routes
from gaia.daemon.app import create_app
from gaia.daemon.broker import (
    LeaseNotHeldError,
    LeaseTimeoutError,
    ModelSlotBroker,
)
from gaia.daemon.constants import (
    BROKER_TOKEN_ENV_VAR,
    BROKER_URL_ENV_VAR,
    LEASE_WAIT_LIMIT_ENV_VAR,
)

_TOKEN = "broker-queue-token"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Daemon:
    def __init__(self, broker: ModelSlotBroker, port: int):
        self.broker = broker
        self.url = f"http://127.0.0.1:{port}"

    def raw_lease(self, model, **body):
        return requests.post(
            f"{self.url}/host/v1/models/lease",
            json={"model": model, **body},
            headers={"Authorization": f"Bearer {_TOKEN}"},
            timeout=60,
        )

    def raw_release(self, lease_id):
        return requests.post(
            f"{self.url}/host/v1/models/lease/{lease_id}/release",
            headers={"Authorization": f"Bearer {_TOKEN}"},
            timeout=10,
        )

    def waiting(self) -> int:
        return len(self.broker.snapshot()["waiting"])

    def wait_until(self, check, what, timeout=5.0):
        deadline = time.time() + timeout
        while not check():
            assert time.time() < deadline, f"timed out waiting for {what}"
            time.sleep(0.02)


def _start_daemon(broker: ModelSlotBroker):
    uvicorn = pytest.importorskip("uvicorn")
    port = _free_port()
    app = create_app(
        token=_TOKEN,
        port=port,
        pid=os.getpid(),
        started_at=time.time(),
        broker=broker,
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "test daemon did not come up"
    return server, thread, _Daemon(broker, port)


@pytest.fixture
def daemon_factory(monkeypatch):
    """Start a real daemon app; point this process's broker client at it."""
    started = []

    def _make(broker=None):
        server, thread, daemon = _start_daemon(broker or ModelSlotBroker())
        started.append((server, thread))
        monkeypatch.setenv(BROKER_URL_ENV_VAR, daemon.url)
        monkeypatch.setenv(BROKER_TOKEN_ENV_VAR, _TOKEN)
        return daemon

    monkeypatch.delenv(LEASE_WAIT_LIMIT_ENV_VAR, raising=False)
    monkeypatch.setattr(broker_client, "_attached", None)
    monkeypatch.setattr(broker_client, "_broker_unsupported", False)
    broker_client._held.set(None)
    yield _make
    for server, thread in started:
        server.should_exit = True
        thread.join(timeout=30)
    broker_client._held.set(None)


def _hold_in_thread(model, seconds, **lease_kwargs):
    """Hold a lease through ``model_lease`` on another thread for *seconds*."""
    granted = threading.Event()
    released = {}

    def _run():
        with broker_client.model_lease(model, **lease_kwargs):
            granted.set()
            time.sleep(seconds)
        released["at"] = time.monotonic()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert granted.wait(10), "holder never got its lease"
    return thread, released


# ── A1: a busy queue is not a broken daemon ─────────────────────────────────


@pytest.mark.allow_network
def test_a_queued_lease_waits_past_the_old_ceiling_while_the_holder_lives(
    daemon_factory, monkeypatch
):
    """Before: the wait hit a fixed ceiling and failed with 504 + "check the
    daemon logs" while the holder was still working. Shrinking that ceiling to
    1 s makes the 300 s symptom reproducible in a unit test."""
    monkeypatch.setattr(broker_routes, "DEFAULT_LEASE_WAIT_TIMEOUT_S", 1.0)
    daemon_factory()
    holder, released = _hold_in_thread("holder-model", 3.0)
    updates: List[dict] = []

    lease = broker_client.acquire_lease("waiter-model", on_update=updates.append)

    assert lease["model"] == "waiter-model"
    assert time.monotonic() >= released["at"], "granted while the holder held it"
    assert updates, "the waiter heard nothing while queued"
    assert updates[0]["position"] == 1
    assert updates[0]["holder_model"] == "holder-model"
    assert updates[-1]["waited_s"] > updates[0]["waited_s"]
    broker_client.release_lease(lease["lease_id"])
    holder.join(10)


@pytest.mark.allow_network
def test_model_lease_waits_out_a_busy_holder_instead_of_failing(
    daemon_factory, monkeypatch
):
    """The A1 symptom through the API every caller uses: on main this raised
    BrokerUnavailableError (HTTP 504, "Check `gaia daemon logs`")."""
    monkeypatch.setattr(broker_routes, "DEFAULT_LEASE_WAIT_TIMEOUT_S", 1.0)
    daemon_factory()
    holder, released = _hold_in_thread("holder-model", 3.0)

    with broker_client.model_lease("waiter-model") as lease:
        assert lease is not None
        assert time.monotonic() >= released["at"]
    holder.join(10)


@pytest.mark.allow_network
def test_an_explicit_wait_limit_fails_with_where_the_request_stood(
    daemon_factory, monkeypatch
):
    daemon_factory()
    holder, _ = _hold_in_thread("holder-model", 3.0)
    monkeypatch.setenv(LEASE_WAIT_LIMIT_ENV_VAR, "1")

    with pytest.raises(broker_client.BrokerUnavailableError) as err:
        broker_client.acquire_lease("waiter-model")

    msg = str(err.value)
    assert err.value.status_code == 504
    assert "#1 of 1 waiting" in msg and "holder-model" in msg
    assert "busy, not broken" in msg
    assert LEASE_WAIT_LIMIT_ENV_VAR in msg
    assert "gaia daemon logs" not in msg
    holder.join(10)


@pytest.mark.allow_network
def test_a_daemon_that_goes_silent_mid_queue_is_reported_as_down(monkeypatch):
    """Queued updates arrive every few seconds; silence means the daemon died."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def _serve_headers_then_hang():
        conn, _ = listener.accept()
        conn.recv(65536)
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        time.sleep(5)
        conn.close()

    threading.Thread(target=_serve_headers_then_hang, daemon=True).start()
    monkeypatch.setenv(BROKER_URL_ENV_VAR, f"http://127.0.0.1:{port}")
    monkeypatch.setenv(BROKER_TOKEN_ENV_VAR, _TOKEN)
    monkeypatch.setattr(broker_client, "STREAM_IDLE_TIMEOUT_S", 1.0)

    started = time.monotonic()
    with pytest.raises(broker_client.BrokerUnavailableError) as err:
        broker_client.acquire_lease("m")

    assert time.monotonic() - started < 4
    assert "stopped answering" in str(err.value)
    listener.close()


# ── A2: a requester that goes away leaves the queue ─────────────────────────


@pytest.mark.allow_network
def test_a_waiter_that_disconnects_is_never_granted_the_slot(daemon_factory):
    """Before: the dead waiter got the lease on release and held the slot for
    nobody, so the next request hung until it timed out."""
    daemon = daemon_factory()
    first = daemon.raw_lease("holder-model")
    assert first.status_code == 200

    sock = socket.create_connection(("127.0.0.1", int(daemon.url.rsplit(":", 1)[1])))
    body = json.dumps({"model": "abandoned-model"}).encode()
    sock.sendall(
        b"POST /host/v1/models/lease HTTP/1.1\r\nHost: x\r\n"
        b"Authorization: Bearer " + _TOKEN.encode() + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    daemon.wait_until(lambda: daemon.waiting() == 1, "the waiter to queue")
    sock.close()  # Ctrl+C, Esc, process exit
    daemon.wait_until(lambda: daemon.waiting() == 0, "the dead waiter to leave")

    daemon.raw_release(first.json()["lease_id"])
    third = daemon.raw_lease("next-model", timeout=3)

    assert third.status_code == 200, third.text
    assert third.json()["model"] == "next-model"


@pytest.mark.allow_network
def test_cancelling_a_streaming_wait_leaves_the_queue(daemon_factory):
    daemon = daemon_factory()
    holder, _ = _hold_in_thread("holder-model", 4.0)
    cancel = threading.Event()
    outcome = {}

    def _wait():
        with broker_client.lease_cancel_scope(cancel):
            try:
                broker_client.acquire_lease("waiter-model")
            except broker_client.LeaseWaitCancelled as e:
                outcome["cancelled"] = e

    waiter = threading.Thread(target=_wait, daemon=True)
    waiter.start()
    daemon.wait_until(lambda: daemon.waiting() == 1, "the waiter to queue")
    cancel.set()
    waiter.join(5)

    assert "cancelled" in outcome, "the cancel did not end the wait"
    daemon.wait_until(lambda: daemon.waiting() == 0, "the waiter to leave")
    holder.join(10)


# ── A3: a renewing holder keeps its lease ───────────────────────────────────


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_a_renewed_lease_outlives_the_fixed_ttl():
    """Before: a 900 s TTL reclaimed a lease mid-generation for a call allowed
    3600 s, and the holder's release then failed with 409."""
    clock = _Clock()
    broker = ModelSlotBroker(lease_ttl_s=900, heartbeat_ttl_s=60, time_fn=clock)
    lease = broker.acquire("m", renewable=True)

    for _ in range(80):  # 2400 s of a long generation, renewing every 30 s
        clock.t += 30
        broker.renew(lease.lease_id)

    with pytest.raises(LeaseTimeoutError):
        broker.acquire("other", timeout=0)
    broker.release(lease.lease_id)


def test_a_lease_whose_holder_stops_renewing_is_reclaimed():
    clock = _Clock()
    broker = ModelSlotBroker(heartbeat_ttl_s=60, time_fn=clock)
    lease = broker.acquire("m", renewable=True)
    clock.t += 61

    assert broker.acquire("other", timeout=0).model == "other"
    with pytest.raises(LeaseNotHeldError):
        broker.renew(lease.lease_id)


def test_a_renewing_holder_is_still_capped():
    clock = _Clock()
    broker = ModelSlotBroker(heartbeat_ttl_s=60, max_hold_s=600, time_fn=clock)
    lease = broker.acquire("m", renewable=True)
    for _ in range(20):
        clock.t += 30
        if clock.t - 1000 < 600:
            broker.renew(lease.lease_id)

    assert broker.acquire("other", timeout=0).model == "other"


def test_a_lease_from_a_client_that_does_not_renew_keeps_the_fixed_ttl():
    clock = _Clock()
    broker = ModelSlotBroker(lease_ttl_s=900, heartbeat_ttl_s=60, time_fn=clock)
    broker.acquire("m")
    clock.t += 899

    with pytest.raises(LeaseTimeoutError):
        broker.acquire("other", timeout=0)


@pytest.mark.allow_network
def test_model_lease_renews_while_held(daemon_factory):
    """A holder that runs past the heartbeat TTL keeps the slot end to end."""
    daemon_factory(ModelSlotBroker(lease_ttl_s=1.0, heartbeat_ttl_s=1.0))
    holder, released = _hold_in_thread("holder-model", 3.0)

    lease = broker_client.acquire_lease("waiter-model")

    assert time.monotonic() >= released["at"], "the lease was reclaimed mid-hold"
    broker_client.release_lease(lease["lease_id"])
    holder.join(10)


# ── A4: priority and the user's stream ──────────────────────────────────────


@pytest.mark.allow_network
def test_the_queued_state_reaches_the_turns_listener_while_it_waits(
    daemon_factory,
):
    """Before: the wait notice fired after the grant, and only to stdout."""
    daemon_factory()
    holder, released = _hold_in_thread("holder-model", 2.5)
    events: List[dict] = []

    def _listen(event):
        events.append({**event, "at": time.monotonic()})

    with broker_client.lease_wait_listener(_listen):
        with broker_client.model_lease("waiter-model"):
            pass

    queued = [e for e in events if e["event"] == "queued"]
    assert queued and queued[0]["at"] < released["at"]
    assert queued[0]["scope"] == "daemon" and queued[0]["position"] == 1
    assert events[-1]["event"] == "granted" and events[-1]["waited_s"] >= 2
    holder.join(10)


@pytest.mark.allow_network
def test_an_interactive_turn_leases_at_interactive_priority(daemon_factory):
    daemon = daemon_factory()
    holder, _ = _hold_in_thread("holder-model", 2.0)
    seen = {}

    def _side_work():
        # A plain thread starts with a fresh context: background, like the
        # memory extraction a turn kicks off.
        seen["side"] = broker_client.current_priority()

    def _turn():
        with broker_client.interactive_turn():
            seen["turn"] = broker_client.current_priority()
            side = threading.Thread(target=_side_work)
            side.start()
            side.join()
            with broker_client.model_lease("turn-model"):
                pass

    turn = threading.Thread(target=_turn, daemon=True)
    turn.start()
    daemon.wait_until(lambda: daemon.waiting() == 1, "the turn to queue")
    assert daemon.broker.snapshot()["waiting"][0]["priority"] == "interactive"
    turn.join(10)
    holder.join(10)
    assert seen == {"turn": "interactive", "side": "background"}


# ── The in-process per-model lock ───────────────────────────────────────────


def _local_client():
    from gaia.llm.lemonade_client import LemonadeClient

    client = object.__new__(LemonadeClient)
    client.model_lease_priority = None
    client._model_metadata = {}
    client.log = logging.getLogger("test.lemonade")
    return client


def test_a_wait_on_this_processs_model_lock_is_reported_and_cancellable(
    monkeypatch,
):
    """Before: a second request blocked on an RLock with no status and no way
    out until the first finished."""
    monkeypatch.delenv(BROKER_URL_ENV_VAR, raising=False)
    monkeypatch.setattr(broker_client, "_attached", None)
    client = _local_client()
    holding, done = threading.Event(), threading.Event()

    def _hold():
        with client._model_slot_lease("local-model"):
            holding.set()
            done.wait(10)

    threading.Thread(target=_hold, daemon=True).start()
    assert holding.wait(5)
    events: List[dict] = []
    cancel = threading.Event()
    outcome = {}

    def _second():
        with broker_client.lease_wait_listener(events.append):
            with broker_client.lease_cancel_scope(cancel):
                try:
                    with client._model_slot_lease("local-model"):
                        outcome["entered"] = True
                except broker_client.LeaseWaitCancelled as e:
                    outcome["cancelled"] = e

    second = threading.Thread(target=_second, daemon=True)
    second.start()
    deadline = time.time() + 5
    while not events and time.time() < deadline:
        time.sleep(0.02)
    cancel.set()
    second.join(5)
    done.set()

    assert events and events[0]["event"] == "queued"
    assert events[0]["scope"] == "process"
    assert "cancelled" in outcome and "entered" not in outcome


# ── Review follow-ups: races, ownership, privacy ────────────────────────────


@pytest.mark.allow_network
def test_a_cancel_that_arrives_with_the_grant_gives_the_slot_back(daemon_factory):
    """The grant line can be the first one read after a cancel; keeping that
    lease would idle the slot for every process until the heartbeat TTL."""
    daemon = daemon_factory()
    cancel = threading.Event()
    cancel.set()

    with broker_client.lease_cancel_scope(cancel):
        with pytest.raises(broker_client.LeaseWaitCancelled):
            broker_client.acquire_lease("m")

    daemon.wait_until(
        lambda: daemon.broker.snapshot()["active"] is None, "the lease to go back"
    )


def test_only_the_holder_can_renew_or_release_and_no_lease_id_leaks():
    broker = ModelSlotBroker()
    lease = broker.acquire("m", holder="email", renewable=True)

    for call in (broker.renew, broker.release):
        with pytest.raises(LeaseNotHeldError):
            call(lease.lease_id, holder="other-agent")
        # A guessed id must not be answered with the real one.
        with pytest.raises(LeaseNotHeldError) as err:
            call("wrong-id", holder="email")
        assert lease.lease_id not in str(err.value)
    broker.release(lease.lease_id, holder="email")


@pytest.mark.allow_network
def test_the_queue_route_shows_who_waits_without_lease_ids(daemon_factory):
    daemon = daemon_factory()
    held = daemon.raw_lease("holder-model").json()

    r = requests.get(
        f"{daemon.url}/host/v1/models/queue",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        timeout=10,
    )

    assert r.status_code == 200
    assert r.json()["active"]["model"] == "holder-model"
    assert held["lease_id"] not in r.text
    daemon.raw_release(held["lease_id"])


def test_a_raising_queue_callback_never_leaves_a_dead_waiter_behind():
    broker = ModelSlotBroker()
    held = broker.acquire("holder")

    def _boom(_status):
        raise RuntimeError("listener failed")

    with pytest.raises(RuntimeError):
        broker.acquire("waiter", on_update=_boom)

    assert broker.snapshot()["waiting"] == []
    broker.release(held.lease_id)
    assert broker.acquire("next", timeout=0).model == "next"
