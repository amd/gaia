# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AgentSidecarManager: the generalized, spec-driven successor to
EmailSidecarManager — mode select, ephemeral port, spawn-arg shape, health,
tree-kill, PLUS the new spec-parametrization and resolved_mode-capture
mechanics (#2142 T1)."""

import dataclasses
import importlib.util
import sys
import threading
import time as _t
from pathlib import Path

import pytest

from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.errors import (
    BinaryNotFoundError,
    HealthTimeoutError,
    IntegrityError,
    PlatformError,
    RouteNotAvailableError,
    SidecarError,
    SidecarHTTPError,
    SidecarSpawnError,
    VersionMismatchError,
)
from gaia.daemon.sidecars.spec import AgentSidecarSpec, builtin_specs

# ---------------------------------------------------------------------------
# spec.py — AgentSidecarSpec + builtin_specs()
# ---------------------------------------------------------------------------


def test_spec_is_frozen():
    spec = builtin_specs()["email"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.agent_id = "not-email"


def test_builtin_specs_has_an_email_entry():
    specs = builtin_specs()
    assert "email" in specs
    email = specs["email"]
    assert email.agent_id == "email"
    assert email.service_id == "gaia-agent-email"
    assert email.expected_api_major == "2"
    assert email.cache_dir_name == "email"


def test_builtin_email_spec_token_env_var_matches_the_hub_wheel_contract():
    # Cross-repo literal: must equal gaia_agent_email.caller_auth.TOKEN_ENV_VAR.
    # Never silently drift — this env var is how the daemon hands the sidecar
    # its per-session auth token.
    assert builtin_specs()["email"].token_env_var == "GAIA_EMAIL_SIDECAR_TOKEN"


def test_builtin_email_spec_mode_env_var():
    assert builtin_specs()["email"].mode_env_var == "GAIA_EMAIL_AGENT_MODE"


def test_email_spec_dev_src_dir_resolves_under_repo_root():
    # tests/unit/test_agent_sidecar_manager.py -> repo root is parents[2].
    repo_root = Path(__file__).resolve().parents[2]
    expected = repo_root / "hub" / "agents" / "python" / "email"
    assert builtin_specs()["email"].dev_src_dir == expected


# ---------------------------------------------------------------------------
# errors.py — relocated hierarchy + backward-compat shim
# ---------------------------------------------------------------------------


def test_error_hierarchy_relocated_verbatim():
    assert issubclass(SidecarError, Exception)
    for cls in (
        PlatformError,
        IntegrityError,
        BinaryNotFoundError,
        HealthTimeoutError,
        SidecarSpawnError,
        RouteNotAvailableError,
        SidecarHTTPError,
        VersionMismatchError,
    ):
        assert issubclass(cls, SidecarError)
    err = SidecarHTTPError(502, "boom", path="/v1/x")
    assert err.status_code == 502
    assert err.detail == "boom"
    assert err.path == "/v1/x"
    assert "502" in str(err) and "boom" in str(err) and "/v1/x" in str(err)


def test_ui_email_sidecar_errors_is_a_reexport_shim():
    # gaia.ui.email_sidecar.errors becomes a pure re-export shim: identity, not
    # just equality, so existing `from ... import SidecarError` callers still
    # get the exact same class object as the new location.
    import gaia.daemon.sidecars.errors as new_errors
    import gaia.ui.email_sidecar.errors as old_errors

    assert old_errors.SidecarError is new_errors.SidecarError
    assert old_errors.VersionMismatchError is new_errors.VersionMismatchError
    assert old_errors.SidecarSpawnError is new_errors.SidecarSpawnError


# ---------------------------------------------------------------------------
# manager.py — find_free_port / mode / build_spawn_command
# ---------------------------------------------------------------------------

_TOY_SPEC = AgentSidecarSpec(
    agent_id="toy",
    service_id="gaia-agent-toy",
    display_name="Toy Agent",
    expected_api_major="1",
    token_env_var="GAIA_TOY_SIDECAR_TOKEN",
    mode_env_var="GAIA_TOY_AGENT_MODE",
    cache_dir_name="toy",
)


def _email_spec_with_src(src_dir: Path) -> AgentSidecarSpec:
    return dataclasses.replace(builtin_specs()["email"], dev_src_dir=src_dir)


def test_find_free_port_never_4001():
    for _ in range(20):
        assert mgr.find_free_port() != 4001


def test_mode_defaults_to_user(monkeypatch):
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    assert mgr.AgentSidecarManager(builtin_specs()["email"]).mode == "user"


def test_mode_reads_env(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    assert mgr.AgentSidecarManager(builtin_specs()["email"]).mode == "dev"


def test_invalid_mode_raises(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "bananas")
    with pytest.raises(SidecarSpawnError, match="GAIA_EMAIL_AGENT_MODE"):
        mgr.AgentSidecarManager(builtin_specs()["email"]).mode


def test_invalid_mode_error_names_the_spec_mode_env_var_not_email(monkeypatch):
    """A non-email spec's invalid-mode error must be truly spec-driven — no
    leftover hardcoded 'GAIA_EMAIL_AGENT_MODE' string anywhere in the message."""
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    monkeypatch.setenv("GAIA_TOY_AGENT_MODE", "bananas")
    with pytest.raises(SidecarSpawnError) as exc_info:
        mgr.AgentSidecarManager(_TOY_SPEC).mode
    msg = str(exc_info.value)
    assert "GAIA_TOY_AGENT_MODE" in msg
    assert "GAIA_EMAIL_AGENT_MODE" not in msg


def test_user_mode_spawn_command_uses_fetched_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")
    fake_binary = tmp_path / "email-agent"
    fake_binary.write_bytes(b"x")

    class _Res:
        binary_path = fake_binary

    monkeypatch.setattr(mgr.fetch, "fetch_binary", lambda **kw: _Res())
    m = mgr.AgentSidecarManager(builtin_specs()["email"])
    argv, kwargs = m.build_spawn_command(port=9123)
    assert argv[0] == str(fake_binary)
    assert "--port" in argv and "9123" in argv
    assert "4001" not in argv


def test_user_mode_fetch_failure_raises_with_remedy(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")

    def _boom(**kw):
        raise RuntimeError("placeholder sha256")

    monkeypatch.setattr(mgr.fetch, "fetch_binary", _boom)
    m = mgr.AgentSidecarManager(builtin_specs()["email"])
    with pytest.raises(SidecarSpawnError, match="GAIA_EMAIL_AGENT_MODE=dev"):
        m.build_spawn_command(port=9123)


def test_user_mode_spawns_hub_installed_binary_despite_placeholder_lock(
    monkeypatch, tmp_path
):
    # #2095: a Hub-installed, checksum-verified binary must spawn even while
    # binaries.lock.json still ships placeholder SHAs. Real fetch, no mocks —
    # exercises the new gaia.daemon.sidecars.fetch module for real.
    import hashlib
    import json

    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")
    cache = tmp_path / "email"
    cache.mkdir()
    data = b"hub-verified-binary"
    binary = cache / "email-agent"
    binary.write_bytes(data)
    (cache / ".installed").write_text(
        json.dumps(
            {
                "id": "email",
                "version": "0.5.0",
                "language": "python",
                "installed_at": "2026-07-15T00:00:00+00:00",
                "artifact_sha256": hashlib.sha256(data).hexdigest(),
                "artifact_kind": "binary",
                "executable": "email-agent",
            }
        )
    )
    lock = tmp_path / "binaries.lock.json"
    lock.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "agentVersion": "0.5.0",
                "baseUrl": "https://r2.example",
                "binaries": {
                    "darwin-arm64": {
                        "filename": "email-agent-darwin-arm64",
                        "executable": "email-agent",
                        "sha256": "PENDING-1648-replace-with-real-sha256",
                        "size": 0,
                    }
                },
            }
        )
    )
    m = mgr.AgentSidecarManager(
        builtin_specs()["email"], cache_dir=cache, lock_path=lock
    )
    argv, kwargs = m.build_spawn_command(port=9126)
    assert argv[0] == str(binary)
    assert "--port" in argv and "9126" in argv


def test_dev_mode_spawn_command_is_uvicorn_app_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    m = mgr.AgentSidecarManager(_email_spec_with_src(src))
    argv, kwargs = m.build_spawn_command(port=9124)
    assert argv[0] == sys.executable
    assert "uvicorn" in argv
    # Loaded as TOP-LEVEL module `server` via --app-dir, NOT `packaging.server:app`
    # (which would resolve to the PyPI packaging library).
    assert "server:app" in argv
    assert "packaging.server:app" not in argv
    assert "--reload" in argv
    assert "--app-dir" in argv
    app_dir = argv[argv.index("--app-dir") + 1]
    assert app_dir == str(src / "packaging")


def test_dev_mode_missing_src_dir_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = mgr.AgentSidecarManager(_email_spec_with_src(tmp_path / "does-not-exist"))
    with pytest.raises(SidecarSpawnError, match="uv pip install -e"):
        m.build_spawn_command(port=9125)


def test_spawn_port_4001_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    m = mgr.AgentSidecarManager(_email_spec_with_src(src))
    with pytest.raises(ValueError, match="4001"):
        m.build_spawn_command(port=4001)


# ---------------------------------------------------------------------------
# start() / shutdown() with a faked Popen + faked HTTP
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return None  # still running

    def wait(self, timeout=None):
        self.returncode = 0
        return 0


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.text = str(payload)

    def json(self):
        return self._payload


def _install_fake_spawn(monkeypatch, tmp_path, *, spec=None, version_payload=None):
    """Wire a manager with a fake Popen + fake HTTP so start() runs without a
    real subprocess. Returns (manager, captured) where captured records calls."""
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    spec = spec or _email_spec_with_src(src)
    captured = {"popen_kwargs": None, "atexit": []}

    def _fake_popen(argv, **kwargs):
        captured["popen_kwargs"] = kwargs
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        mgr.atexit, "register", lambda fn: captured["atexit"].append(fn)
    )
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)

    m = mgr.AgentSidecarManager(spec, cache_dir=tmp_path, log_dir=tmp_path / "logs")

    vp = version_payload or {"apiVersion": "1.0", "agentVersion": "0.2.2"}

    def _fake_http_get(url, timeout):
        if url.endswith("/health"):
            return _FakeResp({"status": "ok", "service": spec.service_id})
        if url.endswith("/version"):
            return _FakeResp(vp)
        raise AssertionError(url)

    monkeypatch.setattr(m, "_http_get", _fake_http_get)
    return m, captured


def test_spawn_redirects_output_to_logfile_not_pipe(monkeypatch, tmp_path):
    # The deadlock fix: stdout must go to a real file (has fileno), stderr merged
    # — NOT subprocess.PIPE, which would deadlock once the buffer fills.
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    kwargs = captured["popen_kwargs"]
    assert kwargs["stdout"] is not mgr.subprocess.PIPE
    assert hasattr(kwargs["stdout"], "fileno")  # an open file object
    assert kwargs["stderr"] == mgr.subprocess.STDOUT
    assert (tmp_path / "logs").is_dir()


def test_start_registers_atexit_reaper(monkeypatch, tmp_path):
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    assert m.shutdown in captured["atexit"]


def test_spawn_passes_per_session_token_via_env(monkeypatch, tmp_path):
    # #1706: the sidecar's caller-auth token is handed over the private env
    # channel on spawn (never argv), so no other local process sees it in a
    # process listing. Now keyed by spec.token_env_var, not a hardcoded literal.
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    env = captured["popen_kwargs"]["env"]
    assert m.auth_token  # a real random token was generated
    assert env[builtin_specs()["email"].token_env_var] == m.auth_token
    # The inherited environment is preserved (merged, not replaced).
    assert "PATH" in env or "Path" in env
    # The token must never appear on the command line.
    assert all(m.auth_token not in str(a) for a in captured["argv"])


def test_proxy_is_bound_with_the_session_token(monkeypatch, tmp_path):
    # The UI path (manager.proxy()) must replay the token so the sidecar accepts
    # its calls end-to-end.
    m, _ = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    proxy = m.proxy()
    assert proxy._auth_token == m.auth_token
    assert proxy._session.headers.get("Authorization") == f"Bearer {m.auth_token}"


def test_start_captures_version(monkeypatch, tmp_path):
    m, captured = _install_fake_spawn(
        monkeypatch,
        tmp_path,
        version_payload={"apiVersion": "1.3", "agentVersion": "0.9"},
    )
    m.start()
    assert m.api_version == "1.3"
    assert m.agent_version == "0.9"


def test_version_major_mismatch_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    monkeypatch.setattr(mgr.subprocess, "Popen", lambda argv, **kw: _FakeProc())
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)

    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src),
        cache_dir=tmp_path,
        log_dir=tmp_path / "logs",
        expected_api_version="2.0",
    )
    # shutdown is called on failure; stub it so the fake proc isn't tree-killed.
    monkeypatch.setattr(m, "shutdown", lambda *a, **k: None)
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp(
            {"status": "ok", "service": "gaia-agent-email"}
            if url.endswith("/health")
            else {"apiVersion": "1.0"}
        ),
    )
    with pytest.raises(VersionMismatchError, match="major"):
        m.start()


def test_old_sidecar_logs_are_pruned(monkeypatch, tmp_path):
    # Per-port log files accumulate across restarts (ephemeral ports differ).
    # Opening a new log prunes the oldest, keeping only the most recent few.
    logs = tmp_path / "logs"
    logs.mkdir()
    for p in range(9000, 9000 + 10):
        (logs / f"sidecar-{p}.log").write_text("old")
    m, _ = _install_fake_spawn(monkeypatch, tmp_path)
    m.log_dir = logs
    m.start()
    remaining = sorted(logs.glob("sidecar-*.log"))
    assert len(remaining) <= mgr._MAX_SIDECAR_LOGS
    # The just-opened log for the live port must survive the prune.
    assert (logs / f"sidecar-{m.port}.log").exists()


def test_proxy_requires_started(tmp_path):
    m = mgr.AgentSidecarManager(_email_spec_with_src(tmp_path))
    with pytest.raises(SidecarError, match="not started"):
        m.proxy()


def test_proxy_bound_to_base_url(monkeypatch, tmp_path):
    from gaia.ui.email_sidecar.proxy import EmailSidecarProxy

    m, _ = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    proxy = m.proxy()
    assert isinstance(proxy, EmailSidecarProxy)
    assert proxy.base_url == m.base_url


def test_context_manager_starts_and_shuts_down(monkeypatch, tmp_path):
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    shut = {"called": 0}
    real_shutdown = m.shutdown

    def _spy(*a, **k):
        shut["called"] += 1
        return real_shutdown(*a, **k)

    monkeypatch.setattr(m, "shutdown", _spy)
    with m as started:
        assert started is m
        assert m.is_running
    assert shut["called"] >= 1


def test_health_rejects_foreign_server_on_port(monkeypatch, tmp_path):
    # A non-GAIA server returning {"status":"ok"} must NOT be accepted as our
    # sidecar — require the service identity (spec.service_id).
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    monkeypatch.setattr(mgr.subprocess, "Popen", lambda argv, **kw: _FakeProc())
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)
    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src),
        cache_dir=tmp_path,
        log_dir=tmp_path / "logs",
        health_timeout=0.3,
    )
    # Foreign server: status ok but no/wrong service identity.
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp({"status": "ok", "service": "nginx"}),
    )
    with pytest.raises(HealthTimeoutError):
        m.start()


def test_pinned_version_with_missing_apiversion_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    monkeypatch.setattr(mgr.subprocess, "Popen", lambda argv, **kw: _FakeProc())
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)
    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src),
        cache_dir=tmp_path,
        log_dir=tmp_path / "logs",
        expected_api_version="1.0",
    )
    monkeypatch.setattr(m, "shutdown", lambda *a, **k: None)
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp(
            {"status": "ok", "service": "gaia-agent-email"}
            if url.endswith("/health")
            else {"agentVersion": "0.2"}  # apiVersion intentionally absent
        ),
    )
    with pytest.raises(VersionMismatchError, match="apiVersion"):
        m.start()


def test_concurrent_start_spawns_only_one_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    spawn_count = {"n": 0}

    def _fake_popen(argv, **kwargs):
        spawn_count["n"] += 1
        return _FakeProc()

    # Widen the pre-spawn window so both threads would race past the is_running
    # check if start() were not serialized — the lock must collapse it to one.
    def _slow_free_port(host="127.0.0.1"):
        _t.sleep(0.2)
        return 50000 + spawn_count["n"]

    monkeypatch.setattr(mgr, "find_free_port", _slow_free_port)
    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src), cache_dir=tmp_path, log_dir=tmp_path / "logs"
    )
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp(
            {"status": "ok", "service": "gaia-agent-email"}
            if url.endswith("/health")
            else {"apiVersion": "1.0", "agentVersion": "0.2"}
        ),
    )

    results = []

    def _run():
        try:
            results.append(m.start())
        except Exception as e:  # noqa: BLE001
            results.append(e)

    t1, t2 = threading.Thread(target=_run), threading.Thread(target=_run)
    t1.start()
    t2.start()
    t1.join(10)
    t2.join(10)
    # Only one spawn despite two concurrent start() calls; both got the base_url.
    assert spawn_count["n"] == 1, spawn_count
    assert all(isinstance(r, str) for r in results), results


class _ExitedProc:
    """A proc that exited early (e.g. uvicorn lost a port race on bind)."""

    def __init__(self, pid=1, code=1):
        self.pid = pid
        self.returncode = code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def test_start_retries_on_early_exit_then_succeeds(monkeypatch, tmp_path):
    # Port-race mitigation: if the sidecar exits early (bind failure), start()
    # retries with a fresh port instead of surfacing a spurious failure.
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    procs = [_ExitedProc(), _FakeProc()]  # first dies, second lives
    ports = []

    def _fake_popen(argv, **kwargs):
        ports.append(argv[argv.index("--port") + 1])
        return procs.pop(0)

    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    # killpg must be a no-op for the fake procs on the early-exit shutdown.
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)

    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src), cache_dir=tmp_path, log_dir=tmp_path / "logs"
    )
    monkeypatch.setattr(
        m,
        "_http_get",
        lambda url, timeout: _FakeResp(
            {"status": "ok", "service": "gaia-agent-email"}
            if url.endswith("/health")
            else {"apiVersion": "1.0"}
        ),
    )
    base = m.start()
    assert base.startswith("http://127.0.0.1:")
    assert m.api_version == "1.0"
    assert len(ports) == 2  # retried once with a fresh port


def test_start_does_not_retry_on_health_timeout(monkeypatch, tmp_path):
    # A genuine hang (process alive but never healthy) must NOT be retried —
    # retrying a 30s timeout would multiply latency. Fail loudly, once.
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    spawns = []

    def _fake_popen(argv, **kwargs):
        spawns.append(argv)
        return _FakeProc()

    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)

    m = mgr.AgentSidecarManager(
        _email_spec_with_src(src),
        cache_dir=tmp_path,
        log_dir=tmp_path / "logs",
        health_timeout=0.3,
    )

    class _Boom:
        status_code = 503
        text = "starting"

        def json(self):
            return {"status": "starting"}

    monkeypatch.setattr(m, "_http_get", lambda url, timeout: _Boom())
    with pytest.raises(HealthTimeoutError):
        m.start()
    assert len(spawns) == 1  # no retry on timeout


# ---------------------------------------------------------------------------
# NEW in this generalization: resolved_mode is captured at spawn time, not
# recomputed live from the (spec-driven) mode property.
# ---------------------------------------------------------------------------


def test_resolved_mode_captures_spawn_time_mode_not_live_env(monkeypatch, tmp_path):
    """After a successful start(), resolved_mode reports the mode that was
    ACTUALLY used to spawn — captured once, not live-recomputed. Changing the
    env var after start() must move .mode (the live property) but leave
    .resolved_mode unchanged."""
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)  # spawns in "dev"
    m.start()
    assert m.resolved_mode == "dev"

    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")
    assert m.mode == "user"
    assert m.resolved_mode == "dev"


# ---------------------------------------------------------------------------
# Live spawn (skipped unless the email agent + uvicorn are installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="email agent + uvicorn required for live dev-mode spawn",
)
def test_dev_mode_real_spawn_health_and_treekill(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = mgr.AgentSidecarManager(builtin_specs()["email"], health_timeout=60.0)
    base = m.start()
    try:
        import requests

        assert requests.get(f"{base}/health", timeout=5).json()["status"] == "ok"
        assert m.is_running
    finally:
        m.shutdown()
    assert not m.is_running
