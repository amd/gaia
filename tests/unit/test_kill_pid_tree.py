# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""kill_pid_tree ends a process it did not start, with everything under it."""

import subprocess
import sys
import time

import psutil

from gaia.utils.process_tree import kill_pid_tree, popen_kwargs

_PARENT = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print(child.pid, flush=True)
time.sleep(120)
"""


def _gone(pid: int, within: float = 10.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


def test_the_process_and_its_child_are_both_killed():
    parent = subprocess.Popen(
        [sys.executable, "-c", _PARENT],
        stdout=subprocess.PIPE,
        text=True,
        **popen_kwargs(),
    )
    try:
        child_pid = int(parent.stdout.readline())
        assert psutil.pid_exists(child_pid)

        kill_pid_tree(parent.pid)

        assert _gone(parent.pid), "the process itself survived"
        assert _gone(child_pid), "its child survived"
    finally:
        parent.kill()
        parent.wait()
        parent.stdout.close()


def test_a_pid_that_is_already_gone_is_not_an_error():
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    kill_pid_tree(done.pid)


def test_a_vetted_process_object_is_killed_with_its_child():
    parent = subprocess.Popen(
        [sys.executable, "-c", _PARENT],
        stdout=subprocess.PIPE,
        text=True,
        **popen_kwargs(),
    )
    try:
        child_pid = int(parent.stdout.readline())
        kill_pid_tree(psutil.Process(parent.pid))
        assert _gone(parent.pid), "the process itself survived"
        assert _gone(child_pid), "its child survived"
    finally:
        parent.kill()
        parent.wait()
        parent.stdout.close()


def test_a_vetted_process_whose_pid_was_reused_is_not_killed(monkeypatch):
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        vetted = psutil.Process(live.pid)
        # What psutil reports once the checked process exits and its pid is reused.
        monkeypatch.setattr(vetted, "is_running", lambda: False)
        kill_pid_tree(vetted)
        time.sleep(0.5)
        assert live.poll() is None, "a process that was never vetted was killed"
    finally:
        live.kill()
        live.wait()


def test_a_vetted_process_that_already_exited_is_not_an_error():
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    vetted = psutil.Process(done.pid)
    done.wait()
    kill_pid_tree(vetted)


def test_the_discord_stop_kills_the_process_it_checked(monkeypatch):
    from gaia.messaging.discord import cli as discord_cli
    from gaia.utils import process_tree

    class _Proc:
        def __init__(self, pid):
            self.pid = pid

        def cmdline(self):
            return ["python", "-m", "gaia", "discord", "start"]

    made, killed = [], []

    def _process(pid):
        made.append(_Proc(pid))
        return made[-1]

    monkeypatch.setattr(psutil, "Process", _process)
    monkeypatch.setattr(process_tree, "kill_pid_tree", killed.append)
    discord_cli._kill_tree(4242)
    assert len(made) == 1, "the pid was looked up again after the check"
    assert killed == [made[0]]


def test_the_discord_stop_never_kills_another_program(monkeypatch):
    from gaia.messaging.discord import cli as discord_cli
    from gaia.utils import process_tree

    class _Proc:
        pid = 4242

        def cmdline(self):
            return ["notepad.exe", "start"]

    killed = []
    monkeypatch.setattr(psutil, "Process", lambda pid: _Proc())
    monkeypatch.setattr(process_tree, "kill_pid_tree", killed.append)
    discord_cli._kill_tree(4242)
    assert killed == []
