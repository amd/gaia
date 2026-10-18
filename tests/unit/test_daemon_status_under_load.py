# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A daemon busy with queued model leases is still a live daemon.

Every queued lease request used to park a worker thread from the same pool the
sync ``/daemon/v1/status`` route runs on. About 40 waiters filled it, status
stopped answering within the 1.5 s probe, and the next ``gaia`` command took the
silence for a hung daemon and tree-killed it along with every sidecar.
"""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest
import requests

from gaia.daemon import client as daemon_client
from gaia.daemon.app import create_app
from gaia.daemon.broker import ModelSlotBroker
from gaia.daemon.errors import DaemonBusyError, DaemonHungError
from gaia.daemon.instance import DaemonInstance, probe_state

_TOKEN = "status-under-load-token"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_daemon():
    uvicorn = pytest.importorskip("uvicorn")
    port = _free_port()
    broker = ModelSlotBroker()
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
    yield DaemonInstance(pid=os.getpid(), port=port, token=_TOKEN), broker
    server.should_exit = True
    thread.join(timeout=30)


def _lease(inst, model, timeout):
    return requests.post(
        f"{inst.base_url}/host/v1/models/lease",
        json={"model": model, "timeout": timeout},
        headers={"Authorization": f"Bearer {inst.token}"},
        timeout=timeout + 10,
    )


@pytest.mark.allow_network
def test_status_answers_while_lease_waiters_fill_the_thread_pool(live_daemon):
    """Reproduces A5: 45 queued leases must not make status miss the probe."""
    live_daemon, broker = live_daemon
    first = _lease(live_daemon, "holder-model", 8)
    assert first.status_code == 200

    waiters = [
        threading.Thread(
            target=_lease, args=(live_daemon, f"waiter-{i}", 8), daemon=True
        )
        for i in range(45)
    ]
    for w in waiters:
        w.start()
    # 40 is anyio's default worker-pool size: enough queued leases to fill it.
    deadline = time.time() + 5
    while len(broker.snapshot()["waiting"]) < 40 and time.time() < deadline:
        time.sleep(0.05)
    assert len(broker.snapshot()["waiting"]) >= 40, "waiters did not queue"

    try:
        assert probe_state(live_daemon, timeout=1.5)[0] == "live", (
            "status did not answer within the 1.5 s probe while leases queued; "
            "start_or_attach would read this live daemon as hung and kill it"
        )
        # The holder must be able to give the slot back while waiters fill
        # the pool; a starved release keeps every waiter behind it.
        started = time.monotonic()
        released = requests.post(
            f"{live_daemon.base_url}/host/v1/models/lease/"
            f"{first.json()['lease_id']}/release",
            headers={"Authorization": f"Bearer {live_daemon.token}"},
            timeout=30,
        )
        took = time.monotonic() - started
        assert released.status_code == 200, released.text
        assert took < 1.5, f"releasing the held lease took {took:.1f}s"
    finally:
        for w in waiters:
            w.join(timeout=30)


def _stale_registry(monkeypatch, outcomes):
    """A registered daemon whose pid is alive; *outcomes* script its probes."""
    inst = DaemonInstance(pid=4242, port=1, token="t")
    monkeypatch.setattr(daemon_client, "read_instance", lambda *a, **k: inst)
    monkeypatch.setattr(daemon_client, "pid_alive", lambda pid: True)
    calls = []

    def _probe_state(_inst, timeout=1.5):
        calls.append(timeout)
        return outcomes[min(len(calls), len(outcomes)) - 1]

    monkeypatch.setattr(daemon_client, "probe_state", _probe_state)
    killed = []
    monkeypatch.setattr(
        daemon_client, "terminate_instance", lambda i, **k: killed.append(i)
    )
    spawned = []
    monkeypatch.setattr(
        daemon_client,
        "_spawn_and_wait",
        lambda *a, **k: spawned.append(a) or inst,
    )

    class _NoLock:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(daemon_client, "StartLock", _NoLock)
    monkeypatch.setattr(daemon_client, "missing_daemon_deps", lambda: [])
    monkeypatch.setattr(daemon_client, "_is_gaia_daemon", lambda pid: True)
    return inst, calls, killed, spawned


_BODY = {"service": "gaia-daemon", "pid": 4242}


def test_start_or_attach_waits_out_a_slow_status_instead_of_killing(monkeypatch):
    """A probe that times out, then answers, attaches — and kills nothing."""
    inst, calls, killed, spawned = _stale_registry(
        monkeypatch, [("busy", None), ("live", _BODY)]
    )

    assert daemon_client.start_or_attach() is inst
    assert killed == [], "a slow status probe tree-killed a live daemon"
    assert spawned == []
    assert len(calls) == 2 and calls[1] > calls[0], "busy needs a longer re-probe"


def test_start_or_attach_never_kills_a_daemon_that_stays_busy(monkeypatch):
    inst, _calls, killed, spawned = _stale_registry(monkeypatch, [("busy", None)])

    with pytest.raises(DaemonBusyError) as err:
        daemon_client.start_or_attach()

    assert killed == [] and spawned == []
    msg = str(err.value)
    assert "4242" in msg and "gaia daemon restart" in msg


def test_start_or_attach_still_reclaims_a_daemon_that_refuses_connections(
    monkeypatch,
):
    """A live pid with nothing listening is wedged, not busy: reclaim it."""
    inst, _calls, killed, spawned = _stale_registry(monkeypatch, [("gone", None)])

    daemon_client.start_or_attach()

    assert killed == [inst] and len(spawned) == 1


def test_attach_reports_a_busy_daemon_instead_of_no_daemon(monkeypatch):
    """``None`` means "no daemon": a busy one must not read as absent, or a
    model load would run unbrokered and race the daemon's sidecars."""
    _stale_registry(monkeypatch, [("busy", None)])

    with pytest.raises(DaemonBusyError):
        daemon_client.attach()


def test_daemon_status_reports_busy_rather_than_stale(monkeypatch):
    inst, _calls, killed, _spawned = _stale_registry(monkeypatch, [("busy", None)])

    told = []
    report = daemon_client.status_report(on_slow=lambda: told.append(True))

    assert report == {"state": "busy", "instance": inst}
    assert killed == []
    assert told == [True], "the CLI was not told it is waiting on a slow daemon"


def test_a_busy_daemon_fails_a_model_load_as_a_broker_error(monkeypatch):
    """Not unbrokered (it would race the daemon's sidecars), and not a raw
    daemon error that callers would blame on Lemonade."""
    from gaia.daemon import broker_client

    _stale_registry(monkeypatch, [("busy", None)])
    monkeypatch.delenv("GAIA_MODEL_BROKER_URL", raising=False)
    monkeypatch.setattr(broker_client, "_attached", None)

    with pytest.raises(broker_client.BrokerUnavailableError) as err:
        broker_client.attach_broker_env()
    assert isinstance(err.value.__cause__, DaemonBusyError)


def test_probe_state_classifies_a_read_timeout_as_busy(monkeypatch):
    inst = DaemonInstance(pid=os.getpid(), port=1, token="t")

    def _slow(*a, **k):
        raise requests.exceptions.ReadTimeout("slow")

    monkeypatch.setattr(requests, "get", _slow)
    assert probe_state(inst)[0] == "busy"

    def _refused(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", _refused)
    assert probe_state(inst)[0] == "gone"


def test_a_busy_daemon_fails_a_lemonade_start_with_the_start_error(monkeypatch):
    """ensure_ready only handles LemonadeStartError; anything else would reach
    the user as a traceback instead of its clean message."""
    from gaia.llm import lemonade_service
    from gaia.llm.lemonade_service import LemonadeStartError

    def _busy(*a, **k):
        raise DaemonBusyError("the GAIA daemon (pid 4242) is running but ...")

    monkeypatch.setattr(daemon_client, "attach", _busy)

    with pytest.raises(LemonadeStartError) as err:
        lemonade_service._post_start({}, 1.0)
    assert "pid 4242" in str(err.value)
    assert isinstance(err.value.__cause__, DaemonBusyError)


def _busy_for(seconds: float, pid: int = 4242) -> None:
    """Record that the registered daemon has been busy for *seconds*."""
    import json

    from gaia.daemon import paths

    paths.ensure_host_dir()
    (paths.host_dir() / daemon_client.BUSY_MARKER_NAME).write_text(
        json.dumps({"pid": pid, "since": time.time() - seconds}), encoding="utf-8"
    )


def test_a_daemon_busy_past_the_hung_limit_is_restarted(monkeypatch):
    """Busy is bounded: status never waits on other work, so a daemon that has
    not answered it for minutes is hung, and the next start replaces it."""
    inst, _calls, killed, spawned = _stale_registry(monkeypatch, [("busy", None)])
    _busy_for(daemon_client.HUNG_AFTER_S + 1)

    daemon_client.start_or_attach()

    assert killed == [inst] and len(spawned) == 1


def test_a_hung_daemon_is_named_as_hung_to_callers_that_only_attach(monkeypatch):
    _stale_registry(monkeypatch, [("busy", None)])
    _busy_for(daemon_client.HUNG_AFTER_S + 1)

    with pytest.raises(DaemonHungError) as err:
        daemon_client.attach()

    assert "so it is hung" in str(err.value)
    assert "gaia daemon restart" in str(err.value)


def test_a_busy_spell_is_forgotten_once_the_daemon_answers(monkeypatch):
    _stale_registry(monkeypatch, [("live", _BODY)])
    _busy_for(daemon_client.HUNG_AFTER_S + 1)

    daemon_client.attach()

    from gaia.daemon import paths

    assert not (paths.host_dir() / daemon_client.BUSY_MARKER_NAME).exists()


def test_a_reused_pid_that_is_not_a_daemon_is_gone_not_busy(monkeypatch):
    """A foreign process holding the recorded pid and port never answers
    status; it must not read as a busy daemon forever."""
    inst, calls, _killed, spawned = _stale_registry(monkeypatch, [("busy", None)])
    monkeypatch.setattr(daemon_client, "_is_gaia_daemon", lambda pid: False)

    daemon_client.start_or_attach()

    assert len(spawned) == 1
    assert max(calls) <= 1.5, "waited out a foreign process as if it were busy"


def test_releasing_on_exit_does_not_wait_out_a_busy_daemon(monkeypatch):
    _inst, calls, _killed, _spawned = _stale_registry(monkeypatch, [("busy", None)])

    with pytest.raises(DaemonBusyError):
        daemon_client.release_lemonade_client("client-1")

    assert calls == [1.5], "an exiting process waited 10 more seconds"
