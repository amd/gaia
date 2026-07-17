# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration contract for generic daemon sidecar supervision (issue #2142, T4).

Drives a real ``python -m gaia.daemon`` process and a toy sidecar (see
``tests/fixtures/toy_sidecar.py``) registered through the test-only
``GAIA_DAEMON_EXTRA_SPECS`` seam — the daemon reads this env var once at
startup and merges the specs it points at over ``builtin_specs()``. Nothing
here mocks the manager/registry/ledger: it exercises the real HTTP control
plane, the real ``gaia.ui.email_sidecar.daemon_client`` module, and the real
``gaia daemon ...`` CLI subcommands.

Covers the T4 acceptance criteria:
  * ensure over real HTTP spawns the toy sidecar as a child of the daemon.
  * a client that acquires a handle and exits leaves the sidecar running.
  * the stop route tree-kills the sidecar leader AND its child.
  * a graceful daemon shutdown reaps every supervised sidecar.
  * `kill -9` the daemon, then restart -> the crash-reap ledger kills the
    survivor (leader + child) before serving; a fresh ensure then works.
  * the real `daemon_client.acquire_handle`/`stop_sidecar` round-trip against
    the toy spec (the one automated place this module runs unmocked).
  * `gaia daemon agents` / `start-agent` / `stop-agent` / `status` work as
    real subcommands and never print the sidecar bearer token.

Requires POSIX: the toy binary is executed directly via its shebang, and
process-group tree-kill assertions rely on ``os.killpg``/session semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="direct shebang exec of the toy sidecar + process-group tree-kill "
    "assertions require POSIX",
)

fastapi = pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

import psutil  # noqa: E402
import requests  # noqa: E402

from gaia.daemon import client, instance  # noqa: E402
from gaia.daemon.constants import API_PREFIX  # noqa: E402

_REPO_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"
)
_TOY_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "toy_sidecar.py"

_TOY_SPEC = {
    "service_id": "gaia-agent-toy",
    "display_name": "Toy",
    "expected_api_major": "1",
    "token_env_var": "GAIA_TOY_SIDECAR_TOKEN",
    "mode_env_var": "GAIA_TOY_AGENT_MODE",
    "cache_dir_name": "toy",
}


@dataclass
class ToyEnv:
    daemon_home: Path
    fake_home: Path
    extra_specs_path: Path
    toy_binary: Path


@pytest.fixture()
def toy_env(tmp_path, monkeypatch) -> ToyEnv:
    """Plant a verified toy sidecar hub install + register its spec via the
    daemon's test-only EXTRA_SPECS seam, all under isolated tmp state."""
    daemon_home = tmp_path / "host"
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    agent_dir = fake_home / ".gaia" / "agents" / "toy"
    agent_dir.mkdir(parents=True)
    toy_binary = agent_dir / "toy-agent"
    shutil.copy(_TOY_FIXTURE, toy_binary)
    toy_binary.chmod(0o755)
    sha256 = hashlib.sha256(toy_binary.read_bytes()).hexdigest()
    sentinel = {
        "id": "toy",
        "version": "0.0.1",
        "language": "python",
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "artifact_sha256": sha256,
        "artifact_kind": "binary",
        "executable": "toy-agent",
    }
    (agent_dir / ".installed").write_text(json.dumps(sentinel), encoding="utf-8")

    extra_specs_path = fake_home / "toy-specs.json"
    extra_specs_path.write_text(json.dumps({"toy": _TOY_SPEC}), encoding="utf-8")

    monkeypatch.setenv("GAIA_DAEMON_HOME", str(daemon_home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("GAIA_DAEMON_EXTRA_SPECS", str(extra_specs_path))

    return ToyEnv(
        daemon_home=daemon_home,
        fake_home=fake_home,
        extra_specs_path=extra_specs_path,
        toy_binary=toy_binary,
    )


def _child_env(env: ToyEnv) -> dict:
    """Env for a spawned subprocess: isolated home/daemon state + importable src."""
    child = os.environ.copy()
    child["GAIA_DAEMON_HOME"] = str(env.daemon_home)
    child["HOME"] = str(env.fake_home)
    child["GAIA_DAEMON_EXTRA_SPECS"] = str(env.extra_specs_path)
    existing = child.get("PYTHONPATH", "")
    child["PYTHONPATH"] = _REPO_SRC + (os.pathsep + existing if existing else "")
    # Keep the CLI subprocess's matplotlib font cache outside the fake $HOME
    # (a fresh dir per test) so it isn't rebuilt from scratch on every call.
    child.setdefault(
        "MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "gaia-test-mplconfig")
    )
    return child


def _stop(inst) -> None:
    """Best-effort teardown of a running daemon (and, via shutdown_all, its
    supervised sidecars)."""
    if inst is None:
        return
    try:
        client.request_shutdown(inst)
        client.wait_until_gone(inst, timeout=10.0)
    except Exception:
        pass
    try:
        if instance.pid_alive(inst.pid):
            instance.terminate_instance(inst)
    except Exception:
        pass


def _kill_if_alive(pid) -> None:
    if not pid:
        return
    try:
        if psutil.pid_exists(pid):
            psutil.Process(pid).kill()
    except Exception:
        pass


def _wait_for(predicate, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _ensure_toy(inst, mode: str = "user", timeout: float = 30.0) -> dict:
    r = requests.post(
        f"{inst.base_url}{API_PREFIX}/agents/toy/ensure",
        headers={"Authorization": f"Bearer {inst.token}"},
        json={"mode": mode},
        timeout=timeout,
    )
    assert r.status_code == 200, f"ensure failed: {r.status_code} {r.text}"
    return r.json()


def _toy_health(base_url: str) -> dict:
    r = requests.get(f"{base_url}/health", timeout=5)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Real HTTP control plane against a real toy sidecar
# ---------------------------------------------------------------------------


def test_ensure_spawns_toy_under_daemon(toy_env):
    """Ensure over real HTTP spawns the toy sidecar as a direct child of the
    daemon process (not the test process, not some other ancestor)."""
    inst = None
    try:
        inst = client.start_or_attach()
        body = _ensure_toy(inst)

        assert body["agent_id"] == "toy"
        assert body["state"] == "running"
        assert body["mode"] == "user"
        assert isinstance(body["pid"], int)
        assert isinstance(body["port"], int)
        assert body["base_url"]
        assert body["token"]

        health = _toy_health(body["base_url"])
        assert health["service"] == "gaia-agent-toy"

        assert psutil.Process(body["pid"]).ppid() == inst.pid
    finally:
        _stop(inst)


def test_stop_route_kills_leader_and_child(toy_env):
    """The stop route tree-kills the toy leader AND the child it spawned."""
    inst = None
    try:
        inst = client.start_or_attach()
        body = _ensure_toy(inst)
        leader_pid = body["pid"]
        child_pid = _toy_health(body["base_url"])["childPid"]
        assert psutil.pid_exists(child_pid)

        r = requests.post(
            f"{inst.base_url}{API_PREFIX}/agents/toy/stop",
            headers={"Authorization": f"Bearer {inst.token}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "stopped"

        assert _wait_for(lambda: not psutil.pid_exists(leader_pid))
        assert _wait_for(lambda: not psutil.pid_exists(child_pid))
    finally:
        _stop(inst)


def test_daemon_stop_reaps_sidecar(toy_env):
    """A graceful `gaia daemon stop`-equivalent shutdown reaps every
    supervised sidecar, leader and child alike."""
    inst = None
    leader_pid = None
    child_pid = None
    try:
        inst = client.start_or_attach()
        body = _ensure_toy(inst)
        leader_pid = body["pid"]
        child_pid = _toy_health(body["base_url"])["childPid"]

        client.request_shutdown(inst)
        assert client.wait_until_gone(inst, timeout=10.0)
        inst = None

        assert _wait_for(lambda: not psutil.pid_exists(leader_pid))
        assert _wait_for(lambda: not psutil.pid_exists(child_pid))
    finally:
        _stop(inst)
        _kill_if_alive(leader_pid)
        _kill_if_alive(child_pid)


def test_kill9_daemon_then_restart_reaps_ledger_survivors(toy_env):
    """kill -9 the daemon while a sidecar runs -> the sidecar survives the
    daemon's death, but the next daemon start reaps it (leader + child) via
    the crash ledger before serving; a fresh ensure then yields a NEW pid."""
    first = None
    second = None
    old_leader_pid = None
    old_child_pid = None
    try:
        first = client.start_or_attach()
        body = _ensure_toy(first)
        old_leader_pid = body["pid"]
        old_child_pid = _toy_health(body["base_url"])["childPid"]

        psutil.Process(first.pid).kill()
        psutil.Process(first.pid).wait(timeout=10)

        # The toy sidecar has its own session/process group -> it survives a
        # hard kill of the daemon.
        assert psutil.pid_exists(old_leader_pid)
        assert psutil.pid_exists(old_child_pid)

        second = client.start_or_attach()
        assert second.pid != first.pid

        assert _wait_for(lambda: not psutil.pid_exists(old_leader_pid))
        assert _wait_for(lambda: not psutil.pid_exists(old_child_pid))

        fresh = _ensure_toy(second)
        assert fresh["pid"] != old_leader_pid
    finally:
        _stop(second if second is not None else None)
        _kill_if_alive(old_leader_pid)
        _kill_if_alive(old_child_pid)


def test_client_exit_leaves_sidecar_running(toy_env):
    """A client process that acquires the sidecar handle then exits leaves
    the sidecar running (the daemon owns its lifecycle, not the caller)."""
    inst = None
    try:
        code = textwrap.dedent("""
            from gaia.ui.email_sidecar.daemon_client import acquire_handle
            h = acquire_handle("toy")
            print("PID=%d" % h.pid)
            print("BASE_URL=%s" % h.base_url)
            """)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            env=_child_env(toy_env),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
        toy_pid = None
        base_url = None
        for line in proc.stdout.splitlines():
            if line.startswith("PID="):
                toy_pid = int(line.split("=", 1)[1])
            elif line.startswith("BASE_URL="):
                base_url = line.split("=", 1)[1]
        assert toy_pid is not None and base_url is not None, proc.stdout

        # The client process has already exited (subprocess.run returned).
        assert psutil.pid_exists(toy_pid)
        assert _toy_health(base_url)["status"] == "ok"

        inst = client.attach()
        assert inst is not None
    finally:
        _stop(inst)


def test_real_daemon_client_roundtrip(toy_env):
    """The one automated place `gaia.ui.email_sidecar.daemon_client` runs for
    real (unmocked): acquire_handle + stop_sidecar against the toy spec."""
    from gaia.ui.email_sidecar import daemon_client

    inst = None
    try:
        handle = daemon_client.acquire_handle("toy")
        assert handle.base_url
        assert handle.token
        assert handle.api_version == "1.0"
        assert handle.mode == "user"
        assert isinstance(handle.pid, int)

        health = _toy_health(handle.base_url)
        assert health["service"] == "gaia-agent-toy"

        inst = client.attach()
        assert inst is not None

        daemon_client.stop_sidecar("toy")
        assert _wait_for(lambda: not psutil.pid_exists(handle.pid))
    finally:
        _stop(inst)


# ---------------------------------------------------------------------------
# The actual CLI (`gaia daemon ...`) — per repo convention, exercise the real
# commands rather than the underlying modules.
# ---------------------------------------------------------------------------


def _run_cli(env: ToyEnv, *cli_args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "gaia.cli", "daemon", *cli_args],
        env=_child_env(env),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_cli_agents_reports_not_running_without_a_daemon(toy_env):
    r = _run_cli(toy_env, "agents")
    assert r.returncode != 0
    assert "not running" in r.stdout


def test_cli_stop_agent_reports_not_running_without_a_daemon(toy_env):
    r = _run_cli(toy_env, "stop-agent", "toy")
    assert r.returncode != 0
    assert "not running" in r.stdout


def test_cli_start_agent_auto_starts_the_daemon(toy_env):
    inst = None
    try:
        assert client.attach() is None

        r = _run_cli(toy_env, "start-agent", "toy", "--mode", "user")
        assert r.returncode == 0, r.stderr
        assert "toy" in r.stdout
        assert "user" in r.stdout

        inst = client.attach()
        assert inst is not None
    finally:
        _stop(inst)


def test_cli_lifecycle_roundtrip_never_prints_the_token(toy_env):
    """start-agent -> agents -> status -> stop-agent, as real commands; the
    sidecar bearer token must never appear in any of their output."""
    inst = None
    try:
        r_start = _run_cli(toy_env, "start-agent", "toy", "--mode", "user")
        assert r_start.returncode == 0, r_start.stderr
        assert "toy" in r_start.stdout
        assert "pid" in r_start.stdout.lower()
        assert "port" in r_start.stdout.lower()

        inst = client.attach()
        assert inst is not None

        # Obtain the REAL token straight from the HTTP control plane so the
        # fence below asserts against the actual secret, not a guess.
        token = _ensure_toy(inst)["token"]
        assert token not in r_start.stdout
        assert token not in r_start.stderr

        r_agents = _run_cli(toy_env, "agents")
        assert r_agents.returncode == 0, r_agents.stderr
        assert "toy" in r_agents.stdout
        assert "running" in r_agents.stdout.lower()
        assert token not in r_agents.stdout
        assert token not in r_agents.stderr

        r_status = _run_cli(toy_env, "status")
        assert r_status.returncode == 0, r_status.stderr
        assert "toy" in r_status.stdout
        assert token not in r_status.stdout
        assert token not in r_status.stderr

        r_stop = _run_cli(toy_env, "stop-agent", "toy")
        assert r_stop.returncode == 0, r_stop.stderr
        assert "stopped" in r_stop.stdout.lower()
        assert token not in r_stop.stdout
        assert token not in r_stop.stderr

        assert client.attach() is not None  # daemon itself stays up
    finally:
        _stop(inst)


def test_cli_stop_agent_unknown_id_surfaces_registered_ids(toy_env):
    inst = None
    try:
        inst = client.start_or_attach()
        r = _run_cli(toy_env, "stop-agent", "does-not-exist")
        assert r.returncode != 0
        combined = r.stdout + r.stderr
        assert "toy" in combined  # 404 detail lists registered ids
    finally:
        _stop(inst)
