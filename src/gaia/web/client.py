# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lightweight HTTP client for web content extraction."""

import ipaddress
import os
import re
import socket
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests

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

        for family, _, _, _, sockaddr in results:
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
        """Wait if needed to respect per-domain rate limit."""
        now = time.time()
        last = self._domain_last_request.get(domain, 0)
        elapsed = now - last
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._domain_last_request[domain] = time.time()

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
        """Internal request method with SSRF checks and manual redirect following."""
        self.validate_url(url)

        domain = urlparse(url).hostname
        self._rate_limit_wait(domain)

        # Disable auto-redirects -- we follow manually to validate each hop
        kwargs.setdefault("timeout", self._timeout)
        kwargs["allow_redirects"] = False

        current_url = url
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            response = self._session.request(method, current_url, **kwargs)

            # Check response size
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > self._max_response_size:
                raise ValueError(
                    f"Response too large: {int(content_length)} bytes "
                    f"(max: {self._max_response_size})"
                )

            # Not a redirect -- return
            if response.status_code not in (301, 302, 303, 307, 308):
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
                return response  # No Location header, return as-is

            # Resolve relative redirects
            redirect_url = urljoin(current_url, redirect_url)

            # Validate redirect target (SSRF check on each hop)
            self.validate_url(redirect_url)

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

            # Get data rows
            data_rows = rows[1:] if not thead else table.find("tbody", recursive=False)
            if hasattr(data_rows, "find_all"):
                data_rows = data_rows.find_all("tr")

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

        # Verify path is still within save_dir (prevent traversal)
        if not str(save_path.resolve()).startswith(str(save_dir)):
            raise ValueError(f"Path traversal detected: {filename}")

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

        content_type = response.headers.get("Content-Type", "unknown")

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

    @staticmethod
    def _sanitize_filename(raw_name: str) -> str:
        """Sanitize filename from URL or Content-Disposition header."""
        name = os.path.basename(raw_name)
        name = name.replace("\x00", "").strip()
        name = re.sub(r"[/\\]", "_", name)
        name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
        if name.startswith("."):
            name = "_" + name
        name = name[:200]
        return name or "download"
