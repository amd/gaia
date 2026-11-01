# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The MCP handshake against a strict server, over real stdio and HTTP (#3322).

``tests/fixtures/toy_mcp_server.py`` refuses an invalid ``protocolVersion``
and any request before ``notifications/initialized``, so these tests fail on
a client that gets either wrong, which a mocked transport can't show.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from gaia.mcp.client.mcp_client import MCPClient
from gaia.mcp.client.transports.http import HTTPTransport
from gaia.mcp.client.transports.stdio import StdioTransport
from gaia.mcp.protocol import MCP_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS

TOY = Path(__file__).resolve().parents[3] / "fixtures" / "toy_mcp_server.py"


def _load_toy():
    spec = importlib.util.spec_from_file_location("_toy_mcp_server", TOY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


toy = _load_toy()


def _seen(log: Path) -> list:
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]


def _stdio_client(log: Path, *extra: str, env=None) -> MCPClient:
    transport = StdioTransport(
        sys.executable,
        args=["-u", str(TOY), "--log", str(log), *extra],
        env=env,
        timeout=20,
        name="toy",
    )
    return MCPClient("toy", transport)


@pytest.fixture
def http_toy(tmp_path):
    servers = []

    def start(**kwargs):
        server = toy.make_http_server(log_path=str(tmp_path / "http.jsonl"), **kwargs)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        host, port = server.server_address
        return f"http://{host}:{port}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _assert_handshake_shape(seen: list) -> None:
    init, initialized = seen[0], seen[1]
    assert init["method"] == "initialize"
    assert "id" in init
    params = init["params"]
    assert params["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert isinstance(params["capabilities"], dict)
    assert params["clientInfo"]["name"] and params["clientInfo"]["version"]
    assert initialized == {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert [m["method"] for m in seen].count("initialize") == 1


def test_the_advertised_version_is_a_date_revision_gaia_supports():
    assert MCP_PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS
    for version in SUPPORTED_PROTOCOL_VERSIONS:
        year, month, day = version.split("-")
        assert len(year) == 4 and len(month) == 2 and len(day) == 2


# ---------------------------------------------------------------------------
# stdio
# ---------------------------------------------------------------------------


def test_stdio_handshake_then_tools_work_against_a_strict_server(tmp_path):
    log = tmp_path / "stdio.jsonl"
    client = _stdio_client(log)
    try:
        assert client.connect(), client.last_error
        assert client.protocol_version == MCP_PROTOCOL_VERSION
        assert [t.name for t in client.list_tools()] == ["echo"]
        reply = client.call_tool("echo", {"text": "hi"})
        assert reply["content"][0]["text"] == "hi"
    finally:
        client.disconnect()

    seen = _seen(log)
    _assert_handshake_shape(seen)
    assert [m["method"] for m in seen[2:]] == ["tools/list", "tools/call"]


@pytest.mark.parametrize(
    "answer",
    ["1.0.0", "2026-07-28", "<omit>", "2025-11-25 "],
    ids=["semver", "future-revision", "missing", "trailing-space"],
)
def test_stdio_server_answering_an_unsupported_version_is_refused(tmp_path, answer):
    log = tmp_path / "stdio.jsonl"
    client = _stdio_client(log, "--answer-version", answer)

    assert client.connect() is False
    assert "protocol version" in client.last_error
    assert not client.is_connected()
    assert "notifications/initialized" not in [m.get("method") for m in _seen(log)]


def test_stdio_server_may_settle_on_an_older_supported_version(tmp_path):
    client = _stdio_client(tmp_path / "stdio.jsonl", "--answer-version", "2024-11-05")
    try:
        assert client.connect(), client.last_error
        assert client.protocol_version == "2024-11-05"
        assert [t.name for t in client.list_tools()] == ["echo"]
    finally:
        client.disconnect()


def test_a_version_echoing_a_secret_is_redacted_from_the_error(tmp_path):
    secret = "sk-3322-handshake-SENTINEL"
    client = _stdio_client(
        tmp_path / "stdio.jsonl",
        "--answer-version",
        "env:TOY_TOKEN",
        env={"TOY_TOKEN": secret},
    )

    assert client.connect() is False
    assert secret not in client.last_error


def test_stdio_notification_has_no_id_and_gets_no_reply(tmp_path):
    log = tmp_path / "stdio.jsonl"
    transport = StdioTransport(
        sys.executable, args=["-u", str(TOY), "--log", str(log)], timeout=20
    )
    assert transport.connect()
    try:
        transport.send_notification("notifications/initialized")
        transport.send_notification("notifications/progress", {"progress": 1})
        # The next request still pairs with its own reply.
        assert transport.send_request("ping", {})["result"] == {}
    finally:
        transport.disconnect()

    seen = _seen(log)
    assert seen[0] == {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert seen[1] == {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {"progress": 1},
    }


def test_stdio_notification_before_connect_raises():
    transport = StdioTransport(sys.executable, args=[str(TOY)])
    with pytest.raises(RuntimeError, match="not connected"):
        transport.send_notification("notifications/initialized")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@pytest.mark.allow_network
def test_http_handshake_then_tools_work_against_a_strict_server(tmp_path, http_toy):
    client = MCPClient("toy-http", HTTPTransport(http_toy(), timeout=10))
    try:
        assert client.connect(), client.last_error
        assert [t.name for t in client.list_tools()] == ["echo"]
    finally:
        client.disconnect()

    seen = _seen(tmp_path / "http.jsonl")
    _assert_handshake_shape(seen)
    assert seen[2]["method"] == "tools/list"


@pytest.mark.allow_network
def test_http_transport_connect_sends_nothing(tmp_path, http_toy):
    transport = HTTPTransport(http_toy(), timeout=10)
    assert transport.connect() is True
    assert _seen(tmp_path / "http.jsonl") == []


@pytest.mark.allow_network
def test_http_unsupported_version_is_refused(http_toy):
    client = MCPClient("toy-http", HTTPTransport(http_toy(answer_version="1.0.0")))

    assert client.connect() is False
    assert "'1.0.0'" in client.last_error
    assert not client.is_connected()


@pytest.mark.allow_network
def test_http_unreachable_server_fails_connect_with_the_url():
    client = MCPClient("gone", HTTPTransport("http://127.0.0.1:9", timeout=2))

    assert client.connect() is False
    assert "HTTP request to http://127.0.0.1:9 failed" in client.last_error


@pytest.mark.allow_network
def test_http_notification_rejected_with_a_jsonrpc_error_raises(http_toy):
    body = json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": "no"}})
    transport = HTTPTransport(http_toy(notification_body=body), timeout=10)

    with pytest.raises(RuntimeError, match="rejected 'notifications/initialized': no"):
        transport.send_notification("notifications/initialized")


@pytest.mark.allow_network
def test_http_notification_tolerates_a_non_json_202_body(http_toy):
    transport = HTTPTransport(http_toy(notification_body="Accepted"), timeout=10)
    transport.send_notification("notifications/initialized")


# ---------------------------------------------------------------------------
# Client ordering, with the transport mocked
# ---------------------------------------------------------------------------


def _mock_transport(result):
    transport = Mock()
    transport.connect.return_value = True
    transport.send_request.return_value = {"jsonrpc": "2.0", "id": 0, **result}
    return transport


def test_initialized_is_sent_once_and_only_after_a_good_initialize():
    transport = _mock_transport({"result": {"protocolVersion": MCP_PROTOCOL_VERSION}})

    assert MCPClient("m", transport).connect()

    assert [c.args[0] for c in transport.method_calls if c[0] != "connect"] == [
        "initialize",
        "notifications/initialized",
    ]
    transport.send_notification.assert_called_once_with("notifications/initialized")


def test_initialized_is_not_sent_when_initialize_errors():
    transport = _mock_transport({"error": {"code": -32602, "message": "bad"}})

    assert MCPClient("m", transport).connect() is False
    transport.send_notification.assert_not_called()
    transport.disconnect.assert_called_once()


@pytest.mark.parametrize("version", [["2025-11-25"], {"v": 1}, 20251125])
def test_a_non_string_version_is_refused_not_crashed(version):
    transport = _mock_transport({"result": {"protocolVersion": version}})
    client = MCPClient("m", transport)

    assert client.connect() is False
    assert "protocol version" in client.last_error
    transport.send_notification.assert_not_called()


def test_a_failed_initialized_notification_fails_connect():
    transport = _mock_transport({"result": {"protocolVersion": MCP_PROTOCOL_VERSION}})
    transport.send_notification.side_effect = RuntimeError("pipe closed")
    client = MCPClient("m", transport)

    assert client.connect() is False
    assert "pipe closed" in client.last_error
    transport.disconnect.assert_called_once()
