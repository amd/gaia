# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A sidecar shuts down when the daemon that started it is killed.

The daemon reaped stale sidecars only when the next daemon started, so a
killed daemon with none after it left its agent running indefinitely.
"""

import os
import subprocess
import sys
import threading

import pytest

from gaia.daemon.constants import DAEMON_PID_ENV_VAR
from gaia.sidecar import parent_watch


@pytest.fixture(autouse=True)
def _fresh_watcher():
    # One watcher per process: each test starts from none and stops its own,
    # so a leftover thread can never fire into a later test.
    parent_watch.stop()
    yield
    parent_watch.stop()


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_no_daemon_pid_means_no_watch():
    assert parent_watch.start(env={}) is None


def test_a_malformed_pid_is_an_error_not_a_silent_skip():
    with pytest.raises(ValueError, match=DAEMON_PID_ENV_VAR):
        parent_watch.start(env={DAEMON_PID_ENV_VAR: "abc"})


def test_shuts_down_once_the_daemon_dies():
    daemon = _sleeper()
    gone = threading.Event()
    try:
        thread = parent_watch.start(
            env={DAEMON_PID_ENV_VAR: str(daemon.pid)}, poll_s=0.1, on_gone=gone.set
        )
        assert not gone.wait(0.5)
        daemon.kill()
        daemon.wait()
        assert gone.wait(5)
        thread.join(5)
        assert not thread.is_alive()
    finally:
        daemon.kill()


def test_a_daemon_already_gone_at_start_refuses_to_start_in_one_line():
    """A SIGINT before the server is listening surfaced as a bare traceback."""
    daemon = _sleeper()
    daemon.kill()
    daemon.wait()
    gone = threading.Event()
    with pytest.raises(ValueError, match="already gone"):
        parent_watch.start(
            env={DAEMON_PID_ENV_VAR: str(daemon.pid)}, poll_s=0.1, on_gone=gone.set
        )
    assert not gone.is_set()


def test_the_daemon_hands_its_pid_to_the_sidecars_it_spawns(tmp_path, monkeypatch):
    from gaia.daemon.sidecars import manager as mgr
    from gaia.daemon.sidecars.errors import SidecarSpawnError
    from gaia.daemon.sidecars.spec import builtin_specs

    calls = []

    def fake_popen(*_args, **kwargs):
        calls.append(kwargs)
        raise OSError("spawn captured")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # Dev mode delivers the secret by file, so no installed binary is needed.
    monkeypatch.setenv("GAIA_GAIA_AGENT_MODE", "dev")
    m = mgr.AgentSidecarManager(
        builtin_specs()["gaia"], cache_dir=tmp_path, log_dir=tmp_path / "logs"
    )
    with pytest.raises(SidecarSpawnError):
        m._spawn_process(["sidecar"], {}, 9127)
    assert calls[0]["env"][DAEMON_PID_ENV_VAR] == str(os.getpid())


def test_a_second_start_returns_the_running_watcher():
    daemon = _sleeper()
    try:
        env = {DAEMON_PID_ENV_VAR: str(daemon.pid)}
        first = parent_watch.start(env=env, poll_s=0.1, on_gone=lambda: None)
        assert parent_watch.start(env=env, poll_s=0.1, on_gone=lambda: None) is first
    finally:
        daemon.kill()
        daemon.wait()


def test_a_daemon_it_may_not_inspect_is_an_error_not_gone(monkeypatch):
    import psutil

    def denied(_pid):
        raise psutil.AccessDenied(4242)

    monkeypatch.setattr(parent_watch.psutil, "Process", denied)
    gone = threading.Event()
    # A ValueError, so the agents' entry points print one line instead of a traceback.
    with pytest.raises(ValueError, match="cannot watch the GAIA daemon") as raised:
        parent_watch.start(env={DAEMON_PID_ENV_VAR: "4242"}, on_gone=gone.set)
    assert isinstance(raised.value.__cause__, psutil.AccessDenied)
    assert not gone.is_set()


def test_a_killed_but_unreaped_daemon_counts_as_gone(monkeypatch):
    """On POSIX a zombie keeps its start time until its parent reaps it."""
    import psutil

    killed = threading.Event()

    class _Daemon:
        def __init__(self, _pid):
            pass

        def status(self):
            return psutil.STATUS_ZOMBIE if killed.is_set() else psutil.STATUS_RUNNING

        def create_time(self):
            return 1234.0

    monkeypatch.setattr(parent_watch.psutil, "Process", _Daemon)
    gone = threading.Event()
    parent_watch.start(env={DAEMON_PID_ENV_VAR: "4242"}, poll_s=0.1, on_gone=gone.set)
    assert not gone.wait(0.3)
    killed.set()
    assert gone.wait(5)


def test_reading_the_environment_removes_the_pid_from_it(monkeypatch):
    """What the agent launches (MCP servers, shell commands) must not inherit it."""
    daemon = _sleeper()
    try:
        monkeypatch.setenv(DAEMON_PID_ENV_VAR, str(daemon.pid))
        monkeypatch.setattr(parent_watch, "_shut_down", lambda: None)
        first = parent_watch.start(poll_s=0.1, on_gone=lambda: None)
        assert first is not None
        assert DAEMON_PID_ENV_VAR not in os.environ
        # The dev-mode import and main() both call start(): the second still gets it.
        assert parent_watch.start(poll_s=0.1, on_gone=lambda: None) is first
    finally:
        daemon.kill()
        daemon.wait()


def test_stop_ends_the_watcher_without_firing():
    daemon = _sleeper()
    gone = threading.Event()
    try:
        thread = parent_watch.start(
            env={DAEMON_PID_ENV_VAR: str(daemon.pid)}, poll_s=0.1, on_gone=gone.set
        )
        parent_watch.stop()
        assert not thread.is_alive()
        daemon.kill()
        daemon.wait()
        assert not gone.wait(0.5)
    finally:
        daemon.kill()


@pytest.mark.parametrize("agent, package", [("gaia", "gaia_agent")])
def test_the_dev_mode_module_starts_the_watch_when_imported(
    monkeypatch, agent, package
):
    """``uvicorn server:app`` never reaches main(); importing the module must."""
    import importlib.util
    from pathlib import Path

    pytest.importorskip(package)
    path = (
        Path(__file__).resolve().parents[2]
        / "hub"
        / "agents"
        / agent
        / "python"
        / "packaging"
        / "server.py"
    )
    daemon = _sleeper()
    try:
        monkeypatch.setenv(DAEMON_PID_ENV_VAR, str(daemon.pid))
        spec = importlib.util.spec_from_file_location(f"_dev_server_{agent}", path)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        assert parent_watch._watcher is not None and parent_watch._watcher.is_alive()
        assert DAEMON_PID_ENV_VAR not in os.environ
    finally:
        parent_watch.stop()  # before the daemon dies, or the watch would SIGINT pytest
        daemon.kill()
        daemon.wait()


def test_the_dev_mode_module_refuses_a_bad_pid_in_one_line(monkeypatch):
    import importlib.util
    from pathlib import Path

    pytest.importorskip("gaia_agent")
    path = (
        Path(__file__).resolve().parents[2]
        / "hub"
        / "agents"
        / "gaia"
        / "python"
        / "packaging"
        / "server.py"
    )
    monkeypatch.setenv(DAEMON_PID_ENV_VAR, "not-a-pid")
    spec = importlib.util.spec_from_file_location("_dev_server_bad_pid", path)
    with pytest.raises(SystemExit, match="gaia-agent: GAIA_DAEMON_PID='not-a-pid'"):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
