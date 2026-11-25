# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Rows for a chart or table slide, read from a .csv or .xlsx file.

The caller has already checked that the file may be read. Every refusal says
what to pass instead, so a chart never needs another tool to get its values.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

#: Above this a slide cannot show the data; the caller is told to name a range.
MAX_ROWS = 60
MAX_COLS = 30

#: A text file above this is data to aggregate first, not a table for a slide.
MAX_CSV_BYTES = 20_000_000

#: Rows read when no range is given: enough to see that a sheet is too large.
_READ_ROWS = MAX_ROWS * 4

_RANGE = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)(?::\$?([A-Za-z]{1,3})\$?(\d+))?$")


class DataError(ValueError):
    """The file or range cannot give chart rows; the message says what to pass."""


def _col(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + ord(ch) - 64
    return n


def parse_range(cell_range: str) -> Tuple[str, Optional[Tuple[int, int, int, int]]]:
    """("sheet or ''", (first row, first col, last row, last col) or None), 1-based."""
    text = str(cell_range or "").strip()
    sheet = ""
    if "!" in text:
        sheet, text = text.rsplit("!", 1)
        sheet = sheet.strip().strip("'")
    if not text:
        return sheet, None
    m = _RANGE.match(text.strip())
    if not m:
        raise DataError(
            f"range {cell_range!r} is not a cell range. Write it as 'A1:D8' "
            "(header row first), or 'Sheet name!A1:D8'."
        )
    c1, r1 = _col(m.group(1)), int(m.group(2))
    c2 = _col(m.group(3)) if m.group(3) else c1
    r2 = int(m.group(4)) if m.group(4) else r1
    return sheet, (min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))


def _shown(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return repr(text[:24])


def _trim(rows: List[List[Any]]) -> List[List[Any]]:
    """Drop empty rows, and columns that are empty in every row."""

    def blank(v: Any) -> bool:
        return v is None or (isinstance(v, str) and not v.strip())

    rows = [r for r in rows if not all(blank(v) for v in r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    keep = [c for c in range(width) if not all(blank(r[c]) for r in rows)]
    return [[r[c] for c in keep] for r in rows]


def _csv_rows(path: Path, box) -> List[List[Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataError(
            f"{path.name} is not UTF-8 text ({exc.reason} at byte {exc.start}). "
            "Save it as UTF-8, or pass the values as 'rows'."
        ) from exc
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    if box:
        r1, c1, r2, c2 = box
        rows = [r[c1 - 1 : c2] for r in rows[r1 - 1 : r2]]
    return rows


def _xlsx_rows(path: Path, sheet: str, box) -> Tuple[List[List[Any]], str]:
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        if sheet and sheet not in wb.sheetnames:
            raise DataError(
                f"{path.name} has no sheet {sheet!r}. Its sheets are: "
                f"{', '.join(wb.sheetnames)}."
            )
        ws = wb[sheet] if sheet else wb.worksheets[0]
        if box:
            r1, c1, r2, c2 = box
            cells = ws.iter_rows(
                min_row=r1, min_col=c1, max_row=r2, max_col=c2, values_only=True
            )
        else:
            cells = ws.iter_rows(max_row=_READ_ROWS, values_only=True)
        return [list(r) for r in cells], ws.title
    finally:
        wb.close()


def read_rows(
    path: str, sheet: str = "", cell_range: str = "", series_in: str = ""
) -> List[List[Any]]:
    """The header row and data rows of *path*, as a chart or table slide takes them.

    *cell_range* is ``A1:D8`` or ``Sheet!A1:D8``; without one the whole first
    sheet (or *sheet*) is read. ``series_in="rows"`` turns a sheet with one
    series per row (periods across the top) into one series per column.

    Raises:
        DataError: the file, sheet or range cannot be read, holds no values,
            or is too large to show; the message says what to pass.
    """
    p = Path(path)
    kind = p.suffix.lower()
    range_sheet, box = parse_range(cell_range)
    sheet = str(sheet or "").strip() or range_sheet
    where = p.name
    if kind in (".csv", ".tsv", ".txt"):
        size = p.stat().st_size
        if size > MAX_CSV_BYTES:
            raise DataError(
                f"{p.name} is {size / 1e6:.0f} MB, too large to chart from "
                "directly. Aggregate it first (run_python) and pass the "
                "result as 'rows'."
            )
        rows = _csv_rows(p, box)
    elif kind in (".xlsx", ".xlsm"):
        rows, title = _xlsx_rows(p, sheet, box)
        where = f"{p.name}, sheet {title!r}"
    else:
        raise DataError(
            f"{p.name} is a {kind or 'file with no extension'}; chart data is "
            "read from .csv and .xlsx files. Pass the values as 'rows' instead: "
            'rows=[["Period", "Series"], ["Q1", 4.1], ["Q2", 4.6]].'
        )
    capped = not box and len(rows) >= _READ_ROWS
    rows = _trim(rows)
    if cell_range:
        where += f", range {cell_range}"
    if len(rows) < 2:
        raise DataError(
            f"{where} holds {len(rows)} row(s) with values; a chart needs a "
            "header row and at least one data row. Name the range that holds "
            "them, e.g. range='A1:D8'."
        )
    series_in = str(series_in or "").strip().lower()
    if series_in in ("rows", "row"):
        rows = [list(r) for r in zip(*rows)]
    elif series_in not in ("", "columns", "column", "cols"):
        raise DataError(
            f"series_in {series_in!r} is not known. Use 'columns' (the default: "
            "one series per column) or 'rows' (one series per row)."
        )
    if len(rows) - 1 > MAX_ROWS or len(rows[0]) > MAX_COLS:
        # Enough of the sheet to pick a range from, without another read.
        across = ", ".join(_shown(v) for v in rows[0][:8])
        down = ", ".join(_shown(r[0]) for r in rows[1:9])
        raise DataError(
            f"{where} holds {'at least ' if capped else ''}{len(rows) - 1} data "
            f"rows and {len(rows[0])} columns, more than a slide shows "
            f"({MAX_ROWS} rows, {MAX_COLS} columns). Its first row starts: "
            f"{across}. Its first column starts: {down}. Name the part to "
            "chart, e.g. range='A1:D13' (header row first; series_in='rows' "
            "when each series is a row), or aggregate it first and pass the "
            "result as 'rows'."
        )
    if all(v is None for r in rows[1:] for v in r[1:]):
        raise DataError(
            f"{where} has no values beside its labels (formulas with no saved "
            "result read as empty). Name the range that holds the numbers, or "
            "pass them as 'rows'."
        )
    return rows
