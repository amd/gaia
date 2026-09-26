# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A sidecar whose leader (PyInstaller bootloader) already exited must still
have its surviving server child killed, and the ledger entry kept until then
(#4209)."""

import os
import subprocess
import sys
import textwrap
import time

import pytest

from gaia.daemon.sidecars import manager as mgr
from gaia.daemon.sidecars.spec import AgentSidecarSpec

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX process-group semantics (fork/killpg)"
)

_SPEC = AgentSidecarSpec(
    agent_id="toy",
    service_id="gaia-agent-toy",
    display_name="Toy Agent",
    expected_api_major="1",
    token_env_var="GAIA_TOY_SIDECAR_TOKEN",
    mode_env_var="GAIA_TOY_AGENT_MODE",
    cache_dir_name="toy",
)

# Parent forks a long-sleeping child, records its pid, then exits — the shape
# of a PyInstaller one-file bootloader that died while its server lives on.
_FIXTURE = textwrap.dedent("""
    import os, signal, sys, time
    pid_file, ignore_term = sys.argv[1], sys.argv[2] == "1"
    child = os.fork()
    if child == 0:
        if ignore_term:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with open(pid_file + ".tmp", "w") as f:
            f.write(str(os.getpid()))
        os.replace(pid_file + ".tmp", pid_file)
        time.sleep(120)
        os._exit(0)
    sys.exit(0)
    """)


def _alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _spawn_orphaning_leader(tmp_path, *, ignore_term: bool):
    script = tmp_path / "bootloader.py"
    script.write_text(_FIXTURE)
    pid_file = tmp_path / "child.pid"
    proc = subprocess.Popen(
        [sys.executable, str(script), str(pid_file), "1" if ignore_term else "0"],
        start_new_session=True,
    )
    proc.wait(timeout=10)
    deadline = time.monotonic() + 10
    while not pid_file.exists():
        assert time.monotonic() < deadline, "fixture child never started"
        time.sleep(0.02)
    return proc, int(pid_file.read_text())


@pytest.fixture
def orphan(tmp_path, monkeypatch, request):
    monkeypatch.setattr(mgr.atexit, "unregister", lambda fn: None)
    ignore_term = getattr(request, "param", False)
    proc, child = _spawn_orphaning_leader(tmp_path, ignore_term=ignore_term)
    m = mgr.AgentSidecarManager(_SPEC, log_dir=tmp_path / "logs")
    m._proc = proc
    yield m, child
    if _alive(child):
        os.kill(child, 9)


def _record_reap(m, child):
    seen = []
    m.on_process_reaped = lambda: seen.append(_alive(child))
    return seen


def test_is_running_sees_the_surviving_child(orphan):
    m, child = orphan
    assert m._proc.poll() is not None
    assert _alive(child)
    assert m.is_running


def test_shutdown_kills_the_child_the_dead_leader_left(orphan):
    m, child = orphan
    seen = _record_reap(m, child)
    m.shutdown(timeout=5.0)
    assert not _alive(child)
    assert seen == [False], "ledger entry must be removed only once the child is gone"
    assert not m.is_running


@pytest.mark.parametrize("orphan", [True], indirect=True)
def test_shutdown_escalates_to_sigkill_for_a_term_ignoring_child(orphan):
    m, child = orphan
    seen = _record_reap(m, child)
    m.shutdown(timeout=1.0)
    assert not _alive(child)
    assert seen == [False]


def test_ledger_entry_kept_when_the_group_survives(orphan, monkeypatch):
    m, child = orphan
    seen = _record_reap(m, child)
    monkeypatch.setattr(mgr.os, "killpg", lambda pgid, sig: None)
    m.shutdown(timeout=0.5)
    assert _alive(child)
    assert seen == [], "a surviving child must keep its ledger entry for crash-reap"


def _registry_holding(m):
    from gaia.daemon.sidecars.registry import SidecarRegistry

    removed = []
    reg = SidecarRegistry({"toy": _SPEC}, on_stop=removed.append)
    reg._manager_factory = lambda *a, **kw: m
    reg._holder("toy", _SPEC)
    return reg, removed


def test_registry_stop_reaps_the_orphaned_child(orphan):
    m, child = orphan
    reg, removed = _registry_holding(m)
    assert reg.stop("toy") == {"agent_id": "toy", "state": "stopped"}
    assert not _alive(child)
    assert removed == ["toy"]


def test_registry_stop_is_loud_when_the_group_survives(orphan, monkeypatch):
    from gaia.daemon.sidecars.errors import StopFailedError

    m, child = orphan
    reg, removed = _registry_holding(m)
    monkeypatch.setattr(mgr.os, "killpg", lambda pgid, sig: None)
    with pytest.raises(StopFailedError, match="process group"):
        reg.stop("toy")
    assert _alive(child)
    assert removed == []
