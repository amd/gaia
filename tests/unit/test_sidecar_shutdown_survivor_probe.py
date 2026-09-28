# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shutdown must not trust a recycled pgid, and must not claim success on
Windows while the dead bootloader's server child still serves the port (#4209).

Platform-independent by design: the POSIX group probe and the Windows port
probe are both driven through fakes, so both halves run on every OS. The
fork-based companion suite (``test_sidecar_orphan_group_kill.py``) is skipped
on Windows and so can never cover the Windows path.
"""

import pytest
import requests

from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.spec import AgentSidecarSpec

_SPEC = AgentSidecarSpec(
    agent_id="toy",
    service_id="gaia-agent-toy",
    display_name="Toy Agent",
    expected_api_major="1",
    token_env_var="GAIA_TOY_SIDECAR_TOKEN",
    mode_env_var="GAIA_TOY_AGENT_MODE",
    cache_dir_name="toy",
)


class _DeadLeader:
    """A Popen whose child has already been reaped — its pid is now free."""

    def __init__(self, pid: int = 424242):
        self.pid = pid
        self.returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    m = mgr.AgentSidecarManager(_SPEC, log_dir=tmp_path / "logs")
    m._proc = _DeadLeader()
    m.port = 51234
    m.base_url = f"http://127.0.0.1:{m.port}"
    return m


def _reaped_flag(m):
    seen = []
    m.on_process_reaped = lambda: seen.append(True)
    return seen


# --- the pgid the OS handed to somebody else -------------------------------


def test_recycled_pgid_does_not_resurrect_a_dead_sidecar(manager, monkeypatch):
    monkeypatch.setattr(mgr.os, "name", "posix")
    manager._group_alive = lambda pgid: False
    assert manager.is_running is False, "empty group means the sidecar is gone"

    # The OS reissues that number to an unrelated process group.
    manager._group_alive = lambda pgid: True
    assert manager.is_running is False, "a group that was empty can never come back"


def test_shutdown_never_signals_a_recycled_pgid(manager, monkeypatch):
    monkeypatch.setattr(mgr.os, "name", "posix")
    signalled = []
    # raising=False: os.killpg does not exist on Windows, where this test still runs.
    monkeypatch.setattr(
        mgr.os, "killpg", lambda pgid, sig: signalled.append((pgid, sig)), raising=False
    )
    manager._group_alive = lambda pgid: False
    assert manager.is_running is False  # latches the group as gone

    manager._group_alive = lambda pgid: True  # pid reused by a stranger
    seen = _reaped_flag(manager)
    manager.shutdown(timeout=0.2)

    assert signalled == [], "must not aim SIGTERM/SIGKILL at a stranger's group"
    assert seen == [True], "a confirmed-gone sidecar still reports a clean stop"
    assert manager._proc is None


# --- Windows: no group to probe, so the port is the only evidence ----------


def test_windows_survivor_on_the_port_blocks_a_false_stopped(manager, monkeypatch):
    monkeypatch.setattr(mgr.os, "name", "nt")
    manager._http_get = lambda url, timeout: _Resp(
        200, {"status": "ok", "service": _SPEC.service_id}
    )
    seen = _reaped_flag(manager)

    assert manager.is_running is True, "the orphan still serving our port is running"
    manager.shutdown(timeout=0.2)

    assert seen == [], "a surviving child must keep its ledger entry for crash-reap"
    assert manager._proc is not None
    assert manager.is_running is True


def test_windows_reports_a_clean_stop_when_the_port_is_dead(manager, monkeypatch):
    monkeypatch.setattr(mgr.os, "name", "nt")

    def _refused(url, timeout):
        raise requests.exceptions.ConnectionError("refused")

    manager._http_get = _refused
    seen = _reaped_flag(manager)

    assert manager.is_running is False
    manager.shutdown(timeout=0.2)

    assert seen == [True]
    assert manager._proc is None


def test_windows_ignores_a_foreign_service_on_a_reused_port(manager, monkeypatch):
    """An unrelated service that grabbed our ephemeral port is not our orphan."""
    monkeypatch.setattr(mgr.os, "name", "nt")
    manager._http_get = lambda url, timeout: _Resp(
        200, {"status": "ok", "service": "somebody-elses-server"}
    )
    seen = _reaped_flag(manager)

    assert manager.is_running is False
    manager.shutdown(timeout=0.2)

    assert seen == [True]
    assert manager._proc is None
