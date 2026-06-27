# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailSidecarManager: mode select, ephemeral port, spawn-arg shape, health,
tree-kill."""

import importlib.util
import sys
from pathlib import Path

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
