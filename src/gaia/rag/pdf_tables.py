# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Keep a PDF table's rows together, each cell named by its column header.

pypdf's plain extraction puts every table cell on its own line, so a row's
values read as an unbroken column of numbers and a reader picks the next row's
price. Layout extraction keeps the rows; this puts them back into the plain
text as one line per row, like ``Row: 52 | Item code: PT-4676 | Unit price: 504.37``,
so a row survives chunking whole and still says what each value is.
"""

import re
from typing import List, Optional, Sequence, Tuple

# Layout extraction pads columns with spaces; prose words sit one space apart.
_COLUMN_GAP = re.compile(r"\s{3,}")
_NUMBER = re.compile(r"[-+(]?[$€£¥]?\d[\d.,]*%?\)?")
_MIN_COLUMNS = 3
_MIN_ROWS = 3
# A page of mostly one-or-two-word lines is how a table reads in plain mode.
_SHORT_LINE_CHARS = 30
_MIN_SHORT_LINES = 12


def looks_tabular(plain_text: str) -> bool:
    """True when the plain text reads like table cells, one per line."""
    lines = [line.strip() for line in plain_text.splitlines() if line.strip()]
    short = sum(1 for line in lines if len(line) <= _SHORT_LINE_CHARS)
    return short >= _MIN_SHORT_LINES and short * 2 >= len(lines)


def _fields(line: str) -> List[str]:
    return [cell for cell in _COLUMN_GAP.split(line.strip()) if cell]


def _is_header(cells: Sequence[str]) -> bool:
    return not any(_NUMBER.fullmatch(cell) for cell in cells)


def _table_blocks(layout_text: str) -> List[List[List[str]]]:
    """Runs of at least three lines that split into the same 3+ columns."""
    blocks, run = [], []
    for line in layout_text.splitlines():
        if not line.strip():
            continue
        cells = _fields(line)
        if run and len(cells) == len(run[0]):
            run.append(cells)
            continue
        if len(run) >= _MIN_ROWS:
            blocks.append(run)
        run = [cells] if len(cells) >= _MIN_COLUMNS else []
    if len(run) >= _MIN_ROWS:
        blocks.append(run)
    return blocks


def _tokens(text: str) -> List[str]:
    return text.split()


def _find_cells(lines: List[str], cells: List[str], start: int) -> Optional[range]:
    """The span of ``lines`` from ``start`` holding exactly ``cells``' words."""
    wanted = [token for cell in cells for token in _tokens(cell)]
    for first in range(start, len(lines)):
        got: List[str] = []
        for last in range(first, len(lines)):
            got.extend(_tokens(lines[last]))
            if got != wanted[: len(got)]:
                break
            if len(got) == len(wanted):
                return range(first, last + 1)
    return None


def _row_line(header: Optional[Sequence[str]], cells: Sequence[str]) -> str:
    if header is None:
        return " | ".join(cells)
    return " | ".join(f"{name}: {value}" for name, value in zip(header, cells))


def tables_as_rows(
    plain_text: str, layout_text: str, carried_header: Optional[List[str]] = None
) -> Tuple[str, Optional[List[str]]]:
    """``plain_text`` with each table's cell-per-line run rewritten as rows.

    ``carried_header`` is the last header of the previous page: a table that
    continues onto this page without repeating it still names its columns.
    Returns the text and the header to carry to the next page. A table whose
    cells can't be found in ``plain_text`` word for word is left as it was.
    """
    lines = plain_text.splitlines()
    header = carried_header
    at = 0
    for block in _table_blocks(layout_text):
        if _is_header(block[0]):
            header, rows = block[0], block[1:]
        else:
            if header is not None and len(header) != len(block[0]):
                header = None
            rows = block
        span = _find_cells(lines, [cell for row in block for cell in row], at)
        if span is None:
            continue
        rendered = [_row_line(header, row) for row in rows]
        if header is not None and rows is not block:
            rendered.insert(0, " | ".join(header))
        lines[span.start : span.stop] = rendered
        at = span.start + len(rendered)
    return "\n".join(lines), header
