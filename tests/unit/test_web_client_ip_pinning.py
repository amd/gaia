import socket
import threading
from unittest.mock import MagicMock, patch

import pytest
import requests

from gaia.web.client import PinnedIPAdapter, WebClient


def test_ip_pinning_blocks_rebind_to_private_ip(monkeypatch):
    """PinnedIPAdapter resolves and caches the IP on first request, so a
    DNS-rebind that returns a different IP on the second resolution has
    no effect — the adapter already pinned the first IP."""
    calls = {"count": 0}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
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
    assert "8.8.8.8" in req.url
    assert resp.status_code == 200

    # Cache should store the resolved IP
    key = ("example.local", 80)
    assert adapter._pinned_cache.get(key) == "8.8.8.8"


def test_ip_pinning_prevents_dns_rebind(monkeypatch):
    """Subsequent resolutions would return a different IP, but adapter
    continues to use the pinned one from cache."""
    states = {"calls": 0}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        states["calls"] += 1
        if states["calls"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
        # Rebind to loopback on later calls
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    adapter = PinnedIPAdapter()

    mock_response = requests.Response()
    mock_response.status_code = 200
    mock_response._content = b"ok"

    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_response):
        # First request pins 8.8.8.8
        r1_req = requests.Request("GET", "http://example.local/first").prepare()
        mock_response.request = r1_req
        adapter.send(r1_req)
        assert "8.8.8.8" in r1_req.url

        # Second request — getaddrinfo would return 127.0.0.1,
        # but adapter uses cached 8.8.8.8
        r2_req = requests.Request("GET", "http://example.local/second").prepare()
        mock_response.request = r2_req
        adapter.send(r2_req)
        assert "8.8.8.8" in r2_req.url


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
            "beta.example.com": "1.1.1.1",
        }
        ip = ips.get(host, "8.8.8.8")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    adapter = PinnedIPAdapter()
    results = {}
    errors = []

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp._content = b"ok"

    def make_request(hostname):
        try:
            req = requests.Request("GET", f"https://{hostname}/path").prepare()
            adapter.send(req)
            pool = adapter.get_connection(req.url)
            results[hostname] = pool.assert_hostname
        except Exception as exc:
            errors.append(exc)

    # Install the transport + pool-factory patches ONCE around both threads.
    # Patching a shared class method inside each thread races on install/
    # teardown and can leak a real network call; a single install is safe.
    # get_connection returns a FRESH mock per call so each request gets its
    # own pool — the per-hostname isolation under test.
    with (
        patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_resp),
        patch.object(
            PinnedIPAdapter.__bases__[0],
            "get_connection",
            side_effect=lambda *a, **k: MagicMock(),
        ),
    ):
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

    mock_resp = requests.Response()
    mock_resp.status_code = 200
    mock_resp._content = b"ok"

    def make_request(hostname):
        try:
            req = requests.Request("GET", f"https://{hostname}/path").prepare()
            adapter.send(req)

            # Synchronize so both threads call get_connection concurrently
            barrier.wait()

            pool = adapter.get_connection(req.url)
            results[hostname] = pool.assert_hostname
        except Exception as exc:
            errors.append(exc)

    # Single install of the patches (see sibling test): per-thread context
    # managers race on teardown and can leak a real connection. A fresh mock
    # pool per get_connection call proves each host keeps its own
    # assert_hostname even though both resolve to the same pinned IP.
    with (
        patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_resp),
        patch.object(
            PinnedIPAdapter.__bases__[0],
            "get_connection",
            side_effect=lambda *a, **k: MagicMock(),
        ),
    ):
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


# ============================================================================
# DNS-rebind TOCTOU: validate_url sees a PUBLIC IP, the adapter's own lookup
# sees a PRIVATE IP. The adapter must validate the IP it actually pins/dials
# and BLOCK — not connect to the private address.
# ============================================================================


def test_adapter_blocks_rebind_when_pinned_ip_is_private(monkeypatch):
    """The adapter's own resolution returns a private IP — pinning
    must reject it rather than caching/dialing it.

    ``_resolve_first_ip`` performs a single ``getaddrinfo``, so the fixture
    returns the rebound private IP directly: the contract under test is that
    the adapter validates the exact address it is about to pin/dial.
    """

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    adapter = PinnedIPAdapter()
    # super().send must NOT be reached — validation happens before connect.
    with patch.object(
        PinnedIPAdapter.__bases__[0], "send", side_effect=AssertionError("connected!")
    ):
        with pytest.raises(ValueError, match="private/reserved IP"):
            adapter._resolve_first_ip("example.local", 80)

    # Poisoned IP must NOT be cached — a later safe lookup should be retryable.
    assert ("example.local", 80) not in adapter._pinned_cache


def test_full_request_blocked_when_rebind_returns_private_ip(monkeypatch):
    """End-to-end through WebClient.get: validate_url's resolution returns a
    public IP (passes the pre-flight), but the adapter's resolution returns a
    private IP. The fetch must raise and NEVER reach the transport."""
    calls = {"count": 0}

    def fake_getaddrinfo(host, port=None, *args, **kwargs):
        calls["count"] += 1
        # Call 1 = WebClient.validate_url -> _validate_host_ip (public, OK).
        # Call 2 = PinnedIPAdapter._resolve_first_ip (rebound to private).
        if calls["count"] == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    client = WebClient()
    try:
        # Patch the underlying transport so a "successful" connect would be
        # observable — it must never be invoked.
        with patch.object(
            PinnedIPAdapter.__bases__[0],
            "send",
            side_effect=AssertionError("transport reached private IP"),
        ):
            with pytest.raises(ValueError, match="private/reserved IP"):
                client.get("http://rebind.example/path")
    finally:
        client.close()


def test_full_request_succeeds_for_public_host(monkeypatch):
    """Positive path: a normal public host resolves to a public IP on both
    lookups and the request completes through the (mocked) transport."""

    def fake_getaddrinfo(host, port=None, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    client = WebClient()
    try:
        mock_response = requests.Response()
        mock_response.status_code = 200
        mock_response.headers["Content-Type"] = "text/html"
        mock_response._content = b"<html><body><p>ok</p></body></html>"
        mock_response._content_consumed = True
        mock_response.encoding = "utf-8"

        with patch.object(
            PinnedIPAdapter.__bases__[0], "send", return_value=mock_response
        ):
            with patch.object(client, "_rate_limit_wait"):
                resp = client.get("http://public.example/page")

        assert resp.status_code == 200
        assert b"ok" in resp.content
        # Adapter pinned the validated public IP.
        assert (
            client._session.get_adapter("http://public.example/")._pinned_cache[
                ("public.example", 80)
            ]
            == "93.184.216.34"
        )
    finally:
        client.close()
