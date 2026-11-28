# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Anyone with the tunnel token can use GAIA, so it is never logged."""

import asyncio
import logging
import subprocess

from gaia.ui import tunnel as tunnel_mod
from gaia.ui.tunnel import TunnelManager

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


def test_a_started_tunnel_logs_its_host_but_never_the_token(monkeypatch, caplog):
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
    assert "publicIp" not in status
    token = status["token"]
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert URL in logged
    assert token[:8] not in logged


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
