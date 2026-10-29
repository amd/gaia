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

from gaia.ui import tunnel as tunnel_mod
from gaia.ui.tunnel import TunnelManager, _parse_ngrok_error

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


class _Fake:
    """A stand-in ngrok process that never touches a real pid."""

    pid = None
    stdout = stderr = None

    def __init__(self, exit_code=None):
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


async def test_the_event_loop_keeps_serving_while_ngrok_is_stopped(monkeypatch):
    def slow_shut_down(*_args, **_kwargs):
        time.sleep(0.6)

    monkeypatch.setattr(tunnel_mod, "_shut_down", slow_shut_down)
    manager = TunnelManager(port=4200)
    manager._process = _Fake()
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    beat = asyncio.create_task(ticker())
    try:
        await manager.stop()
    finally:
        beat.cancel()

    assert ticks >= 5, f"the loop ticked {ticks} times during a 0.6 s stop"


async def test_a_timeout_with_no_known_error_explains_the_timeout(monkeypatch):
    monkeypatch.setattr(tunnel_mod, "_terminate_tree", lambda *_a, **_k: None)
    manager = TunnelManager(port=4200)
    manager._process = _Fake(exit_code=None)
    monkeypatch.setattr(
        manager,
        "_drain_ngrok_output",
        lambda: 't=1 lvl=info msg="open config file" path=C:\\ngrok.yml err=nil',
    )

    assert await manager._poll_ngrok_api(timeout=0.2, interval=0.05) is None

    assert manager._error.startswith("GAIA's ngrok agent didn't open")
    assert "`ngrok http 4200`" in manager._error
    assert "open config file" not in manager._error


async def test_a_timeout_with_a_known_error_still_names_it(monkeypatch):
    monkeypatch.setattr(tunnel_mod, "_terminate_tree", lambda *_a, **_k: None)
    manager = TunnelManager(port=4200)
    manager._process = _Fake(exit_code=None)
    monkeypatch.setattr(
        manager, "_drain_ngrok_output", lambda: "authentication failed: ERR_NGROK_108"
    )

    await manager._poll_ngrok_api(timeout=0.2, interval=0.05)

    assert manager._error == tunnel_mod._NGROK_SESSION_LIMIT_HINT


def test_a_reused_pid_is_never_walked_for_children():
    class _StaleHandle:
        def is_running(self):
            return False  # the start time no longer matches: pid reused

        def children(self, recursive=False):
            raise AssertionError("walked the children of a reused pid")

    tunnel_mod._terminate_tree(_Fake(exit_code=0), 0.1, _StaleHandle())


def test_no_handle_is_taken_for_an_already_reaped_child():
    class _Reaped(_Fake):
        pid = 4242

    assert tunnel_mod._process_handle(_Reaped(exit_code=0)) is None


def test_the_windows_bind_error_is_a_port_conflict():
    output = (
        'lvl=eror msg="failed to start web service" err="listen tcp '
        "127.0.0.1:4040: bind: Only one usage of each socket address "
        '(protocol/network address/port) is normally permitted."'
    )

    assert _parse_ngrok_error(output) == tunnel_mod._NGROK_PORT_CONFLICT_HINT
