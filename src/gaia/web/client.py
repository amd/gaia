# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lightweight HTTP client for web content extraction.

Includes ``PinnedIPAdapter`` — an ``HTTPAdapter`` that resolves the
hostname once and rewrites the request URL to the pinned IP. This closes
the DNS-rebind TOCTOU window between ``validate_url`` (which calls
``getaddrinfo``) and the actual TCP connection (where ``requests`` /
``urllib3`` would resolve a second time).
"""

import ipaddress
import os
import re
import socket
import time
from pathlib import Path
from typing import Dict, Tuple
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter

from gaia.logger import get_logger

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
    hostname.  The resolved IP is cached per ``(host, port)`` tuple so
    subsequent requests to the same origin reuse the same IP — preventing
    DNS-rebind attacks between ``WebClient.validate_url`` and the actual
    TCP connect.

    For HTTPS, the original hostname is encoded in the URL's userinfo
    section (``originalhostname@pinnedip:port``) so that urllib3 creates
    separate connection-pool keys per original hostname.  This avoids a
    race where two threads requesting different hostnames that resolve to
    the same IP would overwrite each other's ``assert_hostname`` on a
    shared pool.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pinned_cache: Dict[Tuple[str, int], str] = {}

    def _resolve_first_ip(self, host: str, port: int) -> str:
        key = (host, port)
        if key in self._pinned_cache:
            return self._pinned_cache[key]

        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        if not infos:
            raise OSError(f"getaddrinfo returned no addresses for {host}:{port}")

        ip = infos[0][4][0]  # sockaddr[0] of the first result
        self._pinned_cache[key] = ip
        return ip

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
        parsed = urlparse(request.url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        if host:
            pinned_ip = self._resolve_first_ip(host, port)

            if parsed.scheme == "https":
                # Encode original hostname in userinfo for unique pool keys
                new_netloc = f"{host}@{pinned_ip}:{port}"
            else:
                new_netloc = f"{pinned_ip}:{port}"

            new_url = urlunparse(
                (
                    parsed.scheme,
                    new_netloc,
                    parsed.path or "",
                    parsed.params or "",
                    parsed.query or "",
                    parsed.fragment or "",
                )
            )
            request.url = new_url
            request.headers.setdefault("Host", host)

        return super().send(request, **kwargs)

    def get_connection(self, url, proxies=None):
        clean_url, tls_hostname = self._strip_tls_host(url)
        pool = super().get_connection(clean_url, proxies)
        if tls_hostname:
            pool.assert_hostname = tls_hostname
        return pool

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        original_url = request.url
        clean_url, tls_hostname = self._strip_tls_host(original_url)
        request.url = clean_url
        pool = super().get_connection_with_tls_context(
            request, verify, proxies=proxies, cert=cert
        )
        request.url = original_url
        if tls_hostname:
            pool.assert_hostname = tls_hostname
        return pool


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
    ):
        self._timeout = timeout or self.DEFAULT_TIMEOUT
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

            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
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

        # Disable auto-redirects -- we follow manually to validate each hop.
        # Force streaming so we can cap decompressed body size before it
        # reaches memory (requests would otherwise eagerly decode gzip).
        kwargs.setdefault("timeout", self._timeout)
        kwargs["allow_redirects"] = False
        kwargs["stream"] = True

        current_url = url
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            response = self._session.request(method, current_url, **kwargs)

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
                # Use apparent_encoding for better charset handling
                if response.encoding and response.apparent_encoding:
                    if (
                        response.encoding.lower() == "iso-8859-1"
                        and response.apparent_encoding.lower() != "iso-8859-1"
                    ):
                        response.encoding = response.apparent_encoding
                self._consume_body_capped(response)
                return response

            # Follow redirect -- validate the new URL
            redirect_url = response.headers.get("Location")
            if not redirect_url:
                # No Location header; cap body just like a normal response
                # before returning.
                self._consume_body_capped(response)
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

    def _consume_body_capped(self, response: requests.Response) -> None:
        """Read ``response`` body in chunks up to ``self._max_response_size``.

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
        for chunk in response.iter_content(chunk_size=8192):
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
            raise ImportError(
                "beautifulsoup4 is required for HTML parsing. "
                "Install with: pip install beautifulsoup4"
            )
        # Try lxml first (faster), fall back to html.parser (stdlib)
        try:
            return BeautifulSoup(html, "lxml")
        except Exception:
            return BeautifulSoup(html, "html.parser")

    def extract_text(self, soup: "BeautifulSoup", max_length: int = 5000) -> str:
        """Extract readable text from parsed HTML.

        Removes script/style/nav/footer tags, preserves heading hierarchy,
        paragraph breaks, and list structure. Collapses whitespace.
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
        if len(result) > max_length:
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
    ) -> dict:
        """Download a file from URL to local disk.

        Streams to disk to handle large files. Returns dict with
        path, size, and content_type.

        Args:
            url: URL to download
            save_dir: Directory to save file in
            filename: Override filename (default: from URL/headers)
            max_size: Max file size in bytes (default: self._max_download_size)
        """
        max_size = max_size or self._max_download_size

        self.validate_url(url)
        domain = urlparse(url).hostname
        self._rate_limit_wait(domain)

        # Stream the download
        response = self._session.get(
            url,
            stream=True,
            timeout=self._timeout,
            allow_redirects=False,
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
            response = self._session.get(
                redirect_url,
                stream=True,
                timeout=self._timeout,
                allow_redirects=False,
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
        save_dir.mkdir(parents=True, exist_ok=True)
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

        # Stream to disk
        downloaded = 0
        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                downloaded += len(chunk)
                if downloaded > max_size:
                    f.close()
                    save_path.unlink(missing_ok=True)
                    response.close()
                    raise ValueError(
                        f"Download exceeded max size: {downloaded} bytes (max: {max_size})"
                    )
                f.write(chunk)

        response.close()

        return {
            "path": str(save_path),
            "size": downloaded,
            "content_type": content_type,
            "filename": filename,
        }

    # -- Search --------------------------------------------------------------

    def search_duckduckgo(self, query: str, num_results: int = 5) -> list:
        """Search DuckDuckGo and parse results from HTML.

        Uses the HTML-only version (html.duckduckgo.com) which does not
        require JavaScript rendering. Uses POST as DDG expects form submission.

        Returns: [{"title": str, "url": str, "snippet": str}]
        """
        if not BS4_AVAILABLE:
            raise ImportError("beautifulsoup4 is required for web search.")

        response = self.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "b": ""},
        )

        soup = self.parse_html(response.text)
        results = []

        for result_div in soup.select(".result"):
            title_el = result_div.select_one(".result__title a, .result__a")
            snippet_el = result_div.select_one(".result__snippet")

            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            # DDG wraps URLs in a redirect -- extract the actual URL
            if "uddg=" in href:
                parsed = urlparse(href)
                params = parse_qs(parsed.query)
                if "uddg" in params:
                    href = params["uddg"][0]

            if title and href:
                results.append(
                    {
                        "title": title,
                        "url": href,
                        "snippet": snippet,
                    }
                )

            if len(results) >= num_results:
                break

        return results

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
