# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A pip/uv run that is stuck must not hold the install forever, and a slow
one that is still working must not be killed.

Older uv waited with no limit for a cache lock another session's uv held;
current uv gives up after ``UV_LOCK_TIMEOUT`` with "Could not acquire lock".
Either way the install used to hang, or fall through to ``python -m uv``
(which waits on the same lock) and end with "install uv". Now the child gets
``UV_LOCK_TIMEOUT``, a lock failure gets its own error and stops there, and a
run is stopped only after ``GAIA_HUB_PIP_TIMEOUT`` seconds with no CPU or
disk activity anywhere in its process tree.
"""

from __future__ import annotations

import shutil
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


def test_a_finished_lock_wait_then_an_unrelated_failure_is_not_a_lock_error(
    monkeypatch, tmp_path
):
    """uv carries on once it gets the lock; a later resolver error is that error."""
    waited_then_failed = _script(
        tmp_path,
        "resolver.py",
        "import sys\n"
        "sys.stderr.write('Waiting to acquire lock for `cache` (lockfile: x)\\n')\n"
        "sys.stderr.write('error: No solution found when resolving dependencies\\n')\n"
        "sys.exit(1)\n",
    )
    fallback = _script(tmp_path, "fallback.py", "import sys; sys.exit(3)\n")
    calls = []
    real = installer._run_frontend

    def recording(cmd, timeout):
        calls.append(cmd)
        return real(cmd, timeout)

    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 30.0)
    monkeypatch.setattr(
        installer, "_pip_frontends", lambda: [waited_then_failed, fallback]
    )
    monkeypatch.setattr(installer, "_run_frontend", recording)

    with pytest.raises(InstallError) as excinfo:
        installer._default_run_pip(["pkg"])
    message = str(excinfo.value)
    assert "another install is using it" not in message
    assert "No solution found when resolving dependencies" in message
    assert len(calls) == 2


def test_stopped_while_still_waiting_for_the_lock_is_a_lock_error(
    monkeypatch, tmp_path
):
    """Older uv never gives up; stopped mid-wait, the lock is still the cause."""
    parked = _script(
        tmp_path,
        "parked.py",
        "import sys, time\n"
        "sys.stderr.write('Waiting to acquire lock for `cache` (lockfile: x)\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(60)\n",
    )
    monkeypatch.setattr(installer, "_pip_frontends", lambda: [parked, _IDLE])

    error = _run_in_thread(lambda: installer._default_run_pip(["pkg"]), 30)

    assert isinstance(error, InstallError), error
    assert "another install is using it" in str(error)


@pytest.mark.parametrize(
    "output, still_running, expected",
    [
        ("error: Could not acquire lock", False, True),
        ("ERROR: COULD NOT ACQUIRE LOCK", False, True),
        ("Waiting to acquire lock for `cache`", False, False),
        ("Waiting to acquire lock for `cache`", True, True),
        ("acquired lock; error: build failed", True, False),
        ("", True, False),
    ],
)
def test_only_a_given_up_or_ongoing_wait_counts_as_a_lock_failure(
    output, still_running, expected
):
    assert installer._mentions_uv_lock(output, still_running=still_running) is expected


def test_a_job_object_failure_is_never_read_as_a_missing_frontend(monkeypatch):
    """Windows error 2 as an OSError errno would become FileNotFoundError, which
    the runner treats as "not on PATH" and silently skips."""
    import ctypes

    monkeypatch.setattr(ctypes, "get_last_error", lambda: 2, raising=False)
    error = installer._job_error("AssignProcessToJobObject")
    assert isinstance(error, InstallError)
    assert not isinstance(error, OSError)
    assert "AssignProcessToJobObject" in str(error)
    assert "Windows error 2" in str(error)


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


# The line uv itself prints while parked on the lock, once RUST_LOG enables it.
_REAL_UV_WAIT_LINE = (
    "INFO Waiting to acquire shared lock for `C:/cache` at `cache\\.lock`"
)


@pytest.mark.parametrize(
    "line",
    [
        _REAL_UV_WAIT_LINE,
        "INFO Waiting to acquire exclusive lock for `/home/u/.cache/uv`",
        "Waiting to acquire lock for `cache` (lockfile: x)",
    ],
)
def test_every_form_of_the_uv_wait_line_is_recognised(line):
    assert installer._mentions_uv_lock(line, still_running=True)
    assert not installer._mentions_uv_lock(line)


def test_uv_is_asked_to_log_its_lock_wait_unless_the_user_chose(monkeypatch, tmp_path):
    """uv only prints the wait at info level; without it the status never shows."""
    show = _script(
        tmp_path, "rust_log.py", "import os; print(os.environ.get('RUST_LOG'))\n"
    )
    monkeypatch.delenv("RUST_LOG", raising=False)
    assert installer._run_frontend(show, 15.0).stdout.strip() == "uv_fs=info"
    monkeypatch.setenv("RUST_LOG", "debug")
    assert installer._run_frontend(show, 15.0).stdout.strip() == "debug"


def _hold_lock(path):
    """Hold ``path`` the way another uv would hold its cache lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 2**31 - 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def _release_lock(handle):
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 2**31 - 1)
    handle.close()


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv on PATH")
def test_real_uv_held_lock_shows_the_wait_then_names_the_lock(monkeypatch, tmp_path):
    cache = tmp_path / "uv-cache"
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.delenv("RUST_LOG", raising=False)
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 6.0)  # uv waits 3 s
    monkeypatch.setattr(
        installer, "_pip_frontends", lambda: [["uv", "pip", "install"], _IDLE]
    )
    installer._set_progress("realuv", status="running", phase="installing", percent=60)
    installer._install_context.agent_id = "realuv"
    handle = _hold_lock(cache / ".lock")
    try:
        with pytest.raises(InstallError) as excinfo:
            installer._default_run_pip(
                ["--offline", "--target", str(tmp_path / "t"), "gaia-no-such-pkg"]
            )
    finally:
        _release_lock(handle)
        installer._install_context.agent_id = None
    assert "another install is using it" in str(excinfo.value)
    status = installer.get_install_status("realuv")
    assert status["phase"] == "waiting for another install to finish"


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv on PATH")
def test_real_uv_that_got_the_lock_then_failed_reports_the_real_error(
    monkeypatch, tmp_path
):
    cache = tmp_path / "uv-cache"
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.delenv("RUST_LOG", raising=False)
    monkeypatch.setattr(installer, "PIP_TIMEOUT_SECONDS", 60.0)
    fallback = _script(tmp_path, "fallback.py", "import sys; sys.exit(3)\n")
    monkeypatch.setattr(
        installer, "_pip_frontends", lambda: [["uv", "pip", "install"], fallback]
    )
    handle = _hold_lock(cache / ".lock")
    releaser = threading.Timer(2.0, _release_lock, args=(handle,))
    releaser.start()
    try:
        with pytest.raises(InstallError) as excinfo:
            installer._default_run_pip(
                ["--offline", "--target", str(tmp_path / "t"), "gaia-no-such-pkg"]
            )
    finally:
        releaser.join()
    message = str(excinfo.value)
    assert "another install is using it" not in message
    assert "gaia-no-such-pkg" in message
