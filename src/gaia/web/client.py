# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lightweight HTTP client for web content extraction.

Includes ``PinnedIPAdapter`` — an ``HTTPAdapter`` that resolves the
hostname once and rewrites the request URL to the pinned IP. This closes
the DNS-rebind TOCTOU window between ``validate_url`` (which calls
``getaddrinfo``) and the actual TCP connection (where ``requests`` /
``urllib3`` would resolve a second time).
"""

import http.client
import ipaddress
import os
import random
import re
import socket
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

from gaia.logger import get_logger
from gaia.tool_cancellation import ToolCancelled

log = get_logger(__name__)

# Try to import BeautifulSoup with fallback
try:
    from bs4 import BeautifulSoup

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    log.debug("beautifulsoup4 not installed. HTML extraction will be limited.")


# Security constants
ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_PORTS = {22, 23, 25, 445, 3306, 5432, 6379, 27017}


#: Carrier-grade NAT (RFC 6598). ``ipaddress`` does not call it private, but
#: Tailscale addresses every machine on the user's mesh from it.
CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def _shutdown_socket(sock) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError as e:
        log.debug("Socket already closed while aborting a fetch: %s", e)


def _abort_stream(response: requests.Response) -> None:
    """Unblock a read in progress on ``response``'s socket from another thread.

    Closing the response is not enough on every platform: a thread blocked in
    ``recv`` stays blocked until the socket is shut down.
    """
    raw = getattr(response, "raw", None)
    conn_sock = getattr(getattr(raw, "_connection", None), "sock", None)
    fp = getattr(getattr(raw, "_fp", None), "fp", None)
    fp_sock = getattr(getattr(fp, "raw", fp), "_sock", None)
    for sock in {s for s in (conn_sock, fp_sock) if s is not None}:
        _shutdown_socket(sock)
    response.close()


# Thread id -> the socket that thread is connecting, handshaking or reading
# headers on, and thread id -> the deadline it is fetching under, so a deadline
# can unblock a server that trickles its TLS handshake or its headers.
_THREAD_SOCKETS: Dict[int, socket.socket] = {}
_THREAD_DEADLINES: Dict[int, "_Deadline"] = {}
_SOCKETS_LOCK = threading.Lock()

#: A read whose timeout was cut to the time left can fail a few ms before the
#: timer thread runs (or before Windows' clock ticks over); that close to the
#: deadline, the failure is the deadline.
_DEADLINE_SLACK_SECONDS = 0.25


def _track_socket(sock) -> None:
    """Register ``sock`` for this thread; abort it at once if the thread's
    deadline has already passed (it fired while DNS or connect blocked)."""
    thread_id = threading.get_ident()
    with _SOCKETS_LOCK:
        _THREAD_SOCKETS[thread_id] = sock
        deadline = _THREAD_DEADLINES.get(thread_id)
        if deadline is not None and deadline.fired.is_set():
            _shutdown_socket(sock)


def _untrack_socket(sock) -> None:
    thread_id = threading.get_ident()
    with _SOCKETS_LOCK:
        if _THREAD_SOCKETS.get(thread_id) is sock:
            del _THREAD_SOCKETS[thread_id]


def _tracked(connection_cls):
    """``connection_cls`` with its socket registered from creation through
    the header read, so a fetch deadline can unblock it."""

    class Tracked(connection_cls):
        # Both inherited from urllib3's HTTPConnection; pylint misses them.
        def _new_conn(self):
            sock = super()._new_conn()  # pylint: disable=no-member
            _track_socket(sock)
            return sock

        def getresponse(self, *args, **kwargs):
            sock = self.sock
            _track_socket(sock)
            try:
                return super().getresponse(*args, **kwargs)  # pylint: disable=no-member
            finally:
                _untrack_socket(sock)

    Tracked.__name__ = Tracked.__qualname__ = f"Tracked{connection_cls.__name__}"
    return Tracked


def _tracked_pools(http_pool, https_pool) -> Dict[str, type]:
    return {
        scheme: type(
            f"Tracked{pool.__name__}",
            (pool,),
            {"ConnectionCls": _tracked(pool.ConnectionCls)},
        )
        for scheme, pool in (("http", http_pool), ("https", https_pool))
    }


_TRACKED_POOLS = _tracked_pools(HTTPConnectionPool, HTTPSConnectionPool)
_TRACKED_SOCKS_POOLS: Dict[str, type] = {}


def _pools_for(manager) -> Dict[str, type]:
    """Tracked pool classes matching ``manager``'s own (plain, or SOCKS)."""
    try:
        from urllib3.contrib.socks import (
            SOCKSHTTPConnectionPool,
            SOCKSHTTPSConnectionPool,
            SOCKSProxyManager,
        )
    except ImportError:
        return _TRACKED_POOLS  # PySocks absent: no SOCKS manager can exist
    if not isinstance(manager, SOCKSProxyManager):
        return _TRACKED_POOLS
    if not _TRACKED_SOCKS_POOLS:
        _TRACKED_SOCKS_POOLS.update(
            _tracked_pools(SOCKSHTTPConnectionPool, SOCKSHTTPSConnectionPool)
        )
    return _TRACKED_SOCKS_POOLS


class FetchDeadlineExceeded(requests.exceptions.Timeout):
    """A fetch or download ran past its overall deadline.

    The per-request ``timeout`` bounds each socket read, so a server that sends
    a byte just inside it, again and again, never trips it. This bounds the
    whole operation: every redirect hop, the connection and the body.
    """


class _Deadline:
    """One overall deadline for a fetch or download, enforced by a timer.

    When it passes, the timer shuts down whatever socket the calling thread is
    blocked on (handshake, headers or body), so the blocked read returns at
    once. A socket or response that appears after it fired is aborted on
    arrival.
    """

    def __init__(self, seconds: float, url: str, what: str) -> None:
        self.seconds = seconds
        self.url = url
        self.what = what
        self.received = 0
        self.fired = threading.Event()
        self._at = time.monotonic() + seconds
        self._thread_id = threading.get_ident()
        self._response = None
        self._done = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def __enter__(self) -> "_Deadline":
        with _SOCKETS_LOCK:
            _THREAD_DEADLINES[self._thread_id] = self
            _THREAD_SOCKETS.pop(self._thread_id, None)
        self._timer.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._timer.cancel()
        # cancel() cannot stop a _fire already running; _done makes it a no-op,
        # so it never touches a socket this thread's next request registered.
        with _SOCKETS_LOCK:
            self._done = True
            if _THREAD_DEADLINES.get(self._thread_id) is self:
                del _THREAD_DEADLINES[self._thread_id]

    def expired(self) -> bool:
        return (
            self.fired.is_set()
            or time.monotonic() >= self._at - _DEADLINE_SLACK_SECONDS
        )

    def remaining(self) -> float:
        left = self._at - time.monotonic()
        if left <= 0 or self.fired.is_set():
            raise self.error()
        return left

    def watch(self, response: requests.Response) -> None:
        with _SOCKETS_LOCK:
            self._response = response
            if self.fired.is_set():
                _abort_stream(response)

    def error(self) -> FetchDeadlineExceeded:
        got = f" after receiving {self.received} bytes" if self.received else ""
        return FetchDeadlineExceeded(
            f"The {self.what} of {self.url} did not finish within "
            f"{self.seconds:.0f}s{got}, so it was stopped. The server is sending "
            "too slowly or not at all. Try again later, or use a different "
            "source."
        )

    def _fire(self) -> None:
        with _SOCKETS_LOCK:
            if self._done:
                return
            self.fired.set()
            sock = _THREAD_SOCKETS.get(self._thread_id)
            if sock is not None:
                _shutdown_socket(sock)
            response = self._response
            if response is not None:
                _abort_stream(response)


#: What a read cut short by the deadline (its timeout, or the timer's
#: shutdown) raises. Anything else, a blocked address or a bug, is not
#: relabelled as a slow server.
_TRANSPORT_ERRORS = (
    requests.exceptions.RequestException,
    OSError,
    http.client.HTTPException,
    urllib3.exceptions.HTTPError,
)


def _is_deadline_failure(
    error: BaseException, deadline: "_Deadline", reading_body: bool = False
) -> bool:
    if isinstance(error, _TRANSPORT_ERRORS):
        return deadline.expired()
    # The timer closing a response mid-read can surface as one of these from
    # inside http.client, but only while a body is being read.
    return (
        reading_body
        and isinstance(error, (AttributeError, ValueError))
        and deadline.fired.is_set()
    )


def _cap_timeout(timeout, remaining: float):
    """``timeout`` (a number or a ``(connect, read)`` pair) capped at ``remaining``.

    Any other form (a ``urllib3.Timeout``) is passed through; the deadline's
    timer still bounds the fetch.
    """
    if isinstance(timeout, tuple):
        return tuple(remaining if t is None else min(t, remaining) for t in timeout)
    if timeout is None:
        return remaining
    if isinstance(timeout, (int, float)):
        return min(timeout, remaining)
    return timeout


def _is_blocked_ip(ip: "ipaddress._BaseAddress") -> bool:
    """Return True if ``ip`` points at a private/internal range we must not fetch."""
    # ::ffff:10.0.0.1 reaches 10.0.0.1 -- judge the IPv4 address it carries.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or (ip.version == 4 and ip in CGNAT_NETWORK)
    )


def _loopback_allowed_hosts() -> frozenset:
    """Hostnames the operator has explicitly opted to allow on loopback.

    Off by default. ``GAIA_WEB_ALLOWED_HOSTS`` (comma-separated) is a trusted
    automated-testing affordance — the eval's fixture HTTP server runs on
    127.0.0.1 and the SSRF guard would otherwise (correctly) refuse it. It only
    ever permits *loopback* for a host the operator named; every other address
    that name resolves to still goes through the block list. Nothing about the
    default production posture changes.
    """
    raw = os.environ.get("GAIA_WEB_ALLOWED_HOSTS", "")
    return frozenset(h.strip() for h in raw.split(",") if h.strip())


def _assert_ip_allowed(ip_str: str, hostname: str) -> None:
    """Raise ValueError if ``ip_str`` is a private/reserved address.

    The single authority for "is this IP safe to connect to". Both
    ``WebClient.validate_url`` (pre-flight DNS check) and
    ``PinnedIPAdapter`` (the IP it actually connects to) route through this,
    so a DNS rebind that slips a private IP past the pre-flight lookup is
    still caught at connect time on the *exact* address being dialed.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # Not parseable as an IP — treat as unsafe rather than letting an
        # unvalidated value reach the socket layer (fail loudly).
        raise ValueError(
            f"Blocked: {hostname} resolved to unparseable address {ip_str!r}."
        )
    # Opt-in affordance: loopback ONLY, for a host the operator named. Not
    # `is_private` — that covers 169.254.0.0/16, so it would reopen the cloud
    # metadata endpoint to any allowlisted name.
    if hostname in _loopback_allowed_hosts() and ip.is_loopback:
        return
    if _is_blocked_ip(ip):
        raise ValueError(
            f"Blocked: {hostname} resolves to private/reserved IP {ip}. "
            "Cannot fetch internal network addresses."
        )


# Tags to remove during text extraction
REMOVE_TAGS = [
    "script",
    "style",
    "nav",
    "footer",
    "aside",
    "header",
    "noscript",
    "iframe",
    "svg",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "meta",
    "link",
]


class PinnedIPAdapter(HTTPAdapter):
    """HTTPAdapter that pins the resolved IP address for a hostname.

    On ``send()``, the adapter resolves the request hostname once via
    ``socket.getaddrinfo``, replaces the request URL netloc with the
    resolved IP:port, and sets the ``Host`` header to the original
    hostname. The resolved IP is cached per ``(host, port)`` tuple so
    subsequent requests to the same origin reuse the same IP — preventing
    DNS-rebind attacks between ``WebClient.validate_url`` and the actual
    TCP connect.

    Crucially, the pinned IP is itself validated (``_assert_ip_allowed``)
    before it is cached or connected to. ``validate_url`` runs a *separate*
    pre-flight ``getaddrinfo``; an attacker controlling DNS could answer that
    lookup with a public IP and answer the adapter's lookup with a private
    one. Validating the exact address the adapter is about to dial closes
    that residual rebind window for BOTH http and https.

    For HTTPS the connect address and the TLS identity are decoupled, so
    pinning the IP does not break SNI-based virtual hosting (most of the
    web). The original hostname is carried in the URL's userinfo section
    (``originalhostname@pinnedip:port``) and
    ``build_connection_pool_key_attributes`` — the extension point
    ``requests`` documents for exactly this — turns it into
    ``server_hostname``, which urllib3 uses both as the ClientHello SNI and
    as the name OpenSSL verifies the certificate against, so a certificate
    valid only for the bare IP is never accepted. It is a ``PoolKey`` field,
    so two hostnames that resolve to the same IP get distinct connection
    pools rather than sharing one.

    ``assert_hostname`` is deliberately NOT set: it would move name checking
    out of the handshake into urllib3's post-hoc matcher by setting
    ``check_hostname = False`` on the SSL context — which ``requests`` shares
    process-wide on some versions, silently weakening every other caller.

    The socket still connects to the validated, pinned IP: the URL host is
    the IP, and ``server_hostname`` changes only what is *named* in the
    handshake, never what is dialed. The SSRF block is unaffected — it fires
    on the validated IP before any bytes are sent.
    """

    def __init__(self, *args, **kwargs):
        # Without this hook the SNI override below is never called and TLS
        # silently names the pinned IP again. A capability check rather than a
        # version parse: requests 2.32.2 also routes through
        # get_connection_with_tls_context without providing the hook.
        if not hasattr(HTTPAdapter, "build_connection_pool_key_attributes"):
            raise RuntimeError(
                "PinnedIPAdapter needs requests>=2.32.3 to send the correct TLS "
                f"SNI while pinning the IP, but requests {requests.__version__} "
                "is installed. Without it every HTTPS fetch names the pinned IP "
                "in the handshake and CDN-fronted hosts reject the connection. "
                "Upgrade with: pip install --upgrade 'requests>=2.32.3'"
            )
        super().__init__(*args, **kwargs)
        self._pinned_cache: Dict[Tuple[str, int], str] = {}

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        # Lets a fetch deadline reach a socket still connecting or reading
        # headers.
        self.poolmanager.pool_classes_by_scheme = dict(_TRACKED_POOLS)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        manager = super().proxy_manager_for(proxy, **proxy_kwargs)
        # The same for requests sent through a proxy; a SOCKS proxy keeps its
        # own pool classes, tracked the same way.
        manager.pool_classes_by_scheme = dict(_pools_for(manager))
        return manager

    def _resolve_first_ip(self, host: str, port: int) -> str:
        key = (host, port)
        if key in self._pinned_cache:
            return self._pinned_cache[key]

        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        if not infos:
            raise OSError(f"getaddrinfo returned no addresses for {host}:{port}")

        ip = infos[0][4][0]  # sockaddr[0] of the first result
        # Validate the EXACT IP we are about to pin & connect to. validate_url
        # did a pre-flight getaddrinfo, but that was a separate lookup — a DNS
        # rebind could hand validate_url a public IP and hand us a private one.
        # Re-check here so the address actually dialed is always safe; cache
        # only after it passes so a poisoned answer is never reused.
        _assert_ip_allowed(ip, host)
        self._pinned_cache[key] = ip
        return ip

    @staticmethod
    def _format_ip_for_url(ip: str) -> str:
        """Bracket IPv6 literals for use in a URL authority."""
        return f"[{ip}]" if ipaddress.ip_address(ip).version == 6 else ip

    @staticmethod
    def _strip_tls_host(url: str) -> Tuple[str, "str | None"]:
        """Extract the original hostname stashed in URL userinfo.

        Returns ``(clean_url_without_userinfo, hostname_or_None)``.
        """
        parsed = urlparse(url)
        if not parsed.username:
            return url, None
        tls_hostname = parsed.username
        # Rebuild netloc without userinfo
        host_part = parsed.hostname
        if host_part is None:
            raise ValueError("Pinned URL is missing a host")
        host_part = PinnedIPAdapter._format_ip_for_url(host_part)
        if parsed.port:
            netloc = f"{host_part}:{parsed.port}"
        else:
            netloc = host_part
        clean = urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path or "",
                parsed.params or "",
                parsed.query or "",
                parsed.fragment or "",
            )
        )
        return clean, tls_hostname

    def send(self, request: requests.PreparedRequest, **kwargs) -> requests.Response:
        original_url = request.url
        parsed = urlparse(original_url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        if not host:
            return super().send(request, **kwargs)

        pinned_ip = self._resolve_first_ip(host, port)
        url_ip = self._format_ip_for_url(pinned_ip)

        if parsed.scheme == "https":
            # Carry the original hostname in userinfo; it is read back in
            # build_connection_pool_key_attributes to set the TLS SNI and
            # the certificate-name assertion. Never sent on the wire.
            new_netloc = f"{host}@{url_ip}:{port}"
        else:
            new_netloc = f"{url_ip}:{port}"

        request.url = urlunparse(
            (
                parsed.scheme,
                new_netloc,
                parsed.path or "",
                parsed.params or "",
                parsed.query or "",
                parsed.fragment or "",
            )
        )
        request.headers.setdefault("Host", host)

        # The pinned form only steers the connection. Restore the real URL so
        # HTTPError text, response.url and cookie domains never show host@ip.
        try:
            response = super().send(request, **kwargs)
        finally:
            request.url = original_url
        response.url = original_url
        return response

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        """Pin the connect address to the IP while keeping the TLS identity.

        ``requests`` documents this as the hook for customising urllib3 pool
        arguments. ``host_params["host"]`` is already the pinned IP (urlparse
        drops the userinfo), so the only thing left is to name the real host
        in TLS terms via ``server_hostname``.
        """
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        _, tls_hostname = self._strip_tls_host(request.url)
        if tls_hostname and host_params.get("scheme") == "https":
            pool_kwargs["server_hostname"] = tls_hostname
        return host_params, pool_kwargs


# -- Search pacing and block detection -----------------------------------------

#: Statuses DuckDuckGo uses to turn away automated traffic. Any other non-200
#: is a real failure and is not retried.
SEARCH_BLOCK_STATUSES = frozenset({202, 403, 429, 503})

#: Markup only DDG's bot-check page carries; a 200 with these holds no results.
_DDG_CHALLENGE_MARKERS = (
    "anomaly-modal",
    'id="challenge-form"',
    "bots use duckduckgo too",
)

_DDG_ENDPOINTS = (
    ("html", "https://html.duckduckgo.com/html/"),
    ("lite", "https://lite.duckduckgo.com/lite/"),
)


class SearchUnavailableError(RuntimeError):
    """Every search endpoint turned the query away; waiting may fix it.

    ``retry_after`` is the seconds to wait before searching again.
    """

    def __init__(self, message: str, retry_after: float, status: Optional[int]):
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status


class _SearchGate:
    """Process-wide pacing for search requests, shared by every ``WebClient``.

    Agents fire searches in parallel; each caller reserves the next free slot,
    so requests leave at least ``min_interval`` apart. A block signal pushes
    every caller back until ``blocked_until``.
    """

    def __init__(self, min_interval: float, sleep=time.sleep) -> None:
        self.min_interval = min_interval
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._blocked_until = 0.0

    def blocked_for(self) -> float:
        """Seconds until searching is allowed again after a block, or 0."""
        with self._lock:
            return max(0.0, self._blocked_until - time.monotonic())

    #: Longest single sleep while a stop check is wired, so Stop ends a wait promptly.
    STOP_POLL_SECONDS = 0.25

    def wait_turn(self, should_stop: Optional[Callable[[], bool]] = None) -> None:
        """Wait for this caller's slot; raise ``ToolCancelled`` if *should_stop* fires."""
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot, self._blocked_until)
            self._next_slot = slot + self.min_interval
        remaining = slot - now
        if should_stop is None:
            if remaining > 0:
                self._sleep(remaining)
            return
        while True:
            if should_stop():
                raise ToolCancelled(
                    "web search was stopped while waiting out a DuckDuckGo rate limit"
                )
            if remaining <= 0:
                return
            step = min(remaining, self.STOP_POLL_SECONDS)
            self._sleep(step)
            remaining -= step

    def back_off(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)


_SEARCH_GATE = _SearchGate(min_interval=1.5)


def _retry_after_seconds(response: requests.Response) -> Optional[float]:
    """The ``Retry-After`` header in seconds (delta or HTTP-date), if present."""
    value = (response.headers or {}).get("Retry-After")
    if not value:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        log.debug("Unparseable Retry-After header: %r", value)
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _ddg_block_reason(response: requests.Response) -> Optional[str]:
    """Why DDG refused to answer, or ``None`` when the page holds real results."""
    if response.status_code in SEARCH_BLOCK_STATUSES:
        return f"HTTP {response.status_code}"
    if response.status_code == 200:
        page = (response.text or "").lower()
        if any(marker in page for marker in _DDG_CHALLENGE_MARKERS):
            return "a bot-check page (HTTP 200)"
    return None


def _unwrap_ddg_href(href: str) -> str:
    """DDG wraps result URLs in a redirect; return the real target."""
    if "uddg=" in href:
        params = parse_qs(urlparse(href).query)
        if "uddg" in params:
            return params["uddg"][0]
    return href


class WebClient:
    """Lightweight HTTP client for web content extraction.

    Uses requests for HTTP and BeautifulSoup for HTML parsing.
    Handles rate limiting, timeouts, size limits, SSRF prevention,
    and content extraction.

    This is NOT a mixin or tool -- it is an internal utility used by
    BrowserToolsMixin. Follows the service-class pattern (like
    FileSystemIndexService and ScratchpadService).
    """

    DEFAULT_TIMEOUT = 30
    #: Whole fetch, redirects and body included. At the 10 MB body cap this
    #: still allows a ~85 KB/s link.
    DEFAULT_FETCH_DEADLINE = 120
    #: Whole download; a 100 MB file needs ~55 KB/s to finish inside it.
    DEFAULT_DOWNLOAD_DEADLINE = 1800
    DEFAULT_MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10 MB
    DEFAULT_MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024  # 100 MB
    DEFAULT_USER_AGENT = "GAIA-Agent/0.15 (https://github.com/amd/gaia)"
    MAX_REDIRECTS = 5
    MIN_REQUEST_INTERVAL = 1.0  # seconds between requests per domain

    def __init__(
        self,
        timeout: int = None,
        max_response_size: int = None,
        max_download_size: int = None,
        user_agent: str = None,
        rate_limit: float = None,
        fetch_deadline: float = None,
        download_deadline: float = None,
    ):
        self._timeout = timeout or self.DEFAULT_TIMEOUT
        self._fetch_deadline = fetch_deadline or self.DEFAULT_FETCH_DEADLINE
        self._download_deadline = download_deadline or self.DEFAULT_DOWNLOAD_DEADLINE
        self._max_response_size = max_response_size or self.DEFAULT_MAX_RESPONSE_SIZE
        self._max_download_size = max_download_size or self.DEFAULT_MAX_DOWNLOAD_SIZE
        self._user_agent = user_agent or self.DEFAULT_USER_AGENT
        self._rate_limit = rate_limit or self.MIN_REQUEST_INTERVAL
        self._domain_last_request: dict = {}  # Per-domain rate limiting
        self._session = requests.Session()
        # Mount PinnedIPAdapter to close the DNS-rebind TOCTOU window
        # between validate_url() and the actual TCP connect.
        _adapter = PinnedIPAdapter()
        self._session.mount("https://", _adapter)
        self._session.mount("http://", _adapter)
        self._session.headers.update(
            {
                "User-Agent": self._user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
        )

    def close(self):
        """Close the HTTP session."""
        if self._session:
            self._session.close()

    # -- URL Validation (SSRF Prevention) ------------------------------------

    def validate_url(self, url: str) -> str:
        """Validate URL is safe to fetch. Raises ValueError if not.

        Checks:
        1. Scheme is http or https only
        2. Port is not in blocked set
        3. Resolved IP is not private/loopback/link-local/reserved
        """
        parsed = urlparse(url)

        if parsed.scheme not in ALLOWED_SCHEMES:
            raise ValueError(
                f"Blocked URL scheme: {parsed.scheme}. Only http/https allowed."
            )

        hostname = parsed.hostname
        if not hostname:
            raise ValueError(f"Invalid URL: no hostname in {url}")

        port = parsed.port
        if port and port in BLOCKED_PORTS:
            raise ValueError(f"Blocked port: {port}")

        # Resolve and validate IP
        self._validate_host_ip(hostname)

        return url

    def _validate_host_ip(self, hostname: str) -> None:
        """Resolve hostname and check IP is not private/internal."""
        allowed = hostname in _loopback_allowed_hosts()
        try:
            results = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            raise ValueError(f"Cannot resolve hostname: {hostname}")

        for _family, _, _, _, sockaddr in results:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            # Opt-in affordance: loopback ONLY, for a named host (see
            # ``_assert_ip_allowed`` for why not ``is_private``).
            if allowed and ip.is_loopback:
                continue

            if _is_blocked_ip(ip):
                raise ValueError(
                    f"Blocked: {hostname} resolves to private/reserved IP {ip}. "
                    "Cannot fetch internal network addresses."
                )

    # -- Rate Limiting -------------------------------------------------------

    def _rate_limit_wait(self, domain: str) -> None:
        """Wait if needed to respect per-domain rate limit.

        Uses ``time.monotonic()`` instead of ``time.time()`` so NTP
        adjustments and DST transitions cannot produce negative elapsed
        values or accidentally disable throttling.
        """
        now = time.monotonic()
        last = self._domain_last_request.get(domain, 0)
        elapsed = now - last
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._domain_last_request[domain] = time.monotonic()

    # -- Deadlines -----------------------------------------------------------

    def _send_hop(self, deadline: _Deadline, send, caller_timeout=None):
        """Send one request inside ``deadline``; ``send`` takes the timeout."""
        timeout = _cap_timeout(
            self._timeout if caller_timeout is None else caller_timeout,
            deadline.remaining(),
        )
        try:
            response = send(timeout)
        except Exception as e:
            if _is_deadline_failure(e, deadline):
                raise deadline.error() from e
            raise
        deadline.watch(response)
        return response

    @staticmethod
    def _read_chunks(response: requests.Response, deadline: _Deadline):
        """Yield ``response``'s body, stopping the read when ``deadline`` fires.

        A chunk read blocks until 8 KB arrive, which at a byte per read timeout
        is hours, so the deadline's timer unblocks the socket mid-read.
        """
        try:
            for chunk in response.iter_content(chunk_size=8192):
                if deadline.fired.is_set():
                    break
                deadline.received += len(chunk)
                yield chunk
        except Exception as e:
            if _is_deadline_failure(e, deadline, reading_body=True):
                raise deadline.error() from e
            raise
        # An unblocked socket can also read as a clean, truncated end.
        if deadline.fired.is_set():
            raise deadline.error()

    # -- HTTP Methods --------------------------------------------------------

    def get(self, url: str, **kwargs) -> requests.Response:
        """HTTP GET with SSRF validation, rate limiting, manual redirect following.

        Returns the final Response object after following redirects.
        Raises ValueError for blocked URLs, requests.RequestException for HTTP errors.
        """
        return self._request("GET", url, **kwargs)

    def post(self, url: str, data: dict = None, **kwargs) -> requests.Response:
        """HTTP POST with SSRF validation and rate limiting."""
        return self._request("POST", url, data=data, **kwargs)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Internal request method with SSRF checks and manual redirect following.

        Returns a response whose ``content`` / ``text`` are already guaranteed
        to be within ``self._max_response_size`` bytes — we stream the body
        and cap it so a gzip bomb (Content-Length: 100 → decompresses to
        100 GB) can't OOM the process by the time a caller touches
        ``response.text``.
        """
        self.validate_url(url)

        domain = urlparse(url).hostname
        self._rate_limit_wait(domain)
        # Started after the first rate-limit wait. A redirect to a new domain
        # waits up to rate_limit (1 s) inside it, which the deadline absorbs.
        with _Deadline(self._fetch_deadline, url, "fetch") as deadline:
            return self._follow_redirects(method, url, domain, deadline, **kwargs)

    def _follow_redirects(
        self, method: str, url: str, domain: str, deadline: _Deadline, **kwargs
    ) -> requests.Response:
        # Disable auto-redirects -- we follow manually to validate each hop.
        # Force streaming so we can cap decompressed body size before it
        # reaches memory (requests would otherwise eagerly decode gzip).
        caller_timeout = kwargs.pop("timeout", None)
        kwargs["allow_redirects"] = False
        kwargs["stream"] = True

        current_url = url
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            response = self._send_hop(
                deadline,
                lambda timeout, hop_url=current_url: self._session.request(
                    method, hop_url, timeout=timeout, **kwargs
                ),
                caller_timeout,
            )

            # Pre-check declared Content-Length (still useful — rejects cheap
            # DoS before we stream anything).
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > self._max_response_size:
                response.close()
                raise ValueError(
                    f"Response too large: {int(content_length)} bytes "
                    f"(max: {self._max_response_size})"
                )

            # Not a redirect -- consume body with a hard byte cap and return.
            if response.status_code not in (301, 302, 303, 307, 308):
                # Read first: apparent_encoding reads response.content, which
                # would pull the body outside the deadline-bounded read.
                self._consume_body_capped(response, deadline)
                # Use apparent_encoding for better charset handling
                if response.encoding and response.apparent_encoding:
                    if (
                        response.encoding.lower() == "iso-8859-1"
                        and response.apparent_encoding.lower() != "iso-8859-1"
                    ):
                        response.encoding = response.apparent_encoding
                return response

            # Follow redirect -- validate the new URL
            redirect_url = response.headers.get("Location")
            if not redirect_url:
                # No Location header; cap body just like a normal response
                # before returning.
                self._consume_body_capped(response, deadline)
                return response

            # Resolve relative redirects
            redirect_url = urljoin(current_url, redirect_url)

            # Validate redirect target (SSRF check on each hop). If this
            # raises (e.g. the Location header tries to send us to a private
            # IP), close the current streamed response FIRST so we don't
            # leak the connection / file descriptor on the validation error.
            try:
                self.validate_url(redirect_url)
            except Exception:
                response.close()
                raise

            # Close the prior streamed response — we're not reading its body
            # (redirects have empty / informational bodies anyway).
            response.close()

            # Rate limit for new domain
            new_domain = urlparse(redirect_url).hostname
            if new_domain != domain:
                self._rate_limit_wait(new_domain)
                domain = new_domain

            current_url = redirect_url
            # After redirect, always use GET (except for 307/308)
            if response.status_code in (301, 302, 303):
                method = "GET"
                kwargs.pop("data", None)

            log.debug(
                f"Following redirect ({redirect_count + 1}/{self.MAX_REDIRECTS}): "
                f"{current_url}"
            )

        raise ValueError(f"Too many redirects (max {self.MAX_REDIRECTS})")

    def _consume_body_capped(
        self,
        response: requests.Response,
        deadline: "_Deadline",
    ) -> None:
        """Read ``response`` body in chunks up to ``self._max_response_size``.

        When ``deadline`` fires the read is abandoned with
        :class:`FetchDeadlineExceeded`.

        Because we forced ``stream=True`` in ``_request``, ``response.content``
        would otherwise lazily fetch everything on first access. This replaces
        ``response._content`` with the capped payload so that downstream
        ``response.text`` / ``response.content`` observe the cap regardless
        of server Content-Length honesty. If the body exceeds the cap we
        raise — matching the behaviour of the declared-Content-Length check.
        """
        # If body was already materialized (caller passed a preloaded
        # response — uncommon, but possible in tests), leave it alone.
        if (
            getattr(response, "_content_consumed", False)
            and response._content is not False
        ):
            return

        chunks = []
        total = 0
        for chunk in self._read_chunks(response, deadline):
            if not chunk:
                continue
            total += len(chunk)
            if total > self._max_response_size:
                response.close()
                raise ValueError(
                    f"Response body exceeds max size: {total} bytes "
                    f"(max: {self._max_response_size}). "
                    "Possible decompression bomb or server mis-reporting "
                    "Content-Length."
                )
            chunks.append(chunk)
        response._content = b"".join(chunks)
        response._content_consumed = True

    # -- HTML Parsing & Extraction -------------------------------------------

    def parse_html(self, html: str) -> "BeautifulSoup":
        """Parse HTML content with BeautifulSoup."""
        if not BS4_AVAILABLE:
            from gaia.agents.install_hints import (  # pylint: disable=import-outside-toplevel
                missing_package_hint,
            )

            raise ImportError(
                "beautifulsoup4 is required for HTML parsing. "
                + missing_package_hint("beautifulsoup4")
            )
        # Try lxml first (faster), fall back to html.parser (stdlib)
        try:
            return BeautifulSoup(html, "lxml")
        except Exception:
            return BeautifulSoup(html, "html.parser")

    def extract_text(self, soup: "BeautifulSoup", max_length: int | None = 5000) -> str:
        """Extract readable text from parsed HTML.

        Removes script/style/nav/footer tags, preserves heading hierarchy,
        paragraph breaks, and list structure. Collapses whitespace.
        Set max_length=None when the caller archives the complete extraction.
        """
        # Remove unwanted tags
        for tag_name in REMOVE_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        lines = []

        for element in soup.find_all(
            [
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "p",
                "li",
                "td",
                "th",
                "pre",
                "blockquote",
            ]
        ):
            text = element.get_text(strip=True)
            if not text:
                continue

            tag_name = element.name
            if tag_name == "h1":
                lines.append(f"\n{text}")
                lines.append("=" * min(len(text), 60))
            elif tag_name == "h2":
                lines.append(f"\n{text}")
                lines.append("-" * min(len(text), 60))
            elif tag_name in ("h3", "h4", "h5", "h6"):
                lines.append(f"\n### {text}")
            elif tag_name == "li":
                lines.append(f"  - {text}")
            elif tag_name in ("td", "th"):
                continue  # Tables handled separately
            else:
                lines.append(text)

        # If structured extraction got too little, fall back to get_text
        result = "\n".join(lines).strip()
        if len(result) < 100:
            result = soup.get_text(separator="\n", strip=True)

        # Collapse multiple blank lines
        result = re.sub(r"\n{3,}", "\n\n", result)

        # Truncate at word boundary
        if max_length is not None and len(result) > max_length:
            truncated = result[:max_length]
            last_space = truncated.rfind(" ")
            if last_space > max_length * 0.8:
                truncated = truncated[:last_space]
            result = truncated + "\n\n... (truncated)"

        return result

    def extract_tables(self, soup: "BeautifulSoup") -> list:
        """Extract HTML tables as list of list-of-dicts.

        Each table becomes a list of dicts where keys are from the header row.
        Skips tables with fewer than 2 rows (likely layout tables).
        Returns: [{"table_name": str, "data": [{"col": "val", ...}, ...]}]
        """
        results = []

        for table_idx, table in enumerate(soup.find_all("table")):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue  # Skip layout tables

            # Get headers from first row or thead
            thead = table.find("thead")
            if thead:
                header_row = thead.find("tr")
            else:
                header_row = rows[0]

            headers = []
            for cell in header_row.find_all(["th", "td"]):
                headers.append(cell.get_text(strip=True))

            if not headers:
                continue

            # Get data rows — when <thead> is present, look for <tbody>; if
            # there is no <tbody> (valid HTML — rows can be direct children of
            # <table>), fall back to rows[1:] so we don't crash iterating None.
            if thead:
                tbody = table.find("tbody", recursive=False)
                if tbody is not None:
                    data_rows = tbody.find_all("tr")
                else:
                    data_rows = rows[1:]
            else:
                data_rows = rows[1:]

            table_data = []
            for row in data_rows:
                cells = row.find_all(["td", "th"])
                row_dict = {}
                for i, cell in enumerate(cells):
                    key = headers[i] if i < len(headers) else f"col_{i}"
                    row_dict[key] = cell.get_text(strip=True)
                if row_dict:
                    table_data.append(row_dict)

            if table_data:
                # Try to get table caption/name
                caption = table.find("caption")
                table_name = (
                    caption.get_text(strip=True)
                    if caption
                    else f"Table {table_idx + 1}"
                )

                results.append(
                    {
                        "table_name": table_name,
                        "data": table_data,
                    }
                )

        return results

    def extract_links(self, soup: "BeautifulSoup", base_url: str) -> list:
        """Extract all links with text and resolved URLs.

        Returns: [{"text": str, "url": str}]
        """
        links = []
        seen_urls = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            text = a_tag.get_text(strip=True)

            # Skip empty, anchor-only, and javascript links
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            # Resolve relative URLs
            full_url = urljoin(base_url, href)

            if full_url not in seen_urls:
                seen_urls.add(full_url)
                links.append(
                    {
                        "text": text or "(no text)",
                        "url": full_url,
                    }
                )

        return links

    # -- File Download -------------------------------------------------------

    def download(
        self,
        url: str,
        save_dir: str,
        filename: str = None,
        max_size: int = None,
        on_path_resolved=None,
    ) -> dict:
        """Download a file from URL to local disk.

        Streams to disk to handle large files. Returns dict with
        path, size, and content_type.

        Refuses to overwrite an existing file: the destination is checked (and
        handed to ``on_path_resolved`` for policy screening) *before* any bytes
        are written, and the body is streamed to a sibling ``.part`` file that
        is renamed into place only on success. The filename can come from an
        attacker-controlled ``Content-Disposition`` header, so a failed policy
        check must not have already clobbered the user's file. The check is
        repeated immediately before the rename, leaving only the sub-microsecond
        window between that check and ``os.replace`` in which a file created by
        another local process would still be replaced.

        Args:
            url: URL to download
            save_dir: Directory to save file in
            filename: Override filename (default: from URL/headers)
            max_size: Max file size in bytes (default: self._max_download_size)
            on_path_resolved: Optional callable invoked with the resolved
                destination ``Path`` before the file is created. Raise from it
                to refuse the download.
        """
        max_size = max_size or self._max_download_size

        self.validate_url(url)
        domain = urlparse(url).hostname
        self._rate_limit_wait(domain)
        with _Deadline(self._download_deadline, url, "download") as deadline:
            return self._download_within(
                url, save_dir, filename, max_size, on_path_resolved, deadline
            )

    def _download_within(
        self, url, save_dir, filename, max_size, on_path_resolved, deadline
    ) -> dict:
        # Stream the download
        response = self._send_hop(
            deadline,
            lambda timeout: self._session.get(
                url, stream=True, timeout=timeout, allow_redirects=False
            ),
        )

        # Handle redirects manually for downloads too
        redirect_count = 0
        while response.status_code in (301, 302, 303, 307, 308):
            redirect_count += 1
            if redirect_count > self.MAX_REDIRECTS:
                raise ValueError(f"Too many redirects (max {self.MAX_REDIRECTS})")
            redirect_url = response.headers.get("Location")
            if not redirect_url:
                break
            redirect_url = urljoin(url, redirect_url)
            self.validate_url(redirect_url)
            response.close()
            response = self._send_hop(
                deadline,
                lambda timeout, hop_url=redirect_url: self._session.get(
                    hop_url, stream=True, timeout=timeout, allow_redirects=False
                ),
            )
            url = redirect_url

        response.raise_for_status()

        # Check content length
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > max_size:
            response.close()
            raise ValueError(
                f"File too large: {int(content_length)} bytes (max: {max_size})"
            )

        # Determine filename
        if not filename:
            # Try Content-Disposition header
            cd = response.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                # Extract filename from header
                match = re.search(r'filename[*]?=["\']?([^"\';]+)', cd)
                if match:
                    filename = match.group(1)

            if not filename:
                # Fall back to URL path
                filename = urlparse(url).path.split("/")[-1]

            if not filename:
                filename = "download"

        # Sanitize filename
        filename = self._sanitize_filename(filename)

        # Resolve save path
        save_dir = Path(save_dir).expanduser().resolve()
        save_path = save_dir / filename

        # Verify path is still within save_dir (prevent traversal). Compare
        # against `save_dir + os.sep` so ``/tmp/foo`` does not accept a
        # resolved path in ``/tmp/foobar/…`` — same defense-in-depth pattern
        # used in PathValidator.is_write_blocked.
        save_dir_prefix = str(save_dir).rstrip(os.sep) + os.sep
        resolved_save = str(save_path.resolve())
        if not (
            resolved_save == str(save_dir) or resolved_save.startswith(save_dir_prefix)
        ):
            raise ValueError(f"Path traversal detected: {filename}")

        # Read content_type BEFORE response.close() — `requests.Response`
        # caches headers but relying on a closed response for later attribute
        # access is fragile (future requests versions may clear them).
        content_type = response.headers.get("Content-Type", "unknown")

        # Refuse to clobber an existing file. The name may come straight from
        # a remote Content-Disposition header, so `report.pdf` from a hostile
        # page must not be able to replace the user's own ~/Downloads/report.pdf.
        if save_path.exists():
            response.close()
            raise ValueError(
                f"Refusing to overwrite existing file: {save_path}. "
                f"Pass an explicit filename= to download under a different name."
            )

        # Policy screening happens before the file is created, not after —
        # a blocked download must leave the filesystem untouched.
        if on_path_resolved is not None:
            try:
                on_path_resolved(save_path)
            except BaseException:
                response.close()
                raise

        # Created only after the screen, so a refused download leaves no folder.
        save_dir.mkdir(parents=True, exist_ok=True)

        # Stream to a sibling .part file and rename into place, so an
        # interrupted download never leaves a truncated file under the real
        # name. Exclusive create means a concurrent download of the same name,
        # or a .part left by a crashed one, fails loudly rather than having
        # two writers interleave into one file.
        part_path = save_path.with_name(save_path.name + ".part")
        try:
            part_file = open(part_path, "xb")
        except FileExistsError:
            response.close()
            raise ValueError(
                f"Cannot download to {save_path.name}: {part_path.name} already "
                f"exists, so another download of this name is either in flight "
                f"or was interrupted. Delete it, or pass a different filename=."
            ) from None

        # Only ever clean up the .part this call created — unlinking on any
        # failure would delete a concurrent download's file.
        downloaded = 0
        try:
            with part_file as f:
                for chunk in self._read_chunks(response, deadline):
                    downloaded += len(chunk)
                    if downloaded > max_size:
                        raise ValueError(
                            f"Download exceeded max size: {downloaded} bytes "
                            f"(max: {max_size})"
                        )
                    f.write(chunk)
            if save_path.exists():
                raise ValueError(
                    f"Refusing to overwrite existing file: {save_path}. "
                    f"It appeared while the download was in flight."
                )
            os.replace(part_path, save_path)
        except BaseException:
            part_path.unlink(missing_ok=True)
            raise
        finally:
            response.close()

        return {
            "path": str(save_path),
            "size": downloaded,
            "content_type": content_type,
            "filename": filename,
        }

    # -- Search --------------------------------------------------------------

    #: Retries after a block: exponential from this base, plus up to this much jitter.
    SEARCH_BACKOFF_BASE = 2.0
    #: A wait longer than this is not slept inside one tool call; the caller is
    #: told how long to wait instead.
    SEARCH_BACKOFF_CAP = 20.0

    def search_duckduckgo(
        self,
        query: str,
        num_results: int = 5,
        max_attempts: int = 3,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> list:
        """Search DuckDuckGo and parse results from HTML.

        Tries the HTML endpoint (html.duckduckgo.com), then the lite one
        (lite.duckduckgo.com), by POST as DDG expects. When both turn the query
        away (202/403/429/503 or a bot-check page) it waits with jittered
        exponential backoff, honouring ``Retry-After``, for up to
        ``max_attempts`` rounds. Searches from every client in the process are
        paced through one gate.

        Returns: [{"title": str, "url": str, "snippet": str}]

        ``should_stop`` is polled during pacing and backoff waits, so a user's
        Stop ends the wait instead of sitting it out.

        Raises:
            SearchUnavailableError: every attempt was turned away.
            ToolCancelled: ``should_stop`` returned True during a wait.
            RuntimeError: DDG answered another non-200 status.
        """
        if not BS4_AVAILABLE:
            raise ImportError("beautifulsoup4 is required for web search.")
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")

        pending = _SEARCH_GATE.blocked_for()
        if pending > self.SEARCH_BACKOFF_CAP:
            raise self._search_unavailable("an earlier block", pending, None)

        reason, status, wait = "", None, 0.0
        for attempt in range(max_attempts):
            hinted = None
            for name, url in _DDG_ENDPOINTS:
                _SEARCH_GATE.wait_turn(should_stop)
                response = self.post(url, data={"q": query, "b": ""})
                blocked = _ddg_block_reason(response)
                if blocked is None:
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"DuckDuckGo answered HTTP {response.status_code}, so "
                            "no results were read."
                        )
                    if reason:
                        log.warning(
                            "DuckDuckGo %s endpoint answered after an earlier "
                            "refusal (%s)",
                            name,
                            reason,
                        )
                    return self._parse_ddg(name, response.text, num_results)
                reason, status = blocked, response.status_code
                after = _retry_after_seconds(response)
                if after is not None:
                    hinted = max(hinted or 0.0, after)
                log.warning(
                    "DuckDuckGo %s endpoint refused a search (%s), attempt %d/%d",
                    name,
                    blocked,
                    attempt + 1,
                    max_attempts,
                )
            backoff = self.SEARCH_BACKOFF_BASE * 2**attempt + random.uniform(
                0, self.SEARCH_BACKOFF_BASE
            )
            wait = max(backoff, hinted or 0.0)
            _SEARCH_GATE.back_off(wait)
            if wait > self.SEARCH_BACKOFF_CAP:
                break
        raise self._search_unavailable(reason, wait, status)

    @staticmethod
    def _search_unavailable(
        reason: str, wait: float, status: Optional[int]
    ) -> SearchUnavailableError:
        return SearchUnavailableError(
            f"DuckDuckGo turned the search away ({reason}), so no results were "
            f"read. It is rate-limiting automated searches; searching again in "
            f"about {wait:.0f} seconds may work.",
            retry_after=wait,
            status=status,
        )

    def _parse_ddg(self, endpoint: str, html: str, num_results: int) -> list:
        """Results from an html- or lite-endpoint page."""
        soup = self.parse_html(html)
        results = []
        if endpoint == "lite":
            for link in soup.select("a.result-link"):
                after = link.find_next(
                    ["a", "td"], class_=["result-link", "result-snippet"]
                )
                snippet = (
                    after.get_text(strip=True)
                    if after is not None and after.name == "td"
                    else ""
                )
                results.append((link, snippet))
        else:
            for result_div in soup.select(".result"):
                title_el = result_div.select_one(".result__title a, .result__a")
                if not title_el:
                    continue
                snippet_el = result_div.select_one(".result__snippet")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                results.append((title_el, snippet))

        parsed = []
        for title_el, snippet in results:
            title = title_el.get_text(strip=True)
            href = _unwrap_ddg_href(title_el.get("href", ""))
            if title and href:
                parsed.append({"title": title, "url": href, "snippet": snippet})
            if len(parsed) >= num_results:
                break
        return parsed

    # -- Utility -------------------------------------------------------------

    # Windows reserved device names — creating a file called e.g. ``CON`` on
    # Windows opens the console device instead of a file, and ``CON.txt``
    # still resolves to the device. Avoid these even on non-Windows so
    # downloads remain portable.
    _WINDOWS_RESERVED = frozenset(
        {"CON", "PRN", "AUX", "NUL"}
        | {f"COM{i}" for i in range(1, 10)}
        | {f"LPT{i}" for i in range(1, 10)}
    )

    @staticmethod
    def _sanitize_filename(raw_name: str) -> str:
        """Sanitize filename from URL or Content-Disposition header.

        Guarantees the returned value:
        - contains no null bytes or control characters
        - has no path-separator characters (``/`` or ``\\``)
        - is not a Windows reserved device name (CON, PRN, NUL, COM1…)
        - does not start with a leading dot
        - is at most 200 bytes
        - is never the empty string
        """
        name = os.path.basename(raw_name)
        # Strip null bytes + control chars + whitespace
        name = name.replace("\x00", "").strip()
        name = re.sub(r"[\x00-\x1f]", "", name)
        # Path separators → underscores
        name = re.sub(r"[/\\]", "_", name)
        # Safe character set
        name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
        # Avoid leading dot (hidden file) and trailing dots / spaces (Windows
        # strips them on creation, which can cause unexpected collisions).
        if name.startswith("."):
            name = "_" + name
        name = name.rstrip(". ")
        # Reject Windows reserved device names (compare against the stem).
        stem = name.split(".", 1)[0].upper()
        if stem in WebClient._WINDOWS_RESERVED:
            name = "_" + name
        # Length cap
        name = name[:200]
        return name or "download"
