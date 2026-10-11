# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for client-side daemon compatibility guidance."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil
import pytest

from gaia.daemon import client
from gaia.daemon.errors import DaemonVersionError
from gaia.daemon.instance import DaemonInstance


def _instance(api_version: str) -> DaemonInstance:
    return DaemonInstance(pid=1, port=54321, token="token", api_version=api_version)


@pytest.mark.parametrize(
    ("check", "api_version"),
    [
        (client._check_version, "2.0"),
        (client._check_agents_floor, "1.0"),
    ],
)
def test_daemon_version_errors_guide_users_to_upgrade_the_installed_core(
    check, api_version
):
    with pytest.raises(DaemonVersionError) as exc_info:
        check(_instance(api_version))

    message = str(exc_info.value)
    assert f"v{api_version}" in message
    assert "pip install --upgrade amd-gaia" in message
    assert "https://amd-gaia.ai" in message
    assert "gaia daemon restart` brings the same one back" in message


def test_a_daemon_started_through_a_venv_launcher_is_recognised():
    """A Windows venv's python.exe runs the real interpreter as its child."""
    launcher = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys; "
            "subprocess.run([sys.executable, '-c', 'import time; time.sleep(30)'])",
        ]
    )
    try:
        deadline = time.monotonic() + 15
        children = []
        while not children and time.monotonic() < deadline:
            children = psutil.Process(launcher.pid).children(recursive=True)
            time.sleep(0.05)
        assert children, "launcher never started its child"
        assert client._spawned_by(children[-1].pid, launcher.pid)
    finally:
        for child in psutil.Process(launcher.pid).children(recursive=True):
            child.kill()
        launcher.kill()
        launcher.wait()


def test_a_daemon_that_is_not_our_child_is_not_trusted():
    assert client._spawned_by(os.getpid(), os.getpid())
    assert not client._spawned_by(os.getppid(), os.getpid())
    assert not client._spawned_by(2**22 + 12345, os.getpid())
