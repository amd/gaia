# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon's phone routes, through the real app factory."""

import re
import time

import pytest
from fastapi.testclient import TestClient

from gaia.daemon.app import create_app
from gaia.daemon.custody.auth import CustodyAuth
from gaia.daemon.custody.store import CustodyStore
from gaia.phone.service import PhoneService
from gaia.phone.store import PhoneStore

TOKEN = "t" * 32
ME = "+15551230001"


class Secrets(dict):
    def set(self, name, value):
        self[name] = value

    def delete(self, name):
        self.pop(name, None)


@pytest.fixture
def setup(tmp_path):
    service = PhoneService(PhoneStore(tmp_path / "phone.db"), Secrets())
    custody_store = CustodyStore(tmp_path / "custody.db")
    custody_auth = CustodyAuth()
    app = create_app(
        token=TOKEN,
        port=1,
        pid=1,
        started_at=time.time(),
        custody_auth=custody_auth,
        custody_store=custody_store,
        phone=service,
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        yield client, service, custody_auth
    custody_store.close()


def _last_code(service):
    for call in reversed(service.simulated.calls):
        m = re.search(r"code is ([\d, ]+)\.", call["script"])
        if m:
            return m.group(1).replace(", ", "")
    raise AssertionError("no code sent")


def _enroll(client, service):
    assert client.post("/daemon/v1/phone/simulated").status_code == 200
    started = client.post(
        "/daemon/v1/phone/contacts", json={"number": "(555) 123-0001"}
    )
    assert started.status_code == 200, started.text
    body = started.json()
    confirmed = client.post(
        "/daemon/v1/phone/contacts/confirm",
        json={"verification_id": body["verification_id"], "code": _last_code(service)},
    )
    assert confirmed.status_code == 200, confirmed.text


def test_every_route_needs_the_token(setup):
    client, _, _ = setup
    del client.headers["Authorization"]
    for method, path in [
        ("get", "/daemon/v1/phone/status"),
        ("put", "/daemon/v1/phone/settings"),
        ("post", "/daemon/v1/phone/contacts"),
        ("post", "/daemon/v1/phone/alerts"),
        ("post", "/daemon/v1/phone/test"),
    ]:
        assert client.request(method.upper(), path, json={}).status_code == 401, path


def test_setup_with_the_simulated_provider(setup):
    client, service, _ = setup
    _enroll(client, service)
    on = client.put("/daemon/v1/phone/settings", json={"enabled": True})
    assert on.status_code == 200 and on.json()["enabled"] is True
    status = client.get("/daemon/v1/phone/status").json()
    assert status["configured"] and status["contacts"][0]["number"] == "+1 •••• 0001"
    # The full number never comes back out.
    assert ME not in client.get("/daemon/v1/phone/status").text


def test_test_alert_calls_the_phone_book(setup):
    client, service, _ = setup
    _enroll(client, service)
    client.put("/daemon/v1/phone/settings", json={"enabled": True})
    r = client.post("/daemon/v1/phone/test")
    assert r.status_code == 200
    alert = r.json()["alert"]
    assert alert["kind"] == "test" and alert["title"] == "Test alert"


def test_front_ends_cant_raise_agent_alerts(setup):
    client, _, _ = setup
    r = client.post("/daemon/v1/phone/alerts", json={"source": "agent", "reason": "x"})
    assert r.status_code == 422


def test_agent_route_needs_a_custody_secret(setup):
    client, service, custody_auth = setup
    del client.headers["Authorization"]
    assert client.post("/host/v1/alerts", json={"reason": "hi"}).status_code == 403
    secret = custody_auth.mint("gaia")
    r = client.post(
        "/host/v1/alerts",
        json={"reason": "hi"},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert r.json() == {
        "raised": False,
        "message": "Agents may not alert the user: that setting is off.",
    }
    service.update_settings({"rules": {"agent": {"on": True}}})
    r = client.post(
        "/host/v1/alerts",
        json={"reason": "hi"},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert r.json()["raised"] is True
    assert service.store.alerts()[0]["rule_key"] == "agent:gaia"


def test_errors_carry_stable_codes(setup):
    client, _, _ = setup
    r = client.post("/daemon/v1/phone/contacts", json={"number": "911"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_number"
    r = client.put("/daemon/v1/phone/settings", json={"enabled": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_configured"
    r = client.post("/daemon/v1/phone/test")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "phone_off"


def test_bad_twilio_keys_are_not_a_401(setup, monkeypatch):
    client, _, _ = setup
    from gaia.phone import provider as provider_mod

    def reject(self, number):
        from gaia.phone.errors import PhoneError

        raise PhoneError(
            "bad_credentials", "Twilio rejected the Account SID or Auth Token."
        )

    monkeypatch.setattr(provider_mod.TwilioProvider, "check", reject)
    r = client.post(
        "/daemon/v1/phone/keys",
        json={"account_sid": "AC" + "1" * 32, "auth_token": "x", "from_number": ME},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_credentials"
