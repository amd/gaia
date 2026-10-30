import socket
import threading
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
import requests

from gaia.web.client import PinnedIPAdapter, WebClient


def _transport(response, sent):
    """Stand-in for HTTPAdapter.send that records the request as dialed.

    The adapter restores the original URL once send returns, so the pinned
    form is only observable while the transport holds the request.
    """

    def fake_send(request, *args, **kwargs):
        sent.append(request.copy())
        return response

    return fake_send


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

    sent = []
    with patch.object(
        PinnedIPAdapter.__bases__[0],
        "send",
        side_effect=_transport(mock_response, sent),
    ):
        resp = adapter.send(req)

    # Adapter should have rewritten the URL to use the first resolved IP
    assert "8.8.8.8" in sent[0].url
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

    sent = []
    with patch.object(
        PinnedIPAdapter.__bases__[0],
        "send",
        side_effect=_transport(mock_response, sent),
    ):
        # First request pins 8.8.8.8
        r1_req = requests.Request("GET", "http://example.local/first").prepare()
        mock_response.request = r1_req
        adapter.send(r1_req)
        assert "8.8.8.8" in sent[-1].url

        # Second request — getaddrinfo would return 127.0.0.1,
        # but adapter uses cached 8.8.8.8
        r2_req = requests.Request("GET", "http://example.local/second").prepare()
        mock_response.request = r2_req
        adapter.send(r2_req)
        assert "8.8.8.8" in sent[-1].url


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

    sent = []
    with patch.object(
        PinnedIPAdapter.__bases__[0],
        "send",
        side_effect=_transport(mock_response, sent),
    ):
        adapter.send(req)

    # URL should contain userinfo with original hostname
    assert "example.com@93.184.216.34:443" in sent[0].url

    # The pool is keyed on the pinned IP but names the real host in TLS.
    host_params, pool_kwargs = adapter.build_connection_pool_key_attributes(
        sent[0], True, None
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

    sent = []
    with patch.object(
        PinnedIPAdapter.__bases__[0],
        "send",
        side_effect=_transport(mock_response, sent),
    ):
        adapter.send(req)

    # HTTP URL should NOT have userinfo
    assert "@" not in sent[0].url
    assert "93.184.216.34:80" in sent[0].url


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

    sent = []
    with patch.object(
        PinnedIPAdapter.__bases__[0],
        "send",
        side_effect=_transport(mock_response, sent),
    ):
        adapter.send(req)

    parsed = urlparse(sent[0].url)
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

    sent = []

    def make_request(hostname):
        try:
            req = requests.Request("GET", f"https://{hostname}/path").prepare()
            adapter.send(req)
            (dialed,) = [r for r in sent if r.headers["Host"] == hostname]
            _, pool_kwargs = adapter.build_connection_pool_key_attributes(
                dialed, True, None
            )
            results[hostname] = pool_kwargs["server_hostname"]
        except Exception as exc:
            errors.append(exc)

    # Install the transport patch ONCE around both threads. Patching a shared
    # class method inside each thread races on install/teardown and can leak a
    # real network call; a single install is safe.
    with patch.object(
        PinnedIPAdapter.__bases__[0], "send", side_effect=_transport(mock_resp, sent)
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

    sent = []

    def make_request(hostname):
        try:
            req = requests.Request("GET", f"https://{hostname}/path").prepare()
            adapter.send(req)

            # Synchronize so both threads resolve the pool key concurrently.
            barrier.wait()

            (dialed,) = [r for r in sent if r.headers["Host"] == hostname]
            _, pool_kwargs = adapter.build_connection_pool_key_attributes(
                dialed, True, None
            )
            results[hostname] = pool_kwargs["server_hostname"]
        except Exception as exc:
            errors.append(exc)

    # Single install of the patch (see sibling test): per-thread context
    # managers race on teardown and can leak a real connection.
    with patch.object(
        PinnedIPAdapter.__bases__[0], "send", side_effect=_transport(mock_resp, sent)
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
    sent = []
    with patch.object(
        PinnedIPAdapter.__bases__[0], "send", side_effect=_transport(MagicMock(), sent)
    ):
        adapter.send(req)
    return adapter, sent[0]


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
    sent = []
    with patch.object(
        PinnedIPAdapter.__bases__[0], "send", side_effect=_transport(MagicMock(), sent)
    ):
        adapter.send(req)

    host_params, pool_kwargs = adapter.build_connection_pool_key_attributes(
        sent[0], True, None
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
        sent = []
        with patch.object(
            PinnedIPAdapter.__bases__[0],
            "send",
            side_effect=_transport(MagicMock(), sent),
        ):
            adapter.send(req)
        pools[hostname] = adapter.get_connection_with_tls_context(sent[0], True)

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


class TestLoopbackAllowlist:
    """GAIA_WEB_ALLOWED_HOSTS is an opt-in test affordance: default-off, and it
    only permits loopback for a host the operator explicitly named."""

    def test_default_blocks_loopback(self, monkeypatch):
        import pytest

        from gaia.web import client

        monkeypatch.delenv("GAIA_WEB_ALLOWED_HOSTS", raising=False)
        with pytest.raises(ValueError, match="private/reserved"):
            client._assert_ip_allowed("127.0.0.1", "127.0.0.1")

    def test_allowlisted_host_permitted(self, monkeypatch):
        from gaia.web import client

        monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
        client._assert_ip_allowed("127.0.0.1", "127.0.0.1")  # no raise

    def test_other_private_host_still_blocked(self, monkeypatch):
        import pytest

        from gaia.web import client

        monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "127.0.0.1")
        with pytest.raises(ValueError, match="private/reserved"):
            client._assert_ip_allowed("10.0.0.1", "evil.internal")

    def test_allowlisted_name_resolving_off_loopback_still_blocked(self, monkeypatch):
        """The allowlist keys on the *name*; the address still has to be
        loopback. A DNS rebind that points an allowlisted name at RFC1918 is
        the whole reason the connect-time pin exists."""
        import pytest

        from gaia.web import client

        monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "fixtures.local")
        with pytest.raises(ValueError, match="private/reserved"):
            client._assert_ip_allowed("10.0.0.1", "fixtures.local")

    def test_allowlisted_name_cannot_reach_cloud_metadata(self, monkeypatch):
        """169.254.169.254 is ``is_private``, so an ``is_private`` carve-out
        would hand the metadata endpoint to any allowlisted name."""
        import pytest

        from gaia.web import client

        monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "fixtures.local")
        with pytest.raises(ValueError, match="private/reserved"):
            client._assert_ip_allowed("169.254.169.254", "fixtures.local")

    def test_allowlisted_name_unparseable_address_still_raises(self, monkeypatch):
        import pytest

        from gaia.web import client

        monkeypatch.setenv("GAIA_WEB_ALLOWED_HOSTS", "fixtures.local")
        with pytest.raises(ValueError, match="unparseable"):
            client._assert_ip_allowed("not-an-ip", "fixtures.local")


# ---------------------------------------------------------------------------
# User-visible URLs: the pinned host@ip form steers the connection only. Error
# text and result URLs must name the page the user asked for, or a 404 reads
# like a phishing link ("https://www.nps.gov@143.204.160.88:443/...").
# ---------------------------------------------------------------------------

_PINNED_IP = "143.204.160.88"


class _FakeOrigin:
    """Replaces the urllib3 pool so requests' real HTTPAdapter.send runs.

    ``routes`` maps a path to ``(status, reason, headers, body)``. Every dialed
    request is recorded with its URL and TLS pool kwargs.
    """

    def __init__(self, routes):
        self.routes = routes
        self.dialed = []

    def install(self, monkeypatch):
        origin = self

        def get_connection_with_tls_context(
            self, request, verify, proxies=None, cert=None
        ):
            _, pool_kwargs = self.build_connection_pool_key_attributes(
                request, verify, cert
            )
            origin.dialed.append((request.url, pool_kwargs.get("server_hostname")))
            conn = MagicMock()
            conn.urlopen.side_effect = lambda **kw: origin._respond(request.path_url)
            return conn

        monkeypatch.setattr(
            PinnedIPAdapter,
            "get_connection_with_tls_context",
            get_connection_with_tls_context,
        )
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda host, port=None, *a, **k: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PINNED_IP, port or 0))
            ],
        )

    def _respond(self, path):
        import io

        from urllib3 import HTTPResponse

        status, reason, headers, body = self.routes[path]
        return HTTPResponse(
            body=io.BytesIO(body),
            headers=headers,
            status=status,
            reason=reason,
            preload_content=False,
        )


def _assert_no_pinned_form(text):
    assert "@" + _PINNED_IP not in text
    assert _PINNED_IP not in text


def _fetch_page_tool(client):
    from gaia.agents.tools.browser_tools import BrowserToolsMixin

    class _Host(BrowserToolsMixin):
        def __init__(self):
            self._web_client = client
            self._path_validator = None
            self._tools = {}

    tools = {}

    def capture_tool(atomic=True):
        def decorator(func):
            tools[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", capture_tool):
        _Host().register_browser_tools()
    return tools["fetch_page"]


def test_http_error_names_the_requested_url_not_the_pinned_ip(monkeypatch):
    url = "https://www.nps.gov/rain/learn/nature/weather.htm"
    origin = _FakeOrigin(
        {"/rain/learn/nature/weather.htm": (404, "Not Found", {}, b"missing")}
    )
    origin.install(monkeypatch)
    client = WebClient()
    try:
        with patch.object(client, "_rate_limit_wait"):
            resp = client.get(url)
            with pytest.raises(requests.HTTPError) as excinfo:
                resp.raise_for_status()
            tool_result = _fetch_page_tool(client)(url)
    finally:
        client.close()

    assert str(excinfo.value) == f"404 Client Error: Not Found for url: {url}"
    assert resp.url == url
    assert resp.request.url == url
    assert f"for url: {url}" in tool_result
    _assert_no_pinned_form(tool_result)
    # Pinning and SNI are unchanged: the pinned IP is dialed, the host named.
    assert origin.dialed[0] == (
        f"https://www.nps.gov@{_PINNED_IP}:443/rain/learn/nature/weather.htm",
        "www.nps.gov",
    )


def test_redirect_result_names_the_requested_urls_not_the_pinned_ip(monkeypatch):
    url = "https://www.nps.gov/old"
    origin = _FakeOrigin(
        {
            "/old": (301, "Moved Permanently", {"Location": "/new"}, b""),
            "/new": (200, "OK", {"Content-Type": "text/plain"}, b"rain today"),
        }
    )
    origin.install(monkeypatch)
    client = WebClient()
    try:
        with patch.object(client, "_rate_limit_wait"):
            resp = client.get(url)
            tool_result = _fetch_page_tool(client)(url)
    finally:
        client.close()

    assert resp.status_code == 200
    assert resp.url == "https://www.nps.gov/new"
    assert "rain today" in tool_result
    _assert_no_pinned_form(tool_result)
    assert [d[0] for d in origin.dialed[:2]] == [
        f"https://www.nps.gov@{_PINNED_IP}:443/old",
        f"https://www.nps.gov@{_PINNED_IP}:443/new",
    ]


def test_download_http_error_names_the_requested_url(monkeypatch, tmp_path):
    url = "https://files.example.com/report.pdf"
    _FakeOrigin({"/report.pdf": (404, "Not Found", {}, b"")}).install(monkeypatch)
    client = WebClient()
    try:
        with patch.object(client, "_rate_limit_wait"):
            with pytest.raises(requests.HTTPError) as excinfo:
                client.download(url, str(tmp_path))
    finally:
        client.close()

    assert str(excinfo.value) == f"404 Client Error: Not Found for url: {url}"


def test_request_url_restored_when_transport_raises(monkeypatch):
    """A connection failure must not leave the pinned form on the request."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, 1, 6, "", (_PINNED_IP, port))],
    )
    adapter = PinnedIPAdapter()
    req = requests.Request("GET", "https://www.nps.gov/page").prepare()
    with patch.object(
        PinnedIPAdapter.__bases__[0],
        "send",
        side_effect=requests.ConnectionError("refused"),
    ):
        with pytest.raises(requests.ConnectionError):
            adapter.send(req)

    assert req.url == "https://www.nps.gov/page"
