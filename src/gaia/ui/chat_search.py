# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Accent- and case-insensitive matching for the sidebar's chat search."""

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

SNIPPET_BEFORE = 20  # the sidebar shows ~30 chars; keep the hit in view
SNIPPET_AFTER = 80


def _fold_char(ch: str) -> str:
    decomposed = unicodedata.normalize("NFKD", ch)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def fold(text: str) -> str:
    """Fold *text* so "Résumé", "RESUME" and "resume" compare equal."""
    if text.isascii():
        return text.lower()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _fold_with_map(text: str) -> Tuple[str, List[int]]:
    """Fold *text*, also returning the original index of each folded char."""
    out: List[str] = []
    origin: List[int] = []
    for i, ch in enumerate(text):
        f = _fold_char(ch)
        out.append(f)
        origin.extend([i] * len(f))
    return "".join(out), origin


_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", re.M)
_LINE_MARKS = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+|\d+[.)][ \t]+)", re.M
)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)\s]*\)")
_EMPHASIS = re.compile(r"(\*{1,3}|(?<!\w)_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)")


def plain_text(text: str) -> str:
    """Strip markdown syntax so a snippet shows words, not ``|``, ``**`` or ``1.``."""
    text = _TABLE_RULE.sub("", text)
    text = _LINE_MARKS.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = text.replace("```", " ").replace("`", "")
    return re.sub(r"\s*\|\s*", " ", text)


def snippet(text: str, needle: str) -> Optional[Dict[str, str]]:
    """Split the text around the first match of the folded *needle*.

    Returns ``{"before", "match", "after"}`` in the original spelling, so the
    UI can highlight ``match`` without re-implementing the folding, or None
    when *text* does not contain *needle*.
    """
    if not needle:
        return None
    plain = plain_text(text)
    # A query for the syntax itself (e.g. "**") only matches the raw text.
    text = plain if needle in fold(plain) else text
    folded, origin = _fold_with_map(text)
    at = folded.find(needle)
    if at < 0:
        return None
    start = origin[at]
    end = origin[at + len(needle) - 1] + 1

    lo = max(0, start - SNIPPET_BEFORE)
    if lo > 0:
        space = text.find(" ", lo, start)
        lo = space + 1 if space >= 0 else lo
    hi = min(len(text), end + SNIPPET_AFTER)
    if hi < len(text):
        space = text.rfind(" ", end, hi)
        hi = space if space >= 0 else hi

    def flat(s: str) -> str:
        return " ".join(s.split())

    before = flat(text[lo:start])
    after = flat(text[end:hi])
    # split() drops the edge spaces that separate the parts from the match.
    if before and text[start - 1].isspace():
        before += " "
    if after and text[end].isspace():
        after = " " + after
    return {
        "before": ("…" if lo > 0 else "") + before,
        "match": text[start:end],
        "after": after + ("…" if hi < len(text) else ""),
    }
