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
    ],
)
def test_private_destination(host, where):
    assert private_destination(host) == where
