# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's tunnel must tell its own ngrok agent apart from the user's.

Now that the user's ngrok is left running, it can hold the free-tier session
or ngrok's local API on :4040. GAIA must report that plainly, find its own
agent wherever it bound, and never mistake a process it can't inspect for
one it may ignore. Every process and HTTP endpoint here is a fake.
"""

from __future__ import annotations

import io

import httpx
import psutil
import pytest

from gaia.ui import tunnel as tunnel_mod
from gaia.ui.tunnel import (
    _NGROK_SESSION_LIMIT_HINT,
    TunnelManager,
    _parse_ngrok_error,
    _StaleNgrokError,
)

_GAIA_ARGS = ["http", "--log=stdout", "--log-format=logfmt", "4200"]

# ngrok 3's real ERR_NGROK_108 output, console and logfmt forms.
_SESSION_LIMIT_CONSOLE = (
    "authentication failed: Your account is limited to 1 simultaneous ngrok "
    "agent sessions.\nYou can run multiple simultaneous tunnels from a single "
    "agent session by defining the tunnels in your agent configuration file "
    "and starting them with the command `ngrok start --all`.\nYou can view "
    "your current agent sessions in the dashboard:\n"
    "https://dashboard.ngrok.com/agents\n\nERR_NGROK_108\n"
)
_SESSION_LIMIT_LOGFMT = (
    't=2026-10-18T20:00:00-0700 lvl=eror msg="failed to reconnect session" '
    'obj=tunnels.session err="authentication failed: Your account is limited '
    "to 1 simultaneous ngrok agent sessions.\\r\\nYou can view your current "
    "agent sessions in the dashboard:\\r\\nhttps://dashboard.ngrok.com/agents"
    '\\r\\n\\r\\nERR_NGROK_108\\r\\n"'
)


@pytest.mark.parametrize(
    "output", [_SESSION_LIMIT_CONSOLE, _SESSION_LIMIT_LOGFMT], ids=["console", "logfmt"]
)
def test_session_limit_is_not_reported_as_a_bad_authtoken(output):
    msg = _parse_ngrok_error(output)

    assert msg == _NGROK_SESSION_LIMIT_HINT
    assert "add-authtoken" not in msg
    assert "only session" in msg


def test_a_real_authentication_failure_still_names_the_authtoken():
    msg = _parse_ngrok_error("authentication failed: authtoken missing")

    assert "add-authtoken" in msg


# --- Finding GAIA's own agent ------------------------------------------------


def _manager_with_log(*lines):
    manager = TunnelManager(port=4200)
    manager._output.extend(lines)
    return manager


def _api(monkeypatch, by_host):
    """Fake ngrok local APIs: ``{"127.0.0.1:4040": [tunnel, ...]}``."""
    requested = []

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url):
            requested.append(url)
            host = url.split("//", 1)[1].split("/", 1)[0]
            if host not in by_host:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, json={"tunnels": by_host[host]})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return requested


def _tunnel(url, addr):
    return {"proto": "https", "public_url": url, "config": {"addr": addr}}


_USERS = _tunnel("https://users.ngrok.app", "http://localhost:8080")
_OURS = _tunnel("https://gaia.ngrok.app", "http://localhost:4200")


async def test_finds_its_agent_on_4041_when_the_users_owns_4040(monkeypatch):
    requested = _api(
        monkeypatch, {"127.0.0.1:4040": [_USERS], "127.0.0.1:4041": [_OURS]}
    )
    manager = _manager_with_log(
        't=1 lvl=info msg="starting web service" obj=web addr=127.0.0.1:4041'
    )

    url = await manager._poll_ngrok_api(timeout=0.2, interval=0.05)

    assert url == "https://gaia.ngrok.app"
    assert all("4041" in r for r in requested)


async def test_reads_the_url_from_its_own_log(monkeypatch):
    requested = _api(monkeypatch, {"127.0.0.1:4040": [_USERS]})
    manager = _manager_with_log(
        't=1 lvl=info msg="started tunnel" obj=tunnels name=command_line '
        "addr=http://localhost:4200 url=https://from-log.ngrok.app"
    )

    url = await manager._poll_ngrok_api(timeout=0.2, interval=0.05)

    assert url == "https://from-log.ngrok.app"
    assert requested == []


async def test_logged_tunnel_to_another_port_is_ignored(monkeypatch):
    _api(monkeypatch, {})
    manager = _manager_with_log(
        't=1 lvl=info msg="started tunnel" addr=http://localhost:8080 '
        "url=https://other.ngrok.app"
    )

    assert await manager._poll_ngrok_api(timeout=0.1, interval=0.05) is None


async def test_a_foreign_api_owner_is_named_in_the_error(monkeypatch):
    _api(monkeypatch, {"127.0.0.1:4040": [_USERS]})
    manager = _manager_with_log()

    assert await manager._poll_ngrok_api(timeout=0.2, interval=0.05) is None

    assert "not GAIA's" in manager._error
    assert "authtoken" not in manager._error


def test_output_readers_collect_lines_while_ngrok_runs():
    manager = TunnelManager(port=4200)

    class _Proc:
        stdout = io.BytesIO(b'msg="starting web service" addr=127.0.0.1:4041\r\n')
        stderr = io.BytesIO(b"second line\n")

    manager._process = _Proc()
    manager._start_output_readers()

    text = manager._drain_ngrok_output()

    assert "second line" in text
    assert manager._logged_web_addr() == "127.0.0.1:4041"


@pytest.mark.parametrize(
    "addr,ours",
    [
        ("http://127.0.0.1:4200", True),
        ("http://[::1]:4200", True),
        ("https://localhost:4200", True),
        ("http://evil.example:4200", False),
        ("http://192.168.1.5:4200", False),
        ("http://localhost:notaport", False),
    ],
)
def test_only_a_local_address_on_our_port_is_ours(addr, ours):
    assert TunnelManager(port=4200)._is_our_tunnel(_tunnel("https://x", addr)) is ours


# --- The stale-agent sweep ---------------------------------------------------


class _Proc:
    def __init__(self, pid, name, cmdline, on_kill=None):
        self.pid = pid
        self.info = {"name": name, "cmdline": cmdline}
        self._on_kill = on_kill
        self.killed = False

    def kill(self):
        if self._on_kill:
            raise self._on_kill(self.pid)
        self.killed = True


def _table(monkeypatch, procs, alive=()):
    monkeypatch.setattr(psutil, "process_iter", lambda *_a, **_k: iter(procs))
    monkeypatch.setattr(
        psutil, "wait_procs", lambda procs, timeout=None: ([], list(alive))
    )


async def test_an_ngrok_it_cannot_inspect_is_reported(monkeypatch):
    _table(monkeypatch, [_Proc(41, "ngrok.exe", None)])

    with pytest.raises(_StaleNgrokError, match="pid 41"):
        await TunnelManager(port=4200)._kill_stale_ngrok()


async def test_an_uninspectable_non_ngrok_process_is_ignored(monkeypatch):
    _table(monkeypatch, [_Proc(42, "svchost.exe", None)])

    await TunnelManager(port=4200)._kill_stale_ngrok()


async def test_a_stale_agent_that_survives_the_kill_is_reported(monkeypatch):
    stale = _Proc(21, "ngrok.exe", ["ngrok.exe", *_GAIA_ARGS])
    _table(monkeypatch, [stale], alive=[stale])

    with pytest.raises(_StaleNgrokError, match="pid 21"):
        await TunnelManager(port=4200)._kill_stale_ngrok()


async def test_an_agent_that_already_exited_is_skipped(monkeypatch):
    gone = _Proc(21, "ngrok.exe", ["ngrok.exe", *_GAIA_ARGS], psutil.NoSuchProcess)
    other = _Proc(22, "ngrok", ["ngrok", *_GAIA_ARGS])
    _table(monkeypatch, [gone, other])

    await TunnelManager(port=4200)._kill_stale_ngrok()

    assert other.killed


async def test_sweep_errors_reach_the_tunnel_status(monkeypatch):
    _table(monkeypatch, [_Proc(41, "ngrok.exe", None)])
    monkeypatch.setattr(tunnel_mod, "_check_ngrok_authtoken_configured", lambda: True)
    manager = TunnelManager(port=4200)
    manager._find_ngrok = lambda: "ngrok"

    async def _no_ip():
        manager._public_ip = None

    manager._fetch_public_ip = _no_ip

    status = await manager.start()

    assert status["active"] is False
    assert "pid 41" in status["error"]
