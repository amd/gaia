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
  converted headless in a temporary folder, which computes every formula; the
  computed values and error values (``#REF!``, ``#DIV/0!`` ...) are read back
  from that copy. The original file is never written.
* **Static checks** — always: unknown functions and names, references to
  sheets that do not exist, literal error values, circular references,
  numeric functions over text or empty cells, SUMIFS-style ranges of different
  sizes, criteria that match no row, and arithmetic on text or empty cells.
"""

from __future__ import annotations

import fnmatch
import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from gaia.logger import get_logger

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

#: Where a formula's result is far more often wrong than the formula looks.
MAX_REPORTED = 50
MAX_VALUES = 60
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
            self.bounds[ws.title] = (ws.max_row or 1, ws.max_column or 1)
        self.names = _defined_names(wb)

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
        target = self.names.get(name.lower())
        if target is None:
            return None, (
                f"uses the name '{name}', which is not a cell reference or a "
                "defined name in this workbook — #NAME?"
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


def _tokenize(formula: str) -> Tuple[List[Any], List[_Call]]:
    """The formula's tokens and its function calls with their direct arguments."""
    from openpyxl.formula.tokenizer import Token, Tokenizer

    tokens = [t for t in Tokenizer(formula).items if t.type != Token.WSPACE]
    calls: List[_Call] = []
    stack: List[_Call] = []
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
    return tokens, calls


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
        self.deps: Dict[Tuple[str, int, int], set] = {}

    def _add(self, bucket, sheet, row, col, formula, problem, error=None):
        entry = {
            "cell": cell_label(sheet, row, col),
            "formula": formula,
            "problem": problem,
        }
        if error:
            entry["error"] = error
        bucket.append(entry)

    def run(self) -> None:
        for sheet, formulas in self.sheets.formulas.items():
            for (row, col), formula in formulas.items():
                self._check(sheet, row, col, formula)

    def _check(self, sheet: str, row: int, col: int, formula: str) -> None:
        from openpyxl.formula.tokenizer import Token, TokenizerError

        try:
            tokens, calls = _tokenize(formula)
        except TokenizerError as e:
            self._add(self.errors, sheet, row, col, formula, f"cannot be parsed: {e}")
            return

        def error(problem: str, code: Optional[str] = None) -> None:
            self._add(self.errors, sheet, row, col, formula, problem, code)

        def warn(problem: str) -> None:
            self._add(self.warnings, sheet, row, col, formula, problem)

        deps = self.deps.setdefault((sheet, row, col), set())

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
                ref, problem = self.sheets.parse_ref(token.value, sheet)
                if problem:
                    error(problem, "#NAME?" if "#NAME?" in problem else "#REF!")
                if ref is not None:
                    refs[id(token)] = ref
                    self._note_deps(deps, ref)

        self._check_arithmetic(tokens, refs, error, warn)
        for call in calls:
            self._check_call(call, refs, error, warn)

    def _note_deps(self, deps: set, ref: Ref) -> None:
        for r, c in self.sheets.formulas.get(ref.sheet, {}):
            if ref.contains(ref.sheet, r, c):
                deps.add((ref.sheet, r, c))

    def _check_arithmetic(self, tokens, refs, error, warn) -> None:
        from openpyxl.formula.tokenizer import Token

        for i, token in enumerate(tokens):
            ref = refs.get(id(token))
            if ref is None or not ref.is_cell:
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
                    error(
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
                error(f"divides by {ref.label()}, which is 0 — #DIV/0!", "#DIV/0!")
            elif in_arithmetic and isinstance(value, str) and _as_number(value) is None:
                error(
                    f"does arithmetic with {ref.label()}, which holds the text "
                    f"{value[:40]!r} — #VALUE!",
                    "#VALUE!",
                )

    def _range_profile(self, ref: Ref) -> Dict[str, Any]:
        """Counts of numbers, formulas and text in *ref* (header text excluded)."""
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
        return {
            "numbers": numbers,
            "formulas": formulas,
            "text": text,
            "body_text": body_text,
        }

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
            error(
                f"{fn} over {ref.label()} covers only empty cells, so it returns "
                "0. It probably points at the wrong column or rows."
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


def find_soffice() -> Optional[str]:
    """The LibreOffice executable, or None when it is not installed."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    system = platform.system()
    candidates: List[Path] = []
    if system == "Windows":
        for base in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        ):
            if base:
                candidates.append(
                    Path(base) / "LibreOffice" / "program" / "soffice.exe"
                )
    elif system == "Darwin":
        candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    else:
        candidates += [
            Path("/usr/bin/soffice"),
            Path("/usr/lib/libreoffice/program/soffice"),
        ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def recalculate(path: Path, soffice: str, workdir: Path) -> Path:
    """Convert a copy of *path* with LibreOffice, which computes every formula.

    Returns the recalculated copy inside *workdir*. A private LibreOffice
    profile keeps a running LibreOffice window from swallowing the request,
    and is set to recalculate on load: by default Calc keeps the values cached
    in an Excel-saved file, which would hide a formula broken since.

    Raises:
        RuntimeError: LibreOffice did not produce the copy.
    """
    source = workdir / ("source" + path.suffix.lower())
    shutil.copyfile(path, source)
    out_dir = workdir / "out"
    profile_dir = workdir / "profile"
    (profile_dir / "user").mkdir(parents=True, exist_ok=True)
    (profile_dir / "user" / "registrymodifications.xcu").write_text(
        _ALWAYS_RECALC_PROFILE, encoding="utf-8"
    )
    profile = profile_dir.resolve().as_uri()
    cmd = [
        soffice,
        f"-env:UserInstallation={profile}",
        "--headless",
        "--norestore",
        "--nologo",
        "--convert-to",
        "xlsx",
        "--outdir",
        str(out_dir),
        str(source),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=RECALC_TIMEOUT_S,
            check=False,
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


def _computed_values(path: Path) -> Dict[str, Dict[Tuple[int, int], Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
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


#: Static guesses a computed value can overrule: whether text or an empty cell
#: actually reaches the arithmetic depends on branches (IF, IFERROR) the static
#: pass does not evaluate.
_EVALUATION_DEPENDENT = frozenset({"#VALUE!", "#DIV/0!"})


def _reconcile(
    checker: _Checker,
    sheets: _Sheets,
    computed: Dict[str, Dict[Tuple[int, int], Any]],
) -> Dict[str, Any]:
    """Fold computed values into the findings; return a sample of them."""
    by_cell: Dict[str, List[Dict[str, Any]]] = {}
    for entry in checker.errors:
        by_cell.setdefault(entry["cell"], []).append(entry)
    values: Dict[str, Any] = {}
    overruled: List[Tuple[Dict[str, Any], Any]] = []
    for sheet, formulas in sheets.formulas.items():
        for (r, c), formula in formulas.items():
            value = computed.get(sheet, {}).get((r, c))
            label = cell_label(sheet, r, c)
            is_error = isinstance(value, str) and value in ERROR_VALUES
            if is_error and label not in by_cell:
                checker._add(
                    checker.errors, sheet, r, c, formula, f"evaluates to {value}", value
                )
            for entry in by_cell.get(label, []):
                if is_error:
                    entry["computed"] = value
                elif entry.get("error") in _EVALUATION_DEPENDENT:
                    overruled.append((entry, value))
            if len(values) < MAX_VALUES:
                values[label] = _jsonable(value)
    for entry, value in overruled:
        checker.errors.remove(entry)
        entry["problem"] += (
            f"; the recalculation computed {_jsonable(value)!r} "
            "without an error, so check whether that branch can be reached"
        )
        entry.pop("error", None)
        checker.warnings.append(entry)
    return values


def check_workbook(path: Path, *, recalc: bool = True) -> Dict[str, Any]:
    """Recalculate (when possible) and check every formula in *path*.

    Args:
        path: An existing .xlsx or .xlsm file.
        recalc: Try LibreOffice recalculation. Static checks always run.

    Returns:
        The tool result: errors, warnings, circular references, computed
        values and whether a recalculation happened.
    """
    from openpyxl import load_workbook

    wb = load_workbook(path)
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
        "file_path": str(path),
        "sheets": sheet_info,
        "formula_count": formula_count,
        "recalculated": False,
    }

    computed: Optional[Dict[str, Dict[Tuple[int, int], Any]]] = None
    soffice = find_soffice() if recalc and formula_count else None
    if formula_count and not recalc:
        result["recalc_note"] = "Recalculation was not requested; static checks only."
    elif formula_count and soffice is None:
        result["recalc_note"] = (
            "LibreOffice is not installed, so the formulas were not computed. "
            "Static checks ran; an error that only shows in a computed value "
            "can still be missed."
        )
    elif soffice is not None:
        try:
            with tempfile.TemporaryDirectory(prefix="gaia-wb-") as tmp:
                computed = _computed_values(recalculate(path, soffice, Path(tmp)))
            result["recalculated"] = True
            result["recalculated_with"] = "LibreOffice"
        except (RuntimeError, OSError) as e:
            logger.warning("Workbook recalculation failed for %s: %s", path, e)
            result["recalc_error"] = (
                f"Recalculation failed: {e}. Static checks ran; values were not "
                "computed."
            )

    if computed is None and formula_count:
        cached = _computed_values(path)
        if any(
            (r, c) in cached.get(sheet, {})
            for sheet, formulas in sheets.formulas.items()
            for (r, c) in formulas
        ):
            computed = cached
            result["values_source"] = (
                "cached values saved in the file by the last program that "
                "calculated it"
            )

    if computed is not None:
        result["computed_values"] = _reconcile(checker, sheets, computed)

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
        result["summary"] = (
            "The workbook has no formulas, so there is nothing to check."
        )
    elif errors:
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
