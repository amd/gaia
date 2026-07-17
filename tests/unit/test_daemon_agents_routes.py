# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Failing (red-phase) spec for issue #2142 T2: the multi-agent sidecar registry,
the spawn ledger (crash-reap), the ``/daemon/v1/agents/*`` routes, and the small
constants/paths additions they depend on.

None of the modules under test exist yet:
  - gaia.daemon.sidecars.registry   (SidecarRegistry, MAX_LIVE_SIDECARS)
  - gaia.daemon.sidecars.ledger     (record_spawn/remove_entry/read_entries/reap_stale)
  - gaia.daemon.sidecars.routes     (build_agents_router)
  - gaia.daemon.sidecars.errors new classes (UnknownAgentError, ModeConflictError,
    CapacityError, StopFailedError)
  - gaia.daemon.constants.DAEMON_API_VERSION bump to "1.1"
  - gaia.daemon.paths.atomic_write_json / sidecars_ledger_path

Every test that touches on-disk daemon state sets GAIA_DAEMON_HOME to a tmp_path
so it never touches the real ~/.gaia/host. No real subprocesses, no real network —
fakes/monkeypatching only.
"""

from __future__ import annotations

import json
import threading
import time as _t

import pytest

from gaia.daemon.sidecars.spec import AgentSidecarSpec, builtin_specs

# ---------------------------------------------------------------------------
# Shared fixtures / toy specs
# ---------------------------------------------------------------------------


@pytest.fixture()
def daemon_home(tmp_path, monkeypatch):
    """Isolate all daemon on-disk state under a tmp dir for one test."""
    home = tmp_path / "host"
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(home))
    return home


_TOY_A = AgentSidecarSpec(
    agent_id="toy-a",
    service_id="gaia-agent-toy-a",
    display_name="Toy Agent A",
    expected_api_major="1",
    token_env_var="GAIA_TOY_A_SIDECAR_TOKEN",
    mode_env_var="GAIA_TOY_A_AGENT_MODE",
    cache_dir_name="toy-a",
)

_TOY_B = AgentSidecarSpec(
    agent_id="toy-b",
    service_id="gaia-agent-toy-b",
    display_name="Toy Agent B",
    expected_api_major="1",
    token_env_var="GAIA_TOY_B_SIDECAR_TOKEN",
    mode_env_var="GAIA_TOY_B_AGENT_MODE",
    cache_dir_name="toy-b",
)


# ===========================================================================
# constants.py — version bump
# ===========================================================================


def test_daemon_api_version_is_1_1():
    from gaia.daemon.constants import DAEMON_API_VERSION

    assert DAEMON_API_VERSION == "1.1"


def test_daemon_api_version_major_still_parses_to_1():
    from gaia.daemon.constants import DAEMON_API_VERSION

    assert int(DAEMON_API_VERSION.split(".")[0]) == 1


# ===========================================================================
# paths.py — atomic_write_json + sidecars_ledger_path
# ===========================================================================


def test_sidecars_ledger_path_under_host_dir(daemon_home):
    from gaia.daemon import paths

    p = paths.sidecars_ledger_path()
    assert p == paths.host_dir() / "sidecars.json"


def test_atomic_write_json_mode_0600_and_roundtrip(daemon_home, tmp_path):
    import os
    import stat

    from gaia.daemon import paths

    target = tmp_path / "somefile.json"
    payload = {"a": 1, "b": [1, 2, 3]}
    paths.atomic_write_json(target, payload)

    assert target.exists()
    if os.name != "nt":
        assert oct(stat.S_IMODE(os.stat(target).st_mode)) == "0o600"
    with open(target, encoding="utf-8") as f:
        assert json.load(f) == payload


# ===========================================================================
# sidecars/errors.py — new error classes
# ===========================================================================


def test_new_registry_error_classes_are_sidecar_errors():
    from gaia.daemon.sidecars.errors import (
        CapacityError,
        ModeConflictError,
        SidecarError,
        StopFailedError,
        UnknownAgentError,
    )

    for cls in (UnknownAgentError, ModeConflictError, CapacityError, StopFailedError):
        assert issubclass(cls, SidecarError)


# ===========================================================================
# sidecars/registry.py — SidecarRegistry
# ===========================================================================


class _FakeManager:
    """Records start()/shutdown() calls and mimics AgentSidecarManager's public
    surface used by SidecarRegistry, with a controllable delay for concurrency
    testing."""

    _next_pid = [9000]

    def __init__(self, spec, mode=None, *, delay=0.0, **kwargs):
        self.spec = spec
        self._mode_override = mode
        self._delay = delay
        self._running = False
        self.port = None
        self.base_url = None
        self.api_version = "1.0"
        self.agent_version = "0.1.0"
        self.resolved_mode = None
        self.auth_token = f"tok-{spec.agent_id}"
        self.pid = None
        self.started_at = None
        self.start_calls = 0

    @property
    def mode(self):
        import os

        return self._mode_override or os.environ.get(self.spec.mode_env_var) or "user"

    @property
    def is_running(self):
        return self._running

    def start(self):
        self.start_calls += 1
        if self._delay:
            _t.sleep(self._delay)
        self.resolved_mode = self.mode
        _FakeManager._next_pid[0] += 1
        self.pid = _FakeManager._next_pid[0]
        self.port = 50000 + self.pid
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.started_at = _t.time()
        self._running = True
        return self.base_url

    def shutdown(self):
        self._running = False


def _make_registry(specs=None, *, max_live=3, manager_cls=_FakeManager):
    """Build a SidecarRegistry wired to inject _FakeManager instead of the real
    AgentSidecarManager. Uses the registry's manager-construction seam."""
    from gaia.daemon.sidecars.registry import SidecarRegistry

    reg = SidecarRegistry(specs or {"email": builtin_specs()["email"]}, max_live=max_live)
    # Injection seam: the registry must expose an overridable manager factory so
    # tests never spawn a real AgentSidecarManager/subprocess.
    reg._manager_factory = manager_cls  # type: ignore[attr-defined]
    return reg


def test_max_live_sidecars_default_constant():
    from gaia.daemon.sidecars.registry import MAX_LIVE_SIDECARS

    assert MAX_LIVE_SIDECARS == 3


def test_registry_default_max_live_matches_constant():
    from gaia.daemon.sidecars.registry import MAX_LIVE_SIDECARS, SidecarRegistry

    reg = SidecarRegistry({"email": builtin_specs()["email"]})
    assert reg.max_live == MAX_LIVE_SIDECARS


def test_ensure_unknown_agent_raises_and_lists_registered_ids():
    from gaia.daemon.sidecars.errors import UnknownAgentError

    reg = _make_registry({"email": builtin_specs()["email"]})
    with pytest.raises(UnknownAgentError) as exc_info:
        reg.ensure("bogus-agent")
    assert "email" in str(exc_info.value)


def test_ensure_starts_and_returns_expected_fields():
    reg = _make_registry({"toy-a": _TOY_A})
    result = reg.ensure("toy-a")
    assert result["agent_id"] == "toy-a"
    assert result["state"] == "running"
    assert result["mode"] in ("user", "dev")
    assert isinstance(result["pid"], int)
    assert isinstance(result["port"], int)
    assert result["base_url"].startswith("http://127.0.0.1:")
    assert result["api_version"]
    assert result["agent_version"]
    assert result["started_at"]
    assert "dev_src_dir" in result
    assert result["token"]  # ensure() includes the token


def test_ensure_is_idempotent_when_already_running():
    reg = _make_registry({"toy-a": _TOY_A})
    first = reg.ensure("toy-a")
    second = reg.ensure("toy-a")
    assert first["pid"] == second["pid"]
    assert first["port"] == second["port"]


def test_ensure_concurrent_first_calls_spawn_exactly_one_manager():
    """N=4 threads calling ensure() for the SAME never-before-started agent must
    collapse to exactly one spawned manager / one start() call."""
    created = []

    class _SlowFakeManager(_FakeManager):
        def __init__(self, spec, mode=None, **kwargs):
            super().__init__(spec, mode=mode, delay=0.2, **kwargs)
            created.append(self)

    reg = _make_registry({"toy-a": _TOY_A}, manager_cls=_SlowFakeManager)

    results = []

    def _run():
        results.append(reg.ensure("toy-a"))

    threads = [threading.Thread(target=_run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert len(created) == 1, "exactly one manager instance must be constructed"
    assert created[0].start_calls == 1, "start() must be invoked exactly once"
    pids = {r["pid"] for r in results}
    assert pids == {created[0].pid}


def test_ensure_mode_conflict_raises_naming_both_modes(monkeypatch):
    from gaia.daemon.sidecars.errors import ModeConflictError

    monkeypatch.setenv(_TOY_A.mode_env_var, "dev")
    reg = _make_registry({"toy-a": _TOY_A})
    reg.ensure("toy-a", mode="dev")
    with pytest.raises(ModeConflictError) as exc_info:
        reg.ensure("toy-a", mode="user")
    msg = str(exc_info.value)
    assert "dev" in msg and "user" in msg


def test_ensure_reensure_matches_captured_resolved_mode_not_live_env(monkeypatch):
    """Start in 'dev', then the env var changes to 'user' (a live-mode change with
    no restart). A subsequent ensure(mode='dev') must NOT conflict — it compares
    against the manager's captured resolved_mode. ensure(mode='user') DOES
    conflict, since the running sidecar is still actually 'dev'."""
    from gaia.daemon.sidecars.errors import ModeConflictError

    monkeypatch.setenv(_TOY_A.mode_env_var, "dev")
    reg = _make_registry({"toy-a": _TOY_A})
    reg.ensure("toy-a", mode="dev")

    monkeypatch.setenv(_TOY_A.mode_env_var, "user")

    # No conflict: matches the captured resolved_mode ("dev"), not the live env.
    result = reg.ensure("toy-a", mode="dev")
    assert result["state"] == "running"

    with pytest.raises(ModeConflictError):
        reg.ensure("toy-a", mode="user")


def test_ensure_capacity_error_names_running_agents():
    from gaia.daemon.sidecars.errors import CapacityError

    reg = _make_registry({"toy-a": _TOY_A, "toy-b": _TOY_B}, max_live=1)
    reg.ensure("toy-a")
    with pytest.raises(CapacityError) as exc_info:
        reg.ensure("toy-b")
    assert "toy-a" in str(exc_info.value)


def test_list_agents_includes_every_registered_spec_running_or_not():
    reg = _make_registry({"toy-a": _TOY_A, "toy-b": _TOY_B})
    reg.ensure("toy-a")
    entries = {e["agent_id"]: e for e in reg.list_agents()}
    assert set(entries) == {"toy-a", "toy-b"}
    assert entries["toy-a"]["state"] == "running"
    assert entries["toy-a"]["pid"] is not None
    assert entries["toy-b"]["state"] == "stopped"
    assert entries["toy-b"]["pid"] is None
    assert entries["toy-b"]["port"] is None
    assert entries["toy-b"]["base_url"] is None


def test_list_agents_entries_never_contain_a_token_key():
    reg = _make_registry({"toy-a": _TOY_A, "toy-b": _TOY_B})
    reg.ensure("toy-a")
    for entry in reg.list_agents():
        assert "token" not in entry


def test_stop_unknown_agent_raises():
    from gaia.daemon.sidecars.errors import UnknownAgentError

    reg = _make_registry({"toy-a": _TOY_A})
    with pytest.raises(UnknownAgentError):
        reg.stop("bogus-agent")


def test_stop_not_running_is_a_noop_returning_stopped_state():
    reg = _make_registry({"toy-a": _TOY_A})
    result = reg.stop("toy-a")
    assert result["agent_id"] == "toy-a"
    assert result["state"] == "stopped"


def test_stop_running_shuts_down_and_verifies_pid_gone(monkeypatch):
    reg = _make_registry({"toy-a": _TOY_A})
    reg.ensure("toy-a")

    import gaia.daemon.sidecars.registry as registry_mod

    monkeypatch.setattr(registry_mod.psutil, "pid_exists", lambda pid: False)
    result = reg.stop("toy-a")
    assert result["state"] == "stopped"


def test_stop_survivor_pid_raises_stop_failed_error(monkeypatch):
    from gaia.daemon.sidecars.errors import StopFailedError

    reg = _make_registry({"toy-a": _TOY_A})
    ensured = reg.ensure("toy-a")

    import gaia.daemon.sidecars.registry as registry_mod

    # shutdown() "succeeds" but the pid stubbornly still exists afterward.
    monkeypatch.setattr(registry_mod.psutil, "pid_exists", lambda pid: True)
    with pytest.raises(StopFailedError) as exc_info:
        reg.stop("toy-a")
    assert str(ensured["pid"]) in str(exc_info.value)


def test_shutdown_all_stops_every_running_manager():
    reg = _make_registry({"toy-a": _TOY_A, "toy-b": _TOY_B})
    reg.ensure("toy-a")
    reg.ensure("toy-b")
    reg.shutdown_all()
    entries = {e["agent_id"]: e for e in reg.list_agents()}
    assert entries["toy-a"]["state"] == "stopped"
    assert entries["toy-b"]["state"] == "stopped"


# ===========================================================================
# sidecars/ledger.py — spawn ledger for crash-reap
# ===========================================================================


def test_record_spawn_then_read_entries_roundtrip(daemon_home):
    from gaia.daemon.sidecars import ledger

    ledger.record_spawn(
        agent_id="email",
        pid=1234,
        port=55123,
        mode="dev",
        argv=["uvicorn", "server:app"],
        started_at=1234567890.0,
    )
    entries = ledger.read_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["agent_id"] == "email"
    assert e["pid"] == 1234
    assert e["port"] == 55123
    assert e["mode"] == "dev"
    assert e["argv"] == ["uvicorn", "server:app"]
    assert e["started_at"] == 1234567890.0


def test_record_spawn_replaces_existing_entry_for_same_agent(daemon_home):
    from gaia.daemon.sidecars import ledger

    ledger.record_spawn(
        agent_id="email", pid=1, port=100, mode="user", argv=["a"], started_at=1.0
    )
    ledger.record_spawn(
        agent_id="email", pid=2, port=200, mode="dev", argv=["b"], started_at=2.0
    )
    entries = ledger.read_entries()
    assert len(entries) == 1
    assert entries[0]["pid"] == 2
    assert entries[0]["port"] == 200


def test_remove_entry_deletes_only_the_named_agent(daemon_home):
    from gaia.daemon.sidecars import ledger

    ledger.record_spawn(
        agent_id="email", pid=1, port=100, mode="user", argv=["a"], started_at=1.0
    )
    ledger.record_spawn(
        agent_id="toy-a", pid=2, port=200, mode="user", argv=["b"], started_at=2.0
    )
    ledger.remove_entry("email")
    entries = ledger.read_entries()
    assert len(entries) == 1
    assert entries[0]["agent_id"] == "toy-a"


def test_ledger_file_never_contains_a_token_field(daemon_home):
    from gaia.daemon.sidecars import ledger
    from gaia.daemon import paths

    ledger.record_spawn(
        agent_id="email", pid=1, port=100, mode="user", argv=["a"], started_at=1.0
    )
    ledger.record_spawn(
        agent_id="toy-a", pid=2, port=200, mode="dev", argv=["b"], started_at=2.0
    )
    ledger.remove_entry("email")
    ledger.record_spawn(
        agent_id="email", pid=3, port=300, mode="user", argv=["c"], started_at=3.0
    )
    raw = paths.sidecars_ledger_path().read_text(encoding="utf-8")
    assert "token" not in raw


def test_concurrent_record_spawn_for_different_agents_no_lost_update(daemon_home):
    """Two threads doing read-modify-write record_spawn calls for TWO DIFFERENT
    agent ids, interleaved 20x each, must never lose an update — both final
    entries survive."""
    from gaia.daemon.sidecars import ledger

    def _hammer(agent_id, base_port):
        for i in range(20):
            ledger.record_spawn(
                agent_id=agent_id,
                pid=1000 + i,
                port=base_port + i,
                mode="user",
                argv=[agent_id],
                started_at=float(i),
            )

    t1 = threading.Thread(target=_hammer, args=("toy-a", 10000))
    t2 = threading.Thread(target=_hammer, args=("toy-b", 20000))
    t1.start()
    t2.start()
    t1.join(10)
    t2.join(10)

    entries = {e["agent_id"]: e for e in ledger.read_entries()}
    assert set(entries) == {"toy-a", "toy-b"}
    # Last write of each thread's sequence must be the one that stuck.
    assert entries["toy-a"]["pid"] == 1000 + 19
    assert entries["toy-b"]["pid"] == 1000 + 19


# --- reap_stale() identity matrix ------------------------------------------


def _reap_specs():
    return {"toy-a": _TOY_A, "toy-b": _TOY_B}


def test_reap_stale_kills_when_health_probe_confirms_identity(daemon_home, monkeypatch):
    from gaia.daemon.sidecars import ledger

    ledger.record_spawn(
        agent_id="toy-a",
        pid=4242,
        port=51001,
        mode="user",
        argv=["/path/to/toy-a-agent", "--port", "51001"],
        started_at=1.0,
    )

    killed = []
    monkeypatch.setattr(
        ledger,
        "_probe_health",
        lambda port: {"service": "gaia-agent-toy-a"} if port == 51001 else None,
    )
    monkeypatch.setattr(ledger, "_pid_cmdline", lambda pid: None)
    monkeypatch.setattr(ledger, "_tree_kill", lambda pid: killed.append(pid))

    result = ledger.reap_stale(_reap_specs())
    assert result == [4242]
    assert killed == [4242]
    assert ledger.read_entries() == []


def test_reap_stale_falls_back_to_cmdline_match_when_probe_fails(
    daemon_home, monkeypatch
):
    from gaia.daemon.sidecars import ledger

    argv = ["/path/to/toy-a-agent", "--port", "51002"]
    ledger.record_spawn(
        agent_id="toy-a", pid=4243, port=51002, mode="user", argv=argv, started_at=1.0
    )

    killed = []
    monkeypatch.setattr(ledger, "_probe_health", lambda port: None)  # probe fails
    monkeypatch.setattr(
        ledger,
        "_pid_cmdline",
        lambda pid: "/path/to/toy-a-agent --host 127.0.0.1 --port 51002",
    )
    monkeypatch.setattr(ledger, "_tree_kill", lambda pid: killed.append(pid))

    result = ledger.reap_stale(_reap_specs())
    assert result == [4243]
    assert killed == [4243]
    assert ledger.read_entries() == []


def test_reap_stale_does_not_kill_on_pid_reuse_no_identity_match(
    daemon_home, monkeypatch
):
    from gaia.daemon.sidecars import ledger

    argv = ["/path/to/toy-a-agent", "--port", "51003"]
    ledger.record_spawn(
        agent_id="toy-a", pid=4244, port=51003, mode="user", argv=argv, started_at=1.0
    )

    killed = []
    monkeypatch.setattr(ledger, "_probe_health", lambda port: None)  # probe fails
    # Cmdline belongs to a totally unrelated process now sitting on the reused pid.
    monkeypatch.setattr(ledger, "_pid_cmdline", lambda pid: "/usr/bin/some-other-proc")
    monkeypatch.setattr(ledger, "_tree_kill", lambda pid: killed.append(pid))

    result = ledger.reap_stale(_reap_specs())
    assert result == []
    assert killed == []
    # The ledger is truncated to [] after the pass regardless of outcome.
    assert ledger.read_entries() == []


# ===========================================================================
# sidecars/routes.py — build_agents_router() HTTP mapping
# ===========================================================================


class _FakeRegistry:
    """Duck-typed registry stub for route-layer tests. Route tests verify HTTP
    status/body mapping only — registry logic is covered above."""

    def __init__(self, *, ensure_result=None, ensure_error=None, list_result=None,
                 stop_result=None, stop_error=None):
        self._ensure_result = ensure_result
        self._ensure_error = ensure_error
        self._list_result = list_result if list_result is not None else []
        self._stop_result = stop_result or {"agent_id": "email", "state": "stopped"}
        self._stop_error = stop_error
        self.ensure_calls = []
        self.stop_calls = []

    def ensure(self, agent_id, mode=None):
        self.ensure_calls.append((agent_id, mode))
        if self._ensure_error:
            raise self._ensure_error
        return self._ensure_result

    def list_agents(self):
        return self._list_result

    def stop(self, agent_id):
        self.stop_calls.append(agent_id)
        if self._stop_error:
            raise self._stop_error
        return self._stop_result


def _routes_client(registry, token="secret-tok"):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.daemon.sidecars.routes import build_agents_router

    app = FastAPI()
    app.include_router(build_agents_router(token, registry))
    return TestClient(app, raise_server_exceptions=False)


def _auth(token="secret-tok"):
    return {"Authorization": f"Bearer {token}"}


def test_get_agents_returns_list_agents_payload():
    reg = _FakeRegistry(
        list_result=[
            {"agent_id": "email", "state": "stopped", "mode": "user", "pid": None}
        ]
    )
    client = _routes_client(reg)
    r = client.get("/daemon/v1/agents", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["agents"][0]["agent_id"] == "email"


def test_get_agents_entries_never_contain_token():
    reg = _FakeRegistry(
        list_result=[{"agent_id": "email", "state": "stopped", "mode": "user"}]
    )
    client = _routes_client(reg)
    r = client.get("/daemon/v1/agents", headers=_auth())
    for entry in r.json()["agents"]:
        assert "token" not in entry


def test_post_ensure_returns_token_in_body():
    reg = _FakeRegistry(
        ensure_result={
            "agent_id": "email",
            "state": "running",
            "mode": "user",
            "pid": 111,
            "port": 55000,
            "base_url": "http://127.0.0.1:55000",
            "api_version": "2.0",
            "agent_version": "0.2.0",
            "started_at": 123.0,
            "dev_src_dir": None,
            "token": "the-sidecar-token",
        }
    )
    client = _routes_client(reg)
    r = client.post("/daemon/v1/agents/email/ensure", headers=_auth(), json={})
    assert r.status_code == 200
    assert r.json()["token"] == "the-sidecar-token"


def test_post_ensure_omitted_body_defaults_mode_to_none():
    reg = _FakeRegistry(
        ensure_result={"agent_id": "email", "state": "running", "mode": "user", "token": "t"}
    )
    client = _routes_client(reg)
    r = client.post("/daemon/v1/agents/email/ensure", headers=_auth())
    assert r.status_code == 200
    assert reg.ensure_calls == [("email", None)]


def test_post_ensure_passes_mode_through():
    reg = _FakeRegistry(
        ensure_result={"agent_id": "email", "state": "running", "mode": "dev", "token": "t"}
    )
    client = _routes_client(reg)
    r = client.post(
        "/daemon/v1/agents/email/ensure", headers=_auth(), json={"mode": "dev"}
    )
    assert r.status_code == 200
    assert reg.ensure_calls == [("email", "dev")]


def test_post_stop_returns_stopped_state():
    reg = _FakeRegistry(stop_result={"agent_id": "email", "state": "stopped"})
    client = _routes_client(reg)
    r = client.post("/daemon/v1/agents/email/stop", headers=_auth())
    assert r.status_code == 200
    assert r.json() == {"agent_id": "email", "state": "stopped"}


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/daemon/v1/agents"),
        ("post", "/daemon/v1/agents/email/ensure"),
        ("post", "/daemon/v1/agents/email/stop"),
    ],
)
def test_all_agent_routes_require_a_valid_token(method, url):
    reg = _FakeRegistry()
    client = _routes_client(reg)
    r = getattr(client, method)(url)  # no Authorization header
    assert r.status_code == 401
    detail = r.json()["detail"]
    assert "Authorization" in detail


@pytest.mark.parametrize(
    "method,url",
    [
        ("get", "/daemon/v1/agents"),
        ("post", "/daemon/v1/agents/email/ensure"),
        ("post", "/daemon/v1/agents/email/stop"),
    ],
)
def test_all_agent_routes_reject_wrong_token(method, url):
    reg = _FakeRegistry()
    client = _routes_client(reg)
    r = getattr(client, method)(url, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_unknown_agent_error_maps_to_404_listing_registered_ids():
    from gaia.daemon.sidecars.errors import UnknownAgentError

    reg = _FakeRegistry(ensure_error=UnknownAgentError("unknown agent 'bogus'; registered: email"))
    client = _routes_client(reg)
    r = client.post("/daemon/v1/agents/bogus/ensure", headers=_auth(), json={})
    assert r.status_code == 404
    assert "email" in r.json()["detail"]


def test_mode_conflict_error_maps_to_409_naming_both_modes():
    from gaia.daemon.sidecars.errors import ModeConflictError

    reg = _FakeRegistry(
        ensure_error=ModeConflictError(
            "email is running in 'dev' but 'user' was requested; stop it first"
        )
    )
    client = _routes_client(reg)
    r = client.post(
        "/daemon/v1/agents/email/ensure", headers=_auth(), json={"mode": "user"}
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "dev" in detail and "user" in detail


def test_capacity_error_maps_to_409_naming_running_agents():
    from gaia.daemon.sidecars.errors import CapacityError

    reg = _FakeRegistry(
        ensure_error=CapacityError("capacity reached (max 3); running: email, toy-a")
    )
    client = _routes_client(reg)
    r = client.post("/daemon/v1/agents/toy-b/ensure", headers=_auth(), json={})
    assert r.status_code == 409
    assert "email" in r.json()["detail"]


@pytest.mark.parametrize(
    "err_cls_name",
    ["SidecarSpawnError", "HealthTimeoutError", "VersionMismatchError"],
)
def test_manager_level_ensure_errors_map_to_502_with_verbatim_detail(err_cls_name):
    from gaia.daemon.sidecars import errors as sidecar_errors

    err_cls = getattr(sidecar_errors, err_cls_name)
    distinctive = f"DISTINCTIVE-{err_cls_name}-MESSAGE"
    reg = _FakeRegistry(ensure_error=err_cls(distinctive))
    client = _routes_client(reg)
    r = client.post("/daemon/v1/agents/email/ensure", headers=_auth(), json={})
    assert r.status_code == 502
    assert distinctive in r.json()["detail"]


def test_stop_failed_error_maps_to_500_naming_surviving_pid():
    from gaia.daemon.sidecars.errors import StopFailedError

    reg = _FakeRegistry(stop_error=StopFailedError("pid 4242 survived shutdown"))
    client = _routes_client(reg)
    r = client.post("/daemon/v1/agents/email/stop", headers=_auth())
    assert r.status_code == 500
    assert "4242" in r.json()["detail"]


def test_stop_unknown_agent_maps_to_404():
    from gaia.daemon.sidecars.errors import UnknownAgentError

    reg = _FakeRegistry(
        stop_error=UnknownAgentError("unknown agent 'bogus'; registered: email")
    )
    client = _routes_client(reg)
    r = client.post("/daemon/v1/agents/bogus/stop", headers=_auth())
    assert r.status_code == 404
    assert "email" in r.json()["detail"]


# ===========================================================================
# app.py wiring — create_app(..., registry=...) mounts /daemon/v1/agents
# ===========================================================================


def test_create_app_mounts_agents_router_when_registry_given():
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    reg = _FakeRegistry(list_result=[])
    app = create_app(
        token="tok-1",
        port=4001,
        pid=1,
        started_at=_t.time(),
        registry=reg,
    )
    client = TestClient(app)
    r = client.get("/daemon/v1/agents", headers=_auth("tok-1"))
    assert r.status_code == 200


def test_create_app_without_registry_does_not_mount_agents_routes():
    from fastapi.testclient import TestClient

    from gaia.daemon.app import create_app

    app = create_app(
        token="tok-1",
        port=4001,
        pid=1,
        started_at=_t.time(),
        registry=None,
    )
    client = TestClient(app)
    r = client.get("/daemon/v1/agents", headers=_auth("tok-1"))
    assert r.status_code == 404
