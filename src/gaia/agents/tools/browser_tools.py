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
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, Union
from urllib.parse import urlsplit

from gaia.agents.base.file_snapshots import before_change
from gaia.agents.base.verification import NOT_EXECUTED
from gaia.agents.tools.workspace_paths import _workspace_target
from gaia.tool_cancellation import ToolCancelled, tool_cancelled

if TYPE_CHECKING:
    from gaia.connectors.spec import ConnectorSpec
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


class _DownloadRefused(ValueError):
    """The destination screen refused the file before anything was written."""


def _download_refusal(reason: str) -> Dict[str, Any]:
    """A download the write policy stopped before it ran."""
    return {**NOT_EXECUTED, "status": "error", "error": reason}


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

#: Searched through GAIA's own client (cache, credit ledger), not its MCP tool.
_TAVILY_CONNECTOR_ID = "mcp-tavily"

#: Parallel ``search_web`` calls share one refusal streak per agent.
_STREAK_LOCK = threading.Lock()

#: Distinct queries a session remembers, and for how long, so a query the
#: model repeats is answered without spending another DuckDuckGo request.
SEARCH_CACHE_SIZE = 128
SEARCH_CACHE_TTL_SECONDS = 30 * 60

_REPEAT_SEARCH_NOTE = (
    "(You already ran this search, so these are the same results. Search with "
    "different words, or fetch_page a result, to learn more.)"
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


def _cache_key(query: str) -> str:
    """Case and spacing don't change what a search finds."""
    return " ".join(query.casefold().split())


class _SearchCache:
    """A session's recent search results, keyed by normalized query."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, _CachedSearch]" = OrderedDict()

    def get(
        self, query: str, num_results: int
    ) -> Optional[Tuple[List[Dict[str, str]], Optional[str]]]:
        key = _cache_key(query)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            stored_at, asked, results, source_note = entry
            if time.monotonic() - stored_at > SEARCH_CACHE_TTL_SECONDS:
                del self._entries[key]
                return None
            # A bigger ask needs a new search, unless the last one already
            # returned everything there was.
            if num_results > asked and len(results) >= asked:
                return None
            self._entries.move_to_end(key)
            return results[:num_results], source_note

    def put(
        self,
        query: str,
        num_results: int,
        results: List[Dict[str, str]],
        source_note: Optional[str],
    ) -> None:
        key = _cache_key(query)
        with self._lock:
            self._entries[key] = (time.monotonic(), num_results, results, source_note)
            self._entries.move_to_end(key)
            while len(self._entries) > SEARCH_CACHE_SIZE:
                self._entries.popitem(last=False)


#: (stored at, results asked for, results, source note)
_CachedSearch = Tuple[float, int, List[Dict[str, str]], Optional[str]]


def _search_connectors() -> List["ConnectorSpec"]:
    """Every catalog connector that can answer a web search, in catalog order."""
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.registry import REGISTRY
    from gaia.connectors.spec import WEB_SEARCH

    return [spec for spec in REGISTRY if WEB_SEARCH in spec.provides]


def _no_backup_search_note() -> str:
    names = " or ".join(spec.display_name for spec in _search_connectors())
    return (
        "No backup search provider is set up. Tell the user that adding a search "
        f"connector ({names}) in Settings > Connectors lets search keep working "
        "when DuckDuckGo limits it."
    )


def _tavily_search(query: str, num_results: int) -> Optional[List[Dict[str, str]]]:
    """Results from GAIA's Tavily client, or ``None`` when it holds no key."""
    from gaia.web.tavily import TavilyClient

    client = TavilyClient()
    try:
        if not client.configured:
            return None
        response = client.search(query, max_results=num_results)
    finally:
        client.close()
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
        }
        for r in response.get("results", [])
        if r.get("url")
    ]


#: Labels search servers put before each field of a plain-text result.
_TEXT_RESULT_FIELDS = {
    "title": "title",
    "url": "url",
    "link": "url",
    "description": "snippet",
    "content": "snippet",
    "snippet": "snippet",
}

#: A search result's snippet is a summary; longer text is page content.
_SNIPPET_CHARS = 500

_FIELD_LABEL = re.compile(r"[A-Za-z][A-Za-z ]{0,30}$")

#: Argument names search tools use for how many results to return.
_RESULT_COUNT_ARGS = ("count", "max_results", "num_results", "limit")


def _parse_search_text(text: str) -> List[Dict[str, str]]:
    """Results from a search tool's answer.

    Reads JSON with a ``results`` list, or ``Title:`` / ``URL:`` /
    ``Description:`` blocks, the plain-text shape search servers return.
    """
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return [
            {
                "title": str(r.get("title") or ""),
                "url": str(r["url"]),
                "snippet": str(
                    r.get("content") or r.get("description") or r.get("snippet") or ""
                ),
            }
            for r in data["results"]
            if isinstance(r, dict) and r.get("url")
        ]
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    last_field = None
    for line in text.splitlines():
        label, sep, value = line.partition(":")
        name = _TEXT_RESULT_FIELDS.get(label.strip().lower()) if sep else None
        if name is None:
            # A wrapped snippet continues; a blank line or another field
            # ("Raw Content:", "Favicon:") ends it.
            if not line.strip() or (sep and _FIELD_LABEL.match(label)):
                last_field = None
            elif last_field == "snippet":
                current["snippet"] += " " + line.strip()
            continue
        # A repeated field, or a title after other fields, starts the next result.
        if name in current or (name == "title" and current):
            records.append(current)
            current = {}
        current[name] = value.strip()
        last_field = name
    records.append(current)
    return [
        {
            "title": r.get("title", ""),
            "url": r["url"],
            "snippet": r.get("snippet", "")[:_SNIPPET_CHARS],
        }
        for r in records
        if r.get("url")
    ]


def _mcp_connector_search(
    mixin, spec: "ConnectorSpec", query: str, num_results: int
) -> List[Dict[str, str]]:
    """Run *spec*'s search tool on the MCP server this agent already runs."""
    from gaia.mcp.client.mcp_client import mcp_tool_error

    manager = getattr(mixin, "_mcp_manager", None)
    active_servers = getattr(mixin, "_active_mcp_servers", None)
    active = active_servers(manager) if manager and active_servers else []
    client = manager.get_client(spec.id) if spec.id in active else None
    if client is None:
        failure = manager.failure(spec.id) if manager else None
        reason = f" ({failure})" if failure else ""
        raise RuntimeError(
            f"it is set up but not running for this agent{reason}. Turn it on "
            "for this agent in Settings > Connectors"
        )
    tool = next(
        (t for t in client.list_tools() if t.name == spec.web_search_tool), None
    )
    if tool is None:
        raise RuntimeError(f"its server has no {spec.web_search_tool} tool")
    properties = (tool.input_schema or {}).get("properties") or {}
    arguments: Dict[str, Any] = {"query": query}
    count_arg = next((a for a in _RESULT_COUNT_ARGS if a in properties), None)
    if count_arg:
        arguments[count_arg] = num_results
    result = client.call_tool(tool.name, arguments)
    error = mcp_tool_error(result)
    if error:
        raise RuntimeError(error)
    text = "\n".join(
        item.get("text", "")
        for item in result.get("content") or []
        if isinstance(item, dict) and item.get("type", "text") == "text"
    ).strip()
    results = _parse_search_text(text)
    if text and not results:
        raise RuntimeError(f"GAIA could not read results from its answer: {text[:200]}")
    return results[:num_results]


def _connector_fallback(
    mixin, query: str, num_results: int
) -> Tuple[Optional[List[Dict[str, str]]], Optional[str], List[str]]:
    """Results from the first configured search connector that answers.

    Returns ``(results, connector name, errors)``. ``results`` is ``None`` when
    none answered; ``errors`` then says why each configured one failed, and is
    empty when none is configured.
    """
    from gaia.connectors.mcp_server import (
        is_mcp_server_configured,
        is_mcp_server_enabled,
    )

    errors: List[str] = []
    for spec in _search_connectors():
        try:
            if not is_mcp_server_configured(spec.id) or not is_mcp_server_enabled(
                spec.id
            ):
                continue
            if spec.id == _TAVILY_CONNECTOR_ID:
                results = _tavily_search(query, num_results)
                if results is None:
                    continue
            else:
                results = _mcp_connector_search(mixin, spec, query, num_results)
        # Tool boundary: the error is reported to the model, never swallowed.
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("%s fallback search failed: %s", spec.display_name, e)
            errors.append(f"The {spec.display_name} connector also failed: {e}")
            continue
        return results, spec.display_name, errors
    return None, None, errors


def _search_unavailable_result(
    mixin, error: "SearchUnavailableError", fallback_errors: List[str]
) -> Dict[str, Any]:
    """What the model gets when every search route refused the query.

    Marked ``rate_limited`` so the loop waits it out instead of counting it as
    a repeat or a tool failure; after a streak of refusals the mark is dropped
    so the failure breaker can stop a search loop that cannot recover.
    """
    with _STREAK_LOCK:
        streak = getattr(mixin, "_search_unavailable_streak", 0) + 1
        mixin._search_unavailable_streak = streak
    detail = "".join(f" {e}" for e in fallback_errors)
    backup = "" if fallback_errors else f" {_no_backup_search_note()}"
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


#: Appended to a result's title line; the Agent UI strips it from source titles.
PRIMARY_SOURCE_MARKER = " [primary source]"

#: Hosts whose first path segment is the owner and second is the repository.
_REPO_HOSTS = frozenset(
    {"github.com", "gitlab.com", "codeberg.org", "bitbucket.org", "huggingface.co"}
)

#: Package registries, keyed by host, with the path prefix before the name.
_REGISTRY_PREFIXES = {
    "pypi.org": ("project",),
    "npmjs.com": ("package",),
    "crates.io": ("crates",),
    "rubygems.org": ("gems",),
    "nuget.org": ("packages",),
    "pkg.go.dev": (),
    "sourceforge.net": ("projects",),
}

#: Query words that describe the question, not the thing it is about.
_GENERIC_QUERY_WORDS = frozenset(
    {
        "about",
        "best",
        "build",
        "changelog",
        "check",
        "cite",
        "current",
        "docs",
        "documentation",
        "download",
        "free",
        "from",
        "guide",
        "install",
        "latest",
        "news",
        "newest",
        "notes",
        "number",
        "official",
        "online",
        "page",
        "recent",
        "release",
        "released",
        "releases",
        "server",
        "site",
        "software",
        "stable",
        "that",
        "this",
        "today",
        "tutorial",
        "update",
        "updates",
        "used",
        "version",
        "versions",
        "website",
        "what",
        "when",
        "where",
        "which",
        "with",
    }
)

_RELEASE_QUERY_WORDS = frozenset(
    {"version", "versions", "release", "releases", "released", "changelog"}
)

_RELEASE_PRIMARY_NOTE = (
    "This is a version or release question. Open the project's own releases, "
    "changelog or download page with fetch_page (results marked [primary "
    "source] come from the project itself) and answer from that page, not "
    "from a search snippet."
)

_RELEASE_NO_PRIMARY_NOTE = (
    "None of these results comes from the project itself (its own site, "
    "repository or package registry). News articles and aggregators often lag "
    "behind the newest release, so do not answer from them: search again for "
    "the project's releases page (for example '<project> releases github') or "
    "fetch it directly, then cite that page."
)


def _query_words(query: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", query.lower())


def _project_words(query: str) -> frozenset:
    """Query words distinctive enough to name the subject's own domain."""
    return frozenset(
        w
        for w in _query_words(query)
        if len(w) >= 4 and not w.isdigit() and w not in _GENERIC_QUERY_WORDS
    )


def _is_release_query(query: str) -> bool:
    return not _RELEASE_QUERY_WORDS.isdisjoint(_query_words(query))


def _is_primary_source(url: str, project_words: frozenset) -> bool:
    """True when *url* is the subject's own site, repository or package page."""
    if not project_words:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    segments = [s for s in parts.path.lower().split("/") if s]
    if host in _REPO_HOSTS:
        # The owner names the project, or the repository is exactly its name:
        # someone's "climate-memes" repo is not climate's own page.
        if not segments:
            return False
        owner = set(segments[0].split("-"))
        repo = segments[1] if len(segments) > 1 else ""
        return not project_words.isdisjoint(owner) or repo in project_words
    if host in _REGISTRY_PREFIXES:
        prefix = _REGISTRY_PREFIXES[host]
        if segments[: len(prefix)] != list(prefix) or len(segments) <= len(prefix):
            return False
        name = segments[len(prefix)]
        return not project_words.isdisjoint(re.split(r"[-_.]", name))
    # Whole label parts only, so "realpython.com" is not python's own site.
    labels = host.split(".")[:-1]
    words = {w for label in labels for w in label.split("-")}
    return not project_words.isdisjoint(words)


def _rank_by_source(
    results: List[Dict[str, str]], query: str
) -> List[Tuple[Dict[str, str], bool]]:
    """Results paired with their primary flag, primary sources first."""
    words = _project_words(query)
    flagged = [(r, _is_primary_source(r.get("url", ""), words)) for r in results]
    return sorted(flagged, key=lambda pair: not pair[1])


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


def _refuse_host_this_turn(host: Any, tool_name: str, tool_args: dict, error) -> None:
    """Refuse a cloud-metadata host the web client blocked to every tool, this turn.

    The block is policy, not a person's no, so nothing may lift it: a shell
    ``curl`` or a Python ``urlopen`` to the same address is refused too. Only
    metadata and link-local targets carry over; loopback and private LAN
    addresses stay reachable from the shell, where reaching them is ordinary
    work (a dev server, a NAS).
    """
    ledger = getattr(host, "_denied_effects", None)
    if ledger is None:
        return
    from gaia.agents.base.denied_effects import NETWORK, is_cloud_metadata_host

    if not (
        is_cloud_metadata_host(error.host) or is_cloud_metadata_host(error.address)
    ):
        return

    ledger.record(
        tool_name,
        tool_args,
        str(error),
        kinds={NETWORK},
        refused=True,
        hosts=[error.host, error.address],
    )
    logger.info("Refusing %s for the rest of this turn: %s", error.host, error)


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

            from gaia.web.client import BlockedHostError

            try:
                response = mixin._web_client.get(url)
                response.raise_for_status()
            except BlockedHostError as e:
                _refuse_host_this_turn(mixin, "fetch_page", {"url": url}, e)
                return f"Error: {e}"
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
            brief descriptions. Use fetch_page to read the full content of any
            result. For a version or release question, the project's own site,
            repository or package page is listed first and marked [primary
            source]: fetch its releases or changelog page and answer from it,
            since snippets and news articles go stale.

            Args:
                query: Search query string
                num_results: Number of results to return (default: 5, max: 10)
            """
            if not _ensure_web_client():
                return "Error: Browser tools not initialized. Web search is disabled."

            from gaia.web.client import SearchUnavailableError

            # Clamp num_results
            num_results = max(1, min(num_results, 10))

            with _STREAK_LOCK:
                cache = getattr(mixin, "_search_cache", None)
                if cache is None:
                    cache = mixin._search_cache = _SearchCache()
            cached = cache.get(query, num_results)
            repeat = cached is not None
            if repeat:
                results, source_note = cached
            else:
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
                    results, connector, errors = _connector_fallback(
                        mixin, query, num_results
                    )
                    if results is None:
                        return _search_unavailable_result(mixin, e, errors)
                    logger.warning(
                        "DuckDuckGo refused the search; answered by the %s "
                        "connector instead (%d results)",
                        connector,
                        len(results),
                    )
                    source_note = (
                        "(DuckDuckGo is rate-limiting searches; these results are "
                        f"from the configured {connector} connector.)"
                    )
                except Exception as e:
                    logger.error(f"Error searching web: {e}")
                    return (
                        f"Error performing web search: {e}\n"
                        "Try using fetch_page with a direct URL instead."
                    )
                if results:
                    cache.put(query, num_results, results, source_note)
            with _STREAK_LOCK:
                mixin._search_unavailable_streak = 0

            if not results:
                return (
                    f'No results found for: "{query}"\n\n'
                    "Try different search terms or use fetch_page with a direct URL."
                )

            lines = [f'Web search results for: "{query}"', ""]
            notes = [n for n in (source_note, repeat and _REPEAT_SEARCH_NOTE) if n]
            lines[1:1] = notes
            # Only release questions: on everyday searches a shared word
            # ("boston" in boston.com) does not make a site the subject's own.
            release_query = _is_release_query(query)
            ranked = (
                _rank_by_source(results, query)
                if release_query
                else [(r, False) for r in results]
            )
            for i, (result, primary) in enumerate(ranked, 1):
                marker = PRIMARY_SOURCE_MARKER if primary else ""
                lines.append(f"{i}. {result['title']}{marker}")
                lines.append(f"   {result['url']}")
                if result.get("snippet"):
                    lines.append(f"   {result['snippet']}")
                lines.append("")

            if release_query:
                any_primary = any(primary for _, primary in ranked)
                lines.append(
                    _RELEASE_PRIMARY_NOTE if any_primary else _RELEASE_NO_PRIMARY_NOTE
                )
            lines.append("Use fetch_page(url) to read the full content of any result.")
            return "\n".join(lines)

        @tool(atomic=True)
        def download_file(
            url: str,
            save_to: str = "~/Downloads",
            filename: str = None,
        ) -> Union[str, Dict[str, Any]]:
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
                    return _download_refusal(reason)

            # WebClient calls this with the final file path before creating
            # anything, so a refusal leaves the filesystem untouched.
            def _screen_destination(dest_path: Path) -> None:
                if validator:
                    is_blocked, reason = validator.is_write_blocked(str(dest_path))
                    if is_blocked:
                        raise _DownloadRefused(
                            f"Downloaded file blocked by security policy: {reason}"
                        )
                    # Ask about the folder when it can be granted whole, so one
                    # approval covers later downloads; else (e.g. ~) the one file.
                    # The response stays open while the user answers.
                    target = dest_path.parent
                    if validator.grant_refusal_reason(str(target)):
                        target = dest_path
                    if not validator.is_path_allowed(str(target), prompt_user=True):
                        raise _DownloadRefused(_download_denied(validator, dest_path))
                before_change(dest_path, "download_file")

            from gaia.web.client import BlockedHostError

            try:
                result = mixin._web_client.download(
                    url=url,
                    save_dir=save_to,
                    filename=filename,
                    on_path_resolved=_screen_destination,
                )
            except _DownloadRefused as e:
                return _download_refusal(str(e))
            except BlockedHostError as e:
                _refuse_host_this_turn(mixin, "download_file", {"url": url}, e)
                return f"Error: {e}"
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
