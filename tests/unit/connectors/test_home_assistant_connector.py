# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Home Assistant connector, end to end over real HTTP against a fake HA.

No transport is mocked: every request goes through GAIA's HTTP transport to
``tests/fixtures/home_assistant/fake_ha.py`` on loopback, so the outgoing
call's shape (path, ``Authorization`` header) is asserted, not assumed.
"""

from __future__ import annotations

import asyncio
import json
import socket

import pytest

import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors import home_assistant as ha
from gaia.connectors._keyring import keyring
from gaia.connectors.activations import is_agent_active
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.mcp_server import (
    _read_mcp_servers_json,
    rederive_catalog_entries,
    validate_base_url,
)
from gaia.connectors.registry import REGISTRY
from gaia.connectors.store import SERVICE_NAME
from gaia.mcp.client.mcp_client import MCPClient
from gaia.mcp.client.transports import http as http_transport
from tests.fixtures.home_assistant.fake_ha import FakeHomeAssistant

pytestmark = pytest.mark.allow_network

TOKEN = "test-token-abc123"
SPEC = REGISTRY.get("mcp-home-assistant")


@pytest.fixture
def fake():
    server = FakeHomeAssistant(TOKEN)
    base = server.start()
    server.base = base
    yield server
    server.stop()


def _connect(base: str, token: str = TOKEN):
    return asyncio.run(ha.connect(base, token))


# --- the catalog pins the safe endpoint -------------------------------------


def test_spec_pins_the_assist_endpoint_and_person_only_writes():
    assert SPEC.mcp_transport == "http"
    assert SPEC.mcp_url_path == "/api/mcp/assist"
    assert SPEC.mcp_writes_need_person is True
    assert SPEC.mcp_headers == (("Authorization", "HA_TOKEN", "Bearer {}"),)


# --- address rules ------------------------------------------------------------


@pytest.mark.parametrize(
    "url, fragment",
    [
        ("homeassistant.local:8123", "not a web address"),
        ("ftp://homeassistant.local", "not a web address"),
        ("http://localhost:8123/api/mcp", "without a path"),
        ("http://localhost:8123/?x=1", "without a path"),
        ("http://user:pw@localhost:8123", "user name and password"),
        ("http://8.8.8.8:8123", "public address"),
    ],
)
def test_bad_addresses_are_refused_with_the_fix(url, fragment):
    with pytest.raises(ConnectorsError, match=fragment):
        validate_base_url(url)


def test_good_addresses_normalize_to_a_bare_origin():
    assert validate_base_url(" http://LOCALHOST:8123/ ") == "http://localhost:8123"
    assert validate_base_url("http://127.0.0.1:8123") == "http://127.0.0.1:8123"
    # https is not resolved: the token is protected in transit.
    assert validate_base_url("https://ha.example.com") == "https://ha.example.com"


# --- connect -----------------------------------------------------------------


def test_connect_saves_a_keyring_reference_never_the_token(fake):
    result = _connect(fake.base)

    assert result["connected"] is True and result["tool_count"] == 4
    entry = _read_mcp_servers_json()["mcp-home-assistant"]
    assert entry["url"] == fake.base + "/api/mcp/assist"
    assert entry["headers"]["Authorization"] == {
        "$keyring": f"{SERVICE_NAME}:mcp-home-assistant:HA_TOKEN",
        "format": "Bearer {}",
    }
    assert entry["writes_need_person"] is True and entry["private_http_only"] is True
    assert TOKEN not in json.dumps(entry)
    assert keyring.get_password(SERVICE_NAME, "mcp-home-assistant:HA_TOKEN") == TOKEN
    assert is_agent_active("mcp-home-assistant", ha.FLAGSHIP_AGENT_ID)
    # The token only ever went to the pinned endpoint, as a bearer header.
    assert {r["path"] for r in fake.requests} == {"/api/mcp/assist"}
    assert all(
        r["headers"]["Authorization"] == f"Bearer {TOKEN}" for r in fake.requests
    )


def test_a_rejected_token_saves_nothing(fake):
    with pytest.raises(ConnectorsError, match="rejected the access token"):
        _connect(fake.base, "wrong")
    assert "mcp-home-assistant" not in _read_mcp_servers_json()
    assert keyring.get_password(SERVICE_NAME, "mcp-home-assistant:HA_TOKEN") is None
    assert ha.status()["connected"] is False


def test_missing_mcp_integration_names_the_integration():
    server = FakeHomeAssistant(TOKEN, mcp_enabled=False)
    base = server.start()
    try:
        with pytest.raises(ConnectorsError, match="Model Context Protocol Server"):
            _connect(base)
    finally:
        server.stop()


def test_nothing_listening_says_check_the_address():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(ConnectorsError, match="isn't answering"):
        _connect(f"http://127.0.0.1:{port}")


def test_empty_token_is_refused_before_any_request(fake):
    with pytest.raises(ConnectorsError, match="long-lived access token"):
        _connect(fake.base, "  ")
    assert fake.requests == []


def test_a_redirect_is_refused_and_the_token_never_follows_it(fake):
    sink = FakeHomeAssistant("other")
    sink_base = sink.start()
    redirector = FakeHomeAssistant(TOKEN, redirect_to=sink_base)
    base = redirector.start()
    try:
        with pytest.raises(ConnectorsError, match="redirect"):
            _connect(base)
        assert sink.requests == []
    finally:
        redirector.stop()
        sink.stop()


def test_plain_http_refuses_a_public_peer_at_the_socket(fake, monkeypatch):
    """The private-address check runs on the connected socket, not a lookup."""
    real = http_transport.is_private_address
    monkeypatch.setattr(http_transport, "is_private_address", lambda a: False)
    try:
        with pytest.raises(ConnectorsError, match="public address"):
            asyncio.run(ha.connect(fake.base, TOKEN))
    finally:
        monkeypatch.setattr(http_transport, "is_private_address", real)
    assert fake.requests == []


# --- after connecting --------------------------------------------------------


def test_check_reprobes_with_the_saved_token(fake):
    _connect(fake.base)
    fake.requests.clear()
    assert asyncio.run(ha.check())["ok"] is True
    assert fake.requests and all(
        r["headers"]["Authorization"] == f"Bearer {TOKEN}" for r in fake.requests
    )
    fake.token = "rotated-in-ha"
    result = asyncio.run(ha.check())
    assert result["ok"] is False and "rejected the access token" in result["detail"]


def test_rederive_keeps_the_remote_entry(fake):
    _connect(fake.base)
    servers = _read_mcp_servers_json()
    assert (
        rederive_catalog_entries(servers)["mcp-home-assistant"]
        == servers["mcp-home-assistant"]
    )
    stale = {
        **servers["mcp-home-assistant"],
        "url": "http://elsewhere/api/mcp",
        "command": "x",
    }
    rebuilt = rederive_catalog_entries({"mcp-home-assistant": stale})[
        "mcp-home-assistant"
    ]
    assert rebuilt["url"] == fake.base + "/api/mcp/assist" and "command" not in rebuilt


def test_disconnect_removes_entry_keyring_slot_and_activation(fake):
    _connect(fake.base)
    result = asyncio.run(ha.disconnect())
    assert "still works until you delete it" in result["detail"]
    assert "mcp-home-assistant" not in _read_mcp_servers_json()
    assert keyring.get_password(SERVICE_NAME, "mcp-home-assistant:HA_TOKEN") is None
    assert not is_agent_active("mcp-home-assistant", ha.FLAGSHIP_AGENT_ID)


# --- the MCP client from the saved entry ---------------------------------------


def test_client_from_saved_entry_lists_tools_and_flags_person_only(fake):
    _connect(fake.base)
    entry = _read_mcp_servers_json()["mcp-home-assistant"]
    client = MCPClient.from_config("mcp-home-assistant", entry)
    assert client.writes_need_person is True
    assert client.tool_note == SPEC.mcp_tool_note and client.tool_note
    assert client.connect(), client.last_error
    try:
        tools = {
            t.name: t.to_gaia_format(client.prefix, client.name)
            for t in client.list_tools()
        }
    finally:
        client.disconnect()
    live = tools["homeassistant__GetLiveContext"]
    turn_on = tools["intent__HassTurnOn"]
    assert live["requires_confirmation"] is False
    assert turn_on["requires_confirmation"] is True
    assert turn_on["display_name"] == "Turn on (home_assistant)"
    assert turn_on["parameters"]["domain"]["items"]["enum"] == [
        "light",
        "fan",
        "switch",
        "cover",
    ]


def test_a_missing_keyring_secret_fails_closed(fake):
    _connect(fake.base)
    keyring.delete_password(SERVICE_NAME, "mcp-home-assistant:HA_TOKEN")
    entry = _read_mcp_servers_json()["mcp-home-assistant"]
    with pytest.raises(Exception, match="mcp-home-assistant"):
        MCPClient.from_config("mcp-home-assistant", entry)


def test_http_entry_with_a_command_is_rejected():
    with pytest.raises(ValueError, match="both 'url' and 'command'"):
        MCPClient.from_config(
            "x", {"type": "http", "url": "http://127.0.0.1:1/mcp", "command": "npx"}
        )


def test_transport_redacts_the_token_from_replies():
    transport = http_transport.HTTPTransport(
        "http://127.0.0.1:1/mcp", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    cleaned = transport.redact_value(
        {"error": f"bad header Bearer {TOKEN}", "echo": [TOKEN]}
    )
    assert TOKEN not in json.dumps(cleaned)


def test_manager_loads_http_entries(fake, tmp_path):
    from gaia.mcp.client.config import MCPConfig
    from gaia.mcp.client.mcp_client_manager import MCPClientManager

    _connect(fake.base)
    config_path = tmp_path / "servers.json"
    config_path.write_text(
        json.dumps({"mcpServers": _read_mcp_servers_json()}), encoding="utf-8"
    )
    manager = MCPClientManager(config=MCPConfig(str(config_path)))
    try:
        manager.load_from_config()
        client = manager.get_client("mcp-home-assistant")
        assert client is not None and len(client.list_tools()) == 4
    finally:
        manager.disconnect_all()


def test_socket_level_check_stops_plain_http_to_a_public_peer(fake, monkeypatch):
    """The transport checks the connected peer itself, after any DNS answer."""
    transport = http_transport.HTTPTransport(
        fake.base + "/api/mcp/assist",
        headers={"Authorization": f"Bearer {TOKEN}"},
        private_http_only=True,
    )
    monkeypatch.setattr(http_transport, "is_private_address", lambda a: False)
    with pytest.raises(http_transport.MCPHTTPError) as raised:
        transport.send_request("tools/list")
    assert raised.value.refused is True
    assert fake.requests == []


@pytest.mark.parametrize(
    "address, private",
    [
        ("127.0.0.1", True),
        ("192.168.1.20", True),
        ("10.0.0.5", True),
        ("fe80::1%eth0", True),
        ("::ffff:192.168.1.2", True),
        ("100.101.102.103", True),
        ("8.8.8.8", False),
        ("2001:4860:4860::8888", False),
    ],
)
def test_private_address_rule(address, private):
    assert http_transport.is_private_address(address) is private


def test_a_keyring_that_keeps_nothing_fails_the_connect(fake, monkeypatch):
    monkeypatch.setattr(keyring, "set_password", lambda *a: None)
    with pytest.raises(ConnectorsError, match="keyring didn't keep"):
        _connect(fake.base)
    assert "mcp-home-assistant" not in _read_mcp_servers_json()
