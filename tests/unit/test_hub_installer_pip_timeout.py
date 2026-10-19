# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A pip/uv run that is stuck must not hold the install forever, and a slow
one that is still working must not be killed.

Older uv waited with no limit for a cache lock another session's uv held;
current uv gives up after ``UV_LOCK_TIMEOUT`` with "Could not acquire lock".
Either way the install used to hang, or fall through to ``python -m uv``
(which waits on the same lock) and end with "install uv". Now the child gets
``UV_LOCK_TIMEOUT``, a lock failure gets its own error and stops there, and a
run is stopped only after ``GAIA_HUB_PIP_TIMEOUT`` seconds with no CPU, disk
or network activity anywhere in its process tree.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import psutil
import pytest

from gaia.hub import installer
from gaia.hub.installer import InstallError

_IDLE = [sys.executable, "-c", "import time; time.sleep(60)"]

# A parent that starts a long-lived grandchild sharing its stdout/stderr, the
# shape of ``python -m uv``. Killing only the parent leaves the pipes open.
_IDLE_WITH_GRANDCHILD = [
    sys.executable,
    "-c",
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
    "time.sleep(60)",
]

# Current uv once UV_LOCK_TIMEOUT runs out (exit 2, as uv 0.12 does).
_UV_LOCKED = [
    sys.executable,
    "-c",
    "import os, sys; "
    "assert os.environ.get('UV_LOCK_TIMEOUT'), 'UV_LOCK_TIMEOUT not set'; "
    "sys.stderr.write('error: Could not acquire lock\\n  Caused by: Timeout ("
    "300s) when waiting for lock on `cache/.lock` (UV_LOCK_TIMEOUT)\\n'); "
    "sys.exit(2)",
]


@pytest.fixture(autouse=True)
def _short_window(monkeypatch):
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 1.0)
    monkeypatch.delenv(installer.PIP_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv("UV_LOCK_TIMEOUT", raising=False)


def _run_in_thread(fn, limit):
    """Run ``fn`` and return its exception, failing if it is still running."""
    box = {}

    def target():
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 - surfaced to the test
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(limit)
    assert not t.is_alive(), f"pip runner still waiting after {limit}s"
    return box.get("error")


@pytest.mark.parametrize(
    "frontend", [_IDLE, _IDLE_WITH_GRANDCHILD], ids=["child", "grandchild"]
)
def test_an_idle_frontend_is_stopped_as_stuck(monkeypatch, frontend):
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [frontend])

    started = time.monotonic()
    error = _run_in_thread(lambda: installer._default_run_pip(["pkg"]), 30)

    assert isinstance(error, InstallError), error
    assert time.monotonic() - started < 30
    message = str(error)
    assert "stuck" in message
    assert installer.PIP_TIMEOUT_ENV in message


def test_a_slow_install_that_keeps_working_is_not_stopped(monkeypatch, tmp_path):
    """Three times the window, busy the whole time: a slow download, not a hang."""
    target = tmp_path / "download.bin"
    busy = [
        sys.executable,
        "-c",
        "import time, sys\n"
        f"end = time.monotonic() + 3\n"
        f"with open({str(target)!r}, 'wb') as f:\n"
        "    while time.monotonic() < end:\n"
        "        f.write(b'x' * 65536); f.flush()\n"
        "        time.sleep(0.05)\n",
    ]
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [busy])
    installer._default_run_pip(["pkg"])
    assert target.stat().st_size > 0


def test_a_uv_cache_lock_failure_names_the_lock_and_stops_there(monkeypatch):
    calls = []
    real = installer._run_frontend

    def recording(cmd, timeout):
        calls.append(cmd)
        return real(cmd, timeout)

    monkeypatch.setattr(installer, "_pip_frontends", lambda: [_UV_LOCKED, _IDLE])
    monkeypatch.setattr(installer, "_run_frontend", recording)

    with pytest.raises(InstallError) as excinfo:
        installer._default_run_pip(["pkg"])
    message = str(excinfo.value)
    assert "another install is using it" in message
    assert "Install uv" not in message
    assert len(calls) == 1


def test_an_orphaned_grandchild_is_killed_too(monkeypatch, tmp_path):
    """Its parent exits first, so it is no longer anyone's descendant."""
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 3.0)
    pid_file = tmp_path / "pid"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]],\n"
        "                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "time.sleep(1.5)\n"  # sampled while still the grandchild's parent
    )
    frontend = tmp_path / "frontend.py"
    frontend.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable] + sys.argv[1:])\n"
        "time.sleep(60)\n"
    )
    command = [
        sys.executable,
        str(frontend),
        str(child),
        str(grandchild),
        str(pid_file),
    ]
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [command])

    error = _run_in_thread(lambda: installer._default_run_pip([]), 40)
    assert isinstance(error, InstallError), error
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid)


def test_the_runner_kills_the_tree_on_any_error_not_just_a_stall(monkeypatch):
    started = []
    real_popen = installer.subprocess.Popen

    def popen(cmd, **kwargs):
        proc = real_popen(_IDLE, **kwargs)
        started.append(proc)
        return proc

    def broken(*_a, **_k):
        raise OverflowError("timeout too large")

    monkeypatch.setattr(installer.subprocess, "Popen", popen)
    monkeypatch.setattr(installer, "_tree_activity", broken)
    with pytest.raises(OverflowError):
        installer._run_frontend(["uv"], 1.0)
    assert started[0].wait(10) is not None


def test_the_limit_comes_from_the_environment_when_set(monkeypatch):
    seen = []

    def fake(cmd, timeout):
        seen.append(timeout)
        return installer.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(installer, "_run_frontend", fake)
    installer._default_run_pip(["pkg"])
    monkeypatch.setenv(installer.PIP_TIMEOUT_ENV, "1800")
    installer._default_run_pip(["pkg"])
    assert seen == [1.0, 1800.0]


@pytest.mark.parametrize("raw", ["soon", "0", "-5", "inf", "nan", "1e400"])
def test_a_bad_limit_in_the_environment_fails_loudly(monkeypatch, raw):
    monkeypatch.setenv(installer.PIP_TIMEOUT_ENV, raw)
    with pytest.raises(InstallError, match=installer.PIP_TIMEOUT_ENV):
        installer._default_run_pip(["pkg"])


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return [sys.executable, str(path)]


def test_uv_always_gives_up_on_the_lock_before_gaia_would(monkeypatch, tmp_path):
    """A user's longer UV_LOCK_TIMEOUT would let uv outwait GAIA's window and
    turn a clear lock error into a generic "stuck"."""
    show = _script(
        tmp_path, "show.py", "import os; print(os.environ['UV_LOCK_TIMEOUT'])\n"
    )
    monkeypatch.setenv("UV_LOCK_TIMEOUT", "600")
    assert installer._run_frontend(show, 15.0).stdout.strip() == "7"
    monkeypatch.delenv("UV_LOCK_TIMEOUT")
    assert installer._run_frontend(show, 900.0).stdout.strip() == "300"
    monkeypatch.setenv("UV_LOCK_TIMEOUT", "30")
    assert installer._run_frontend(show, 900.0).stdout.strip() == "30"


def test_waiting_for_the_cache_lock_shows_in_the_install_status(monkeypatch, tmp_path):
    waiting = _script(
        tmp_path,
        "waiting.py",
        "import sys, time\n"
        "sys.stderr.write('Waiting to acquire lock for `cache` (lockfile: x)\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(1.5)\n",
    )
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [waiting])
    installer._set_progress(
        "lockwait", status="running", phase="installing", percent=60
    )
    installer._install_context.agent_id = "lockwait"
    try:
        installer._default_run_pip(["pkg"])
    finally:
        installer._install_context.agent_id = None
    status = installer.get_install_status("lockwait")
    assert status["phase"] == "waiting for another install to finish"
    assert status["percent"] == 60


def test_a_grandchild_orphaned_before_the_first_sample_is_killed(monkeypatch, tmp_path):
    """Its parent exits at once, so no sample ever sees it as a descendant;
    only the job (Windows) or the session (POSIX) still reaches it."""
    pid_file = tmp_path / "pid"
    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]],\n"
        "                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    )
    frontend = _script(
        tmp_path,
        "frontend.py",
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable] + sys.argv[1:]).wait()\n"
        "time.sleep(60)\n",
    )
    command = frontend + [str(child), str(grandchild), str(pid_file)]
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [command])

    error = _run_in_thread(lambda: installer._default_run_pip([]), 40)
    assert isinstance(error, InstallError), error
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid)


def test_a_process_that_spins_forever_hits_the_ceiling(monkeypatch, tmp_path):
    spin = _script(tmp_path, "spin.py", "while True:\n    pass\n")
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setenv(installer.PIP_MAX_ENV, "2")
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [spin])
    error = _run_in_thread(lambda: installer._default_run_pip([]), 30)
    assert isinstance(error, InstallError), error
    assert "still running after 2s" in str(error)
    assert installer.PIP_MAX_ENV in str(error)


def test_a_reused_pid_is_not_counted_as_activity():
    class Reused:
        pid = 4242

        def is_running(self):
            return False  # psutil compares creation times

        def cpu_times(self):
            raise AssertionError("sampled a process that is not ours")

    class Root:
        pid = 1

        def children(self, recursive=False):
            return []

        def is_running(self):
            return True

        def cpu_times(self):
            return types.SimpleNamespace(user=1.0, system=0.0)

        def io_counters(self):
            raise AttributeError

    seen = {4242: Reused()}
    assert installer._tree_activity(Root(), seen) == 1.0
    assert 4242 not in seen


def test_a_success_whose_leftover_holds_the_output_is_still_a_success(
    monkeypatch, tmp_path
):
    pid_file = tmp_path / "pid"
    leftover = tmp_path / "leftover.py"
    leftover.write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    frontend = _script(
        tmp_path,
        "frontend.py",
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable] + sys.argv[1:])\n"  # inherits stdout
        "time.sleep(0.5)\n",
    )
    command = frontend + [str(leftover), str(pid_file)]
    monkeypatch.setattr(installer, "_PIP_PIPE_WAIT_SECONDS", 1.0)
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [command])
    installer._default_run_pip([])  # must not raise "stuck"
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid)


def test_the_last_output_in_an_error_starts_at_a_line(monkeypatch, tmp_path):
    noisy = _script(
        tmp_path,
        "noisy.py",
        "import sys\n"
        "for i in range(40):\n"
        "    sys.stderr.write(f'resolving dependency number {i:03d} of many\\n')\n"
        "sys.stderr.write('error: Could not acquire lock\\n')\n"
        "sys.exit(2)\n",
    )
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [noisy])
    with pytest.raises(InstallError) as excinfo:
        installer._default_run_pip(["pkg"])
    last = str(excinfo.value).split("Last output: ", 1)[1]
    assert last.startswith("resolving dependency number")
    assert last.endswith("error: Could not acquire lock")
