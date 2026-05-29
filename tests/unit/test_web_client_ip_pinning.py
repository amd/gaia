import socket
import threading
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

    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_response):
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

    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_response):
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


def test_https_pinning_preserves_tls_hostname(monkeypatch):
    """HTTPS requests encode the original hostname in URL userinfo so
    get_connection sets assert_hostname on the pool."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    adapter = PinnedIPAdapter()

    req = requests.Request("GET", "https://example.com/page").prepare()

    mock_response = requests.Response()
    mock_response.status_code = 200
    mock_response._content = b"ok"
    mock_response.request = req

    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_response):
        adapter.send(req)

    # URL should contain userinfo with original hostname
    assert "example.com@93.184.216.34:443" in req.url

    # get_connection should strip userinfo and set assert_hostname
    mock_pool = MagicMock()
    with patch.object(
        PinnedIPAdapter.__bases__[0], "get_connection", return_value=mock_pool
    ):
        pool = adapter.get_connection(req.url)
    assert pool.assert_hostname == "example.com"


def test_http_pinning_does_not_set_tls_hostname(monkeypatch):
    """HTTP requests don't encode userinfo — no TLS hostname needed."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    adapter = PinnedIPAdapter()

    req = requests.Request("GET", "http://example.com/page").prepare()

    mock_response = requests.Response()
    mock_response.status_code = 200
    mock_response._content = b"ok"
    mock_response.request = req

    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_response):
        adapter.send(req)

    # HTTP URL should NOT have userinfo
    assert "@" not in req.url
    assert "93.184.216.34:80" in req.url


def test_concurrent_https_requests_use_correct_tls_hostname(monkeypatch):
    """Each thread's HTTPS request gets the correct assert_hostname on its pool."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        ips = {
            "alpha.example.com": "93.184.216.34",
            "beta.example.com": "198.51.100.1",
        }
        ip = ips.get(host, "203.0.113.1")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    adapter = PinnedIPAdapter()
    results = {}
    errors = []

    def make_request(hostname):
        try:
            req = requests.Request("GET", f"https://{hostname}/path").prepare()
            mock_resp = requests.Response()
            mock_resp.status_code = 200
            mock_resp._content = b"ok"
            mock_resp.request = req

            with patch.object(
                PinnedIPAdapter.__bases__[0], "send", return_value=mock_resp
            ):
                adapter.send(req)

            mock_pool = MagicMock()
            with patch.object(
                PinnedIPAdapter.__bases__[0], "get_connection", return_value=mock_pool
            ):
                pool = adapter.get_connection(req.url)
            results[hostname] = pool.assert_hostname
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=make_request, args=("alpha.example.com",)),
        threading.Thread(target=make_request, args=("beta.example.com",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Threads raised: {errors}"
    assert results["alpha.example.com"] == "alpha.example.com"
    assert results["beta.example.com"] == "beta.example.com"


def test_concurrent_same_ip_different_hosts(monkeypatch):
    """Two hosts resolving to the SAME pinned IP get separate pools with
    correct assert_hostname — the key race condition this design prevents."""

    SHARED_IP = "93.184.216.34"

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (SHARED_IP, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    adapter = PinnedIPAdapter()
    results = {}
    errors = []
    barrier = threading.Barrier(2, timeout=5)

    def make_request(hostname):
        try:
            req = requests.Request("GET", f"https://{hostname}/path").prepare()
            mock_resp = requests.Response()
            mock_resp.status_code = 200
            mock_resp._content = b"ok"
            mock_resp.request = req

            with patch.object(
                PinnedIPAdapter.__bases__[0], "send", return_value=mock_resp
            ):
                adapter.send(req)

            # Synchronize so both threads call get_connection concurrently
            barrier.wait()

            mock_pool = MagicMock()
            with patch.object(
                PinnedIPAdapter.__bases__[0], "get_connection", return_value=mock_pool
            ):
                pool = adapter.get_connection(req.url)
            results[hostname] = pool.assert_hostname
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=make_request, args=("site-a.example.com",)),
        threading.Thread(target=make_request, args=("site-b.example.com",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Threads raised: {errors}"
    # Even though both resolve to the same IP, each gets its own hostname
    assert results["site-a.example.com"] == "site-a.example.com"
    assert results["site-b.example.com"] == "site-b.example.com"


def test_strip_tls_host_with_userinfo():
    """_strip_tls_host extracts hostname from userinfo and returns clean URL."""
    url = "https://example.com@93.184.216.34:443/path?q=1"
    clean, hostname = PinnedIPAdapter._strip_tls_host(url)
    assert hostname == "example.com"
    assert clean == "https://93.184.216.34:443/path?q=1"
    assert "@" not in clean


def test_strip_tls_host_without_userinfo():
    """_strip_tls_host returns None hostname when no userinfo present."""
    url = "https://93.184.216.34:443/path"
    clean, hostname = PinnedIPAdapter._strip_tls_host(url)
    assert hostname is None
    assert clean == url
