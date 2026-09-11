# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``fetch_webpage`` / ``open_url`` must honour the same SSRF guard as ``fetch_page``.

Both are registered in the flagship's default ``full`` profile. They used a
bare ``httpx.get`` with only a scheme check, so a prompt-injected document
could make the agent fetch the Lemonade API, the daemon's loopback API, or
cloud metadata at 169.254.169.254.
"""

import io
import ipaddress
from unittest.mock import patch

import pytest
import requests
import requests.adapters

from gaia.web import client as web_client
from gaia.web.client import WebClient
from tests.unit.test_profilespec_characterization import chat_agent_build_context


@pytest.fixture
def full_profile_tools():
    with chat_agent_build_context("full") as agent:
        agent._register_tools()
        yield agent, agent._tools_registry


def _tool(registry, name):
    return registry[name]["function"]


BLOCKED_URLS = [
    "http://127.0.0.1:13305/api/v1/models",  # Lemonade
    "http://localhost:4200/api/sessions",  # Agent UI backend
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://10.0.0.5/admin",
    "http://100.64.0.1/",  # CGNAT (I49)
    "http://100.100.100.100/",  # Tailscale's MagicDNS address (CGNAT)
    "http://[::1]:8000/v1/models",
    "file:///etc/passwd",
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_fetch_webpage_refuses_internal_targets(full_profile_tools, url):
    _, registry = full_profile_tools
    with patch("httpx.get") as bare_httpx:
        result = _tool(registry, "fetch_webpage")(url)
    assert result["status"] == "error"
    assert "Blocked" in result["error"]
    bare_httpx.assert_not_called()


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_open_url_refuses_internal_targets(full_profile_tools, url):
    _, registry = full_profile_tools
    with patch("webbrowser.open") as opener:
        result = _tool(registry, "open_url")(url)
    assert result["status"] == "error"
    opener.assert_not_called()


PUBLIC_IP = "93.184.216.34"


def _public_dns(host, port, *args, **kwargs):
    """Resolve names to one public address; IP literals resolve to themselves.

    Mirrors real ``getaddrinfo``: a literal like ``127.0.0.1`` is not looked
    up, so the guard still sees it as loopback.
    """
    import socket

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, port or 0))]
    family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (str(literal), port or 0))]


def _fake_transport(sent):
    """Stand in for the socket layer *below* PinnedIPAdapter, recording requests."""

    def send(adapter, request, **kwargs):
        sent.append(request)
        resp = requests.Response()
        resp.status_code = 200
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        resp.raw = io.BytesIO(b"<html><body><p>hello</p></body></html>")
        resp.url = request.url
        resp.request = request
        return resp

    return send


@pytest.mark.parametrize(
    "url,wire_url",
    [
        # http: the dialed address is the validated, pinned IP.
        ("http://example.com/page?q=1", f"http://{PUBLIC_IP}:80/page?q=1"),
        # https: host rides in userinfo so #3364's SNI hook can name it in TLS.
        ("https://example.com/page", f"https://example.com@{PUBLIC_IP}:443/page"),
    ],
)
def test_fetch_webpage_public_url_request_shape(full_profile_tools, url, wire_url):
    agent, registry = full_profile_tools
    sent = []
    with (
        patch("socket.getaddrinfo", side_effect=_public_dns),
        patch.object(requests.adapters.HTTPAdapter, "send", _fake_transport(sent)),
        patch("httpx.get") as bare_httpx,
    ):
        result = _tool(registry, "fetch_webpage")(url)

    assert result == {
        "status": "success",
        "url": url,
        "content": "hello",
        "truncated": False,
    }
    bare_httpx.assert_not_called()
    assert len(sent) == 1
    request = sent[0]
    assert request.method == "GET"
    assert request.url == wire_url
    assert request.headers["Host"] == "example.com"
    assert request.headers["User-Agent"].startswith("GAIA-Agent/")
    # Separate from the opt-in browser mixin's client: fetch_page stays off.
    assert agent._web_client is None


def test_redirect_to_loopback_is_refused_after_the_first_hop(full_profile_tools):
    """A public page that 302s to 127.0.0.1 must not be followed."""
    _, registry = full_profile_tools
    sent = []

    def redirecting(adapter, request, **kwargs):
        sent.append(request)
        resp = requests.Response()
        resp.status_code = 302
        resp.headers["Location"] = "http://127.0.0.1:13305/api/v1/models"
        resp.raw = io.BytesIO(b"")
        resp.url = request.url
        resp.request = request
        return resp

    with (
        patch("socket.getaddrinfo", side_effect=_public_dns),
        patch.object(requests.adapters.HTTPAdapter, "send", redirecting),
    ):
        result = _tool(registry, "fetch_webpage")("http://example.com/")
    assert result["status"] == "error"
    assert "Blocked" in result["error"]
    assert len(sent) == 1  # the loopback hop was never sent


def test_open_url_opens_a_public_url(full_profile_tools):
    _, registry = full_profile_tools
    with (
        patch.object(WebClient, "validate_url", return_value=None),
        patch("webbrowser.open") as opener,
    ):
        result = _tool(registry, "open_url")("https://example.com/")
    assert result["status"] == "success"
    opener.assert_called_once_with("https://example.com/")


@pytest.mark.parametrize(
    "ip",
    ["100.64.0.1", "100.127.255.254", "::ffff:100.64.0.1", "::ffff:10.0.0.1"],
)
def test_cgnat_and_mapped_private_ranges_are_blocked(ip):
    assert web_client._is_blocked_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["100.63.255.255", "100.128.0.1", "93.184.216.34"])
def test_neighbouring_public_ranges_stay_reachable(ip):
    assert not web_client._is_blocked_ip(ipaddress.ip_address(ip))


def test_inline_web_client_is_closed_during_agent_cleanup(full_profile_tools):
    agent, _ = full_profile_tools
    client = agent._inline_web_client()
    with patch.object(client, "close") as close:
        agent.__del__()
    close.assert_called_once()


@pytest.mark.parametrize(
    "failure", [OSError("certificate config failed"), ImportError("missing dependency")]
)
def test_open_url_reports_client_initialization_failure(full_profile_tools, failure):
    agent, registry = full_profile_tools
    with (
        patch.object(agent, "_inline_web_client", side_effect=failure),
        patch("webbrowser.open") as opener,
    ):
        result = _tool(registry, "open_url")("https://example.com")
    assert result["status"] == "error"
    assert str(failure) in result["error"]
    opener.assert_not_called()
