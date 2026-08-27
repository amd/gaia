# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/gateway/*`` — the routes that let the TUI persist a gateway token.

The TUI is Go and the credential store is Python; go-keyring and python-keyring
do not share a Credential Manager target name, so the token has to travel
through the daemon. These tests pin the parts a caller depends on: the token is
guarded by the daemon client token, an unavailable store is a 503 carrying the
platform remedy, and the token is never echoed back or logged.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.daemon.gateway_routes import build_gateway_router

# TestClient drives ASGI over a local socket; nothing leaves the machine.
pytestmark = pytest.mark.allow_network

TOKEN = "daemon-client-token"
SECRET = "gateway-token-value"


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(build_gateway_router(TOKEN))
    return TestClient(app)


@pytest.fixture()
def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_token_route_requires_the_daemon_client_token(client):
    resp = client.post("/daemon/v1/gateway/token", json={"token": SECRET})
    assert resp.status_code == 401


def test_remember_stores_the_token_and_never_returns_it(client, auth, monkeypatch):
    stored = []
    monkeypatch.setattr("gaia.llm.gateway.remember_token", lambda t: stored.append(t))

    resp = client.post("/daemon/v1/gateway/token", json={"token": SECRET}, headers=auth)

    assert resp.status_code == 200
    assert stored == [SECRET]
    assert SECRET not in resp.text


def test_empty_token_is_rejected_before_it_reaches_the_store(client, auth, monkeypatch):
    monkeypatch.setattr(
        "gaia.llm.gateway.remember_token",
        lambda t: pytest.fail("an empty token must not reach the store"),
    )

    assert (
        client.post("/daemon/v1/gateway/token", json={"token": ""}, headers=auth)
    ).status_code == 422


def test_unavailable_store_is_a_503_carrying_the_remedy(client, auth, monkeypatch):
    from gaia.llm.gateway import GatewayError

    remedy = "No usable credential store. Set LEMONADE_AMD_API_KEY instead."

    def boom(_):
        raise GatewayError(remedy)

    monkeypatch.setattr("gaia.llm.gateway.remember_token", boom)

    resp = client.post("/daemon/v1/gateway/token", json={"token": SECRET}, headers=auth)

    # 503 rather than 500: the TUI branches on it to say "this session only".
    assert resp.status_code == 503
    assert remedy in resp.json()["detail"]


def test_the_token_is_never_logged(client, auth, monkeypatch, caplog):
    monkeypatch.setattr("gaia.llm.gateway.remember_token", lambda t: None)

    with caplog.at_level("DEBUG"):
        client.post("/daemon/v1/gateway/token", json={"token": SECRET}, headers=auth)

    assert SECRET not in caplog.text


def test_authenticate_replays_the_stored_token_without_exposing_it(
    client, auth, monkeypatch
):
    class FakeManager:
        def ensure_authenticated(self):
            return True

    monkeypatch.setattr("gaia.llm.gateway.GatewayManager", FakeManager)

    resp = client.post("/daemon/v1/gateway/authenticate", headers=auth)

    assert resp.status_code == 200
    assert resp.json() == {"authenticated": True}


def test_authenticate_reports_false_when_nothing_is_stored(client, auth, monkeypatch):
    class FakeManager:
        def ensure_authenticated(self):
            return False

    monkeypatch.setattr("gaia.llm.gateway.GatewayManager", FakeManager)

    resp = client.post("/daemon/v1/gateway/authenticate", headers=auth)

    assert resp.json() == {"authenticated": False}


def test_forget_is_idempotent(client, auth, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "gaia.llm.gateway.forget_token", lambda: bool(calls.append(1)) or False
    )

    first = client.delete("/daemon/v1/gateway/token", headers=auth)
    second = client.delete("/daemon/v1/gateway/token", headers=auth)

    assert first.status_code == second.status_code == 200
    assert len(calls) == 2
