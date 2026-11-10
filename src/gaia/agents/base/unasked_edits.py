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
_TOKEN_RE = re.compile(r"[a-z]{2,}|\d+")
_CLAUSE_RE = re.compile(r"[.;,]\s|\band\b|\bthen\b")
_ADD_RE = re.compile(r"\b(?:add|append|insert|include)\b")
#: Requests whose new text is the model's to write.
_REWRITE_RE = re.compile(
    r"\b(?:translat|rewrit|reword|rephras|paraphras|convert|reformat|format|"
    r"simplif|shorten|summari[sz]|grammar|wording)\w*"
    r"|\breplac\w*(?![^.;]*\b(?:with|by)\b)",
    re.IGNORECASE,
)
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


def _tokens(text: str) -> Set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _variant(a: str, b: str) -> bool:
    """True when *b* is *a* with one typo (a slip, a swap, a missing letter)."""
    if a == b:
        return True
    if a.isdigit() or b.isdigit() or min(len(a), len(b)) < 3:
        return False
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        i, j = diff[0], diff[-1]
        return len(diff) == 2 and j == i + 1 and a[i] == b[j] and a[j] == b[i]
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))


def _changed(old: str, new: str) -> tuple:
    """Words *new* brought in and words it dropped, typo fixes aside."""
    before, after = _tokens(old), _tokens(new)
    novel = {t for t in after - before if not any(_variant(t, o) for o in before)}
    dropped = {t for t in before - after if not any(_variant(t, n) for n in after)}
    return novel, dropped


def _spelled_out(request: str) -> Set[str]:
    """Words the request gives as a new value, not as text to add."""
    words: Set[str] = set()
    for clause in _CLAUSE_RE.split(request.lower()):
        if not _ADD_RE.search(clause):
            words |= _tokens(clause)
    return words


def _in_place(line: str, replacement: str, request: str) -> bool:
    novel, dropped = _changed(line, replacement)
    if not novel and not dropped:
        return True
    if novel and novel <= _spelled_out(request):
        return True
    if not _REWRITE_RE.search(request):
        return False
    numbers = _tokens(request)
    return all(t in numbers for t in dropped if t.isdigit())


def edited_in_place(old_content: str, new_content: str, request: str) -> Set[str]:
    """Removed lines the edit rewrote rather than dropped.

    A line counts as rewritten when the line that replaced it only fixes typos
    or case ("Teh" to "The"), when every new word in it is a value the request
    gave ("Due:" with the requested date), or when the request asks for a
    rewrite (a translation) that keeps the line's numbers. A line swapped for
    text the request asked to add, or for text nobody asked for, is the line
    lost.
    """
    request = request or ""
    old_lines = [line.strip() for line in old_content.splitlines() if line.strip()]
    new_lines = [line.strip() for line in new_content.splitlines() if line.strip()]
    before = set(old_lines)
    added = [line.lower() for line in new_lines if line not in before]
    pairs = []
    for line in old_lines:
        for match in difflib.get_close_matches(
            line.lower(), added, n=3, cutoff=_SAME_LINE_RATIO
        ):
            pairs.append((line, match))
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            pairs.extend(zip(old_lines[i1:i2], new_lines[j1:j2]))
    return {line for line, repl in pairs if _in_place(line, repl, request)}


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
