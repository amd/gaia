# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Does the answer rest on what this turn's tools actually did?

Three checks, each comparing the answer's prose with the turn's tool record:

* **look** — the request names a file or symbol and no tool touched it, or the
  answer says something does not exist with no lookup on record.
* **content** — values the answer presents as data (table cells, rows of a
  data block, decimals, dates) that appear in no tool output, argument, or
  earlier message.
* **action** — the answer says it searched, read, ran, tested, verified or
  documented something, and no tool of that kind ran.

Each returns :class:`Finding` objects; the agent loop turns them into one
correction, then — if the next answer still has them — a line marking them
unverified. A finding is a prompt to look, never a rewrite of the answer.

Records are the loop's ``_turn_tool_executions`` entries
(:func:`~gaia.agents.base.verification.verification_record` plus ``args`` and
``observed``). Pure and dependency-free, like :mod:`gaia.agents.base.claims`.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set

from gaia.agents.base.checks import check_kind
from gaia.agents.base.claims import absence_claim, action_claims, admits_unverified
from gaia.agents.base.completion import mentioned_paths
from gaia.agents.base.verification import is_check_execution, observed_text

LOOK = "look"
CONTENT = "content"
ACTION = "action"

#: How much of one tool result is kept for the checks.
OBSERVED_MAX_CHARS = 400_000


@dataclass(frozen=True)
class Finding:
    """One gap between the answer and the record."""

    gate: str
    #: Sentence for the correction the model sees.
    correction: str
    #: Short phrase for the "Unverified:" line if the gap survives.
    note: str


# ---------------------------------------------------------------------------
# What kind of tool a record is, by name
# ---------------------------------------------------------------------------

_LISTING_WORDS = ("list", "browse", "tree", "glob")
_SEARCH_WORDS = _LISTING_WORDS + ("search", "grep", "find", "locate")
_READ_WORDS = _SEARCH_WORDS + (
    "read",
    "view",
    "open",
    "cat",
    "get",
    "extract",
    "analyze",
    "query",
    "fetch",
    "summar",
    "describe",
    "show",
    "inspect",
    "index",
)
_EXEC_WORDS = ("run", "exec", "shell", "python", "pytest", "test", "build", "lint")
_DOC_MARKERS = ("readme", ".md", ".rst", "docs/", "docs\\", "changelog")


def _name(record: Dict[str, Any]) -> str:
    return (record.get("tool") or "").strip().lower()


def _ran(record: Dict[str, Any]) -> bool:
    return bool(record.get("ran", True))


def _has(name: str, words: Sequence[str]) -> bool:
    return any(word in name for word in words)


def _args_text(record: Dict[str, Any]) -> str:
    return _slashed(observed_text(record.get("args") or {}))


def _slashed(text: str) -> str:
    return text.replace("\\", "/").lower()


def _looked(record: Dict[str, Any]) -> bool:
    return _ran(record) and _has(_name(record), _READ_WORDS + _EXEC_WORDS)


def _searched(record: Dict[str, Any]) -> bool:
    return _ran(record) and _has(_name(record), _SEARCH_WORDS + _EXEC_WORDS)


def _executed(record: Dict[str, Any]) -> bool:
    return _ran(record) and _has(_name(record), _EXEC_WORDS)


def _tested(record: Dict[str, Any]) -> bool:
    if not is_check_execution(record):
        return False
    label = record.get("check_label")
    return not label or (record.get("check_kind") or check_kind(label)) == "test"


def _touched_docs(record: Dict[str, Any]) -> bool:
    return _ran(record) and _has(_args_text(record), _DOC_MARKERS)


# ---------------------------------------------------------------------------
# look: the request names something and nothing looked at it
# ---------------------------------------------------------------------------

#: Extensions that make a bare token a file name rather than a word.
_FILE_SUFFIX = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
#: ``parse_updated()`` or `parse_updated`: project code, not a library call.
_SYMBOL = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`|\b([A-Za-z_][A-Za-z0-9_]*)\(\)"
)


def named_targets(query: str) -> List[str]:
    """Files and snake_case symbols *query* names, in order.

    A file needs an extension ("dates.py", "toybox/dates.py"); a directory or
    the working folder the request opens with is not something to read. A
    capitalised ``Name.js`` is a framework, not a file. A symbol needs an
    underscore and no dot, so ``parse_updated()`` counts and ``print()`` or
    ``os.path.join()`` do not.
    """
    targets: List[str] = []
    for path in mentioned_paths(query or ""):
        base = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
        framework = base[:1].isupper() and base.endswith(".js")
        if not _FILE_SUFFIX.search(base) or framework:
            continue
        targets.append(path)
    for match in _SYMBOL.finditer(query or ""):
        symbol = match.group(1) or match.group(2)
        if "_" in symbol.strip("_") and symbol not in targets:
            targets.append(symbol)
    return targets


_FINDER_WORDS = ("search", "grep", "find", "locate", "glob")


def _read_it(target: str, records: Iterable[Dict[str, Any]]) -> bool:
    """True when a successful call read *target*.

    A file counts when a read or command call names it — by file name, since
    tools get relative and absolute spellings of the same file. A symbol also
    counts when a search or read result shows it. A failed read, a listing,
    and a file name that only appears in a ``dir`` output did not read it.
    """
    key = _slashed(re.split(r"[\\/]", target.rstrip("\\/"))[-1])
    is_file = bool(_FILE_SUFFIX.search(target))
    for record in records:
        name = _name(record)
        if not _ran(record) or record.get("failed") or _has(name, _LISTING_WORDS):
            continue
        if is_file:
            if not _has(name, _FINDER_WORDS) and key in _args_text(record):
                return True
        elif key in _args_text(record) or key in _slashed(record.get("observed") or ""):
            return True
    return False


def _names(items: Sequence[str]) -> str:
    quoted = [f"`{item}`" for item in items]
    return (
        quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " or " + quoted[-1]
    )


def look_findings(
    answer: str,
    query: str,
    records: Sequence[Dict[str, Any]],
    locate: Optional[Callable[[str], Optional[str]]] = None,
    history: str = "",
) -> List[Finding]:
    """The request's files and symbols nothing read, and unbacked "not found"s.

    A named file only counts when *locate* finds it on disk or the answer
    claims it does not exist — a request that merely mentions ``setup.py`` in
    passing is not asking for it to be read. The correction names where the
    file is, because a model that resolved the path against the wrong folder
    will do it again. A symbol counts whenever nothing showed it. A
    target an earlier turn (*history*) already discussed is not asked for again.
    A "not found" only counts in a turn about something the request named, so
    explaining what a 404 means is not a claim about the disk.
    """
    targets = named_targets(query)
    if not targets:
        return []
    findings: List[Finding] = []
    absent = absence_claim(answer)
    earlier = _slashed(history)
    missed = []
    found: List[str] = []
    for target in targets:
        if _read_it(target, records) or _slashed(target) in earlier:
            continue
        where = locate(target) if locate and _FILE_SUFFIX.search(target) else None
        if where:
            found.append(f"`{target}` is at `{where}`")
        if not _FILE_SUFFIX.search(target) or absent is not None or where:
            missed.append(target)
    if missed:
        names = _names(missed)
        located = f" ({'; '.join(found)})" if found else ""
        findings.append(
            Finding(
                LOOK,
                f"You haven't read {names} this turn{located} — read "
                f"{'it' if len(missed) == 1 else 'them'} now, or say plainly that "
                "you didn't.",
                f"no tool read {names} this turn",
            )
        )
    if absent and not any(_searched(r) or _looked(r) for r in records):
        findings.append(
            Finding(
                LOOK,
                f'Your answer says "{_clip(absent)}", but no search, listing or '
                "read ran this turn — look first, or drop the claim.",
                _ABSENT_NOTE,
            )
        )
    return findings


_ABSENT_NOTE = "the claim that something does not exist was made without a search"


# ---------------------------------------------------------------------------
# content: values presented as data that no tool produced
# ---------------------------------------------------------------------------

_GROUPED = re.compile(r"(?<![\w.,])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w,]|\.\d)")
_PLAIN = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w]|\.\d)")
_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_FENCE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[ \t]*([\w+-]*)[^\n]*\n(.*?)^[ \t]*\1[ \t]*$", re.M | re.S
)
#: Fenced blocks presented as data, not code.
_DATA_FENCES = frozenset(
    {"", "csv", "tsv", "text", "txt", "plain", "output", "console", "json"}
)
_CODE_LINE = re.compile(r"[()=;{}]|\b(?:def|import|return|class|print)\b")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}")
#: A cell or field that is one value and nothing else: "181.0", "12%", "3.5s".
_PURE_VALUE = re.compile(
    r"^\s*\**\s*(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)\s*(%|s|ms|sec|secs)?\s*\**\s*$",
    re.I,
)
_PURE_DATE = re.compile(r"^\s*\**\s*(\d{4}-\d{2}-\d{2})\s*\**\s*$")
#: Column headers whose numbers the answer assigns itself.
_ORDINAL_HEADER = re.compile(
    r"^\s*\**\s*(?:#|no\.?|n|rank|priority|step|order|id|line|lines|severity|position|pos)\s*\**\s*$",
    re.I,
)
#: A version or model name is not a measurement: "Python 3.12", "gpt-4.1".
_VERSION_LEAD = re.compile(
    r"(?:\b[A-Z][\w-]*[ -]?|\bv[ -]?|\b[\w-]*\d[\w-]*[ -]?|[A-Za-z]-)$"
    r"|(?i:\b(?:version|python|node|py)[ -]?)$"
)
_ESTIMATE_LEAD = re.compile(
    r"(?:~|\b(?:about|approximately|approx\.?|around|roughly|nearly|almost))\s*$",
    re.I,
)


@dataclass(frozen=True)
class _Value:
    text: str
    number: Optional[float]
    decimals: int


def _value(text: str) -> _Value:
    if _DATE.fullmatch(text):
        return _Value(text, None, 0)
    plain = text.replace(",", "")
    decimals = len(plain.split(".", 1)[1]) if "." in plain else 0
    return _Value(text, float(plain), decimals)


def _cell_value(cell: str) -> Optional[_Value]:
    date = _PURE_DATE.match(cell)
    if date:
        return _value(date.group(1))
    match = _PURE_VALUE.match(cell)
    if not match:
        return None
    value = _value(match.group(1))
    if match.group(2) == "%" or value.decimals or abs(value.number) >= 100:
        return value
    return None  # counts, week numbers, ordinals: too common to judge


def _table_values(lines: List[str]) -> List[_Value]:
    values: List[_Value] = []
    header: Optional[List[str]] = None
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = [c for c in line.strip().strip("|").split("|")]
        if _TABLE_RULE.match(line):
            continue
        if header is None:
            nxt = lines[index + 1] if index + 1 < len(lines) else ""
            if _TABLE_RULE.match(nxt):
                header = cells
                continue
            header = []
        for col, cell in enumerate(cells):
            if col < len(header) and _ORDINAL_HEADER.match(header[col]):
                continue
            value = _cell_value(cell)
            if value is not None:
                values.append(value)
    return values


def _prose_values(text: str) -> List[_Value]:
    """Decimals, dates, percentages and 1,234-style numbers in running text."""
    values: List[_Value] = []
    for match in _DATE.finditer(text):
        values.append(_value(match.group(0)))
    text = _DATE.sub(" ", text)
    for match in list(_GROUPED.finditer(text)) + list(_PLAIN.finditer(text)):
        raw = match.group(0)
        after = text[match.end() : match.end() + 1]
        if after == "+" or not ("." in raw or "," in raw or after == "%"):
            continue  # "3.11+" is a version floor
        lead = text[max(0, match.start() - 24) : match.start()]
        if _VERSION_LEAD.search(lead) or _ESTIMATE_LEAD.search(lead):
            continue
        values.append(_value(raw))
    return values


def presented_values(answer: str) -> List[_Value]:
    """Values *answer* presents as data, in order, without duplicates.

    Table cells that are one value, fields of an untagged/CSV/JSON code block,
    and — only when the answer has one of those — decimals, dates and
    percentages in its prose. Code blocks in a language, inline code, small
    whole numbers, estimates and version numbers are left alone.
    """
    values: List[_Value] = []

    def data_block(match: "re.Match[str]") -> str:
        if match.group(2).lower() in _DATA_FENCES:
            for line in match.group(3).splitlines():
                if _CODE_LINE.search(line) and match.group(2).lower() != "json":
                    continue
                for field in re.split(r"[,;\t|]|\s{2,}|:\s", line):
                    value = _cell_value(field.strip().strip('"'))
                    if value is not None:
                        values.append(value)
        return "\n"

    text = _FENCE.sub(data_block, answer or "")
    text = re.sub(r"`[^`\n]*`", " ", text)
    lines = text.splitlines()
    values.extend(_table_values(lines))
    if not values:
        return []  # no table or data block: prose numbers are not presented data
    prose = "\n".join(line for line in lines if not line.strip().startswith("|"))
    values.extend(_prose_values(prose))
    seen: Set[str] = set()
    unique = []
    for value in values:
        if value.text not in seen:
            seen.add(value.text)
            unique.append(value)
    return unique


class _Corpus:
    """Every number and date the turn's evidence holds."""

    def __init__(self, text: str):
        self.dates = set(_DATE.findall(text))
        text = _DATE.sub(" ", text)
        # "2,184.0" is a CSV row to one reader and 2184 to another: keep both.
        self.numbers = {
            float(match.group(0).replace(",", ""))
            for pattern in (_GROUPED, _PLAIN)
            for match in pattern.finditer(text)
        }
        self._rounded: Dict[int, Set[int]] = {}

    def holds(self, value: _Value) -> bool:
        if value.number is None:
            return value.text in self.dates
        places = value.decimals
        if places not in self._rounded:
            scale = 10**places
            # 0.45 in the output may be shown as 45%; halves round either way.
            self._rounded[places] = {
                key
                for n in self.numbers
                for shown in (n * scale, n * 100 * scale)
                if math.isfinite(shown)
                for key in (math.floor(shown + 0.5), round(shown))
            }
        return round(value.number * 10**places) in self._rounded[places]


def _rounds_to(number: float, value: _Value) -> bool:
    half = 0.5 * 10 ** (-value.decimals)
    return math.isfinite(number) and abs(number - value.number) <= half + 1e-9


def _derived(value: _Value, operands: Sequence[float]) -> bool:
    """*value* is one arithmetic step from two grounded values the answer shows."""
    for i, a in enumerate(operands):
        for b in operands[i + 1 :]:
            results = [a + b, abs(a - b), a * b, (a + b) / 2]
            for x, y in ((a, b), (b, a)):
                if y:
                    results += [x / y, 100 * x / y]
            if any(_rounds_to(r, value) for r in results):
                return True
    return False


def content_findings(
    answer: str, records: Sequence[Dict[str, Any]], context: str = ""
) -> List[Finding]:
    """Values the answer presents that no tool output, argument or message holds.

    *context* is everything else the model was given this turn — the request
    and earlier messages. A value one arithmetic step from two grounded values
    the answer also shows is grounded (a total, a difference, an average of
    two). Silent when no tool ran: an answer from knowledge has nothing to be
    grounded in, and the look check owns that case.
    """
    if not any(_ran(r) for r in records):
        return []
    values = presented_values(answer)
    if not values:
        return []
    corpus = _Corpus(
        "\n".join(
            [context]
            + [observed_text(r.get("args") or {}) for r in records]
            + [r.get("observed") or "" for r in records]
        )
    )
    grounded = [v for v in values if corpus.holds(v)]
    operands = [v.number for v in grounded if v.number is not None][:40]
    missing = [
        v.text
        for v in values
        if v not in grounded and not (v.number is not None and _derived(v, operands))
    ]
    if not missing:
        return []
    shown = ", ".join(missing[:6]) + (", …" if len(missing) > 6 else "")
    return [
        Finding(
            CONTENT,
            f"These values in your answer appear in no tool output this turn: {shown}. "
            "Read the file back (or rerun the command) and report what it shows, or "
            "mark the values as unverified.",
            f"{shown} appear in no tool output this turn",
        )
    ]


# ---------------------------------------------------------------------------
# action: "I searched / ran / tested …" with no such tool on record
# ---------------------------------------------------------------------------

_ACTION_EVIDENCE: Dict[str, "tuple[Callable[[Dict[str, Any]], bool], str]"] = {
    "searched": (_searched, "no search or listing ran this turn"),
    "read": (_looked, "no file was read this turn"),
    "ran": (_executed, "no command or script ran this turn"),
    "tested": (_tested, "no test run is recorded for this turn"),
    "verified": (_looked, "no tool ran this turn to check it"),
    "documented": (
        _touched_docs,
        "no documentation file was read or written this turn",
    ),
}


def action_findings(answer: str, records: Sequence[Dict[str, Any]]) -> List[Finding]:
    """Work the answer reports that no tool of that kind did."""
    findings: List[Finding] = []
    for kind, phrase in action_claims(answer):
        backed, why = _ACTION_EVIDENCE[kind]
        if any(backed(r) for r in records):
            continue
        findings.append(
            Finding(
                ACTION,
                f'Your answer says "{phrase}", but {why}. Do it now, or drop the claim.',
                f'"{phrase}" — {why}',
            )
        )
    return findings


# ---------------------------------------------------------------------------
# All three, and what the loop says
# ---------------------------------------------------------------------------


def ungrounded(
    answer: str,
    query: str,
    records: Sequence[Dict[str, Any]],
    *,
    history: str = "",
    locate: Optional[Callable[[str], Optional[str]]] = None,
) -> List[Finding]:
    """Every gap between *answer* and this turn's tool record, look first.

    *history* is the text of earlier turns. An answer that already says it did
    not look, or that its figures are unverified, has nothing left to correct
    about looking or figures — only a false report of work done still counts.
    """
    findings = look_findings(answer, query, records, locate, history)
    if admits_unverified(answer):
        findings = [f for f in findings if f.note == _ABSENT_NOTE]
    else:
        findings += content_findings(answer, records, history + "\n" + query)
    return findings + action_findings(answer, records)


def grounding_correction(findings: Sequence[Finding]) -> str:
    """The one message that asks the model to close *findings*."""
    return (
        "[check:grounding] "
        + " ".join(f.correction for f in findings)
        + " Then give your complete answer again: it replaces the one above, "
        "which the user will not see."
    )


UNVERIFIED_PREFIX = "Unverified: "


def unverified_note(findings: Sequence[Finding]) -> str:
    """The line appended to an answer whose gaps survived their correction."""
    notes = list(dict.fromkeys(f.note for f in findings))
    return UNVERIFIED_PREFIX + "; ".join(notes) + "."


def path_locator(
    roots: Sequence[Optional[str]],
) -> Callable[[str], Optional[str]]:
    """A ``locate`` for :func:`look_findings`: the file's absolute path, or ``None``.

    An absolute path is taken as given; a relative one is tried under each of
    *roots* in order.
    """

    def locate(path: str) -> Optional[str]:
        expanded = os.path.expanduser(path)
        candidates = (
            [expanded]
            if os.path.isabs(expanded)
            else [os.path.join(root, expanded) for root in roots if root]
        )
        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.normpath(candidate)
        return None

    return locate


def _clip(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
