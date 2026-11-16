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
def _fresh_watcher(monkeypatch):
    # One watcher per process: each test starts from none.
    monkeypatch.setattr(parent_watch, "_watcher", None)


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


def test_a_daemon_already_gone_at_start_shuts_down_at_once():
    daemon = _sleeper()
    daemon.kill()
    daemon.wait()
    gone = threading.Event()
    parent_watch.start(
        env={DAEMON_PID_ENV_VAR: str(daemon.pid)}, poll_s=0.1, on_gone=gone.set
    )
    assert gone.wait(5)


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
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = mgr.AgentSidecarManager(
        builtin_specs()["email"], cache_dir=tmp_path, log_dir=tmp_path / "logs"
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

    class _Zombie:
        def __init__(self, _pid):
            pass

        def status(self):
            return psutil.STATUS_ZOMBIE

        def create_time(self):
            return 1234.0

    monkeypatch.setattr(parent_watch.psutil, "Process", _Zombie)
    gone = threading.Event()
    parent_watch.start(env={DAEMON_PID_ENV_VAR: "4242"}, poll_s=0.1, on_gone=gone.set)
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
