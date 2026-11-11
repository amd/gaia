# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Settings → Messaging: the Discord routes are local-PC only, reads included."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.messaging.discord import cli as discord_cli
from gaia.messaging.discord.transport import DiscordTokenError
from gaia.ui.routers import messaging

LOCAL = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_HOME", raising=False)


def make_client(peer=LOCAL, headers=None) -> TestClient:
    app = FastAPI()
    app.include_router(messaging.router)
    return TestClient(app, client=peer, headers=headers or {"X-Gaia-UI": "1"})


def test_status_reads_from_the_pc():
    response = make_client().get("/api/messaging/discord")
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    assert body["portal_url"].startswith("https://discord.com/developers")


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/messaging/discord"),
        ("post", "/api/messaging/discord/connect"),
        ("post", "/api/messaging/discord/pair"),
        ("post", "/api/messaging/discord/start"),
    ],
)
def test_mobile_access_cannot_read_the_code_or_swap_the_bot(method, path):
    client = make_client(headers={"X-Gaia-UI": "1", "X-Forwarded-For": "203.0.113.9"})
    kwargs = {"json": {"token": "x"}} if path.endswith("connect") else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403


def test_another_computer_is_refused():
    response = make_client(peer=("192.168.1.20", 50000)).get("/api/messaging/discord")
    assert response.status_code == 403


def test_a_write_without_the_ui_header_is_refused():
    client = make_client(headers={"X-Gaia-UI": ""})
    response = client.post("/api/messaging/discord/stop")
    assert response.status_code == 403


def test_a_refused_token_is_a_400_with_discords_reason(monkeypatch):
    def refuse(_token):
        raise DiscordTokenError("Discord refused that token.")

    monkeypatch.setattr(discord_cli, "connect", refuse)
    response = make_client().post(
        "/api/messaging/discord/connect", json={"token": "bad"}
    )
    assert response.status_code == 400
    assert "refused" in response.json()["detail"]


def test_connect_starts_the_bridge(monkeypatch):
    started = []
    monkeypatch.setattr(discord_cli, "connect", lambda token: {"connected": True})
    monkeypatch.setattr(
        discord_cli,
        "start_detached",
        lambda **kw: started.append(kw) or 0,
    )
    response = make_client().post(
        "/api/messaging/discord/connect",
        json={"token": "tok", "model": "fireworks.deepseek-v4p1-flash"},
    )
    assert response.status_code == 200
    assert started and started[0]["model"] == "fireworks.deepseek-v4p1-flash"


def test_disconnect_keeps_everything_when_the_bridge_will_not_stop(monkeypatch):
    from gaia.messaging.discord import credentials, state

    state.save_config(state.BridgeConfig(bot_id="9", owner_id="111"))
    cleared = []
    monkeypatch.setattr(credentials, "clear", lambda: cleared.append(1))
    monkeypatch.setattr(discord_cli, "stop", lambda **_: False)
    response = make_client().post("/api/messaging/discord/disconnect")
    assert response.status_code == 500
    assert "did not stop" in response.json()["detail"]
    assert cleared == [], "the token must be kept while the bridge still runs"
    assert state.load_config().owner_id == "111"


def test_stop_failure_names_this_os_tool(monkeypatch):
    monkeypatch.setattr(discord_cli, "stop", lambda **_: False)
    detail = make_client().post("/api/messaging/discord/stop").json()["detail"]
    assert "gaia discord start" in detail


def test_restart_stops_then_starts(monkeypatch):
    calls = []
    monkeypatch.setattr(discord_cli, "stop", lambda **_: calls.append("stop") or True)
    monkeypatch.setattr(
        discord_cli, "start_detached", lambda **_: calls.append("start") or 0
    )
    assert make_client().post("/api/messaging/discord/restart").status_code == 200
    assert calls == ["stop", "start"]


def test_forgetting_always_allow_restarts_a_running_bridge(monkeypatch):
    from gaia.messaging.discord import state

    calls = []
    monkeypatch.setattr(state, "is_running", lambda: True)
    monkeypatch.setattr(state, "forget_always", lambda: calls.append("forget") or 2)
    monkeypatch.setattr(discord_cli, "stop", lambda **_: calls.append("stop") or True)
    monkeypatch.setattr(
        discord_cli, "start_detached", lambda **_: calls.append("start") or 0
    )
    response = make_client().post("/api/messaging/discord/forget-always")
    assert response.status_code == 200
    assert calls == ["stop", "forget", "start"]


def test_forgetting_keeps_everything_when_the_bridge_will_not_stop(monkeypatch):
    from gaia.messaging.discord import state

    forgot = []
    monkeypatch.setattr(state, "is_running", lambda: True)
    monkeypatch.setattr(state, "forget_always", lambda: forgot.append(1))
    monkeypatch.setattr(discord_cli, "stop", lambda **_: False)
    response = make_client().post("/api/messaging/discord/forget-always")
    assert response.status_code == 500 and forgot == []


def test_status_lists_what_is_always_allowed(monkeypatch):
    from gaia.messaging.discord import state

    config = state.BridgeConfig(bot_id="9", owner_id="111", dm_channel_id="5")
    state.save_config(config)
    state.remember_always("111", ["k1"], "dir")
    body = make_client().get("/api/messaging/discord").json()
    assert body["always_allowed"] == ["dir"]
