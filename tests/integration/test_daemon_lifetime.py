# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A real daemon exits once nothing can reach it, and a test run cannot leak one
(#5293).

Test runs used to leave dozens of ``python -m gaia.daemon`` processes behind,
polling deleted temp homes forever and owning the developer's real Lemonade.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
psutil = pytest.importorskip("psutil")

from gaia.daemon import client, instance, lifetime, paths  # noqa: E402
from gaia.daemon.errors import DaemonError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBES = REPO_ROOT / "tests" / "fixtures" / "session_guards"
#: One check interval, plus a graceful shutdown.
EXIT_WITHIN = lifetime.CHECK_SECONDS + 20


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    """A real daemon under a tmp home; stopped at teardown if still running."""
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "host"))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    started = []

    def start():
        inst = client.start_or_attach()
        started.append(inst)
        return inst

    yield start
    for inst in started:
        with contextlib.suppress(DaemonError):
            if instance.pid_alive(inst.pid) and client.request_shutdown(inst):
                client.wait_until_gone(inst, timeout=10)
        if instance.pid_alive(inst.pid):
            instance.terminate_instance(inst)


def _exits(inst) -> bool:
    return client.wait_until_gone(inst, timeout=EXIT_WITHIN)


def _log() -> str:
    return paths.log_path().read_text(encoding="utf-8", errors="replace")


def test_daemon_exits_when_its_instance_json_is_deleted(daemon):
    inst = daemon()
    instance.remove_instance()
    assert _exits(inst), "the daemon kept running without its instance.json"
    assert "shutting down because its state file" in _log()


def test_daemon_exits_when_its_home_is_deleted(daemon):
    inst = daemon()
    # What the test session's atexit cleanup does; on Windows the open log
    # keeps the folder itself alive.
    shutil.rmtree(paths.host_dir(), ignore_errors=True)
    assert _exits(inst), "the daemon kept running in a deleted home"


def test_daemon_exits_with_the_process_it_is_tied_to(daemon, monkeypatch):
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        monkeypatch.setenv(lifetime.EXIT_WITH_PID_ENV, str(parent.pid))
        inst = daemon()
        time.sleep(lifetime.CHECK_SECONDS + 1)
        assert instance.is_live(inst), "exited while its process was alive"
        parent.kill()
        parent.wait(timeout=30)
        assert _exits(inst), "the daemon outlived the process it is tied to"
    finally:
        if parent.poll() is None:
            parent.kill()


def test_daemon_refuses_a_malformed_exit_with_pid(daemon, monkeypatch):
    monkeypatch.setenv(lifetime.EXIT_WITH_PID_ENV, "not-a-pid")
    with pytest.raises(DaemonError, match="exited early"):
        daemon()
    assert lifetime.EXIT_WITH_PID_ENV in _log()


def _child_pytest(probe: str, *extra: str) -> subprocess.CompletedProcess:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTEST_", "_GAIA_TEST_STATE")) and k != "PYTEST_ADDOPTS"
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(PROBES / probe),
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            "-s",
            "-rfE",
            *extra,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_a_test_session_fails_and_stops_a_daemon_left_running():
    result = _child_pytest("probe_daemon_leak.py")
    out = result.stdout + result.stderr
    match = re.search(r"LEAKED_PID=(\d+)", out)
    assert match, out
    pid = int(match.group(1))
    try:
        assert result.returncode != 0, out
        assert "still running at its end" in out, out
        assert "test_leaves_a_daemon_running" in out, out
        assert not instance.pid_alive(pid), "the leaked daemon was not stopped"
    finally:
        if instance.pid_alive(pid):
            psutil.Process(pid).kill()


def test_tests_cannot_act_on_the_real_gaia_home():
    result = _child_pytest("probe_real_home.py", "-v")
    out = result.stdout + result.stderr
    assert "2 failed, 2 passed" in out, out
    for name in (
        "test_embedded_stop_on_the_real_home_is_refused",
        "test_daemon_spawn_without_gaia_home_is_refused",
    ):
        assert re.search(rf"{name} FAILED", out), out
    assert out.count("your real home") >= 2, out
    assert "the guarded" not in out, out
