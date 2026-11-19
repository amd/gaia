# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check the formulas in an .xlsx workbook without changing it.

openpyxl writes formulas but never computes them, so a workbook built in code
opens with whatever its formulas happen to produce: a SUMIFS whose criteria
range slid one column over sums to 0, a SUM over a text column sums to 0, a
variance cell pointing at an empty cell reads as the full amount. Nothing in
the file says so until a person opens it.

Two passes:

* **Recalculation** — when LibreOffice is installed, a copy of the workbook is
  converted headless in the tool call's temporary folder, which computes every
  formula; the computed values and error values (``#REF!``, ``#DIV/0!`` ...)
  are read back from that copy. The original file is never written. This goes
  through :mod:`gaia.skills.context`, so it runs only inside the skill's tool
  call, with the ``program:run:soffice`` and ``scratch:write`` it declares.
* **Static checks** — always: unknown functions and names, references to
  sheets that do not exist, literal error values, circular references,
  numeric functions over text or empty cells, SUMIFS-style ranges of different
  sizes, criteria that match no row, and arithmetic on text or empty cells.

A finding that depends on what a cell evaluates to (a division by an empty
cell, arithmetic on text) is an error only when a computed value confirms it.
Without a recalculation it is a warning: Excel reads ``"5%"`` and
``"2024-03-01"`` as numbers, and an empty cell is often an input the user has
yet to fill in.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import zipfile
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import IO, Any, Callable, Dict, Iterable, List, Optional, Tuple

from gaia.logger import get_logger
from gaia.skills.context import (
    copy_to_temp,
    find_program,
    run_program,
    temp_dir,
    write_temp_file,
)

logger = get_logger(__name__)

#: Error values a spreadsheet cell can hold.
ERROR_VALUES = frozenset(
    {
        "#NULL!",
        "#DIV/0!",
        "#VALUE!",
        "#REF!",
        "#NAME?",
        "#NUM!",
        "#N/A",
        "#SPILL!",
        "#CALC!",
        "#GETTING_DATA",
    }
)

#: Functions whose range arguments are summed or averaged; text in them is skipped.
NUMERIC_FUNCTIONS = frozenset(
    {
        "SUM",
        "AVERAGE",
        "MIN",
        "MAX",
        "PRODUCT",
        "SUMPRODUCT",
        "MEDIAN",
        "STDEV",
        "STDEVP",
        "VAR",
        "VARP",
        "STDEV.S",
        "STDEV.P",
        "VAR.S",
        "VAR.P",
    }
)

#: Conditional aggregates: (index of the value range or None, index of the
#: first criteria range, whether criteria come in (range, criterion) pairs).
#: SUMIF/AVERAGEIF take (range, criterion[, sum_range]).
_CONDITIONAL = {
    "SUMIFS": (0, 1),
    "AVERAGEIFS": (0, 1),
    "MAXIFS": (0, 1),
    "MINIFS": (0, 1),
    "COUNTIFS": (None, 0),
}
_SINGLE_CONDITION = frozenset({"SUMIF", "AVERAGEIF", "COUNTIF"})

#: Functions whose result is a total or a count: the values worth reporting.
AGGREGATE_FUNCTIONS = (
    NUMERIC_FUNCTIONS
    | frozenset(_CONDITIONAL)
    | _SINGLE_CONDITION
    | frozenset({"COUNT", "COUNTA", "COUNTBLANK", "AVERAGEA", "SUBTOTAL", "AGGREGATE"})
)

#: A formula inside one of these may never evaluate the branch that looks
#: broken, so value-dependent guesses about it are not made.
_GUARDS = frozenset(
    {"IFERROR", "IFNA", "IF", "IFS", "ISERROR", "ISERR", "ISNA", "ISNUMBER", "ISBLANK"}
)
#: Functions whose arguments name their own variables.
_BINDERS = frozenset({"LET", "LAMBDA"})

MAX_REPORTED = 50
MAX_VALUES = 60
#: Above these the workbook is refused: it is loaded twice, and a check that
#: outlasts the tool's time limit tells the agent nothing.
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_CELLS = 500_000
#: LibreOffice's first start creates its profile, which takes a while.
RECALC_TIMEOUT_S = 180

# Calc's "Recalculation on file load" for Excel 2007+ and ODF files defaults to
# never, which keeps the values the last program cached instead of computing
# them. 0 is "always recalculate"; seeded into the private profile per run.
_ALWAYS_RECALC_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""

_ARITHMETIC = frozenset({"+", "-", "*", "/", "^"})
_CELL = re.compile(r"^([A-Z]{1,3})(\d{1,7})$")
_COLS = re.compile(r"^([A-Z]{1,3}):([A-Z]{1,3})$")
_ROWS = re.compile(r"^(\d{1,7}):(\d{1,7})$")
_CRITERION_OP = re.compile(r"^(<=|>=|<>|=|<|>)(.*)$", re.DOTALL)
_PREFIXES = ("_xlfn._xlws.", "_xlfn.", "_xlws.")
#: How Excel stores a LET or LAMBDA variable in the file.
_PARAMETER_PREFIX = "_xlpm."
_SHEET_XML = re.compile(r"^xl/worksheets/[^/]+\.xml$")
_CELL_TAG = re.compile(rb"<c[ >]")
_TOTAL_LABEL = re.compile(r"\b(grand\s+)?(sub)?totals?\b", re.IGNORECASE)
_SUMMARY_SHEET = re.compile(r"summary|total|dashboard|overview|report", re.IGNORECASE)


class NotAWorkbook(Exception):
    """The file cannot be opened as an .xlsx workbook."""


class WorkbookTooLarge(Exception):
    """The workbook is over :data:`MAX_FILE_BYTES` or :data:`MAX_CELLS`."""


@dataclass(frozen=True)
class Ref:
    """A rectangular reference resolved to one sheet."""

    sheet: str
    min_col: int
    min_row: int
    max_col: int
    max_row: int
    whole: bool = False

    @property
    def is_cell(self) -> bool:
        return self.min_col == self.max_col and self.min_row == self.max_row

    @property
    def shape(self) -> Tuple[int, int]:
        return (self.max_row - self.min_row + 1, self.max_col - self.min_col + 1)

    def contains(self, sheet: str, row: int, col: int) -> bool:
        return (
            sheet == self.sheet
            and self.min_row <= row <= self.max_row
            and self.min_col <= col <= self.max_col
        )

    def label(self) -> str:
        from openpyxl.utils import get_column_letter

        if self.whole and self.min_row == 1:
            cols = (
                f"{get_column_letter(self.min_col)}:{get_column_letter(self.max_col)}"
            )
            return f"{_quote(self.sheet)}!{cols}"
        if self.whole:
            return f"{_quote(self.sheet)}!{self.min_row}:{self.max_row}"
        start = f"{get_column_letter(self.min_col)}{self.min_row}"
        if self.is_cell:
            return f"{_quote(self.sheet)}!{start}"
        end = f"{get_column_letter(self.max_col)}{self.max_row}"
        return f"{_quote(self.sheet)}!{start}:{end}"


def _quote(sheet: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", sheet):
        return sheet
    return "'" + sheet.replace("'", "''") + "'"


def cell_label(sheet: str, row: int, col: int) -> str:
    from openpyxl.utils import get_column_letter

    return f"{_quote(sheet)}!{get_column_letter(col)}{row}"


def _formula_text(value: Any) -> Optional[str]:
    """The formula a cell holds, or None. Covers array and data-table formulas."""
    if isinstance(value, str):
        return value if value.startswith("=") and len(value) > 1 else None
    text = getattr(value, "text", None)
    if isinstance(text, str) and text:
        return text if text.startswith("=") else "=" + text
    return None


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float, datetime, date, time))


def _as_number(value: Any) -> Optional[float]:
    """*value* as a number the way a comparison would read it, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


class _Sheets:
    """Every non-empty cell of every sheet, read once."""

    def __init__(self, wb: Any):
        from openpyxl.utils import column_index_from_string

        self._col_index = column_index_from_string
        self.titles = {ws.title.lower(): ws.title for ws in wb.worksheets}
        self.cells: Dict[str, Dict[Tuple[int, int], Any]] = {}
        self.bounds: Dict[str, Tuple[int, int]] = {}
        self.formulas: Dict[str, Dict[Tuple[int, int], str]] = {}
        # Rows holding a formula, per sheet and column, sorted: what lets a
        # reference find the formulas it covers without scanning the sheet.
        self.formula_rows: Dict[str, Dict[int, List[int]]] = {}
        self.cell_count = 0
        for ws in wb.worksheets:
            values: Dict[Tuple[int, int], Any] = {}
            formulas: Dict[Tuple[int, int], str] = {}
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is None or cell.value == "":
                        continue
                    values[(cell.row, cell.column)] = cell.value
                    text = _formula_text(cell.value)
                    if text:
                        formulas[(cell.row, cell.column)] = text
            self.cells[ws.title] = values
            self.formulas[ws.title] = formulas
            by_col: Dict[int, List[int]] = {}
            for r, c in formulas:
                by_col.setdefault(c, []).append(r)
            for rows in by_col.values():
                rows.sort()
            self.formula_rows[ws.title] = by_col
            self.cell_count += len(values)
            self.bounds[ws.title] = (ws.max_row or 1, ws.max_column or 1)
        self.names = _defined_names(wb)
        self.tables = _table_bodies(wb)

    def sheet(self, name: Optional[str], default: str) -> Optional[str]:
        if name is None:
            return default
        return self.titles.get(name.lower())

    def value(self, sheet: str, row: int, col: int) -> Any:
        return self.cells.get(sheet, {}).get((row, col))

    def cells_in(self, ref: Ref) -> Iterable[Tuple[Tuple[int, int], Any]]:
        """Non-empty cells inside *ref*, top to bottom."""
        cells = self.cells.get(ref.sheet, {})
        rows, cols = ref.shape
        if rows * cols <= len(cells):
            for r in range(ref.min_row, ref.max_row + 1):
                for c in range(ref.min_col, ref.max_col + 1):
                    if (r, c) in cells:
                        yield (r, c), cells[(r, c)]
            return
        for (r, c), value in sorted(cells.items()):
            if ref.min_row <= r <= ref.max_row and ref.min_col <= c <= ref.max_col:
                yield (r, c), value

    def formulas_in(self, ref: Ref) -> Iterable[Tuple[int, int]]:
        """``(row, col)`` of every formula cell inside *ref*."""
        by_col = self.formula_rows.get(ref.sheet, {})
        if ref.max_col - ref.min_col + 1 <= len(by_col):
            cols: Iterable[int] = range(ref.min_col, ref.max_col + 1)
        else:
            cols = [c for c in by_col if ref.min_col <= c <= ref.max_col]
        for c in cols:
            rows = by_col.get(c)
            if not rows:
                continue
            start = bisect_left(rows, ref.min_row)
            for r in rows[start : bisect_right(rows, ref.max_row)]:
                yield r, c

    def parse_ref(self, text: str, current: str) -> Tuple[Optional[Ref], Optional[str]]:
        """``(Ref, None)``, ``(None, problem)``, or ``(None, None)`` when unknowable.

        Unknowable: structured table references, external workbooks, 3-D
        references — none of them is checked rather than guessed at.
        """
        sheet_part, _, address = text.rpartition("!")
        if "[" in text or ":" in sheet_part:
            return None, None
        sheet_name: Optional[str] = None
        if sheet_part:
            sheet_name = sheet_part
            if sheet_name.startswith("'") and sheet_name.endswith("'"):
                sheet_name = sheet_name[1:-1].replace("''", "'")
        address = address.replace("$", "").upper()
        if not sheet_part and not (
            _CELL.match(address) or _COLS.match(address) or ":" in address
        ):
            return self._resolve_name(text, current)
        sheet = self.sheet(sheet_name, current)
        if sheet is None:
            return None, (
                f"refers to sheet '{sheet_name}', which does not exist in this "
                f"workbook (sheets: {', '.join(self.titles.values())}) — #REF!"
            )
        ref = self._rect(address, sheet)
        if ref is None:
            return None, None
        return ref, None

    def _rect(self, address: str, sheet: str) -> Optional[Ref]:
        max_row, max_col = self.bounds[sheet]
        if ":" in address:
            start, _, end = address.partition(":")
            a, b = _CELL.match(start), _CELL.match(end)
            if a and b:
                c1, c2 = self._col_index(a[1]), self._col_index(b[1])
                r1, r2 = int(a[2]), int(b[2])
                return Ref(sheet, min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2))
            cols = _COLS.match(address)
            if cols:
                c1, c2 = self._col_index(cols[1]), self._col_index(cols[2])
                return Ref(sheet, min(c1, c2), 1, max(c1, c2), max_row, whole=True)
            rows = _ROWS.match(address)
            if rows:
                r1, r2 = int(rows[1]), int(rows[2])
                return Ref(sheet, 1, min(r1, r2), max_col, max(r1, r2), whole=True)
            return None
        cell = _CELL.match(address)
        if cell:
            col = self._col_index(cell[1])
            return Ref(sheet, col, int(cell[2]), col, int(cell[2]))
        return None

    def _resolve_name(
        self, name: str, current: str
    ) -> Tuple[Optional[Ref], Optional[str]]:
        if name.lower() in self.tables:
            return self.tables[name.lower()], None
        target = self.names.get(name.lower())
        if target is None:
            return None, (
                f"uses the name '{name}', which is not a cell reference, a "
                "defined name or a table in this workbook — #NAME?"
            )
        if "!" not in target:
            return None, None
        return self.parse_ref(target, current)


def _defined_names(wb: Any) -> Dict[str, str]:
    """Workbook- and sheet-scoped defined names, lower-cased, to their text."""
    names: Dict[str, str] = {}
    containers = [getattr(wb, "defined_names", None)]
    containers += [getattr(ws, "defined_names", None) for ws in wb.worksheets]
    for container in containers:
        if not container:
            continue
        for key in list(container.keys()) if hasattr(container, "keys") else []:
            defined = container[key]
            text = getattr(defined, "attr_text", None) or getattr(defined, "value", "")
            names[str(key).lower()] = str(text)
    return names


def _table_bodies(wb: Any) -> Dict[str, Optional[Ref]]:
    """Each table's data rows by lower-cased name: what ``=ROWS(Sales)`` means."""
    from openpyxl.utils import range_boundaries

    tables: Dict[str, Optional[Ref]] = {}
    for ws in wb.worksheets:
        for table in ws.tables.values():
            min_col, min_row, max_col, max_row = range_boundaries(table.ref)
            # An absent headerRowCount means one header row.
            header = table.headerRowCount
            min_row += 1 if header is None else int(header)
            max_row -= int(table.totalsRowCount or 0)
            body = None
            if min_row <= max_row:
                body = Ref(ws.title, min_col, min_row, max_col, max_row)
            tables[str(table.name).lower()] = body
    return tables


def _function_name(token_value: str) -> str:
    name = token_value[:-1] if token_value.endswith("(") else token_value
    upper = name.upper()
    for prefix in _PREFIXES:
        if upper.startswith(prefix.upper()):
            return upper[len(prefix) :]
    return upper


def _is_known_function(token_value: str) -> bool:
    from openpyxl.utils import FORMULAE

    name = token_value[:-1] if token_value.endswith("(") else token_value
    upper = name.upper()
    if any(upper.startswith(p.upper()) for p in _PREFIXES):
        return True
    return upper in FORMULAE


@dataclass
class _Call:
    name: str
    args: List[List[Any]]


def _tokenize(formula: str) -> Tuple[List[Any], List[_Call], set]:
    """The formula's tokens, its function calls, and the guarded tokens.

    A call lists its direct arguments. A token is guarded (by ``id``) when it
    sits inside a :data:`_GUARDS` call.
    """
    from openpyxl.formula.tokenizer import Token, Tokenizer

    tokens = [t for t in Tokenizer(formula).items if t.type != Token.WSPACE]
    calls: List[_Call] = []
    stack: List[_Call] = []
    guarded: set = set()
    for token in tokens:
        if token.type == Token.FUNC and token.subtype == Token.OPEN:
            call = _Call(_function_name(token.value), [[]])
            if stack:
                stack[-1].args[-1].append(call)
            stack.append(call)
            calls.append(call)
        elif token.type == Token.FUNC and token.subtype == Token.CLOSE:
            if stack:
                stack.pop()
        elif token.type == Token.SEP and token.subtype == Token.ARG and stack:
            stack[-1].args.append([])
        elif stack:
            stack[-1].args[-1].append(token)
            if any(call.name in _GUARDS for call in stack):
                guarded.add(id(token))
    return tokens, calls, guarded


def _local_names(calls: List[_Call]) -> set:
    """Lower-cased variables that LET and LAMBDA calls declare."""
    names: set = set()
    for call in calls:
        if call.name not in _BINDERS:
            continue
        # LET(name, value, ..., result); LAMBDA(parameter, ..., body).
        step = 2 if call.name == "LET" else 1
        for arg in call.args[:-1][::step]:
            token = _single_operand(arg)
            if token is not None:
                names.add(str(token.value).lower())
    return names


def _single_operand(arg: List[Any]) -> Optional[Any]:
    """The arg's one token when it is a lone operand, else None."""
    if len(arg) == 1 and not isinstance(arg[0], _Call):
        return arg[0]
    return None


def _criterion_matches(criterion: Any, value: Any) -> bool:
    """Excel's criterion matching, for a literal criterion."""
    op, operand = "=", criterion
    if isinstance(criterion, str):
        match = _CRITERION_OP.match(criterion)
        if match:
            op, operand = match[1], match[2]
    target = _as_number(operand)
    actual = _as_number(value)
    if op in ("<", ">", "<=", ">="):
        if target is None or actual is None:
            return False
        return {
            "<": actual < target,
            ">": actual > target,
            "<=": actual <= target,
            ">=": actual >= target,
        }[op]
    if target is not None and actual is not None:
        equal = actual == target
    else:
        text = "" if value is None else str(value).lower()
        pattern = str(operand).lower()
        if "*" in pattern or "?" in pattern:
            equal = fnmatch.fnmatchcase(text, pattern)
        else:
            equal = text == pattern
    return not equal if op == "<>" else equal


class _Checker:
    def __init__(self, sheets: _Sheets):
        self.sheets = sheets
        self.errors: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []
        # Errors only if the cell evaluates to one; _settle decides each.
        self.guesses: List[Tuple[Tuple[str, int, int], Dict[str, Any]]] = []
        self.deps: Dict[Tuple[str, int, int], set] = {}
        self.aggregates: set = set()
        self._seen: set = set()
        self._profiles: Dict[Ref, Dict[str, Any]] = {}

    def _entry(self, sheet, row, col, formula, problem, error=None):
        """A finding, or None when this cell already has the same one."""
        key = (sheet, row, col, problem)
        if key in self._seen:
            return None
        self._seen.add(key)
        entry = {
            "cell": cell_label(sheet, row, col),
            "formula": formula,
            "problem": problem,
        }
        if error:
            entry["error"] = error
        return entry

    def _add(self, bucket, sheet, row, col, formula, problem, error=None):
        entry = self._entry(sheet, row, col, formula, problem, error)
        if entry is not None:
            bucket.append(entry)

    def run(self) -> None:
        for sheet, formulas in self.sheets.formulas.items():
            for (row, col), formula in formulas.items():
                self._check(sheet, row, col, formula)

    def _check(self, sheet: str, row: int, col: int, formula: str) -> None:
        from openpyxl.formula.tokenizer import Token, TokenizerError

        try:
            tokens, calls, guarded = _tokenize(formula)
        except TokenizerError as e:
            self._add(self.errors, sheet, row, col, formula, f"cannot be parsed: {e}")
            return

        def error(problem: str, code: Optional[str] = None) -> None:
            self._add(self.errors, sheet, row, col, formula, problem, code)

        def guess(problem: str, code: str) -> None:
            entry = self._entry(sheet, row, col, formula, problem, code)
            if entry is not None:
                self.guesses.append(((sheet, row, col), entry))

        def warn(problem: str) -> None:
            self._add(self.warnings, sheet, row, col, formula, problem)

        deps = self.deps.setdefault((sheet, row, col), set())
        local = _local_names(calls)
        if any(call.name in AGGREGATE_FUNCTIONS for call in calls):
            self.aggregates.add((sheet, row, col))

        refs: Dict[int, Ref] = {}
        for token in tokens:
            if token.type == Token.FUNC and token.subtype == Token.OPEN:
                if not _is_known_function(token.value):
                    name = token.value[:-1]
                    error(
                        f"calls {name}(), which Excel does not recognise when "
                        f"written this way. If it is a newer Excel function, "
                        f"write it as _xlfn.{name.upper()}(; otherwise check the "
                        "spelling — #NAME?",
                        "#NAME?",
                    )
            elif token.type == Token.OPERAND and token.subtype == Token.ERROR:
                error(
                    f"contains the error value {token.value} written into the "
                    "formula itself",
                    token.value if token.value in ERROR_VALUES else None,
                )
            elif token.type == Token.OPERAND and token.subtype == Token.RANGE:
                name = token.value.lower()
                if name in local or name.startswith(_PARAMETER_PREFIX):
                    continue
                ref, problem = self.sheets.parse_ref(token.value, sheet)
                if problem:
                    error(problem, "#NAME?" if "#NAME?" in problem else "#REF!")
                if ref is not None:
                    refs[id(token)] = ref
                    deps.update(
                        (ref.sheet, r, c) for r, c in self.sheets.formulas_in(ref)
                    )

        self._check_arithmetic(tokens, refs, guarded, guess, warn)
        for call in calls:
            self._check_call(call, refs, error, warn)

    def _check_arithmetic(self, tokens, refs, guarded, guess, warn) -> None:
        from openpyxl.formula.tokenizer import Token

        for i, token in enumerate(tokens):
            ref = refs.get(id(token))
            if ref is None or not ref.is_cell or id(token) in guarded:
                continue
            value = self.sheets.value(ref.sheet, ref.min_row, ref.min_col)
            prev = tokens[i - 1] if i > 0 else None
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None

            def infix(t) -> bool:
                return (
                    t is not None and t.type == Token.OP_IN and t.value in _ARITHMETIC
                )

            negated = prev is not None and prev.type == Token.OP_PRE
            in_arithmetic = infix(prev) or infix(nxt) or negated
            divisor = infix(prev) and prev.value == "/"
            if value is None:
                if divisor:
                    guess(
                        f"divides by {ref.label()}, which is empty — #DIV/0!", "#DIV/0!"
                    )
                elif in_arithmetic:
                    warn(
                        f"does arithmetic with {ref.label()}, which is empty "
                        "(read as 0). Check the formula points at the right cell."
                    )
                continue
            if _formula_text(value):
                continue
            if divisor and _as_number(value) == 0 and not isinstance(value, str):
                guess(f"divides by {ref.label()}, which is 0 — #DIV/0!", "#DIV/0!")
            elif in_arithmetic and isinstance(value, str) and _as_number(value) is None:
                guess(
                    f"does arithmetic with {ref.label()}, which holds the text "
                    f"{value[:40]!r} — #VALUE! unless Excel can read that text "
                    "as a number or a date. Store it as a number or a date.",
                    "#VALUE!",
                )

    def _range_profile(self, ref: Ref) -> Dict[str, Any]:
        """Counts of numbers, formulas and text in *ref* (header text excluded)."""
        if ref in self._profiles:
            return self._profiles[ref]
        numbers = formulas = 0
        text: List[Tuple[Tuple[int, int], Any]] = []
        first_data_row = None
        for (r, c), value in self.sheets.cells_in(ref):
            if _formula_text(value):
                formulas += 1
                first_data_row = first_data_row or r
            elif _is_number(value):
                numbers += 1
                first_data_row = first_data_row or r
            elif isinstance(value, str):
                text.append(((r, c), value))
        # Text above the first number is a header, not data SUM skips.
        body_text = [
            t for t in text if first_data_row is not None and t[0][0] > first_data_row
        ]
        profile = {
            "numbers": numbers,
            "formulas": formulas,
            "text": text,
            "body_text": body_text,
        }
        self._profiles[ref] = profile
        return profile

    def _check_numeric_range(self, fn: str, ref: Ref, error, warn) -> None:
        profile = self._range_profile(ref)
        if profile["numbers"] or profile["formulas"]:
            if profile["body_text"]:
                (r, c), sample = profile["body_text"][0]
                warn(
                    f"{fn} over {ref.label()} skips {len(profile['body_text'])} "
                    f"text cell(s) among the numbers (e.g. "
                    f"{cell_label(ref.sheet, r, c)} = {sample[:40]!r}). Numbers "
                    "stored as text are not added."
                )
            return
        if profile["text"]:
            (r, c), sample = profile["text"][0]
            error(
                f"{fn} over {ref.label()} finds only text (e.g. "
                f"{cell_label(ref.sheet, r, c)} = {sample[:40]!r}), so it "
                "returns 0. It probably points at the wrong column or rows."
            )
        else:
            # A template's entry cells are empty until the user fills them in.
            warn(
                f"{fn} over {ref.label()} covers only empty cells, so it returns "
                "0. That is fine for cells the user will fill in; otherwise it "
                "points at the wrong column or rows."
            )

    def _check_call(self, call: _Call, refs, error, warn) -> None:
        def ref_of(arg) -> Optional[Ref]:
            token = _single_operand(arg)
            return refs.get(id(token)) if token is not None else None

        if call.name in NUMERIC_FUNCTIONS:
            for arg in call.args:
                ref = ref_of(arg)
                if ref is not None and not ref.is_cell:
                    self._check_numeric_range(call.name, ref, error, warn)
            return

        pairs: List[Tuple[Optional[Ref], List[Any]]] = []
        value_ref: Optional[Ref] = None
        if call.name in _CONDITIONAL:
            value_index, first = _CONDITIONAL[call.name]
            if value_index is not None and len(call.args) > value_index:
                value_ref = ref_of(call.args[value_index])
            for i in range(first, len(call.args) - 1, 2):
                pairs.append((ref_of(call.args[i]), call.args[i + 1]))
            shapes = {r.shape for r in [value_ref, *(p[0] for p in pairs)] if r}
            if len(shapes) > 1:
                labels = ", ".join(
                    r.label() for r in [value_ref, *(p[0] for p in pairs)] if r
                )
                error(
                    f"{call.name} ranges are different sizes ({labels}); every "
                    "range must have the same number of rows and columns — "
                    "#VALUE!",
                    "#VALUE!",
                )
                return
        elif call.name in _SINGLE_CONDITION and len(call.args) >= 2:
            pairs.append((ref_of(call.args[0]), call.args[1]))
            if call.name != "COUNTIF":
                value_ref = ref_of(call.args[2]) if len(call.args) >= 3 else pairs[0][0]
        else:
            return

        if value_ref is not None and not value_ref.is_cell:
            self._check_numeric_range(call.name, value_ref, error, warn)
        for criteria_ref, criterion_arg in pairs:
            if criteria_ref is not None:
                self._check_criterion(
                    call.name, criteria_ref, criterion_arg, refs, warn
                )

    def _check_criterion(self, fn, criteria_ref, criterion_arg, refs, warn) -> None:
        from openpyxl.formula.tokenizer import Token

        token = _single_operand(criterion_arg)
        if token is None:
            return
        source = ""
        if token.type == Token.OPERAND and token.subtype == Token.TEXT:
            criterion: Any = token.value[1:-1].replace('""', '"')
        elif token.type == Token.OPERAND and token.subtype == Token.NUMBER:
            criterion = float(token.value)
        elif id(token) in refs and refs[id(token)].is_cell:
            ref = refs[id(token)]
            criterion = self.sheets.value(ref.sheet, ref.min_row, ref.min_col)
            source = f" (from {ref.label()})"
            if criterion is None:
                warn(
                    f"{fn} criterion {ref.label()} is empty, so it matches only "
                    f"empty cells in {criteria_ref.label()}."
                )
                return
            if _formula_text(criterion):
                return
        else:
            return
        for _, value in self.sheets.cells_in(criteria_ref):
            if _formula_text(value):
                return  # computed criteria cells: only a recalculation can tell
            if _criterion_matches(criterion, value):
                return
        warn(
            f"{fn} criterion {criterion!r}{source} matches no cell in "
            f"{criteria_ref.label()}, so the result is 0 (or #DIV/0! for an "
            "average). Check that the criteria range is the column holding "
            "those values."
        )


def _cycles(deps: Dict[Tuple[str, int, int], set]) -> List[List[Tuple[str, int, int]]]:
    """Strongly connected components that form a loop (Tarjan, iterative)."""
    index: Dict[Any, int] = {}
    low: Dict[Any, int] = {}
    on_stack: set = set()
    stack: List[Any] = []
    found: List[List[Any]] = []
    counter = 0
    for root in deps:
        if root in index:
            continue
        work = [(root, iter(sorted(deps.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(deps.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    item = stack.pop()
                    on_stack.discard(item)
                    component.append(item)
                    if item == node:
                        break
                if len(component) > 1 or node in deps.get(node, ()):
                    found.append(sorted(component))
    return found


def soffice_installed() -> bool:
    """True when LibreOffice is on PATH or in its usual install folder."""
    return find_program("soffice") is not None


def recalculate(path: Path) -> Path:
    """Convert a copy of *path* with LibreOffice, which computes every formula.

    Runs inside a skill tool call: the copy, the LibreOffice profile and the
    converted file live in the call's temporary folder (``scratch:write``) and
    LibreOffice starts through ``run_program`` (``program:run:soffice``). A
    private profile keeps a running LibreOffice window from swallowing the
    request, and is set to recalculate on load: by default Calc keeps the
    values cached in an Excel-saved file, which would hide a formula broken
    since.

    Returns:
        The recalculated copy, inside the call's temporary folder.

    Raises:
        RuntimeError: LibreOffice did not produce the copy.
    """
    source = copy_to_temp(path, "source" + path.suffix.lower())
    write_temp_file("profile/user/registrymodifications.xcu", _ALWAYS_RECALC_PROFILE)
    profile = temp_dir("profile").as_uri()
    out_dir = temp_dir("out")
    try:
        proc = run_program(
            "soffice",
            [
                f"-env:UserInstallation={profile}",
                "--headless",
                "--norestore",
                "--nologo",
                "--convert-to",
                "xlsx",
                "--outdir",
                str(out_dir),
                str(source),
            ],
            timeout=RECALC_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"LibreOffice did not finish recalculating within {RECALC_TIMEOUT_S}s"
        ) from e
    result = out_dir / "source.xlsx"
    if proc.returncode != 0 or not result.is_file():
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise RuntimeError(
            f"LibreOffice exited with code {proc.returncode} without writing a "
            f"recalculated copy. {detail}".strip()
        )
    return result


def _refuse_if_too_large(path: Path, stream: IO[bytes]) -> None:
    """Raise unless the workbook is small enough to load and check in time.

    Cells are counted from the sheets' XML without parsing it, so a workbook
    that would take minutes to load is refused in a second or two.
    """
    smaller = (
        "Save the summary sheets and the rows they are built from as a "
        "smaller workbook and check that, and say the full workbook was not "
        "checked."
    )
    size = stream.seek(0, 2)
    stream.seek(0)
    if size > MAX_FILE_BYTES:
        raise WorkbookTooLarge(
            f"{path} is {size / 1048576:.0f} MB; verify_workbook checks "
            f"workbooks up to {MAX_FILE_BYTES // 1048576} MB. {smaller}"
        )
    cells = 0
    try:
        with zipfile.ZipFile(stream) as archive:
            for info in archive.infolist():
                if not _SHEET_XML.match(info.filename):
                    continue
                with archive.open(info) as part:
                    tail = b""
                    while chunk := part.read(1 << 20):
                        # The tail catches a tag split across two chunks.
                        cells += len(_CELL_TAG.findall(tail + chunk))
                        tail = chunk[-2:]
                        if cells > MAX_CELLS:
                            raise WorkbookTooLarge(
                                f"{path} has more than {MAX_CELLS:,} cells, "
                                f"the most verify_workbook checks. {smaller}"
                            )
    except zipfile.BadZipFile as e:
        raise NotAWorkbook(str(e)) from e
    finally:
        stream.seek(0)


def _load(source: Any, *, data_only: bool = False) -> Any:
    """``load_workbook``, with an unreadable file reported as :class:`NotAWorkbook`."""
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        return load_workbook(source, data_only=data_only)
    except (
        InvalidFileException,
        zipfile.BadZipFile,
        KeyError,
        ValueError,
        OSError,
    ) as e:
        # What openpyxl raises for a file that is not a valid workbook.
        raise NotAWorkbook(str(e) or type(e).__name__) from e


def _computed_values(source: Any) -> Dict[str, Dict[Tuple[int, int], Any]]:
    """Each cell's stored value, from a path or an open binary file."""
    wb = _load(source, data_only=True)
    try:
        return {
            ws.title: {
                (cell.row, cell.column): cell.value
                for row in ws.iter_rows()
                for cell in row
                if cell.value is not None
            }
            for ws in wb.worksheets
        }
    finally:
        wb.close()


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 6)
    return value


def _is_error_value(value: Any) -> bool:
    return isinstance(value, str) and value in ERROR_VALUES


def _settle(
    checker: _Checker,
    sheets: _Sheets,
    computed: Optional[Dict[str, Dict[Tuple[int, int], Any]]],
) -> None:
    """Decide each value-dependent guess, then add the error values computed.

    A guess is an error when the cell's computed value is an error, and a
    warning otherwise: whether text or an empty cell actually breaks the
    arithmetic depends on how the spreadsheet evaluates it.
    """
    for (sheet, r, c), entry in checker.guesses:
        value = None if computed is None else computed.get(sheet, {}).get((r, c))
        if _is_error_value(value):
            entry["computed"] = value
            checker.errors.append(entry)
            continue
        code = entry.pop("error")
        if value is None:
            entry["possible_error"] = code
            entry[
                "problem"
            ] += " The formulas were not recalculated, so this is not confirmed."
        else:
            entry["problem"] += (
                f" The computed value is {_jsonable(value)!r}, not an error; "
                "check the cell it names holds what the formula expects."
            )
        checker.warnings.append(entry)
    if computed is None:
        return
    by_cell: Dict[str, List[Dict[str, Any]]] = {}
    for entry in checker.errors:
        by_cell.setdefault(entry["cell"], []).append(entry)
    for sheet, formulas in sheets.formulas.items():
        values = computed.get(sheet, {})
        for (r, c), formula in formulas.items():
            value = values.get((r, c))
            if not _is_error_value(value):
                continue
            known = by_cell.get(cell_label(sheet, r, c))
            if known is None:
                checker._add(
                    checker.errors, sheet, r, c, formula, f"evaluates to {value}", value
                )
            else:
                for entry in known:
                    entry["computed"] = value


def _reported_values(
    checker: _Checker,
    sheets: _Sheets,
    computed: Dict[str, Dict[Tuple[int, int], Any]],
) -> Tuple[Dict[str, Any], int]:
    """Up to :data:`MAX_VALUES` computed values and how many there are in all.

    When there are more, the ones a person reports come first: aggregate
    formulas, rows labelled as totals and summary sheets, then later sheets.
    """
    cells = [
        (index, sheet, r, c)
        for index, (sheet, formulas) in enumerate(sheets.formulas.items())
        for (r, c) in formulas
        if computed.get(sheet, {}).get((r, c)) is not None
    ]
    total = len(cells)
    if total > MAX_VALUES:
        total_rows = {
            sheet: {
                r
                for (r, _), value in values.items()
                if isinstance(value, str)
                and not value.startswith("=")
                and _TOTAL_LABEL.search(value)
            }
            for sheet, values in sheets.cells.items()
        }

        def rank(cell: Tuple[int, str, int, int]) -> Tuple[int, int, int, int]:
            index, sheet, r, c = cell
            score = (
                2 * ((sheet, r, c) in checker.aggregates)
                + 2 * (r in total_rows[sheet])
                + bool(_SUMMARY_SHEET.search(sheet))
            )
            return (-score, -index, r, c)

        cells = sorted(sorted(cells, key=rank)[:MAX_VALUES])
    return {
        cell_label(sheet, r, c): _jsonable(computed[sheet][(r, c)])
        for _, sheet, r, c in cells
    }, total


#: What ``summary`` says for a workbook of typed numbers.
NO_FORMULAS = (
    "No formulas: every total here is a typed value. Rewrite totals as "
    "formulas over the data, or state that they were computed in Python."
)
#: Why the formulas were not computed, when the caller passes no recalculator.
NOT_REQUESTED = "recalculation was not requested"
NO_LIBREOFFICE = "LibreOffice is not installed"
#: What to do when there are no computed values to report from.
NOT_RECALCULATED_NEXT = (
    "Formulas were not recalculated ({reason}). Compute the same figures with "
    "pandas in run_python and report those; say the workbook's formulas were "
    "not recalculated."
)


def check_workbook(
    path: Path,
    *,
    opener: Callable[[Path], IO[bytes]],
    recalculator: Optional[Callable[[Path], Path]] = None,
    not_recalculated: str = NOT_REQUESTED,
) -> Dict[str, Any]:
    """Recalculate (when given a recalculator) and check every formula in *path*.

    Args:
        path: An existing .xlsx or .xlsm file.
        opener: Opens *path* for reading bytes. The tool passes the skill
            context's ``open_read``, so the file is read only if the agent
            may read it.
        recalculator: Returns a recalculated copy of *path*, normally
            :func:`recalculate`. ``None`` runs the static checks only.
        not_recalculated: Why there is no recalculator, for the result.

    Returns:
        The tool result: ``status`` (``success``, ``errors_found`` or
        ``no_formulas``), ``ok``, errors, warnings, circular references,
        computed values and whether a recalculation happened.

    Raises:
        NotAWorkbook: *path* is not a workbook openpyxl can open.
        WorkbookTooLarge: *path* is over the size or cell limit.
    """
    with opener(path) as stream:
        _refuse_if_too_large(path, stream)
        wb = _load(stream)
        try:
            sheets = _Sheets(wb)
            sheet_info = [
                {
                    "name": ws.title,
                    "rows": ws.max_row,
                    "columns": ws.max_column,
                    "formulas": len(sheets.formulas[ws.title]),
                    "charts": len(getattr(ws, "_charts", [])),
                    "images": len(getattr(ws, "_images", [])),
                }
                for ws in wb.worksheets
            ]
        finally:
            wb.close()

    checker = _Checker(sheets)
    checker.run()
    cycles = _cycles(checker.deps)
    for cycle in cycles:
        sheet, row, col = cycle[0]
        checker._add(
            checker.errors,
            sheet,
            row,
            col,
            sheets.formulas[sheet][(row, col)],
            "is part of a circular reference: "
            + " -> ".join(cell_label(*node) for node in cycle)
            + ". Each of these formulas depends on itself.",
        )

    formula_count = sum(len(f) for f in sheets.formulas.values())
    result: Dict[str, Any] = {
        "status": "success",
        "ok": True,
        "file_path": str(path),
        "sheets": sheet_info,
        "formula_count": formula_count,
        "recalculated": False,
    }

    computed: Optional[Dict[str, Dict[Tuple[int, int], Any]]] = None
    reason = not_recalculated
    if formula_count and recalculator is not None:
        try:
            computed = _computed_values(recalculator(path))
            result["recalculated"] = True
            result["recalculated_with"] = "LibreOffice"
        except (RuntimeError, OSError) as e:
            logger.warning("Workbook recalculation failed for %s: %s", path, e)
            reason = "the recalculation failed"
            result["recalc_error"] = (
                f"Recalculation failed: {e}. Static checks ran; values were not "
                "computed."
            )

    if computed is None and formula_count:
        with opener(path) as stream:
            cached = _computed_values(stream)
        if any(
            (r, c) in cached.get(sheet, {})
            for sheet, formulas in sheets.formulas.items()
            for (r, c) in formulas
        ):
            computed = cached
            result["values_source"] = (
                "cached values saved in the file by the last program that "
                "calculated it; they are stale if a cell was edited since"
            )
        else:
            result["next"] = NOT_RECALCULATED_NEXT.format(reason=reason)

    _settle(checker, sheets, computed)
    if computed is not None:
        values, total = _reported_values(checker, sheets, computed)
        result["computed_values"] = values
        if total > len(values):
            result["computed_values_note"] = (
                f"{len(values)} of {total} shown: totals and aggregate "
                "formulas first, then later sheets."
            )

    errors, warnings = checker.errors, checker.warnings
    result.update(
        {
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors[:MAX_REPORTED],
            "warnings": warnings[:MAX_REPORTED],
            "circular_references": [
                [cell_label(*node) for node in cycle] for cycle in cycles
            ],
        }
    )
    if len(errors) > MAX_REPORTED or len(warnings) > MAX_REPORTED:
        result["truncated"] = (
            f"Showing the first {MAX_REPORTED} errors and warnings of "
            f"{len(errors)} and {len(warnings)}."
        )
    if not formula_count:
        typed_numbers = any(
            _is_number(value)
            for cells in sheets.cells.values()
            for value in cells.values()
        )
        if typed_numbers:
            result.update(status="no_formulas", ok=False, summary=NO_FORMULAS)
        else:
            result["summary"] = (
                "The workbook has no formulas and no numbers, so there is "
                "nothing to check."
            )
    elif errors:
        result.update(status="errors_found", ok=False)
        result["summary"] = (
            f"{len(errors)} formula error(s) and {len(warnings)} warning(s). Fix "
            "the formulas that build this workbook, save it, and run "
            "verify_workbook again before reporting any number from it."
        )
    elif warnings:
        result["summary"] = (
            f"No formula errors; {len(warnings)} warning(s) worth checking — each "
            "names a cell whose result may not be what was intended."
        )
    else:
        result["summary"] = f"All {formula_count} formulas passed the checks."
    return result
