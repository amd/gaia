# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""HTTPTransport against the official MCP SDK's streamable-HTTP server (#6399).

The toy server in ``test_mcp_handshake.py`` accepts any POST, so it can't show
what a spec-compliant server requires: the ``Accept`` header, the
``Mcp-Session-Id`` it hands out, the ``MCP-Protocol-Version`` header, and
answers sent as SSE streams. These tests run the real server in a thread.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

pytest.importorskip("mcp")
uvicorn = pytest.importorskip("uvicorn")

from mcp.server import MCPServer  # noqa: E402

from gaia.mcp.client.mcp_client import MCPClient  # noqa: E402
from gaia.mcp.client.transports.http import HTTPTransport  # noqa: E402

pytestmark = pytest.mark.allow_network

# name -> streamable_http_app() options. "sse" is the SDK default.
MODES = {
    "sse": {},
    "json": {"json_response": True},
    "stateless": {"stateless_http": True},
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sdk_server():
    servers = []

    def start(**options) -> str:
        mcp = MCPServer(name="sdk-test")

        @mcp.tool()
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        port = _free_port()
        config = uvicorn.Config(
            mcp.streamable_http_app(**options),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                raise RuntimeError("MCP SDK server did not start")
            time.sleep(0.05)
        servers.append((server, thread))
        return f"http://127.0.0.1:{port}/mcp"

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("mode", MODES)
def test_handshake_list_and_call_against_the_sdk_server(sdk_server, mode):
    client = MCPClient("sdk", HTTPTransport(sdk_server(**MODES[mode]), timeout=10))
    try:
        assert client.connect(), client.last_error
        assert [t.name for t in client.list_tools()] == ["add"]

        result = client.call_tool("add", {"a": 2, "b": 3})
        assert "error" not in result, result
        assert result["content"][0]["text"] == "5"
    finally:
        client.disconnect()


def test_disconnect_ends_the_session_on_the_server(sdk_server):
    url = sdk_server()
    transport = HTTPTransport(url, timeout=10)
    client = MCPClient("sdk", transport)
    assert client.connect(), client.last_error
    session_id = transport.session_id
    assert session_id

    client.disconnect()

    # The server forgets a session once the client DELETEs it.
    probe = urllib.request.Request(
        url,
        data=b'{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}',
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": session_id,
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(probe, timeout=10)
    assert excinfo.value.code == 404


# ---------------------------------------------------------------------------
# Edge cases the SDK server doesn't produce on its own, with urlopen faked
# ---------------------------------------------------------------------------


class _Reply:
    def __init__(self, body="", content_type="application/json", headers=None):
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(monkeypatch, *replies):
    sent = []
    queue = list(replies)

    def urlopen(req, timeout=None):
        sent.append(req)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(
        "gaia.mcp.client.transports.http.urllib.request.urlopen", urlopen
    )
    return sent


def _sse(*messages) -> str:
    return "".join(f"event: message\r\ndata: {m}\r\n\r\n" for m in messages)


def test_sse_reply_after_a_server_notification_is_found(monkeypatch):
    note = '{"jsonrpc": "2.0", "method": "notifications/message", "params": {}}'
    reply = '{"jsonrpc": "2.0", "id": 0, "result": {"ok": true}}'
    _fake_urlopen(monkeypatch, _Reply(_sse(note, reply), "text/event-stream"))

    response = HTTPTransport("http://mcp.test").send_request("ping")

    assert response["result"] == {"ok": True}


def test_sse_stream_without_the_reply_raises(monkeypatch):
    note = '{"jsonrpc": "2.0", "method": "notifications/message", "params": {}}'
    _fake_urlopen(monkeypatch, _Reply(_sse(note), "text/event-stream"))

    with pytest.raises(ValueError, match="without answering request 0"):
        HTTPTransport("http://mcp.test").send_request("ping")


def test_session_and_version_headers_follow_initialize(monkeypatch):
    init = '{"jsonrpc": "2.0", "id": 0, "result": {"protocolVersion": "2025-06-18"}}'
    sent = _fake_urlopen(
        monkeypatch,
        _Reply(init, headers={"Mcp-Session-Id": "abc"}),
        _Reply('{"jsonrpc": "2.0", "id": 1, "result": {}}'),
    )
    transport = HTTPTransport("http://mcp.test")

    transport.send_request("initialize")
    transport.send_request("tools/list")

    first, second = (dict(r.header_items()) for r in sent)
    assert first["Accept"] == "application/json, text/event-stream"
    assert "Mcp-session-id" not in first
    assert second["Mcp-session-id"] == "abc"
    assert second["Mcp-protocol-version"] == "2025-06-18"


def test_an_expired_session_says_to_reconnect(monkeypatch):
    init = '{"jsonrpc": "2.0", "id": 0, "result": {}}'
    gone = urllib.error.HTTPError("http://mcp.test", 404, "Not Found", {}, None)
    _fake_urlopen(monkeypatch, _Reply(init, headers={"Mcp-Session-Id": "abc"}), gone)
    transport = HTTPTransport("http://mcp.test")
    transport.send_request("initialize")

    with pytest.raises(RuntimeError, match="no longer knows this session") as excinfo:
        transport.send_request("tools/list")
    assert "abc" not in str(excinfo.value)


def test_disconnect_tolerates_a_server_that_refuses_delete(monkeypatch):
    init = '{"jsonrpc": "2.0", "id": 0, "result": {}}'
    refused = urllib.error.HTTPError("http://mcp.test", 405, "No", {}, None)
    sent = _fake_urlopen(
        monkeypatch, _Reply(init, headers={"Mcp-Session-Id": "abc"}), refused
    )
    transport = HTTPTransport("http://mcp.test")
    transport.send_request("initialize")

    transport.disconnect()

    assert sent[1].get_method() == "DELETE"
    assert transport.session_id is None and not transport.is_connected()
