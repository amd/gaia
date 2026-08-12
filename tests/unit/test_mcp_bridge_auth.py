#!/usr/bin/env python
#
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Bearer-token enforcement on the MCP bridge.

``--auth-token`` used to be accepted, announced, and then dropped on the floor:
the token never reached the HTTP server and no handler looked at the
Authorization header, so every endpoint answered identically with no token, a
valid token, or a wrong one. These tests drive a real ``HTTPServer`` over real
sockets — mocking the handler would prove only that a function was called, not
that an unauthenticated request is actually refused on the wire.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from gaia.mcp.mcp_bridge import (
    AUTH_TOKEN_ENV_VAR,
    PUBLIC_PATHS,
    MCPHTTPHandler,
)

# Every test here drives a loopback HTTPServer on an ephemeral port — that is
# the point of the suite, so opt out of the unit-test socket guard.
pytestmark = pytest.mark.allow_network

TOKEN = "s3cret-token"


class StubBridge:
    """Minimal stand-in for GAIAMCPBridge.

    The real constructor imports agents (faiss, LLM clients, Jira); the auth
    boundary needs none of that.
    """

    def __init__(self, auth_token=None):
        self.auth_token = auth_token
        self.host = "localhost"
        self.port = 0
        self.base_url = "http://localhost:13305/api/v1"
        self.agents = {"llm": {"description": "stub"}}
        self.tools = {"gaia.query": {"name": "gaia.query", "description": "stub"}}
        self.executed = []

    def execute_tool(self, tool_name, arguments):
        self.executed.append((tool_name, arguments))
        return {"success": True, "result": "stub"}


@pytest.fixture(name="server_factory")
def _server_factory():
    """Start a real MCP bridge HTTP server on an ephemeral port."""
    started = []

    def start(auth_token=None):
        bridge = StubBridge(auth_token=auth_token)

        def handler(*args, **kwargs):
            return MCPHTTPHandler(*args, bridge=bridge, **kwargs)

        httpd = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        started.append((httpd, thread))
        return f"http://127.0.0.1:{httpd.server_port}", bridge

    yield start

    for httpd, thread in started:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(url, token=None, method="GET", payload=None, raw_header=None):
    """Perform a request, returning (status, body_dict)."""
    headers = {}
    if raw_header is not None:
        headers["Authorization"] = raw_header
    elif token is not None:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


PROTECTED_GETS = ["/status", "/tools"]
JSONRPC_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
JSONRPC_CALL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {"name": "gaia.query", "arguments": {"query": "hi"}},
}


class TestTokenConfigured:
    """With --auth-token set, unauthenticated callers must be refused."""

    @pytest.mark.parametrize("path", PROTECTED_GETS)
    def test_get_without_token_is_401(self, server_factory, path):
        base, _ = server_factory(auth_token=TOKEN)
        status, body = _request(f"{base}{path}")
        assert status == 401
        assert "Authorization" in body["error"]

    @pytest.mark.parametrize("path", PROTECTED_GETS)
    def test_get_with_wrong_token_is_403(self, server_factory, path):
        base, _ = server_factory(auth_token=TOKEN)
        status, body = _request(f"{base}{path}", token="WRONGTOKEN")
        assert status == 403
        assert "Invalid" in body["error"]

    @pytest.mark.parametrize("path", PROTECTED_GETS)
    def test_get_with_valid_token_succeeds(self, server_factory, path):
        base, _ = server_factory(auth_token=TOKEN)
        status, _body = _request(f"{base}{path}", token=TOKEN)
        assert status == 200

    def test_jsonrpc_tools_list_without_token_is_401(self, server_factory):
        base, _ = server_factory(auth_token=TOKEN)
        status, _ = _request(f"{base}/", method="POST", payload=JSONRPC_LIST)
        assert status == 401

    def test_jsonrpc_tools_call_without_token_does_not_execute(self, server_factory):
        """The tool must not run — rejection happens before dispatch."""
        base, bridge = server_factory(auth_token=TOKEN)
        status, _ = _request(f"{base}/", method="POST", payload=JSONRPC_CALL)
        assert status == 401
        assert bridge.executed == []

    def test_jsonrpc_tools_call_with_valid_token_executes(self, server_factory):
        base, bridge = server_factory(auth_token=TOKEN)
        status, _ = _request(
            f"{base}/", method="POST", payload=JSONRPC_CALL, token=TOKEN
        )
        assert status == 200
        assert bridge.executed == [("gaia.query", {"query": "hi"})]

    @pytest.mark.parametrize("path", ["/chat", "/llm", "/jira"])
    def test_direct_tool_endpoints_reject_and_do_not_execute(
        self, server_factory, path
    ):
        base, bridge = server_factory(auth_token=TOKEN)
        status, _ = _request(f"{base}{path}", method="POST", payload={"query": "hi"})
        assert status == 401
        assert bridge.executed == []

    @pytest.mark.parametrize(
        "raw_header",
        [
            "",
            TOKEN,  # bare token, no scheme
            f"Basic {TOKEN}",  # wrong scheme
            "Bearer",  # scheme with no value
            "Bearer ",
        ],
    )
    def test_malformed_authorization_headers_are_401(self, server_factory, raw_header):
        base, _ = server_factory(auth_token=TOKEN)
        status, _ = _request(f"{base}/status", raw_header=raw_header)
        assert status == 401

    def test_bearer_scheme_is_case_insensitive(self, server_factory):
        """RFC 7235 auth-scheme matching is case-insensitive."""
        base, _ = server_factory(auth_token=TOKEN)
        status, _ = _request(f"{base}/status", raw_header=f"bearer {TOKEN}")
        assert status == 200

    def test_non_ascii_token_is_rejected_cleanly(self, server_factory):
        """A non-ASCII token must 403, not blow up compare_digest into a 500."""
        base, _ = server_factory(auth_token=TOKEN)
        status, _ = _request(f"{base}/status", raw_header="Bearer pásswörd")
        assert status == 403

    def test_non_ascii_configured_token_still_works(self, server_factory):
        base, _ = server_factory(auth_token="pásswörd")
        assert _request(f"{base}/status", token="pásswörd")[0] == 200
        assert _request(f"{base}/status", token="wrong")[0] == 403

    def test_token_prefix_is_rejected(self, server_factory):
        """A truncated token must not pass — guards against prefix comparison."""
        base, _ = server_factory(auth_token=TOKEN)
        status, _ = _request(f"{base}/status", token=TOKEN[:-1])
        assert status == 403

    def test_health_stays_public(self, server_factory):
        """Liveness probes and `gaia mcp status` rely on /health being open."""
        base, _ = server_factory(auth_token=TOKEN)
        status, body = _request(f"{base}/health")
        assert status == 200
        assert body["status"] == "healthy"

    def test_health_does_not_leak_inventory(self, server_factory):
        """The public endpoint exposes counts only, never agent or tool names."""
        base, _ = server_factory(auth_token=TOKEN)
        _, body = _request(f"{base}/health")
        assert body["agents"] == 1
        assert body["tools"] == 1
        assert "gaia.query" not in json.dumps(body)

    def test_status_inventory_requires_auth(self, server_factory):
        """Tool names are only readable with credentials."""
        base, _ = server_factory(auth_token=TOKEN)
        _, unauth = _request(f"{base}/status")
        assert "gaia.query" not in json.dumps(unauth)
        _, authed = _request(f"{base}/status", token=TOKEN)
        assert "gaia.query" in json.dumps(authed)

    def test_cors_preflight_stays_open_and_allows_authorization(self, server_factory):
        """Browsers never send Authorization on preflight."""
        base, _ = server_factory(auth_token=TOKEN)
        req = urllib.request.Request(f"{base}/status", method="OPTIONS")
        with urllib.request.urlopen(req, timeout=10) as response:
            assert response.status == 200
            allowed = response.headers.get("Access-Control-Allow-Headers", "")
        assert "Authorization" in allowed


class TestNoTokenConfigured:
    """Without a token the bridge stays open — unchanged default behaviour."""

    @pytest.mark.parametrize("path", ["/health"] + PROTECTED_GETS)
    def test_endpoints_open_when_unconfigured(self, server_factory, path):
        base, _ = server_factory(auth_token=None)
        status, _ = _request(f"{base}{path}")
        assert status == 200

    def test_stray_authorization_header_is_ignored(self, server_factory):
        base, _ = server_factory(auth_token=None)
        status, _ = _request(f"{base}/status", token="anything-at-all")
        assert status == 200

    def test_empty_token_is_treated_as_unconfigured(self, server_factory):
        """An empty --auth-token must not silently enable a bypassable check."""
        base, _ = server_factory(auth_token="")
        status, _ = _request(f"{base}/status")
        assert status == 200


class TestConfigurationContract:
    def test_health_is_the_only_public_path(self):
        assert PUBLIC_PATHS == frozenset({"/health"})

    def test_cli_env_var_matches_bridge(self):
        """cli.py duplicates the name to avoid importing the heavy bridge module."""
        from gaia.cli import MCP_AUTH_TOKEN_ENV

        assert MCP_AUTH_TOKEN_ENV == AUTH_TOKEN_ENV_VAR

    def test_bridge_reads_token_from_environment(self, monkeypatch):
        """start_server falls back to the env var so argv never carries the secret."""
        import sys

        import gaia.mcp.mcp_bridge as bridge_mod

        # start_server rewraps sys.stdout on Windows, which breaks pytest capture.
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv(AUTH_TOKEN_ENV_VAR, "from-env")
        captured = {}

        class FakeBridge(StubBridge):
            def __init__(self, *args, **kwargs):
                super().__init__(auth_token=kwargs.get("auth_token"))
                captured["auth_token"] = kwargs.get("auth_token")

        class FakeServer:
            def __init__(self, *args, **kwargs):
                pass

            def serve_forever(self):
                raise KeyboardInterrupt

        monkeypatch.setattr(bridge_mod, "GAIAMCPBridge", FakeBridge)
        monkeypatch.setattr(bridge_mod, "HTTPServer", FakeServer)

        bridge_mod.start_server(host="localhost", port=0)

        assert captured["auth_token"] == "from-env"
