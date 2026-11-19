# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One short self-check before a turn hands over a file it built.

What goes wrong in a built deliverable is seldom a mistyped number. It is a
requirement the request stated and the file lacks, a result nobody looked at
twice, or an input that was dirty to begin with. So the check asks for those,
in a form a model answers in a few lines, and whatever code can settle on its
own (a slide count, a named tab, a word limit, an empty file) is settled here
and raised only when it fails.

The reply is a verdict, not a new answer: on ``RESULT: PASS`` the answer the
model already gave goes out unchanged, so a turn with nothing wrong pays for
one short step.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import zipfile
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Opens the check, and every log line about it.
SELF_CHECK_TAG = "[check:self]"

#: Files a program assembles: their structure is not something the model saw.
BUILT_SUFFIXES = frozenset({".xlsx", ".xlsm", ".docx", ".pptx"})
#: Files that read as prose, where a figure can be typed from memory.
PROSE_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".docx"})
_WORKBOOK_SUFFIXES = frozenset({".xlsx", ".xlsm"})
_SHOWN = 5
#: Figures a prose file must quote before they are worth a line of the check.
_MIN_QUOTED_FIGURES = 3
#: A length within this much of a stated limit is not reported.
_LENGTH_SLACK = 1.05

_COUNT_WORDS = {
    word: number
    for number, word in enumerate(
        "one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(),
        start=1,
    )
}
_COUNT_WORDS["single"] = 1
_COUNT = r"(\d{1,3}|" + "|".join(_COUNT_WORDS) + ")"
_AT_MOST = ("at most", "no more than", "not more than", "up to", "maximum", "max")
_AT_LEAST = ("at least", "minimum", "min")
_BOUND = (
    r"(at most|no more than|not more than|up to|(?:a )?max(?:imum)?(?: of)?|"
    r"at least|(?:a )?min(?:imum)?(?: of)?|exactly)"
)
_SLIDE_COUNT = re.compile(rf"(?:\b{_BOUND}\s+)?\b{_COUNT}([\s-]+)slide(s?)\b", re.I)
#: "add two slides", "the first 3 slides": a count of some slides, not the deck.
_PART_OF_DECK = re.compile(
    r"\b(?:add|adds|adding|insert|append|another|extra|more|first|last|next|"
    r"other|remaining|these|those|following|each|every|per|of the)\W+(?:\w+\W+){0,2}$",
    re.I,
)

_QUOTED = r"[\"'“‘`]([^\"'“”‘’`\n]{1,31})[\"'”’`]"
_TAB = r"(?:tabs?|sheets?|worksheets?)"
_TAB_AFTER = re.compile(rf"{_QUOTED}\s+{_TAB}\b", re.I)
_TAB_BEFORE = re.compile(
    rf"\b{_TAB}\s+(?:(?:named|called|titled|labell?ed)\s+)?{_QUOTED}", re.I
)
_TAB_LIST = re.compile(
    r"\b(?:tabs|sheets|worksheets)\s*(?:(?:named|called|titled|labell?ed)\s*)?"
    r":\s*([^.;\n]+)",
    re.I,
)
_FILE_NAME = re.compile(r"\.[A-Za-z0-9]{2,5}$")

_WORD_LIMIT = re.compile(
    r"\b(?:at most|no more than|not more than|under|fewer than|less than|up to|"
    r"(?:a )?max(?:imum)?(?: of)?|within|limit of|no longer than|"
    r"not exceed(?:ing)?|shorter than)\s+(\d[\d,]*)\s+words\b",
    re.I,
)
_WORD_CAP = re.compile(
    r"\b(\d[\d,]*)[\s-]+words?\s+(?:limit|maximum|max|cap|or (?:less|fewer))\b", re.I
)
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
_FIGURE = re.compile(r"(?<![\w.])[$£€]?\d[\d,]*(?:\.\d+)?%?(?![\w])")
_YEAR = re.compile(r"(?:19|20)\d\d")


def _name(path: str) -> str:
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1]


def _listed(paths: Sequence[str]) -> str:
    shown = ", ".join(f"`{_name(path)}`" for path in paths[:_SHOWN])
    more = len(paths) - _SHOWN
    return f"{shown} and {more} more" if more > 0 else shown


# ---------------------------------------------------------------------------
# What the request states that code can check
# ---------------------------------------------------------------------------


def stated_slide_count(request: str) -> Optional[Tuple[str, int]]:
    """``(bound, n)`` when the request gives the deck one slide count.

    *bound* is ``"exactly"``, ``"at most"`` or ``"at least"``. ``None`` when
    it gives none, or more than one: two counts are two decks, or a deck and
    a part of it, and code cannot tell which.
    """
    found = set()
    for match in _SLIDE_COUNT.finditer(request or ""):
        bound, count, gap, plural = match.groups()
        number = _COUNT_WORDS.get(count.lower()) or int(count)
        # "3 slide" is a count only when it is one, or hyphenated: "5-slide deck".
        if not plural and number != 1 and "-" not in gap:
            continue
        if _PART_OF_DECK.search(request[: match.start()]):
            continue
        bound = (bound or "exactly").lower()
        if any(word in bound for word in _AT_MOST):
            bound = "at most"
        elif any(word in bound for word in _AT_LEAST):
            bound = "at least"
        else:
            bound = "exactly"
        found.add((bound, number))
    return found.pop() if len(found) == 1 else None


def stated_tabs(request: str) -> List[str]:
    """Workbook tabs the request names: quoted beside "tab"/"sheet", or
    listed after "tabs:"."""
    names: List[str] = []
    text = request or ""
    for pattern in (_TAB_AFTER, _TAB_BEFORE):
        names.extend(match.group(1) for match in pattern.finditer(text))
    for match in _TAB_LIST.finditer(text):
        for part in re.split(r",|\band\b|&", match.group(1)):
            name = part.strip().strip("\"'“”‘’`").strip()
            if name and len(name) <= 31 and len(name.split()) <= 4:
                names.append(name)
    kept = [
        name.strip() for name in names if name.strip() and not _FILE_NAME.search(name)
    ]
    return list(dict.fromkeys(kept))


def stated_word_limit(request: str) -> Optional[int]:
    """The most words the request allows, when it says so in words."""
    limits = {
        int(match.group(1).replace(",", ""))
        for pattern in (_WORD_LIMIT, _WORD_CAP)
        for match in pattern.finditer(request or "")
    }
    return limits.pop() if len(limits) == 1 else None


# ---------------------------------------------------------------------------
# What the files hold
# ---------------------------------------------------------------------------


def _slide_count(path: str) -> Optional[int]:
    try:
        if not zipfile.is_zipfile(path):
            return None  # reported by mechanical_findings as not an Office file
        from pptx import Presentation  # pylint: disable=import-outside-toplevel

        return len(Presentation(path).slides)
    except ImportError as error:
        logger.info("%s cannot count slides: %s", SELF_CHECK_TAG, error)
    except Exception as error:  # a parser's own error class: the check is advisory
        logger.warning(
            "%s could not open %s: %s: %s",
            SELF_CHECK_TAG,
            path,
            type(error).__name__,
            error,
        )
    return None


def _sheets(path: str) -> Optional[List[Tuple[str, bool]]]:
    """``(title, has any cell value)`` per sheet of the workbook at *path*."""
    try:
        import openpyxl  # pylint: disable=import-outside-toplevel

        book = openpyxl.load_workbook(path, read_only=True, data_only=False)
    except ImportError as error:
        logger.info("%s cannot list tabs: %s", SELF_CHECK_TAG, error)
        return None
    except Exception as error:  # a parser's own error class: the check is advisory
        logger.warning(
            "%s could not open %s: %s: %s",
            SELF_CHECK_TAG,
            path,
            type(error).__name__,
            error,
        )
        return None
    try:
        return [
            (
                sheet.title,
                any(
                    cell is not None
                    for row in sheet.iter_rows(max_row=200, values_only=True)
                    for cell in row
                ),
            )
            for sheet in book.worksheets
        ]
    except Exception as error:  # a sheet that will not iterate
        logger.warning(
            "%s could not read %s: %s: %s",
            SELF_CHECK_TAG,
            path,
            type(error).__name__,
            error,
        )
        return None
    finally:
        book.close()


def _folded(name: str) -> str:
    return " ".join(name.casefold().split())


def _same_tab(asked: str, title: str) -> bool:
    asked, title = _folded(asked), _folded(title)
    return asked == title or asked in title or title in asked


#: Two folder names at least this alike are one name and a slip of it.
_NEAR_MISS_RATIO = 0.8


def _folder_key(path: str) -> str:
    key = os.path.normcase(os.path.abspath(path)).replace("/", os.sep)
    return key.rstrip(os.sep)


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + os.sep)


def _slip_of(where: str, home: str) -> bool:
    """Whether folder *where* reads as *home* with a folder name or two mistyped.

    Compared name by name, so a long shared start does not make two unrelated
    folders look alike. Only paths of the same depth compare, so a file in a
    subfolder of the mistyped folder is not raised.
    """
    ours, theirs = home.split(os.sep), where.split(os.sep)
    if len(ours) != len(theirs):
        return False
    differing = [(a, b) for a, b in zip(ours, theirs) if a != b]
    return 0 < len(differing) <= 2 and all(
        difflib.SequenceMatcher(None, a, b).ratio() >= _NEAR_MISS_RATIO
        for a, b in differing
    )


def misplaced_findings(request: str, written: Sequence[str], folder: str) -> List[str]:
    """Files written into a folder that is a slip of the working *folder*.

    A long absolute path retyped from memory can lose or change a few
    characters, and the write tool then creates the folder it names: the file
    exists, the turn reports success, and nothing is where the user looks. So
    a file counts only when all of these hold: it is outside *folder* and not
    in a folder above it, its folder's path is the same but for a name or two
    that are nearly the same text, that
    folder holds nothing but this turn's files (the write made it), and the
    request does not name that folder.
    """
    if not folder:
        return []
    home = _folder_key(folder)
    asked = os.path.normcase(request or "").replace("/", os.sep)
    by_folder: dict = {}
    for path in written:
        full = os.path.abspath(path)
        by_folder.setdefault(_folder_key(os.path.dirname(full)), []).append(full)
    findings: List[str] = []
    for where, files in by_folder.items():
        if _inside(where, home) or _inside(home, where):
            continue
        if not _slip_of(where, home):
            continue
        if where in asked:
            continue
        try:
            held = {os.path.normcase(name) for name in os.listdir(where)}
        except OSError:
            continue
        if held - {os.path.normcase(os.path.basename(path)) for path in files}:
            continue
        findings.append(
            f"{_listed(files)} {'was' if len(files) == 1 else 'were'} written to "
            f"`{os.path.dirname(files[0])}`, not the working folder "
            f"`{os.path.abspath(folder)}`: the two paths differ by a few "
            "characters, so the path was probably mistyped. Write it in the "
            "working folder."
        )
    return findings


_OFFICE_KINDS = {
    ".docx": "Word document",
    ".xlsx": "workbook",
    ".pptx": "presentation",
}


def mechanical_findings(
    request: str,
    written: Sequence[str],
    pending: Sequence[str] = (),
    prose: str = "",
    folder: str = "",
    empty_ok: Sequence[str] = (),
) -> List[str]:
    """What is wrong with *written* that needs no model to see.

    Each entry states what the file holds and what the request says, so the
    reader can tell a real miss from a request this code misread. *pending*
    are files the request names that nothing wrote; *prose* is the text of
    the one prose file among *written*, when there is exactly one; *empty_ok*
    are files among *written* that are meant to be empty. *folder* is the
    working folder, for :func:`misplaced_findings`.
    """
    findings: List[str] = misplaced_findings(request, written, folder)
    for path in written:
        try:
            suffix = os.path.splitext(path)[1].lower()
            if os.path.getsize(path) == 0:
                if path not in empty_ok:
                    findings.append(f"`{_name(path)}` is empty.")
            elif suffix in _OFFICE_KINDS and not zipfile.is_zipfile(path):
                findings.append(
                    f"`{_name(path)}` is not a {_OFFICE_KINDS[suffix]}: it holds "
                    f"plain text under a {suffix} name, so it will not open."
                )
        except OSError as error:
            findings.append(f"`{_name(path)}` cannot be opened: {error}.")
    stems = {
        os.path.splitext(_name(path))[0].casefold(): _name(path) for path in written
    }
    for path in pending:
        stem, suffix = os.path.splitext(_name(path))
        other = stems.get(stem.casefold())
        if other and suffix and os.path.splitext(other)[1].lower() != suffix.lower():
            findings.append(
                f"The request names `{_name(path)}`; the file written is `{other}`."
            )
    bound, number = stated_slide_count(request) or ("", 0)
    for path in written if bound else ():
        if os.path.splitext(path)[1].lower() != ".pptx":
            continue
        slides = _slide_count(path)
        if slides is None:
            continue
        wrong = (
            slides > number
            if bound == "at most"
            else slides < number if bound == "at least" else slides != number
        )
        if wrong:
            asked = f"{number} slide{'s' if number != 1 else ''}"
            findings.append(
                f"`{_name(path)}` has {slides} slide{'s' if slides != 1 else ''}; "
                f"the request says {asked if bound == 'exactly' else f'{bound} {asked}'}."
            )
    tabs = stated_tabs(request)
    books = [
        path
        for path in written
        if os.path.splitext(path)[1].lower() in _WORKBOOK_SUFFIXES
    ]
    sheets = [(path, listed) for path in books if (listed := _sheets(path)) is not None]
    if tabs and sheets and len(sheets) == len(books):
        for tab in tabs:
            found = [
                (path, filled)
                for path, listed in sheets
                for title, filled in listed
                if _same_tab(tab, title)
            ]
            if not found:
                titles = ", ".join(
                    f"`{title}`" for _, listed in sheets for title, _ in listed
                )
                findings.append(
                    f"The request names a tab `{tab}`; "
                    f"{_listed([path for path, _ in sheets])} has {titles}."
                )
            elif not any(filled for _, filled in found):
                findings.append(
                    f"The tab `{tab}` in `{_name(found[0][0])}` has no data."
                )
    limit = stated_word_limit(request)
    if limit and prose:
        words = len(_WORD.findall(prose))
        if words > limit * _LENGTH_SLACK:
            findings.append(
                f"The text is about {words:,} words; the request's limit is {limit:,}."
            )
    return findings


def quotes_figures(text: str) -> bool:
    """Whether *text* quotes enough figures for a typed one to be likely."""
    figures = [
        match
        for match in _FIGURE.findall(text or "")
        if sum(ch.isdigit() for ch in match) >= 2 and not _YEAR.fullmatch(match)
    ]
    return len(figures) >= _MIN_QUOTED_FIGURES


# ---------------------------------------------------------------------------
# The prompt and the reply
# ---------------------------------------------------------------------------


def self_check_prompt(
    outputs: Sequence[str],
    *,
    has_inputs: bool = False,
    findings: Sequence[str] = (),
    figure_files: Sequence[str] = (),
    short: bool = False,
) -> str:
    """The one check a turn that built *outputs* gets before it answers.

    *short* keeps the requirements line only, for a turn that is nearly out
    of time. *figure_files* are prose outputs that quote figures.
    """
    lines = [
        f"{SELF_CHECK_TAG} One short check of {_listed(outputs)} before you "
        "finish. Reply in a few lines, in this form, from what you already "
        "know of this turn's work. Do not re-run the analysis to answer.",
        "REQUIREMENTS: each thing the request explicitly asks for (file names, "
        "sections or tabs, formats, chart or other element types, limits on "
        "length or count), each marked met or NOT MET against what is in the "
        "file now.",
    ]
    if has_inputs and not short:
        lines.append(
            "PLAUSIBILITY: (a) What share of the items did you flag, keep or "
            "drop, and is that share plausible? (b) Do the inputs hold "
            "duplicate or conflicting records that affect your totals or "
            "statements, and did you say so? (c) Did any parse or merge "
            "silently produce blanks, zeros or dropped rows?"
        )
    if figure_files and has_inputs and not short:
        lines.append(
            f"FIGURES: {_listed(figure_files)} quotes figures. Is each one "
            "copied from a tool result or a source file of this turn, not "
            "from memory?"
        )
    lines.append("RESULT: PASS, or MISS and what is wrong.")
    if findings:
        lines.append("Checked in code and wrong now: " + " ".join(findings))
    lines.append(
        "On PASS stop there: the answer you just gave goes to the user as it "
        "is, and a file that passes is not rewritten. Act only on a concrete "
        "miss: fix it with the smallest change and then give your complete "
        "answer again, or, if it cannot be fixed now, reply RESULT: MISS with "
        "what is wrong and it is added to your answer. This check is "
        "internal: do not mention it to the user."
    )
    return "\n".join(lines)


_RESULT = re.compile(r"^\W*RESULT\W+(PASS|MISS)\b[\s:.,\-–—]*(.*)$", re.I | re.M)
_CHECK_LINE = re.compile(
    r"^\W*(?:REQUIREMENTS|PLAUSIBILITY|FIGURES|RESULT)\b\W*:.*(?:\n(?!\s*\n).*)*", re.M
)
_NOT_MET = re.compile(r"^.*\bNOT MET\b.*$", re.M)
_MAX_MISS_CHARS = 400


@dataclass(frozen=True)
class Verdict:
    """How a reply to the check reads."""

    #: ``"pass"``, ``"miss"``, or ``None`` when the reply is a new answer.
    result: Optional[str]
    #: What is wrong, in the model's words, for a miss.
    miss: str = ""
    #: The reply without the check's own lines.
    rest: str = ""


def read_verdict(reply: str) -> Verdict:
    """Read a reply to :func:`self_check_prompt`.

    A PASS that still marks a requirement NOT MET is a miss: the line that
    names the gap outranks the summary under it.
    """
    reply = reply or ""
    results = _RESULT.findall(reply)
    rest = _CHECK_LINE.sub("", reply).strip()
    unmet = [line.strip(" -*\t") for line in _NOT_MET.findall(reply)]
    if not results:
        return Verdict(None, rest=rest)
    result, detail = results[-1][0].lower(), results[-1][1].strip()
    if result == "pass" and not unmet:
        return Verdict("pass", rest=rest)
    miss = detail if result == "miss" and detail else " ".join(unmet)
    return Verdict("miss", miss=miss[:_MAX_MISS_CHARS].strip(), rest=rest)


def miss_note(miss: str) -> str:
    """The line a miss the model could not fix adds to its answer."""
    miss = miss.strip() or "something the request asks for is missing"
    return f"Not as requested: {miss}".rstrip(".") + "."
