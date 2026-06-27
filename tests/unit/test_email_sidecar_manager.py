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
            return _FakeResp({"status": "ok"})
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
            {"status": "ok"} if url.endswith("/health") else {"apiVersion": "1.0"}
        ),
    )
    with pytest.raises(VersionMismatchError, match="major"):
        m.start()


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
