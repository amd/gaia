import socket

import requests

from gaia.web.client import PinnedIPAdapter


class DummyInfo:
    def __init__(self, ip, port=80):
        # emulate socket.getaddrinfo return structure
        # (family, socktype, proto, canonname, sockaddr)
        self.entry = (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))

    def __iter__(self):
        yield self.entry


def test_ip_pinning_blocks_rebind_to_private_ip(monkeypatch):
    # simulate DNS rebind: first resolution returns public IP, second returns private
    calls = {
        "count": 0,
    }

    def fake_getaddrinfo(host, port, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    session = requests.Session()
    adapter = PinnedIPAdapter()
    session.mount("http://", adapter)

    resp = session.get("http://example.local/path")

    # Adapter should have rewritten the request URL to use the first resolved IP
    assert resp.request is not None
    assert "203.0.113.10" in resp.request.url
    # And the pinned cache should store the resolved IP
    key = ("example.local", 80)
    assert adapter._pinned_cache.get(key) == "203.0.113.10"


def test_ip_pinning_prevents_dns_rebind(monkeypatch):
    # Ensure subsequent resolutions would return a different IP, but adapter
    # continues to use the pinned one from cache.
    states = {"calls": 0}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        states["calls"] += 1
        if states["calls"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.7", port))]
        # Rebind to loopback on later calls
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    session = requests.Session()
    adapter = PinnedIPAdapter()
    session.mount("http://", adapter)

    # First request pins 198.51.100.7
    r1 = session.get("http://example.local/first")
    assert "198.51.100.7" in r1.request.url

    # On second request, getaddrinfo would return 127.0.0.1, but adapter should
    # use the cached 198.51.100.7
    r2 = session.get("http://example.local/second")
    assert "198.51.100.7" in r2.request.url
