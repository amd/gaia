# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI's and the flagship sidecar's Home Assistant routes.

Both wrap :mod:`gaia.connectors.home_assistant`; these tests drive each over
HTTP against the fake Home Assistant, so the two apps are held to the same
contract: a refused connect is a 400 whose ``detail`` is the user-facing fix.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fixtures.home_assistant.fake_ha import FakeHomeAssistant

pytestmark = pytest.mark.allow_network

TOKEN = "route-token-xyz"


@pytest.fixture
def fake():
    server = FakeHomeAssistant(TOKEN)
    server.base = server.start()
    yield server
    server.stop()


def _ui_client() -> tuple[TestClient, str]:
    from gaia.ui.routers import home_assistant as routes

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app, headers={"X-Gaia-UI": "1"}), "/api/home-assistant"


def _sidecar_client() -> tuple[TestClient, str]:
    from gaia_agent import server as sidecar

    app = FastAPI()
    app.include_router(sidecar.router, prefix="/v1/gaia")
    app.dependency_overrides[sidecar.require_caller_token] = lambda: None
    return TestClient(app), "/v1/gaia/home-assistant"


@pytest.fixture(params=["agent_ui", "sidecar"])
def api(request):
    return _ui_client() if request.param == "agent_ui" else _sidecar_client()


def _connect(client, path, body):
    if path.startswith("/v1/"):
        return client.put(path, json=body)
    return client.post(path, json=body)


def test_full_lifecycle(api, fake):
    client, path = api
    assert client.get(path).json() == {"connected": False, "url": None, "active": False}

    connected = _connect(client, path, {"url": fake.base, "token": TOKEN})
    assert connected.status_code == 200, connected.text
    body = connected.json()
    assert body["connected"] and body["active"] and body["tool_count"] == 4
    assert TOKEN not in connected.text

    status = client.get(path).json()
    assert status["connected"] and status["url"] == fake.base and status["active"]

    tested = client.post(f"{path}/test").json()
    assert tested["ok"] is True

    gone = client.delete(path)
    assert gone.status_code == 200 and "still works" in gone.json()["detail"]
    assert client.get(path).json()["connected"] is False


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"token": "wrong"}, "rejected the access token"),
        ({"token": TOKEN, "url_suffix": "/api"}, "without a path"),
        ({"token": " "}, "long-lived access token"),
    ],
)
def test_a_refused_connect_is_a_400_with_the_fix(api, fake, body, fragment):
    client, path = api
    body = dict(body)
    url = fake.base + body.pop("url_suffix", "")
    response = _connect(client, path, {"url": url, **body})
    assert response.status_code == 400
    assert fragment in response.json()["detail"]
    assert client.get(path).json()["connected"] is False


def test_sidecar_rejects_unknown_fields(fake):
    client, path = _sidecar_client()
    response = client.put(path, json={"url": fake.base, "token": TOKEN, "extra": 1})
    assert response.status_code == 422


def test_messaging_bridge_children_are_marked(monkeypatch):
    from gaia.messaging.bridge import AgentChannel

    channel = AgentChannel(argv=["gaia-agent"], env={"KEEP": "1"}, on_event=print)
    env = channel._child_env()
    assert env["GAIA_MESSAGING_CHANNEL"] == "1" and env["KEEP"] == "1"
    # Inheriting: read at spawn, so a variable set after the channel exists counts.
    inherited = AgentChannel(argv=["gaia-agent"], on_event=print)
    monkeypatch.setenv("SET_LATER", "yes")
    env = inherited._child_env()
    assert env["GAIA_MESSAGING_CHANNEL"] == "1" and env["SET_LATER"] == "yes"
