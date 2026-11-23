# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Anyone with the tunnel token and password can use GAIA, so neither is logged."""

import asyncio
import logging
import subprocess
import sys
import types

from gaia.ui import tunnel as tunnel_mod
from gaia.ui.tunnel import TunnelManager

PUBLIC_IP = "203.0.113.77"
URL = "https://quiet-otter.ngrok.app"


class _LiveProcess:
    pid = None
    stdout = None
    stderr = None
    stdin = None

    def __init__(self, *_a, **_kw):
        pass

    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class _IpResponse:
    status_code = 200
    text = PUBLIC_IP + "\n"


class _IpClient:
    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, _url):
        return _IpResponse()


def test_a_started_tunnel_logs_its_host_but_never_the_token_or_password(
    monkeypatch, caplog
):
    monkeypatch.setitem(
        sys.modules, "httpx", types.SimpleNamespace(AsyncClient=_IpClient)
    )
    monkeypatch.setattr(tunnel_mod, "_check_ngrok_authtoken_configured", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", _LiveProcess)

    async def _no_stale(self):
        return None

    async def _url(self):
        return URL

    monkeypatch.setattr(TunnelManager, "_kill_stale_ngrok", _no_stale)
    monkeypatch.setattr(TunnelManager, "_poll_ngrok_api", _url)

    manager = TunnelManager(port=4200)
    manager._find_ngrok = lambda: "/fake/ngrok"

    with caplog.at_level(logging.DEBUG, logger="gaia.ui.tunnel"):
        status = asyncio.run(manager.start())

    assert status["active"] is True
    assert status["publicIp"] == PUBLIC_IP
    token = status["token"]
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert URL in logged
    assert token[:8] not in logged
    assert PUBLIC_IP not in logged


def test_the_access_log_masks_the_token_a_phone_first_request_carries(caplog):
    tunnel_mod.redact_tunnel_token_in_access_log()
    tunnel_mod.redact_tunnel_token_in_access_log()
    access = logging.getLogger("uvicorn.access")
    assert (
        sum(isinstance(f, tunnel_mod._RedactTunnelToken) for f in access.filters) == 1
    )

    secret = "6f17248d-0000-4000-8000-00000000beef"
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        for path in (f"/?token={secret}", f"/?session=7&token={secret}&x=1"):
            access.info(
                '%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", path, "1.1", 303
            )
        access.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:5000",
            "GET",
            "/?x_token=k",
            "1.1",
            200,
        )

    assert secret not in caplog.text
    assert "/?token=REDACTED" in caplog.text
    assert "/?session=7&token=REDACTED&x=1" in caplog.text
    assert "/?x_token=k" in caplog.text
