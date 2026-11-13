# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Do the figures in the files this turn wrote rest on what its tools saw?

The answer checks in :mod:`gaia.agents.base.grounding` read the chat reply
only, so a price invented inside ``run_python`` and saved to ``bom.xlsx``
went unseen. This reads each deliverable the turn wrote — text formats and
the cells of .xlsx/.docx/.pptx, bounded in size — and finds its money,
percentages and unit quantities. Each must appear in a tool result this turn,
an input file, or the conversation, or follow from ones that do: a line
total, a column sum, a share, a per-unit split. A figure the file itself
marks as an estimate is left alone.

Evidence is what tools *returned*, never what the call sent: a search that
echoes its query, a script's own literals, and reading back the file this
turn wrote prove nothing about where a figure came from.

A finding only reaches the answer's "not confirmed" note. The model is never
asked to change the file, so a false positive cannot damage a correct
deliverable, and nothing here edits it.
"""

from __future__ import annotations

import ast
import builtins
import csv
import decimal
import functools
import io
import json
import logging
import math
import os
import re
import string
import zipfile
from dataclasses import dataclass
from types import MappingProxyType
from typing import (
    Any,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from gaia.agents.base.claims import admits_unverified
from gaia.agents.base.grounding import (
    _EXEC_WORDS,
    _TABLE_RULE,
    Finding,
    _arg_strings,
    _args_text,
    _Corpus,
    _has,
    _name,
    _ran,
    _rounds_to,
    _Value,
    _value,
    evidence_text,
)

logger = logging.getLogger(__name__)

FILE = "file"

#: Formats whose figures are read; anything else is not a deliverable here.
DELIVERABLE_SUFFIXES = frozenset(
    {".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".xlsx", ".docx", ".pptx"}
)
MAX_FILE_BYTES = 5_000_000
MAX_TEXT_CHARS = 400_000
MAX_ROWS = 2_000
MAX_COLS = 40
MAX_SHEETS = 10
MAX_CLAIMS = 200
#: Values named per file in the correction and the note.
_SHOWN = 5


@dataclass
class Block:
    """A table (``header`` + rows of cells) or prose (one-cell rows)."""

    rows: List[List[str]]
    header: Optional[List[str]] = None
    table: bool = False


# ---------------------------------------------------------------------------
# Reading a file into blocks
# ---------------------------------------------------------------------------

_PURE_NUMBER = re.compile(r"^\s*-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*$")


def _table(rows: List[List[str]]) -> Block:
    """A table whose header is its first row of labels, if one comes early."""
    rows = [r[:MAX_COLS] for r in rows[:MAX_ROWS] if any(c.strip() for c in r)]
    for index, row in enumerate(rows[:5]):
        filled = [c for c in row if c.strip()]
        if len(filled) >= 2 and not any(_PURE_NUMBER.match(c) for c in filled):
            return Block(rows[:index] + rows[index + 1 :], row, True)
    return Block(rows, None, True)


def _md_cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _markdown_blocks(text: str) -> List[Block]:
    blocks: List[Block] = []
    prose: List[List[str]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        rule = index + 1 < len(lines) and _TABLE_RULE.match(lines[index + 1])
        if line.strip().startswith("|") and rule:
            header = _md_cells(line)
            index += 2
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_md_cells(lines[index]))
                index += 1
            blocks.append(Block(rows[:MAX_ROWS], header, True))
            continue
        if line.strip():
            prose.append([line])
        index += 1
    if prose:
        blocks.insert(0, Block(prose[:MAX_ROWS]))
    return blocks


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _json_cell(value: Any) -> str:
    return "" if value is None else str(value)


def _json_blocks(data: Any, out: List[Block], depth: int = 0) -> None:
    if depth > 20 or len(out) > 500:
        return
    if isinstance(data, list):
        dicts = [d for d in data[:MAX_ROWS] if isinstance(d, dict)]
        keys = list(dict.fromkeys(k for d in dicts for k, v in d.items() if _scalar(v)))
        if keys:
            out.append(
                Block(
                    [[_json_cell(d.get(k)) for k in keys] for d in dicts],
                    [str(k) for k in keys],
                    True,
                )
            )
        strings = [[s] for s in data[:MAX_ROWS] if isinstance(s, str)]
        if strings:
            out.append(Block(strings))
        for item in data[:MAX_ROWS]:
            children = item.values() if isinstance(item, dict) else [item]
            for child in children:
                if not _scalar(child):
                    _json_blocks(child, out, depth + 1)
    elif isinstance(data, dict):
        keys = [k for k, v in data.items() if _scalar(v)]
        if keys:
            out.append(
                Block(
                    [[_json_cell(data[k]) for k in keys]], [str(k) for k in keys], True
                )
            )
        for value in data.values():
            if not _scalar(value):
                _json_blocks(value, out, depth + 1)


def text_blocks(text: str, suffix: str) -> List[Block]:
    """*text* as blocks, read the way its *suffix* says."""
    text = text[:MAX_TEXT_CHARS]
    if suffix in (".csv", ".tsv"):
        delimiter = "\t" if suffix == ".tsv" else ","
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        except csv.Error:
            return _markdown_blocks(text)
        return [_table(rows)] if rows else []
    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return _markdown_blocks(text)
        out: List[Block] = []
        _json_blocks(data, out)
        return out
    return _markdown_blocks(text)


def _format_decimals(section: str, value: Any) -> int:
    match = re.search(r"\.(0+)", section)
    if match:
        return len(match.group(1))
    if isinstance(value, float) and not value.is_integer():
        digits = repr(value)
        if "e" in digits or "." not in digits:
            return 2
        return min(len(digits.split(".", 1)[1]), 4)
    return 0


def _xlsx_cell(cell: Any) -> str:
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    section = (getattr(cell, "number_format", None) or "General").split(";")[0]
    if "%" in section:
        return f"{value * 100:.{_format_decimals(section, value * 100)}f}%"
    decimals = _format_decimals(section, value)
    text = f"{value:,.{decimals}f}" if "," in section else f"{value:.{decimals}f}"
    symbol = re.search(r"[$€£¥]", section)
    return symbol.group(0) + text if symbol else text


def _xlsx_blocks(path: str) -> List[Block]:
    import openpyxl  # pylint: disable=import-outside-toplevel

    # Formulas stay formulas: a computed cell is derived by construction.
    book = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        blocks = []
        for sheet in book.worksheets[:MAX_SHEETS]:
            rows = [
                [_xlsx_cell(c) for c in row]
                for row in sheet.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS)
            ]
            if any(any(r) for r in rows):
                blocks.append(_table(rows))
        return blocks
    finally:
        book.close()


def _docx_blocks(path: str) -> List[Block]:
    from docx import Document  # pylint: disable=import-outside-toplevel

    document = Document(path)
    prose = [[p.text] for p in document.paragraphs[: MAX_ROWS * 2] if p.text.strip()]
    blocks = [Block(prose)] if prose else []
    for table in document.tables[:50]:
        rows = [[c.text for c in r.cells[:MAX_COLS]] for r in table.rows[:MAX_ROWS]]
        if rows:
            blocks.append(_table(rows))
    return blocks


def _pptx_blocks(path: str) -> List[Block]:
    from pptx import Presentation  # pylint: disable=import-outside-toplevel

    blocks = []
    for slide in list(Presentation(path).slides)[:200]:
        prose = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                for paragraph in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in paragraph.runs)
                    if text.strip():
                        prose.append([text])
            if getattr(shape, "has_table", False):
                rows = [[c.text for c in r.cells] for r in shape.table.rows]
                if rows:
                    blocks.append(_table(rows))
        if prose:
            blocks.append(Block(prose))
    return blocks


def read_blocks(path: str) -> List[Block]:
    """The figures-bearing content of the file at *path*, bounded in size.

    ``[]`` for an unsupported, oversized or unreadable file, logged: this is
    an advisory check and must never fail the turn.
    """
    suffix = os.path.splitext(path)[1].lower()
    if suffix not in DELIVERABLE_SUFFIXES:
        return []
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            logger.info(
                "Not checking figures in %s: larger than %d bytes", path, MAX_FILE_BYTES
            )
            return []
        if suffix == ".xlsx":
            return _xlsx_blocks(path)
        if suffix == ".docx":
            return _docx_blocks(path)
        if suffix == ".pptx":
            return _pptx_blocks(path)
        with open(path, encoding="utf-8", errors="replace") as handle:
            return text_blocks(handle.read(MAX_TEXT_CHARS), suffix)
    except ImportError as error:
        logger.info("Not checking figures in %s: %s is not installed", path, error.name)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        logger.warning("Could not read %s to check its figures: %s", path, error)
    return []


def blocks_text(blocks: Sequence[Block]) -> str:
    """*blocks* as plain lines, cells joined by `` | `` so rows stay tabular."""
    lines = []
    for block in blocks:
        if block.header:
            lines.append("| " + " | ".join(block.header) + " |")
        lines.extend("| " + " | ".join(row) + " |" for row in block.rows)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Which tool calls and files point at inputs
# ---------------------------------------------------------------------------

_PATH_ARGS = ("file_path", "path", "input_path", "source", "filename", "file")


def input_paths(records: Sequence[Dict[str, Any]]) -> List[str]:
    """Deliverable-format files this turn's tools were pointed at, in order.

    Path arguments, plus string literals in code a tool ran — a script that
    reads ``prices.csv`` makes that file an input even if nothing printed it.
    """
    found: List[str] = []
    for record in records:
        if not _ran(record) or record.get("failed"):
            continue
        args = record.get("args") or {}
        if not isinstance(args, dict):
            continue
        for key in _PATH_ARGS:
            if isinstance(args.get(key), str):
                found.append(args[key])
        code = args.get("code")
        if isinstance(code, str):
            tree = _parsed(code)
            if tree is None:
                continue
            found.extend(
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "\n" not in node.value
                and len(node.value) < 1024
            )
    return [
        p
        for p in dict.fromkeys(found)
        if "\x00" not in p and os.path.splitext(p)[1].lower() in DELIVERABLE_SUFFIXES
    ]


# ---------------------------------------------------------------------------
# Figures in the file
# ---------------------------------------------------------------------------

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_CODES = r"USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR"
_SYMBOLS = r"US\$|C\$|A\$|\$|€|£|¥"
#: "$14.5M", "$500k", "$14.5 million", "$2.1bn": the scale after a figure.
_MAGNITUDES = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "mm": 1e6,
    "mn": 1e6,
    "mil": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "t": 1e12,
    "tn": 1e12,
    "trillion": 1e12,
}
_MAG = r"(?i:thousand|million|billion|trillion|mil|mn|mm|bn|tn|[kmbt])(?!\w)"
_MONEY = re.compile(
    rf"(?:{_SYMBOLS}|\b(?:{_CODES})\s?)\s?(?P<a>{_NUM})(?:\s?(?P<ma>{_MAG}))?"
    rf"|(?<![\w.,])(?P<b>{_NUM})(?:\s?(?P<mb>{_MAG}))?"
    rf"\s?(?:€|£|\b(?:{_CODES})\b|(?i:\s?(?:dollars?|euros?)\b))"
)
#: "$3–5M", "$3M-$5M", "$3 to 5 million": both ends take the closing scale.
_MONEY_RANGE = re.compile(
    rf"(?:{_SYMBOLS})\s?(?P<lo>{_NUM})(?:\s?(?P<lomag>{_MAG}))?"
    rf"\s?(?:-|–|—|to)\s?(?:{_SYMBOLS})?\s?(?P<hi>{_NUM})\s?(?P<mag>{_MAG})"
)
_WORD_UNITS = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_WORD_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
#: "twelve percent", "twenty-five per cent", "three million dollars".
_WORD_FIGURE = re.compile(
    rf"\b(?P<w>(?:{'|'.join(_WORD_TENS)})(?:[- ](?:{'|'.join(_WORD_UNITS[1:10])}))?"
    rf"|{'|'.join(sorted(_WORD_UNITS, key=len, reverse=True))})\s+"
    r"(?:(?P<pct>percent|per\s+cent)\b|(?P<mag>thousand|million|billion)\b"
    r"(?P<cur>\s+(?:dollars?|euros?)\b)?)",
    re.I,
)


def _magnitude_value(number: str, magnitude: Optional[str]) -> _Value:
    """*number* scaled by *magnitude*; its precision scales with it.

    "$14.5M" is 14,500,000 to the nearest 100,000, so a source saying
    "$14,500,000" (or "$14,480,000") supports it.
    """
    value = _value(number)
    scale = _MAGNITUDES.get((magnitude or "").lower())
    if not scale or value.number is None:
        return value
    places = value.decimals - round(math.log10(scale))
    return _Value(_plain(value.number * scale), value.number * scale, places)


def _plain(number: float) -> str:
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _word_number(word: str) -> int:
    parts = re.split(r"[- ]", word.lower())
    total = 0
    for part in parts:
        if part in _WORD_TENS:
            total += 20 + 10 * _WORD_TENS.index(part)
        elif part in _WORD_UNITS:
            total += _WORD_UNITS.index(part)
    return total


def _spelled_out(text: str) -> str:
    """*text* with each scaled or spelled-out figure also written in full.

    "$14.5 million" gains "($14500000)" and "twelve percent" gains "(12%)", so
    a file that writes the other form finds it.
    """
    if not text:
        return text

    def full(match: re.Match) -> str:
        groups = match.groupdict()
        if "lo" in groups:
            mag = groups["lomag"] or groups["mag"]
            lo = _magnitude_value(groups["lo"], mag).text
            hi = _magnitude_value(groups["hi"], groups["mag"]).text
            return f"{match.group(0)} (${lo} ${hi})"
        number = groups.get("a") or groups.get("b")
        mag = groups.get("ma") or groups.get("mb")
        if not mag:
            return match.group(0)
        return f"{match.group(0)} (${_magnitude_value(number, mag).text})"

    def words(match: re.Match) -> str:
        number = _word_number(match.group("w"))
        if match.group("pct"):
            return f"{match.group(0)} ({number}%)"
        scaled = _plain(number * _MAGNITUDES[match.group("mag").lower()])
        return f"{match.group(0)} ({'$' if match.group('cur') else ''}{scaled})"

    text = _MONEY_RANGE.sub(full, text)
    text = _MONEY.sub(full, text)
    return _WORD_FIGURE.sub(words, text)


_PERCENT = re.compile(rf"(?<![\w.,])({_NUM})\s?%")
_UNIT = re.compile(
    rf"(?<![\w.,])({_NUM})\s?(kW|mW|W|mV|V|mAh|Ah|mA|A|GHz|MHz|kHz|Hz|TB|GB|MB|KB|kB|"
    r"Gbps|Mbps|ms|µs|ns|°C|mm|cm|kg)(?![\w/])"
)
_ANY_NUMBER = re.compile(rf"(?<![\w.,])(?:{_NUM})(?![\w]|\.\d)")
#: A column of money: "Unit price", "Line total (USD)", "Cost $".
_MONEY_HEADER = re.compile(
    r"price|cost|total|amount|subtotal|budget|fee|msrp|spend|revenue|usd|eur|gbp|[$€£]",
    re.I,
)
#: ...unless it counts or measures something else: "Total qty", "Total time (s)".
_COUNT_HEADER = re.compile(
    r"qty|quantity|\bunits\b|\bcount\b|\bpcs\b|hours|%|percent|#|number of|time|"
    r"\bsecs?\b|seconds|\bms\b|duration|size|bytes|weight|tokens|score",
    re.I,
)
#: The file itself says the figure is not confirmed.
_MARKER = re.compile(
    r"\b(?:est\.?|estimated?|estimates?|approx\.?|approximate(?:ly)?|unverified|"
    r"not\s+verified|unconfirmed|assumed|assumption|tbd|tbc|placeholder|ballpark|"
    r"indicative)(?!\w)|~|≈",
    re.I,
)
#: "All prices are estimates", "Figures below are unverified".
_BLANKET = re.compile(
    r"\b(?:all|these|the|listed|above|below)\s+(?:\w+\s+){0,2}"
    r"(?:prices?|costs?|values?|figures?|numbers?|amounts?|totals?)\b[^.\n]{0,60}?"
    r"\b(?:estimat\w*|unverified|not\s+verified|approximat\w*|indicative|placeholders?)",
    re.I,
)


@dataclass
class _Claim:
    #: ``"money"``, ``"percent"``, or the unit symbol.
    kind: str
    value: _Value
    shown: str
    #: The column or paragraph it sits in, and its place there.
    seq: Tuple[int, int]
    pos: int
    row: int
    #: The file says it is an estimate: never reported, still an operand.
    marked: bool = False
    #: Another reading of the same text: "14.5mm" may be millimetres or
    #: millions in a finance memo.
    alt: Optional[_Value] = None


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _scan(blocks: Sequence[Block]) -> Tuple[List[_Claim], List[List[float]], List[str]]:
    """``(claims, numbers per row, every plain number's text)``."""
    claims: List[_Claim] = []
    rows: List[List[float]] = []
    plain: List[str] = []
    for b, block in enumerate(blocks):
        header = block.header or []
        money_cols = {
            j
            for j, h in enumerate(header)
            if _MONEY_HEADER.search(h) and not _COUNT_HEADER.search(h)
        }
        marked_cols = {j for j, h in enumerate(header) if _MARKER.search(h)}
        place = 0
        for i, row in enumerate(block.rows):
            row_text = " | ".join(row)
            numbers = _ANY_NUMBER.findall(row_text)
            plain.extend(numbers)
            rows.append([_number(n) for n in numbers])
            row_marked = bool(_MARKER.search(row_text))
            for j, cell in enumerate(row):
                if cell.lstrip().startswith("="):
                    continue
                for kind, value, shown in _cell_figures(cell, j in money_cols):
                    if not shown:
                        shown = f"{value.text} ({header[j].strip()})"
                    pos = i if block.table else place
                    place += 1
                    seq = (b, j if block.table else -1)
                    marked = row_marked or j in marked_cols
                    alt = (
                        _magnitude_value(value.text, "mm")
                        if kind == "mm" and shown.endswith("mm")
                        else None
                    )
                    claims.append(
                        _Claim(kind, value, shown, seq, pos, len(rows) - 1, marked, alt)
                    )
    return claims[:MAX_CLAIMS], rows, plain


def _cell_figures(cell: str, money_column: bool) -> List[Tuple[str, _Value, str]]:
    """``(kind, value, shown)`` per figure in *cell*; ``shown`` is ``""`` for a
    bare number in a money column, which the caller labels with the header.

    A scale after a money figure ("$14.5M", "$500k") is part of its value.
    """
    figures: List[Tuple[str, _Value, str]] = []
    taken: List[Tuple[int, int]] = []
    for m in _MONEY_RANGE.finditer(cell):
        mag = m.group("lomag") or m.group("mag")
        figures.append(("money", _magnitude_value(m.group("lo"), mag), m.group(0)))
        figures.append(
            ("money", _magnitude_value(m.group("hi"), m.group("mag")), m.group(0))
        )
        taken.append(m.span())
    for m in _MONEY.finditer(cell):
        if any(start <= m.start() < end for start, end in taken):
            continue
        number = m.group("a") or m.group("b")
        mag = m.group("ma") or m.group("mb")
        figures.append(("money", _magnitude_value(number, mag), m.group(0).strip()))
        taken.append(m.span())
    if not figures and money_column and _PURE_NUMBER.match(cell):
        figures.append(("money", _value(cell.strip()), ""))
    figures += [
        ("percent", _value(m.group(1)), m.group(0).strip())
        for m in _PERCENT.finditer(cell)
    ]
    figures += [
        (m.group(2), _value(m.group(1)), m.group(0).strip())
        for m in _UNIT.finditer(cell)
        if not any(start <= m.start() < end for start, end in taken)
    ]
    return figures


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

_WRITE_WORDS = (
    "write",
    "edit",
    "save",
    "create",
    "append",
    "export",
    "replace",
    "insert",
)
_MONEY_WORDS = re.compile(
    r"price|pricing|cost|msrp|subtotal|quote|\bfee\b|usd|eur|gbp", re.I
)
#: A data row: CSV (commas, never ", " as prose has), tabs, or a pipe table.
_TABULAR = re.compile(r"^(?!.*, )[^,]+,|\t|\|.*\|")


def _money_evidence(text: str) -> str:
    """The numbers in *text* that can be a price.

    Tagged with a currency, on a short line that talks about price, in a
    data row (CSV, tab, pipe table), or the value under a ``price`` key. A
    datasheet's "60 Hz" is not a source for $60.
    """
    text = _spelled_out(text)
    parts = [m.group("a") or m.group("b") for m in _MONEY.finditer(text)]
    previous = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) <= 400 and _TABULAR.search(stripped):
            parts.append(stripped)
        elif len(stripped) <= 200 and _MONEY_WORDS.search(stripped):
            parts.append(stripped)
        elif _PURE_NUMBER.match(stripped) and _MONEY_WORDS.search(previous):
            parts.append(stripped)
        previous = stripped
    return "\n".join(parts)


def _basename(path: str) -> str:
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1].lower()


#: Calls that bring a file into a script; each must name it as a literal.
_PY_READERS = {
    "open",
    "read_csv",
    "read_excel",
    "read_json",
    "read_table",
    "read_parquet",
    "load_workbook",
    "Path",
    "PdfReader",
    "Document",
    "Presentation",
    "loadtxt",
    "genfromtxt",
}
#: Ways a script reaches data without naming a file the turn read.
_PY_UNSEEN = {
    "glob",
    "iglob",
    "rglob",
    "listdir",
    "scandir",
    "walk",
    "iterdir",
    "requests",
    "urllib",
    "urlopen",
    "httpx",
    "subprocess",
    "system",
    "popen",
    "environ",
    "getenv",
    "input",
    "stdin",
    "sqlite3",
    "eval",
    "exec",
    "compile",
}
#: The largest figure a script may type itself: a count, an index, a rounding.
_SMALL_INT = 10
#: ``5,000`` is one number, not a 5 and a 0.
_DIGITS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
#: Type names such as ``'int64'`` or ``'float32'``: no figure.
_DTYPE = re.compile(
    r"(?:u?int|float|complex|Int|UInt|Float)(?:8|16|32|64|128)"
    r"|(?:datetime|timedelta)64(?:\[\w+\])?"
)
#: ``%10.2f`` and ``{:>12}`` size a column; they type no figure.
_FORMAT_DIRECTIVE = re.compile(
    r"%[-+ #0]*\d*(?:\.\d+)?[sdifeEgGxXr%]|\{[\w.\[\]]*(?:![rsa])?(?::[^{}]*)?\}"
)
#: Builtins that turn literals into a figure: ``int('9' + '5')``, ``ord('_')``.
_PY_FOLDERS = {
    "int",
    "float",
    "str",
    "Decimal",
    "ord",
    "chr",
    "len",
    "abs",
    "round",
    "sum",
    "max",
    "min",
}
_PY_OPEN_MODULES = {"io", "codecs"}
#: Calls whose literal arguments pick rows or dates, never a figure the
#: script outputs: ``head(20)``, ``nlargest(25, ...)``, ``date(2026, 3, 1)``.
_PY_SELECTORS = {
    "head",
    "tail",
    "nlargest",
    "nsmallest",
    "sample",
    "quantile",
    "between",
    "isin",
    "startswith",
    "endswith",
    "date",
    "datetime",
    "Timestamp",
    "to_datetime",
    "strptime",
    "strftime",
    "fromisoformat",
    "date_range",
    "period_range",
    "Period",
    "DateOffset",
    "timedelta",
}
#: An ISO date in a string names a day, not an amount.
_DATE_TEXT = re.compile(
    r"(?<![\d-])(?:19|20)\d{2}-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2})?)?(?![\d-])"
)
_YEAR = re.compile(r"(?:19|20)\d{2}")
_SEQUENCES = (str, list, tuple)


#: Past this a folded value is no figure anyone typed; stop folding it.
_FOLD_LIMIT = 10**15
_FOLD_ITEMS = 10_000


class _Unfoldable(Exception):
    """The expression depends on something other than the script's literals."""


class _Bound:
    """Names assigned once, folded on first use: ``a = 9; b = 10``."""

    def __init__(self, values: Dict[str, Any]):
        self.values = values
        self.folded: Dict[str, Any] = {}

    def __contains__(self, name: str) -> bool:
        return name in self.values

    def fold(self, name: str, depth: int) -> Any:
        if name not in self.folded:
            self.folded[name] = _Unfoldable
            try:
                self.folded[name] = (_fold(self.values[name], depth + 1, self),)
            except _Unfoldable:
                pass
        if self.folded[name] is _Unfoldable:
            raise _Unfoldable
        return self.folded[name][0]


#: ``%200000000s`` and ``%*s``: a width that would build a huge string.
_PERCENT_SPEC = re.compile(r"%(?:\([^)]*\))?[-+ #0]*(?:\*|\d+)?(?:\.(?:\*|\d+))?")
#: String methods that pad their receiver to a width.
_PY_PADDERS = ("ljust", "rjust", "center", "zfill")


def _fold_size(value: Any) -> int:
    """Characters and items in *value*, counted until past ``_FOLD_ITEMS``."""
    total = 0
    pending = [value]
    while pending and total <= _FOLD_ITEMS:
        item = pending.pop()
        if isinstance(item, str):
            total += len(item)
        elif isinstance(item, (list, tuple)):
            total += len(item)
            if total <= _FOLD_ITEMS:
                pending.extend(item)
    return total


def _capped(value: Any) -> Any:
    if isinstance(value, _SEQUENCES) and _fold_size(value) > _FOLD_ITEMS:
        raise _Unfoldable
    return value


def _wide(spec: str) -> bool:
    """*spec* pads or rounds past ``_FOLD_ITEMS``, or takes its width from
    an argument."""
    return (
        "{" in spec
        or "*" in spec
        or any(len(n) > 6 or int(n) > _FOLD_ITEMS for n in re.findall(r"\d+", spec))
    )


def _huge_decimal(value: Any) -> bool:
    """``Decimal('1e10000000')``: turning it into an int would take minutes."""
    return (
        isinstance(value, decimal.Decimal)
        and value.is_finite()
        and value.adjusted() > 15
    )


def _fold(node: Any, depth: int = 0, bound: Optional[_Bound] = None) -> Any:
    """The value of an expression built only from literals, ``9*10+5``.

    *bound* maps names assigned once to the expression they hold, so
    ``a = 9; b = 10; a*b+5`` folds too. A string or list past
    ``_FOLD_ITEMS`` does not fold: building it would stall the turn.
    """
    if depth > 32:
        raise _Unfoldable
    return _capped(_fold_value(node, depth, bound))


def _fold_value(node: Any, depth: int, bound: Optional[_Bound]) -> Any:
    def sub(child: Any) -> Any:
        return _fold(child, depth + 1, bound)

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)):
        return node.value
    if isinstance(node, ast.Name) and bound is not None and node.id in bound:
        return bound.fold(node.id, depth)
    try:
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [sub(e) for e in node.elts]
            return items if isinstance(node, ast.List) else tuple(items)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = sub(node.operand)
            return -value if isinstance(node.op, ast.USub) else +value
        if isinstance(node, ast.BinOp):
            left, right = sub(node.left), sub(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > 64 or abs(left) > 1e6):
                raise _Unfoldable
            if isinstance(node.op, ast.Mult) and isinstance(
                left, _SEQUENCES
            ) != isinstance(right, _SEQUENCES):
                seq, times = (
                    (left, right)
                    if isinstance(left, _SEQUENCES)
                    else (
                        right,
                        left,
                    )
                )
                if abs(times) > 1000 or _fold_size(seq) * abs(times) > _FOLD_ITEMS:
                    raise _Unfoldable
            if isinstance(node.op, ast.Mod) and isinstance(left, str):
                if any(_wide(m.group(0)) for m in _PERCENT_SPEC.finditer(left)):
                    raise _Unfoldable
            ops = {
                ast.Add: lambda a, b: a + b,
                ast.Sub: lambda a, b: a - b,
                ast.Mult: lambda a, b: a * b,
                ast.Div: lambda a, b: a / b,
                ast.FloorDiv: lambda a, b: a // b,
                ast.Mod: lambda a, b: a % b,
                ast.Pow: lambda a, b: a**b,
            }
            if type(node.op) not in ops:
                raise _Unfoldable
            value = ops[type(node.op)](left, right)
            if isinstance(value, (int, float)) and abs(value) > _FOLD_LIMIT:
                raise _Unfoldable
            return value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    parts.append(str(sub(part.value)))
                else:
                    parts.append(str(sub(part)))
            if sum(len(p) for p in parts) > _FOLD_ITEMS:
                raise _Unfoldable
            return "".join(parts)
        if isinstance(node, ast.Call) and not node.keywords:
            func = node.func
            # Methods and map() before the arguments: a list argument or a
            # bare ``str`` must not stop ``''.join(map(str, [9, 5])))``.
            if isinstance(func, ast.Attribute) and func.attr in (
                "join",
                "format",
                *_PY_PADDERS,
            ):
                receiver = sub(func.value)
                if not isinstance(receiver, str):
                    raise _Unfoldable
                if func.attr == "join" and len(node.args) == 1:
                    items = sub(node.args[0])
                    joints = len(receiver) * max(len(items) - 1, 0)
                    if joints + _fold_size(items) > _FOLD_ITEMS:
                        raise _Unfoldable
                    return receiver.join(items)
                if func.attr == "format":
                    fields = string.Formatter().parse(receiver)
                    if any(spec and _wide(spec) for _, _, spec, _ in fields):
                        raise _Unfoldable
                    return receiver.format(*[sub(a) for a in node.args])
                if func.attr in _PY_PADDERS and 1 <= len(node.args) <= 2:
                    args = [sub(a) for a in node.args]
                    if not isinstance(args[0], int) or args[0] > _FOLD_ITEMS:
                        raise _Unfoldable
                    return getattr(receiver, func.attr)(*args)
                raise _Unfoldable
            if isinstance(func, ast.Name) and func.id == "map" and len(node.args) == 2:
                mapper = node.args[0]
                if not (isinstance(mapper, ast.Name) and mapper.id in _PY_FOLDERS):
                    raise _Unfoldable
                convert = getattr(builtins, mapper.id, None) or decimal.Decimal
                values = sub(node.args[1])
                if any(_huge_decimal(v) for v in values):
                    raise _Unfoldable
                return [convert(v) for v in values]
            if isinstance(func, ast.Name) and func.id in _PY_FOLDERS:
                args = [sub(a) for a in node.args]
                if any(_huge_decimal(a) for a in args):
                    raise _Unfoldable
                if func.id == "Decimal":
                    return decimal.Decimal(*args)
                return getattr(builtins, func.id)(*args)
    except _Unfoldable:
        raise
    except Exception as e:  # noqa: BLE001 - a literal that won't evaluate types nothing
        raise _Unfoldable from e
    raise _Unfoldable


def _types_a_figure(value: Any) -> bool:
    """*value* holds a number larger than a count: ``95``, ``'$95.00'``."""
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        if _DTYPE.fullmatch(value.strip()):
            return False
        text = _DATE_TEXT.sub(" ", _FORMAT_DIRECTIVE.sub(" ", value))
        return any(
            float(n.replace(",", "")) > _SMALL_INT for n in _DIGITS.findall(text)
        )
    if isinstance(value, (list, tuple)):
        return any(_types_a_figure(v) for v in value)
    if isinstance(value, decimal.Decimal):
        return not value.is_finite() or value.copy_abs() > _SMALL_INT
    try:
        return abs(value) > _SMALL_INT
    except TypeError:
        return False


def _bound_once(tree: ast.AST) -> _Bound:
    """Names assigned exactly once, mapped to the expression they hold."""
    stores: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stores[node.id] = stores.get(node.id, 0) + 1
    return _Bound(
        {
            node.targets[0].id: node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and stores.get(node.targets[0].id) == 1
        }
    )


def _titled(node: Any) -> bool:
    """A printed or written string whose only numbers are years:
    ``'Revenue 2026'``."""
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    text = _DATE_TEXT.sub(" ", _FORMAT_DIRECTIVE.sub(" ", node.value))
    return all(
        _YEAR.fullmatch(n)
        for n in _DIGITS.findall(text)
        if float(n.replace(",", "")) > _SMALL_INT
    )


def _call_name(call: ast.Call) -> Optional[str]:
    return getattr(call.func, "id", getattr(call.func, "attr", None))


def _selecting(tree: ast.AST, bound: _Bound) -> Set[int]:
    """Nodes whose literals pick rows, dates or a layout, not a figure.

    Comparisons (``r['date'] >= '2026-03-01'``, ``year == 2026``), subscripts
    and slices (``rows[:25]``), the arguments of a selector call
    (``nlargest(25, ...)``, ``date(2026, 3, 1)``, ``round(x, 2)``, a ``range``
    a loop walks), format specs, padding widths (``ljust(20)``), pandas
    display options, encodings and ``'=' * 40``. A name bound
    once counts when every use of it is one of these.
    """
    roots: List[Any] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            roots += [node.left, *node.comparators]
        elif isinstance(node, ast.Subscript):
            roots.append(node.slice)
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _PY_SELECTORS:
                roots += list(node.args) + [k.value for k in node.keywords]
            elif name == "round":
                roots += node.args[1:]
            elif name in ("print", "write"):
                roots += [n for a in node.args for n in ast.walk(a) if _titled(n)]
            elif name in _PY_PADDERS:
                roots += node.args[:1]
            elif name == "set_option":
                roots += list(node.args) + [k.value for k in node.keywords]
            roots += [k.value for k in node.keywords if k.arg == "encoding"]
        elif isinstance(node, (ast.For, ast.comprehension)):
            loop = node.iter
            if isinstance(loop, ast.Call) and _call_name(loop) == "range":
                roots += loop.args
        elif isinstance(node, ast.FormattedValue) and node.format_spec is not None:
            roots.append(node.format_spec)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            for side, other in ((node.left, node.right), (node.right, node.left)):
                if isinstance(side, ast.Constant) and isinstance(side.value, str):
                    roots.append(other)
    picked = {
        id(n)
        for root in roots
        if not any(isinstance(n, ast.NamedExpr) for n in ast.walk(root))
        for n in ast.walk(root)
    }
    loads: Dict[str, List[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.setdefault(node.id, []).append(id(node))
    for name, value in bound.values.items():
        uses = loads.get(name)
        if uses and all(u in picked for u in uses):
            picked |= {id(n) for n in ast.walk(value)}
    return picked


def _percent_scales(tree: ast.AST) -> Set[int]:
    """The ``100`` constants that turn a ratio into a percentage (``a / b *
    100``, ``share * 100`` for a ``share = v / total``) or a price into cents
    and back (``round(p * 100) ... / 100``)."""

    def is_hundred(node: Any) -> bool:
        return isinstance(node, ast.Constant) and node.value == 100

    ratio_names: Set[str] = set()

    def is_ratio(node: Any) -> bool:
        return any(
            (
                isinstance(n, ast.BinOp)
                and isinstance(n.op, ast.Div)
                and not _foldable(n.right)
            )
            or (isinstance(n, ast.Name) and n.id in ratio_names)
            for n in ast.walk(node)
        )

    for _ in range(2):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and is_ratio(node.value)
            ):
                ratio_names.add(node.targets[0].id)

    scales = set()
    times: Set[int] = set()
    over: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp):
            continue
        if isinstance(node.op, ast.Mult):
            for side, other in ((node.left, node.right), (node.right, node.left)):
                if is_hundred(side):
                    times.add(id(side))
                    if is_ratio(other):
                        scales.add(id(side))
        elif isinstance(node.op, (ast.Div, ast.FloorDiv)):
            if is_hundred(node.right) and not _foldable(node.left):
                over.add(id(node.right))
            if (
                isinstance(node.op, ast.Div)
                and isinstance(node.left, ast.BinOp)
                and isinstance(node.left.op, ast.Mult)
                and not _foldable(node.right)
            ):
                for side in (node.left.left, node.left.right):
                    if is_hundred(side):
                        scales.add(id(side))
    if times and over:
        scales |= times | over
    return scales


def _foldable(node: Any) -> bool:
    try:
        _fold(node)
    except _Unfoldable:
        return False
    return True


def _string_names(tree: ast.AST) -> Dict[str, str]:
    """Names bound once, to a string literal: ``csv_path = r"...orders.csv"``."""
    stores: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stores[node.id] = stores.get(node.id, 0) + 1
    return {
        node.targets[0].id: node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and stores.get(node.targets[0].id) == 1
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _computed(
    record: Dict[str, Any],
    read_names: Set[str],
    written: Set[str] = frozenset(),
    copied: FrozenSet[float] = frozenset(),
) -> bool:
    """*record* is a script whose output can only come from files read this turn.

    Every file it opens other than one the turn wrote was read this turn, and
    every figure it types is in *copied*: one a tool showed this turn.
    """
    opened = _script_opens(record, copied)
    read = [name for name in opened or () if name not in written]
    return bool(read) and all(name in read_names for name in read)


def _script_opens(
    record: Dict[str, Any], copied: FrozenSet[float] = frozenset()
) -> Optional[List[str]]:
    """The files a ``run_python`` script opens, or ``None`` when its figures
    may not come from them."""
    io = _script_io(record, copied)
    return None if io is None else io[0]


def _script_wrote(
    record: Dict[str, Any], copied: FrozenSet[float] = frozenset()
) -> Set[str]:
    """The files a computing script created or overwrote, not just read or
    appended to."""
    io = _script_io(record, copied)
    return set() if io is None else io[1]


#: Methods that replace the file their path names: ``df.to_csv(path)``.
_PY_SAVERS = {
    "to_csv",
    "to_excel",
    "to_markdown",
    "to_json",
    "to_html",
    "to_parquet",
    "save",
}


def _literal_path(node: Any, named: Dict[str, str]) -> Optional[str]:
    if isinstance(node, ast.Name) and node.id in named:
        return named[node.id]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_arg(call: ast.Call, index: int, keyword: str) -> Any:
    if len(call.args) > index:
        return call.args[index]
    return next((k.value for k in call.keywords if k.arg == keyword), None)


def _is_open(func: Any) -> bool:
    """``open``, ``io.open`` or ``codecs.open``: the same path-then-mode call."""
    return isinstance(func, ast.Name) or (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in _PY_OPEN_MODULES
    )


def _is_print(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Name) and call.func.id == "print"


def _truncates(call: ast.Call, named: Dict[str, str]) -> Optional[str]:
    """The path *call* replaces: ``open(p, 'w')``, ``Path(p).write_text``,
    ``df.to_csv(p)``. Not a read, and not an append."""
    func = call.func
    ident = getattr(func, "id", getattr(func, "attr", None))
    if ident == "open" and _is_open(func):
        mode = _call_arg(call, 1, "mode")
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
            if set(mode.value) & {"w", "x"}:
                return _literal_path(_call_arg(call, 0, "file"), named)
        return None
    if ident in ("write_text", "write_bytes") and isinstance(func, ast.Attribute):
        receiver = func.value
        if isinstance(receiver, ast.Call) and receiver.args:
            return _literal_path(receiver.args[0], named)
        return None
    if ident in _PY_SAVERS:
        mode = next((k.value for k in call.keywords if k.arg == "mode"), None)
        if isinstance(mode, ast.Constant) and "a" in str(mode.value):
            return None
        target = (call.args or [k.value for k in call.keywords] or [None])[0]
        return _literal_path(target, named)
    return None


def _reads(call: ast.Call, named: Dict[str, str]) -> Optional[str]:
    """The path *call* reads: ``open(p)``, ``Path(p).read_text()``,
    ``pd.read_csv(p)``."""
    func = call.func
    ident = getattr(func, "id", getattr(func, "attr", None))
    if ident == "open" and _is_open(func):
        return _literal_path(_call_arg(call, 0, "file"), named)
    if ident in ("read_text", "read_bytes") and isinstance(func, ast.Attribute):
        receiver = func.value
        if isinstance(receiver, ast.Call) and receiver.args:
            return _literal_path(receiver.args[0], named)
        return None
    if ident in _PY_READERS - {"open", "Path"}:
        target = (call.args or [k.value for k in call.keywords] or [None])[0]
        return _literal_path(target, named)
    return None


def _script_io(
    record: Dict[str, Any], copied: FrozenSet[float] = frozenset()
) -> Optional[Tuple[List[str], Set[str]]]:
    """``(files opened, files replaced)`` by a ``run_python`` script, or
    ``None`` when its figures may not come from those files.

    A file the script read and then saved back is not one it replaced: what
    it patched in place, another tool wrote.

    Each file it opens is named, as a literal or a name bound once to one; it
    reaches no other data; and it types no figure beyond a small count —
    ``v*40``, ``1500 - 90``, ``9*10+5`` or ``'CM5,95'.split(',')`` invents its
    result rather than computing it. A decimal in *copied* is exempt:
    a rate a tool printed this turn, typed into the script, was copied.
    """
    if _name(record) != "run_python":
        return None
    code = (record.get("args") or {}).get("code")
    if not isinstance(code, str):
        return None
    io = _analysed(code)
    if io is None or io[2] - copied:
        return None
    return list(io[0]), set(io[1])


@functools.lru_cache(maxsize=16)
def _parsed(code: str) -> Optional[ast.AST]:
    """*code*'s syntax tree, or ``None`` when it cannot be analysed."""
    try:
        return ast.parse(code)
    except (
        SyntaxError,
        ValueError,
        RecursionError,
        UnicodeEncodeError,
        MemoryError,
    ) as error:
        logger.warning(
            "Cannot analyse a %d-character run_python script: %s: %s",
            len(code),
            type(error).__name__,
            error,
        )
        return None


@functools.lru_cache(maxsize=128)
def _analysed(
    code: str,
) -> Optional[Tuple[Tuple[str, ...], FrozenSet[str], FrozenSet[float]]]:
    """``(files opened, files replaced, figures typed)`` for *code*."""
    tree = _parsed(code)
    if tree is None:
        return None
    opened: List[str] = []
    replaced: Set[str] = set()
    read_from: Set[str] = set()
    named = _string_names(tree)
    bound = _bound_once(tree)
    untyped = _percent_scales(tree) | _selecting(tree, bound)
    typed_strings: Set[str] = set()
    typed: Set[float] = set()
    paths: Set[str] = set()
    refs: Dict[str, int] = {}
    truncations: Dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, str) and value.lower().startswith(("http:", "https:")):
                return None
            # '$95.00', "CM5,95,40" or json.loads('{"cm5": 95}') types a price
            # as surely as 95 does, unless the string is a path the script opens.
            if isinstance(value, str) and id(node) not in untyped:
                if _types_a_figure(value):
                    typed_strings.add(value)
            if isinstance(value, (int, float, complex)) and not isinstance(value, bool):
                if id(node) in untyped:
                    continue
                if isinstance(value, complex):
                    return None
                if isinstance(value, int) or value.is_integer():
                    if abs(value) > _SMALL_INT:
                        return None
                    continue
                # A whole number matches a date or count by chance; a rate
                # like 1.0842 does not, so only that can be judged copied.
                if not math.isfinite(value):
                    return None
                typed.add(value)
        elif (
            isinstance(node, (ast.BinOp, ast.UnaryOp, ast.JoinedStr, ast.Call))
            and id(node) not in untyped
        ):
            # 9*10+5, int('9'+'5') or a=9; b=10; a*b+5: small literals that
            # make a price.
            try:
                if _types_a_figure(_fold(node, bound=bound)):
                    return None
            except _Unfoldable:
                pass
        if isinstance(node, (ast.Name, ast.Attribute)):
            if (node.id if isinstance(node, ast.Name) else node.attr) in _PY_UNSEEN:
                return None
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in node.names] + [
                getattr(node, "module", None) or ""
            ]
            if any(m.split(".")[0] in _PY_UNSEEN for m in modules):
                return None
        if isinstance(node, ast.Call):
            wrote = _truncates(node, named)
            if wrote is not None:
                replaced.add(_basename(wrote))
                paths.add(wrote)
                truncations[_basename(wrote)] = truncations.get(_basename(wrote), 0) + 1
            else:
                source = _reads(node, named)
                if source is not None:
                    read_from.add(_basename(source))
                    paths.add(source)
            for arg in list(node.args) + [k.value for k in node.keywords]:
                ref = _literal_path(arg, named)
                if ref is not None and not _is_print(node):
                    refs[_basename(ref)] = refs.get(_basename(ref), 0) + 1
            func = node.func
            if getattr(func, "id", getattr(func, "attr", None)) not in _PY_READERS:
                continue
            target = (node.args or [k.value for k in node.keywords] or [None])[0]
            path = _literal_path(target, named)
            if path is None:
                return None
            opened.append(_basename(path))
            paths.add(path)
    if typed_strings - paths:
        return None
    # A path handed to any call besides the overwrite, Path(p).read_text()
    # through a variable included, may have read the file first.
    read_from |= {name for name in replaced if refs.get(name, 0) > truncations[name]}
    return tuple(opened), frozenset(replaced - read_from), frozenset(typed)


def _script_written(
    files: Mapping[str, Sequence[Block]],
    records: Sequence[Dict[str, Any]],
    read_names: Set[str],
    written: Set[str],
    copied: Mapping[int, FrozenSet[float]] = MappingProxyType({}),
) -> List[str]:
    """What a computing script wrote straight into a file in *files*, minus
    its own literals — the file was its output, as stdout would have been.

    Only when the script created or overwrote that file and nothing wrote it
    after: a script that read or appended to it did not write what is there.
    """
    texts = []
    for path, blocks in files.items():
        name = _basename(path)
        last = None
        for record in records:
            if not _ran(record) or record.get("failed"):
                continue
            numbers = copied.get(id(record), frozenset())
            if _computed(record, read_names, written, numbers) and name in (
                _script_wrote(record, numbers)
            ):
                last = record
            elif _may_change(record, name):
                last = None
        if last is not None:
            output = {**last, "observed": blocks_text(blocks)}
            texts.append(_spelled_out(evidence_text(output, code_literals=True)))
    return texts


_PATH_BREAK = re.compile(r"[^\w.\\/:~-]+")


def _mentions(text: str, name: str) -> bool:
    """*text* names the file called *name*, as a path or a bare file name."""
    return any(_basename(t) == name for t in _PATH_BREAK.split(text) if t)


def _may_change(record: Dict[str, Any], name: str) -> bool:
    """*record* is a write or a run that names the file called *name*, other
    than a script that only reads it."""
    if not _has(_name(record), _WRITE_WORDS + _EXEC_WORDS):
        return False
    args = record.get("args") or {}
    if not any(_mentions(text, name) for text in _arg_strings(args)):
        return False
    code = args.get("code") if isinstance(args, dict) else None
    if _name(record) == "run_python" and isinstance(code, str):
        tree = _parsed(code)
        return tree is None or not _only_reads(tree, name)
    return True


def _read_target(call: ast.Call) -> Any:
    """The path argument of a *call* that only reads it: ``open(p)``,
    ``Path(p).read_text()``, ``pd.read_csv(p)``. Not a write or an append."""
    func = call.func
    ident = getattr(func, "id", getattr(func, "attr", None))
    if ident == "open" and _is_open(func):
        mode = _call_arg(call, 1, "mode")
        if mode is None or (
            isinstance(mode, ast.Constant)
            and isinstance(mode.value, str)
            and not set(mode.value) & {"w", "x", "a", "+"}
        ):
            return _call_arg(call, 0, "file")
        return None
    if ident in ("read_text", "read_bytes") and isinstance(func, ast.Attribute):
        receiver = func.value
        if isinstance(receiver, ast.Call) and receiver.args:
            return receiver.args[0]
        return None
    if ident in _PY_READERS - {"open", "Path"}:
        return (call.args or [k.value for k in call.keywords] or [None])[0]
    return None


def _only_reads(tree: ast.AST, name: str) -> bool:
    """Every place the script names the file called *name* is a call that
    reads it."""
    named = _string_names(tree)
    read = {
        id(target)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for target in [_read_target(node)]
        if target is not None
    }
    bindings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in named
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            mention = id(node) not in bindings and _mentions(node.value, name)
        elif (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in named
        ):
            mention = _mentions(named[node.id], name)
        else:
            continue
        if mention and id(node) not in read:
            return False
    return True


def _read_names(records: Sequence[Dict[str, Any]], written: Set[str]) -> Set[str]:
    """Names of the files a read tool returned this turn, other than *written*."""
    names = set()
    for record in records:
        name = _name(record)
        if not _ran(record) or record.get("failed"):
            continue
        if _has(name, _WRITE_WORDS + _EXEC_WORDS):
            continue
        args = record.get("args") or {}
        for key in _PATH_ARGS:
            if isinstance(args, dict) and isinstance(args.get(key), str):
                names.add(_basename(args[key]))
    return names - written


def _source_records(
    records: Sequence[Dict[str, Any]], written: Set[str]
) -> List[Dict[str, Any]]:
    """The tool results this turn that can source a figure in *written*.

    Not a write tool's reply, not a failed call, and not a read of a written
    file once it was written — a read-back shows the file, not its source.
    """
    first_write: Dict[str, int] = {}
    for index, record in enumerate(records):
        if _has(_name(record), _WRITE_WORDS + _EXEC_WORDS):
            args = _args_text(record)
            for name in written:
                if name in args:
                    first_write.setdefault(name, index)
    first_exec = next(
        (i for i, r in enumerate(records) if _has(_name(r), _EXEC_WORDS)),
        len(records),
    )
    sources = []
    for index, record in enumerate(records):
        name = _name(record)
        if not _ran(record) or record.get("failed"):
            continue
        if _has(name, _WRITE_WORDS) and not _has(name, _EXEC_WORDS):
            continue
        args = _args_text(record)
        if any(
            name_ in args and index > first_write.get(name_, first_exec - 1)
            for name_ in written
        ):
            continue
        sources.append(record)
    return sources


def _copied_numbers(
    sources: Sequence[Dict[str, Any]], shown: Iterable[str]
) -> Dict[int, FrozenSet[float]]:
    """Per script in *sources* (by ``id``), the numbers it could have copied:
    those another source or a *shown* text holds. Never its own output."""
    own = {
        id(record): frozenset(
            _number(n)
            for n in _ANY_NUMBER.findall(
                _spelled_out(evidence_text(record, code_literals=True))
            )
        )
        for record in sources
    }
    common = frozenset(_number(n) for text in shown for n in _ANY_NUMBER.findall(text))
    return {
        id(record): common.union(
            *(numbers for key, numbers in own.items() if key != id(record))
        )
        for record in sources
        if _name(record) == "run_python"
    }


def _evidence_texts(
    records: Sequence[Dict[str, Any]],
    written: Set[str],
    read_names: Set[str],
    copied: Mapping[int, FrozenSet[float]] = MappingProxyType({}),
) -> List[Tuple[str, bool]]:
    """``(text, computed)`` per tool result this turn that can source a figure
    in *written*; *computed* marks a script that computed from *read_names*.

    See :func:`_source_records` for which count. The script that wrote a
    file still counts for what it computed, minus its own literals.
    """
    return [
        (
            evidence_text(record, code_literals=True),
            _computed(record, read_names, written, copied.get(id(record), frozenset())),
        )
        for record in _source_records(records, written)
    ]


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _any_rounds(results: Iterable[float], value: _Value) -> bool:
    return any(_rounds_to(r, value) for r in results)


def _run_sums(
    index: int, claims: Sequence[_Claim], known: Sequence[bool], backward_only: bool
) -> bool:
    """*claims[index]* is the sum of the known figures just before (or after)
    it, or of the leading rows of another table column — a total in prose."""
    claim = claims[index]
    peers = [
        k
        for k, c in enumerate(claims)
        if c.seq == claim.seq and c.kind == claim.kind and k != index
    ]
    before = [k for k in peers if claims[k].pos < claim.pos][::-1]
    after = [k for k in peers if claims[k].pos > claim.pos]
    runs = [before] if backward_only else [before, after]
    columns: Dict[Tuple[int, int], List[int]] = {}
    for k, c in enumerate(claims):
        if c.seq != claim.seq and c.seq[1] >= 0 and c.kind == claim.kind:
            columns.setdefault(c.seq, []).append(k)
    runs.extend(columns.values())
    for run in runs:
        total = 0.0
        for k in run:
            if not known[k]:
                break
            total += claims[k].value.number
            if _rounds_to(total, claim.value):
                return True
    return False


def _follows(
    index: int,
    claims: Sequence[_Claim],
    known: Sequence[bool],
    rows: Sequence[Sequence[float]],
    request_numbers: Sequence[float],
    plain_known: Sequence[float],
) -> bool:
    """*claims[index]* follows from known figures by one arithmetic step."""
    claim = claims[index]
    value = claim.value
    same = list(
        dict.fromkeys(
            c.value.number
            for k, c in enumerate(claims)
            if known[k] and k != index and c.kind == claim.kind
        )
    )[:40]
    if _any_rounds(same, value):
        return True
    for i, a in enumerate(same):
        for b in same[i + 1 :]:
            if _any_rounds((a + b, abs(a - b), (a + b) / 2), value):
                return True
    multipliers = {
        m
        for m in list(rows[claim.row]) + list(request_numbers)
        if m not in (0.0, 1.0) and m <= 100_000 and m != value.number
    }
    percents = [
        c.value.number for k, c in enumerate(claims) if known[k] and c.kind == "percent"
    ]
    for a in same:
        if _any_rounds((r for m in multipliers for r in (a * m, a / m)), value):
            return True
        if _any_rounds(
            (
                r
                for p in percents
                for r in (a * p / 100, a * (1 + p / 100), a * (1 - p / 100))
            ),
            value,
        ):
            return True
    if _run_sums(index, claims, known, backward_only=False):
        return True
    if claim.kind == "percent":
        money = [
            c.value.number
            for k, c in enumerate(claims)
            if known[k] and c.kind == "money"
        ]
        pool = list(dict.fromkeys(money + list(plain_known)))[:40]
        for a in pool:
            for b in pool:
                if (
                    b
                    and a != b
                    and _any_rounds((100 * a / b, 100 * abs(a - b) / b), value)
                ):
                    return True
    return False


def _downstream(
    index: int,
    claims: Sequence[_Claim],
    rows: Sequence[Sequence[float]],
    request_numbers: Sequence[float],
) -> bool:
    """A total or line amount computed from other figures, sourced or not.

    Listing it beside the unit price it was computed from is noise: fixing
    the price fixes it. Only multiplication and running sums, so an invented
    price is never excused as the difference of two others.
    """
    claim = claims[index]
    known = [True] * len(claims)
    known[index] = False
    others = list(
        dict.fromkeys(
            c.value.number
            for k, c in enumerate(claims)
            if k != index and c.kind == claim.kind
        )
    )[:60]
    multipliers = {
        m
        for m in list(rows[claim.row]) + list(request_numbers)
        if m not in (0.0, 1.0) and m <= 100_000 and m != claim.value.number
    }
    if _any_rounds((a * m for a in others for m in multipliers), claim.value):
        return True
    return _run_sums(index, claims, known, backward_only=True)


def _holds(corpus: _Corpus, value: _Value) -> bool:
    """*corpus* holds *value*; a scaled figure ("$14.5M") only to its precision.

    The corpus's "0.45 may be shown as 45%" reading is not applied to a scaled
    figure, where it would let "$30,000" source "$3M".
    """
    if value.decimals >= 0:
        return corpus.holds(value)
    return any(_rounds_to(n, value) for n in corpus.numbers)


#: An amount this large matches a spec's number only by design, not chance.
_BARE_AMOUNT_MIN = 1e6


def _bare_amount_holds(corpus: _Corpus, value: _Value) -> bool:
    """A source states *value* as a bare number: "293,500,000,000" for "$293.5B"."""
    return abs(value.number) >= _BARE_AMOUNT_MIN and any(
        _rounds_to(n, value) for n in corpus.numbers
    )


def _unsupported(
    blocks: Sequence[Block],
    general: _Corpus,
    money: _Corpus,
    request_numbers: Sequence[float],
    data_numbers: Sequence[float] = (),
) -> List[_Claim]:
    """Figures in *blocks* with no source, minus totals computed from them.

    *data_numbers* — numbers from data rows the tools returned — may be the
    operands of a percentage the file shows without them.
    """
    if _BLANKET.search(blocks_text(blocks)):
        return []
    claims, rows, plain = _scan(blocks)
    if not claims:
        return []
    known = [
        c.marked
        or _holds(money if c.kind == "money" else general, c.value)
        or (c.alt is not None and _holds(money, c.alt))
        or (c.kind == "money" and _bare_amount_holds(general, c.value))
        for c in claims
    ]
    plain_known = list(
        dict.fromkeys(
            [v.number for v in (_value(t) for t in plain) if general.holds(v)]
            + list(data_numbers)
        )
    )[:40]
    for _ in range(4):
        changed = False
        for index in range(len(claims)):
            if not known[index] and _follows(
                index, claims, known, rows, request_numbers, plain_known
            ):
                known[index] = changed = True
        if not changed:
            break
    missing = [k for k, ok in enumerate(known) if not ok]
    roots = [k for k in missing if not _downstream(k, claims, rows, request_numbers)]
    return [claims[k] for k in (roots or missing)]


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


_GLUED_UNIT = re.compile(r"(\d)([A-Za-zµμ°])")

#: Dates and clock times in the request are not quantities to multiply by.
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)"
)


def _mentioned(claim: _Claim, answer: str) -> bool:
    named = {_number(n) for n in _ANY_NUMBER.findall(_spelled_out(answer))}
    return claim.value.number in named


def file_findings(
    files: Mapping[str, Sequence[Block]],
    records: Sequence[Dict[str, Any]],
    *,
    query: str = "",
    context: str = "",
    inputs: Sequence[str] = (),
    input_names: Sequence[str] = (),
    answer: str = "",
) -> List[Finding]:
    """Figures in *files* (path → blocks) that nothing this turn supports.

    Evidence is the tool results in *records*, the text of *inputs* (files
    the tools were pointed at), the request (*query*) and earlier turns
    (*context*). A script's output sources an amount only when it computed
    from files read this turn (*input_names* are the files *inputs* hold).
    An answer that says its figures are unverified and names a
    value has already told the user about it.
    """
    if not files:
        return []
    written = {_basename(path) for path in files}
    read_names = _read_names(records, written) | (
        {_basename(n) for n in input_names} - written
    )
    input_texts = [_spelled_out(t) for t in inputs]
    copied = _copied_numbers(
        _source_records(records, written),
        [_spelled_out(query or "")] + input_texts,
    )
    found = [
        (_spelled_out(t), c)
        for t, c in _evidence_texts(records, written, read_names, copied)
    ]
    texts = [t for t, _ in found] + input_texts
    query = _spelled_out(query or "")
    context = _spelled_out(context or "")
    # "27W", "8GB": a unit glued to its number still shows the number.
    general = _Corpus(_GLUED_UNIT.sub(r"\1 \2", "\n".join([context, query] + texts)))
    # An amount a script computed from this turn's files sources a money
    # figure; one it echoed from them, or a "60 Hz", still doesn't.
    seen = {
        _number(n)
        for t in [context] + [t for t, c in found if not c] + input_texts
        for n in _ANY_NUMBER.findall(t)
    }
    computed = [
        _ANY_NUMBER.sub(
            lambda m: " " if _number(m.group(0)) in seen else m.group(0),
            _UNIT.sub(" ", t),
        )
        for t in [t for t, c in found if c]
        + _script_written(files, records, read_names, written, copied)
    ]
    money = _Corpus(
        "\n".join([query] + [_money_evidence(t) for t in [context] + texts] + computed)
    )
    request_numbers = [
        _number(n) for n in _ANY_NUMBER.findall(_TIMESTAMP.sub(" ", query or ""))
    ][:20]
    data_numbers = list(
        dict.fromkeys(
            _number(n)
            for t in texts
            for line in t.splitlines()
            if len(line) <= 400 and _TABULAR.search(line)
            for n in re.split(r"[,;\t|]", line)
            if _PURE_NUMBER.match(n)
        )
    )[:20]
    admitted = admits_unverified(answer or "")
    findings: List[Finding] = []
    for path, blocks in files.items():
        missing = _unsupported(blocks, general, money, request_numbers, data_numbers)
        if admitted:
            missing = [c for c in missing if not _mentioned(c, answer)]
        if not missing:
            continue
        shown = list(dict.fromkeys(c.shown for c in missing))
        listed = ", ".join(shown[:_SHOWN]) + (
            f" and {len(shown) - _SHOWN} more" if len(shown) > _SHOWN else ""
        )
        name = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
        findings.append(
            Finding(
                FILE,
                f"`{name}` has figures no tool result this turn supports: {listed}.",
                f"`{name}` has figures I found no source for this turn: {listed}",
            )
        )
    return findings
