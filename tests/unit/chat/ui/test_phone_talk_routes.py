# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI's call routes: the incoming-call webhook and the call stream."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gaia.ui import security
from gaia.ui.routers import phone


@pytest.fixture
def app(monkeypatch):
    calls = []

    async def fake_daemon(method, path, body=None):
        calls.append((method, path, body))
        if path == "/talk/incoming":
            return {"twiml": "<Response><Say>hi</Say></Response>"}
        raise RuntimeError("That call isn't known or has expired.")

    monkeypatch.setattr(phone, "_daemon_call", fake_daemon)
    application = FastAPI()
    application.include_router(phone.router)
    application.state.calls = calls
    return application


def test_incoming_webhook_forwards_the_signed_request(app):
    client = TestClient(app)
    r = client.post(
        "/api/phone/incoming",
        data={"From": "+15551230001", "To": "+15550001111"},
        headers={
            "x-twilio-signature": "sig",
            "x-forwarded-host": "abc.ngrok-free.app",
            "x-forwarded-proto": "https",
        },
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/xml")
    [(method, path, body)] = app.state.calls
    assert path == "/talk/incoming" and body["signature"] == "sig"
    # The URL Twilio signed is the tunnel's, not the loopback one.
    assert body["url"] == "https://abc.ngrok-free.app/api/phone/incoming"
    assert body["params"] == {"From": "+15551230001", "To": "+15550001111"}


def test_incoming_webhook_rejects_when_the_daemon_refuses(app, monkeypatch):
    async def down(*a, **k):
        raise RuntimeError("GAIA's background service isn't running.")

    monkeypatch.setattr(phone, "_daemon_call", down)
    r = TestClient(app).post("/api/phone/incoming", data={"From": "+1"})
    assert "<Reject/>" in r.text


def test_only_the_webhook_skips_the_browser_checks():
    assert security.CSRF_EXEMPT_PATHS == frozenset({"/api/phone/incoming"})
    from gaia.ui import server

    assert server._AUTH_EXEMPT_PATHS == {"/api/health", "/api/phone/incoming"}


def test_stream_with_an_unknown_token_ends_without_audio(app):
    with TestClient(app).websocket_connect("/api/phone/stream") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(
            json.dumps(
                {
                    "event": "start",
                    "streamSid": "MZ1",
                    "start": {"customParameters": {"token": "x" * 20}},
                }
            )
        )
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert app.state.calls[0][1] == "/talk/claim"


def test_stream_refuses_a_foreign_host(app):
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect(
            "/api/phone/stream", headers={"host": "evil.example"}
        ) as ws:
            ws.receive_text()
