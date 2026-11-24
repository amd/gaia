# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia mcp stop` only signals the process it started."""

import subprocess
import sys

import pytest

from gaia.cli import handle_mcp_stop

_SLEEP = "import time; time.sleep(60)"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def spawn():
    procs = []

    def _spawn(*extra):
        p = subprocess.Popen([sys.executable, "-c", _SLEEP, *extra])
        procs.append(p)
        return p

    yield _spawn
    for p in procs:
        p.kill()
        p.wait()


@pytest.mark.parametrize("bad", ["-1", "0"])
def test_non_positive_pid_is_refused_and_signals_nothing(workdir, mocker, bad):
    (workdir / "gaia.mcp.pid").write_text(bad)
    kill = mocker.patch("os.kill")

    with pytest.raises(SystemExit) as exc:
        handle_mcp_stop(None)

    assert exc.value.code == 1
    kill.assert_not_called()
    assert not (workdir / "gaia.mcp.pid").exists()


def test_unrelated_process_is_left_alone_and_file_removed(workdir, spawn):
    other = spawn()
    (workdir / "gaia.mcp.pid").write_text(str(other.pid))

    handle_mcp_stop(None)

    assert other.poll() is None
    assert not (workdir / "gaia.mcp.pid").exists()


def test_the_mcp_bridge_is_stopped(workdir, spawn):
    bridge = spawn("gaia.cli", "mcp", "start", "--port", "8765")
    (workdir / "gaia.mcp.pid").write_text(str(bridge.pid))

    handle_mcp_stop(None)

    assert bridge.wait(timeout=10) != 0
    assert not (workdir / "gaia.mcp.pid").exists()


def test_unreadable_command_line_keeps_the_file_and_signals_nothing(
    workdir, spawn, mocker
):
    import psutil

    other = spawn()
    (workdir / "gaia.mcp.pid").write_text(str(other.pid))
    mocker.patch.object(
        psutil.Process, "cmdline", side_effect=psutil.AccessDenied(other.pid)
    )
    kill = mocker.patch("os.kill")
    run = mocker.patch("subprocess.run")

    with pytest.raises(SystemExit) as exc:
        handle_mcp_stop(None)

    assert exc.value.code == 1
    kill.assert_not_called()
    run.assert_not_called()
    assert other.poll() is None
    assert (workdir / "gaia.mcp.pid").exists()
