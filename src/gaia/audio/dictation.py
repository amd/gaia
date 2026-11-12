# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn a document's text into what a text-to-speech voice should say.

Two forms. *As written* keeps the author's words and only removes what a page
carries that speech does not (page markers, line wraps). *Listening* is a
rewrite by the chat model, part by part, for text that makes no sense by ear:
tables, charts, footnotes, web addresses and reference codes.

Nothing here talks to a model or writes a file; the dictation tool supplies
both, so every rule in this module can be tested on plain strings.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from gaia.audio.lemonade_tts import SPEECH_CHUNK_CHARS, _split_text

AS_WRITTEN = "as_written"
LISTENING = "listening"
#: ``form`` values the dictation tool accepts; ``auto`` picks one of the other two.
FORMS = ("auto", AS_WRITTEN, LISTENING)

#: Plain-text documents read as they are; Word, PDF and Excel go through the
#: document extractor.
TEXT_SUFFIXES = frozenset({".txt", ".text", ".md", ".markdown"})
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})

#: Most text one dictation speaks: about two hours of audio at a normal pace.
MAX_DICTATION_CHARS = 120_000

#: Source text handed to the model per rewrite request. Small enough that the
#: rewrite of one part never runs into the reply limit.
REWRITE_PART_CHARS = 6000
#: Reply limit for one rewritten part; a rewrite is about as long as its source.
REWRITE_MAX_TOKENS = 4096

#: Speaking pace used to tell the user how long the audio will be.
WORDS_PER_MINUTE = 150

_PAGE_MARKER = re.compile(r"^\[Page (\d+)\]\s*$", re.MULTILINE)
_SHEET_MARKER = re.compile(r"^\[Sheet: (.+)\]\s*$")
_PAGE_NUMBER_LINE = re.compile(
    r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"[.!?:;。！？][\"'”’)\]]*$")
_NUMBER_TOKEN = re.compile(r"^[($€£+\-−]*\d[\d,.]*[%)]*$")


class DictationError(ValueError):
    """The document cannot be dictated as asked; the message says what to do."""


@dataclass(frozen=True)
class Block:
    """A heading or a run of prose, in reading order."""

    text: str
    heading: bool = False


# ── Choosing the form ────────────────────────────────────────────────────────

_URL = re.compile(
    r"https?://\S+|\bwww\.\S+|\b[\w.+-]{1,64}@[\w-]{1,63}\.[\w.-]{1,63}\b"
)
_FOOTNOTE = re.compile(
    r"(?<=[\w.,;:!?%)\"”])\[\d{1,3}\]|[¹²³⁴⁵⁶⁷⁸⁹]|^[*†‡]\s?\S"
    r"|(?<=[a-z)%][.,;])(?<!\bp\.)(?<!\bpp\.)(?<!\bNo\.)(?<!\bFig\.)\d{1,2}(?=\s+[A-Z])",
    re.M,
)
# Letters and digits in hyphenated groups: three groups ("QX-4471-B") or a run
# of three digits ("INV-00123"). "COVID-19" and "A-1" are ordinary words.
_REFERENCE_CODE = re.compile(
    r"\b(?=[A-Z0-9-]{0,40}[A-Z])(?=[A-Z0-9-]{0,40}\d)"
    r"(?:[A-Z0-9]{1,20}(?:-[A-Z0-9]{1,20}){2,6}"
    r"|[A-Z]{1,20}-[A-Z0-9]{0,17}\d{3}[A-Z0-9]{0,17})\b"
)
_FIGURE = re.compile(r"^\s*(?:Figure|Fig\.|Chart|Exhibit)\s+\d", re.M)
_TABLE_REF = re.compile(r"^\s*Table\s+\d", re.M)


def _table_rows(text: str) -> int:
    """Lines that read as a row of figures: three or more number cells."""
    rows = 0
    for line in text.splitlines():
        cells = line.replace("|", " ").split()
        if sum(1 for cell in cells if _NUMBER_TOKEN.match(cell)) >= 3:
            rows += 1
    return rows


def listening_reasons(text: str) -> List[str]:
    """What in *text* a voice cannot read as written, in plain words."""
    reasons = []
    lines = [line.strip() for line in text.splitlines()]
    if (
        _table_rows(text) >= 2
        or sum(1 for line in lines if line.startswith("|") and line.count("|") >= 3)
        >= 2
        or any(_cell_lines(lines))
        or _TABLE_REF.search(text)
    ):
        reasons.append("tables")
    if _FIGURE.search(text):
        reasons.append("charts or figures")
    if _FOOTNOTE.search(text):
        reasons.append("footnotes")
    if _URL.search(text):
        reasons.append("web or email addresses")
    if _REFERENCE_CODE.search(text):
        reasons.append("reference codes")
    return reasons


def choose_form(text: str, form: str = "auto") -> Tuple[str, List[str]]:
    """``(form, reasons)``: the form to dictate *text* in and what decided it.

    An explicit *form* is kept. ``auto`` rewrites for listening only when the
    text has something a voice cannot read as written.
    """
    if form not in FORMS:
        raise DictationError(f"form must be one of {', '.join(FORMS)}; got {form!r}.")
    reasons = listening_reasons(text)
    if form == "auto":
        form = LISTENING if reasons else AS_WRITTEN
    return form, reasons


# ── Pages ────────────────────────────────────────────────────────────────────


def split_pages(text: str) -> List[Tuple[Optional[int], str]]:
    """``[(page number or None, text)]`` from extracted text with [Page N] markers."""
    parts = _PAGE_MARKER.split(text)
    pages: List[Tuple[Optional[int], str]] = []
    if parts[0].strip():
        pages.append((None, parts[0].strip()))
    for index in range(1, len(parts), 2):
        pages.append((int(parts[index]), parts[index + 1].strip()))
    return pages


def parse_page_range(pages: str) -> Tuple[int, int]:
    """``(first, last)`` from ``"12"`` or ``"3-10"``."""
    match = re.fullmatch(r"\s*(\d+)\s*(?:[-–]\s*(\d+))?\s*", pages or "")
    if not match:
        raise DictationError(
            f"pages must be one page or a range such as '3-10'; got {pages!r}."
        )
    first, last = int(match.group(1)), int(match.group(2) or match.group(1))
    if first < 1 or last < first:
        raise DictationError(
            f"pages must count up from 1, such as '3-10'; got {pages!r}."
        )
    return first, last


def select_pages(text: str, pages: str, name: str) -> str:
    """The part of *text* on the pages named, with its [Page N] markers."""
    first, last = parse_page_range(pages)
    numbered = [(number, body) for number, body in split_pages(text) if number]
    if not numbered:
        raise DictationError(
            f"{name} has no page numbers to select from; leave pages empty to "
            "dictate all of it."
        )
    total = numbered[-1][0]
    if first > total:
        raise DictationError(f"{name} has {total} pages; page {first} does not exist.")
    kept = [(n, body) for n, body in numbered if first <= n <= last]
    return "\n\n".join(f"[Page {n}]\n{body}" for n, body in kept)


def fitting_page_range(text: str) -> Optional[Tuple[int, int]]:
    """The longest run of whole pages from the start of *text* that one dictation speaks.

    ``None`` when the text has no page marks, or its first page alone is too long.
    """
    numbered = [(number, body) for number, body in split_pages(text) if number]
    size, last = 0, None
    for number, body in numbered:
        size += len(f"[Page {number}]\n{body}\n\n")
        if size > MAX_DICTATION_CHARS:
            break
        last = number
    return (numbered[0][0], last) if last is not None else None


def too_long_error(text: str, name: str) -> DictationError:
    """The refusal for text longer than one dictation speaks, with the range that fits."""
    hours = len(text.split()) / WORDS_PER_MINUTE / 60
    fits = fitting_page_range(text)
    if fits:
        how = (
            f"Dictate it in parts: pages='{fits[0]}-{fits[1]}' fits in one, then "
            "continue from the next page. Tell the user it will be several files."
        )
    else:
        how = (
            "Ask the user which part they want, and dictate that part from its "
            "own file."
        )
    return DictationError(
        f"{name} is about {hours:.1f} hours of audio, longer than one dictation "
        f"makes. {how}"
    )


# ── As written ───────────────────────────────────────────────────────────────


_DASH_BULLET = re.compile(r"^[-–—*]\s+(?=\S)")
# Unicode categories a PDF's bullet glyph extracts as: symbols, private-use
# and control characters.
_BULLET_CATEGORIES = frozenset({"So", "Co", "Cc", "Cn"})


def _without_bullet(line: str) -> str:
    """*line* without the list marker it starts with, if it has one."""
    if len(line) > 2 and line[1].isspace():
        first = line[0]
        if first in "•▪◦·‣" or unicodedata.category(first) in _BULLET_CATEGORIES:
            return line[2:].lstrip()
    return _DASH_BULLET.sub("", line)


#: This many short lines in a row are the cells of a table or chart, not headings.
_CELL_RUN = 4


def _is_short(line: str) -> bool:
    """A line that could be a heading or a table cell: brief, no closing punctuation."""
    return (
        len(line) <= 80
        and len(line.split()) <= 10
        and not _SENTENCE_END.search(line)
        and not line.endswith(",")
    )


def _cell_lines(lines: Sequence[str]) -> List[bool]:
    """Which lines sit in a run of short lines long enough to be table cells."""
    cells = [False] * len(lines)
    start = None
    for index in range(len(lines) + 1):
        short = index < len(lines) and bool(lines[index]) and _is_short(lines[index])
        if short and start is None:
            start = index
        elif not short and start is not None:
            if index - start >= _CELL_RUN and _are_cells(lines[start:index]):
                cells[start:index] = [True] * (index - start)
            start = None
    return cells


def _are_cells(run: Sequence[str]) -> bool:
    """A run of short lines is a table when it holds figures or only labels.

    A title, subtitle and two headings in a row are short lines too; they are
    phrases, with no numbers among them.
    """
    figures = sum(1 for line in run if _NUMBER_TOKEN.match(line.replace(" ", "")))
    return figures * 4 >= len(run) or all(len(line.split()) <= 2 for line in run)


def plain_blocks(text: str) -> List[Block]:
    """Reading-order blocks from extracted PDF, Word, Excel or plain text.

    Page markers and bare page numbers are dropped, words hyphenated across a
    line break are rejoined, and wrapped lines are joined into prose. A heading
    stays its own block so the voice pauses around it; the cells of a table,
    which arrive one per line, are read as one comma-separated run.
    """
    lines: List[str] = []
    for number, body in split_pages(text):
        page = [line.strip() for line in body.strip().splitlines()]
        # A page number sits at the top or bottom edge. A bare number there
        # that is not this page's own, next to another figure, is a table cell.
        for edge, neighbour in ((-1, -2), (0, 1)):
            if not page or not _PAGE_NUMBER_LINE.match(page[edge]):
                continue
            beside = page[neighbour] if len(page) > 1 else ""
            own = page[edge].strip() == str(number)
            if own or not _NUMBER_TOKEN.match(beside.replace(" ", "")):
                page.pop(edge)
        if lines:
            lines.append("")
        for line in page:
            sheet = _SHEET_MARKER.match(line)
            if sheet:
                lines.extend(["", f"Sheet: {sheet.group(1)}.", ""])
            else:
                lines.append(_without_bullet(line))
    cells = _cell_lines(lines)

    blocks: List[Block] = []
    current = ""
    row: List[str] = []

    def flush() -> None:
        nonlocal current
        if row:
            blocks.append(Block(", ".join(row) + "."))
            row.clear()
        if current.strip():
            blocks.append(Block(current.strip()))
        current = ""

    for index, line in enumerate(lines):
        if not line:
            # A blank line ends a paragraph only where the sentence ended too:
            # a page break in mid-sentence leaves one behind.
            if not current or _SENTENCE_END.search(current):
                flush()
            continue
        if cells[index]:
            if not row:
                flush()
            row.append(line)
            continue
        if row:
            flush()
        following = next((later for later in lines[index + 1 :] if later), "")
        if (
            (not current or _SENTENCE_END.search(current))
            and _is_short(line)
            and (line[:1].isupper() or line[:1].isdigit())
            and (not following or following[:1].isupper() or following[:1].isdigit())
        ):
            flush()
            blocks.append(Block(line, heading=True))
            continue
        if current.endswith("-") and line[:1].islower() and current[-2:-1].isalpha():
            current = current[:-1] + line
        else:
            current = f"{current} {line}".strip()
    flush()
    return blocks


_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_MD_BULLET = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_MD_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|`|~~)(?=\S)(.{1,400}?)(?<=\S)\1")


def _plain(markdown_line: str) -> str:
    line = _MD_LINK.sub(r"\1", markdown_line)
    previous = None
    while previous != line:
        previous = line
        line = _MD_EMPHASIS.sub(r"\2", line)
    return line.strip()


def markdown_blocks(text: str) -> List[Block]:
    """Reading-order blocks from Markdown: a listening script or a .md document."""
    blocks: List[Block] = []
    current: List[str] = []

    def flush() -> None:
        if current:
            blocks.append(Block(" ".join(current)))
            current.clear()

    for raw in text.splitlines():
        if not raw.strip() or _MD_RULE.match(raw):
            flush()
            continue
        heading = _MD_HEADING.match(raw)
        if heading:
            flush()
            title = _plain(heading.group(1))
            if title:
                blocks.append(Block(title, heading=True))
            continue
        if _MD_BULLET.match(raw):
            flush()
            item = _plain(_MD_BULLET.sub("", raw, count=1))
            if item:
                blocks.append(Block(item))
            continue
        line = _plain(raw)
        if line:
            current.append(line)
    flush()
    return blocks


# ── Saying it clearly ────────────────────────────────────────────────────────

_MONTHS = {
    "Jan": "January",
    "Feb": "February",
    "Mar": "March",
    "Apr": "April",
    "Jun": "June",
    "Jul": "July",
    "Aug": "August",
    "Sep": "September",
    "Sept": "September",
    "Oct": "October",
    "Nov": "November",
    "Dec": "December",
}
_FRACTIONS = {
    "1/2": "one half",
    "1/3": "one third",
    "2/3": "two thirds",
    "1/4": "one quarter",
    "3/4": "three quarters",
}
_MAGNITUDE = r"(thousand|million|billion|trillion)"


def _digits(run: str) -> str:
    return " ".join(run)


def _decimal(match: "re.Match[str]") -> str:
    return f"{match.group(1)} point {_digits(match.group(2))}"


def _money(match: "re.Match[str]") -> str:
    whole, cents = match.group(1), match.group(2)
    if cents == "00":
        return f"{whole} dollars"
    return f"{whole} dollars and {cents.lstrip('0') or '0'} cents"


# Each rule fixes one thing the Kokoro voice was heard to misread; order matters.
_SPEAKABLE: Sequence[Tuple["re.Pattern[str]", object]] = (
    # First, so "p.14 The" keeps its number when footnote marks are removed.
    (re.compile(r"\bpp\.\s*(?=\d)"), "pages "),
    (re.compile(r"\bp\.\s*(?=\d)"), "page "),
    (re.compile(r"\bNo\.\s*(?=\d)"), "number "),
    (re.compile(r"\bFig\.\s*(?=\d)"), "Figure "),
    # "[1]" is read out as "one"; a footnote mark is not part of the sentence.
    (re.compile(r"(?<=[\w.,;:!?%)\"”])\[\d{1,3}\]"), ""),
    (re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+"), ""),
    # A superscript note number extracts as a digit glued to the full stop.
    (
        re.compile(
            r"(?<=[a-z)%][.,;])(?<!\bp\.)(?<!\bpp\.)(?<!\bNo\.)(?<!\bFig\.)\d{1,2}(?=\s+[A-Z])"
        ),
        "",
    ),
    # "https colon slash slash" tells a listener nothing.
    (re.compile(r"\bhttps?://(?=\S)"), ""),
    (re.compile(r"\be\.g\.,?", re.I), "for example,"),
    (re.compile(r"\bi\.e\.,?", re.I), "that is,"),
    (re.compile(r"\bvs\.?(?=\s)"), "versus"),
    (re.compile(r"(?<![\w&])#(?=\d)"), "number "),
    (re.compile(r"(?<=\s)&(?=\s)"), "and"),
    (
        re.compile(r"\b(" + "|".join(_MONTHS) + r")\.?(?=\s+\d)"),
        lambda m: _MONTHS[m.group(1)],
    ),
    # A decimal point is read as a full stop: "3.5" comes out "three. five".
    (
        re.compile(r"\$(\d[\d,]{0,30})\.(\d{1,30})\s*" + _MAGNITUDE + r"\b", re.I),
        lambda m: f"{m.group(1)} point {_digits(m.group(2))} {m.group(3)} dollars",
    ),
    (
        re.compile(r"\$(\d[\d,]{0,30})\s*" + _MAGNITUDE + r"\b", re.I),
        lambda m: f"{m.group(1)} {m.group(2)} dollars",
    ),
    (re.compile(r"\$(\d[\d,]{0,30})\.(\d{2})(?![\d.]{0,20}\d)"), _money),
    (
        re.compile(r"\$(\d[\d,]{0,30})\.(\d{1,30})(?![\d.]{0,20}\d)"),
        lambda m: f"{m.group(1)} point {_digits(m.group(2))} dollars",
    ),
    (re.compile(r"\bv(?=\d+\.\d)"), "version "),
    (re.compile(r"(?<=\d)x\b"), " times"),
    (re.compile(r"(?<=\d)\s+[x×]\s+(?=\d)"), " times "),
    (re.compile(r"~(?=\d)"), "about "),
    # Dotted numbers with several parts (versions, sections, IP addresses), not dates.
    (
        re.compile(
            r"(?<![\d.])(?!\d{1,2}\.\d{1,2}\.\d{4}\b)\d{1,10}(?:\.\d{1,10}){2,8}(?![\d.]{0,20}\d)"
        ),
        lambda m: m.group(0).replace(
            ".", " dot " if m.group(0).count(".") == 3 else " point "
        ),
    ),
    (re.compile(r"(?<![\d.])(\d[\d,]{0,30})\.(\d{1,30})(?![\d.]{0,20}\d)"), _decimal),
    (
        re.compile(r"(?<![\d/])(1/2|1/3|2/3|1/4|3/4)(?![\d/])"),
        lambda m: _FRACTIONS[m.group(1)],
    ),
    (re.compile(r"(?<=\d)\s?[–—]\s?(?=\d)"), " to "),
)


def speakable(text: str) -> str:
    """*text* with the forms the voice misreads rewritten so it says them right.

    The words stay the author's: this changes how a decimal, a footnote mark or
    an abbreviation is voiced, not what the sentence says.
    """
    for pattern, replacement in _SPEAKABLE:
        text = pattern.sub(replacement, text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ── Speech pieces ────────────────────────────────────────────────────────────


def speech_pieces(
    blocks: Sequence[Block], limit: int = SPEECH_CHUNK_CHARS
) -> List[str]:
    """The text of each speech request, in order.

    A heading always opens a new piece, so the gap between two requests falls
    on the section break. Prose is packed up to *limit* and split at sentences.
    """
    pieces: List[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            pieces.append(current)
        current = ""

    for block in blocks:
        text = speakable(block.text)
        if not text:
            continue
        if block.heading:
            flush()
            current = text if _SENTENCE_END.search(text) else f"{text}."
            continue
        for part in _split_text(text, limit):
            if current and len(current) + len(part) + 1 > limit:
                flush()
            current = f"{current} {part}".strip()
    flush()
    return pieces


def spoken_minutes(pieces: Sequence[str]) -> float:
    """Roughly how long *pieces* take to say, in minutes."""
    return sum(len(piece.split()) for piece in pieces) / WORDS_PER_MINUTE


# ── Listening rewrite ────────────────────────────────────────────────────────

LISTENING_SYSTEM_PROMPT = """\
You turn part of a written document into a script that a text-to-speech voice \
will read aloud. The listener cannot see the page.

Keep every fact, figure, name and date. Add nothing that is not in the text. \
Copy every number digit for digit, as digits ("13,100", never "thirteen \
thousand one hundred"): a number that differs from the page is the one \
mistake that cannot be heard. Do not summarize, and leave prose that already \
reads well aloud as it is. Rewrite only what does not work by ear:
- Tables: say what the table shows, then go through it row by row in full \
sentences, each number attached to what it measures.
- Charts and figures: say what the figure shows from what the text gives \
(its title, the trend, the highest and lowest values). If only a caption is \
there, name the figure in one sentence and move on.
- Footnotes: fold the note into the sentence it explains. Never read out a \
marker such as [1] or an asterisk.
- Web and email addresses: name the site in words ("the report page on \
example dot com") instead of spelling the address out.
- Reference codes and part numbers: keep them, with the letters and digits \
spaced so each is said ("Q X, 4 4 7 1, B").
- Lists: turn bullets into sentences.
- Page headers, footers and page numbers: leave out.

Write numbers so a voice says them clearly:
- Quantities in digits with the unit in words: "15 minutes", "18 percent", \
"8,400 dollars", "72 degrees".
- Decimals as "3 point 5". Money with cents as "237 dollars and 50 cents". \
Simple fractions in words: "one half".
- Ranges as "10 to 12". Times as "6:30 in the morning". Dates in full: \
"August 4, 2026".
- Spell out abbreviations: "for example", "page 14", "number 4", "versus", \
full month names, "third quarter" for Q3.

Output only the script, as plain paragraphs. Put each section heading on its \
own line starting with "## ". Use no other formatting, no bullet points, and \
no remarks about what you changed.\
"""


def rewrite_parts(text: str, limit: int = REWRITE_PART_CHARS) -> List[str]:
    """Split document text into parts the model rewrites one at a time.

    Whole pages stay together while they fit; a longer page is cut at line
    breaks, so a part ends between lines rather than inside one.
    """
    units: List[str] = []
    for _number, body in split_pages(text) or [(None, text.strip())]:
        while len(body) > limit:
            cut = body.rfind("\n", limit // 2, limit)
            if cut <= 0:
                cut = body.rfind(" ", limit // 2, limit)
            if cut <= 0:
                cut = limit
            units.append(body[:cut].strip())
            body = body[cut:].strip()
        if body:
            units.append(body)
    parts: List[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 2 > limit:
            parts.append(current)
            current = unit
        else:
            current = f"{current}\n\n{unit}".strip()
    if current:
        parts.append(current)
    return parts


def rewrite_prompt(part: str, index: int, total: int, title: str, tail: str) -> str:
    """The request for part *index* of *total*; *tail* is how the script ends so far."""
    if total == 1:
        where = f'This is the whole document, "{title}".'
    else:
        where = f'This is part {index} of {total} of the document "{title}".'
    if index == 1:
        opening = "Open with one sentence saying what this document is, then begin."
    else:
        opening = (
            "The script so far ends with the lines below. Continue straight on "
            "from them: no greeting, no recap, and do not repeat them. If the "
            "text carries on a section the script has already headed, do not "
            "write its heading again.\n\n"
            f"--- script so far ends ---\n{tail}\n--- end ---"
        )
    return f"{where} {opening}\n\n--- document text ---\n{part}\n--- end of text ---"


_UNITS = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fifth": 5,
    "eighth": 8,
    "ninth": 9,
    "twelfth": 12,
}
_SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_WORD_VALUES: Dict[str, int] = {
    **{word: value for value, word in enumerate(_UNITS)},
    **{word: (index + 2) * 10 for index, word in enumerate(_TENS)},
    **_ORDINALS,
}
# Punctuation is a token too, so "three hundred thirty, six" is two numbers.
_FIGURE_WORD = re.compile(r"\d[\d,]{0,30}|[A-Za-z]+|[^\sA-Za-z\d-]")
#: Figures below this are list markers, footnote marks and section numbers as
#: often as they are facts, so they are not checked.
_CHECKED_FROM = 10


def figures(text: str) -> List[int]:
    """Every whole number *text* states, in digits or in English words.

    "13,100" and "thirteen thousand one hundred" are both 13100, so a script
    can be checked against its source however each writes its numbers.
    """
    found: List[int] = []
    total = current = 0
    counting = False

    def close() -> None:
        nonlocal total, current, counting
        if counting:
            found.append(total + current)
        total = current = 0
        counting = False

    for token in _FIGURE_WORD.findall(_PAGE_MARKER.sub("", text)):
        word = token.lower()
        if word[0].isdigit():
            close()
            found.append(int(word.replace(",", "")))
        elif word in _WORD_VALUES:
            value = _WORD_VALUES[word]
            # A year said in pairs: "twenty twenty-six" is 2026.
            if counting and not total and current in (19, 20) and value >= 10:
                current = current * 100 + value
                continue
            # "twenty" then "two" is 22; "two" then "twenty" is two numbers.
            if counting and (
                value >= 10 and current % 100 or current % 10 and value < 10
            ):
                close()
            current += value
            counting = True
        elif word == "hundred" and counting and current:
            current *= 100
        elif word in _SCALES and counting and current:
            total += current * _SCALES[word]
            current = 0
        elif word != "and" or not counting:
            close()
    close()
    return found


def _same_figure(script_figure: int, source: frozenset) -> bool:
    """A script figure the source supports: itself, or a plain expansion of one.

    "10k" read as 10000 and "FY26" read as 2026 are the source's own numbers.
    """
    if script_figure in source:
        return True
    text = str(script_figure)
    for figure in source:
        digits = str(figure)
        if text.startswith(digits) and not text[len(digits) :].strip("0"):
            return True
        if len(digits) == 2 and text in ("19" + digits, "20" + digits):
            return True
    return False


def figure_differences(source: str, script: str) -> Tuple[List[int], List[int]]:
    """``(invented, missing)``: figures the script states that the source does
    not, and figures the source states that the script left out."""
    source_figures = frozenset(n for n in figures(source) if n >= _CHECKED_FROM)
    script_figures = frozenset(n for n in figures(script) if n >= _CHECKED_FROM)
    invented = sorted(n for n in script_figures if not _same_figure(n, source_figures))
    covered = "".join(str(n) for n in figures(script))
    # A page's printed number is a footer, left out on purpose.
    own_pages = {int(number) for number in _PAGE_MARKER.findall(source)}
    missing = sorted(
        n
        for n in source_figures - script_figures - own_pages
        # A code spelled out digit by digit ("4 4 7 1") still states it.
        if str(n) not in covered
    )
    return invented, missing


def _figure_feedback(invented: Sequence[int], missing: Sequence[int]) -> str:
    said = []
    if invented:
        said.append(
            "it states "
            + ", ".join(f"{n:,}" for n in invented)
            + ", which the document does not"
        )
    if missing:
        said.append(
            "it leaves out "
            + ", ".join(f"{n:,}" for n in missing)
            + " from the document"
        )
    return (
        f"Your last script got numbers wrong: {'; and '.join(said)}. Write the "
        "script for this part again, copying every number from the document "
        "digit for digit."
    )


def rewrite_for_listening(
    text: str,
    title: str,
    ask: Callable[[str, str], str],
    on_part: Optional[Callable[[int, int], None]] = None,
    limit: int = REWRITE_PART_CHARS,
    left_out: Optional[List[int]] = None,
) -> str:
    """The listening script for *text*, as Markdown.

    *ask* is ``(system prompt, prompt) -> reply``. Each part's figures are
    checked against the page: a part that states a number the document does
    not is asked for once more, and fails the rewrite if it still does. A
    figure the script drops is asked for once more and then added to
    *left_out*, since a page header or a footnote mark is dropped on purpose.
    A part that comes back empty fails too: a script with a silent gap looks
    complete and is not.
    """
    parts = rewrite_parts(text, limit)
    if not parts:
        raise DictationError(f"{title} has no text to rewrite.")
    script: List[str] = []
    for index, part in enumerate(parts, 1):
        if on_part:
            on_part(index, len(parts))
        tail = "\n".join("\n\n".join(script).splitlines()[-6:])
        prompt = rewrite_prompt(part, index, len(parts), title, tail)
        reply = (ask(LISTENING_SYSTEM_PROMPT, prompt) or "").strip()
        invented, missing = figure_differences(part, reply) if reply else ([], [])
        if invented or missing:
            again = f"{prompt}\n\n{_figure_feedback(invented, missing)}"
            reply = (ask(LISTENING_SYSTEM_PROMPT, again) or "").strip()
            invented, missing = figure_differences(part, reply) if reply else ([], [])
        if not reply:
            raise DictationError(
                f"The model returned nothing for part {index} of {len(parts)} of "
                f"{title}, so the listening script would have a gap. Try again, "
                "or dictate it as written."
            )
        if invented:
            raise DictationError(
                f"The listening script for part {index} of {len(parts)} of {title} "
                f"states {', '.join(f'{n:,}' for n in invented)}, which the "
                "document does not, and a second attempt did not fix it. Nothing "
                "was recorded. Dictate it as written, which keeps the document's "
                "own figures, or use a larger chat model for the rewrite."
            )
        if left_out is not None:
            left_out.extend(missing)
        script.append(reply)
    return "\n\n".join(script) + "\n"


# ── Reading the document ─────────────────────────────────────────────────────


def document_text(path: str) -> Tuple[str, Dict[str, object], bool]:
    """``(text, metadata, is_markdown)`` for a document that can be dictated.

    Raises:
        DictationError: the file is not a kind of document GAIA reads aloud, or
            holds no text.
        ImportError: the format's parser is not installed.
    """
    from gaia.agents.tools.document_text import (
        READABLE_DOCUMENTS,
        extract_document_text,
    )
    from gaia.agents.tools.text_files import read_text

    name = os.path.basename(path)
    suffix = os.path.splitext(path)[1].lower()
    if suffix in READABLE_DOCUMENTS:
        try:
            text, meta = extract_document_text(path)
        except ValueError as e:
            raise DictationError(str(e)) from e
    elif suffix in TEXT_SUFFIXES:
        try:
            text, _encoding = read_text(path)
        except UnicodeDecodeError as e:
            raise DictationError(
                f"{name} is not readable text, so there is nothing to dictate."
            ) from e
        meta = {}
    else:
        kinds = sorted(set(READABLE_DOCUMENTS) | TEXT_SUFFIXES)
        raise DictationError(
            f"{name} is not a document GAIA can dictate. It reads "
            f"{', '.join(kinds)} files. For another format, read its text and "
            "pass that to text_to_speech."
        )
    if not text.strip():
        raise DictationError(f"{name} has no text to dictate.")
    return text, dict(meta), suffix in MARKDOWN_SUFFIXES
