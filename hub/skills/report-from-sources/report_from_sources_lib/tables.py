# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Stream rows out of .xlsx, .csv/.tsv and .json files, and read what a sheet holds.

Every reader yields ``(row_number, values)`` with 1-based row numbers as Excel
shows them, so a value can always be cited back to its cell. Nothing is loaded
whole: workbooks open read-only, CSV is read line by line, and layout
detection looks at a bounded sample of the top of each sheet. Every file
opens through ``gaia.skills.context.open_read``, which applies the agent's
allowed folders, so these readers run inside a skill tool call.

Only time series are read: a table needs period labels across a header row or
down a column. A table by category, or row-level records, is reported as such.

:func:`analyze_layout` decides how a sheet is laid out:

- ``wide``: periods across a header row (possibly built from two rows, e.g.
  "2025" merged over "Q1 Q2 Q3 Q4"), one series per row, labels on the left;
- ``columns``: periods down a column, one series per column;
- ``stacked``: periods down a column plus a text column naming the series
  (long/tidy format), values in a numeric column.
"""

from __future__ import annotations

import csv
import json
import re
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from gaia.logger import get_logger
from gaia.skills.context import open_read

from .periods import (
    Period,
    clean_label,
    dominant_freq,
    is_bare_year,
    parse_axis,
    parse_period,
    parse_yearless,
)

log = get_logger(__name__)

SPREADSHEET_SUFFIXES = (".xlsx", ".xlsm")
#: Formats the shipped libraries cannot read (no xlrd/odfpy/pyxlsb).
UNREADABLE_SPREADSHEET_SUFFIXES = (".xls", ".xlsb", ".ods")
DELIMITED_SUFFIXES = (".csv", ".tsv", ".tab")
JSON_SUFFIXES = (".json", ".jsonl", ".ndjson")
DATA_SUFFIXES = SPREADSHEET_SUFFIXES + DELIMITED_SUFFIXES + JSON_SUFFIXES
NARRATIVE_SUFFIXES = (
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".md",
    ".txt",
    ".rtf",
    ".html",
    ".htm",
)

#: Rows read from the top of a sheet to decide its layout.
SAMPLE_ROWS = 200
#: Header rows are looked for in this many top rows.
HEADER_SEARCH_ROWS = 40
#: Widest row kept from a sheet; wider rows are cut and reported.
MAX_COLUMNS = 5000
#: JSON is parsed whole, so it is capped.
MAX_JSON_BYTES = 64 * 1024 * 1024


class DataFileError(ValueError):
    """A data file that cannot be read, with what to do about it."""


# --------------------------------------------------------------------------- #
# Cell values
# --------------------------------------------------------------------------- #

_BLANK_TEXT = {
    "",
    "-",
    "--",
    "—",
    "–",
    "..",
    "...",
    "…",
    "n/a",
    "na",
    "n.a.",
    "nan",
    "none",
    "null",
    "x",
    "(x)",
    "(na)",
    "(d)",
    "(s)",
    "(z)",
    "*",
    "**",
    "nm",
    "n.m.",
    ":",
}
_NUMBER_RE = re.compile(
    r"^(?P<open>\()?\s*(?P<sign>[-+−])?\s*(?P<cur>[$€£¥])?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?:[eE](?P<exp>[-+]?\d+))?\s*(?P<pct>%)?\s*(?P<close>\))?"
    r"(?P<marker>\s*(?:\((?:[A-Za-z]{1,3}|\d{1,2})\)|\[[A-Za-z0-9]{1,3}\]|"
    r"[*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰]+|[A-Za-z]{1,2}))?$"
)
_SCALE_SUFFIX = {"k": 1e3, "m": 1e6, "mn": 1e6, "mm": 1e6, "bn": 1e9, "b": 1e9}


@dataclass(frozen=True)
class CellNumber:
    """A cell read as a number: ``value`` is None for blanks and non-numbers."""

    value: Optional[float]
    raw: Any = None
    marker: Optional[str] = None
    percent: bool = False
    blank: bool = False
    #: "123.4bn" — the suffix is reported, the value is not rescaled.
    scale_suffix: Optional[str] = None


def read_number(value: Any) -> CellNumber:
    """Read a cell as a number, keeping any footnote marker it carries.

    "123.4(a)" -> 123.4 marker "(a)"; "(12.5)" -> -12.5 (accounting negative);
    "1,234" -> 1234; "4.5%" -> 4.5 percent; "-", "n/a", "..", "" -> blank.
    Numeric cells come back unchanged, so values match the source exactly.
    """
    if value is None:
        return CellNumber(None, value, blank=True)
    if isinstance(value, bool):
        return CellNumber(None, value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return CellNumber(None, value, blank=True)
        return CellNumber(value, value)
    if not isinstance(value, str):
        return CellNumber(None, value)
    text = value.replace(" ", " ").strip()
    if text.lower() in _BLANK_TEXT:
        return CellNumber(None, value, marker=text or None, blank=True)
    match = _NUMBER_RE.match(text)
    if not match:
        return CellNumber(None, value)
    g = match.groupdict()
    if bool(g["open"]) != bool(g["close"]):
        # "(12.5" or "12.5)" — a broken accounting negative is not a number.
        return CellNumber(None, value)
    marker = (g["marker"] or "").strip() or None
    scale = None
    if marker and marker.lower() in _SCALE_SUFFIX:
        scale, marker = marker, None
    elif marker and marker.isalpha() and len(marker) == 2:
        return CellNumber(None, value)  # "12ab" is text, not a flagged number
    digits = g["num"].replace(",", "")
    number: float
    if g["exp"] is not None:
        number = float(f"{digits}e{g['exp']}")
    elif "." in digits:
        number = float(digits)
    else:
        number = int(digits)
    negative = bool(g["open"]) or g["sign"] in ("-", "−")
    if negative:
        number = -number
    return CellNumber(number, value, marker, bool(g["pct"]), scale_suffix=scale)


def is_text(value: Any) -> bool:
    """A non-empty string that is not a number."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and read_number(value).value is None
    )


def cell_ref(row: int, col: int) -> str:
    """A1-style reference for a 1-based row and 0-based column."""
    letters = ""
    n = col + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def column_letter(col: int) -> str:
    return re.sub(r"\d+", "", cell_ref(1, col))


# --------------------------------------------------------------------------- #
# Readers
# --------------------------------------------------------------------------- #


class Table:
    """One sheet of a workbook, or the single table of a CSV/JSON file."""

    def __init__(self, path: Path, name: str, kind: str):
        self.path = path
        self.name = name
        self.kind = kind  # "xlsx" | "csv" | "json"
        self.merged: List[Tuple[int, int, int, int]] = []  # r1, c1, r2, c2
        self.read_errors: List[str] = []
        #: How the file's text was read when that was a choice (CSV conventions).
        self.format_notes: List[str] = []
        self.truncated_columns = False

    def rows(
        self, limit: Optional[int] = None
    ) -> Iterator[Tuple[int, Tuple[Any, ...]]]:
        raise NotImplementedError

    def sample(self, limit: int = SAMPLE_ROWS) -> List[List[Any]]:
        """The top *limit* rows as a dense grid (row i is sheet row i+1)."""
        grid: List[List[Any]] = []
        for number, values in self.rows(limit):
            while len(grid) < number - 1:
                grid.append([])
            grid.append(list(values))
        width = max((len(r) for r in grid), default=0)
        return [r + [None] * (width - len(r)) for r in grid]

    def _cut(self, values: Sequence[Any]) -> Tuple[Any, ...]:
        if len(values) > MAX_COLUMNS:
            self.truncated_columns = True
            values = values[:MAX_COLUMNS]
        return tuple(values)


class _XlsxTable(Table):
    def __init__(self, path: Path, ws: Any):
        super().__init__(path, ws.title, "xlsx")
        self._ws = ws

    def rows(
        self, limit: Optional[int] = None
    ) -> Iterator[Tuple[int, Tuple[Any, ...]]]:
        # Row numbers come from the cells, not a counter: openpyxl skips rows
        # that are missing from the XML, and a counter would cite wrong cells.
        for row in self._ws.iter_rows():
            number = None
            values = []
            for cell in row:
                if number is None and getattr(cell, "row", None) is not None:
                    number = cell.row
                values.append(getattr(cell, "value", None))
            if number is None:
                continue
            if limit is not None and number > limit:
                return
            yield number, self._cut(values)


_MERGE_RE = re.compile(
    rb"<(?:\w+:)?mergeCell\s+ref=\"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?\""
)


def _col_index(letters: bytes) -> int:
    n = 0
    for ch in letters.decode():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _merged_ranges(
    zf: zipfile.ZipFile, member: str, cap: int = 20000
) -> List[Tuple[int, int, int, int]]:
    """Merged ranges of one sheet, scanned from its XML in bounded chunks."""
    out: List[Tuple[int, int, int, int]] = []
    tail = b""
    try:
        with zf.open(member) as stream:
            while len(out) < cap:
                chunk = stream.read(1 << 20)
                if not chunk:
                    break
                data = tail + chunk
                last_end = 0
                for m in _MERGE_RE.finditer(data):
                    c1, r1 = _col_index(m.group(1)), int(m.group(2))
                    c2 = _col_index(m.group(3)) if m.group(3) else c1
                    r2 = int(m.group(4)) if m.group(4) else r1
                    out.append((r1, c1, r2, c2))
                    last_end = m.end()
                tail = data[max(last_end, len(data) - 200) :]
    except KeyError:
        return []
    return out[:cap]


class _CsvTable(Table):
    def __init__(self, path: Path):
        super().__init__(path, path.name, "csv")
        self.encoding, self.delimiter, self.decimal_comma, note = _sniff_delimited(path)
        if note:
            self.format_notes.append(f"{path.name}: {note}")

    def rows(
        self, limit: Optional[int] = None
    ) -> Iterator[Tuple[int, Tuple[Any, ...]]]:
        with open_read(
            path_str(self.path), encoding=self.encoding, errors="replace", newline=""
        ) as f:
            reader = csv.reader(f, delimiter=self.delimiter)
            number = 0
            while True:
                try:
                    values = next(reader)
                except StopIteration:
                    return
                except csv.Error as e:
                    self.read_errors.append(f"line {reader.line_num}: {e}")
                    if len(self.read_errors) > 20:
                        self.read_errors.append(
                            "stopped reading after 20 malformed lines"
                        )
                        return
                    continue
                number += 1
                if limit is not None and number > limit:
                    return
                if self.decimal_comma:
                    values = [_decimal_comma_to_point(v) for v in values]
                yield number, self._cut([v if v.strip() else None for v in values])


def path_str(path: Path) -> str:
    return str(path)


_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)
_NUM_LEAD = r"^[(\-+\u2212\u20ac$\u00a3\u00a5\s]*"
_NUM_TAIL = r"\s*%?\s*\)?$"
#: Numbers only a decimal comma explains: "1.234,56", "12,5", "0,125".
_COMMA_DECIMAL_RE = re.compile(
    _NUM_LEAD + r"(?:\d{1,3}(?:\.\d{3})+,\d+|0,\d+|\d+,\d{1,2}|\d+,\d{4,})" + _NUM_TAIL
)
#: Numbers only a decimal point explains: "1,234.56", "12.5", "0.125".
_POINT_DECIMAL_RE = re.compile(
    _NUM_LEAD
    + r"(?:\d{1,3}(?:,\d{3})+\.\d+|0\.\d+|\d+\.\d{1,2}|\d+\.\d{4,})"
    + _NUM_TAIL
)
#: "1.234" or "1,234": thousands under one convention, a decimal under the other.
_EITHER_RE = re.compile(_NUM_LEAD + r"[1-9]\d{0,2}[.,]\d{3}" + _NUM_TAIL)
_COMMA_NUMBER_RE = re.compile(
    r"^(?P<pre>[(\-+\u2212\u20ac$\u00a3\u00a5\s]*)"
    r"(?P<int>\d{1,3}(?:[.\u00a0\u202f ]\d{3})+|\d+)"
    r"(?:,(?P<dec>\d+))?(?P<post>[^\d.,]{0,8})$"
)
_DELIMITER_NAMES = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}


def _sniff_encoding(head: bytes) -> str:
    """The encoding of a delimited file, from its BOM or its byte pattern."""
    for bom, name in _BOMS:
        if head.startswith(bom):
            return name
    # UTF-16 without a BOM: every other byte of mostly-ASCII text is NUL.
    sample = head[:4096]
    if len(sample) >= 8:
        even = sample[0::2].count(0) / len(sample[0::2])
        odd = sample[1::2].count(0) / len(sample[1::2])
        if odd > 0.4 and even < 0.1:
            return "utf-16-le"
        if even > 0.4 and odd < 0.1:
            return "utf-16-be"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # A multi-byte character cut by the 64 KB boundary is not an encoding problem.
        if e.start < len(head) - 4:
            return "cp1252"
    return "utf-8-sig"


def _decimal_comma_to_point(text: str) -> str:
    """ "1.234,56" -> "1234.56" for a file that writes decimals with a comma."""
    match = _COMMA_NUMBER_RE.match(text.strip())
    if not match:
        return text
    whole = re.sub(r"[.\u00a0\u202f ]", "", match.group("int"))
    dec = f".{match.group('dec')}" if match.group("dec") else ""
    return f"{match.group('pre')}{whole}{dec}{match.group('post')}"


def _sniff_delimited(path: Path) -> Tuple[str, str, bool, Optional[str]]:
    """Encoding, delimiter and decimal convention from the first 64 KB.

    Returns:
        ``(encoding, delimiter, decimal_comma, note)``; the note says which
        convention was assumed when the file left a choice.
    """
    with open_read(path, "rb") as f:
        head = f.read(65536)
    encoding = _sniff_encoding(head)
    text = head.decode(encoding, errors="replace")
    lines = [ln for ln in text.splitlines()[:50] if ln.strip()]
    best, best_score = ",", -1.0
    if path.suffix.lower() in (".tsv", ".tab"):
        best = "\t"
    else:
        for delim in (",", "\t", ";", "|"):
            counts = [
                next(csv.reader([ln], delimiter=delim), []).__len__() for ln in lines
            ]
            if not counts or max(counts) < 2:
                continue
            common = max(set(counts), key=counts.count)
            score = counts.count(common) / len(counts) * min(common, 50)
            if score > best_score:
                best, best_score = delim, score
    cells = [
        cell.strip()
        for ln in lines
        for cell in next(csv.reader([ln], delimiter=best), [])
    ]
    comma = sum(1 for c in cells if _COMMA_DECIMAL_RE.match(c))
    point = sum(1 for c in cells if _POINT_DECIMAL_RE.match(c))
    either = next((c for c in cells if _EITHER_RE.match(c)), None)
    decimal_comma = comma > point
    name = _DELIMITER_NAMES[best]
    note = None
    if comma and point:
        note = (
            f"{name}-delimited; numbers mix decimal commas and decimal points, read "
            f"with a decimal {'comma' if decimal_comma else 'point'}. Check the values."
        )
    elif decimal_comma:
        note = (
            f"{name}-delimited with decimal commas: 1.234,56 was read as 1234.56 "
            "and 12,5 as 12.5"
        )
    elif either and not point and best != ",":
        note = (
            f"{name}-delimited; {either!r} could use either decimal convention and "
            "no value in the file settles it. Read with a decimal point "
            f"({either} = {read_number(either).value}). Check with the user."
        )
    return encoding, best, decimal_comma, note


class _JsonTable(Table):
    """Records (list of objects, or JSON Lines) or a {series: {period: value}} map."""

    def __init__(self, path: Path):
        super().__init__(path, path.name, "json")
        size = path.stat().st_size
        self._lines = path.suffix.lower() in (".jsonl", ".ndjson")
        if not self._lines and size > MAX_JSON_BYTES:
            raise DataFileError(
                f"{path.name} is {size / 1e6:.0f} MB of JSON, over the "
                f"{MAX_JSON_BYTES // (1024 * 1024)} MB that is parsed whole. Convert "
                "it to CSV or JSON Lines with run_python and read that."
            )
        self._grid: Optional[List[List[Any]]] = None

    def _build(self) -> List[List[Any]]:
        if self._grid is not None:
            return self._grid
        with open_read(self.path, encoding="utf-8-sig", errors="replace") as f:
            data = json.load(f)
        records = _json_records(data)
        if records is None:
            raise DataFileError(
                f"{self.path.name} is JSON but not a table: expected a list of "
                "objects, an object holding one ({'data': [...]}) or a "
                "{series: {period: value}} map."
            )
        self._grid = records
        return records

    def rows(
        self, limit: Optional[int] = None
    ) -> Iterator[Tuple[int, Tuple[Any, ...]]]:
        if self._lines:
            yield from self._json_lines(limit)
            return
        for i, row in enumerate(self._build(), start=1):
            if limit is not None and i > limit:
                return
            yield i, self._cut(row)

    def _json_lines(
        self, limit: Optional[int]
    ) -> Iterator[Tuple[int, Tuple[Any, ...]]]:
        keys: List[str] = []
        pending: List[Dict[str, Any]] = []
        number = 0
        with open_read(self.path, encoding="utf-8-sig", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    self.read_errors.append(f"line {line_no}: {e.msg}")
                    continue
                if not isinstance(obj, dict):
                    self.read_errors.append(f"line {line_no}: not an object")
                    continue
                if not keys:
                    pending.append(obj)
                    if len(pending) < 200:
                        continue
                    keys = _union_keys(pending)
                    number += 1
                    yield number, tuple(keys)
                    for rec in pending:
                        number += 1
                        if limit is not None and number > limit:
                            return
                        yield number, tuple(_scalar(rec.get(k)) for k in keys)
                    pending = []
                    continue
                number += 1
                if limit is not None and number > limit:
                    return
                yield number, tuple(_scalar(obj.get(k)) for k in keys)
        if not keys and pending:
            keys = _union_keys(pending)
            number += 1
            yield number, tuple(keys)
            for rec in pending:
                number += 1
                if limit is not None and number > limit:
                    return
                yield number, tuple(_scalar(rec.get(k)) for k in keys)


def _union_keys(records: Sequence[Dict[str, Any]]) -> List[str]:
    keys: List[str] = []
    for rec in records:
        for k in rec:
            if k not in keys:
                keys.append(k)
    return keys


def _scalar(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value)[:200]
    return value


def _json_records(data: Any) -> Optional[List[List[Any]]]:
    if isinstance(data, dict):
        for key in (
            "data",
            "records",
            "rows",
            "observations",
            "values",
            "items",
            "results",
        ):
            if isinstance(data.get(key), list):
                return _json_records(data[key])
        if data and all(isinstance(v, dict) for v in data.values()):
            periods: List[str] = []
            for inner in data.values():
                for p in inner:
                    if p not in periods:
                        periods.append(p)
            grid = [["series"] + periods]
            for name, inner in data.items():
                grid.append([name] + [_scalar(inner.get(p)) for p in periods])
            return grid
        return None
    if (
        isinstance(data, list)
        and data
        and all(isinstance(r, dict) for r in data[:1000])
    ):
        keys = _union_keys(data[:1000])
        return [keys] + [
            [_scalar(r.get(k)) for k in keys] for r in data if isinstance(r, dict)
        ]
    if (
        isinstance(data, list)
        and data
        and all(isinstance(r, list) for r in data[:1000])
    ):
        return [list(r) for r in data]
    return None


@contextmanager
def open_tables(path: Path) -> Iterator[List[Table]]:
    """Every table in *path*, read-only. Closes the workbook on exit.

    Raises:
        DataFileError: unsupported or unreadable file, with the fix.
    """
    suffix = path.suffix.lower()
    if suffix in UNREADABLE_SPREADSHEET_SUFFIXES:
        raise DataFileError(
            f"{path.name}: {suffix} workbooks cannot be read here (only .xlsx/.xlsm, "
            ".csv/.tsv and .json). Ask the user to save it as .xlsx, or export the "
            "sheet to CSV."
        )
    if suffix in SPREADSHEET_SUFFIXES:
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise DataFileError(
                "openpyxl is not installed, so .xlsx files cannot be read. Ask the "
                "user to run `gaia init --profile chat`, which installs it."
            ) from e
        with ExitStack() as stack:
            try:
                wb = load_workbook(
                    stack.enter_context(open_read(path, "rb")),
                    read_only=True,
                    data_only=True,
                )
                stack.callback(wb.close)
                zf = stack.enter_context(
                    zipfile.ZipFile(stack.enter_context(open_read(path, "rb")))
                )
            except (zipfile.BadZipFile, KeyError, OSError, ValueError) as e:
                raise DataFileError(
                    f"{path.name} is not a readable .xlsx workbook: {e}"
                ) from e
            tables: List[Table] = []
            for ws in wb.worksheets:
                table = _XlsxTable(path, ws)
                member = getattr(ws, "_worksheet_path", None)
                if member:
                    table.merged = _merged_ranges(zf, member.lstrip("/"))
                tables.append(table)
            yield tables
        return
    if suffix in DELIMITED_SUFFIXES:
        yield [_CsvTable(path)]
        return
    if suffix in JSON_SUFFIXES:
        yield [_JsonTable(path)]
        return
    raise DataFileError(
        f"{path.name}: '{suffix or 'no extension'}' is not a data file this tool reads "
        f"({', '.join(DATA_SUFFIXES)})."
    )


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

_SUBPERIOD_RE = re.compile(
    r"^(?:q[1-4]|[1-4]q|h[12]|[12]h|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?$",
    re.I,
)
_UNITS_CELL_RE = re.compile(
    r"^\(?\s*(?:in\s+|units?\s*:\s*)?(?:(?:us)?\$|usd|eur|€|£|gbp|¥|jpy|chf|cad|aud|inr|cny)?\s*"
    r"(?:thousands?|millions?|billions?|trillions?|mn|bn|mm|m|k|'?000s?)"
    r"(?:\s+of\s+[a-z ]+)?\s*\)?$|"
    r"^\(?\s*(?:%|percent(?:age)?(?:\s+points?)?|per\s*cent|pp|bps|basis points|index"
    r"(?:\s*\(.*\))?|ratio|rate|number|count|persons|people|units?|tonnes|kg|hours|days|"
    r"(?:us)?\$|usd|eur|€|£)\s*(?:[a-z ,=0-9]*)\)?$",
    re.I,
)
_UNITS_PHRASE_RE = re.compile(
    r"(?:(?:us)?\$|usd|eur|€|£|gbp|¥)\s*(?:thousands?|millions?|billions?|trillions?|mn|bn|m|k)\b|"
    r"\b(?:in\s+)?(?:thousands?|millions?|billions?|trillions?)\s+of\s+(?:[a-z]+\s+)?"
    r"(?:dollars|euros|pounds|yen|units|people|persons|tonnes)\b|"
    r"\bpercent(?:age)?(?:\s+points?)?\b|\bper\s*cent\b|\bindex\b[^,;]*=\s*100|%",
    re.I,
)
_UNITS_HEADER_RE = re.compile(
    r"^(?:units?|unit of measure(?:ment)?|measure|uom|scale)$", re.I
)
_FOOTNOTE_RE = re.compile(
    r"^\s*(?:\((?P<a>[a-z]{1,2}|\d{1,2})\)|\[(?P<b>[a-z0-9]{1,2})\]|(?P<c>[*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰]+)|"
    r"(?P<d>[a-z]|\d{1,2})[.)])\s+(?P<text>\S.*)$",
    re.I,
)
_NOTE_LEAD_RE = re.compile(
    r"^\s*(?:notes?|source|sources|footnotes?|memo|nb)\b\s*[:.\-]?", re.I
)


def units_scale(units: Optional[str]) -> Optional[float]:
    """The multiplier a units string names: "$ millions" -> 1e6, "%" -> None."""
    if not units:
        return None
    low = units.lower()
    for words, factor in (
        (("trillion",), 1e12),
        (("billion", "bn"), 1e9),
        (("million", " mn", "$m", "mm", "usd m", "eur m", "£m", "€m"), 1e6),
        (("thousand", "'000", "000s", " k", "$k"), 1e3),
    ):
        if any(w in f" {low}" for w in words):
            return factor
    return None


def units_from_text(text: Any) -> Optional[str]:
    """A units phrase inside a title, header or label, as written."""
    if not isinstance(text, str):
        return None
    paren = re.findall(r"\(([^()]{1,40})\)", text)
    for inner in paren:
        if _UNITS_CELL_RE.match(inner.strip()) or _UNITS_PHRASE_RE.search(inner):
            return inner.strip()
    match = _UNITS_PHRASE_RE.search(text)
    return match.group(0).strip() if match else None


def footnote_key(marker: Optional[str]) -> Optional[str]:
    if not marker:
        return None
    return marker.strip().strip("()[]").lower() or None


@dataclass
class Layout:
    """What :func:`analyze_layout` found in one table."""

    kind: str = "unknown"  # wide | columns | stacked | unknown
    reason: str = ""
    header_rows: List[int] = field(default_factory=list)
    title_lines: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    footnotes: Dict[str, str] = field(default_factory=dict)
    units: Optional[str] = None
    units_row: Optional[int] = None
    units_col: Optional[int] = None
    merged_header_cells: int = 0
    axis_notes: List[str] = field(default_factory=list)
    #: Why a row or column that looks like periods was not read as one.
    rejected: List[str] = field(default_factory=list)
    freq: Optional[str] = None
    first_data_row: int = 0
    # wide
    period_row: Optional[int] = None
    period_cols: Dict[int, Period] = field(default_factory=dict)
    other_period_cols: Dict[int, Period] = field(default_factory=dict)
    header_text: Dict[int, str] = field(default_factory=dict)
    #: Text above or below a period column that is not the period itself
    #: ("Actual", "Budget"): what tells two columns of one period apart.
    banner_text: Dict[int, str] = field(default_factory=dict)
    label_cols: List[int] = field(default_factory=list)
    blank_columns: List[int] = field(default_factory=list)
    # columns / stacked
    period_col: Optional[int] = None
    column_headers: Dict[int, str] = field(default_factory=dict)
    numeric_cols: List[int] = field(default_factory=list)
    dimension_cols: List[int] = field(default_factory=list)


def _fill_merged(
    grid: List[List[Any]], merged: Sequence[Tuple[int, int, int, int]]
) -> int:
    """Copy each merged range's top-left value across the range, in place.

    Returns how many merged ranges touch the sampled rows.
    """
    touched = 0
    height = len(grid)
    for r1, c1, r2, c2 in merged:
        if r1 > height:
            continue
        top = grid[r1 - 1][c1] if c1 < len(grid[r1 - 1]) else None
        touched += 1
        for r in range(r1, min(r2, height) + 1):
            row = grid[r - 1]
            for c in range(c1, min(c2 + 1, len(row))):
                if row[c] is None:
                    row[c] = top
    return touched


def _row_text(row: Sequence[Any]) -> str:
    return " ".join(str(v).strip() for v in row if v is not None and str(v).strip())


def _year_of(value: Any) -> Optional[str]:
    p = parse_period(value, bare_year_numbers=True)
    if p is not None and p.freq in ("A", "FY"):
        return p.label
    return None


#: A column of bare years is a period column only under a header saying so.
_PERIOD_HEADER_RE = re.compile(
    r"(?<![a-z])(?:years?|yrs?|fy|fiscal|periods?|dates?|time|as of|vintage)(?![a-z])",
    re.I,
)
YEAR_UNKNOWN_NOTE = (
    "the header names months or quarters with no year, so periods are labelled "
    "as written (Jan, Q1); the sheet does not state the year. Take it from the "
    "title or file name, or ask the user, and say so"
)


def _has_text(value: Any) -> bool:
    return is_text(value) and not read_number(value).blank


def _bare_years_are_data(
    grid: List[List[Any]], r: int, cols: Sequence[int]
) -> Optional[str]:
    """Why the bare years in row *r* are a data row, or None if they head a table.

    "Salaries 1950 2000 2050" under "Category | Jan | Feb | Mar" is a data row.
    Bare years are a header only when the row's other cells are text or blank
    and no complete text header row sits above them in the same block.
    """
    row = grid[r]
    years = ", ".join(str(row[c]) for c in list(cols)[:4])
    if any(
        read_number(v).value is not None for c, v in enumerate(row) if c not in cols
    ):
        return (
            f"row {r + 1} holds numbers that could be years ({years}) next to "
            "other numbers, so it was read as data"
        )
    first = min(cols)
    for rr in range(r - 1, -1, -1):
        above = grid[rr]
        if not any(v is not None and str(v).strip() for v in above):
            break  # a blank row ends the block this row belongs to
        over = [str(above[c]).strip() for c in cols if _has_text(above[c])]
        # One repeated text is a merged title or banner, not a header.
        if len(over) * 2 < len(cols) or len(set(over)) < 2:
            continue
        if any(_has_text(v) for v in above[:first]):
            return (
                f"row {r + 1} holds numbers that could be years ({years}) under "
                f"the text header in row {rr + 1}, so it was read as data"
            )
    return None


def _wide_candidate(
    grid: List[List[Any]], r: int, rejected: Optional[List[str]] = None
) -> Tuple[Dict[int, Period], List[str], int]:
    """Period columns if row *r* is the period header row.

    Returns the period per column, axis notes, and the topmost row (0-based)
    that contributed a year to a composite "2025" over "Q1" header. Months or
    quarters with no year anywhere above them come back with ``year`` 0.
    """
    row = grid[r]
    composed: List[Any] = []
    yearless: Dict[int, Period] = {}
    top = r
    for c, value in enumerate(row):
        text = clean_label(value) if isinstance(value, str) else value
        if isinstance(text, str) and _SUBPERIOD_RE.match(text):
            year = None
            for rr in range(r - 1, max(-1, r - 4), -1):
                # Forward-fill along the row above: "2025" written once, then blanks.
                for cc in range(c, -1, -1):
                    above = grid[rr][cc]
                    if above is None or (isinstance(above, str) and not above.strip()):
                        continue
                    year = _year_of(above)
                    break
                if year:
                    top = min(top, rr)
                    break
            composed.append(f"{text} {year}" if year else None)
            if not year:
                found = parse_yearless(text)
                if found is not None:
                    yearless[c] = found
        else:
            composed.append(value)
    bare = [c for c, v in enumerate(composed) if is_bare_year(v)]
    if len(bare) >= 2:
        why = _bare_years_are_data(grid, r, bare)
        if why:
            if rejected is not None:
                rejected.append(why)
            for c in bare:
                composed[c] = None
    periods, notes = parse_axis(composed, bare_year_numbers=True)
    cols = {c: p for c, p in enumerate(periods) if p is not None}
    if not cols and len(yearless) >= 2:
        return yearless, notes + [YEAR_UNKNOWN_NOTE], r
    return cols, notes, top


def _numeric_share(values: Sequence[Any]) -> float:
    filled = [
        v
        for v in values
        if v is not None and not (isinstance(v, str) and not v.strip())
    ]
    if not filled:
        return 0.0
    return sum(1 for v in filled if read_number(v).value is not None) / len(filled)


def analyze_layout(table: Table, grid: Optional[List[List[Any]]] = None) -> Layout:
    """Decide how *table* is laid out from a sample of its top rows."""
    if grid is None:
        grid = table.sample()
    layout = Layout()
    if not grid or not any(any(v is not None for v in row) for row in grid):
        layout.reason = "empty sheet"
        return layout
    raw = [list(r) for r in grid]
    layout.merged_header_cells = _fill_merged(
        grid, [m for m in table.merged if m[0] <= HEADER_SEARCH_ROWS]
    )
    width = max(len(r) for r in grid)

    # -- wide: the header row with the most distinct periods ----------------
    wide_best: Tuple[int, int, Dict[int, Period], List[str], int] = (0, -1, {}, [], -1)
    wide_dated = False
    for r in range(min(len(grid), HEADER_SEARCH_ROWS)):
        cols, notes, top = _wide_candidate(grid, r, layout.rejected)
        if len(cols) < 2:
            continue
        # The periods must head numbers: something below them parses as a value.
        below = [
            grid[rr][c] for rr in range(r + 1, min(len(grid), r + 30)) for c in cols
        ]
        if _numeric_share(below) < 0.3:
            continue
        distinct = len({p.label for p in cols.values()})
        # A header with years beats one that names months only.
        dated = all(p.year for p in cols.values())
        if (dated, distinct) > (wide_dated, wide_best[0]):
            wide_best = (distinct, r, cols, notes, top)
            wide_dated = dated

    # -- periods down a column -----------------------------------------------
    col_best: Tuple[int, int, List[Optional[Period]], List[str]] = (0, -1, [], [])
    for c in range(min(width, 60)):
        values = [row[c] if c < len(row) else None for row in grid]
        header_hint = any(
            isinstance(v, str) and _PERIOD_HEADER_RE.search(v) for v in values[:10]
        )
        filled = [v for v in values if v is not None]
        # Order ids 2000-2019 are not years: bare years need a header saying so.
        bare = [v for v in values if is_bare_year(v)]
        axis = values
        if bare and not header_hint:
            axis = [None if is_bare_year(v) else v for v in values]
            if len(bare) >= 3 and len(bare) >= 0.5 * len(filled):
                head = next((str(v) for v in values[:10] if _has_text(v)), "no header")
                layout.rejected.append(
                    f"column {column_letter(c)} ({head!r}) holds numbers that could "
                    "be years, but its header does not say so (Year, FY, Period, "
                    "Date), so it was read as data"
                )
        periods, notes = parse_axis(axis, bare_year_numbers=header_hint)
        hits = [p for p in periods if p is not None]
        if len(hits) < 3 or len(hits) < 0.5 * len(filled):
            continue
        distinct = len({p.label for p in hits})
        if distinct > col_best[0]:
            col_best = (distinct, c, periods, notes)

    if wide_best[0] < 2 and col_best[0] < 3:
        layout.reason = "no period labels found in a header row or a column"
        if layout.rejected:
            layout.reason += "; " + "; ".join(dict.fromkeys(layout.rejected[:2]))
        _collect_notes(layout, raw, 0, len(raw))
        return layout
    if wide_best[0] >= col_best[0]:
        _finish_wide(layout, grid, raw, wide_best, width)
    else:
        _finish_columns(layout, grid, raw, col_best, width)
    return layout


def _finish_wide(layout: Layout, grid, raw, best, width) -> None:
    _, r, cols, notes, top = best
    layout.kind = "wide"
    layout.axis_notes = list(notes)
    freq = dominant_freq(cols.values())
    layout.freq = freq
    layout.period_row = r + 1
    layout.period_cols = {c: p for c, p in cols.items() if p.freq == freq}
    layout.other_period_cols = {c: p for c, p in cols.items() if p.freq != freq}
    first_pc = min(layout.period_cols)
    # Banner rows right above the period row ("Actual" | "Forecast") belong to the header.
    header_top = top
    for rr in range(top - 1, max(-1, top - 3), -1):
        cells = [grid[rr][c] for c in layout.period_cols if c < len(grid[rr])]
        if any(is_text(v) for v in cells) and not any(
            read_number(v).value is not None for v in cells
        ):
            header_top = rr
        else:
            break
    layout.header_rows = list(range(header_top + 1, r + 2))
    data_start = r + 1
    banner_rows = list(range(header_top, top))
    labels = [p.label for p in layout.period_cols.values()]
    if len(set(labels)) < len(labels) and r + 1 < len(grid):
        # One period over several columns: a row of text under it names them
        # ("2024 | 2024" over "Actual | Budget").
        cells = [grid[r + 1][c] for c in layout.period_cols if c < len(grid[r + 1])]
        named = [
            v
            for v in cells
            if _has_text(v) and not _UNITS_CELL_RE.match(clean_label(v))
        ]
        if len(named) * 2 >= len(cells) and not any(
            read_number(v).value is not None for v in cells
        ):
            banner_rows.append(r + 1)
            layout.header_rows.append(r + 2)
            data_start = r + 2
    for c in layout.period_cols:
        seen: List[str] = []
        for rr in banner_rows:
            v = grid[rr][c] if c < len(grid[rr]) else None
            if _has_text(v) and str(v).strip() not in seen:
                seen.append(str(v).strip())
        layout.banner_text[c] = " ".join(seen)
    for c in layout.period_cols:
        parts: List[str] = []
        for rr in range(header_top, r + 1):
            v = raw[rr][c] if c < len(raw[rr]) else None
            if v is not None and str(v).strip() and str(v).strip() not in parts:
                parts.append(str(v).strip())
        layout.header_text[c] = " ".join(parts)

    # Units row: just under (or over) the period row, text in the period columns.
    for rr in list(range(data_start, min(len(grid), data_start + 3))) + list(
        range(header_top - 1, max(-1, header_top - 3), -1)
    ):
        cells = [grid[rr][c] for c in layout.period_cols if c < len(grid[rr])]
        label_cells = [grid[rr][c] for c in range(first_pc) if c < len(grid[rr])]
        unit_cells = [
            v
            for v in cells
            if isinstance(v, str) and _UNITS_CELL_RE.match(clean_label(v))
        ]
        label_says = any(
            isinstance(v, str) and re.match(r"^\s*units?\b", v, re.I)
            for v in label_cells
        )
        if (
            unit_cells
            and len(unit_cells) >= 0.5 * len([v for v in cells if v is not None])
        ) or (label_says and not any(read_number(v).value is not None for v in cells)):
            layout.units_row = rr + 1
            texts = unit_cells or [v for v in cells if is_text(v)]
            if texts:
                layout.units = max(set(texts), key=texts.count).strip()
            elif label_says:
                layout.units = (
                    re.sub(
                        r"^\s*units?\s*[:\-]?\s*",
                        "",
                        _row_text(label_cells),
                        flags=re.I,
                    )
                    or None
                )
            if rr >= data_start:
                data_start = rr + 1
            break
    layout.first_data_row = data_start + 1

    # Label columns: left of the periods, holding text in the data rows.
    label_cols = []
    for c in range(first_pc):
        vals = [grid[rr][c] for rr in range(data_start, len(grid)) if c < len(grid[rr])]
        if any(is_text(v) for v in vals):
            label_cols.append(c)
        hdr = [grid[rr][c] for rr in range(header_top, r + 1)]
        if any(isinstance(v, str) and _UNITS_HEADER_RE.match(v.strip()) for v in hdr):
            layout.units_col = c
    if layout.units_col is not None and layout.units_col in label_cols:
        label_cols.remove(layout.units_col)
    if not label_cols:
        label_cols = [c for c in range(width) if c not in cols][:1]
    layout.label_cols = label_cols
    last_pc = max(layout.period_cols)
    layout.blank_columns = [
        c
        for c in range(first_pc, last_pc + 1)
        if c not in cols
        and all(grid[rr][c] is None for rr in range(r, len(grid)) if c < len(grid[rr]))
    ]
    _collect_titles(layout, raw, header_top)
    if layout.units is None:
        for line in layout.title_lines:
            found = units_from_text(line)
            if found:
                layout.units = found
                break
    # Notes: text rows after the last data row in the sample.
    last_data = data_start
    for rr in range(data_start, len(grid)):
        if any(
            read_number(grid[rr][c]).value is not None
            for c in layout.period_cols
            if c < len(grid[rr])
        ):
            last_data = rr
    _collect_notes(layout, raw, last_data + 1, len(raw))
    layout.reason = (
        f"periods across row {layout.period_row}"
        + (f" (built with the year row {top + 1})" if top < r else "")
        + f", labels in column {', '.join(column_letter(c) for c in label_cols)}"
    )


def _finish_columns(layout: Layout, grid, raw, best, width) -> None:
    _, pc, periods, notes = best
    layout.period_col = pc
    layout.axis_notes = list(notes)
    layout.freq = dominant_freq(periods)
    first = next(i for i, p in enumerate(periods) if p is not None)
    layout.first_data_row = first + 1
    header_rows = []
    for rr in range(first - 1, max(-1, first - 4), -1):
        if any(is_text(v) for v in grid[rr]):
            header_rows.insert(0, rr)
        else:
            break
    # A title line above the header is a single text cell, not a header row.
    while (
        len(header_rows) > 1
        and sum(1 for v in grid[header_rows[0]] if v is not None) == 1
    ):
        header_rows.pop(0)
    layout.header_rows = [rr + 1 for rr in header_rows]
    data = grid[first : len(grid)]
    for c in range(width):
        if c == pc:
            continue
        parts: List[str] = []
        for rr in header_rows:
            v = grid[rr][c] if c < len(grid[rr]) else None
            if v is not None and str(v).strip() and str(v).strip() not in parts:
                parts.append(str(v).strip())
        values = [row[c] if c < len(row) else None for row in data]
        filled = [v for v in values if v is not None]
        if not filled and not parts:
            continue
        name = " ".join(parts) or f"column {column_letter(c)}"
        layout.column_headers[c] = name
        if _UNITS_HEADER_RE.match(name):
            layout.units_col = c
            continue
        share = _numeric_share(values)
        if filled and share >= 0.5:
            layout.numeric_cols.append(c)
        elif filled:
            layout.dimension_cols.append(c)
    layout.kind = "stacked" if layout.dimension_cols else "columns"
    _collect_titles(layout, raw, header_rows[0] if header_rows else first)
    for line in layout.title_lines:
        found = units_from_text(line)
        if found:
            layout.units = found
            break
    last = first
    for rr in range(first, len(grid)):
        if periods[rr] is not None if rr < len(periods) else False:
            last = rr
    _collect_notes(layout, raw, last + 1, len(raw))
    layout.reason = f"periods down column {column_letter(pc)} from row {first + 1}" + (
        f"; series named in column {', '.join(column_letter(c) for c in layout.dimension_cols)}"
        if layout.dimension_cols
        else "; one series per column"
    )


def _collect_titles(layout: Layout, raw, header_top: int) -> None:
    for rr in range(0, header_top):
        text = _row_text(raw[rr])
        if text:
            layout.title_lines.append(text[:300])
            _note_footnote(layout, text)


def _note_footnote(layout: Layout, text: str) -> None:
    match = _FOOTNOTE_RE.match(text)
    if match:
        key = next(
            g
            for g in (
                match.group("a"),
                match.group("b"),
                match.group("c"),
                match.group("d"),
            )
            if g
        )
        layout.footnotes.setdefault(key.lower(), match.group("text").strip()[:300])


def _collect_notes(layout: Layout, raw, start: int, end: int) -> None:
    for rr in range(start, end):
        row = raw[rr]
        if any(read_number(v).value is not None for v in row if not isinstance(v, str)):
            continue
        text = _row_text(row)
        if not text:
            continue
        if (
            _FOOTNOTE_RE.match(text)
            or _NOTE_LEAD_RE.match(text)
            or len(layout.notes) < 12
        ):
            layout.notes.append(text[:300])
            _note_footnote(layout, text)
        if len(layout.notes) >= 30:
            break
