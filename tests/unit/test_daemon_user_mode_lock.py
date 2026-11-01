# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""User mode must not silently serve the daemon's binary pin to a caller whose
checkout pins a different one (#2596, the user-mode half of #2588)."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

import pytest

from gaia.daemon.sidecars import platform as plat
from gaia.daemon.sidecars import spec as spec_mod
from gaia.daemon.sidecars.errors import DevSrcDirResolutionError, ModeConflictError
from gaia.daemon.sidecars.registry import SidecarRegistry
from gaia.daemon.sidecars.spec import AgentSidecarSpec

_SPEC = AgentSidecarSpec(
    agent_id="email",
    service_id="gaia-agent-email",
    display_name="Email",
    expected_api_major="1",
    token_env_var="GAIA_LOCKTEST_SIDECAR_TOKEN",
    mode_env_var="GAIA_LOCKTEST_AGENT_MODE",
    cache_dir_name="email",
)


def _write_lock(repo_root: Path, version: str) -> Path:
    lock = spec_mod.agent_binary_lock_path(repo_root, "email")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('{"agentVersion": "%s", "binaries": {}}' % version)
    return lock


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Manager:
    def __init__(self, spec, mode=None, **kwargs):
        self.spec = spec
        self._mode = mode
        self.lock_path = None
        self.resolved_mode = None
        self.is_running = False
        self.pid = self.port = self.base_url = self.started_at = None
        self.api_version = "1.0"
        self.agent_version = "0.1.0"
        self.auth_token = "tok"
        self.start_calls = 0

    @property
    def mode(self):
        return self._mode or "user"

    def start(self):
        self.start_calls += 1
        self.resolved_mode = self.mode
        self.is_running = True
        self.pid, self.port = 4242, 51234


@pytest.fixture
def daemon_lock(tmp_path, monkeypatch):
    lock = _write_lock(tmp_path / "daemon-checkout", "1.0.0")
    monkeypatch.setattr(plat, "default_lock_path", lambda: lock)
    return lock


@pytest.fixture
def registry():
    reg = SidecarRegistry({"email": _SPEC})
    reg._manager_factory = _Manager
    return reg


# --- path helpers -----------------------------------------------------------


def test_lock_path_and_repo_root_invert_each_other(tmp_path):
    lock = spec_mod.agent_binary_lock_path(tmp_path, "email")
    assert lock == tmp_path / "hub" / "agents" / "email" / "npm" / "binaries.lock.json"
    assert spec_mod.repo_root_from_agent_binary_lock_path(lock, "email") == tmp_path


@pytest.mark.parametrize(
    "tail",
    [
        "hub/agents/email/npm/other.json",
        "hub/agents/gaia/npm/binaries.lock.json",
        "binaries.lock.json",
    ],
)
def test_repo_root_refuses_a_mis_shaped_lock_path(tmp_path, tail):
    with pytest.raises(DevSrcDirResolutionError, match="expected"):
        spec_mod.repo_root_from_agent_binary_lock_path(tmp_path / tail, "email")


def test_default_lock_path_is_this_checkouts_email_lock():
    repo_root = Path(spec_mod.__file__).resolve().parents[4]
    assert plat.default_lock_path() == spec_mod.agent_binary_lock_path(
        repo_root, "email"
    )
    assert plat.default_lock_path().is_file()


# --- caller-side resolution -------------------------------------------------


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_caller_lock_is_found_from_a_subdirectory_of_the_checkout(tmp_path):
    repo = tmp_path / "caller"
    _git_init(repo)
    lock = _write_lock(repo, "2.0.0")
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    assert spec_mod.resolve_caller_binary_lock("email", cwd=sub) == lock.resolve()


def test_caller_in_a_checkout_without_a_lock_sends_nothing(tmp_path):
    repo = tmp_path / "unrelated-repo"
    _git_init(repo)
    assert spec_mod.resolve_caller_binary_lock("email", cwd=repo) is None


def test_caller_outside_any_checkout_sends_nothing(tmp_path, monkeypatch):
    def _no_git(*a, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(spec_mod.subprocess, "run", _no_git)
    assert spec_mod.resolve_caller_binary_lock("email", cwd=tmp_path) is None


def test_caller_lock_that_is_a_directory_is_not_a_pin(tmp_path):
    repo = tmp_path / "caller"
    _git_init(repo)
    spec_mod.agent_binary_lock_path(repo, "email").mkdir(parents=True)
    assert spec_mod.resolve_caller_binary_lock("email", cwd=repo) is None


# --- daemon-side comparison -------------------------------------------------


def test_matching_pin_is_served_and_the_digest_echoed(registry, daemon_lock):
    entry = registry.ensure(
        "email",
        mode="user",
        binary_lock_sha256=_sha(daemon_lock),
        binary_lock_path="/elsewhere/hub/agents/email/npm/binaries.lock.json",
    )
    assert entry["state"] == "running"
    assert entry["binary_lock_sha256"] == _sha(daemon_lock)


def test_digest_comparison_ignores_hex_case(registry, daemon_lock):
    entry = registry.ensure(
        "email", mode="user", binary_lock_sha256=_sha(daemon_lock).upper()
    )
    assert entry["binary_lock_sha256"] == _sha(daemon_lock)


def test_mismatched_pin_is_refused_naming_both_locks_and_the_repo_root(
    registry, daemon_lock, tmp_path
):
    caller = _write_lock(tmp_path / "caller-checkout", "2.0.0")
    with pytest.raises(ModeConflictError) as exc:
        registry.ensure(
            "email",
            mode="user",
            binary_lock_sha256=_sha(caller),
            binary_lock_path=str(caller),
        )
    msg = str(exc.value)
    assert str(caller) in msg
    assert str(daemon_lock) in msg
    assert f"rooted at {tmp_path / 'caller-checkout'}." in msg
    manager, _ = registry._managers["email"]
    assert manager.start_calls == 0


def test_mismatched_pin_is_refused_on_attach_too(registry, daemon_lock, tmp_path):
    registry.ensure("email", mode="user")
    caller = _write_lock(tmp_path / "caller-checkout", "2.0.0")
    with pytest.raises(ModeConflictError):
        registry.ensure(
            "email",
            mode="user",
            binary_lock_sha256=_sha(caller),
            binary_lock_path=str(caller),
        )


def test_daemon_without_a_lock_refuses_a_pinning_caller(
    registry, tmp_path, monkeypatch
):
    missing = tmp_path / "wheel" / "hub/agents/email/npm/binaries.lock.json"
    monkeypatch.setattr(plat, "default_lock_path", lambda: missing)
    caller = _write_lock(tmp_path / "caller-checkout", "2.0.0")
    with pytest.raises(ModeConflictError, match="has no binaries.lock.json"):
        registry.ensure(
            "email",
            mode="user",
            binary_lock_sha256=_sha(caller),
            binary_lock_path=str(caller),
        )


def test_an_explicit_manager_lock_wins_over_the_default(
    registry, daemon_lock, tmp_path
):
    override = _write_lock(tmp_path / "override", "3.0.0")
    holder = registry._holder("email", _SPEC)
    holder[0].lock_path = override
    entry = registry.ensure("email", mode="user", binary_lock_sha256=_sha(override))
    assert entry["binary_lock_sha256"] == _sha(override)


def test_mis_shaped_caller_path_is_rejected_not_guessed(registry, daemon_lock):
    with pytest.raises(DevSrcDirResolutionError):
        registry.ensure(
            "email",
            mode="user",
            binary_lock_sha256="0" * 64,
            binary_lock_path="/tmp/not-a-lock.json",
        )


def test_no_caller_digest_means_no_check_and_no_echo(registry, tmp_path, monkeypatch):
    monkeypatch.setattr(
        plat, "default_lock_path", lambda: tmp_path / "absent" / "lock.json"
    )
    entry = registry.ensure("email", mode="user")
    assert entry["state"] == "running"
    assert "binary_lock_sha256" not in entry


def test_dev_mode_ignores_the_lock_digest(registry, daemon_lock):
    entry = registry.ensure("email", mode="dev", binary_lock_sha256="0" * 64)
    assert "binary_lock_sha256" not in entry


# --- HTTP route ---------------------------------------------------------------


class _RecordingRegistry:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def ensure(self, agent_id, **kwargs):
        self.calls.append((agent_id, kwargs))
        if self._error:
            raise self._error
        return {"agent_id": agent_id, "state": "running", "token": "t"}


def _client(reg):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.daemon.sidecars.routes import build_agents_router

    app = FastAPI()
    app.include_router(build_agents_router("tok", reg))
    return TestClient(app, raise_server_exceptions=False)


def _post(reg, body):
    return _client(reg).post(
        "/daemon/v1/agents/email/ensure",
        headers={"Authorization": "Bearer tok"},
        json=body,
    )


def test_route_forwards_the_digest_and_path_to_the_registry():
    reg = _RecordingRegistry()
    r = _post(
        reg,
        {"mode": "user", "binary_lock_sha256": "ab" * 32, "binary_lock_path": "/p"},
    )
    assert r.status_code == 200
    assert reg.calls == [
        (
            "email",
            {
                "mode": "user",
                "dev_src_dir": None,
                "binary_lock_sha256": "ab" * 32,
                "binary_lock_path": "/p",
            },
        )
    ]


def test_route_omits_lock_kwargs_when_the_caller_sends_none():
    reg = _RecordingRegistry()
    assert _post(reg, {"mode": "user"}).status_code == 200
    assert reg.calls == [("email", {"mode": "user", "dev_src_dir": None})]


@pytest.mark.parametrize(
    "body",
    [
        {"binary_lock_sha256": 123},
        {"binary_lock_sha256": "ab", "binary_lock_path": ["x"]},
    ],
)
def test_route_rejects_non_string_lock_fields(body):
    reg = _RecordingRegistry()
    r = _post(reg, {"mode": "user", **body})
    assert r.status_code == 400
    assert reg.calls == []


def test_route_maps_a_pin_mismatch_to_409():
    reg = _RecordingRegistry(error=ModeConflictError("pins differ"))
    r = _post(reg, {"mode": "user", "binary_lock_sha256": "ab" * 32})
    assert r.status_code == 409
    assert "pins differ" in r.text


# --- CLI ----------------------------------------------------------------------


class _Post:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.bodies = []

    def __call__(self, url, **kwargs):
        self.bodies.append(kwargs.get("json"))
        return self

    def json(self):
        return self._payload


def _run_cli(monkeypatch, tmp_path, payload, *, mode="user"):
    import requests

    import gaia.cli as cli
    import gaia.daemon.client as client_mod
    from gaia.daemon.instance import DaemonInstance

    caller = _write_lock(tmp_path / "caller-checkout", "2.0.0")
    monkeypatch.setattr(
        client_mod,
        "start_or_attach",
        lambda **k: DaemonInstance(pid=1, port=2, token="t", api_version="1.1"),
    )
    monkeypatch.setattr(
        spec_mod, "resolve_caller_binary_lock", lambda agent_id, cwd=None: caller
    )
    monkeypatch.setattr(
        spec_mod, "resolve_caller_dev_src_dir", lambda agent_id, explicit=None: None
    )
    post = _Post(payload(caller) if callable(payload) else payload)
    monkeypatch.setattr(requests, "post", post)
    cli._handle_daemon_start_agent(
        argparse.Namespace(agent_id="email", mode=mode, dev_src_dir=None)
    )
    return caller, post


def _ok(**extra):
    base = {"agent_id": "email", "state": "running", "mode": "user", "pid": 1}
    base.update(extra)
    return base


def test_cli_sends_its_own_lock_digest_and_path(monkeypatch, tmp_path, capsys):
    caller, post = _run_cli(
        monkeypatch, tmp_path, lambda c: _ok(binary_lock_sha256=_sha(c))
    )
    assert post.bodies == [
        {
            "mode": "user",
            "binary_lock_sha256": _sha(caller),
            "binary_lock_path": str(caller),
        }
    ]
    assert "✅" in capsys.readouterr().out


def test_cli_refuses_when_an_old_daemon_does_not_echo_the_digest(
    monkeypatch, tmp_path, capsys
):
    with pytest.raises(SystemExit) as exc:
        _run_cli(monkeypatch, tmp_path, _ok())
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "did not confirm" in out and "✅" not in out


def test_cli_refuses_a_daemon_echoing_a_different_digest(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, tmp_path, _ok(binary_lock_sha256="0" * 64))
    assert "✅" not in capsys.readouterr().out


def test_cli_in_dev_mode_never_sends_a_lock_digest(monkeypatch, tmp_path):
    _, post = _run_cli(
        monkeypatch,
        tmp_path,
        _ok(mode="dev"),
        mode="dev",
    )
    assert "binary_lock_sha256" not in post.bodies[0]
