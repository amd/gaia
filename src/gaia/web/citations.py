# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which URLs a report cites, and which of them the agent actually read.

Shared by the agent's run-time citation check on research turns and the
research eval (:mod:`gaia.eval.research_quality`). Pure text work: no model,
no network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

#: Tools that read a page's content, in either harness.
FETCH_TOOLS = frozenset({"fetch_page", "WebFetch", "fetch_url", "browse_url"})
#: Tools that search the web, in either harness.
SEARCH_TOOLS = frozenset({"search_web", "WebSearch", "web_search"})

#: A Markdown link: ``[text](url)``; the URL may hold one level of parentheses
#: (``.../Python_(programming_language)``).
_MD_LINK = re.compile(r"\[([^\]\n]*)\]\((https?://(?:[^\s()<>]|\([^\s()<>]*\))+)\)")
_BARE_URL = re.compile(r"https?://[^\s<>\"'`\])]+(?:\([^\s()<>]*\)[^\s<>\"'`\])]*)?")
#: Trailing punctuation that ends a sentence, not a URL.
_URL_TAIL = ".,;:!?*_"
#: A numbered citation in prose: ``[1]``, ``[2, 5]``, ``[3][4]``, ``[1-3]``,
#: or a Markdown footnote ``[^2]``.
_REF_MARK = re.compile(r"\[\^?(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")
#: What a sources section is called.
_SOURCES_WORDS = re.compile(
    r"(sources|references|bibliography|citations|works cited|further reading)\b",
    re.IGNORECASE,
)
#: Heading markup: ``## Sources``, ``**Sources**``, ``## 5. Sources``.
_HEADING_MARKUP = re.compile(r"^\s*(?:#{1,6}\s*|\*\*\s*)(?:\d+[.)]\s*)?")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s+\S|\*\*[^*\n]+\*\*:?\s*$)")
#: A source definition: ``[1]: url``, ``[^1]: url``, ``[1] Title — url``,
#: ``1. Title url``.
_REF_DEF = re.compile(r"^\s*(?:[-*]\s*)?(?:\[\^?(\d{1,3})\]:?|(\d{1,3})[.)])\s+(.*)$")
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|ref_src$)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def _strip_tail(url: str) -> str:
    while url and url[-1] in _URL_TAIL:
        url = url[:-1]
    # A closing parenthesis belongs to the URL only when it opened one.
    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def normalize_url(url: str) -> str:
    """One spelling per page: scheme, ``www.``, fragment, trailing slash and
    tracking parameters do not make a different source."""
    url = _strip_tail(url.strip())
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    # Decoded, so `CRISPR%E2%80%93Cas9` and `CRISPR–Cas9` are one page.
    path = unquote(parts.path).rstrip("/") or ""
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)]
    )
    query = unquote(query)
    return urlunsplit(("https", host, path, query, ""))


def domain(url: str) -> str:
    host = urlsplit(normalize_url(url)).hostname or ""
    return host


# ---------------------------------------------------------------------------
# The report: citations and the sentences that carry them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """One claim in the report and the sources it is attached to."""

    sentence: str
    urls: Tuple[str, ...]


def _urls_in(text: str) -> List[str]:
    found = [_strip_tail(m.group(2)) for m in _MD_LINK.finditer(text)]
    rest = _MD_LINK.sub(" ", text)
    found += [_strip_tail(m.group(0)) for m in _BARE_URL.finditer(rest)]
    return [u for u in found if u]


def _is_sources_heading(line: str) -> bool:
    """A line that opens the sources list, not a sentence that starts "Sources".

    With heading markup (``## Sources consulted``, ``**References**``) the name
    may be followed by a few words; a plain line must be the name alone
    (``Sources:``).
    """
    markup = _HEADING_MARKUP.match(line)
    text = line[markup.end() :] if markup else line.strip()
    text = text.strip().strip("*").strip().rstrip(":").strip()
    if not _SOURCES_WORDS.match(text):
        return False
    if markup and markup.group(0).strip():
        return len(text) <= 40
    return _SOURCES_WORDS.fullmatch(text) is not None


def _split_sources(report: str) -> Tuple[List[str], List[str]]:
    """The report's body lines and its sources-section lines."""
    body, sources, in_sources = [], [], False
    for line in report.splitlines():
        if _is_sources_heading(line):
            in_sources = True
            continue
        if in_sources and _HEADING.match(line):
            in_sources = False
        (sources if in_sources else body).append(line)
    return body, sources


def _reference_map(lines: Iterable[str]) -> Dict[int, str]:
    """Source number -> URL. An entry may put its URL on the line after it."""
    refs: Dict[int, str] = {}
    waiting: Optional[int] = None
    for line in lines:
        match = _REF_DEF.match(line)
        if match:
            number = int(match.group(1) or match.group(2))
            urls = _urls_in(match.group(3))
            if urls:
                refs.setdefault(number, urls[0])
            waiting = None if urls else number
            continue
        urls = _urls_in(line)
        if waiting is not None and urls:
            refs.setdefault(waiting, urls[0])
        if line.strip():
            waiting = None
    return refs


def _expand_marks(mark: str) -> List[int]:
    numbers: List[int] = []
    for part in re.split(r"\s*,\s*", mark):
        bounds = re.split(r"\s*[–-]\s*", part)
        if len(bounds) == 2 and all(b.isdigit() for b in bounds):
            low, high = int(bounds[0]), int(bounds[1])
            if 0 < high - low <= 20:
                numbers.extend(range(low, high + 1))
                continue
        numbers.extend(int(b) for b in bounds if b.isdigit())
    return numbers


def _sentences(lines: Sequence[str]) -> List[str]:
    out = []
    for line in lines:
        text = line.strip()
        if not text or set(text) <= set("|-: "):
            continue
        # Shield URLs so a dot inside one never ends a sentence.
        shielded = _MD_LINK.sub(lambda m: m.group(0).replace(". ", ". "), text)
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\[\"(*])", shielded):
            part = part.strip()
            if part:
                out.append(part)
    return out


def citations(report: str) -> List[Citation]:
    """Every sentence of the body that cites something, with the URLs it cites.

    Inline links count where they stand; a numbered mark (``[3]``) counts as
    the URL the sources list gives that number. The sources list itself is not
    a claim.
    """
    body, source_lines = _split_sources(report)
    refs = _reference_map(source_lines)
    defined_in_body = not refs
    if defined_in_body:
        refs = _reference_map(body)
    found = []
    for sentence in _sentences(body):
        definition = _REF_DEF.match(sentence)
        if (
            defined_in_body
            and definition
            and _urls_in(sentence)
            and int(definition.group(1) or definition.group(2)) in refs
        ):
            # A reference definition outside a headed section is not a claim.
            continue
        urls = list(_urls_in(sentence))
        for mark in _REF_MARK.finditer(_MD_LINK.sub(" ", sentence)):
            urls += [refs[n] for n in _expand_marks(mark.group(1)) if n in refs]
        if urls:
            found.append(Citation(sentence, tuple(dict.fromkeys(urls))))
    return found


def cited_urls(report: str) -> List[str]:
    """Every URL the report names, body and sources list, normalized and in order."""
    return list(dict.fromkeys(normalize_url(u) for u in _urls_in(report)))


def has_sources_section(report: str) -> bool:
    return any(_is_sources_heading(line) for line in report.splitlines())


# ---------------------------------------------------------------------------
# The tool record: what was fetched, what was only seen
# ---------------------------------------------------------------------------


def _result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(
            str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in result
        )
    if isinstance(result, dict):
        parts = [
            _result_text(result.get(k))
            for k in ("output", "content", "result", "text", "message")
            if result.get(k)
        ]
        return "\n".join(parts) if parts else json.dumps(result, default=str)
    return str(result)


#: ``fetch_page`` on a PDF or image: nothing was read, it says to download it.
_NOT_TEXT = "returns binary content"


def _failed(result: Any, text: str) -> bool:
    """A call that brought back nothing the agent could read."""
    head = text.lstrip()[:200].lower()
    if not head or _NOT_TEXT in head:
        return True
    if isinstance(result, dict):
        if "is_error" in result:
            # Claude Code's own flag; its WebFetch summary may start "Error".
            return bool(result["is_error"])
        if result.get("status") == "error":
            return True
        if result.get("error") and not result.get("output"):
            return True
    return (
        head.startswith("error")
        or head.startswith("request failed")
        or "error fetching page" in head
    )


@dataclass
class ToolEvidence:
    """What the agent's tools gave it: pages read, and everything else seen."""

    #: Normalized URL -> the text the agent read from it (all reads joined).
    pages: Dict[str, str] = field(default_factory=dict)
    #: Every normalized URL that appears in any tool argument or result.
    seen: Set[str] = field(default_factory=set)
    searches: int = 0
    #: Searches the engine refused or that errored: none of their results came back.
    failed_searches: int = 0
    fetches: int = 0
    failed_fetches: int = 0


def evidence_from_calls(calls: Iterable[Tuple[str, Any, Any]]) -> ToolEvidence:
    """Fold ``(tool name, arguments, result)`` calls into what the agent read."""
    evidence = ToolEvidence()
    for name, args, result in calls:
        args = args if isinstance(args, dict) else {}
        text = _result_text(result)
        given = json.dumps(args, default=str, ensure_ascii=False)
        for url in _urls_in(given) + _urls_in(text):
            evidence.seen.add(normalize_url(url))
        if name in SEARCH_TOOLS:
            evidence.searches += 1
            if _failed(result, text):
                evidence.failed_searches += 1
            continue
        if name not in FETCH_TOOLS:
            continue
        evidence.fetches += 1
        url = str(args.get("url") or "")
        if not url or _failed(result, text):
            evidence.failed_fetches += 1
            continue
        key = normalize_url(url)
        evidence.pages[key] = f"{evidence.pages.get(key, '')}\n{text}".strip()
        evidence.seen.add(key)
    return evidence


def uncited_reads(report: str, evidence: ToolEvidence) -> Tuple[List[str], List[str]]:
    """The report's cited links that were never opened, and those seen nowhere.

    Returns ``(unread, invented)`` as the report spells them, so a reader can
    find each one: *unread* appear in a search result or a page's links but
    were never fetched; *invented* appear in no tool argument or result at all,
    usually a link the model altered or made up.
    """
    spelled: Dict[str, str] = {}
    for url in _urls_in(report):
        spelled.setdefault(normalize_url(url), url)
    invented = [raw for key, raw in spelled.items() if key not in evidence.seen]
    unread = [
        raw
        for key, raw in spelled.items()
        if key not in evidence.pages and key in evidence.seen
    ]
    return unread, invented
