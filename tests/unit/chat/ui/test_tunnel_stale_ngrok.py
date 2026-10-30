# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Starting the mobile tunnel must not kill the user's own ngrok tunnels.

It used to run ``taskkill /f /im ngrok.exe`` / ``pkill -x ngrok``, which ends
every ngrok agent on the machine. Only ngrok agents GAIA itself started for
this tunnel may be stopped, by PID. Every process here is a fake.
"""

from __future__ import annotations

import subprocess

import psutil
import pytest

from gaia.ui import tunnel as tunnel_mod
from gaia.ui.tunnel import TunnelManager


class _FakeNgrok:
    def __init__(self, pid, name, cmdline, killed, deny=False):
        self.pid = pid
        self.info = {"name": name, "cmdline": cmdline}
        self._killed = killed
        self._deny = deny

    def kill(self):
        if self._deny:
            raise psutil.AccessDenied(self.pid)
        self._killed.append(self.pid)


_GAIA_ARGS = ["http", "--log=stdout", "--log-format=logfmt", "4200"]


@pytest.fixture
def machine(monkeypatch):
    """A fake process table; returns ``(killed_pids, subprocess_calls)``."""
    killed, calls = [], []
    table = [
        # The user's own tunnels, including one on the same port.
        _FakeNgrok(11, "ngrok.exe", ["ngrok.exe", "http", "8080"], killed),
        _FakeNgrok(12, "ngrok", ["ngrok", "http", "4200"], killed),
        _FakeNgrok(13, "ngrok", ["ngrok", "start", "--all"], killed),
        # An orphan from a GAIA server that died without stopping its tunnel.
        _FakeNgrok(21, "ngrok.exe", ["C:\\tools\\ngrok.exe", *_GAIA_ARGS], killed),
        # Not ngrok at all, though the arguments match.
        _FakeNgrok(31, "python.exe", ["python.exe", *_GAIA_ARGS], killed),
    ]

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tunnel_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(psutil, "process_iter", lambda *_a, **_k: iter(table))
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout=None: ([], []))
    return killed, calls


async def test_never_kills_ngrok_by_name(machine):
    killed, calls = machine

    await TunnelManager(port=4200)._kill_stale_ngrok()

    assert not any(argv[0] in ("taskkill", "pkill") for argv in calls), calls
    assert 11 not in killed and 12 not in killed and 13 not in killed


async def test_kills_only_gaias_own_stale_agent(machine):
    killed, _ = machine

    await TunnelManager(port=4200)._kill_stale_ngrok()

    assert killed == [21]


async def test_other_port_or_domain_is_not_ours(machine):
    killed, _ = machine

    await TunnelManager(port=4300)._kill_stale_ngrok()
    await TunnelManager(port=4200, domain="me.ngrok.app")._kill_stale_ngrok()

    assert killed == []


def test_spawned_command_is_the_signature_the_sweep_matches():
    manager = TunnelManager(port=4200, domain="me.ngrok.app")

    assert manager._ngrok_args() == [
        "http",
        "--log=stdout",
        "--log-format=logfmt",
        "--domain",
        "me.ngrok.app",
        "4200",
    ]


async def test_stops_its_own_previous_process_first(machine):
    manager = TunnelManager(port=4200)
    stopped = []

    class _Own:
        stdout = stderr = None

        def terminate(self):
            stopped.append("terminate")

        def wait(self, timeout=None):
            return 0

    manager._process = _Own()

    await manager._kill_stale_ngrok()

    assert stopped == ["terminate"]
    assert manager._process is None


async def test_unkillable_stale_agent_is_reported_not_raised(monkeypatch):
    stale = _FakeNgrok(21, "ngrok.exe", ["ngrok.exe", *_GAIA_ARGS], [], deny=True)
    monkeypatch.setattr(psutil, "process_iter", lambda *_a, **_k: iter([stale]))
    monkeypatch.setattr(tunnel_mod, "_check_ngrok_authtoken_configured", lambda: True)
    manager = TunnelManager(port=4200)
    manager._find_ngrok = lambda: "ngrok"

    async def _no_ip():
        manager._public_ip = None

    manager._fetch_public_ip = _no_ip

    status = await manager.start()

    assert status["active"] is False
    assert "pid 21" in status["error"]


@pytest.mark.parametrize(
    "addr,ours",
    [
        ("http://localhost:4200", True),
        ("localhost:4200", True),
        ("4200", True),
        ("http://localhost:8080", False),
        ("http://localhost:42000", False),
        ("", False),
    ],
)
def test_only_a_tunnel_to_our_port_is_ours(addr, ours):
    tunnel = {"proto": "https", "public_url": "https://x", "config": {"addr": addr}}

    assert TunnelManager(port=4200)._is_our_tunnel(tunnel) is ours


async def test_users_tunnel_on_the_ngrok_api_port_is_never_handed_out(monkeypatch):
    """The user's ngrok can answer on :4040; GAIA must not report its URL."""
    import httpx

    foreign = {
        "tunnels": [
            {
                "proto": "https",
                "public_url": "https://users-own.ngrok.app",
                "config": {"addr": "http://localhost:8080"},
            }
        ]
    }

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, _url):
            return httpx.Response(200, json=foreign)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    manager = TunnelManager(port=4200)

    url = await manager._poll_ngrok_api(timeout=0.2, interval=0.05)

    assert url is None
