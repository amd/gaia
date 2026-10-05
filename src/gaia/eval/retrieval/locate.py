# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Map indexed chunks and labelled evidence onto the same coordinates.

The chunker re-joins words with single spaces, so a chunk is not a verbatim
slice of the extracted text — but it is an exact substring once whitespace is
collapsed. Every position here is an offset into that whitespace-normalized
text, and pages come from the ``[Page N]`` markers the PDF extractor writes.

Chunks start in document order, except right after a chunk that was split to
fit the embedder: the next chunk's overlap can begin before the split's tail.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

_PAGE_MARKER = re.compile(r"\[Page (\d+)\]")

#: A chunk "contains" a quote when it covers at least this share of it, so a
#: quote split across two overlapping chunks still counts for either.
MIN_QUOTE_COVERAGE = 0.5


class LabelError(ValueError):
    """A labelled evidence quote is not where its label says it is."""


def norm(text: str) -> str:
    return " ".join(text.split())


@dataclass
class DocText:
    """One indexed document's normalized text and page boundaries."""

    path: str
    text: str
    page_offsets: List[int] = field(default_factory=list)
    page_numbers: List[int] = field(default_factory=list)

    @classmethod
    def from_full_text(cls, path: str, full_text: str) -> "DocText":
        text = norm(full_text)
        doc = cls(path=path, text=text)
        for m in _PAGE_MARKER.finditer(text):
            doc.page_offsets.append(m.start())
            doc.page_numbers.append(int(m.group(1)))
        return doc

    @property
    def paged(self) -> bool:
        return bool(self.page_offsets)

    def pages_between(self, start: int, end: int) -> Set[int]:
        if not self.page_offsets:
            return set()
        first = max(bisect.bisect_right(self.page_offsets, start) - 1, 0)
        last = max(bisect.bisect_right(self.page_offsets, max(end - 1, start)) - 1, 0)
        return set(self.page_numbers[first : last + 1])

    def page_segment(self, page: int) -> Tuple[int, int]:
        if page not in self.page_numbers:
            raise LabelError(f"{self.path} has no page {page}")
        i = self.page_numbers.index(page)
        end = (
            self.page_offsets[i + 1]
            if i + 1 < len(self.page_offsets)
            else len(self.text)
        )
        return self.page_offsets[i], end

    def page_text(self, page: int) -> str:
        start, end = self.page_segment(page)
        return self.text[start:end]


@dataclass
class ChunkSpan:
    index: int
    path: str
    start: int
    end: int
    pages: Set[int]


@dataclass
class Evidence:
    """Where an answer lives: a page, a quote, or both.

    ``span`` is resolved against the indexed text by :func:`resolve_evidence`;
    page-only evidence (FinanceBench) is matched by page.
    """

    path: str
    page: Optional[int] = None
    quote: Optional[str] = None
    span: Optional[Tuple[int, int]] = None


def locate_chunks(
    chunks: Sequence[str], file_chunks: Dict[str, List[int]], docs: Dict[str, DocText]
) -> Tuple[Dict[int, ChunkSpan], List[int]]:
    """Locate every chunk in its document; return spans and unlocated indices."""
    spans: Dict[int, ChunkSpan] = {}
    unlocated: List[int] = []
    for path, indices in file_chunks.items():
        doc = docs[path]
        cursor = before = 0
        for idx in indices:
            needle = norm(chunks[idx])
            # Chunks come in document order; a match before the previous chunk
            # could be repeated text elsewhere, so it counts as unlocated.
            pos = doc.text.find(needle, cursor) if needle else -1
            if pos < 0 and needle:
                # One exception: the previous chunk is the short tail of a chunk
                # split to fit the embedder, and this one's overlap, cut from the
                # unsplit chunk, starts before that tail and runs through it.
                pos = doc.text.find(needle, before, cursor + len(needle))
                if pos + len(needle) <= cursor:
                    pos = -1
            if pos < 0:
                unlocated.append(idx)
                continue
            end = pos + len(needle)
            spans[idx] = ChunkSpan(idx, path, pos, end, doc.pages_between(pos, end))
            before, cursor = cursor, pos
    return spans, unlocated


def resolve_evidence(ev: Evidence, doc: DocText, hint: int = 0) -> Evidence:
    """Pin a quote to a span (on its page, if given); raise if it is not there."""
    if ev.quote is None:
        if ev.page is None:
            raise LabelError(f"Evidence for {doc.path} has neither a page nor a quote")
        doc.page_segment(ev.page)
        return ev
    needle = norm(ev.quote)
    lo, hi = doc.page_segment(ev.page) if ev.page is not None else (0, len(doc.text))
    pos = doc.text.find(needle, max(lo, hint), hi)
    if pos < 0:
        pos = doc.text.find(needle, lo, hi)
    if pos < 0:
        where = f"page {ev.page} of " if ev.page is not None else ""
        raise LabelError(
            f"Evidence quote not found on {where}{doc.path} as indexed: {needle[:120]!r}. "
            "Fix the label, or the extractor changed what it emits for this document."
        )
    return Evidence(ev.path, ev.page, ev.quote, (pos, pos + len(needle)))


def chunk_hits(span: Optional[ChunkSpan], evidence: Sequence[Evidence]) -> bool:
    if span is None:
        return False
    for ev in evidence:
        if ev.path != span.path:
            continue
        if ev.span is not None:
            overlap = min(span.end, ev.span[1]) - max(span.start, ev.span[0])
            if overlap >= MIN_QUOTE_COVERAGE * (ev.span[1] - ev.span[0]):
                return True
        elif ev.page is not None and ev.page in span.pages:
            return True
    return False


def token_overlap(a: str, b: str) -> float:
    """Share of ``a``'s word tokens that also appear in ``b`` (label sanity check)."""
    ta = re.findall(r"\w+", a.lower())
    if not ta:
        return 0.0
    tb = set(re.findall(r"\w+", b.lower()))
    return sum(1 for t in ta if t in tb) / len(ta)
