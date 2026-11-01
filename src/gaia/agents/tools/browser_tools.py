# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access

"""
Browser Tools for web content extraction and search.

Provides lightweight web browsing tools using requests + BeautifulSoup
(no Playwright or browser binaries). Enables agents to fetch web pages,
search the web, and download files for local analysis.
"""

import bisect
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, Union

from gaia.agents.base.file_snapshots import before_change
from gaia.agents.tools.workspace_paths import _workspace_target
from gaia.tool_cancellation import ToolCancelled, tool_cancelled

if TYPE_CHECKING:
    from gaia.web.client import SearchUnavailableError

logger = logging.getLogger(__name__)

#: Characters of context kept on each side of a ``find`` match.
FIND_CONTEXT_CHARS = 300

#: Text this short means the page renders its content with JavaScript.
_NEAR_EMPTY_CHARS = 200

#: Do not suggest link extraction for the one incidental link most articles have.
_LINK_HINT_MINIMUM = 3

_NEAR_EMPTY_NOTE = (
    "\n\n[Almost no text on this page: its content is probably rendered by "
    "JavaScript, which fetch_page does not run. Open it with a real-browser tool "
    "if you have one; otherwise try another source, or report the value as not "
    "found.]"
)


def _download_denied(validator, dest_path: Path) -> str:
    """Why *dest_path* may not be written, and where a download can go instead."""
    message = validator.access_denied_message(str(dest_path))
    if not validator.grant_refusal_reason(str(dest_path)):
        return message
    downloads = Path(os.path.realpath(os.path.expanduser("~/Downloads")))
    if Path(os.path.realpath(dest_path.parent)) == downloads:
        return f"{message} Save it inside a folder this session already allows."
    return (
        f"{message} Save it somewhere else instead, such as save_to='~/Downloads' "
        "or a folder this session already allows."
    )


#: Consecutive refused searches after which ``search_web`` stops asking the
#: loop to wait and retry, so the failure breaker can end a dead search loop.
SEARCH_UNAVAILABLE_STREAK_LIMIT = 3

_TAVILY_CONNECTOR_ID = "mcp-tavily"

#: Parallel ``search_web`` calls share one refusal streak per agent.
_STREAK_LOCK = threading.Lock()

_NO_BACKUP_SEARCH_NOTE = (
    "No backup search provider is set up. Tell the user that adding a Tavily "
    "API key (gaia connectors configure mcp-tavily --set TAVILY_API_KEY=...) "
    "lets search keep working when DuckDuckGo throttles it."
)


def _search_stop_requested(mixin) -> bool:
    """True once the user stopped the turn or the agent gave up on the tool."""
    cancel_event = getattr(mixin, "_cancel_event", None)
    console_cancelled = getattr(getattr(mixin, "console", None), "cancelled", None)
    return (
        (cancel_event is not None and cancel_event.is_set())
        or (console_cancelled is not None and console_cancelled.is_set())
        or tool_cancelled()
    )


def _tavily_fallback(
    query: str, num_results: int
) -> Tuple[Optional[List[Dict[str, str]]], Optional[str]]:
    """Results from the Tavily connector when the user configured one.

    Returns ``(results, None)``, ``(None, None)`` when Tavily is not
    configured, or ``(None, error)`` when it is configured but failed.
    """
    from gaia.connectors.mcp_server import is_mcp_server_configured

    try:
        if not is_mcp_server_configured(_TAVILY_CONNECTOR_ID):
            return None, None
        from gaia.web.tavily import TavilyClient

        client = TavilyClient()
        try:
            if not client.configured:
                return None, None
            response = client.search(query, max_results=num_results)
        finally:
            client.close()
    # Tool boundary: the error is reported to the model, never swallowed.
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Tavily fallback search failed: %s", e)
        return None, f"The Tavily connector also failed: {e}"
    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
        }
        for r in response.get("results", [])
        if r.get("url")
    ]
    return results, None


def _search_unavailable_result(
    mixin, error: "SearchUnavailableError", fallback_error: Optional[str]
) -> Dict[str, Any]:
    """What the model gets when every search route refused the query.

    Marked ``rate_limited`` so the loop waits it out instead of counting it as
    a repeat or a tool failure; after a streak of refusals the mark is dropped
    so the failure breaker can stop a search loop that cannot recover.
    """
    with _STREAK_LOCK:
        streak = getattr(mixin, "_search_unavailable_streak", 0) + 1
        mixin._search_unavailable_streak = streak
    detail = f" {fallback_error}" if fallback_error else ""
    backup = "" if fallback_error else f" {_NO_BACKUP_SEARCH_NOTE}"
    if streak >= SEARCH_UNAVAILABLE_STREAK_LIMIT:
        return {
            "status": "error",
            "error": (
                f"Web search is temporarily unavailable: {error}{detail} "
                f"{streak} searches in a row were refused, so stop calling "
                "search_web for now."
            ),
            "retryable": False,
            "hint": (
                "Call fetch_page on a URL you already know (an official site, "
                "documentation page or Wikipedia article), or answer with what "
                "you have and say web search was unavailable. Do not invent "
                f"search results.{backup}"
            ),
        }
    return {
        "status": "error",
        "error": f"Web search is temporarily unavailable: {error}{detail}",
        "rate_limited": True,
        "wait_time_seconds": round(error.retry_after, 1),
        "retryable": True,
        "hint": (
            "Try search_web again shortly, with a narrower query, or call "
            "fetch_page on a URL you already know. Do not invent search results."
        ),
    }


def _merge_spans(spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge overlapping ``(start, end)`` character windows."""
    merged: List[Tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _find_spans(text: str, term: str) -> List[Tuple[int, int]]:
    """Merged windows around each case-insensitive exact match of *term*."""
    spans: List[Tuple[int, int]] = []
    haystack, needle = text.lower(), term.lower()
    at = haystack.find(needle)
    while at != -1:
        spans.append(
            (
                max(0, at - FIND_CONTEXT_CHARS),
                min(len(text), at + len(needle) + FIND_CONTEXT_CHARS),
            )
        )
        at = haystack.find(needle, at + len(needle))
    return _merge_spans(spans)


def _find_all_words_spans(text: str, term: str) -> List[Tuple[int, int]]:
    """Windows containing every distinct word in multi-word *term*.

    This is deliberately a fallback for a failed exact phrase search.  It does
    not claim that the words are adjacent or that the phrase occurs verbatim;
    it gives the agent a passage to inspect when a page separates a query such
    as ``Sunday Pier 9`` across a table or sentence.
    """
    # Only a phrase splits: "$1,299" must not match "1" and "299" separately.
    if len(term.split()) < 2:
        return []
    words = list(dict.fromkeys(re.findall(r"\w+", term.lower())))
    if len(words) < 2:
        return []
    # One named group per word: a case-folded match ("ſ" for "s") need not
    # lower() back to the word it matched.
    pattern = re.compile(
        r"\b(?:"
        + "|".join(f"(?P<w{i}>{re.escape(word)})" for i, word in enumerate(words))
        + r")\b",
        re.IGNORECASE,
    )
    starts: List[List[int]] = [[] for _ in words]
    ends: List[List[int]] = [[] for _ in words]
    for match in pattern.finditer(text):
        index = int(match.lastgroup[1:])
        starts[index].append(match.start())
        ends[index].append(match.end())
    if any(not found for found in starts):
        return []

    # Bisect, not a scan per anchor: common words on a long page would make
    # this quadratic and stall the tool call. The window is two-sided, so the
    # page may list the words in any order ("Pier 9 ... on Sunday").
    window = FIND_CONTEXT_CHARS * 2
    spans = []
    for anchor in starts[0]:
        first = last = anchor
        for word_starts, word_ends in zip(starts, ends):
            at = bisect.bisect_left(word_starts, anchor - window)
            if at == len(word_starts) or word_starts[at] > anchor + window:
                break
            first = min(first, word_starts[at])
            last = max(last, word_ends[at])
        else:
            spans.append(
                (
                    max(0, first - FIND_CONTEXT_CHARS),
                    min(len(text), last + FIND_CONTEXT_CHARS),
                )
            )
    return _merge_spans(spans)


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
        fallback = False
        if not spans:
            # Exact phrase matching misses ordinary page layouts such as a
            # timetable that keeps ``Sunday`` and ``Pier 9`` in separate cells.
            # Return a clearly labelled all-words passage before suggesting the
            # page lacks the answer.
            spans = [
                (max(start, offset), end)
                for start, end in _find_all_words_spans(text, term)
                if end > offset
            ]
            fallback = bool(spans)
        if not spans:
            where = f"after character {offset:,} of" if offset else "in"
            return (
                f"[No exact occurrence of {term!r} {where} the page's {total:,} "
                "characters of text, and its words were not found together nearby. "
                "Do not infer an answer from this miss. Try one distinctive word, "
                "read on with offset, or inspect links before reporting it as not found.]"
            )
        parts, used = [], 0
        for number, (start, end) in enumerate(spans):
            label = (
                f"[Exact phrase {term!r} was not found; this passage contains all "
                f"of its words near character {start:,}]\n"
                if fallback
                else f"[at character {start:,}]\n"
            )
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
                from gaia.web.client import fetch_error_text

                logger.error(f"Error fetching {url}: {e}")
                return f"Error fetching page: {fetch_error_text(e)}"

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
                links = mixin._web_client.extract_links(soup, url)
                links_hint = ""
                if len(links) >= _LINK_HINT_MINIMUM:
                    links_hint = (
                        f"\n\n[{len(links)} links on this page. Call "
                        f"fetch_page({url!r}, extract='links') to inspect and follow them.]"
                    )
                if len(text.strip()) < _NEAR_EMPTY_CHARS:
                    return header + text + _NEAR_EMPTY_NOTE + links_hint
                return (
                    header
                    + text_window(text, url, max_length, offset, find)
                    + links_hint
                )

        @tool(atomic=True)
        def search_web(
            query: str,
            num_results: int = 5,
        ) -> Union[str, Dict[str, Any]]:
            """Search the web and return results with titles, URLs, and snippets.

            Uses DuckDuckGo to find relevant web pages. Returns titles, URLs, and
            brief descriptions. Use fetch_page to read the full content of any result.

            Args:
                query: Search query string
                num_results: Number of results to return (default: 5, max: 10)
            """
            if not _ensure_web_client():
                return "Error: Browser tools not initialized. Web search is disabled."

            from gaia.web.client import SearchUnavailableError

            # Clamp num_results
            num_results = max(1, min(num_results, 10))

            source_note = None
            try:
                results = mixin._web_client.search_duckduckgo(
                    query,
                    num_results=num_results,
                    should_stop=lambda: _search_stop_requested(mixin),
                )
            except ToolCancelled as e:
                return f"Web search stopped: {e}."
            except ImportError as e:
                return f"Error: {e}"
            except ValueError as e:
                return f"Error: {e}"
            except SearchUnavailableError as e:
                logger.warning("Web search unavailable: %s", e)
                results, fallback_error = _tavily_fallback(query, num_results)
                if results is None:
                    return _search_unavailable_result(mixin, e, fallback_error)
                logger.warning(
                    "DuckDuckGo refused the search; answered by the Tavily "
                    "connector instead (%d results)",
                    len(results),
                )
                source_note = (
                    "(DuckDuckGo is rate-limiting searches; these results are "
                    "from the configured Tavily connector.)"
                )
            except Exception as e:
                logger.error(f"Error searching web: {e}")
                return (
                    f"Error performing web search: {e}\n"
                    "Try using fetch_page with a direct URL instead."
                )
            with _STREAK_LOCK:
                mixin._search_unavailable_streak = 0

            if not results:
                return (
                    f'No results found for: "{query}"\n\n'
                    "Try different search terms or use fetch_page with a direct URL."
                )

            lines = [f'Web search results for: "{query}"', ""]
            if source_note:
                lines[1:1] = [source_note]
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

            validator = getattr(mixin, "_path_validator", None)
            if not Path(save_to).expanduser().is_absolute():
                save_to = str(_workspace_target(mixin, save_to))

            # Refuse a blocked folder (/etc, ~/.ssh, ...) before any request.
            if validator:
                resolved_dir = str(Path(save_to).expanduser().resolve())
                is_blocked, reason = validator.is_write_blocked(resolved_dir)
                if is_blocked:
                    return f"Error: {reason}"

            # WebClient calls this with the final file path before creating
            # anything, so a refusal leaves the filesystem untouched.
            def _screen_destination(dest_path: Path) -> None:
                if validator:
                    is_blocked, reason = validator.is_write_blocked(str(dest_path))
                    if is_blocked:
                        raise ValueError(
                            f"Downloaded file blocked by security policy: {reason}"
                        )
                    # Ask about the folder when it can be granted whole, so one
                    # approval covers later downloads; else (e.g. ~) the one file.
                    # The response stays open while the user answers.
                    target = dest_path.parent
                    if validator.grant_refusal_reason(str(target)):
                        target = dest_path
                    if not validator.is_path_allowed(str(target), prompt_user=True):
                        raise ValueError(_download_denied(validator, dest_path))
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
