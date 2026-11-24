# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A timed-out pipeline leaves no process of any stage running."""

import os
import signal
import subprocess
import sys
import time

import pytest

from gaia.agents.tools import command_timeouts, shell_tools

posix_only = pytest.mark.skipif(
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


@posix_only
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


def test_a_stage_that_already_exited_is_not_tree_killed(tmp_path, monkeypatch):
    killed = []
    real = shell_tools.terminate_process_tree

    def spy(proc, **kwargs):
        killed.append(proc.pid)
        return real(proc, **kwargs)

    monkeypatch.setattr(shell_tools, "terminate_process_tree", spy)
    first = [sys.executable, "-c", "pass"]
    last = [sys.executable, "-c", "import time; time.sleep(300)"]

    with pytest.raises(subprocess.TimeoutExpired):
        shell_tools._run_pipeline(
            [first, last], ("", ""), ({}, {}), str(tmp_path), timeout=2
        )

    assert len(killed) == 1, "a reaped stage's pid may belong to another process"


def test_killing_without_reading_never_touches_the_pipe(monkeypatch):
    proc = subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, "-c", "import time; time.sleep(300)"],
        stdout=subprocess.PIPE,
        # Its own group, as shell_tools starts it: the kill takes the whole group.
        start_new_session=os.name != "nt",
    )
    proc.stdout.close()

    def no_read(*_a, **_k):
        raise AssertionError("communicate() reads the closed pipe")

    monkeypatch.setattr(proc, "communicate", no_read)

    assert command_timeouts.terminate_process_tree(proc, read_output=False) == (
        "",
        "",
    )
    assert proc.poll() is not None


def test_an_upstream_stage_is_killed_without_reading_its_stdout(tmp_path, monkeypatch):
    calls = []
    real = shell_tools.terminate_process_tree

    def spy(proc, **kwargs):
        calls.append(kwargs)
        return real(proc, **kwargs)

    monkeypatch.setattr(shell_tools, "terminate_process_tree", spy)
    first = [sys.executable, "-c", "import time; time.sleep(300)"]
    last = [sys.executable, "-c", "import time; time.sleep(300)"]

    with pytest.raises(subprocess.TimeoutExpired):
        shell_tools._run_pipeline(
            [first, last], ("", ""), ({}, {}), str(tmp_path), timeout=2
        )

    # Its stdout was closed when handed on; reading it on Windows prints a
    # reader-thread traceback per stage.
    assert calls == [{"read_output": False}, {}]
