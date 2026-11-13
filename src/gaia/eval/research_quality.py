# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check a research report's citations against what the agent actually read.

A research report is only as good as its sources, and the failure that hurts a
reader most is a citation that looks real but backs nothing: a URL the agent
never opened, or one it made up. This module reads the report and the agent's
tool record (either harness, through :mod:`gaia.eval.bench.transcripts`) and
answers, without any model:

- which URLs the report cites, and which sentence cites each one;
- which of them the agent fetched (``fetch_page`` / ``WebFetch``) and read;
- which it only saw in a search result, and which appear in no tool result at
  all — an invented source;
- which figures (years, amounts, sizes) in a cited sentence do not appear in
  the page it cites.

:func:`assess` turns that into the research task's mechanical gate;
:func:`citation_evidence` cuts the cited pages down to the passages that
matter, so the judge can tell whether each claim is supported.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from gaia.eval.bench import transcripts

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
#: A figure worth checking: a number with at least two digits (years, sizes,
#: amounts) or a decimal, with or without a unit glued on ("28nm", "$10M").
#: Single digits are too often spelled out in sources. A number inside a name
#: ("RVA23", "v1.0") is not a figure.
_FIGURE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?!\d)")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be been by for from has have in is it its of on or "
    "that the their this to was were which with also than more most into "
    "about after before over under between its it's they them these those".split()
)
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|ref_src$)", re.IGNORECASE)

#: How much page text one claim's evidence may carry, and the whole block.
EXCERPT_CAP = 600
EVIDENCE_CAP = 16000
#: Claims shown to the judge, most specific first.
EVIDENCE_CLAIMS = 24


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


#: Where the harness keeps report files the agent saved, in its transcript.
REPORT_FILES_KEY = "report_files"
#: What counts as a saved report, and how much of each is read.
REPORT_SUFFIXES = (".md", ".markdown", ".txt")
REPORT_FILE_CAP = 200_000
REPORT_FILES_MAX = 5


def collect_report_files(workdir: Path) -> Dict[str, str]:
    """The Markdown or text files the agent saved in its working folder.

    A report saved to a file and linked from the reply is delivered: the reader
    opens it. Largest first, each cut to ``REPORT_FILE_CAP`` characters.
    """
    if not workdir.is_dir():
        return {}
    found = [
        p
        for p in workdir.rglob("*")
        if p.is_file() and p.suffix.lower() in REPORT_SUFFIXES
    ]
    found.sort(key=lambda p: (-p.stat().st_size, str(p)))
    return {
        p.relative_to(workdir).as_posix(): p.read_text(
            encoding="utf-8", errors="replace"
        )[:REPORT_FILE_CAP]
        for p in found[:REPORT_FILES_MAX]
    }


def report_text(transcript: Mapping[str, Any]) -> str:
    """The report a reader got: the reply, then any report file it saved."""
    parts = [str(transcript.get("answer") or "")]
    for name, text in (transcript.get(REPORT_FILES_KEY) or {}).items():
        if text.strip() and text.strip() not in parts[0]:
            parts.append(f"\n\n--- saved report file: {name} ---\n\n{text}")
    return "".join(parts)


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


def tool_evidence(transcript: Mapping[str, Any]) -> ToolEvidence:
    evidence = ToolEvidence()
    for name, args, result in transcripts.tool_calls(transcript):
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


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def figures(sentence: str) -> List[str]:
    """The checkable numbers in *sentence*: outside URLs and citation marks."""
    text = _MD_LINK.sub(lambda m: m.group(1), sentence)
    text = _BARE_URL.sub(" ", text)
    text = _REF_MARK.sub(" ", text)
    out = []
    for match in _FIGURE.finditer(text):
        value = match.group(0).rstrip(".,").replace(",", "")
        if len(value.replace(".", "")) >= 2:
            out.append(value)
    return list(dict.fromkeys(out))


def _has_figure(text: str, value: str) -> bool:
    plain = text.replace(",", "")
    return re.search(rf"(?<![\d.]){re.escape(value)}(?![\d])", plain) is not None


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


@dataclass
class ResearchCheck:
    """The mechanical half of a research task's grade."""

    report_chars: int = 0
    searches: int = 0
    #: Searches that returned nothing because the engine refused or errored.
    #: A run where most searches failed measured research without search.
    failed_searches: int = 0
    fetches: int = 0
    failed_fetches: int = 0
    #: Distinct URLs the report names.
    cited: int = 0
    #: Distinct cited URLs the agent fetched and read.
    cited_fetched: int = 0
    cited_domains: int = 0
    #: Cited, only ever seen in a search result or a page's links.
    unread: List[str] = field(default_factory=list)
    #: Cited, and in no tool argument or result at all.
    invented: List[str] = field(default_factory=list)
    #: ``(url, figure, sentence)``: a figure the cited page does not contain.
    unsupported_figures: List[Tuple[str, str, str]] = field(default_factory=list)
    claims: int = 0
    sources_section: bool = False
    gate_ok: bool = False
    why: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchSpec:
    """What a research task demands of its sources (``tasks.json`` ``research``)."""

    min_sources: int = 3
    min_domains: int = 2
    #: Cited pages the agent never read that a report may carry. Default none:
    #: a source the agent did not open cannot back what it is cited for.
    max_unread: int = 0


def assess(
    report: str, transcript: Mapping[str, Any], spec: ResearchSpec
) -> ResearchCheck:
    """Score the report's sourcing; ``gate_ok`` is the task's mechanical pass."""
    evidence = tool_evidence(transcript)
    check = ResearchCheck(
        report_chars=len(report.strip()),
        searches=evidence.searches,
        failed_searches=evidence.failed_searches,
        fetches=evidence.fetches,
        failed_fetches=evidence.failed_fetches,
        sources_section=has_sources_section(report),
    )
    urls = cited_urls(report)
    read = [u for u in urls if u in evidence.pages]
    check.cited, check.cited_fetched = len(urls), len(read)
    check.cited_domains = len({domain(u) for u in read})
    check.invented = [u for u in urls if u not in evidence.seen]
    check.unread = [u for u in urls if u not in evidence.pages and u in evidence.seen]
    claims = citations(report)
    check.claims = len(claims)
    for claim in claims:
        pages = [
            evidence.pages[normalize_url(u)]
            for u in claim.urls
            if normalize_url(u) in evidence.pages
        ]
        if not pages:
            continue
        for value in figures(claim.sentence):
            if not any(_has_figure(page, value) for page in pages):
                check.unsupported_figures.append(
                    (normalize_url(claim.urls[0]), value, claim.sentence[:240])
                )
    problems = []
    if not check.report_chars:
        problems.append("no report")
    if check.invented:
        problems.append(
            f"cites {len(check.invented)} URL(s) found in no tool result: "
            f"{', '.join(check.invented[:3])}"
        )
    if len(check.unread) > spec.max_unread:
        problems.append(
            f"cites {len(check.unread)} page(s) it never fetched: "
            f"{', '.join(check.unread[:3])}"
        )
    if check.cited_fetched < spec.min_sources:
        problems.append(
            f"cites {check.cited_fetched} fetched source(s), needs {spec.min_sources}"
        )
    if check.cited_domains < spec.min_domains:
        problems.append(
            f"its fetched sources span {check.cited_domains} site(s), "
            f"needs {spec.min_domains}"
        )
    check.gate_ok = not problems
    check.why = (
        f"{check.cited_fetched} fetched sources on {check.cited_domains} sites"
        if check.gate_ok
        else "; ".join(problems)
    )
    return check


# ---------------------------------------------------------------------------
# Evidence for the judge
# ---------------------------------------------------------------------------


def _terms(text: str) -> Set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def best_excerpt(page: str, sentence: str, cap: int = EXCERPT_CAP) -> str:
    """The window of *page* that shares the most words and figures with *sentence*."""
    page = " ".join(page.split())
    if len(page) <= cap:
        return page
    wanted = _terms(sentence) | set(figures(sentence))
    if not wanted:
        return page[:cap]
    step = max(cap // 3, 1)
    best, best_score = 0, -1
    for start in range(0, max(len(page) - cap, 0) + step, step):
        window = page[start : start + cap]
        score = len(wanted & (_terms(window) | set(figures(window))))
        if score > best_score:
            best, best_score = start, score
    head = "…" if best else ""
    tail = "…" if best + cap < len(page) else ""
    return f"{head}{page[best:best + cap]}{tail}"


def citation_evidence(
    report: str, transcript: Mapping[str, Any], cap: int = EVIDENCE_CAP
) -> str:
    """Each cited claim beside the passage of its source that best matches it."""
    evidence = tool_evidence(transcript)
    claims = citations(report)
    if not claims:
        return "The report attaches no citation to any sentence."
    # Claims with figures first: they are the ones a misread source gets wrong.
    ranked = sorted(claims, key=lambda c: -len(figures(c.sentence)))[:EVIDENCE_CLAIMS]
    blocks, used = [], 0
    for n, claim in enumerate(ranked, 1):
        lines = [f"[{n}] CLAIM: {claim.sentence[:400]}"]
        for url in claim.urls[:2]:
            page = evidence.pages.get(normalize_url(url))
            if page is None:
                lines.append(f"    SOURCE {url}: NOT FETCHED by the agent")
            else:
                lines.append(
                    f"    SOURCE {url}:\n    {best_excerpt(page, claim.sentence)}"
                )
        block = "\n".join(lines)
        if used + len(block) > cap:
            blocks.append(f"({len(ranked) - n + 1} more claims not shown)")
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def summary_for_judge(check: Optional[Mapping[str, Any]]) -> str:
    """The mechanical check, in the words the judge reads."""
    if not check:
        return "(not run: judged from the report text alone)"
    lines = [
        f"- searches: {check.get('searches', 0)} "
        f"({check.get('failed_searches', 0)} refused or failed), page fetches: "
        f"{check.get('fetches', 0)} ({check.get('failed_fetches', 0)} failed)",
        f"- distinct URLs cited: {check.get('cited', 0)}; of those fetched and "
        f"read: {check.get('cited_fetched', 0)} on {check.get('cited_domains', 0)} sites",
        f"- cited but never fetched: {check.get('unread') or 'none'}",
        f"- cited but in no tool result at all (invented): "
        f"{check.get('invented') or 'none'}",
        f"- sources section: {'yes' if check.get('sources_section') else 'no'}",
    ]
    figs = check.get("unsupported_figures") or []
    if figs:
        lines.append("- figures not found in the page their sentence cites:")
        lines += [f"    {fig!r} (cites {url})" for url, fig, _s in figs[:12]]
    else:
        lines.append("- every figure in a cited sentence appears in its cited page")
    return "\n".join(lines)
