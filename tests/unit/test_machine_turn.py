# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/machine_turn.py: queue on the machine's eval lock instead of failing."""

import _thread
import importlib.util
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gaia.eval import eval_lock

_PATH = Path(__file__).resolve().parents[2] / "util" / "machine_turn.py"


@pytest.fixture
def turn(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("machine_turn", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lock = tmp_path / "gaia-eval.lock"
    holder = tmp_path / "gaia-eval.holder.json"
    for target in (module, eval_lock):
        monkeypatch.setattr(target, "LOCK_FILE", lock)
        monkeypatch.setattr(target, "HOLDER_FILE", holder)
    return module


def test_runs_the_command_and_returns_its_exit_code(turn):
    code = turn.main(["--", sys.executable, "-c", "import sys; sys.exit(7)"])
    assert code == 7
    assert not turn.HOLDER_FILE.exists()


def test_the_command_can_run_gaia_eval_inside_the_held_lock(turn):
    """The child is told the lock is already held, or `gaia eval` would refuse."""
    code = turn.main(
        [
            "--",
            sys.executable,
            "-c",
            "import os, sys; sys.exit(0 if os.environ.get('GAIA_EVAL_NO_LOCK') == '1' else 1)",
        ]
    )
    assert code == 0


def test_waits_for_a_running_eval_then_takes_its_turn(turn, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(eval_lock, "stay_awake", lambda required=True: _null())

    def other_eval():
        with eval_lock.exclusive_eval("other session's eval"):
            started.set()
            release.wait(10)

    holder = threading.Thread(target=other_eval)
    holder.start()
    assert started.wait(5)
    threading.Timer(0.6, release.set).start()
    t0 = time.monotonic()
    code = turn.main(["--poll", "0.1", "--", sys.executable, "-c", "pass"])
    waited = time.monotonic() - t0
    holder.join(5)
    assert code == 0
    assert waited >= 0.5


def test_gives_up_after_the_timeout_naming_the_holder(turn, capsys):
    import os

    from gaia.daemon.lock import try_lock, unlock

    fd = os.open(str(turn.LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    assert try_lock(fd)
    turn.HOLDER_FILE.write_text(
        '{"pid": 4242, "command": "gaia eval agent", "started": 0}',
        encoding="utf-8",
    )
    try:
        code = turn.main(
            ["--timeout", "0.2", "--poll", "0.05", "--", sys.executable, "-c", "pass"]
        )
    finally:
        unlock(fd)
        os.close(fd)
    assert code == turn.TIMED_OUT == 124
    assert "PID 4242" in capsys.readouterr().err


def test_refuses_an_empty_command(turn):
    with pytest.raises(SystemExit):
        turn.main([])


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_runs_as_a_script():
    result = subprocess.run(
        [sys.executable, str(_PATH), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Wait for this machine's model slot" in result.stdout


def test_a_commands_own_exit_code_2_is_not_read_as_a_timeout(turn):
    code = turn.main(["--", sys.executable, "-c", "import sys; sys.exit(2)"])
    assert code == 2 != turn.TIMED_OUT


_SLEEPER = (
    "import os, sys, time; "
    "open(sys.argv[1], 'w').write(str(os.getpid())); "
    "time.sleep(60)"
)


def _wait_for_pid(pid_file, timeout_s=10.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text():
            return int(pid_file.read_text())
        time.sleep(0.05)
    raise AssertionError("the queued command never started")


def _alive(pid):
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _lock_is_free(turn):
    from gaia.daemon.lock import try_lock, unlock

    fd = os.open(str(turn.LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if try_lock(fd):
            unlock(fd)
            return True
        return False
    finally:
        os.close(fd)


def _interrupt_once_started(pid_file, interrupt):
    def run():
        _wait_for_pid(pid_file)
        interrupt()

    threading.Thread(target=run, daemon=True).start()


def test_ctrl_c_stops_the_command_before_releasing_the_lock(turn, tmp_path):
    pid_file = tmp_path / "child.pid"
    _interrupt_once_started(pid_file, _thread.interrupt_main)
    with pytest.raises(KeyboardInterrupt):
        turn.main(["--", sys.executable, "-c", _SLEEPER, str(pid_file)])
    assert not _alive(int(pid_file.read_text()))
    assert _lock_is_free(turn)
    assert not turn.HOLDER_FILE.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM is a hard kill on Windows")
def test_sigterm_stops_the_command_before_releasing_the_lock(turn, tmp_path):
    pid_file = tmp_path / "child.pid"
    _interrupt_once_started(pid_file, lambda: os.kill(os.getpid(), signal.SIGTERM))
    with pytest.raises(SystemExit) as stopped:
        turn.main(["--", sys.executable, "-c", _SLEEPER, str(pid_file)])
    assert stopped.value.code == 128 + signal.SIGTERM
    assert not _alive(int(pid_file.read_text()))
    assert _lock_is_free(turn)
    assert signal.getsignal(signal.SIGTERM) is not turn._raise_on_sigterm


@pytest.mark.skipif(
    sys.platform == "win32", reason="terminate() is already a kill on Windows"
)
def test_a_command_that_ignores_sigterm_is_killed(turn, tmp_path, monkeypatch):
    monkeypatch.setattr(turn, "_STOP_GRACE_S", 0.5)
    pid_file = tmp_path / "child.pid"
    stubborn = (
        "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); " + _SLEEPER
    )
    _interrupt_once_started(pid_file, _thread.interrupt_main)
    with pytest.raises(KeyboardInterrupt):
        turn.main(["--", sys.executable, "-c", stubborn, str(pid_file)])
    assert not _alive(int(pid_file.read_text()))
    assert _lock_is_free(turn)
