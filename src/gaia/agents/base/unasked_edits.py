# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Lines an edit removed that a "change nothing else" request never mentioned.

A small model asked to change one line and append another can replace a line
instead, then report that it appended. The edit's own arguments show what
went, so the answer can say so whatever the model claims.
"""

import difflib
import os
import re
from typing import Dict, Iterable, List, Sequence, Set

#: Requests that limit an edit to what they name.
_KEEP_REST_RE = re.compile(
    r"\b(?:change|touch|modify|edit|alter)\s+nothing\s+else\b"
    r"|\bdo\s*n[o'’]?t\s+(?:change|touch|modify|edit)\s+"
    r"(?:anything|the\s+rest|(?:any\s+)?other\s+lines?)\b"
    r"|\bleave\s+(?:the\s+rest|everything\s+else)\b"
    r"|\bkeep\s+(?:the\s+rest|everything\s+else)\s+"
    r"(?:the\s+same|as\s+is|unchanged|intact)\b"
    r"|\bnothing\s+else\s+should\s+(?:change|be\s+changed)\b",
    re.IGNORECASE,
)
#: Requests that ask for something to be taken out.
_DELETE_RE = re.compile(
    r"\b(?:delet(?:e|es|ed|ing)|remov(?:e|es|ed|ing|al)|drop(?:s|ped|ping)?|"
    r"strip(?:s|ped|ping)?|cut(?:s|ting)?|eras(?:e|es|ed|ing)|get\s+rid\s+of)\b",
    re.IGNORECASE,
)
#: Words in an answer that own up to a removal.
_ADMIT_RE = re.compile(
    r"\b(?:remov(?:e|es|ed|ing)|replac(?:e|es|ed|ing)|delet(?:e|es|ed|ing)|"
    r"drop(?:s|ped|ping)?|swap(?:s|ped|ping)?|overwr(?:ote|itten|ites?|iting)|"
    r"(?:took|taken|take|takes|taking)\s+out)\b",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
#: Past this many changed lines on either side the edit is a rewrite, not a tweak.
_MAX_CHANGED_LINES = 200
_NOTE_LINE_CHARS = 80
_NOTE_MAX_LINES = 5
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
    "change nothing else keep leave rest same other should everything anything "
    "touch don".split()
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


def _lines(content: str) -> List[str]:
    return [line.strip() for line in content.splitlines() if line.strip()]


def _named(line: str, asked: Set[str]) -> bool:
    return any(w in asked for w in _distinctive(line))


def _survives(line: str, added_tokens: Set[str]) -> bool:
    """True when every word of *line* is still in the added text."""
    tokens = _tokens(line)
    return bool(tokens) and tokens <= added_tokens


def _deleted_outright(old_lines: List[str], new_lines: List[str]) -> Set[str]:
    """Old lines in hunks that took text out without bringing any in."""
    gone: Set[str] = set()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        brought = set().union(*map(_tokens, new_lines[j1:j2]))
        if brought <= set().union(*map(_tokens, old_lines[i1:i2])):
            gone.update(old_lines[i1:i2])
    return gone


def unasked_removals(old_content: str, new_content: str, request: str) -> List[str]:
    """Lines the edit dropped that a limited *request* never asked about.

    Lines whose words all reappear in the added text were reshaped, not lost.
    A request that names no removed line describes its change by kind: a
    deletion it asked for ("delete line 3") or a lone line edited in place
    ("fix the off-by-one") is not reported. Edits too large to compare line
    by line report nothing.
    """
    request = request or ""
    removed = removed_lines(old_content, new_content)
    if not removed:
        return []
    old_lines, new_lines = _lines(old_content), _lines(new_content)
    before = set(old_lines)
    added = [line for line in new_lines if line not in before]
    if max(len(removed), len(added)) > _MAX_CHANGED_LINES:
        return []
    asked = set(_distinctive(request))
    named = any(_named(line, asked) for line in removed)
    rewritten = edited_in_place(old_content, new_content, request)
    added_tokens = set().union(*map(_tokens, added))
    lost = [
        line
        for line in unasked(removed, request)
        if line not in rewritten and not _survives(line, added_tokens)
    ]
    if named or not lost:
        return lost
    if _DELETE_RE.search(request):
        gone = _deleted_outright(old_lines, new_lines)
        lost = [line for line in lost if line not in gone]
    if len(removed) == 1 and difflib.get_close_matches(
        removed[0].lower(), [a.lower() for a in added], n=1, cutoff=_SAME_LINE_RATIO
    ):
        return []
    return lost


def _admitted(line: str, sentences: List[str]) -> bool:
    words = _WORD_RE.findall(line) or [line]
    patterns = [
        re.compile(rf"(?<!\w){re.escape(w)}(?!\w)", re.IGNORECASE) for w in words
    ]
    return any(
        _ADMIT_RE.search(s) and all(p.search(s) for p in patterns) for s in sentences
    )


def _quoted(line: str) -> str:
    if len(line) > _NOTE_LINE_CHARS:
        line = line[: _NOTE_LINE_CHARS - 1].rstrip() + "…"
    fence = "`" * (max(map(len, re.findall(r"`+", line)), default=0) + 1)
    pad = " " if line.startswith("`") or line.endswith("`") else ""
    return f"{fence}{pad}{line}{pad}{fence}"


def _label(path: str, others: Iterable[str]) -> str:
    """The shortest tail of *path* that no other path in the note ends with."""
    if not path:
        return "the file"
    parts = [x for x in re.split(r"[\\/]", path) if x] or [path]
    rest = [
        [x for x in re.split(r"[\\/]", o) if x]
        for o in others
        if os.path.normcase(o) != os.path.normcase(path)
    ]
    depth = 1
    while depth < len(parts) and any(o[-depth:] == parts[-depth:] for o in rest):
        depth += 1
    return os.sep.join(parts[-depth:])


def removal_note(removals: Sequence[tuple], answer: str) -> str:
    """The heads-up for *removals* (path, line) the answer does not own up to."""
    sentences = [s for s in _SENTENCE_RE.split(answer or "") if s.strip()]
    missing = [(p, line) for p, line in removals if not _admitted(line, sentences)]
    if not missing:
        return ""
    shown = missing[:_NOTE_MAX_LINES]
    paths = [path for path, _ in shown]
    groups: Dict[str, List[str]] = {}
    for path, line in shown:
        groups.setdefault(path, []).append(_quoted(line))
    parts = [
        f"{', '.join(lines)} from {_label(p, paths)}" for p, lines in groups.items()
    ]
    more = len(missing) - len(shown)
    if more:
        parts.append(f"{more} more line{'s' if more > 1 else ''}")
    listed = parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"
    return (
        f"Heads up: the edit also removed {listed}, which the request did not ask "
        "to change."
    )
