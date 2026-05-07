import socket
from unittest.mock import MagicMock, patch

import requests

from gaia.web.client import PinnedIPAdapter


def test_ip_pinning_blocks_rebind_to_private_ip(monkeypatch):
    """PinnedIPAdapter resolves and caches the IP on first request, so a
    DNS-rebind that returns a different IP on the second resolution has
    no effect — the adapter already pinned the first IP."""
    calls = {"count": 0}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    adapter = PinnedIPAdapter()

    # Build a PreparedRequest to call send() directly (avoids real HTTP)
    req = requests.Request("GET", "http://example.local/path").prepare()

    # Mock super().send() so no real HTTP call is made
    mock_response = requests.Response()
    mock_response.status_code = 200
    mock_response._content = b"ok"
    mock_response.request = req

    with patch.object(
        PinnedIPAdapter.__bases__[0], "send", return_value=mock_response
    ):
        resp = adapter.send(req)

    # Adapter should have rewritten the URL to use the first resolved IP
    assert "203.0.113.10" in req.url
    assert resp.status_code == 200

    # Cache should store the resolved IP
    key = ("example.local", 80)
    assert adapter._pinned_cache.get(key) == "203.0.113.10"


def test_ip_pinning_prevents_dns_rebind(monkeypatch):
    """Subsequent resolutions would return a different IP, but adapter
    continues to use the pinned one from cache."""
    states = {"calls": 0}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        states["calls"] += 1
        if states["calls"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.7", port))]
        # Rebind to loopback on later calls
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    adapter = PinnedIPAdapter()

    mock_response = requests.Response()
    mock_response.status_code = 200
    mock_response._content = b"ok"

    with patch.object(
        PinnedIPAdapter.__bases__[0], "send", return_value=mock_response
    ):
        # First request pins 198.51.100.7
        r1_req = requests.Request("GET", "http://example.local/first").prepare()
        mock_response.request = r1_req
        adapter.send(r1_req)
        assert "198.51.100.7" in r1_req.url

        # Second request — getaddrinfo would return 127.0.0.1,
        # but adapter uses cached 198.51.100.7
        r2_req = requests.Request("GET", "http://example.local/second").prepare()
        mock_response.request = r2_req
        adapter.send(r2_req)
        assert "198.51.100.7" in r2_req.url
