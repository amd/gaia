# Browser Tools — Feature Specification

> **Branch:** `feature/chat-agent-file-navigation`
> **Date:** 2026-03-10
> **Status:** Draft v2 — post architecture review
> **Owner:** GAIA Team

---

## 1. Executive Summary

Add a lightweight `BrowserToolsMixin` to the GAIA ChatAgent that provides web browsing, content extraction, file downloading, and web search capabilities — **without Playwright or any browser engine dependency**. Uses `requests` + `beautifulsoup4` (both already in GAIA's dependency tree) for fast, headless HTTP-based web interaction.

This completes the ChatAgent's data pipeline: **find local files + browse the web + extract data + analyze with scratchpad**.

---

## 2. Problem Statement

The ChatAgent can now navigate the local file system and analyze documents with the scratchpad. But users frequently need to:

| Gap | Example |
|-----|---------|
| Download files from the web | "Download my bank statement from this link" |
| Look up information online | "What's the current price of NVDA stock?" |
| Extract structured data from web pages | "Scrape the pricing table from this page" |
| Research to complement local analysis | "Compare my spending to national averages" |
| Fetch documentation/references | "Get the API docs for this library" |

Without browser tools, users must manually download files and feed them to the agent. This breaks the autonomous workflow.

---

## 3. Design Decisions

### 3.1 Why NOT Playwright/Selenium

| Factor | Playwright/Selenium | requests + BeautifulSoup |
|--------|--------------------|-----------------------|
| Install size | ~200 MB (browser binaries) | ~1 MB (already installed) |
| Startup time | 2-5 seconds (browser launch) | 0 ms |
| Memory | 200-500 MB per browser | ~5 MB per request |
| Dependencies | Node.js or browser binaries | Pure Python |
| JS rendering | Yes | No (but most data pages work without JS) |
| Reliability | Flaky (timeouts, browser crashes) | Stable (HTTP is simple) |
| Security | Full browser = full attack surface | HTTP only, sandboxed |

**Trade-off:** We lose JavaScript-rendered content (SPAs, dynamic pages). For the ChatAgent's use case (document download, data extraction, reference lookup), this is acceptable. 90%+ of useful web content is in the initial HTML response.

### 3.2 Key Design Principles

1. **No browser binary dependencies** — pure Python HTTP + HTML parsing
2. **Tools return text, not screenshots** — optimized for LLM consumption
3. **Rate limiting** — prevent accidental DoS (1 req/sec per domain)
4. **Size limits** — cap response sizes to avoid flooding LLM context
5. **Download to local filesystem** — integrate with file system tools
6. **Timeout everything** — 30-second default, configurable
7. **SSRF prevention** — validate resolved IPs against private/reserved ranges
8. **Manual redirect following** — validate each hop to prevent redirect-based SSRF

---

## 4. Tool Specification

### 4.1 `fetch_page(url, extract, max_length)`

Fetch a web page and extract its readable content.

```python
@tool(atomic=True)
def fetch_page(
    url: str,
    extract: str = "text",
    max_length: int = 5000,
) -> str:
    """Fetch a web page and extract its content.

    Retrieves the page at the given URL and returns readable text content.
    Use this to read articles, documentation, reference pages, or any web content.
    Does NOT execute JavaScript — works best with static content, articles, docs.

    Args:
        url: The full URL to fetch (must start with http:// or https://)
        extract: What to extract - 'text' (readable content), 'html' (raw HTML),
                 'links' (all links on page), 'tables' (HTML tables as text)
        max_length: Maximum characters to return (default: 5000, max: 20000)
    """
```

**Extract modes:**
- `text` — Strip HTML tags, return readable text with headings preserved. Uses BeautifulSoup `get_text()` with separator formatting.
- `html` — Return raw HTML (truncated). Useful when user needs to see page structure.
- `links` — Extract all `<a href>` links with their text. Returns formatted list.
- `tables` — Extract HTML `<table>` elements and format as readable text tables.

**Output format (text mode):**
```
Page: Example Documentation - My Library
URL: https://example.com/docs/api
Length: 4,521 chars | Fetched: 2026-03-10 14:30

API Reference
=============

Authentication
--------------
All API requests require a Bearer token in the Authorization header.

Endpoints
---------
GET /api/users - List all users
POST /api/users - Create a new user
...
```

### 4.2 `search_web(query, num_results)`

Search the web and return results.

```python
@tool(atomic=True)
def search_web(
    query: str,
    num_results: int = 5,
) -> str:
    """Search the web and return results with titles, URLs, and snippets.

    Uses a search API to find relevant web pages. Returns titles, URLs, and
    brief descriptions. Use fetch_page to read the full content of any result.

    Args:
        query: Search query string
        num_results: Number of results to return (default: 5, max: 10)
    """
```

**Search backend options (in priority order):**
1. **DuckDuckGo HTML** — No API key needed, parse search results page
2. **Google Custom Search API** — If user has configured API key
3. **Bing Search API** — If user has configured API key

Default: DuckDuckGo (free, no key required).

**Output format:**
```
Web search results for: "python sqlite fts5 tutorial"

1. SQLite FTS5 Full-Text Search - SQLite Documentation
   https://www.sqlite.org/fts5.html
   FTS5 is an SQLite virtual table module that provides full-text search...

2. Full-Text Search with SQLite and Python
   https://example.com/blog/sqlite-fts5-python
   Learn how to implement full-text search in Python using SQLite's FTS5...

3. ...
```

### 4.3 `download_file(url, save_to, filename)`

Download a file from the web to the local filesystem.

```python
@tool(atomic=True)
def download_file(
    url: str,
    save_to: str = "~/Downloads",
    filename: str = None,
) -> str:
    """Download a file from a URL to the local filesystem.

    Downloads the file and saves it locally. Useful for getting documents,
    PDFs, CSVs, images, or any file from the web for local analysis.
    After downloading, use read_file or index_document to process it.

    Args:
        url: Direct URL to the file to download
        save_to: Local directory to save the file (default: ~/Downloads)
        filename: Override filename (default: derived from URL or Content-Disposition)
    """
```

**Limits:**
- Max file size: 100 MB (configurable)
- Streams download to disk (doesn't load into memory)
- Validates path with `PathValidator` before writing
- Returns file path + size for follow-up tool use

**Output format:**
```
Downloaded: report-2026.pdf
  Saved to: C:\Users\John\Downloads\report-2026.pdf
  Size: 2.4 MB
  Type: application/pdf

Use read_file or index_document to process this file.
```

**Note:** `extract_page_data` from v1 has been merged into `fetch_page(extract="tables")` to reduce tool count per review issue M3. The `tables` mode returns JSON-formatted data ready for `insert_data()`.

---

## 5. Architecture

### 5.1 Component Diagram

```
ChatAgent
  |
  +-- BrowserToolsMixin (NEW - 3 tools)
  |     +-- fetch_page()           # Read web content (text/links/tables)
  |     +-- search_web()           # Web search
  |     +-- download_file()        # Download files to local disk
  |     |
  |     +-- self._web_client → WebClient (separate module)
  |           +-- get()            # HTTP GET with rate limiting + SSRF check
  |           +-- post()           # HTTP POST (for search)
  |           +-- parse_html()     # BeautifulSoup wrapper
  |           +-- extract_text()   # HTML to readable text
  |           +-- extract_tables() # HTML tables to JSON dicts
  |           +-- extract_links()  # Links extraction
  |           +-- download()       # Stream file to disk
  |
  +-- FileSystemToolsMixin (existing - 6 tools)
  +-- ScratchpadToolsMixin (existing - 5 tools)
  +-- RAGToolsMixin (existing)
  +-- ShellToolsMixin (existing)
```

### 5.2 WebClient Internal Class

Not a mixin — a utility class used by `BrowserToolsMixin` internally.

```python
class WebClient:
    """Lightweight HTTP client for web content extraction.

    Uses requests for HTTP and BeautifulSoup for HTML parsing.
    Handles rate limiting, timeouts, size limits, and content extraction.
    """

    DEFAULT_TIMEOUT = 30  # seconds
    DEFAULT_MAX_SIZE = 10 * 1024 * 1024  # 10 MB response limit
    MIN_REQUEST_INTERVAL = 1.0  # seconds between requests (rate limit)
    DEFAULT_USER_AGENT = "GAIA-Agent/0.15 (https://github.com/amd/gaia)"

    def __init__(self, timeout=None, max_size=None, user_agent=None):
        self._timeout = timeout or self.DEFAULT_TIMEOUT
        self._max_size = max_size or self.DEFAULT_MAX_SIZE
        self._user_agent = user_agent or self.DEFAULT_USER_AGENT
        self._last_request_time = 0  # For rate limiting
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })

    def get(self, url: str, stream: bool = False) -> requests.Response:
        """HTTP GET with rate limiting, timeout, and size checking."""

    def parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML content."""

    def extract_text(self, soup: BeautifulSoup, max_length: int = 5000) -> str:
        """Extract readable text from parsed HTML."""

    def extract_tables(self, soup: BeautifulSoup) -> list[list[dict]]:
        """Extract HTML tables as list of list-of-dicts."""

    def extract_links(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        """Extract all links with text and resolved URLs."""

    def close(self):
        """Close the session."""
```

### 5.3 File Locations

```
src/gaia/web/
+-- __init__.py               # Exports WebClient
+-- client.py                 # WebClient (HTTP + HTML extraction)

src/gaia/agents/tools/
+-- browser_tools.py          # BrowserToolsMixin (3 tools, delegates to WebClient)
```

---

## 6. Integration with ChatAgent

### 6.1 MRO Update

```python
class ChatAgent(
    Agent,
    RAGToolsMixin,
    FileToolsMixin,
    ShellToolsMixin,
    FileSystemToolsMixin,
    ScratchpadToolsMixin,
    BrowserToolsMixin,         # NEW
):
```

### 6.2 Config Additions

```python
@dataclass
class ChatAgentConfig:
    # ... existing fields ...

    # Browser settings
    enable_browser: bool = True  # Enable web browsing tools
    browser_timeout: int = 30  # HTTP request timeout in seconds
    browser_max_download_size: int = 100 * 1024 * 1024  # 100 MB max download
    browser_user_agent: str = "GAIA-Agent/0.15"
    browser_rate_limit: float = 1.0  # Seconds between requests
```

### 6.3 Tool Registration

```python
def _register_tools(self) -> None:
    self.register_rag_tools()
    self.register_file_tools()
    self.register_shell_tools()
    self.register_filesystem_tools()
    self.register_scratchpad_tools()
    self.register_browser_tools()  # NEW
```

### 6.4 Total Tool Count

After adding browser tools, the ChatAgent will have:

| Category | Tools | Count |
|----------|-------|-------|
| File System | browse_directory, tree, file_info, find_files, read_file, bookmark | 6 |
| Scratchpad | create_table, insert_data, query_data, list_tables, drop_table | 5 |
| Browser | fetch_page, search_web, download_file | 3 |
| RAG | query_documents, query_specific_file, index_document, index_directory, list_indexed_documents, search_indexed_chunks | 6 |
| File Ops | add_watch_directory | 1 |
| Shell | run_shell_command | 1 |
| **Total** | | **22** |

22 tools is manageable for Qwen3.5-35B. Tool names are intentionally distinct across categories to minimize selection confusion. Reduced from 4 to 3 browser tools by merging `extract_page_data` into `fetch_page(extract="tables")`.

---

## 7. Demo Workflows

### 7.1 Web Research + Local Analysis

```
User: "Compare my monthly grocery spending to the national average"

Agent:
1. query_data("SELECT strftime('%Y-%m', date) as month, SUM(amount) as total
               FROM scratch_transactions WHERE category='groceries' GROUP BY month")
   → User spends ~$650/month on groceries

2. search_web("average monthly grocery spending US household 2026")
   → Finds USDA data page

3. fetch_page("https://www.usda.gov/food-spending-data")
   → Extracts: "Average US household: $475/month"

4. Answer: "Your average monthly grocery spending is $650, which is 37% above
   the national average of $475/month. Here's the month-by-month breakdown..."
```

### 7.2 Download + Analyze

```
User: "Download the latest AMD earnings report and summarize it"

Agent:
1. search_web("AMD Q4 2025 earnings report PDF")
   → Finds direct PDF link

2. download_file("https://ir.amd.com/reports/Q4-2025.pdf")
   → Saved to ~/Downloads/Q4-2025.pdf

3. index_document("~/Downloads/Q4-2025.pdf")
   → Indexed, 85 chunks

4. query_documents("key financial metrics revenue profit")
   → Extracts: Revenue $7.1B, Net Income $1.2B...

5. Answer: "AMD's Q4 2025 earnings report shows..."
```

### 7.3 Web Scraping + Scratchpad

```
User: "Scrape the pricing from these three SaaS competitors and compare"

Agent:
1. extract_page_data("https://competitor1.com/pricing")
   → JSON table of plans

2. create_table("competitor_pricing",
     "company TEXT, plan TEXT, price_monthly REAL, users INTEGER, features TEXT")

3. insert_data("competitor_pricing", [...extracted data...])

4. Repeat for competitors 2 and 3

5. query_data("SELECT company, plan, price_monthly FROM scratch_competitor_pricing
               ORDER BY price_monthly")

6. Answer: "Here's a comparison of all three competitors' pricing..."
```

---

## 8. Security

### 8.1 URL Validation (SSRF Prevention)

```python
import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_PORTS = {22, 23, 25, 445, 3306, 5432, 6379, 27017}  # SSH, SMTP, DB ports

def _validate_url(url: str) -> str:
    """Validate URL is safe to fetch. Returns normalized URL or raises ValueError.

    1. Parse URL and validate scheme (http/https only)
    2. Check port is not in blocked set
    3. Resolve hostname to IP address
    4. Validate resolved IP is not private/reserved/loopback/link-local
    5. Return validated URL
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"Blocked scheme: {parsed.scheme}")
    if parsed.port and parsed.port in BLOCKED_PORTS:
        raise ValueError(f"Blocked port: {parsed.port}")
    # Resolve and validate IP
    _validate_host_ip(parsed.hostname)
    return url

def _validate_host_ip(hostname: str) -> None:
    """Resolve hostname and check IP is not private/internal."""
    try:
        resolved = socket.getaddrinfo(hostname, None)
        for family, _, _, _, sockaddr in resolved:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise ValueError(f"Blocked: {hostname} resolves to private/reserved IP {ip}")
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname: {hostname}")
```

**Security model:**
- Only `http://` and `https://` schemes allowed
- DNS resolution happens BEFORE connection — resolved IP is validated
- Blocks all RFC 1918 private ranges (`10.x`, `172.16-31.x`, `192.168.x`)
- Blocks loopback (`127.0.0.0/8`), link-local (`169.254.x.x` — AWS/Azure/GCP metadata)
- Blocks IPv6 private (`fc00::/7`), link-local (`fe80::/10`), mapped (`::ffff:127.0.0.1`)
- Redirects are followed manually (max 5 hops), each hop re-validated
- Prevents DNS rebinding by checking resolved IP, not hostname

### 8.2 Content Limits

| Limit | Default | Purpose |
|-------|---------|---------|
| Response size | 10 MB | Prevent memory exhaustion |
| Download size | 100 MB | Prevent disk fill |
| Text extraction | 20,000 chars max | Prevent context overflow |
| Rate limit | 1 req/sec | Prevent accidental DoS |
| Timeout | 30 seconds | Prevent hanging |
| Max redirects | 5 | Prevent redirect loops |

### 8.3 Download Path Validation

```python
def _sanitize_filename(raw_name: str) -> str:
    """Sanitize filename from URL or Content-Disposition header.

    1. Extract basename only (strip path components)
    2. Remove null bytes and control characters
    3. Replace path separators (/, \\) with _
    4. Reject filenames starting with . (hidden files)
    5. Limit to safe charset [a-zA-Z0-9._-]
    6. Truncate to 200 chars
    7. Fallback to 'download' if empty after sanitization
    """
    import re
    name = os.path.basename(raw_name)
    name = name.replace("\x00", "").strip()
    name = re.sub(r'[/\\]', '_', name)
    name = re.sub(r'[^a-zA-Z0-9._-]', '_', name)
    if name.startswith('.'):
        name = '_' + name
    name = name[:200]
    return name or "download"
```

Downloaded files must pass two checks:
1. Filename sanitized via `_sanitize_filename()` (prevents path traversal from Content-Disposition)
2. Final resolved path validated through `PathValidator.is_path_allowed()`
3. Verify resolved path is still within `save_to` directory after path resolution

---

## 9. Dependencies

### 9.1 Required (already installed)

| Package | Usage | Status |
|---------|-------|--------|
| `requests` | HTTP client | Already in GAIA deps |
| `beautifulsoup4` | HTML parsing | Already in GAIA eval extras |

### 9.2 Optional

| Package | Usage | Status |
|---------|-------|--------|
| `lxml` | Faster HTML parser for BS4 | Optional, falls back to `html.parser` |

**No new dependencies needed.** Both `requests` and `beautifulsoup4` are already in the project.

---

## 10. Implementation Plan

Single phase — this is a focused, self-contained feature.

- [ ] Create `src/gaia/agents/tools/browser_tools.py`:
  - `WebClient` utility class (rate limiting, timeouts, extraction)
  - `BrowserToolsMixin` with `register_browser_tools()` containing 4 tools
- [ ] Update `src/gaia/agents/tools/__init__.py` to export `BrowserToolsMixin`
- [ ] Update `src/gaia/agents/chat/agent.py`:
  - Add `BrowserToolsMixin` to class MRO
  - Add `enable_browser` + config fields to `ChatAgentConfig`
  - Initialize `WebClient` in `__init__`
  - Call `register_browser_tools()` in `_register_tools()`
  - Update system prompt with browser tool guidance
- [ ] Add unit tests: `tests/unit/test_browser_tools.py`
  - Mock HTTP responses with `responses` library (already in dev deps)
  - Test URL validation (SSRF prevention)
  - Test content extraction (text, links, tables)
  - Test rate limiting
  - Test download with size limits
- [ ] Format with black + isort

---

## 11. DuckDuckGo Search Implementation

Since we want no API keys required, the default search uses DuckDuckGo's HTML search:

```python
def _search_duckduckgo(self, query: str, num_results: int = 5) -> list[dict]:
    """Search DuckDuckGo and parse results from HTML.

    Uses the HTML-only version (html.duckduckgo.com) which doesn't
    require JavaScript rendering.

    Returns list of {"title": str, "url": str, "snippet": str}.
    """
    response = self.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
    )
    soup = self.parse_html(response.text)
    results = []
    for result in soup.select(".result"):
        title_el = result.select_one(".result__title a")
        snippet_el = result.select_one(".result__snippet")
        if title_el:
            results.append({
                "title": title_el.get_text(strip=True),
                "url": title_el.get("href", ""),
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            })
        if len(results) >= num_results:
            break
    return results
```

**Fallback:** If DuckDuckGo blocks or changes their HTML structure, the tool returns a clear error message suggesting the user try a direct URL instead.

---

## 12. Text Extraction Strategy

### 12.1 Readable Text Extraction

```python
def extract_text(self, soup: BeautifulSoup, max_length: int = 5000) -> str:
    """Extract readable text, preserving structure.

    Strategy:
    1. Remove script, style, nav, footer, aside tags
    2. Preserve heading hierarchy (h1-h6 → underlined text)
    3. Preserve list structure (ul/ol → bulleted/numbered)
    4. Preserve paragraph breaks
    5. Collapse whitespace
    6. Truncate to max_length with word boundary
    """
```

### 12.2 Tags Removed Before Extraction

```python
REMOVE_TAGS = [
    "script", "style", "nav", "footer", "aside", "header",
    "noscript", "iframe", "svg", "form", "button", "input",
    "select", "textarea", "meta", "link",
]
```

### 12.3 Table Extraction

```python
def extract_tables(self, soup: BeautifulSoup) -> list:
    """Extract tables as list of dicts.

    For each <table>:
    1. Use first <tr> or <thead> as column headers
    2. Subsequent rows become dicts with header keys
    3. Strip whitespace from cells
    4. Skip tables with fewer than 2 rows (likely layout tables)
    """
```

---

## 13. Decisions Log

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | No Playwright/Selenium | 200 MB install, slow startup, bloated for HTTP-only use case |
| D2 | requests + BeautifulSoup | Already in deps, pure Python, fast, stable |
| D3 | DuckDuckGo for search | No API key needed, free, privacy-respecting |
| D4 | 3 tools (merged extract_page_data into fetch_page) | Minimize tool count and LLM confusion (review M3) |
| D5 | Text output (not screenshots) | LLM processes text better; no VLM requirement |
| D6 | Per-domain rate limiting (1 req/sec) | Prevent accidental DoS; doesn't penalize cross-domain (review M4) |
| D7 | SSRF prevention via resolved IP validation | Check resolved IP against private/reserved ranges using `ipaddress` module (review C1) |
| D8 | WebClient in separate `src/gaia/web/` module | Follows service-class pattern; independently testable/reusable (review M1) |
| D9 | Manual redirect following (no auto-redirect) | Validate each redirect hop to prevent redirect-based SSRF (review C2) |
| D10 | beautifulsoup4 with html.parser fallback | lxml is faster but optional; html.parser is stdlib |
| D11 | Download filename sanitized to basename + safe chars | Prevent path traversal from Content-Disposition headers (review C3) |
| D12 | search_web uses POST for DuckDuckGo | DDG HTML search uses POST form submission |
| D13 | Content-Type checking on fetch_page | Return JSON directly for APIs, suggest download_file for binary (review M2) |
| D14 | Clamp max_length and num_results in tools | Prevent LLM-generated extreme values (review H3) |
| D15 | No robots.txt enforcement | This is a lightweight fetcher, not a crawler (review H4) |
| D16 | `_ensure_web_client()` guard pattern | Match existing `_ensure_scratchpad()` pattern (review H2) |
| D17 | response.apparent_encoding fallback | Handle incorrect charset headers for non-ASCII pages (review L3) |
