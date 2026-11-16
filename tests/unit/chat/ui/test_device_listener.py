# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The listener paired devices reach, over a real TLS socket on 127.0.0.1.

The listener runs in its own event loop on a thread, as it shares the
backend's loop in production; the tests talk to it through a client that
checks the certificate pin before it sends anything.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import socket
import ssl
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from gaia.config import GaiaConfig
from gaia.ui.devices import listener as listener_module
from gaia.ui.devices.access import DeviceAccess
from gaia.ui.devices.listener import DeviceGate, ListenerError
from gaia.ui.devices.store import token_hash
from gaia.ui.routers import sessions as sessions_router
from gaia.ui.security import ListenerScopeMiddleware
from gaia.ui.server import create_app

from .pinned_client import PinMismatch, PinnedConnection, call

pytestmark = pytest.mark.allow_network  # TLS on 127.0.0.1 only

PC = ("127.0.0.1", 50000)


class _Running:
    def poll(self):
        return None


@pytest.fixture
def served(tmp_path):
    GaiaConfig.save_key("device_pairing", True)
    app = create_app(db_path=":memory:")
    access = DeviceAccess(app, folder=tmp_path / "devices", host="127.0.0.1", port=0)
    app.state.devices = access
    loop = asyncio.new_event_loop()
    thread = threading.Thread(
        target=loop.run_forever, name="listener-test", daemon=True
    )
    thread.start()

    def run(coroutine, timeout=30):
        return asyncio.run_coroutine_threadsafe(coroutine, loop).result(timeout)

    run(access.start())
    offer = access.open_pairing()
    try:
        yield SimpleNamespace(
            app=app,
            access=access,
            run=run,
            port=access.status()["port"],
            pin=offer["pin"],
            offer=offer,
        )
    finally:
        run(access.shutdown())
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def _pair(served, allow=True, invite=None):
    invite = invite or served.access.open_pairing()["invite"]
    paired = served.access.redeem(invite, "Pixel 9", "android", "192.168.1.20")
    if allow:
        served.access.allow(paired["device_id"])
    return paired


def _tunnel_token(app) -> str:
    tunnel = app.state.tunnel
    tunnel._url, tunnel._token, tunnel._process = (
        "https://fake.ngrok-free.app",
        str(uuid.uuid4()),
        _Running(),
    )
    return tunnel._token


def _served_count(served, expected):
    """uvicorn counts a request after writing its response, on its own thread."""
    deadline = time.monotonic() + 5
    while served.access.requests_served < expected and time.monotonic() < deadline:
        time.sleep(0.01)
    return served.access.requests_served


def test_client_checks_pin_before_sending(served):
    """The pin is the served leaf's SHA-256; a wrong one sends no request."""
    status, _, _ = call(served.port, served.pin, "GET", "/api/sessions")
    assert status == 401
    assert _served_count(served, 1) == 1

    with pytest.raises(PinMismatch):
        call(served.port, "0" * 64, "GET", "/api/sessions")
    call(served.port, served.pin, "GET", "/api/sessions")
    # Had the mismatched connection sent its request, this would be 3.
    assert _served_count(served, 2) == 2


def test_tls_min_version_1_2(served):
    for version in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
        connection = PinnedConnection(
            "127.0.0.1", served.port, served.pin, max_tls=version
        )
        connection.request("GET", "/api/health")
        assert connection.getresponse().status == 401
        connection.close()

    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    old.check_hostname, old.verify_mode = False, ssl.CERT_NONE
    old.set_ciphers("DEFAULT:@SECLEVEL=0")
    old.minimum_version = old.maximum_version = ssl.TLSVersion.TLSv1_1
    with socket.create_connection(("127.0.0.1", served.port), timeout=5) as raw:
        with pytest.raises(ssl.SSLError) as refused:
            old.wrap_socket(raw)
    # The ClientHello went out and the listener hung up or sent the alert; a
    # client unable to offer TLS 1.1 would have failed before connecting.
    assert refused.value.reason in (
        "TLSV1_ALERT_PROTOCOL_VERSION",
        "UNEXPECTED_EOF_WHILE_READING",
    )


def _gate_scope(**overrides):
    return {
        "type": "http",
        "method": "GET",
        "path": "/api/sessions",
        "headers": [],
        "client": ("192.168.1.20", 50000),
        "server": ("192.168.1.10", 4243),
        "scheme": "https",
        **overrides,
    }


async def _through_gate(app, scope, allow_loopback=False):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    gate = DeviceGate(app, app.state.devices, allow_loopback=allow_loopback)
    await ListenerScopeMiddleware(gate)(scope, receive, send)
    return sent


@pytest.mark.parametrize(
    "case",
    [
        "no-token",
        "tunnel-token",
        "tunnel-cookie",
        "query-token",
        "health-without-token",
        "web-ui",
        "web-ui-with-token",
        "docs",
        "docs-with-token",
        "openapi",
        "openapi-with-token",
        "pending-device",
        "body-over-the-cap",
        "public-peer",
        "relay-from-loopback",
        "websocket-without-token",
        "listener-stack-without-the-gate",
        "device-token-over-plain-http",
    ],
)
def test_listener_refuses(served, case):
    device = _pair(served)

    def get(path, **kwargs):
        return call(served.port, served.pin, "GET", path, **kwargs)

    if case == "no-token":
        assert get("/api/sessions")[0] == 401
    elif case == "tunnel-token":
        assert get("/api/sessions", token=_tunnel_token(served.app))[0] == 401
    elif case == "tunnel-cookie":
        cookie = f"gaia_tunnel_token={_tunnel_token(served.app)}"
        assert get("/api/sessions", headers={"Cookie": cookie})[0] == 401
    elif case == "query-token":
        assert get(f"/api/sessions?token={_tunnel_token(served.app)}")[0] == 401
    elif case == "health-without-token":
        assert get("/api/health")[0] == 401
    elif case.startswith(("web-ui", "docs", "openapi")):
        path = {"web-ui": "/", "docs": "/docs", "openapi": "/openapi.json"}[
            case.replace("-with-token", "")
        ]
        token = device["token"] if case.endswith("with-token") else None
        status, _, body = get(path, token=token)
        assert status == 404 and b"openapi" not in body.lower()
    elif case == "pending-device":
        waiting = _pair(served, allow=False)
        status, _, body = get("/api/sessions", token=waiting["token"])
        assert status == 403 and b"device_pending" in body
    elif case == "body-over-the-cap":
        status, _, _ = call(
            served.port,
            served.pin,
            "POST",
            "/api/sessions",
            token=device["token"],
            body={"title": "x" * (9 * 1024 * 1024)},
        )
        assert status == 413
    elif case in ("public-peer", "relay-from-loopback"):
        peer = ("203.0.113.7", 443) if case == "public-peer" else ("127.0.0.1", 50000)
        headers = [(b"authorization", f"Bearer {device['token']}".encode())]
        sent = served.run(
            _through_gate(served.app, _gate_scope(client=peer, headers=headers))
        )
        assert sent[0]["status"] == 403
    elif case == "websocket-without-token":
        sent = served.run(
            _through_gate(
                served.app, _gate_scope(type="websocket", scheme="wss", method=None)
            )
        )
        assert sent == [{"type": "websocket.close", "code": 1008}]
    elif case == "listener-stack-without-the-gate":
        misbuilt = TestClient(ListenerScopeMiddleware(served.app), client=PC)
        for path in ("/", "/docs", "/openapi.json", "/api/sessions"):
            assert misbuilt.get(path).status_code == 401, path
    elif case == "device-token-over-plain-http":
        local = TestClient(served.app, client=PC)
        resp = local.get(
            "/api/sessions", headers={"Authorization": f"Bearer {device['token']}"}
        )
        assert resp.status_code == 401 and "plain http" in resp.json()["detail"]


def _pc(served, method, path, **kwargs):
    """The PC's own call to the main app, on the listener's loop."""

    async def go():
        transport = httpx.ASGITransport(app=served.app, client=PC)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:4200"
        ) as client:
            return await client.request(
                method, path, headers={"X-Gaia-UI": "1"}, **kwargs
            )

    return served.run(go())


def _redeem(served, invite, pin=None):
    status, headers, body = call(
        served.port,
        pin or served.pin,
        "POST",
        "/api/devices/pair",
        body={
            "invitation": invite,
            "device_name": "Test \u202ephone",
            "platform": "ios",
        },
    )
    return status, headers, json.loads(body)


def test_pair_allow_use_and_unpair(served):
    GaiaConfig.save_key("device_pairing", True)
    events = served.run(sessions_router._system_emitter.subscribe())
    assert _pc(served, "POST", "/api/devices/listener").json()["listening"] is True
    offer = _pc(served, "POST", "/api/devices/pairing").json()
    assert offer["origins"] == [f"https://127.0.0.1:{served.port}"]
    assert offer["pin"] == served.pin

    status, headers, paired = _redeem(served, offer["invite"])
    assert status == 200 and headers["cache-control"] == "no-store"
    token = paired["token"]
    assert paired["status"] == "pending" and paired["pc_id"] == offer["pc_id"]
    event = served.run(asyncio.wait_for(events.get(), 5))
    assert event["type"] == "device_paired" and event["actions"] == ["review", "deny"]
    assert event["device"]["id"] == paired["device_id"]

    assert call(served.port, served.pin, "GET", "/api/sessions", token=token)[0] == 403
    listing = _pc(served, "GET", "/api/devices")
    status_now = _pc(served, "GET", "/api/devices/status")
    for resp in (listing, status_now):
        assert resp.headers["cache-control"] == "no-store"
        for secret in (token, token[:14], token_hash(token), offer["invite"]):
            assert secret not in resp.text
    (device,) = listing.json()["devices"]
    assert device["name"] == "Test phone" and device["status"] == "pending"
    assert device["expires_at"] is not None
    assert status_now.json()["pairing_open"] is False

    allowed = _pc(served, "POST", f"/api/devices/{paired['device_id']}/allow")
    assert allowed.json()["status"] == "allowed"
    assert call(served.port, served.pin, "GET", "/api/sessions", token=token)[0] == 200

    gone = call(served.port, served.pin, "DELETE", "/api/devices/me", token=token)
    assert gone[0] == 204
    assert call(served.port, served.pin, "GET", "/api/sessions", token=token)[0] == 401
    assert _pc(served, "GET", "/api/devices").json()["devices"] == []


def test_pair_window_closed_gives_uniform_refusal(served):
    GaiaConfig.save_key("device_pairing", True)
    events = served.run(sessions_router._system_emitter.subscribe())
    _pc(served, "DELETE", "/api/devices/pairing")
    refusals = [_redeem(served, "no-window-is-open")]
    invite = _pc(served, "POST", "/api/devices/pairing").json()["invite"]
    refusals.append(_redeem(served, "not-the-code"))
    assert _redeem(served, invite)[0] == 200
    refusals.append(_redeem(served, invite))
    assert {status for status, _, _ in refusals} == {403}
    assert len({json.dumps(body) for _, _, body in refusals}) == 1

    reasons = _pc(served, "GET", "/api/devices/status").json()["failed_redemptions"]
    assert [r["reason"] for r in reasons] == ["closed", "wrong", "used"]
    assert {r["address"] for r in reasons} == {"127.0.0.1"}

    served.run(asyncio.wait_for(events.get(), 5))  # the pairing itself
    for _ in range(2):
        assert _redeem(served, "guess")[0] == 403
    alert = served.run(asyncio.wait_for(events.get(), 5))
    assert alert["type"] == "security_alert" and alert["kind"] == "pairing_locked_out"
    status, headers, _ = _redeem(served, "guess")
    assert status == 429 and int(headers["retry-after"]) > 0


def test_reset_rotates_key_and_revokes_all(served):
    GaiaConfig.save_key("device_pairing", True)
    old_pin = served.pin
    paired = _pair(served)
    pc_id = served.access.open_pairing()["pc_id"]

    _pc(served, "DELETE", "/api/devices/listener")
    if os.name != "nt":  # mode bits only widen access on POSIX
        (served.access.folder / "listener.key").chmod(0o644)
        refused = _pc(served, "POST", "/api/devices/listener")
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "unsafe_folder"
        status = _pc(served, "GET", "/api/devices/status").json()
        assert status["error_code"] == "unsafe_folder"

    assert _pc(served, "POST", "/api/devices/pairing/reset").status_code == 200
    now_on = _pc(served, "POST", "/api/devices/listener").json()
    assert now_on["listening"] is True and now_on["error_code"] is None
    offer = _pc(served, "POST", "/api/devices/pairing").json()
    assert offer["pin"] != old_pin and offer["pc_id"] == pc_id
    with pytest.raises(PinMismatch):
        call(now_on["port"], old_pin, "GET", "/api/sessions")
    status, _, _ = call(
        now_on["port"], offer["pin"], "GET", "/api/sessions", token=paired["token"]
    )
    assert status == 401
    assert _pc(served, "GET", "/api/devices").json()["devices"] == []


def test_revoke_closes_inflight_stream(served):
    device = _pair(served)
    stream = PinnedConnection("127.0.0.1", served.port, served.pin, timeout=10)
    stream.request(
        "GET",
        "/api/sessions/events",
        headers={"Authorization": f"Bearer {device['token']}"},
    )
    response = stream.getresponse()
    assert response.status == 200
    assert response.readline().startswith(b": keepalive")

    started = time.monotonic()
    assert served.access.revoke(device["device_id"])
    response.read()
    assert time.monotonic() - started < 3
    stream.close()

    status, _, _ = call(
        served.port, served.pin, "GET", "/api/sessions", token=device["token"]
    )
    assert status == 401


def test_revoke_between_auth_and_serving_refuses(served, monkeypatch):
    device = _pair(served)
    authenticate = served.access.authenticate

    def then_revoked(token):
        found = authenticate(token)
        served.access.revoke(found.id)
        return found

    monkeypatch.setattr(served.access, "authenticate", then_revoked)
    status, _, _ = call(
        served.port, served.pin, "GET", "/api/sessions", token=device["token"]
    )
    assert status == 401


def test_port_in_use_fails_loudly(tmp_path):
    GaiaConfig.save_key("device_pairing", True)
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        app = create_app(db_path=":memory:")
        access = DeviceAccess(
            app, folder=tmp_path / "devices", host="127.0.0.1", port=port
        )
        with pytest.raises(ListenerError) as failed:
            asyncio.run(access.start())
    assert failed.value.code == "port_in_use" and str(port) in failed.value.detail
    assert access.status()["error_code"] == "port_in_use"
    assert access.status()["listening"] is False


def test_port_held_on_one_address_fails_loudly(tmp_path):
    # macOS lets a wildcard bind share a port another program holds on one
    # address; phones dialing that address would reach it and see a pin mismatch.
    GaiaConfig.save_key("device_pairing", True)
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        app = create_app(db_path=":memory:")
        access = DeviceAccess(
            app, folder=tmp_path / "devices", host="0.0.0.0", port=port
        )

        async def attempt():
            try:
                await access.start()
            finally:
                await access.shutdown()

        with pytest.raises(ListenerError) as failed:
            asyncio.run(attempt())
    assert failed.value.code == "port_in_use" and str(port) in failed.value.detail
    assert access.status()["listening"] is False


def test_probe_skips_an_address_the_pc_no_longer_has(monkeypatch):
    # A VPN utun, link-local address or dropped interface answers the
    # test-bind with EADDRNOTAVAIL: the address is gone, not the port held.
    monkeypatch.setattr(
        listener_module,
        "local_ipv4_addresses",
        lambda: ["203.0.113.7", "127.0.0.1"],
    )
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    listener_module._refuse_shared_port(port)


def test_probe_names_an_unexpected_errno(monkeypatch):
    class Broken:
        def setsockopt(self, *args):
            pass

        def bind(self, address):
            raise OSError(errno.EPERM, "Operation not permitted")

        def close(self):
            pass

    monkeypatch.setattr(listener_module, "local_ipv4_addresses", lambda: ["10.0.0.1"])
    monkeypatch.setattr(listener_module.socket, "socket", lambda *a: Broken())
    with pytest.raises(OSError, match="EPERM") as failed:
        listener_module._refuse_shared_port(5000)
    assert failed.value.errno == errno.EPERM


@pytest.mark.parametrize(
    "os_name,platform,expected,forbidden",
    [
        ("posix", "darwin", "SO_REUSEADDR", "SO_EXCLUSIVEADDRUSE"),
        ("posix", "linux", "SO_REUSEADDR", "SO_EXCLUSIVEADDRUSE"),
        ("nt", "win32", "SO_EXCLUSIVEADDRUSE", "SO_REUSEADDR"),
    ],
)
def test_bind_socket_options_per_platform(
    monkeypatch, os_name, platform, expected, forbidden
):
    options = []

    class Recorder:
        def __init__(self, *args):
            pass

        def setsockopt(self, level, option, value):
            options.append(option)

        def bind(self, address):
            pass

        def getsockname(self):
            return ("0.0.0.0", 1)

        def listen(self, backlog):
            pass

        def setblocking(self, flag):
            pass

        def close(self):
            pass

    monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", -5, raising=False)
    monkeypatch.setattr(listener_module.socket, "socket", Recorder)
    monkeypatch.setattr(listener_module, "_refuse_shared_port", lambda port: None)
    monkeypatch.setattr(listener_module.os, "name", os_name)
    monkeypatch.setattr(listener_module.sys, "platform", platform)
    listener_module._bind("0.0.0.0", 1)
    assert getattr(socket, expected) in options
    assert getattr(socket, forbidden) not in options


def test_listener_restarts_on_its_own_port(tmp_path):
    # Connections the listener closed linger in TIME_WAIT; a restart must not
    # read them as another program.
    GaiaConfig.save_key("device_pairing", True)
    app = create_app(db_path=":memory:")
    access = DeviceAccess(app, folder=tmp_path / "devices", host="0.0.0.0", port=0)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE

    async def restart():
        await access.start()
        port = access.status()["port"]
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", port, ssl=client
            )
            writer.write(
                b"GET /api/health HTTP/1.1\r\nHost: pc\r\nConnection: close\r\n\r\n"
            )
            assert (await reader.read()).startswith(b"HTTP/1.1 ")
            writer.close()
            await access.stop()
            restarted = DeviceAccess(
                app, folder=tmp_path / "devices", host="0.0.0.0", port=port
            )
            await restarted.start()
            await restarted.shutdown()
        finally:
            await access.shutdown()

    asyncio.run(restart())


def test_no_token_or_invite_in_logs(served, caplog):
    caplog.set_level(logging.DEBUG)
    invite = served.access.open_pairing()["invite"]
    device = _pair(served, invite=invite)
    call(served.port, served.pin, "GET", "/api/sessions", token=device["token"])
    call(served.port, served.pin, "GET", "/api/sessions", token=device["token"] + "x")
    call(served.port, served.pin, "GET", f"/api/sessions?token={device['token']}")
    served.access.revoke(device["device_id"])

    logged = caplog.text
    secret = device["token"].removeprefix("gaiad_")
    for value in (device["token"], secret[:8], invite, invite[:8]):
        assert value not in logged
    assert device["device_id"] in logged
