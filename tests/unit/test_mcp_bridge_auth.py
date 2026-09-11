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


def _raw(url, method="GET", origin=None, payload=None):
    """Perform a request, returning (status, lowercased headers, body bytes)."""
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return (
                response.status,
                {k.lower(): v for k, v in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


LOCAL_ORIGIN = "http://localhost:3000"  # the example app's dev server
EVIL_ORIGIN = "https://evil.example"

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

    def test_loopback_preflight_is_approved_and_allows_authorization(
        self, server_factory
    ):
        """Browsers never send Authorization on preflight, so it stays unauthenticated."""
        base, _ = server_factory(auth_token=TOKEN)
        status, headers, _ = _raw(
            f"{base}/status", method="OPTIONS", origin=LOCAL_ORIGIN
        )
        assert status == 200
        assert "Authorization" in headers.get("access-control-allow-headers", "")
        assert headers.get("access-control-allow-origin") == LOCAL_ORIGIN


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


class TestBrowserOrigins:
    """A web page elsewhere must not be able to drive or read the bridge.

    Loopback is no boundary here: the attacker is a page in the user's own
    browser fetching ``http://localhost:<port>``. What stops it is refusing a
    foreign ``Origin`` outright and never answering ``Access-Control-Allow-Origin: *``.
    """

    @pytest.mark.parametrize("auth_token", [None, TOKEN])
    def test_foreign_origin_post_is_refused_before_any_tool_runs(
        self, server_factory, auth_token
    ):
        base, bridge = server_factory(auth_token=auth_token)
        status, headers, body = _raw(
            f"{base}/chat", method="POST", origin=EVIL_ORIGIN, payload={"query": "x"}
        )
        assert status == 403
        assert bridge.executed == []
        assert "access-control-allow-origin" not in headers
        assert b"GAIA_MCP_ALLOWED_ORIGINS" in body

    @pytest.mark.parametrize("path", ["/", "/chat", "/llm", "/rpc", "/v1/messages"])
    def test_every_post_route_refuses_a_foreign_origin(self, server_factory, path):
        base, bridge = server_factory(auth_token=None)
        status, _, _ = _raw(
            f"{base}{path}", method="POST", origin=EVIL_ORIGIN, payload=JSONRPC_CALL
        )
        assert status == 403
        assert bridge.executed == []

    def test_foreign_origin_cannot_read_inventory(self, server_factory):
        base, _ = server_factory(auth_token=None)
        status, headers, body = _raw(f"{base}/status", origin=EVIL_ORIGIN)
        assert status == 403
        assert b"gaia.query" not in body
        assert "access-control-allow-origin" not in headers

    def test_foreign_origin_preflight_is_refused(self, server_factory):
        base, _ = server_factory(auth_token=None)
        status, headers, _ = _raw(f"{base}/chat", method="OPTIONS", origin=EVIL_ORIGIN)
        assert status == 403
        assert "access-control-allow-origin" not in headers

    def test_loopback_origin_is_echoed_never_wildcard(self, server_factory):
        base, bridge = server_factory(auth_token=None)
        status, headers, _ = _raw(
            f"{base}/chat", method="POST", origin=LOCAL_ORIGIN, payload={"query": "x"}
        )
        assert status == 200
        assert headers.get("access-control-allow-origin") == LOCAL_ORIGIN
        assert bridge.executed == [("gaia.chat", {"query": "x"})]

    def test_non_browser_client_gets_no_cors_header(self, server_factory):
        """curl / MCP clients send no Origin: they work, and get no ACAO at all."""
        base, _ = server_factory(auth_token=None)
        status, headers, _ = _raw(f"{base}/status")
        assert status == 200
        assert "access-control-allow-origin" not in headers

    def test_env_var_admits_an_extra_origin(self, server_factory, monkeypatch):
        monkeypatch.setenv("GAIA_MCP_ALLOWED_ORIGINS", "https://n8n.internal")
        base, _ = server_factory(auth_token=None)
        status, headers, _ = _raw(f"{base}/status", origin="https://n8n.internal")
        assert status == 200
        assert headers.get("access-control-allow-origin") == "https://n8n.internal"


class TestNoWildcardEver:
    """No response the bridge sends may carry ``Access-Control-Allow-Origin: *``."""

    @pytest.mark.parametrize("origin", [EVIL_ORIGIN, "null", LOCAL_ORIGIN, None])
    @pytest.mark.parametrize(
        "method,path,payload",
        [
            ("GET", "/health", None),
            ("GET", "/status", None),
            ("OPTIONS", "/chat", None),
            ("POST", "/chat", {"query": "x"}),
            ("POST", "/", JSONRPC_LIST),
        ],
    )
    def test_no_wildcard_acao(self, server_factory, origin, method, path, payload):
        base, _ = server_factory(auth_token=None)
        _, headers, _ = _raw(
            f"{base}{path}", method=method, origin=origin, payload=payload
        )
        acao = headers.get("access-control-allow-origin")
        assert acao != "*"
        if origin != LOCAL_ORIGIN:
            assert acao is None


@pytest.mark.xfail(
    strict=True,
    reason="Requiring a token by default would break every existing gaia mcp "
    "consumer; the bridge still defaults to no auth and warns at startup.",
)
def test_bridge_requires_a_token_by_default(server_factory):
    base, bridge = server_factory(auth_token=None)
    status, _, _ = _raw(f"{base}/chat", method="POST", payload={"query": "x"})
    assert status == 401
    assert bridge.executed == []


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
