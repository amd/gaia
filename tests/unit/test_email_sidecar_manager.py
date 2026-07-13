# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailSidecarManager: mode select, ephemeral port, spawn-arg shape, health,
tree-kill."""

import importlib.util
import sys

import pytest

from gaia.ui.email_sidecar import manager as mgr
from gaia.ui.email_sidecar.errors import SidecarSpawnError


def test_find_free_port_never_4001():
    for _ in range(20):
        assert mgr.find_free_port() != 4001


def test_mode_defaults_to_user(monkeypatch):
    monkeypatch.delenv("GAIA_EMAIL_AGENT_MODE", raising=False)
    assert mgr.EmailSidecarManager().mode == "user"


def test_mode_reads_env(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    assert mgr.EmailSidecarManager().mode == "dev"


def test_invalid_mode_raises(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "bananas")
    with pytest.raises(SidecarSpawnError, match="GAIA_EMAIL_AGENT_MODE"):
        mgr.EmailSidecarManager().mode


def test_user_mode_spawn_command_uses_fetched_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")
    fake_binary = tmp_path / "email-agent"
    fake_binary.write_bytes(b"x")

    class _Res:
        binary_path = fake_binary

    monkeypatch.setattr(mgr.fetch, "fetch_binary", lambda **kw: _Res())
    m = mgr.EmailSidecarManager()
    argv, kwargs = m.build_spawn_command(port=9123)
    assert argv[0] == str(fake_binary)
    assert "--port" in argv and "9123" in argv
    assert "4001" not in argv


def test_user_mode_fetch_failure_raises_with_remedy(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "user")

    def _boom(**kw):
        raise RuntimeError("placeholder sha256")

    monkeypatch.setattr(mgr.fetch, "fetch_binary", _boom)
    m = mgr.EmailSidecarManager()
    with pytest.raises(SidecarSpawnError, match="GAIA_EMAIL_AGENT_MODE=dev"):
        m.build_spawn_command(port=9123)


def test_dev_mode_spawn_command_is_uvicorn_app_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    m = mgr.EmailSidecarManager(email_src_dir=src)
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
    m = mgr.EmailSidecarManager(email_src_dir=tmp_path / "does-not-exist")
    with pytest.raises(SidecarSpawnError, match="uv pip install -e"):
        m.build_spawn_command(port=9125)


def test_spawn_port_4001_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    m = mgr.EmailSidecarManager(email_src_dir=src)
    with pytest.raises(ValueError, match="4001"):
        m.build_spawn_command(port=4001)


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


def _install_fake_spawn(monkeypatch, tmp_path, *, version_payload=None):
    """Wire a manager with a fake Popen + fake HTTP so start() runs without a
    real subprocess. Returns (manager, captured) where captured records calls."""
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
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

    m = mgr.EmailSidecarManager(
        email_src_dir=src, cache_dir=tmp_path, log_dir=tmp_path / "logs"
    )

    vp = version_payload or {"apiVersion": "1.0", "agentVersion": "0.2.2"}

    def _fake_http_get(url, timeout):
        if url.endswith("/health"):
            return _FakeResp({"status": "ok", "service": "gaia-agent-email"})
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
    # process listing.
    m, captured = _install_fake_spawn(monkeypatch, tmp_path)
    m.start()
    env = captured["popen_kwargs"]["env"]
    assert m.auth_token  # a real random token was generated
    assert env["GAIA_EMAIL_SIDECAR_TOKEN"] == m.auth_token
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
    from gaia.ui.email_sidecar.errors import VersionMismatchError

    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    monkeypatch.setattr(mgr.subprocess, "Popen", lambda argv, **kw: _FakeProc())
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)

    m = mgr.EmailSidecarManager(
        email_src_dir=src,
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
    from gaia.ui.email_sidecar.errors import SidecarError

    m = mgr.EmailSidecarManager(email_src_dir=tmp_path)
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
    # sidecar — require the service identity.
    from gaia.ui.email_sidecar.errors import HealthTimeoutError

    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    monkeypatch.setattr(mgr.subprocess, "Popen", lambda argv, **kw: _FakeProc())
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)
    m = mgr.EmailSidecarManager(
        email_src_dir=src,
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
    from gaia.ui.email_sidecar.errors import VersionMismatchError

    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    src = tmp_path / "email"
    (src / "packaging").mkdir(parents=True)
    monkeypatch.setattr(mgr.subprocess, "Popen", lambda argv, **kw: _FakeProc())
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)
    m = mgr.EmailSidecarManager(
        email_src_dir=src,
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
    import threading
    import time as _t

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
    m = mgr.EmailSidecarManager(
        email_src_dir=src, cache_dir=tmp_path, log_dir=tmp_path / "logs"
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
        # capture the --port chosen this attempt
        ports.append(argv[argv.index("--port") + 1])
        return procs.pop(0)

    monkeypatch.setattr(mgr.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(mgr.atexit, "register", lambda fn: None)
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    # killpg must be a no-op for the fake procs on the early-exit shutdown.
    monkeypatch.setattr(mgr.os, "killpg", lambda *a: None)
    monkeypatch.setattr(mgr.os, "getpgid", lambda pid: pid)

    m = mgr.EmailSidecarManager(
        email_src_dir=src, cache_dir=tmp_path, log_dir=tmp_path / "logs"
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
    from gaia.ui.email_sidecar.errors import HealthTimeoutError

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

    m = mgr.EmailSidecarManager(
        email_src_dir=src,
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


@pytest.mark.skipif(
    importlib.util.find_spec("gaia_agent_email") is None
    or importlib.util.find_spec("uvicorn") is None,
    reason="email agent + uvicorn required for live dev-mode spawn",
)
def test_dev_mode_real_spawn_health_and_treekill(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = mgr.EmailSidecarManager(health_timeout=60.0)
    base = m.start()
    try:
        import requests

        assert requests.get(f"{base}/health", timeout=5).json()["status"] == "ok"
        assert m.is_running
    finally:
        m.shutdown()
    assert not m.is_running
