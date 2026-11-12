# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A shell command that reaches a local or private address says so on its card.

The web tools refuse loopback and private-network addresses outright. The shell
tool runs whatever the user approves, so it cannot refuse ``curl`` to them, but
the approval card has to say that this is somewhere the web tools would not go,
and that approval covers this one command, never an "always".
"""

from __future__ import annotations

import pytest

from gaia.agents.tools.shell_tools import (
    TIER_CONFIRM_ONCE,
    ShellToolsMixin,
    private_destination,
)


class _Host(ShellToolsMixin):
    debug = False

    def __init__(self):
        super().__init__()

        class _Console:
            auto_approve_gated_tools = False
            full_access = False

        self.console = _Console()


@pytest.mark.parametrize(
    "command, host",
    [
        ("curl -s http://127.0.0.1:8765/article.html", "127.0.0.1"),
        ("curl -L http://localhost:3000/", "localhost"),
        ("curl http://[::1]:8080/x", "[::1]"),
        ("curl -s http://192.168.1.20:8000/page", "192.168.1.20"),
        ("curl http://10.0.0.5/admin", "10.0.0.5"),
        ("curl http://169.254.169.254/latest/meta-data/", "169.254.169.254"),
        ("curl 127.0.0.1:9000/page", "127.0.0.1"),
        ("curl http://2130706433/", "2130706433"),
        ("curl http://router.lan/", "router.lan"),
        ("curl -s http://0177.0.0.1:8123/x", "0177.0.0.1"),
        ("curl -s http://017700000001:8123/x", "017700000001"),
        ("curl -s http://0x7f.0.0.1:8123/x", "0x7f.0.0.1"),
        ("curl -s http://127.1:8123/x", "127.1"),
        ("curl -s http://127.0.1:8123/x", "127.0.1"),
        ("curl -s 2130706433:8123/x", "2130706433"),
        ("curl -s 2130706433", "2130706433"),
        ("curl 0x7f000001/x", "0x7f000001"),
        ("curl -s 127.1", "127.1"),
        ("curl 0:8123/x", "0"),
        ("curl 0", "0"),
        ("curl --retry 3 192.168.1", "192.168.1"),
        ("wget -q 0300.0250.1.1/index.html", "0300.0250.1.1"),
        ("curl user:pw@10.1:8080/x", "10.1"),
        ("curl -s http://127.0.0.1.nip.io:8123/x", "127.0.0.1.nip.io"),
        ("curl -s http://localtest.me:8123/x", "localtest.me"),
        ("curl app.localtest.me:8123/x", "app.localtest.me"),
        ("curl app-192-168-1-9.sslip.io/x", "app-192-168-1-9.sslip.io"),
        ("curl http://api.localhost:3000/", "api.localhost"),
        ("Invoke-WebRequest -Uri 127.1 -TimeoutSec 30", "127.1"),
        (
            'powershell -Command "Invoke-WebRequest -Uri http://127.0.0.1:8765/x"',
            "127.0.0.1",
        ),
        (
            "python -c \"import urllib.request; urllib.request.urlopen('http://10.1.2.3/')\"",
            "10.1.2.3",
        ),
    ],
)
def test_a_command_reaching_a_private_address_is_asked_once_with_a_note(command, host):
    error, _ = _Host()._line_verdict(command)

    assert error is not None, command
    assert error["tier"] == TIER_CONFIRM_ONCE
    assert host in error["risk_note"]
    assert "web tools" in error["risk_note"]


@pytest.mark.parametrize(
    "command",
    [
        "curl -s https://example.com/page",
        "curl --retry 3 https://8.8.8.8/",
        "git status",
        "ls docs/127.0.0.1",
        'grep "http://localhost:3000" notes.md',
        "echo http://127.0.0.1:8000",
        "echo 127 apples",
        "echo 127.1",
        "pip install foo==1.2.3",
        "pip install 10.2.3",
        "curl -s https://example.com:8123/x",
        "curl example.com:8123/x",
        "curl --retry 3 --max-time 10.5 https://example.com/",
        "curl -m 10 --connect-timeout 0 https://example.com/",
        "curl --limit-rate 2130706433 https://example.com/",
        "wget -t 3 -T 10 https://example.com/file.zip",
        "curl -o v10.0.1.tar.gz https://example.com/v10.0.1.tar.gz",
        "curl 8.8.8.8.nip.io/x",
        "curl https://nip.io/",
        "curl 1.1:80/x",
        "Invoke-WebRequest -Uri https://example.com -TimeoutSec 10",
    ],
)
def test_a_public_or_unrelated_command_gets_no_private_note(command):
    error, _ = _Host()._line_verdict(command)

    assert "web tools" not in str((error or {}).get("risk_note") or "")


@pytest.mark.parametrize(
    "host, where",
    [
        ("127.0.0.1", "this PC"),
        ("LOCALHOST.", "this PC"),
        ("0.0.0.0", "this PC"),
        ("0x7f000001", "this PC"),
        ("[::ffff:192.168.0.1]", "your local network"),
        ("printer.local", "your local network"),
        ("example.com", ""),
        ("93.184.216.34", ""),
        ("0177.0.0.1", "this PC"),
        ("017700000001", "this PC"),
        ("0x7f.0.0.1", "this PC"),
        ("0X7F.0.0.1", "this PC"),
        ("127.1", "this PC"),
        ("127.0.1", "this PC"),
        ("0", "this PC"),
        ("10.1", "your local network"),
        ("192.168.257", "your local network"),
        ("0xc0.0250.1", "your local network"),
        ("0xa9fea9fe", "your local network"),
        ("010.0.0.1", ""),
        ("8.8", ""),
        ("1.2.3", ""),
        ("256.0.0.1", ""),
        ("127.0.0.256", ""),
        ("127.1.2.3.4", ""),
        ("0x", ""),
        ("089.0.0.1", ""),
        ("4294967296", ""),
        ("localtest.me", "this PC"),
        ("a.b.lvh.me", "this PC"),
        ("notlocaltest.me", ""),
        ("127.0.0.1.nip.io", "this PC"),
        ("app.127.0.0.1.nip.io", "this PC"),
        ("127-0-0-1.nip.io", "this PC"),
        ("app-10-0-0-1.nip.io", "your local network"),
        ("7f000001.nip.io", "this PC"),
        ("app.c0a80101.sslip.io", "your local network"),
        ("192.168.1.250.sslip.io", "your local network"),
        ("--1.sslip.io", "this PC"),
        ("fd00--1.sslip.io", "your local network"),
        ("8.8.8.8.nip.io", ""),
        ("app.nip.io", ""),
        ("sslip.io", ""),
        ("deadbeef.example.com", ""),
        ("999.0.0.1.nip.io", ""),
    ],
)
def test_private_destination(host, where):
    assert private_destination(host) == where
