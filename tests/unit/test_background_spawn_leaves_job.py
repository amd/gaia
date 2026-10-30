# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA's daemon and Lemonade Server leave the job object of whoever started them.

The TUI runs each session's agent in a Windows job created with
KILL_ON_JOB_CLOSE, and a process started by a process in a job joins that job.
So when a session's agent was the first GAIA process to need the daemon, the
daemon and its Lemonade Server joined that session's job, and closing the
session killed both under every other session (#5261).
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.utils import terminal
from gaia.utils.terminal import CREATE_BREAKAWAY_FROM_JOB, popen_outliving_job

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def popen_calls(monkeypatch):
    """Record each Popen's creationflags; answer the ones in *refuse* with
    ERROR_ACCESS_DENIED, as Windows does for a job that forbids leaving."""
    calls = []
    refuse = set()

    def fake(argv, **kwargs):
        flags = kwargs.get("creationflags", 0)
        calls.append(flags)
        if flags in refuse:
            error = OSError("Access is denied")
            error.winerror = 5
            raise error
        return SimpleNamespace(pid=4242, argv=argv)

    monkeypatch.setattr(subprocess, "Popen", fake)
    return SimpleNamespace(calls=calls, refuse=refuse)


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(terminal, "sys", SimpleNamespace(platform="win32"))


def test_on_windows_the_server_asks_to_leave_the_job(on_windows, popen_calls):
    popen_outliving_job(["lemond"], creationflags=0x200)
    assert popen_calls.calls == [0x200 | CREATE_BREAKAWAY_FROM_JOB]


def test_posix_is_left_as_it_was(monkeypatch, popen_calls):
    monkeypatch.setattr(terminal, "sys", SimpleNamespace(platform="linux"))
    popen_outliving_job(["lemond"], start_new_session=True)
    assert popen_calls.calls == [0]


def test_a_refused_breakaway_starts_it_in_the_job_and_says_so(
    on_windows, popen_calls, caplog
):
    popen_calls.refuse.add(0x200 | CREATE_BREAKAWAY_FROM_JOB)
    with caplog.at_level("WARNING", logger=terminal.log.name):
        proc = popen_outliving_job(["lemond"], creationflags=0x200)
    assert proc.pid == 4242
    assert popen_calls.calls == [0x200 | CREATE_BREAKAWAY_FROM_JOB, 0x200]
    (warning,) = [r.getMessage() for r in caplog.records]
    assert "lemond" in warning and "stops when the program that started GAIA" in warning


def test_any_other_spawn_failure_is_raised(on_windows, monkeypatch):
    def fake(argv, **kwargs):
        raise FileNotFoundError(2, "not found")

    monkeypatch.setattr(subprocess, "Popen", fake)
    with pytest.raises(FileNotFoundError):
        popen_outliving_job(["missing"], creationflags=0)


# -- real job objects ----------------------------------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")

#: Runs inside the job: starts a "server" the way the daemon and lemond are
#: started, reports it, then waits to be killed with the job.
_LAUNCHER = """
import json, sys, time
from gaia.utils.terminal import background_popen_kwargs, popen_outliving_job
server = popen_outliving_job([sys.executable, "-c", "import time; time.sleep(60)"],
                             **background_popen_kwargs())
print(json.dumps({"server": server.pid}), flush=True)
time.sleep(60)
"""


def _job_class():
    spec = importlib.util.spec_from_file_location(
        "lemonade_restart_harness", REPO / "util" / "lemonade_restart_harness.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module.Job


#: The same, starting the "server" with the raw flag rather than GAIA's helper.
_RAW_LAUNCHER = """
import json, subprocess, sys, time
server = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                          creationflags=0x01000000)
print(json.dumps({"server": server.pid}), flush=True)
time.sleep(60)
"""


def _server_outlives_job(breakaway_ok: bool, launcher_code: str = _LAUNCHER) -> bool:
    import psutil

    job = _job_class()(breakaway_ok=breakaway_ok)
    launcher = job.spawn(
        [sys.executable, "-c", launcher_code],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )
    server = json.loads(launcher.stdout.readline())["server"]
    job.close()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and launcher.poll() is None:
        time.sleep(0.1)
    time.sleep(0.5)
    alive = psutil.pid_exists(server)
    if alive:
        psutil.Process(server).kill()
    return alive


@windows_only
def test_a_server_started_in_the_tuis_kind_of_job_outlives_it():
    """The TUI's job allows breakaway (procgroup_windows.go), so a server GAIA
    starts from inside it lives on when the session closes."""
    if not _server_outlives_job(breakaway_ok=True, launcher_code=_RAW_LAUNCHER):
        pytest.skip("this runner's own job object forbids breakaway")
    assert _server_outlives_job(breakaway_ok=True)


@windows_only
def test_a_job_that_forbids_leaving_still_takes_it_down():
    """GAIA cannot leave such a job, and does not claim to."""
    assert not _server_outlives_job(breakaway_ok=False)
