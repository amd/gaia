# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A first ``npx -y`` start installs the package; nothing may cut that short.

#7969: Connect gave up on a slow first start and killed npx mid-install, while
the chat started a second npx for the same package, and the cache was left
with the package but not its command. Each test runs a real stand-in ``npx``
launcher, so the pipes, the process tree and the file lock are the real ones.
"""

import os
import stat
import sys
import textwrap
import threading
import time

import pytest

from gaia.mcp.client.mcp_client import MCPClient
from gaia.mcp.client.transports.stdio import (
    StdioTransport,
    _npx_lock_target,
    npx_package,
)
from gaia.utils.file_lock import acquire_file_lock

_PACKAGE = "@modelcontextprotocol/server-memory@2026.8.31"

# Logs "start", "installs" for *install_s*, logs "installed", then answers
# JSON-RPC until its input closes. With a "broken" file beside it, the first
# run exits at once, as npx does on a package missing its command.
_FAKE_NPX = textwrap.dedent("""
    import json, os, sys, time
    here = os.path.dirname(os.path.abspath(__file__))
    log = os.path.join(here, "log.txt")
    def note(word):
        with open(log, "a") as f:
            f.write(word + "\\n")
    note("start")
    broken = os.path.join(here, "broken")
    if os.path.exists(broken):
        os.remove(broken)
        sys.exit(1)
    time.sleep(float(os.environ.get("FAKE_INSTALL_S", "0")))
    note("installed")
    for line in sys.stdin:
        req = json.loads(line)
        if "id" in req:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {
                "protocolVersion": "2024-11-05", "serverInfo": {"name": "fake"}}}) + "\\n")
            sys.stdout.flush()
    note("exited")
""")


@pytest.fixture
def npx(tmp_path, monkeypatch):
    """A stand-in ``npx`` launcher in *tmp_path*; returns its path."""
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    script = tmp_path / "fake_npx.py"
    script.write_text(_FAKE_NPX)
    if os.name == "nt":
        launcher = tmp_path / "npx.cmd"
        launcher.write_text(f'@"{sys.executable}" -u "{script}" %*\r\n')
    else:
        launcher = tmp_path / "npx"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -u "{script}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return launcher


def _log(npx):
    path = npx.parent / "log.txt"
    return path.read_text().split() if path.exists() else []


def _wait_for(check, limit=20.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def _transport(npx):
    t = StdioTransport(str(npx), args=["-y", _PACKAGE], timeout=20)
    t.name = "mcp-memory"
    return t


def test_the_package_is_read_from_an_npx_launch_only():
    assert npx_package(["C:\\node\\npx.cmd", "-y", _PACKAGE]) == _PACKAGE
    assert npx_package(["/usr/bin/npx", "--yes", "pkg"]) == "pkg"
    assert npx_package(["npx", "-y", "-p", "pkg", "cmd"]) == "pkg"
    assert npx_package(["npx", "--package=pkg", "cmd"]) == "pkg"
    assert npx_package(["/usr/bin/uvx", "pkg"]) is None


def test_a_start_given_up_on_mid_install_finishes_the_install(npx, monkeypatch):
    monkeypatch.setenv("FAKE_INSTALL_S", "1.5")
    t = _transport(npx)
    assert t.connect()

    t.disconnect()

    assert _wait_for(lambda: "installed" in _log(npx)), _log(npx)
    assert _wait_for(lambda: "exited" in _log(npx)), _log(npx)
    # The next start may go ahead once that install is done.
    acquire_file_lock(_npx_lock_target(_PACKAGE), timeout=10).release()


def test_a_second_first_start_waits_for_the_first_install(npx, monkeypatch):
    monkeypatch.setenv("FAKE_INSTALL_S", "1.0")
    first, second = _transport(npx), _transport(npx)
    assert first.connect()
    done = threading.Event()
    threading.Thread(target=lambda: (second.connect(), done.set()), daemon=True).start()
    try:
        time.sleep(0.5)
        assert _log(npx).count("start") == 1, "a second npx ran during the install"

        first.send_request("initialize", {})

        assert done.wait(20)
        assert _wait_for(lambda: _log(npx).count("start") == 2), _log(npx)
        assert (
            _log(npx).index("installed")
            < [i for i, w in enumerate(_log(npx)) if w == "start"][1]
        )
    finally:
        first.disconnect()
        second.disconnect()


def test_a_server_that_dies_on_its_first_npx_start_is_started_once_more(npx):
    (npx.parent / "broken").write_text("")
    client = MCPClient.from_config(
        "mcp-memory", {"command": str(npx), "args": ["-y", _PACKAGE], "timeout": 20}
    )
    try:
        assert client.connect(), client.last_error
        assert _log(npx).count("start") == 2
    finally:
        client.disconnect()
