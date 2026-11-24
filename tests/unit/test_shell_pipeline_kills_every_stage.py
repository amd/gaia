# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A timed-out pipeline leaves no process of any stage running."""

import os
import signal
import subprocess
import sys
import time

import pytest

from gaia.agents.tools import shell_tools

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="Checks a child's pid with os.kill(pid, 0), POSIX only"
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie still answers signal 0
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
    except OSError:
        return True
    return bool(state) and not state.startswith("Z")


def test_a_timeout_kills_the_children_of_an_earlier_stage(tmp_path):
    pidfile = tmp_path / "child.pid"
    first = [
        sys.executable,
        "-c",
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(300)\n",
    ]
    last = [sys.executable, "-c", "import sys; sys.stdin.read()"]

    child_pid = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            shell_tools._run_pipeline(
                [first, last], ("", ""), ({}, {}), str(tmp_path), timeout=2
            )
        child_pid = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while _alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(child_pid), "the first stage's child outlived the timeout"
    finally:
        if pidfile.exists():
            leftover = int(pidfile.read_text())
            if _alive(leftover):
                os.kill(leftover, signal.SIGKILL)
