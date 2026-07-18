# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit spec for the broker's HTTP lease route + client (#2151 / V2-11).

Covers the callback-plane auth contract (daemon client token OR a live sidecar's
launch token, anything else → 401), the lease/release request shapes and their
loud error codes, that ``create_app`` mounts the route only when a broker is
given, and the env-gated ``broker_client`` (no-op standalone; fail-loud when the
broker URL is set but unreachable).

No real daemon, no real Lemonade, no real subprocess — a fake registry supplies
sidecar tokens, and the client tests point at a dead port.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from gaia.daemon.app import create_app
from gaia.daemon.broker import ModelSlotBroker

_DAEMON_TOKEN = "daemon-client-token-xyz"


class _FakeManager:
    def __init__(self, agent_id, token, running=True):
        self._agent_id = agent_id
        self.auth_token = token
        self._running = running

    @property
    def is_running(self):
        return self._running


class _FakeRegistry:
    """Minimal stand-in exposing only what the broker route uses:
    ``authenticate_callback`` (real logic) over a fixed token→agent map."""

    def __init__(self, live=None, stopped=None):
        self._managers = {}
        for aid, tok in (live or {}).items():
            self._managers[aid] = (_FakeManager(aid, tok, running=True), None)
        for aid, tok in (stopped or {}).items():
            self._managers[aid] = (_FakeManager(aid, tok, running=False), None)

    def authenticate_callback(self, credential):
        import secrets

        if not credential:
            return None
        for agent_id, (manager, _) in self._managers.items():
            if not manager.is_running:
                continue
            if manager.auth_token and secrets.compare_digest(
                credential, manager.auth_token
            ):
                return agent_id
        return None

    # create_app also mounts the agents + relay routers, which call these.
    def list_agents(self):
        return []

    def connection(self, agent_id):  # pragma: no cover - relay not exercised here
        from gaia.daemon.sidecars.errors import UnknownAgentError

        raise UnknownAgentError(agent_id)


def _client(registry=None, broker=None):
    app = create_app(
        token=_DAEMON_TOKEN,
        port=1234,
        pid=999,
        started_at=0.0,
        registry=registry if registry is not None else _FakeRegistry(),
        broker=broker if broker is not None else ModelSlotBroker(),
    )
    return TestClient(app, raise_server_exceptions=False)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# create_app wiring
# ---------------------------------------------------------------------------


def test_create_app_mounts_broker_route_when_broker_given():
    client = _client()
    r = client.post(
        "/host/v1/models/lease", json={"model": "m"}, headers=_auth(_DAEMON_TOKEN)
    )
    assert r.status_code == 200


def test_create_app_without_broker_does_not_mount_route():
    app = create_app(
        token=_DAEMON_TOKEN,
        port=1234,
        pid=999,
        started_at=0.0,
        registry=_FakeRegistry(),
        broker=None,
    )
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/host/v1/models/lease", json={"model": "m"}, headers=_auth(_DAEMON_TOKEN)
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Auth contract: daemon token OR live sidecar token, else 401
# ---------------------------------------------------------------------------


def test_host_side_daemon_token_grants_lease_with_host_holder():
    client = _client()
    r = client.post(
        "/host/v1/models/lease",
        json={"model": "chat"},
        headers=_auth(_DAEMON_TOKEN),
    )
    assert r.status_code == 200
    assert r.json()["holder"] == "host"


def test_sidecar_launch_token_grants_lease_with_agent_holder():
    reg = _FakeRegistry(live={"email": "email-launch-token"})
    client = _client(registry=reg)
    r = client.post(
        "/host/v1/models/lease",
        json={"model": "chat"},
        headers=_auth("email-launch-token"),
    )
    assert r.status_code == 200
    assert r.json()["holder"] == "email"


def test_stopped_sidecar_token_rejected():
    reg = _FakeRegistry(stopped={"email": "dead-token"})
    client = _client(registry=reg)
    r = client.post(
        "/host/v1/models/lease", json={"model": "m"}, headers=_auth("dead-token")
    )
    assert r.status_code == 401


def test_unknown_token_rejected():
    client = _client()
    r = client.post(
        "/host/v1/models/lease", json={"model": "m"}, headers=_auth("bogus")
    )
    assert r.status_code == 401


def test_missing_authorization_rejected():
    client = _client()
    r = client.post("/host/v1/models/lease", json={"model": "m"})
    assert r.status_code == 401


def test_malformed_authorization_rejected():
    client = _client()
    r = client.post(
        "/host/v1/models/lease",
        json={"model": "m"},
        headers={"Authorization": "Basic abc"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Request validation + release
# ---------------------------------------------------------------------------


def test_missing_model_is_422():
    client = _client()
    r = client.post("/host/v1/models/lease", json={}, headers=_auth(_DAEMON_TOKEN))
    assert r.status_code == 422


def test_bad_priority_is_422():
    client = _client()
    r = client.post(
        "/host/v1/models/lease",
        json={"model": "m", "priority": "urgent"},
        headers=_auth(_DAEMON_TOKEN),
    )
    assert r.status_code == 422


def test_lease_then_release_roundtrip():
    broker = ModelSlotBroker()
    client = _client(broker=broker)
    r = client.post(
        "/host/v1/models/lease", json={"model": "m"}, headers=_auth(_DAEMON_TOKEN)
    )
    assert r.status_code == 200
    lease_id = r.json()["lease_id"]
    rel = client.post(
        f"/host/v1/models/lease/{lease_id}/release", headers=_auth(_DAEMON_TOKEN)
    )
    assert rel.status_code == 200
    assert rel.json()["state"] == "released"
    # Slot is free again.
    assert broker.snapshot()["active"] is None


def test_release_unknown_lease_is_409():
    client = _client()
    r = client.post("/host/v1/models/lease/nope/release", headers=_auth(_DAEMON_TOKEN))
    assert r.status_code == 409


def test_release_requires_auth():
    client = _client()
    r = client.post("/host/v1/models/lease/whatever/release")
    assert r.status_code == 401


def test_concurrent_different_model_leases_serialize_over_http():
    """End-to-end over the route: two callers requesting different models are
    granted one at a time; the second one reports it had to wait."""
    broker = ModelSlotBroker()
    client = _client(broker=broker)

    first = client.post(
        "/host/v1/models/lease", json={"model": "model-a"}, headers=_auth(_DAEMON_TOKEN)
    )
    assert first.status_code == 200
    assert first.json()["waited"] is False
    first_id = first.json()["lease_id"]

    second_result = {}

    def _second():
        r = client.post(
            "/host/v1/models/lease",
            json={"model": "model-b"},
            headers=_auth(_DAEMON_TOKEN),
        )
        second_result["resp"] = r

    t = threading.Thread(target=_second)
    t.start()
    # Let the second request enqueue behind the first.
    _wait_until(lambda: len(broker.snapshot()["waiting"]) == 1, timeout=3.0)
    assert "resp" not in second_result  # blocked

    client.post(
        f"/host/v1/models/lease/{first_id}/release", headers=_auth(_DAEMON_TOKEN)
    )
    t.join(timeout=5.0)
    resp = second_result["resp"]
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "model-b"
    assert body["waited"] is True
    assert body["switching"] is True


# ---------------------------------------------------------------------------
# broker_client — env gate + fail-loud
# ---------------------------------------------------------------------------


def test_broker_client_no_op_when_unconfigured(monkeypatch):
    from gaia.daemon import broker_client
    from gaia.daemon.constants import BROKER_URL_ENV_VAR

    monkeypatch.delenv(BROKER_URL_ENV_VAR, raising=False)
    assert broker_client.broker_configured() is False
    with broker_client.model_lease("m") as lease:
        assert lease is None  # standalone: no lease, not a failure


def test_broker_client_fails_loud_when_url_set_but_unreachable(monkeypatch):
    from gaia.daemon import broker_client
    from gaia.daemon.constants import (
        BROKER_TOKEN_ENV_VAR,
        BROKER_URL_ENV_VAR,
    )

    # Point at a closed loopback port so the connection is refused fast.
    monkeypatch.setenv(BROKER_URL_ENV_VAR, "http://127.0.0.1:9")
    monkeypatch.setenv(BROKER_TOKEN_ENV_VAR, "tok")
    assert broker_client.broker_configured() is True
    with pytest.raises(broker_client.BrokerUnavailableError):
        with broker_client.model_lease("m"):
            pass


def test_broker_client_fails_loud_when_token_missing(monkeypatch):
    from gaia.daemon import broker_client
    from gaia.daemon.constants import (
        BROKER_TOKEN_ENV_VAR,
        BROKER_TOKEN_FILE_ENV_VAR,
        BROKER_URL_ENV_VAR,
    )

    monkeypatch.setenv(BROKER_URL_ENV_VAR, "http://127.0.0.1:9")
    monkeypatch.delenv(BROKER_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(BROKER_TOKEN_FILE_ENV_VAR, raising=False)
    with pytest.raises(broker_client.BrokerUnavailableError):
        with broker_client.model_lease("m"):
            pass


def test_broker_client_reads_credential_from_0600_file(monkeypatch, tmp_path):
    """#2149 posture: a sidecar's broker credential rides the 0600 launch-secret
    file, never a bare-env copy."""
    from gaia.daemon import broker_client
    from gaia.daemon.constants import (
        BROKER_TOKEN_ENV_VAR,
        BROKER_TOKEN_FILE_ENV_VAR,
    )

    secret = tmp_path / "launch-secret"
    secret.write_text("file-delivered-token\n", encoding="utf-8")
    monkeypatch.setenv(BROKER_TOKEN_FILE_ENV_VAR, str(secret))
    monkeypatch.delenv(BROKER_TOKEN_ENV_VAR, raising=False)
    assert broker_client._credential() == "file-delivered-token"


def test_broker_client_prefers_file_over_bare_env(monkeypatch, tmp_path):
    from gaia.daemon import broker_client
    from gaia.daemon.constants import (
        BROKER_TOKEN_ENV_VAR,
        BROKER_TOKEN_FILE_ENV_VAR,
    )

    secret = tmp_path / "launch-secret"
    secret.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv(BROKER_TOKEN_FILE_ENV_VAR, str(secret))
    monkeypatch.setenv(BROKER_TOKEN_ENV_VAR, "from-env")
    assert broker_client._credential() == "from-file"


def test_broker_client_credential_file_unreadable_fails_loud(monkeypatch, tmp_path):
    from gaia.daemon import broker_client
    from gaia.daemon.constants import (
        BROKER_TOKEN_ENV_VAR,
        BROKER_TOKEN_FILE_ENV_VAR,
    )

    monkeypatch.setenv(BROKER_TOKEN_FILE_ENV_VAR, str(tmp_path / "does-not-exist"))
    monkeypatch.delenv(BROKER_TOKEN_ENV_VAR, raising=False)
    with pytest.raises(broker_client.BrokerUnavailableError):
        broker_client._credential()


def _wait_until(pred, timeout=2.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")
