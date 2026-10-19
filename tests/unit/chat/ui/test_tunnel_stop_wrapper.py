# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stopping the tunnel must not hang when ngrok runs through a wrapper.

``ngrok.cmd`` or a package-manager shim starts the real agent as a child.
Terminating only the wrapper left the child holding the pipes, and closing a
pipe a reader thread was blocked on then hung ``stop()``, and with it the UI
server's event loop, until the child exited. The "wrapper" and "agent" here
are throwaway Python processes this test starts and always cleans up.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from gaia.ui.tunnel import TunnelManager

# Starts a long-lived child that inherits stdout/stderr, records its pid,
# then waits like a wrapper does.
_WRAPPER = r"""
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(sys.argv[1], "w") as f:
    f.write(str(child.pid))
time.sleep(60)
"""


def _kill(pid):
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        pass


@pytest.fixture
def wrapped_agent(tmp_path):
    pid_file = tmp_path / "child.pid"
    wrapper = subprocess.Popen(
        [sys.executable, "-c", _WRAPPER, str(pid_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 15
        while not (pid_file.exists() and pid_file.read_text()):
            assert time.monotonic() < deadline, "wrapper never started its child"
            time.sleep(0.05)
        child_pid = int(Path(pid_file).read_text())
        yield wrapper, child_pid
    finally:
        if child_pid:
            _kill(child_pid)
        _kill(wrapper.pid)
        wrapper.wait(timeout=10)


async def test_stop_returns_promptly_and_ends_the_wrapped_agent(wrapped_agent):
    wrapper, child_pid = wrapped_agent
    manager = TunnelManager(port=4200)
    manager._process = wrapper
    manager._start_output_readers()

    started = time.monotonic()
    await asyncio.wait_for(manager.stop(), 20)
    elapsed = time.monotonic() - started

    assert elapsed < 2, f"stop() took {elapsed:.1f}s"
    assert not psutil.pid_exists(child_pid) or (
        psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
    )


async def test_a_failed_agent_output_is_drained_off_the_event_loop(monkeypatch):
    manager = TunnelManager(port=4200)
    seen = []

    def drain():
        seen.append(threading.get_ident())
        return "ERR_NGROK_108"

    class _Exited:
        pid = None
        stdout = stderr = None

        def poll(self):
            return 1

    manager._process = _Exited()
    monkeypatch.setattr(manager, "_drain_ngrok_output", drain)

    await manager._poll_ngrok_api(timeout=0.2, interval=0.05)

    assert seen and seen[0] != threading.get_ident()
