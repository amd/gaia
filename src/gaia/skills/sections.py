# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Split a ``SKILL.md`` body into named, addressable sections.

An authored skill body is one opaque string everywhere else in ``gaia.skills``
(:class:`~gaia.skills.format.Skill` keeps ``body: str``). A learned overlay needs
somewhere to *attach*, and attaching to a line number breaks on the first
reflow. So this module gives the body the only stable handle it has: its Markdown
headings.

Two properties the rest of the overlay depends on, and which the tests pin:

* **Deterministic.** The same body always yields the same slugs and the same
  digests, so a delta written today still resolves tomorrow without re-anchoring.
* **Lossless.** ``render_sections(parse_sections(body)) == body`` exactly.
  Resolution rebuilds the body from these pieces, so anything this parser drops
  would silently vanish from the agent's instructions.

Digests reuse :func:`gaia.skills.audit.findings.manifest_digest`'s convention —
sha256 over CRLF-normalized text — rather than adding a third hashing scheme to
the repo. Normalization matters here: a Windows checkout must not produce a
different anchor from the LF bytes the delta was written against.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

#: A Markdown ATX heading. Setext (``===`` underlines) is deliberately not
#: supported — no shipped skill uses it, and accepting both would make the slug
#: for a given heading depend on which form the author picked.
#:
#: Unanchored leading whitespace is also deliberate: an indented line is a code
#: block in CommonMark, so ``    # Heading`` is content, not a split point.
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")

#: A fenced-code delimiter: three or more backticks or tildes, indented up to
#: three spaces (four would make it an indented code block), with an optional
#: info string. Skills describe their output by *showing* it, and a Markdown
#: template shown that way is full of ``#`` lines that are examples rather than
#: structure — so a parser blind to fences hands back sections that do not
#: exist, and a delta anchored to one edits the wrong text.
_FENCE_RE = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<info>.*)$")

#: Slug for the text before the first heading. Not a legal slug otherwise (the
#: slugger strips leading dashes), so it can never collide with a real one.
PREAMBLE_SLUG = "_preamble"

#: Cap so a pathological heading cannot produce an unbounded anchor key.
MAX_SLUG_LENGTH = 64


def slugify_heading(text: str) -> str:
    """Return the anchor slug for a heading's text.

    Lowercase, non-alphanumerics collapsed to single dashes, trimmed. ``##
    What the grant allows`` becomes ``what-the-grant-allows``.

    Inline Markdown is stripped first so that re-styling a heading — wrapping a
    word in backticks, bolding it — does not orphan every delta anchored to it.
    """
    # Strip inline code/emphasis markers, then link syntax [text](url) -> text.
    cleaned = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    cleaned = cleaned.replace("`", "").replace("*", "").replace("_", "")
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")
    return slug[:MAX_SLUG_LENGTH].rstrip("-")


def section_digest(text: str) -> str:
    """A ``sha256:<hex>`` over *text*, CRLF-normalized.

    Same convention as :func:`gaia.skills.audit.findings.manifest_digest`, so a
    CRLF checkout and an LF checkout agree.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class Section:
    """One heading-delimited span of a skill body, including its heading line."""

    slug: str
    level: int
    heading: str
    text: str

    @property
    def digest(self) -> str:
        """Content digest of this section, heading line included."""
        return section_digest(self.text)

    @property
    def is_preamble(self) -> bool:
        return self.slug == PREAMBLE_SLUG


def _next_fence_state(line: str, fence: Optional[str]) -> Optional[str]:
    """Return the fence marker still open after *line*.

    *fence* is the currently-open delimiter, or ``None`` outside a code block.
    CommonMark's rules, kept because authors write real Markdown: a closing
    fence uses the same character, is at least as long as the one that opened
    it, and carries no info string — so ```` ```markdown ```` inside a ``~~~``
    block is content, not a close. An unterminated fence runs to the end of the
    document, which makes a malformed skill parse as one big section rather
    than silently regaining the phantom splits this guards against.
    """
    match = _FENCE_RE.match(line)
    if match is None:
        return fence
    marker = match.group("marker")
    info = match.group("info")
    if fence is None:
        # Backtick fences may not carry a backtick in their info string; that
        # spelling is inline code, not a block.
        if marker[0] == "`" and "`" in info:
            return None
        return marker
    if marker[0] == fence[0] and len(marker) >= len(fence) and not info.strip():
        return None
    return fence


def parse_sections(body: str) -> List[Section]:
    """Split *body* into :class:`Section` spans, in document order.

    The span for a heading runs from its own heading line up to (not including)
    the next heading at any level — headings do not nest here, because a delta
    anchors to the heading it names, not to that heading's subtree.

    A body with no headings yields a single ``_preamble`` section holding all of
    it. That is the legal bare-skill case, and it is why whole-body replacement
    needs no special path: it is a ``_preamble`` replacement.

    Duplicate headings are disambiguated by appending ``-2``, ``-3``, … in
    document order, so every slug in the result is unique and stable.

    A ``#`` line inside a fenced code block is content, not a heading. Skills
    show their output format by printing it, so that line is usually an example
    the author never meant to be addressable — and splitting on it puts the
    author's real text in a section named after their sample data.
    """
    lines = body.split("\n")
    sections: List[Section] = []
    seen: dict[str, int] = {}

    cur_slug = PREAMBLE_SLUG
    cur_level = 0
    cur_heading = ""
    buf: List[str] = []

    def flush() -> None:
        # The preamble is dropped only when it is genuinely empty; a body that
        # opens with prose before its first heading must keep that prose.
        text = "\n".join(buf)
        if cur_slug == PREAMBLE_SLUG and not text.strip():
            return
        slug = cur_slug
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 1
        sections.append(
            Section(slug=slug, level=cur_level, heading=cur_heading, text=text)
        )

    fence: Optional[str] = None
    for line in lines:
        match = _HEADING_RE.match(line) if fence is None else None
        if match:
            flush()
            cur_level = len(match.group(1))
            cur_heading = match.group(2)
            cur_slug = slugify_heading(cur_heading) or "section"
            buf = [line]
        else:
            buf.append(line)
            fence = _next_fence_state(line, fence)
    flush()

    return sections


def find_section(sections: List[Section], slug: str) -> Optional[Section]:
    """Return the section with *slug*, or ``None``.

    A spelling that is really the heading resolves to its slug: ``Brief shape``
    and ``Brief_Shape`` both find ``brief-shape``. Exact matches are tried
    first, and the normalization is the slugger's own, so it can only map a
    name onto the slug that name would have produced — never onto a different
    section. The caller is usually a model, and a model writes a heading the
    way headings are written.
    """
    wanted = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    for section in sections:
        if section.slug == slug:
            return section
    for section in sections:
        if section.slug == wanted:
            return section
    return None


#: One or more whitespace characters — the only difference between a paragraph
#: as the file wraps it and the same paragraph as a reader quotes it back.
_WHITESPACE_RUN = re.compile(r"\s+")


def find_snippet_spans(text: str, snippet: str) -> List[Tuple[int, int]]:
    """Return ``(start, end)`` spans of *snippet* in *text*.

    Exact occurrences win outright. Only when there are none does this fall
    back to treating every whitespace run as interchangeable, which is what
    lets a quote survive a different line wrap.

    That fallback is the difference between the feature working and not. A
    skill body is hard-wrapped at around 80 columns; the model reads it as
    prose and quotes it back as one line, so a byte-exact match fails on every
    paragraph that spans two lines — the correction is refused for a reason the
    user cannot see and the model cannot fix by trying harder.
    """
    if not snippet:
        return []
    # Non-overlapping, like the ``str.replace`` this stands in for: scanning
    # from start+1 would make "aa" match twice in "aaa" and the two spans would
    # corrupt each other on replacement.
    exact = []
    start = text.find(snippet)
    while start != -1:
        exact.append((start, start + len(snippet)))
        start = text.find(snippet, start + len(snippet))
    if exact:
        return exact
    parts = [re.escape(p) for p in _WHITESPACE_RUN.split(snippet.strip()) if p]
    if not parts:
        return []
    pattern = re.compile(r"\s+".join(parts))
    return [m.span() for m in pattern.finditer(text)]


def replace_snippet(text: str, snippet: str, replacement: str) -> str:
    """Return *text* with every span of *snippet* replaced by *replacement*.

    Replaces all occurrences, matching what a plain ``str.replace`` did before
    reflow tolerance existed — a quote that appears twice was always ambiguous,
    and resolving it silently to the first hit would be a new behaviour, not a
    safer one.
    """
    spans = find_snippet_spans(text, snippet)
    for start, end in reversed(spans):
        text = text[:start] + replacement + text[end:]
    return text


def render_sections(sections: List[Section]) -> str:
    """Rebuild a body from *sections*.

    Exact inverse of :func:`parse_sections` for an unmodified list — the
    round-trip test pins that. After a section is dropped, the join still
    produces a well-formed body because each span carries its own trailing
    blank line.
    """
    return "\n".join(section.text for section in sections)
