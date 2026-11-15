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
