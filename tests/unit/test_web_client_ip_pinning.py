import socket
import threading
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

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
    """HTTPS requests encode the original hostname in URL userinfo so the
    pool is built with server_hostname set to it."""

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

    # The pool is keyed on the pinned IP but names the real host in TLS.
    host_params, pool_kwargs = adapter.build_connection_pool_key_attributes(
        req, True, None
    )
    assert host_params["host"] == "93.184.216.34"
    assert pool_kwargs["server_hostname"] == "example.com"


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


@pytest.mark.parametrize(("scheme", "port"), [("http", 80), ("https", 443)])
def test_ip_pinning_brackets_ipv6_literals(monkeypatch, scheme, port):
    pinned_ip = "2606:4700:4700::1111"

    def fake_getaddrinfo(host, resolved_port, *args, **kwargs):
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                (pinned_ip, resolved_port, 0, 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    adapter = PinnedIPAdapter()
    req = requests.Request("GET", f"{scheme}://example.com/path").prepare()
    mock_response = requests.Response()

    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_response):
        adapter.send(req)

    parsed = urlparse(req.url)
    assert f"[{pinned_ip}]:{port}" in parsed.netloc
    assert parsed.hostname == pinned_ip
    assert parsed.port == port


def test_concurrent_https_requests_use_correct_tls_hostname(monkeypatch):
    """Each thread's HTTPS request gets the correct server_hostname."""

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
            _, pool_kwargs = adapter.build_connection_pool_key_attributes(
                req, True, None
            )
            results[hostname] = pool_kwargs["server_hostname"]
        except Exception as exc:
            errors.append(exc)

    # Install the transport patch ONCE around both threads. Patching a shared
    # class method inside each thread races on install/teardown and can leak a
    # real network call; a single install is safe.
    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_resp):
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
    """Two hosts resolving to the SAME pinned IP keep separate TLS identities
    — the race this design exists to prevent."""

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

            # Synchronize so both threads resolve the pool key concurrently.
            barrier.wait()

            _, pool_kwargs = adapter.build_connection_pool_key_attributes(
                req, True, None
            )
            results[hostname] = pool_kwargs["server_hostname"]
        except Exception as exc:
            errors.append(exc)

    # Single install of the patch (see sibling test): per-thread context
    # managers race on teardown and can leak a real connection.
    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=mock_resp):
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


def test_strip_tls_host_brackets_ipv6_literal():
    pinned_ip = "2606:4700:4700::1111"
    url = f"https://example.com@[{pinned_ip}]:443/path?q=1"

    clean, hostname = PinnedIPAdapter._strip_tls_host(url)

    parsed = urlparse(clean)
    assert hostname == "example.com"
    assert parsed.netloc == f"[{pinned_ip}]:443"
    assert parsed.hostname == pinned_ip
    assert parsed.port == 443


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


# ---------------------------------------------------------------------------
# SNI: the pinned IP must not become the TLS server name
#
# The adapter connects to a validated, pinned IP but must still NAME the real
# hostname in the TLS ClientHello, or every SNI-vhosted host (most CDNs)
# returns the wrong certificate or refuses the handshake.
# ---------------------------------------------------------------------------


def _pinned_https_request(monkeypatch, hostname, ip):
    """Build the PreparedRequest the adapter produces for an HTTPS URL."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, 1, 6, "", (ip, port))],
    )
    adapter = PinnedIPAdapter()
    req = requests.Request("GET", f"https://{hostname}/page").prepare()
    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=MagicMock()):
        adapter.send(req)
    return adapter, req


def test_https_pool_kwargs_set_sni_server_hostname(monkeypatch):
    """The TLS pool is built with server_hostname = the real hostname.

    Without this the ClientHello carries the IP and SNI-vhosted servers fail.
    """
    adapter, req = _pinned_https_request(monkeypatch, "example.com", "93.184.216.34")

    host_params, pool_kwargs = adapter.build_connection_pool_key_attributes(
        req, True, None
    )

    # Connect address stays the validated, pinned IP...
    assert host_params["host"] == "93.184.216.34"
    assert host_params["scheme"] == "https"
    # ...while the TLS identity is the real hostname. server_hostname is what
    # urllib3 puts in the ClientHello AND what OpenSSL verifies the cert
    # against, so this one key covers both.
    assert pool_kwargs["server_hostname"] == "example.com"


def test_http_pool_kwargs_have_no_tls_hostname(monkeypatch):
    """Plain HTTP carries no userinfo, so no TLS pool arguments are added."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, 1, 6, "", ("93.184.216.34", port))
        ],
    )
    adapter = PinnedIPAdapter()
    req = requests.Request("GET", "http://example.com/page").prepare()
    with patch.object(PinnedIPAdapter.__bases__[0], "send", return_value=MagicMock()):
        adapter.send(req)

    host_params, pool_kwargs = adapter.build_connection_pool_key_attributes(
        req, True, None
    )
    assert host_params["host"] == "93.184.216.34"
    assert "server_hostname" not in pool_kwargs


def test_same_ip_different_hosts_get_separate_pools(monkeypatch):
    """Two hostnames on one IP must not share a TLS connection pool.

    server_hostname is a urllib3 PoolKey field, so distinct hostnames key
    distinct pools instead of racing to overwrite each other's TLS identity.
    """
    shared_ip = "93.184.216.34"
    adapter = PinnedIPAdapter()
    pools = {}
    for hostname in ("alpha.example", "beta.example"):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda host, port, *a, **k: [(socket.AF_INET, 1, 6, "", (shared_ip, port))],
        )
        req = requests.Request("GET", f"https://{hostname}/").prepare()
        with patch.object(
            PinnedIPAdapter.__bases__[0], "send", return_value=MagicMock()
        ):
            adapter.send(req)
        pools[hostname] = adapter.get_connection_with_tls_context(req, True)

    assert pools["alpha.example"] is not pools["beta.example"]
    assert pools["alpha.example"].conn_kw["server_hostname"] == "alpha.example"
    assert pools["beta.example"].conn_kw["server_hostname"] == "beta.example"
    # Both still dial the same pinned IP.
    assert pools["alpha.example"].host == shared_ip
    assert pools["beta.example"].host == shared_ip


def test_adapter_refuses_to_start_without_the_sni_hook(monkeypatch):
    """An old requests must fail loudly, not silently revert to IP-as-SNI.

    build_connection_pool_key_attributes arrived in requests 2.32.3. Without
    it the override never runs and every HTTPS fetch names the pinned IP in
    the handshake again — the exact bug the adapter exists to avoid. A pin in
    setup.py only covers fresh installs, so the check has to be at runtime.
    """
    monkeypatch.delattr(
        requests.adapters.HTTPAdapter,
        "build_connection_pool_key_attributes",
        raising=True,
    )

    with pytest.raises(RuntimeError) as excinfo:
        PinnedIPAdapter()

    message = str(excinfo.value)
    # The error has to name the requirement, the actual state, and the fix.
    assert "2.32.3" in message
    assert requests.__version__ in message
    assert "pip install" in message


def test_adapter_constructs_on_a_supported_requests():
    """The guard is a capability check, so it passes on the pinned floor."""
    assert hasattr(
        requests.adapters.HTTPAdapter, "build_connection_pool_key_attributes"
    ), "test environment predates requests 2.32.3"
    assert PinnedIPAdapter() is not None
