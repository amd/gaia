# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Starting and stopping mobile access happens only on the PC running GAIA."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.ui.routers import tunnel as tunnel_router


class _FakeTunnel:
    def __init__(self):
        self.calls = []

    async def start(self):
        self.calls.append("start")
        return {"active": True}

    async def stop(self):
        self.calls.append("stop")

    def get_status(self):
        return {"active": False}


def _client(peer=("127.0.0.1", 50000)):
    app = FastAPI()
    app.include_router(tunnel_router.router)
    app.state.tunnel = _FakeTunnel()
    return TestClient(app, client=peer), app.state.tunnel


@pytest.mark.parametrize("action", ["start", "stop"])
def test_the_pc_can_start_and_stop_mobile_access(action):
    client, tunnel = _client()
    assert client.post(f"/api/tunnel/{action}").status_code == 200
    assert tunnel.calls == [action]


@pytest.mark.parametrize("action", ["start", "stop"])
def test_mobile_access_itself_cannot_start_or_stop_it(action):
    client, tunnel = _client()
    resp = client.post(
        f"/api/tunnel/{action}", headers={"X-Forwarded-For": "203.0.113.9"}
    )
    assert resp.status_code == 403
    assert tunnel.calls == []


@pytest.mark.parametrize("action", ["start", "stop"])
def test_another_computer_cannot_start_or_stop_mobile_access(action):
    client, tunnel = _client(peer=("192.168.1.20", 50000))
    assert client.post(f"/api/tunnel/{action}").status_code == 403
    assert tunnel.calls == []
