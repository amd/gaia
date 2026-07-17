# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration tests for the headless custody daemon skeleton (issue #2018).

These spawn a real daemon subprocess, issue live HTTP requests, send SIGKILL,
and invoke the actual `gaia daemon` CLI — cross-system behavior, so they live in
tests/integration/ (the pure instance.json logic tests stay in tests/unit/).

Covers the acceptance criteria:
  * two concurrent start-or-attach callers yield ONE daemon (the second attaches).
  * kill -9 the daemon then restart → the stale lock is reclaimed cleanly.
  * a request without the client token → 401 with an actionable detail.
  * `gaia daemon status` reports pid / port / uptime.

The daemon binds an ephemeral loopback port (never 4001); every test isolates
state under a tmp ``GAIA_DAEMON_HOME`` and tears the daemon down in a finally.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

fastapi = pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from gaia.daemon import client, instance  # noqa: E402
from gaia.daemon.constants import API_PREFIX, RESERVED_PORT, SERVICE_ID  # noqa: E402
from gaia.daemon.instance import DaemonInstance  # noqa: E402

_REPO_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"
)


@pytest.fixture()
def daemon_home(tmp_path, monkeypatch):
    """Isolate all daemon on-disk state under a tmp dir for one test."""
    home = tmp_path / "host"
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(home))
    return home


def _child_env(home) -> dict:
    """Env for a spawned client subprocess: isolated home + importable src."""
    env = os.environ.copy()
    env["GAIA_DAEMON_HOME"] = str(home)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _REPO_SRC + (os.pathsep + existing if existing else "")
    return env


def _stop(inst):
    """Best-effort teardown of a running daemon."""
    if inst is None:
        return
    try:
        client.request_shutdown(inst)
        client.wait_until_gone(inst, timeout=5.0)
    except Exception:
        pass
    try:
        if instance.pid_alive(inst.pid):
            instance.terminate_instance(inst)
    except Exception:
        pass


def test_probe_rejects_pid_mismatch(daemon_home):
    # A live daemon, but the recorded pid does not match what it reports → not trusted.
    real = None
    try:
        real = client.start_or_attach()
        forged = DaemonInstance(
            pid=real.pid + 1,
            port=real.port,
            token=real.token,
            started_at=real.started_at,
        )
        assert instance.probe(forged) is None
    finally:
        _stop(real)


# ---------------------------------------------------------------------------
# Lifecycle: start, attach, token auth (401), status shape
# ---------------------------------------------------------------------------


def test_start_binds_loopback_never_4001_and_authed_status(daemon_home):
    import requests

    inst = None
    try:
        inst = client.start_or_attach()
        assert inst.host == "127.0.0.1"
        assert inst.port != RESERVED_PORT
        assert instance.pid_alive(inst.pid)
        assert instance.is_live(inst)

        # 200 with the token; payload carries pid/port/uptime.
        r = requests.get(
            f"{inst.base_url}{API_PREFIX}/status",
            headers={"Authorization": f"Bearer {inst.token}"},
            timeout=5,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == SERVICE_ID
        assert body["pid"] == inst.pid
        assert body["port"] == inst.port
        assert "uptime_seconds" in body
    finally:
        _stop(inst)


def test_missing_token_returns_actionable_401(daemon_home):
    import requests

    inst = None
    try:
        inst = client.start_or_attach()
        r = requests.get(f"{inst.base_url}{API_PREFIX}/status", timeout=5)
        assert r.status_code == 401
        detail = r.json()["detail"]
        # Actionable: what failed + what to do + where to look.
        assert "token" in detail.lower()
        assert "Authorization" in detail
        assert "instance.json" in detail
    finally:
        _stop(inst)


def test_wrong_token_returns_401(daemon_home):
    import requests

    inst = None
    try:
        inst = client.start_or_attach()
        r = requests.get(
            f"{inst.base_url}{API_PREFIX}/status",
            headers={"Authorization": "Bearer definitely-wrong"},
            timeout=5,
        )
        assert r.status_code == 401
        assert "Invalid client token" in r.json()["detail"]
    finally:
        _stop(inst)


# ---------------------------------------------------------------------------
# Single-instance guarantee + stale-lock reclaim
# ---------------------------------------------------------------------------


def _spawn_start_or_attach(home) -> subprocess.Popen:
    """Run start_or_attach() in a subprocess, printing the resulting pid."""
    code = textwrap.dedent("""
        from gaia.daemon.client import start_or_attach
        inst = start_or_attach()
        print("PID=%d" % inst.pid)
        """)
    return subprocess.Popen(
        [sys.executable, "-c", code],
        env=_child_env(home),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _read_pid(proc: subprocess.Popen, timeout: float = 40.0) -> int:
    out, err = proc.communicate(timeout=timeout)
    assert proc.returncode == 0, f"start_or_attach subprocess failed: {err}\n{out}"
    for line in out.splitlines():
        if line.startswith("PID="):
            return int(line.split("=", 1)[1])
    raise AssertionError(f"no PID in subprocess output: {out!r} / {err!r}")


def test_two_concurrent_callers_yield_one_daemon(daemon_home):
    """Two concurrent start_or_attach invocations → one daemon (second attaches)."""
    inst_for_teardown = None
    a = _spawn_start_or_attach(daemon_home)
    b = _spawn_start_or_attach(daemon_home)
    try:
        pid_a = _read_pid(a)
        pid_b = _read_pid(b)
        assert pid_a == pid_b, "concurrent callers started rival daemons"
        # Exactly one live registry pointing at that pid.
        reg = instance.read_instance()
        assert reg is not None and reg.pid == pid_a and instance.is_live(reg)
        inst_for_teardown = reg
    finally:
        for p in (a, b):
            if p.poll() is None:
                p.kill()
        _stop(inst_for_teardown)


def test_kill9_then_restart_reclaims_stale_lock(daemon_home):
    """kill -9 the daemon, leave the stale registry, then restart → clean reclaim."""
    import psutil

    first = client.start_or_attach()
    first_pid = first.pid
    try:
        # Hard-kill (SIGKILL / TerminateProcess) — no chance to deregister.
        psutil.Process(first_pid).kill()
        psutil.Process(first_pid).wait(timeout=10)

        # Stale registry still on disk, pointing at the dead pid.
        stale = instance.read_instance()
        assert stale is not None and stale.pid == first_pid
        assert not instance.pid_alive(first_pid)
        assert not instance.is_live(stale)

        # Restart reclaims: a fresh, live daemon with a different pid.
        second = client.start_or_attach()
        assert second.pid != first_pid
        assert instance.is_live(second)
        assert instance.read_instance().pid == second.pid
    finally:
        cur = instance.read_instance()
        _stop(cur)


# ---------------------------------------------------------------------------
# Actual CLI (`gaia daemon ...`) — per CLAUDE.md, exercise the real command
# ---------------------------------------------------------------------------


def _run_cli(home, *cli_args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "gaia.cli", "daemon", *cli_args],
        env=_child_env(home),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_start_status_stop_roundtrip(daemon_home):
    started = False
    try:
        r = _run_cli(daemon_home, "status")
        assert r.returncode == 0
        assert "not running" in r.stdout

        r = _run_cli(daemon_home, "start")
        assert r.returncode == 0, r.stderr
        assert "running" in r.stdout
        started = True

        r = _run_cli(daemon_home, "status")
        assert r.returncode == 0
        assert "running" in r.stdout
        assert "pid:" in r.stdout
        assert "port:" in r.stdout
        assert "uptime:" in r.stdout

        r = _run_cli(daemon_home, "stop")
        assert r.returncode == 0
        assert "stopped" in r.stdout or "terminated" in r.stdout
        started = False
    finally:
        if started:
            _stop(instance.read_instance())


def test_cli_status_reports_stale_after_kill9(daemon_home):
    import psutil

    inst = client.start_or_attach()
    try:
        psutil.Process(inst.pid).kill()
        psutil.Process(inst.pid).wait(timeout=10)
        r = _run_cli(daemon_home, "status")
        assert r.returncode == 0
        assert "stale" in r.stdout
        assert "restart" in r.stdout
    finally:
        cur = instance.read_instance()
        _stop(cur)
        instance.remove_instance()
