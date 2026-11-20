# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Accent- and case-insensitive matching for the sidebar's chat search."""

import re
import unicodedata
from typing import Dict, List, Optional, Tuple

SNIPPET_BEFORE = 20  # the sidebar shows ~30 chars; keep the hit in view
SNIPPET_AFTER = 80
# Longer messages are cleaned only around the hit: the markdown regexes
# backtrack on huge lines, base64 or minified code, and stall the search.
WHOLE_TEXT_MAX = 4000
WINDOW = 300  # context kept on each side of the hit
WINDOW_SNAP = 300  # how much further to look for a line break or space to cut at
_CHUNK = 4096


def _fold_char(ch: str) -> str:
    decomposed = unicodedata.normalize("NFKD", ch)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def fold(text: str) -> str:
    """Fold *text* so "Résumé", "RESUME" and "resume" compare equal."""
    if text.isascii():
        return text.lower()
    decomposed = unicodedata.normalize("NFKD", text)
    marks = {ord(c): None for c in set(decomposed) if unicodedata.combining(c)}
    return decomposed.translate(marks).casefold()


def _fold_with_map(text: str) -> Tuple[str, List[int]]:
    """Fold *text*, also returning the original index of each folded char."""
    out: List[str] = []
    origin: List[int] = []
    for i, ch in enumerate(text):
        f = _fold_char(ch)
        out.append(f)
        origin.extend([i] * len(f))
    return "".join(out), origin


# (?=(\s*))\1 is an atomic \s*: giving whitespace back can't help the next
# token match, and without it a run of blank lines backtracks cubically.
_TABLE_RULE = re.compile(
    r"^(?=(\s*))\1\|?(?=(\s*))\2:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$", re.M
)
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


def _locate(text: str, needle: str) -> Optional[Tuple[int, int]]:
    """Original-text span of the first match of the folded *needle*."""
    if text.isascii():
        at = text.lower().find(needle)
        return None if at < 0 else (at, at + len(needle))
    # fold() works char by char, so chunk lengths add up to folded positions.
    parts = [fold(text[i : i + _CHUNK]) for i in range(0, len(text), _CHUNK)]
    at = "".join(parts).find(needle)
    if at < 0:
        return None
    first, base = 0, 0
    while base + len(parts[first]) <= at:
        base += len(parts[first])
        first += 1
    last, reach = first, base + len(parts[first])
    while reach < at + len(needle):
        last += 1
        reach += len(parts[last])
    offset = first * _CHUNK
    _, origin = _fold_with_map(text[offset : (last + 1) * _CHUNK])
    return (
        offset + origin[at - base],
        offset + origin[at - base + len(needle) - 1] + 1,
    )


def _window(text: str, start: int, end: int) -> Tuple[int, int]:
    """Bounds of the text to clean around a hit, cut at a line break or space."""
    lo = max(0, start - WINDOW)
    if lo > 0:
        for sep in ("\n", " "):
            at = text.rfind(sep, max(0, lo - WINDOW_SNAP), lo)
            if at >= 0:
                lo = at + 1
                break
    hi = min(len(text), end + WINDOW)
    if hi < len(text):
        for sep in ("\n", " "):
            at = text.find(sep, hi, hi + WINDOW_SNAP)
            if at >= 0:
                hi = at
                break
    return lo, hi


def snippet(text: str, needle: str) -> Optional[Dict[str, str]]:
    """Split the text around the first match of the folded *needle*.

    Returns ``{"before", "match", "after"}`` in the original spelling, so the
    UI can highlight ``match`` without re-implementing the folding, or None
    when *text* does not contain *needle*.
    """
    if not needle:
        return None
    if len(text) <= WHOLE_TEXT_MAX:
        return _snippet(text, needle, False, False)
    hit = _locate(text, needle)
    if hit is None:
        return None
    lo, hi = _window(text, *hit)
    return _snippet(text[lo:hi], needle, lo > 0, hi < len(text))


def _snippet(
    text: str, needle: str, clipped_lo: bool, clipped_hi: bool
) -> Optional[Dict[str, str]]:
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
        "before": ("…" if lo > 0 or clipped_lo else "") + before,
        "match": text[start:end],
        "after": after + ("…" if hi < len(text) or clipped_hi else ""),
    }
