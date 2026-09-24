# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Documentation Link Checker

Parses all .mdx and .md files in docs/ and README.md, extracts URLs,
and verifies they resolve correctly. Checks both external HTTP(S) links
and internal cross-references between documentation files.

Usage:
    python util/check_doc_links.py                    # Check all links
    python util/check_doc_links.py --internal-only    # Skip external URLs
    python util/check_doc_links.py --external-only    # Skip internal refs
    python util/check_doc_links.py --verbose          # Show all links checked
"""

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set, Tuple


class LinkResult(NamedTuple):
    file: str
    line: int
    url: str
    status: str  # "ok", "broken", "skipped", "warning"
    detail: str


# Regex patterns for extracting links
# Matches [text](url) but not ![image](url) image tags with broken links
MD_LINK_RE = re.compile(r'(?<!!)\[([^\]]*)\]\(([^)]+)\)')
# Matches bare URLs in text
BARE_URL_RE = re.compile(r'(?<=["\s(])https?://[^\s)"\'<>]+')
# Matches href="url" in HTML/JSX
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']')
# Matches src="url" in HTML/JSX
SRC_RE = re.compile(r'src=["\']([^"\']+)["\']')

# Domains to skip (known to block automated requests or require auth)
SKIP_DOMAINS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "example.com",
    "your-server",
    "your-domain",
    "grafana.internal",
    "marketplace.visualstudio.com",  # Blocks automated requests with 404
    "dl.acm.org",  # Blocks automated requests with CAPTCHAs
    "platform.openai.com",  # Rate-limits CI bots
    "www.npmjs.com",  # Returns 403 to automated requests
    "support.microsoft.com",  # Intermittently 400s/429s datacenter IPs
    "www.xda-developers.com",  # Drops connections from datacenter IPs
    "console.cloud.google.com",  # Redirects unauthenticated requests to sign-in (302)
    "support.google.com",  # Locale/UA-gated help pages; 404s automated requests
    "myaccount.google.com",  # Redirects unauthenticated requests to sign-in (302)
}

# A github.com issue/PR page. Rewritten to the REST API equivalent below —
# the HTML page is served through GitHub's anti-abuse layer, which resets
# the connection under CI's shared/high-volume egress IPs (#2925); the API
# is authenticated (when GITHUB_TOKEN is set) and rate-limit aware instead.
GITHUB_ISSUE_PR_RE = re.compile(
    r"^https?://github\.com/([^/]+)/([^/]+)/(?:issues|pull)/(\d+)(?:[/?#].*)?$"
)

# Number of extra attempts for a result classified as "warning" (transient /
# unverifiable), so a single blip doesn't need a whole extra CI run to clear.
EXTERNAL_LINK_RETRIES = 2
EXTERNAL_LINK_RETRY_BACKOFF = 1.5  # seconds, multiplied by attempt number

# URL patterns to skip
SKIP_PATTERNS = [
    r"^mailto:",
    r"^tel:",
    r"^#",  # Anchor-only links
    r"^javascript:",
    r"\{[{%]",  # Template variables like {{ var }}
    r"^\$\{",  # JS template literals
    r"^url$",  # Placeholder "url" in markdown syntax examples
    r"`$",  # Trailing backtick from inline code extraction (e.g. `https://...`)
    r"github\.com/amd/gaia/compare/",  # Release compare URLs (tags may not exist yet)
    r"github\.com/amd/gaia/(blob|tree)/main/",  # Same-repo links (may 404 during PRs before merge)
    r"https?://(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)",  # RFC1918 private IPs (example URLs in docs)
    r"^https?://t\.me/",  # Telegram deep-links (e.g. BotFather) — valid but unresolvable from CI runners
]


def find_doc_files(repo_root: str) -> List[Path]:
    """Find all documentation files to check."""
    files = []
    docs_dir = Path(repo_root) / "docs"
    if docs_dir.exists():
        for ext in ("*.mdx", "*.md"):
            files.extend(docs_dir.rglob(ext))

    # Also check README files
    for readme in ["README.md", "cpp/README.md"]:
        p = Path(repo_root) / readme
        if p.exists():
            files.append(p)

    return sorted(files)


def extract_links(filepath: Path) -> List[Tuple[int, str]]:
    """Extract all links from a file, returning (line_number, url) tuples."""
    links = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return links

    in_code_block = False
    for i, line in enumerate(content.splitlines(), start=1):
        # Track fenced code blocks
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # Markdown links [text](url)
        for match in MD_LINK_RE.finditer(line):
            url = match.group(2).strip()
            links.append((i, url))

        # href="url" and src="url"
        for pattern in (HREF_RE, SRC_RE):
            for match in pattern.finditer(line):
                url = match.group(1).strip()
                links.append((i, url))

        # Bare URLs (only if not already captured)
        existing_urls = {u for _, u in links if _== i}  # noqa: E741
        for match in BARE_URL_RE.finditer(line):
            url = match.group(0).strip().rstrip(".,;:)")
            if url not in existing_urls:
                links.append((i, url))

    return links


def should_skip(url: str) -> bool:
    """Check if a URL should be skipped."""
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, url):
            return True

    # Check skip domains
    for domain in SKIP_DOMAINS:
        if domain in url:
            return True

    return False


def is_internal_link(url: str) -> bool:
    """Check if a link is an internal cross-reference (not an external URL)."""
    return not url.startswith(("http://", "https://"))


def check_internal_link(
    url: str, source_file: Path, repo_root: Path, docs_json_pages: Set[str]
) -> Tuple[str, str]:
    """
    Check an internal documentation cross-reference.
    Returns (status, detail).
    """
    # Strip anchor fragments
    clean_url = url.split("#")[0]
    if not clean_url:
        return "ok", "anchor-only link"

    # Handle relative paths
    if clean_url.startswith("/"):
        # Absolute path from docs root
        target = repo_root / "docs" / clean_url.lstrip("/")
    else:
        # Relative to current file
        target = source_file.parent / clean_url

    # Try with and without extensions
    candidates = [target]
    if not target.suffix:
        candidates.extend([
            target.with_suffix(".mdx"),
            target.with_suffix(".md"),
            target / "index.mdx",
            target / "index.md",
        ])
    elif target.suffix == ".mdx":
        candidates.append(target.with_suffix(".md"))

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.exists():
                return "ok", str(resolved.relative_to(repo_root))
        except (ValueError, OSError):
            continue

    return "broken", f"file not found: {clean_url}"


def github_api_equivalent(url: str) -> Optional[str]:
    """Map a github.com issue/PR page URL to its REST API equivalent."""
    m = GITHUB_ISSUE_PR_RE.match(url)
    if not m:
        return None
    owner, repo, number = m.groups()
    return f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"


def _request_headers(effective_url: str) -> Dict[str, str]:
    headers = {
        "User-Agent": "GAIA-DocLinkChecker/1.0 (+https://github.com/amd/gaia)",
        "Accept": "text/html,application/xhtml+xml,*/*",
    }
    if effective_url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def check_external_link(url: str, timeout: int = 15) -> Tuple[str, str]:
    """
    Check an external URL by sending a HEAD request (falling back to GET).
    Returns (status, detail).
    """
    # github.com issue/PR pages go through an anti-abuse layer that can reset
    # the connection under CI's shared egress IPs; the REST API is
    # authenticated (when GITHUB_TOKEN is set) and rate-limit aware instead.
    api_url = github_api_equivalent(url)
    effective_url = api_url or url
    headers = _request_headers(effective_url)
    method = "GET" if api_url else "HEAD"
    req = urllib.request.Request(effective_url, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            if code < 400:
                return "ok", f"HTTP {code}" + (" (GitHub API)" if api_url else "")
            return "broken", f"HTTP {code}"
    except urllib.error.HTTPError as e:
        # Some servers reject HEAD, retry with GET. 400 belongs here as much as
        # 405 does: status.canonical.com answers HEAD with 400 and GET with 200.
        # A real 400 still fails below, because the retry must come back < 400.
        if e.code in (400, 403, 405):
            try:
                req_get = urllib.request.Request(
                    effective_url, headers=headers, method="GET"
                )
                with urllib.request.urlopen(req_get, timeout=timeout) as resp:
                    code = resp.getcode()
                    if code < 400:
                        return "ok", f"HTTP {code} (GET fallback)"
                    return "broken", f"HTTP {code}"
            except urllib.error.HTTPError as e2:
                if e2.code in (403, 429) or e2.code >= 500:
                    return "warning", f"HTTP {e2.code} (may require auth or be rate limited)"
                return "broken", f"HTTP {e2.code}"
            except (OSError, http.client.HTTPException) as e2:
                return "warning", f"connection error on GET fallback: {e2}"
            except Exception as e2:
                return "broken", str(e2)
        if e.code == 429:
            return "warning", "HTTP 429 (rate limited)"
        if e.code >= 500:
            return "warning", f"HTTP {e.code} (server error)"
        return "broken", f"HTTP {e.code}"
    except urllib.error.URLError as e:
        # Connection reset / refused is often CDN/anti-bot blocking, not a dead link
        reason = e.reason
        if isinstance(reason, (ConnectionResetError, ConnectionRefusedError)):
            return "warning", f"URL error: {reason} (may block automated requests)"
        if hasattr(reason, "errno") and reason.errno in (104, 111):
            return "warning", f"URL error: {reason} (may block automated requests)"
        # SSL handshake timeouts are transient CI network issues, not dead links.
        # They arrive as URLError(SSLError("_ssl.c:...: The handshake operation timed out"))
        # rather than a Python-level TimeoutError, so they must be caught here.
        reason_str = str(reason).lower()
        if "timed out" in reason_str or "handshake" in reason_str:
            return "warning", f"URL error: {reason} (SSL/network timeout)"
        return "broken", f"URL error: {reason}"
    except TimeoutError:
        return "warning", "timeout"
    except (OSError, http.client.HTTPException) as e:
        # Covers e.g. http.client.RemoteDisconnected ("Remote end closed
        # connection without response") — raised directly by
        # HTTPConnection.getresponse() and NOT wrapped in urllib.error.URLError,
        # so it falls through the reason-based classification above. Seen
        # verbatim, only against github.com, in amd/gaia CI runs #31644732155
        # and #31652531850 (2026-08-12) and #31680303088 (2026-08-13) — the
        # anti-abuse layer resetting mid-response under a burst of concurrent
        # unauthenticated requests from the runner's shared IP, not a dead
        # link. Unverifiable, not broken.
        return "warning", f"connection error: {e} (may block automated requests)"
    except Exception as e:
        return "broken", str(e)


def check_external_link_with_retries(
    url: str,
    timeout: int = 15,
    retries: int = EXTERNAL_LINK_RETRIES,
    backoff: float = EXTERNAL_LINK_RETRY_BACKOFF,
) -> Tuple[str, str]:
    """Retry a "warning" (transient/unverifiable) result before giving up.

    Never retries "ok" or "broken" — a genuine 404 stays broken on the first
    attempt so the check keeps checking. Exhausting retries still reports
    "warning", never escalates to "broken".
    """
    status, detail = check_external_link(url, timeout=timeout)
    attempt = 0
    while status == "warning" and attempt < retries:
        time.sleep(backoff * (attempt + 1))
        attempt += 1
        status, detail = check_external_link(url, timeout=timeout)
    if attempt:
        detail = f"{detail} [after {attempt} retry(ies)]"
    return status, detail


def load_docs_json_pages(repo_root: str) -> Set[str]:
    """Load page paths from docs.json for internal link validation."""
    docs_json = Path(repo_root) / "docs" / "docs.json"
    pages = set()
    if not docs_json.exists():
        return pages
    try:
        data = json.loads(docs_json.read_text(encoding="utf-8"))
        _collect_pages(data, pages)
    except Exception:
        pass
    return pages


def _collect_pages(obj, pages: Set[str]):
    """Recursively collect page paths from docs.json structure."""
    if isinstance(obj, str):
        pages.add(obj)
    elif isinstance(obj, list):
        for item in obj:
            _collect_pages(item, pages)
    elif isinstance(obj, dict):
        if "pages" in obj:
            _collect_pages(obj["pages"], pages)
        for key in ("group", "page"):
            if key in obj and isinstance(obj[key], str):
                pages.add(obj[key])


def check_links(
    repo_root: str,
    internal_only: bool = False,
    external_only: bool = False,
    verbose: bool = False,
    max_workers: int = 10,
) -> List[LinkResult]:
    """Check all documentation links and return results."""
    root = Path(repo_root)
    files = find_doc_files(repo_root)
    docs_json_pages = load_docs_json_pages(repo_root)
    results: List[LinkResult] = []

    # Collect all links
    all_links: List[Tuple[Path, int, str]] = []
    for filepath in files:
        for line_num, url in extract_links(filepath):
            all_links.append((filepath, line_num, url))

    # Deduplicate external URLs for efficiency
    external_urls: Dict[str, List[Tuple[Path, int]]] = {}
    internal_links: List[Tuple[Path, int, str]] = []

    for filepath, line_num, url in all_links:
        if should_skip(url):
            if verbose:
                rel = filepath.relative_to(root)
                results.append(LinkResult(str(rel), line_num, url, "skipped", ""))
            continue

        if is_internal_link(url):
            if not external_only:
                internal_links.append((filepath, line_num, url))
        else:
            if not internal_only:
                external_urls.setdefault(url, []).append((filepath, line_num))

    # Check internal links (fast, no network)
    for filepath, line_num, url in internal_links:
        rel = filepath.relative_to(root)
        status, detail = check_internal_link(url, filepath, root, docs_json_pages)
        results.append(LinkResult(str(rel), line_num, url, status, detail))

    # Check external links (parallel with rate limiting)
    if external_urls:
        checked: Dict[str, Tuple[str, str]] = {}

        def _check(url: str) -> Tuple[str, str, str]:
            time.sleep(0.1)  # Basic rate limiting
            status, detail = check_external_link_with_retries(url)
            return url, status, detail

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_check, url): url for url in external_urls}
            for future in as_completed(futures):
                url, status, detail = future.result()
                checked[url] = (status, detail)

        # Map results back to file locations
        for url, locations in external_urls.items():
            status, detail = checked[url]
            for filepath, line_num in locations:
                rel = filepath.relative_to(root)
                results.append(LinkResult(str(rel), line_num, url, status, detail))

    return sorted(results, key=lambda r: (r.status != "broken", r.file, r.line))


def main():
    parser = argparse.ArgumentParser(description="Check documentation links")
    parser.add_argument(
        "--internal-only", action="store_true", help="Only check internal cross-refs"
    )
    parser.add_argument(
        "--external-only", action="store_true", help="Only check external URLs"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Show all links (including OK/skipped)"
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Max parallel HTTP requests (default: 10)",
    )
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"Checking documentation links in: {repo_root}")
    print()

    results = check_links(
        repo_root,
        internal_only=args.internal_only,
        external_only=args.external_only,
        verbose=args.verbose,
        max_workers=args.max_workers,
    )

    # Summarize
    broken = [r for r in results if r.status == "broken"]
    warnings = [r for r in results if r.status == "warning"]
    ok = [r for r in results if r.status == "ok"]
    skipped = [r for r in results if r.status == "skipped"]

    if broken:
        print("BROKEN LINKS:")
        print("=" * 80)
        for r in broken:
            print(f"  {r.file}:{r.line}")
            print(f"    URL: {r.url}")
            print(f"    Error: {r.detail}")
            print()

    if warnings:
        print("WARNINGS:")
        print("=" * 80)
        for r in warnings:
            print(f"  {r.file}:{r.line}")
            print(f"    URL: {r.url}")
            print(f"    Warning: {r.detail}")
            print()

    if args.verbose and ok:
        print("OK LINKS:")
        print("=" * 80)
        for r in ok:
            print(f"  {r.file}:{r.line} -> {r.url} ({r.detail})")
        print()

    print("=" * 80)
    print(f"Total links checked: {len(ok) + len(broken) + len(warnings)}")
    print(f"  OK:       {len(ok)}")
    print(f"  Broken:   {len(broken)}")
    print(f"  Warnings: {len(warnings)}")
    print(f"  Skipped:  {len(skipped)}")

    if broken:
        print()
        print("FAILED: Found broken links")
        sys.exit(1)
    else:
        print()
        print("PASSED: All links valid")
        sys.exit(0)


if __name__ == "__main__":
    main()
