# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Settings › Phone forwards to the daemon, and changes need the PC."""

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.daemon.app import create_app
from gaia.phone.service import PhoneService
from gaia.phone.store import PhoneStore
from gaia.ui.routers import phone

TOKEN = "t" * 32


class Secrets(dict):
    def set(self, name, value):
        self[name] = value

    def delete(self, name):
        self.pop(name, None)


@pytest.fixture
def client(tmp_path, monkeypatch):
    service = PhoneService(PhoneStore(tmp_path / "p.db"), Secrets())
    daemon = TestClient(
        create_app(token=TOKEN, port=1, pid=1, started_at=time.time(), phone=service)
    )
    inst = SimpleNamespace(host="daemon", port=80, token=TOKEN)
    monkeypatch.setattr("gaia.daemon.client.attach", lambda **_k: inst)

    import httpx

    real = httpx.AsyncClient

    class Routed(real):
        def __init__(self, **kw):
            super().__init__(
                transport=httpx.ASGITransport(app=daemon.app), base_url="http://daemon"
            )

    monkeypatch.setattr(phone.httpx, "AsyncClient", Routed)
    app = FastAPI()
    app.include_router(phone.router)
    return TestClient(app, client=("127.0.0.1", 50000))


def test_status_passes_through(client):
    body = client.get("/api/phone/status").json()
    assert body["enabled"] is False and body["provider"] == "simulated"


def test_changes_are_refused_over_mobile_access(client):
    r = client.post("/api/phone/simulated", headers={"x-forwarded-for": "203.0.113.5"})
    assert r.status_code == 403
    assert client.post("/api/phone/simulated").status_code == 200


def test_daemon_errors_keep_their_codes(client):
    r = client.post("/api/phone/contacts", json={"number": "911"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_number"


def test_no_daemon_is_a_clear_error(client, monkeypatch):
    monkeypatch.setattr("gaia.daemon.client.attach", lambda **_k: None)
    r = client.get("/api/phone/status")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "daemon_unavailable"
