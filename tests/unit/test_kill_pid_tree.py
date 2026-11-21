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
