# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lines an edit removed that a "change nothing else" request never mentioned.

A small model asked to change one line and append another can replace a line
instead, then report that it appended. The edit's own arguments show what
went, so the answer can say so whatever the model claims.
"""

import difflib
import re
from typing import Iterable, List, Sequence, Set

#: Requests that limit an edit to what they name.
_KEEP_REST_RE = re.compile(
    r"\b(?:change|touch|modify|edit|alter)\s+nothing\s+else\b"
    r"|\b(?:do\s*n[o']?t|don't)\s+(?:change|touch|modify|edit)\s+"
    r"(?:anything|the\s+rest)\b"
    r"|\bleave\s+(?:the\s+rest|everything\s+else)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z0-9]{3,}")
#: How alike a removed line and an added one must be to count as one line edited.
_SAME_LINE_RATIO = 0.75
#: Words too common to show that a request named a line.
_COMMON = frozenset(
    "the and for with that this from into then than are was were but not all "
    "any you your our its has have had add end top line lines file text also "
    "change nothing else keep leave rest".split()
)


def _distinctive(text: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _COMMON]


def limits_edit(request: str) -> bool:
    """True when *request* asks for its named changes and nothing more."""
    return bool(_KEEP_REST_RE.search(request or ""))


def removed_lines(old_content: str, new_content: str) -> List[str]:
    """Non-blank lines of *old_content* that *new_content* no longer has."""
    kept = {line.strip() for line in new_content.splitlines()}
    return [
        line.strip()
        for line in old_content.splitlines()
        if line.strip() and line.strip() not in kept
    ]


def unasked(lines: Iterable[str], request: str) -> List[str]:
    """The *lines* none of whose words the request mentions."""
    asked = set(_distinctive(request or ""))
    return [
        line
        for line in lines
        if _distinctive(line) and not any(w in asked for w in _distinctive(line))
    ]


def edited_in_place(old_content: str, new_content: str, request: str) -> Set[str]:
    """Removed lines the edit rewrote rather than dropped.

    A line counts as rewritten when an added line closely matches it ("Teh" to
    "The"), or when the line that replaced it keeps one of its words ("Due:"
    with a new date) or is text the request never spelled out (a translation).
    A line swapped wholesale for text the request asked to add is the line
    lost, not rewritten.
    """
    old_lines = [line.strip() for line in old_content.splitlines() if line.strip()]
    new_lines = [line.strip() for line in new_content.splitlines() if line.strip()]
    before = set(old_lines)
    added = [line.lower() for line in new_lines if line not in before]
    asked = set(_distinctive(request or ""))
    rewritten = set()
    for line in old_lines:
        if difflib.get_close_matches(line.lower(), added, n=1, cutoff=_SAME_LINE_RATIO):
            rewritten.add(line)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        for line, replacement in zip(old_lines[i1:i2], new_lines[j1:j2]):
            words = _distinctive(replacement)
            if set(words) & set(_distinctive(line)):
                rewritten.add(line)
            elif words and not any(w in asked for w in words):
                rewritten.add(line)
    return rewritten


def unasked_removals(old_content: str, new_content: str, request: str) -> List[str]:
    """Lines the edit dropped that a limited *request* never asked about."""
    rewritten = edited_in_place(old_content, new_content, request)
    return [
        line
        for line in unasked(removed_lines(old_content, new_content), request)
        if line not in rewritten
    ]


def removal_note(removals: Sequence[tuple], answer: str) -> str:
    """The heads-up for *removals* (path, line) the answer does not mention."""
    lowered = (answer or "").lower()
    missing = [
        (path, line)
        for path, line in removals
        if not all(word.lower() in lowered for word in _WORD_RE.findall(line))
    ]
    if not missing:
        return ""
    shown = ", ".join(f"`{line}` from {path}" for path, line in missing[:5])
    return (
        f"Heads up: the edit also removed {shown}, which the request did not "
        "ask to change."
    )
