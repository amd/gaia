# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The daemon spawned through a Windows venv launcher must still be recognised.

A venv's ``python.exe`` on Windows runs the base interpreter as a child, so the
daemon registers the child's pid. Matching only the launcher's pid made every
first start from a venv wait out the full timeout and report "did not become
healthy" while the daemon was up and serving.
"""

from __future__ import annotations

import os
import subprocess
import sys

import psutil
import pytest

from gaia.daemon import client


def test_the_spawned_process_itself_matches():
    assert client._is_spawned_daemon(4242, 4242)


def test_a_child_of_the_spawned_launcher_matches():
    # This test process is a child of its parent — the launcher-shaped relation.
    assert client._is_spawned_daemon(os.getpid(), os.getppid())


def test_an_unrelated_process_does_not_match():
    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        assert not client._is_spawned_daemon(other.pid, os.getppid())
    finally:
        other.kill()
        other.wait()


def test_a_vanished_pid_does_not_match(monkeypatch):
    def _gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", _gone)
    assert not client._is_spawned_daemon(1234, 99)


@pytest.mark.skipif(sys.platform != "win32", reason="venv launcher is Windows-only")
def test_windows_venv_launcher_registers_its_child(tmp_path):
    """A real venv on Windows: the interpreter's pid is not the launcher's."""
    import venv

    venv.EnvBuilder(with_pip=False).create(tmp_path / "v")
    launcher = tmp_path / "v" / "Scripts" / "python.exe"
    proc = subprocess.Popen(
        [
            str(launcher),
            "-c",
            "import os, sys; print(os.getpid(), flush=True); sys.stdin.read()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        reported = int(proc.stdout.readline())
        if reported == proc.pid:
            pytest.skip("this Python's venv runs in-process, no launcher child")
        assert client._is_spawned_daemon(reported, proc.pid)
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
