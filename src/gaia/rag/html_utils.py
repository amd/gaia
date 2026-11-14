# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Readable text from an HTML page for RAG indexing.

A saved web page is mostly markup, scripts and embedded JSON: Wikipedia's
Coffee article is 1 MB of HTML around about 100 KB of prose. Indexing the raw
bytes buries the facts (an infobox value sits inside a ``data-mw`` attribute)
and fills the index with noise. This keeps the title, headings, paragraphs,
list items and table rows, and drops what a reader never sees.
"""

import re
import warnings
from pathlib import Path
from typing import List

#: Markup bytes one page may hand the parser; 15x the largest Wikipedia article.
MAX_HTML_BYTES = 32 * 1024 * 1024

#: ``<`` one page may hold. A parsed tag costs ~1 KB of RAM, so this keeps a
#: page near 0.5 GB; the 2.7 MB "2022 FIFA World Cup" article has 40,000.
MAX_HTML_TAGS = 500_000

#: Characters one page may produce, the same ceiling an EPUB book has.
MAX_TEXT_CHARS = 10_000_000

HTML_EXTENSIONS = frozenset({".html", ".htm"})

_SKIPPED = frozenset(
    {
        "head",
        "script",
        "style",
        "noscript",
        "template",
        "nav",
        "footer",
        "svg",
        "iframe",
        "object",
        "button",
        "select",
        "input",
        "textarea",
    }
)
_HIDDEN_ROLES = frozenset({"navigation", "search"})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "header",
        "aside",
        "blockquote",
        "pre",
        "figure",
        "figcaption",
        "caption",
        "table",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "br",
        "hr",
        "address",
        "details",
        "summary",
    }
)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_CELLS = ("td", "th")
_DISPLAY_NONE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_BLOCK_END = object()


def _too_large(file_name: str, reason: str) -> ValueError:
    return ValueError(
        f"HTML file too large to index: {file_name}\n"
        f"Reason: {reason}.\n"
        "Save just the part you need (or print the page to PDF) and index that."
    )


def _hidden(tag) -> bool:
    """Not page content: navigation, ``hidden``, ``aria-hidden``, display:none.

    A link with ``hreflang`` points at this page in another language; a
    Wikipedia article lists 180 of them before its first paragraph.
    """
    attrs = tag.attrs or {}
    if "hidden" in attrs or attrs.get("aria-hidden") == "true":
        return True
    if tag.name == "a" and "hreflang" in attrs:
        return True
    if attrs.get("role") in _HIDDEN_ROLES:
        return True
    style = attrs.get("style")
    return bool(style) and bool(_DISPLAY_NONE.search(style))


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _block_text(nodes) -> str:
    """Text of *nodes* with one line per block; tables become ``a | b`` rows."""
    # pylint: disable-next=import-outside-toplevel
    from bs4 import CData, NavigableString, Tag

    parts: List[str] = []
    # Iterative, so a deeply nested page cannot exhaust the recursion limit.
    stack = list(reversed(nodes))
    while stack:
        node = stack.pop()
        if node is _BLOCK_END:
            parts.append("\n")
        elif isinstance(node, str):
            # Exact types: no comments, doctype or processing instructions.
            if type(node) in (NavigableString, CData):
                parts.append(node)
        elif isinstance(node, Tag):
            if node.name in _SKIPPED or _hidden(node):
                continue
            if node.name in _HEADINGS:
                title = _one_line(_block_text(node.contents))
                level = int(node.name[1])
                parts.append(f"\n\n{'#' * level} {title}\n\n" if title else "\n")
                continue
            if node.name == "tr":
                cells = [
                    _one_line(_block_text(cell.contents))
                    for cell in node.find_all(_CELLS, recursive=False)
                    if not _hidden(cell)
                ]
                if any(cells):
                    parts.append("\n" + " | ".join(cells) + "\n")
                continue
            if node.name == "li":
                parts.append("\n- ")
                stack.append(_BLOCK_END)
            elif node.name in _BLOCK_TAGS:
                parts.append("\n")
                stack.append(_BLOCK_END)
            stack.extend(reversed(node.contents))
    lines = [_one_line(line) for line in "".join(parts).splitlines()]
    lines = [line for line in lines if line not in ("", "-")] or [""]
    return "\n".join(lines)


def extract_html_text(html_path: str, markup: bytes) -> str:
    """Readable text of an HTML page: title, headings, paragraphs, table rows.

    Args:
        html_path: The file's path, named in errors.
        markup: The file's bytes; the parser detects their encoding.

    Raises:
        ValueError: the page is too large to index.
    """
    # pylint: disable-next=import-outside-toplevel
    from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

    file_name = Path(html_path).name
    if len(markup) > MAX_HTML_BYTES:
        raise _too_large(
            file_name,
            f"it is over {MAX_HTML_BYTES // (1024 * 1024)} MB, "
            "the most GAIA reads from one web page",
        )
    # Counted before parsing: the parsed tree costs RAM per tag.
    if markup.count(b"<") > MAX_HTML_TAGS:
        raise _too_large(
            file_name,
            f"it has more than {MAX_HTML_TAGS:,} tags, "
            "the most GAIA reads from one web page",
        )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(markup, "html.parser")

    title = _one_line(soup.title.get_text()) if soup.title else ""
    body = _block_text([soup.body or soup])
    # Headings are already Markdown; keep blank lines around them for the chunker.
    body = re.sub(r"\n(#+ [^\n]*)\n", r"\n\n\1\n\n", f"\n{body}\n").strip()
    text = f"# {title}\n\n{body}".strip() if title else body
    if len(text) > MAX_TEXT_CHARS:
        raise _too_large(
            file_name,
            f"it holds more than {MAX_TEXT_CHARS:,} characters, "
            "the most GAIA indexes from one web page",
        )
    return text
