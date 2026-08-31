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
from typing import List, Optional

#: A Markdown ATX heading. Setext (``===`` underlines) is deliberately not
#: supported — no shipped skill uses it, and accepting both would make the slug
#: for a given heading depend on which form the author picked.
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")

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

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            flush()
            cur_level = len(match.group(1))
            cur_heading = match.group(2)
            cur_slug = slugify_heading(cur_heading) or "section"
            buf = [line]
        else:
            buf.append(line)
    flush()

    return sections


def find_section(sections: List[Section], slug: str) -> Optional[Section]:
    """Return the section with *slug*, or ``None``."""
    for section in sections:
        if section.slug == slug:
            return section
    return None


def render_sections(sections: List[Section]) -> str:
    """Rebuild a body from *sections*.

    Exact inverse of :func:`parse_sections` for an unmodified list — the
    round-trip test pins that. After a section is dropped, the join still
    produces a well-formed body because each span carries its own trailing
    blank line.
    """
    return "\n".join(section.text for section in sections)
