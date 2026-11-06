# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lines an edit removed that a "change nothing else" request never mentioned.

A small model asked to change one line and append another can replace a line
instead, then report that it appended. The edit's own arguments show what
went, so the answer can say so whatever the model claims.
"""

import re
from typing import Iterable, List, Sequence

#: Requests that limit an edit to what they name.
_KEEP_REST_RE = re.compile(
    r"\b(?:change|touch|modify|edit|alter)\s+nothing\s+else\b"
    r"|\bnothing\s+else\b"
    r"|\b(?:do\s*n[o']?t|don't)\s+(?:change|touch|modify|edit)\s+"
    r"(?:anything|the\s+rest)\b"
    r"|\bleave\s+(?:the\s+rest|everything\s+else)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z0-9]{3,}")


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
    asked = {word.lower() for word in _WORD_RE.findall(request or "")}
    return [
        line
        for line in lines
        if _WORD_RE.findall(line)
        and not any(word.lower() in asked for word in _WORD_RE.findall(line))
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
