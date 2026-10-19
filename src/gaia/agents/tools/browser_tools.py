# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access

"""
Browser Tools for web content extraction and search.

Provides lightweight web browsing tools using requests + BeautifulSoup
(no Playwright or browser binaries). Enables agents to fetch web pages,
search the web, and download files for local analysis.
"""

import json
import logging
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from gaia.agents.base.file_snapshots import before_change

logger = logging.getLogger(__name__)

#: Characters of context kept on each side of a ``find`` match.
FIND_CONTEXT_CHARS = 300

#: Text this short means the page renders its content with JavaScript.
_NEAR_EMPTY_CHARS = 200
_NEAR_EMPTY_NOTE = (
    "\n\n[Almost no text on this page: its content is probably rendered by "
    "JavaScript, which fetch_page does not run. Open it with a real-browser tool "
    "if you have one; otherwise try another source, or report the value as not "
    "found.]"
)


def _find_spans(text: str, term: str) -> List[Tuple[int, int]]:
    """Merged ``(start, end)`` windows around every case-insensitive match of *term*."""
    spans: List[Tuple[int, int]] = []
    haystack, needle = text.lower(), term.lower()
    at = haystack.find(needle)
    while at != -1:
        start = max(0, at - FIND_CONTEXT_CHARS)
        end = min(len(text), at + len(needle) + FIND_CONTEXT_CHARS)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
        at = haystack.find(needle, at + len(needle))
    return spans


def text_window(
    text: str, url: str, max_length: int, offset: int = 0, find: Optional[str] = None
) -> str:
    """The part of *text* the caller asked for, marked when more remains.

    Never cuts silently: a part that stops before the end names the offset of
    the next one, and a ``find`` that matches nothing says so.
    """
    total = len(text)
    if find and find.strip():
        term = find.strip()
        # A passage the offset falls inside resumes there, so paging moves on.
        spans = [
            (max(start, offset), end)
            for start, end in _find_spans(text, term)
            if end > offset
        ]
        if not spans:
            where = f"after character {offset:,} of" if offset else "in"
            return (
                f"[No occurrence of {term!r} {where} the page's {total:,} characters "
                "of text. Do not infer it; try another word, another page, or "
                "report it as not found.]"
            )
        parts, used = [], 0
        for number, (start, end) in enumerate(spans):
            label = f"[at character {start:,}]\n"
            if parts and used + len(label) + end - start > max_length:
                return "\n\n".join(parts) + (
                    f"\n\n[{len(spans) - number} more passage(s) match {term!r}. "
                    f"Call fetch_page({url!r}, find={term!r}, offset={start}) for "
                    "the rest.]"
                )
            if len(label) + end - start > max_length:
                cut = start + max(1, max_length - len(label))
                parts.append(label + text[start:cut])
                return "\n\n".join(parts) + (
                    f"\n\n[This passage continues. Call fetch_page({url!r}, "
                    f"offset={cut}) to read on.]"
                )
            parts.append(label + text[start:end])
            used += len(parts[-1]) + 2
        return "\n\n".join(parts)

    if offset and offset >= total:
        return (
            f"[offset {offset:,} is past the end: the page has {total:,} "
            "characters of text.]"
        )
    end = min(total, offset + max_length)
    part = text[offset:end]
    if offset:
        part = f"[Continuing at character {offset:,} of {total:,}]\n" + part
    if end < total:
        part += (
            f"\n\n[Truncated: showing characters {offset:,}-{end:,} of {total:,}. "
            f"Call fetch_page({url!r}, offset={end}) for the next part, or pass "
            "find='<word>' to jump to a detail.]"
        )
    return part


class BrowserToolsMixin:
    """Web browsing tools for content extraction, search, and download.

    Gives the agent the ability to fetch web pages, extract structured data,
    search the web, and download files — all without a browser engine.

    Tool registration follows GAIA pattern: register_browser_tools() method.

    The mixin expects self._web_client to be set to a WebClient instance
    before tools are used. If not set, tools return helpful error messages.
    """

    _web_client = None  # WebClient instance, set by agent init

    def register_browser_tools(self) -> None:
        """Register browser tools for web content extraction."""
        from gaia.agents.base.tools import tool

        mixin = self  # Capture self for nested functions

        def _ensure_web_client() -> bool:
            """Check that web client is available."""
            if mixin._web_client is None:
                return False
            return True

        @tool(atomic=True)
        def fetch_page(
            url: str,
            extract: Literal["text", "html", "links", "tables"] = "text",
            max_length: int = 5000,
            offset: int = 0,
            find: Optional[str] = None,
        ) -> str:
            """Fetch a web page and extract its content.

            Retrieves the page at the given URL and returns readable text content.
            Use this to read articles, documentation, reference pages, or any web content.
            Does NOT execute JavaScript — works best with static content, articles, docs.
            Long text ends with a marker naming the offset of the next part; to
            get one detail (a price, a spec) pass find instead of paging.

            Args:
                url: The full URL to fetch (must start with http:// or https://)
                extract: What to extract - 'text' (readable content), 'html' (raw HTML),
                         'links' (all links on page), 'tables' (HTML tables as JSON)
                max_length: Maximum characters to return (default: 5000, max: 20000)
                offset: Text mode: character offset to start from, to continue a long page
                find: Text mode: return only the passages containing this word or phrase
            """
            if not _ensure_web_client():
                return "Error: Browser tools not initialized. Web browsing is disabled."

            # Clamp max_length to prevent extreme values
            max_length = max(512, min(max_length, 20000))
            from gaia.agents.base.artifacts import retain_excerpt

            # Validate extract mode
            valid_modes = {"text", "html", "links", "tables"}
            if extract not in valid_modes:
                return (
                    f"Error: Invalid extract mode '{extract}'. "
                    f"Must be one of: {', '.join(sorted(valid_modes))}"
                )
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                return "Error: offset must be a character position, 0 or more."
            if (offset or find) and extract != "text":
                return "Error: offset and find apply to extract='text' only."

            try:
                response = mixin._web_client.get(url)
                response.raise_for_status()
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                return f"Error fetching page: {e}"

            content_type = response.headers.get("Content-Type", "")

            # If it's not HTML, return raw text or suggest download
            if (
                "text/html" not in content_type
                and "application/xhtml" not in content_type
            ):
                if any(
                    t in content_type
                    for t in ["application/json", "text/plain", "text/csv", "text/xml"]
                ):
                    # Text-based content — return directly
                    return (
                        f"Content from: {url}\n"
                        f"Type: {content_type}\n"
                        f"Length: {len(response.text):,} chars\n\n"
                        + text_window(response.text, url, max_length, offset, find)
                    )
                else:
                    # Binary content — suggest download
                    size = response.headers.get("Content-Length", "unknown")
                    return (
                        f"This URL returns binary content ({content_type}, size: {size}).\n"
                        f"Use download_file to save it locally for analysis."
                    )

            # Parse HTML
            try:
                soup = mixin._web_client.parse_html(response.text)
            except ImportError as e:
                return f"Error: {e}"

            # Get page title
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else "(no title)"

            if extract == "html":
                html = retain_excerpt(mixin, response.text, max_length)
                return (
                    f"Page: {title}\n"
                    f"URL: {url}\n"
                    f"Length: {len(response.text):,} chars\n\n"
                    f"{html}"
                )

            elif extract == "links":
                links = mixin._web_client.extract_links(soup, url)
                if not links:
                    return f"Page: {title}\nURL: {url}\n\nNo links found on this page."

                lines = [f"Page: {title}", f"URL: {url}", f"Links: {len(links)}", ""]
                for i, link in enumerate(links, 1):
                    lines.append(f"  {i}. {link['text']}")
                    lines.append(f"     {link['url']}")

                result = "\n".join(lines)
                result = retain_excerpt(mixin, result, max_length)
                return result

            elif extract == "tables":
                tables = mixin._web_client.extract_tables(soup)
                if not tables:
                    return f"Page: {title}\nURL: {url}\n\nNo data tables found on this page."

                lines = [
                    f"Page: {title}",
                    f"URL: {url}",
                    f"Tables found: {len(tables)}",
                    "",
                ]
                for table in tables:
                    lines.append(f"--- {table['table_name']} ---")
                    # Format as JSON for easy insert_data consumption
                    table_json = json.dumps(table["data"], indent=2)
                    lines.append(table_json)
                    lines.append("")

                result = "\n".join(lines)
                result = retain_excerpt(mixin, result, max_length)
                return result

            else:  # text (default)
                text = mixin._web_client.extract_text(soup, max_length=None)
                header = f"Page: {title}\nURL: {url}\nLength: {len(text):,} chars\n\n"
                if len(text.strip()) < _NEAR_EMPTY_CHARS:
                    return header + text + _NEAR_EMPTY_NOTE
                return header + text_window(text, url, max_length, offset, find)

        @tool(atomic=True)
        def search_web(
            query: str,
            num_results: int = 5,
        ) -> str:
            """Search the web and return results with titles, URLs, and snippets.

            Uses DuckDuckGo to find relevant web pages. Returns titles, URLs, and
            brief descriptions. Use fetch_page to read the full content of any result.

            Args:
                query: Search query string
                num_results: Number of results to return (default: 5, max: 10)
            """
            if not _ensure_web_client():
                return "Error: Browser tools not initialized. Web search is disabled."

            # Clamp num_results
            num_results = max(1, min(num_results, 10))

            try:
                results = mixin._web_client.search_duckduckgo(
                    query, num_results=num_results
                )
            except ImportError as e:
                return f"Error: {e}"
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error searching web: {e}")
                return (
                    f"Error performing web search: {e}\n"
                    "Try using fetch_page with a direct URL instead."
                )

            if not results:
                return (
                    f'No results found for: "{query}"\n\n'
                    "Try different search terms or use fetch_page with a direct URL."
                )

            lines = [f'Web search results for: "{query}"', ""]
            for i, result in enumerate(results, 1):
                lines.append(f"{i}. {result['title']}")
                lines.append(f"   {result['url']}")
                if result.get("snippet"):
                    lines.append(f"   {result['snippet']}")
                lines.append("")

            lines.append("Use fetch_page(url) to read the full content of any result.")
            return "\n".join(lines)

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

            Never replaces a file that already exists: if the destination is
            taken the download fails, so pass filename= to choose a free name.

            Args:
                url: Direct URL to the file to download
                save_to: Local directory to save the file (default: ~/Downloads)
                filename: Override filename (default: derived from URL or Content-Disposition)
            """
            if not _ensure_web_client():
                return "Error: Browser tools not initialized. Download is disabled."

            # Validate save path with PathValidator if available
            if hasattr(mixin, "_path_validator") and mixin._path_validator:
                resolved_dir = str(Path(save_to).expanduser().resolve())
                # Allowlist check — may prompt the user in an interactive TTY;
                # auto-denies in Agent UI / API server contexts (see #495 S1).
                if not mixin._path_validator.is_path_allowed(
                    resolved_dir, prompt_user=True
                ):
                    return f"Error: Access denied to directory: {save_to}"
                # Blocklist check — prevents downloading into /etc, /bin,
                # ~/.ssh, etc. even if the allowlist somehow let it through.
                is_blocked, reason = mixin._path_validator.is_write_blocked(
                    resolved_dir
                )
                if is_blocked:
                    return f"Error: {reason}"

            # Screen the *resolved file path* (not just the directory) against
            # the sensitive-filename guardrail before a single byte is written.
            # The directory check above catches blocked dirs, but the final
            # filename (from Content-Disposition or URL) could be something
            # like `credentials.json` that is_write_blocked would reject for
            # write_file. WebClient.download calls this before creating the
            # file, so a blocked download leaves the filesystem untouched.
            def _screen_destination(dest_path: Path) -> None:
                validator = getattr(mixin, "_path_validator", None)
                if validator:
                    is_blocked, reason = validator.is_write_blocked(str(dest_path))
                    if is_blocked:
                        raise ValueError(
                            f"Downloaded file blocked by security policy: {reason}"
                        )
                before_change(dest_path, "download_file")

            try:
                result = mixin._web_client.download(
                    url=url,
                    save_dir=save_to,
                    filename=filename,
                    on_path_resolved=_screen_destination,
                )
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error downloading {url}: {e}")
                return f"Error downloading file: {e}"

            # Format file size
            size_bytes = result["size"]
            if size_bytes >= 1024 * 1024:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
            elif size_bytes >= 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = f"{size_bytes} bytes"

            return (
                f"Downloaded: {result['filename']}\n"
                f"  Saved to: {result['path']}\n"
                f"  Size: {size_str}\n"
                f"  Type: {result['content_type']}\n\n"
                f"Use read_file or index_document to process this file."
            )
