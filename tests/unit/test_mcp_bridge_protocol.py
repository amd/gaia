#!/usr/bin/env python
#
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""MCP spec compliance of the ``gaia mcp`` bridge's JSON-RPC endpoint.

Strict MCP clients validate every response against the spec. These tests drive
the real ``GAIAMCPBridge`` behind a real ``HTTPServer`` and check each response
shape; when the optional ``mcp`` package is installed they also validate it
with the SDK's own pydantic types, which is what those clients do.
"""

import json
import re
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from gaia.mcp.mcp_bridge import (
    MCP_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    GAIAMCPBridge,
    MCPHTTPHandler,
)

# The suite drives a loopback HTTPServer on an ephemeral port.
pytestmark = pytest.mark.allow_network


class _RefusingLLM:
    """LLM client whose backend is down, like a stopped Lemonade Server."""

    def generate(self, **_kwargs):
        raise ConnectionError("Lemonade Server not reachable at http://localhost")


class _EchoLLM:
    def generate(self, prompt, **_kwargs):
        return f"echo: {prompt}"


@pytest.fixture(name="bridge_server")
def _bridge_server():
    bridge = GAIAMCPBridge(port=0)

    def handler(*args, **kwargs):
        return MCPHTTPHandler(*args, bridge=bridge, **kwargs)

    httpd = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}/", bridge
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def _post(url, payload):
    """POST JSON, returning (status, raw body bytes)."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _rpc(url, method, params=None, request_id=1):
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    status, body = _post(url, payload)
    message = json.loads(body)
    assert message["jsonrpc"] == "2.0"
    assert message["id"] == request_id
    return status, message


INIT_PARAMS = {
    "protocolVersion": MCP_PROTOCOL_VERSION,
    "capabilities": {},
    "clientInfo": {"name": "pytest", "version": "0"},
}


class TestInitialize:
    def test_capabilities_are_objects_and_only_tools(self, bridge_server):
        url, _ = bridge_server
        status, message = _rpc(url, "initialize", INIT_PARAMS)
        assert status == 200
        result = message["result"]
        # The bridge serves tools only; advertising resources/prompts invites
        # calls it would answer with "Method not found".
        assert result["capabilities"] == {"tools": {}}
        assert result["serverInfo"]["name"]
        assert result["serverInfo"]["version"]

    def test_protocol_version_is_an_mcp_date_string(self, bridge_server):
        url, _ = bridge_server
        _, message = _rpc(url, "initialize", INIT_PARAMS)
        version = message["result"]["protocolVersion"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", version), version
        assert version in SUPPORTED_PROTOCOL_VERSIONS

    @pytest.mark.parametrize("requested", sorted(SUPPORTED_PROTOCOL_VERSIONS))
    def test_supported_requested_version_is_echoed(self, bridge_server, requested):
        url, _ = bridge_server
        params = dict(INIT_PARAMS, protocolVersion=requested)
        _, message = _rpc(url, "initialize", params)
        assert message["result"]["protocolVersion"] == requested

    def test_unsupported_requested_version_gets_server_latest(self, bridge_server):
        url, _ = bridge_server
        params = dict(INIT_PARAMS, protocolVersion="1.0.0")
        _, message = _rpc(url, "initialize", params)
        assert message["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION

    def test_validates_against_mcp_sdk_types(self, bridge_server):
        types = pytest.importorskip("mcp.types")
        url, _ = bridge_server
        _, message = _rpc(url, "initialize", INIT_PARAMS)
        parsed = types.InitializeResult.model_validate(message["result"])
        assert parsed.capabilities.tools is not None
        assert parsed.capabilities.resources is None
        assert parsed.capabilities.prompts is None


class TestNotifications:
    def test_initialized_notification_is_accepted_without_a_body(self, bridge_server):
        url, _ = bridge_server
        status, body = _post(
            url, {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert status == 202
        assert body == b""

    def test_ping_returns_empty_result(self, bridge_server):
        url, _ = bridge_server
        status, message = _rpc(url, "ping")
        assert status == 200
        assert message["result"] == {}


class TestToolsList:
    def test_every_tool_has_an_object_input_schema(self, bridge_server):
        url, _ = bridge_server
        status, message = _rpc(url, "tools/list", {})
        assert status == 200
        tools = message["result"]["tools"]
        assert {t["name"] for t in tools} == {"gaia.query", "gaia.chat"}
        for tool in tools:
            schema = tool["inputSchema"]
            assert schema["type"] == "object", tool["name"]
            assert "query" in schema["properties"], tool["name"]
            assert schema["required"] == ["query"], tool["name"]
            assert tool["description"], tool["name"]

    def test_validates_against_mcp_sdk_types(self, bridge_server):
        types = pytest.importorskip("mcp.types")
        url, _ = bridge_server
        _, message = _rpc(url, "tools/list", {})
        parsed = types.ListToolsResult.model_validate(message["result"])
        assert len(parsed.tools) == 2

    def test_get_tools_matches_jsonrpc_list(self, bridge_server):
        url, _ = bridge_server
        _, message = _rpc(url, "tools/list", {})
        with urllib.request.urlopen(f"{url}tools", timeout=10) as response:
            rest = json.loads(response.read())
        assert rest["tools"] == message["result"]["tools"]


class TestToolsCall:
    def test_backend_failure_is_flagged_is_error(self, bridge_server):
        url, bridge = bridge_server
        bridge.llm_client = _RefusingLLM()
        status, message = _rpc(
            url, "tools/call", {"name": "gaia.query", "arguments": {"query": "hi"}}
        )
        assert status == 200
        result = message["result"]
        assert result["isError"] is True
        assert "not reachable" in result["content"][0]["text"]

    def test_success_is_not_flagged_is_error(self, bridge_server):
        url, bridge = bridge_server
        bridge.llm_client = _EchoLLM()
        _, message = _rpc(
            url, "tools/call", {"name": "gaia.query", "arguments": {"query": "hi"}}
        )
        result = message["result"]
        assert result["isError"] is False
        assert json.loads(result["content"][0]["text"])["result"] == "echo: hi"

    def test_call_result_validates_against_mcp_sdk_types(self, bridge_server):
        types = pytest.importorskip("mcp.types")
        url, bridge = bridge_server
        bridge.llm_client = _RefusingLLM()
        _, message = _rpc(
            url, "tools/call", {"name": "gaia.query", "arguments": {"query": "hi"}}
        )
        parsed = types.CallToolResult.model_validate(message["result"])
        assert parsed.is_error is True

    def test_unknown_tool_is_a_jsonrpc_invalid_params_error(self, bridge_server):
        url, _ = bridge_server
        status, message = _rpc(
            url, "tools/call", {"name": "gaia.nonexistent", "arguments": {}}
        )
        assert status == 200
        assert "result" not in message
        assert message["error"]["code"] == -32602
        assert "gaia.nonexistent" in message["error"]["message"]


class TestProtocolErrorsRideA200:
    """Streamable HTTP reads any non-2xx as a TRANSPORT failure, so a 4xx makes
    an SDK client raise instead of reporting the JSON-RPC error it understands.
    """

    def test_unknown_method_is_a_jsonrpc_method_not_found(self, bridge_server):
        url, _ = bridge_server
        status, message = _rpc(url, "resources/list")
        assert status == 200
        assert "result" not in message
        assert message["error"]["code"] == -32601
        assert "resources/list" in message["error"]["message"]

    def test_unknown_method_error_validates_against_mcp_sdk_types(self, bridge_server):
        types = pytest.importorskip("mcp.types")
        url, _ = bridge_server
        _, message = _rpc(url, "prompts/list")
        parsed = types.JSONRPCError.model_validate(message)
        assert parsed.error.code == types.METHOD_NOT_FOUND

    def test_wrong_jsonrpc_version_is_an_invalid_request(self, bridge_server):
        url, _ = bridge_server
        status, body = _post(url, {"jsonrpc": "1.0", "id": 7, "method": "ping"})
        message = json.loads(body)
        assert status == 200
        assert message["error"]["code"] == -32600
        assert message["id"] == 7

    def test_non_object_payload_is_an_invalid_request(self, bridge_server):
        url, _ = bridge_server
        status, body = _post(url, ["not", "an", "object"])
        message = json.loads(body)
        assert status == 200
        assert message["error"]["code"] == -32600
        assert message["id"] is None
