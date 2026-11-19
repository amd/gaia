# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Find named time series in a data file and inventory a folder of data files.

:func:`extract_series` answers "give me Net revenue and Operating costs for the
last 12 quarters from this workbook": it matches each requested label to a row
or column (echoing the label it matched, refusing to guess between two close
ones), reads the values exactly as stored, aligns them on one period axis and
cites every value's cell.

:func:`inventory_folder` answers "what is in this folder and which file is
newest": per sheet the layout, header rows, units and period coverage; per
dataset the files ranked by vintage, with older copies named as such. Its
result is cut to :data:`INVENTORY_BUDGET_CHARS` and says what was cut.

Both read time series only. A table by category or row-level records gets an
error that says so and points at ``run_python``.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from gaia.logger import get_logger
from gaia.skills.context import open_read

from .periods import (
    FREQ_NAMES,
    Period,
    PeriodSpec,
    clean_label,
    date_as_period,
    detect_dayfirst,
    parse_period,
    parse_period_spec,
    select_periods,
)
from .tables import (
    DATA_SUFFIXES,
    NARRATIVE_SUFFIXES,
    SAMPLE_ROWS,
    UNREADABLE_SPREADSHEET_SUFFIXES,
    CellNumber,
    DataFileError,
    Layout,
    Table,
    analyze_layout,
    cell_ref,
    column_letter,
    footnote_key,
    is_text,
    open_tables,
    read_number,
    units_from_text,
    units_scale,
)

log = get_logger(__name__)

#: Rows scanned per table when reading a period column or label list in full.
MAX_SCAN_ROWS = 2_000_000
#: Distinct values remembered per text column of a long-format table.
MAX_DISTINCT = 20_000
#: Candidates listed when a label is ambiguous or not found.
MAX_CANDIDATES = 6
#: Characters an inventory result may take as JSON. The agent cuts a tool
#: result at about 20,000 on a small context, dropping list items from the end.
INVENTORY_BUDGET_CHARS = 18_000
#: Files one inventory call looks at before it asks for a narrower folder.
MAX_WALK_FILES = 5_000
#: What to do with a table these tools do not read.
NOT_A_TIME_SERIES = (
    "These tools read time series only (one value per period). For a table by "
    "category (Region, Revenue) or row-level records, read the file with "
    "run_python (pandas) or the data-explore skill."
)


class SeriesLookupError(ValueError):
    """A lookup that could not be answered exactly; ``payload`` says why."""

    def __init__(self, message: str, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.payload = payload or {}


# --------------------------------------------------------------------------- #
# Label matching
# --------------------------------------------------------------------------- #


def normalize_label(text: Any) -> str:
    """Lower-case words of a label, without footnote markers or a units suffix."""
    t = clean_label(str(text or ""))
    # A trailing units parenthetical is not part of the name: "Revenue ($ millions)".
    m = re.search(r"\(([^()]*)\)\s*$", t)
    if m and units_from_text(f"({m.group(1)})"):
        t = t[: m.start()]
    t = t.lower().replace("&", " and ")
    t = re.sub(r"[^\w%.]+", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(norm: str) -> List[str]:
    out = []
    for tok in norm.split():
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


@dataclass
class Candidate:
    """A row, column or long-format value that could be a requested series."""

    table: str
    label: str
    kind: str  # row | column | value
    section: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None
    units: Optional[str] = None
    norm: str = ""
    path_norm: str = ""

    def __post_init__(self) -> None:
        self.norm = normalize_label(self.label)
        self.path_norm = (
            f"{normalize_label(self.section)} > {self.norm}"
            if self.section
            else self.norm
        )

    @property
    def cell(self) -> str:
        if self.kind == "row" and self.row is not None and self.col is not None:
            return cell_ref(self.row, self.col)
        if self.kind == "column" and self.col is not None:
            return f"column {column_letter(self.col)}"
        if self.kind == "value" and self.col is not None:
            return f"column {column_letter(self.col)} = {self.label!r}"
        return ""

    def describe(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"label": self.label, "sheet": self.table}
        if self.section:
            out["section"] = self.section
        if self.cell:
            out["cell"] = self.cell
        return out


@dataclass
class Match:
    candidate: Candidate
    how: str  # exact | exact_path | contains | fuzzy
    score: float


def _score(query: str, cand: Candidate) -> Tuple[str, float]:
    """How well *cand* matches *query*: (kind, score). Kind '' is no match."""
    parts = [p.strip() for p in re.split(r"\s*>\s*", query) if p.strip()]
    q = normalize_label(parts[-1])
    full = normalize_label(query.replace(">", " "))
    if len(parts) > 1:
        section = normalize_label(" ".join(parts[:-1]))
        if cand.section and normalize_label(cand.section) == section and cand.norm == q:
            return "exact_path", 1.0
        q_tokens = set(_tokens(full))
        if q_tokens and q_tokens <= set(_tokens(cand.path_norm)):
            return "contains", 0.8
        return "", 0.0
    if not q:
        return "", 0.0
    if cand.norm == q:
        return "exact", 1.0
    q_tokens = set(_tokens(q))
    l_tokens = set(_tokens(cand.norm))
    if q_tokens and l_tokens and q_tokens == l_tokens:
        return "exact", 0.99
    if q_tokens and q_tokens <= l_tokens:
        return "contains", 0.6 + 0.3 * len(q_tokens) / max(len(l_tokens), 1)
    ratio = difflib.SequenceMatcher(None, q, cand.norm).ratio()
    jacc = len(q_tokens & l_tokens) / max(len(q_tokens | l_tokens), 1)
    return "fuzzy", max(ratio, jacc)


def resolve_label(
    query: str, candidates: Sequence[Candidate]
) -> Tuple[Optional[Match], Dict[str, Any]]:
    """Pick the one candidate *query* names, or explain why there is none.

    Exact wins over contains wins over fuzzy. Two exact matches, or two
    "contains" matches with no exact one, are ambiguous; a fuzzy match is only
    taken when it is close (>= 0.88) and clearly ahead of the next (>= 0.08).

    Returns:
        ``(match, {})`` or ``(None, problem)`` where problem has ``status``
        ``ambiguous`` or ``not_found`` and the candidates to choose from.
    """
    scored = []
    for cand in candidates:
        how, score = _score(query, cand)
        if how:
            scored.append(Match(cand, how, score))
    scored.sort(key=lambda m: -m.score)

    def listing(ms: Iterable[Match]) -> List[Dict[str, Any]]:
        out = []
        seen = set()
        for m in ms:
            key = (
                m.candidate.table,
                m.candidate.label,
                m.candidate.section,
                m.candidate.cell,
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {**m.candidate.describe(), "match": m.how, "score": round(m.score, 2)}
            )
            if len(out) >= MAX_CANDIDATES:
                break
        return out

    for tier in (("exact_path",), ("exact",), ("contains",)):
        hits = [m for m in scored if m.how in tier]
        if tier == ("exact",):
            top = [m for m in hits if m.score == 1.0] or hits
            hits = top
        if len(hits) == 1:
            return hits[0], {}
        if len(hits) > 1:
            return None, {
                "query": query,
                "status": "ambiguous",
                "candidates": listing(hits),
                "hint": _ambiguity_hint(hits),
            }
    fuzzy = [m for m in scored if m.how == "fuzzy"]
    if (
        fuzzy
        and fuzzy[0].score >= 0.88
        and (len(fuzzy) == 1 or fuzzy[0].score - fuzzy[1].score >= 0.08)
    ):
        return fuzzy[0], {}
    if fuzzy and fuzzy[0].score >= 0.88:
        return None, {
            "query": query,
            "status": "ambiguous",
            "candidates": listing(fuzzy[:MAX_CANDIDATES]),
            "hint": "Several labels are equally close. Repeat the call with the exact label.",
        }
    nearest = sorted(
        candidates,
        key=lambda c: -difflib.SequenceMatcher(
            None, normalize_label(query), c.norm
        ).ratio(),
    )
    return None, {
        "query": query,
        "status": "not_found",
        "nearest": [
            {
                **c.describe(),
                "similarity": round(
                    difflib.SequenceMatcher(
                        None, normalize_label(query), c.norm
                    ).ratio(),
                    2,
                ),
            }
            for c in _unique(nearest)[:MAX_CANDIDATES]
        ],
    }


def _unique(cands: Sequence[Candidate]) -> List[Candidate]:
    seen, out = set(), []
    for c in cands:
        key = (c.table, c.label, c.section, c.cell)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _ambiguity_hint(hits: Sequence[Match]) -> str:
    labels = {m.candidate.label for m in hits}
    sheets = {m.candidate.table for m in hits}
    sections = {m.candidate.section for m in hits if m.candidate.section}
    if len(labels) == 1 and len(sheets) > 1:
        return "The same label is on several sheets. Repeat the call with sheet= set."
    if len(labels) == 1 and sections:
        example = next(m.candidate for m in hits if m.candidate.section)
        return (
            "The same label sits under different sections. Name the section, e.g. "
            f"'{example.section} > {example.label}'."
        )
    return "Repeat the call with the exact label of the series you mean."


# --------------------------------------------------------------------------- #
# Indexing a table
# --------------------------------------------------------------------------- #


@dataclass
class TableIndex:
    """Layout plus every candidate series of one table."""

    table: Table
    layout: Layout
    candidates: List[Candidate] = field(default_factory=list)
    distinct: Dict[int, Dict[str, int]] = field(default_factory=dict)
    rows_scanned: int = 0
    truncated: bool = False
    dayfirst: Optional[bool] = None
    #: Wide sheets: label rows with no number under any period ({row: label}).
    #: Section headings, or formula rows the file holds no results for.
    unvalued_rows: Dict[int, str] = field(default_factory=dict)
    #: Long sheets: headed columns with no value in any row ({column: header}).
    unvalued_cols: Dict[int, str] = field(default_factory=dict)


def _label_of(row: Sequence[Any], cols: Sequence[int]) -> str:
    parts = []
    for c in cols:
        v = row[c] if c < len(row) else None
        if v is None:
            continue
        text = str(v).strip()
        if text and text not in parts and is_text(v):
            parts.append(text)
    return " ".join(parts)


def index_table(table: Table) -> TableIndex:
    """Lay out *table* and list the series it holds (one streaming pass)."""
    grid = table.sample(SAMPLE_ROWS)
    layout = analyze_layout(table, [list(r) for r in grid])
    idx = TableIndex(table, layout)
    if layout.kind == "unknown":
        return idx
    if layout.kind == "wide":
        section: Optional[str] = None
        for number, row in table.rows():
            if number < layout.first_data_row or number == layout.units_row:
                continue
            idx.rows_scanned += 1
            if idx.rows_scanned > MAX_SCAN_ROWS:
                idx.truncated = True
                break
            label = _label_of(row, layout.label_cols)
            numbers = any(
                read_number(row[c]).value is not None
                for c in layout.period_cols
                if c < len(row)
            )
            if label and not numbers:
                if _looks_like_note(label):
                    continue
                if len(idx.unvalued_rows) < 5000:
                    idx.unvalued_rows[number] = label
                section = label
                continue
            if label and numbers:
                units = None
                if layout.units_col is not None and layout.units_col < len(row):
                    units = (
                        str(row[layout.units_col]).strip()
                        if row[layout.units_col]
                        else None
                    )
                units = units or units_from_text(label)
                idx.candidates.append(
                    Candidate(
                        table.name,
                        label,
                        "row",
                        section=section,
                        row=number,
                        col=_first_label_col(row, layout.label_cols),
                        units=units,
                    )
                )
        return idx
    # columns / stacked
    idx.unvalued_cols = {
        c: header
        for c, header in layout.column_headers.items()
        if c not in layout.numeric_cols
        and c not in layout.dimension_cols
        and c != layout.units_col
    }
    for c in layout.numeric_cols:
        header = layout.column_headers.get(c, f"column {column_letter(c)}")
        idx.candidates.append(
            Candidate(
                table.name,
                header,
                "column",
                col=c,
                row=layout.header_rows[-1] if layout.header_rows else None,
                units=units_from_text(header),
            )
        )
    sample_periods = [
        r[layout.period_col] if layout.period_col < len(r) else None for r in grid
    ]
    idx.dayfirst = detect_dayfirst(sample_periods)
    if layout.kind == "stacked":
        idx.distinct = {c: {} for c in layout.dimension_cols}
        for number, row in table.rows():
            if number < layout.first_data_row:
                continue
            idx.rows_scanned += 1
            if idx.rows_scanned > MAX_SCAN_ROWS:
                idx.truncated = True
                break
            for c in layout.dimension_cols:
                v = row[c] if c < len(row) else None
                if v is None:
                    continue
                key = str(v).strip()
                bucket = idx.distinct[c]
                if key in bucket:
                    bucket[key] += 1
                elif len(bucket) < MAX_DISTINCT:
                    bucket[key] = 1
        for c, values in idx.distinct.items():
            for value in values:
                idx.candidates.append(Candidate(table.name, value, "value", col=c))
    return idx


def _first_label_col(row: Sequence[Any], cols: Sequence[int]) -> Optional[int]:
    for c in cols:
        if c < len(row) and is_text(row[c]):
            return c
    return cols[0] if cols else None


def _looks_like_note(text: str) -> bool:
    return (
        bool(
            re.match(
                r"^\s*(?:notes?|source|sources|footnotes?)\b|^\s*\(?[a-z0-9]{1,2}[).]\s|^\s*[*†‡]",
                text,
                re.I,
            )
        )
        or len(text) > 120
    )


# --------------------------------------------------------------------------- #
# Reading values
# --------------------------------------------------------------------------- #


@dataclass
class Point:
    value: Optional[float]
    cell: str
    raw: Any
    marker: Optional[str]
    percent: bool
    blank: bool


@dataclass
class SeriesData:
    query: str
    match: Match
    index: TableIndex
    points: Dict[Period, Point] = field(default_factory=dict)
    other_points: Dict[Period, Point] = field(default_factory=dict)
    duplicates: List[str] = field(default_factory=list)
    filter_desc: str = ""
    units: Optional[str] = None


def _point(num: CellNumber, cell: str) -> Point:
    return Point(num.value, cell, num.raw, num.marker, num.percent, num.blank)


def _period_reader(idx: TableIndex):
    cache: Dict[Any, Optional[Period]] = {}
    freq = idx.layout.freq

    def read(value: Any) -> Optional[Period]:
        key = value if not isinstance(value, float) else repr(value)
        try:
            if key in cache:
                return cache[key]
        except TypeError:
            key = repr(value)
            if key in cache:
                return cache[key]
        p = parse_period(value, bare_year_numbers=True, dayfirst=idx.dayfirst)
        if p is not None and p.freq == "D" and freq == "M":
            p = Period("M", p.day.year, p.day.month)
        elif p is not None and p.freq == "D" and freq in ("Q", "A"):
            p = date_as_period(p.day, freq) or p
        if len(cache) < 100_000:
            cache[key] = p
        return p

    return read


def _filter_items(filters: Any) -> List[Tuple[str, str]]:
    """``"region=East; channel=Online"`` -> [("region", "East"), ...]."""
    if not filters:
        return []
    if isinstance(filters, dict):
        return [(str(k), str(v)) for k, v in filters.items()]
    items = []
    for part in re.split(r"[;\n]", str(filters)):
        if "=" in part:
            k, v = part.split("=", 1)
            items.append((k.strip(), v.strip()))
        elif part.strip():
            raise SeriesLookupError(
                f"filters entry {part.strip()!r} is not 'column=value'."
            )
    return items


def _parse_filters(filters: Any, idx: TableIndex) -> Dict[int, str]:
    """``"region=East; channel=Online"`` -> {column: normalized value}."""
    items = _filter_items(filters)
    out: Dict[int, str] = {}
    headers = idx.layout.column_headers
    for key, value in items:
        nk = normalize_label(key)
        cols = [c for c, h in headers.items() if normalize_label(h) == nk]
        if not cols:
            raise SeriesLookupError(
                f"filters names column {key!r}, which sheet {idx.table.name!r} does not have. "
                f"Its columns: {', '.join(headers.values())}."
            )
        out[cols[0]] = normalize_label(value)
    return out


def _period_columns(idx: TableIndex, filters: Any) -> Tuple[Dict[int, Period], str]:
    """The columns to read from a wide sheet, one per period, and a note.

    A period that heads several columns ("2024" under "Actual" and under
    "Budget") is never picked silently: ``filters="header=Actual"`` chooses by
    the text over or under the columns, ``filters="column=D"`` by letter.

    Raises:
        SeriesLookupError: status ``ambiguous``, listing the choices.
    """
    layout = idx.layout
    sheet = idx.table.name
    banner = layout.banner_text
    groups = list(dict.fromkeys(banner[c] for c in sorted(banner) if banner[c]))
    items = _filter_items(filters)

    def chosen(c: int) -> bool:
        for key, value in items:
            if normalize_label(key) == "column":
                if value.strip().upper() != column_letter(c):
                    return False
                continue
            want = set(_tokens(normalize_label(value)))
            if not want or not want <= set(_tokens(normalize_label(banner.get(c)))):
                return False
        return True

    cols = dict(layout.period_cols)
    note = ""
    by_column = any(normalize_label(k) == "column" for k, _ in items)
    if items and not groups and not by_column:
        note = (
            f"filters do not apply to sheet {sheet!r} (periods across the columns, "
            "no header groups) and were ignored"
        )
    elif items:
        picked = {c: p for c, p in cols.items() if chosen(c)}
        if not picked:
            raise SeriesLookupError(
                f"filters={filters!r} matches no column of sheet {sheet!r}. "
                + (
                    "Its column groups: " + ", ".join(repr(g) for g in groups) + "."
                    if groups
                    else "Choose a column by letter, e.g. filters='column="
                    + column_letter(min(cols))
                    + "'."
                ),
                {"status": "ambiguous", "choices": groups},
            )
        names = {p.label for p in picked.values()}
        loose = sorted(
            c
            for c, p in cols.items()
            if c not in picked and not banner.get(c) and p.label not in names
        )
        if loose and not by_column:
            picked.update({c: cols[c] for c in loose})
            note = (
                f"sheet {sheet!r}: column(s) {', '.join(column_letter(c) for c in loose)} "
                "have no header group and were kept"
            )
        cols = picked
    by_label: Dict[str, List[int]] = {}
    for c in sorted(cols):
        by_label.setdefault(cols[c].label, []).append(c)
    clash = {lab: cs for lab, cs in by_label.items() if len(cs) > 1}
    if not clash:
        return cols, note
    described = {
        lab: [{"column": column_letter(c), "header": banner.get(c) or None} for c in cs]
        for lab, cs in clash.items()
    }
    lab, cs = next(iter(clash.items()))
    listing = ", ".join(
        f"{column_letter(c)}" + (f" {banner[c]!r}" if banner.get(c) else "") for c in cs
    )
    named = list(dict.fromkeys(banner[c] for c in cs if banner.get(c)))
    if len(named) == len(cs):
        how = "Say which to read: repeat the call with " + " or ".join(
            f"filters='header={g}'" for g in named[:4]
        )
    else:
        how = (
            "Nothing in the header tells them apart. Repeat the call with "
            f"filters='column={column_letter(cs[0])}' for the column you mean, or "
            "ask the user"
        )
    raise SeriesLookupError(
        f"Sheet {sheet!r} has more than one column for {lab} ({listing}). {how}.",
        {
            "status": "ambiguous",
            "ambiguous_periods": described,
            "choices": named or [column_letter(c) for c in cs],
        },
    )


def read_values(selected: List[SeriesData], filters: Any = None) -> List[str]:
    """Fill each series' points with one pass per table. Returns notes."""
    notes: List[str] = []
    by_table: Dict[int, List[SeriesData]] = {}
    for s in selected:
        by_table.setdefault(id(s.index), []).append(s)
    for group in by_table.values():
        idx = group[0].index
        layout = idx.layout
        if layout.kind == "wide":
            # Two labels may name one row: each gets the row's values.
            wanted: Dict[int, List[SeriesData]] = {}
            for s in group:
                wanted.setdefault(s.match.candidate.row, []).append(s)
            period_cols, note = _period_columns(idx, filters)
            if note:
                notes.append(note)
            for number, row in idx.table.rows():
                if number not in wanted:
                    continue
                for s in wanted[number]:
                    for c in sorted(period_cols):
                        v = row[c] if c < len(row) else None
                        s.points[period_cols[c]] = _point(
                            read_number(v), cell_ref(number, c)
                        )
                    for c, p in layout.other_period_cols.items():
                        v = row[c] if c < len(row) else None
                        s.other_points[p] = _point(read_number(v), cell_ref(number, c))
                if number >= max(wanted):
                    break
            continue
        flt = _parse_filters(filters, idx)
        read_period = _period_reader(idx)
        pc = layout.period_col
        value_col = _value_column(idx)
        skipped = 0
        dim_seen: Dict[int, set] = {c: set() for c in layout.dimension_cols}
        for number, row in idx.table.rows():
            if number < layout.first_data_row:
                continue
            p = read_period(row[pc] if pc < len(row) else None)
            if p is None:
                if any(v is not None for v in row):
                    skipped += 1
                continue
            if flt and any(
                normalize_label(row[c] if c < len(row) else "") != v
                for c, v in flt.items()
            ):
                continue
            for s in group:
                cand = s.match.candidate
                if cand.kind == "column":
                    col = cand.col
                else:
                    v = row[cand.col] if cand.col < len(row) else None
                    if v is None or str(v).strip() != cand.label:
                        continue
                    col = value_col
                    if col is None:
                        continue
                for c in layout.dimension_cols:
                    if (
                        c != cand.col
                        and c < len(row)
                        and row[c] is not None
                        and len(dim_seen[c]) < 50
                    ):
                        dim_seen[c].add(str(row[c]).strip())
                num = read_number(row[col] if col < len(row) else None)
                point = _point(num, cell_ref(number, col))
                target = s.points if p.freq == layout.freq else s.other_points
                if p in target:
                    old = target[p]
                    if old.value != point.value:
                        s.duplicates.append(
                            f"{p.label}: {old.cell}={old.raw!r} and {point.cell}={point.raw!r}"
                        )
                    continue
                target[p] = point
            if layout.units_col is not None:
                for s in group:
                    if (
                        s.units is None
                        and layout.units_col < len(row)
                        and row[layout.units_col]
                    ):
                        cand = s.match.candidate
                        if cand.kind == "column" or (
                            cand.col < len(row)
                            and str(row[cand.col]).strip() == cand.label
                        ):
                            s.units = str(row[layout.units_col]).strip()
        if skipped:
            notes.append(
                f"sheet {idx.table.name!r}: {skipped} row(s) without a readable period were skipped"
            )
        for s in group:
            if s.duplicates:
                dims = {
                    layout.column_headers.get(c, column_letter(c)): sorted(vals)[:8]
                    for c, vals in dim_seen.items()
                    if len(vals) > 1
                }
                found = (
                    f"{s.match.candidate.label!r} has more than one value for the "
                    f"same period ({'; '.join(s.duplicates[:3])}). "
                )
                if dims:
                    name, values = next(iter(dims.items()))
                    fix = (
                        f"If another column splits the table ({dims}), repeat the "
                        f"call with filters, e.g. filters='{name}={values[0]}'. If "
                        "the rows are single records (orders, transactions), this "
                        "is not a time series: total them per period with "
                        "run_python (pandas) or the data-explore skill."
                    )
                else:
                    fix = (
                        "The rows look like single records (orders, transactions), "
                        "not a time series. " + NOT_A_TIME_SERIES
                    )
                raise SeriesLookupError(
                    found + fix,
                    {"query": s.query, "status": "duplicate_periods", "split_by": dims},
                )
    return notes


def _value_column(idx: TableIndex) -> Optional[int]:
    """The numeric column of a long-format table."""
    cols = idx.layout.numeric_cols
    if len(cols) == 1:
        return cols[0]
    named = [
        c
        for c in cols
        if re.search(
            r"^(?:value|values|amount|obs_?value|val|observation|data|number)$",
            idx.layout.column_headers.get(c, ""),
            re.I,
        )
    ]
    if len(named) == 1:
        return named[0]
    return None


# --------------------------------------------------------------------------- #
# extract_series
# --------------------------------------------------------------------------- #


def _split_labels(labels: Any) -> List[str]:
    if labels is None:
        return []
    if isinstance(labels, str):
        parts = re.split(r"[;\n]", labels)
    else:
        parts = [str(x) for x in labels]
    return [p.strip() for p in parts if p and p.strip()]


def _orientation_ok(idx: TableIndex, orientation: str) -> bool:
    if orientation in ("", "auto"):
        return True
    if orientation in ("periods_in_columns", "wide", "columns_are_periods"):
        return idx.layout.kind == "wide"
    if orientation in ("periods_in_rows", "long", "rows_are_periods"):
        return idx.layout.kind in ("columns", "stacked")
    raise SeriesLookupError(
        f"orientation={orientation!r} is not one of 'auto', 'periods_in_columns', 'periods_in_rows'."
    )


def extract_series(
    path: Path,
    labels: Any,
    *,
    sheet: Optional[str] = None,
    periods: Any = "last 12",
    orientation: str = "auto",
    filters: Any = None,
) -> Dict[str, Any]:
    """Find each label's series in *path* and return them on one period axis.

    Raises:
        DataFileError: the file cannot be read.
        SeriesLookupError: a label is ambiguous or missing, the sheet does not
            exist, or a series has two values for one period. ``payload``
            carries the candidates.
    """
    queries = _split_labels(labels)
    if not queries:
        raise SeriesLookupError(
            "labels is empty: name at least one series, e.g. labels=['Net revenue']."
        )
    spec = parse_period_spec(periods)
    orientation = (orientation or "auto").strip().lower()
    with open_tables(path) as tables:
        if sheet:
            chosen = [
                t for t in tables if t.name.strip().lower() == sheet.strip().lower()
            ]
            if not chosen:
                raise SeriesLookupError(
                    f"{path.name} has no sheet {sheet!r}. Sheets: {', '.join(t.name for t in tables)}.",
                    {"sheets": [t.name for t in tables]},
                )
            tables = chosen
        indexes = [index_table(t) for t in tables]
        usable = [
            i
            for i in indexes
            if i.layout.kind != "unknown" and _orientation_ok(i, orientation)
        ]
        readable = [i for i in indexes if i.layout.kind != "unknown"]
        if readable and not usable:
            raise SeriesLookupError(
                f"orientation={orientation!r} fits no sheet of {path.name}: "
                + "; ".join(f"{i.table.name!r} has {i.layout.reason}" for i in readable)
                + ". Call again with orientation='auto'.",
                {"sheets": {i.table.name: i.layout.reason for i in indexes}},
            )
        if not usable:
            stale = _uncalculated_sheets(path, indexes)
            raise SeriesLookupError(
                f"No sheet of {path.name} has period labels across a header row or "
                "down a column ("
                + "; ".join(f"{i.table.name!r}: {i.layout.reason}" for i in indexes)
                + "). "
                + (
                    f"Sheet(s) {', '.join(repr(n) for n in stale)} hold formulas "
                    "that have never been calculated, so there are no values to "
                    "read: ask the user to open and save the file in Excel or "
                    "LibreOffice, or compute from the inputs with run_python. "
                    if stale
                    else ""
                )
                + NOT_A_TIME_SERIES,
                {"sheets": {i.table.name: i.layout.reason for i in indexes}},
            )
        pool = [c for i in usable for c in i.candidates]
        by_table = {i.table.name: i for i in usable}
        problems: List[Dict[str, Any]] = []
        selected: List[SeriesData] = []
        for q in queries:
            match, problem = resolve_label(q, pool)
            if match is None:
                problems.append(problem)
                continue
            selected.append(SeriesData(q, match, by_table[match.candidate.table]))
        loose = [s for s in selected if s.match.how not in ("exact", "exact_path")]
        if loose or any(p["status"] == "not_found" for p in problems):
            # The row may exist with formulas the file holds no results for.
            stale = uncalculated_series(path, usable)

            def stale_hit(query: str, exact: bool) -> Optional[Dict[str, Any]]:
                hit = resolve_label(query, stale)[0] if stale else None
                if hit is None or (exact and hit.how not in ("exact", "exact_path")):
                    return None
                return {
                    "query": query,
                    "status": "formulas_not_calculated",
                    "label": hit.candidate.label,
                    "sheet": hit.candidate.table,
                    "cell": hit.candidate.cell,
                }

            for i, problem in enumerate(problems):
                if problem["status"] == "not_found":
                    problems[i] = stale_hit(problem["query"], False) or problem
            for entry in loose:
                found = stale_hit(entry.query, True)
                if found:
                    selected.remove(entry)
                    problems.append(found)
        if problems:
            raise SeriesLookupError(
                _problem_message(problems, path),
                {
                    "problems": problems,
                    "matched": [
                        {"query": s.query, **s.match.candidate.describe()}
                        for s in selected
                    ],
                },
            )
        notes = read_values(selected, filters)
        return _assemble(path, selected, spec, notes)


def _formula_cells(path: Path, upto: Dict[str, int]) -> Dict[str, Dict[int, set]]:
    """Per sheet, ``{row: {columns holding a formula}}`` down to ``upto[sheet]``."""
    from openpyxl import load_workbook

    out: Dict[str, Dict[int, set]] = {}
    with open_read(path, "rb") as f:
        wb = load_workbook(f, read_only=True, data_only=False)
        try:
            for ws in wb.worksheets:
                if ws.title not in upto:
                    continue
                rows = out.setdefault(ws.title, {})
                for row in ws.iter_rows(max_row=upto[ws.title]):
                    number = next(
                        (c.row for c in row if getattr(c, "row", None) is not None),
                        None,
                    )
                    cols = {
                        i
                        for i, c in enumerate(row)
                        if isinstance(getattr(c, "value", None), str)
                        and c.value.startswith("=")
                    }
                    if number is not None and cols:
                        rows[number] = cols
        finally:
            wb.close()
    return out


def uncalculated_series(path: Path, indexes: Sequence[TableIndex]) -> List[Candidate]:
    """Rows and columns whose formulas the workbook holds no results for.

    A workbook written by a program and never opened in Excel stores formulas
    without values, so its total rows read as empty.
    """
    upto: Dict[str, int] = {}
    for idx in indexes:
        if idx.table.kind != "xlsx":
            continue
        if idx.layout.kind == "wide" and idx.unvalued_rows:
            upto[idx.table.name] = min(max(idx.unvalued_rows), 100_000)
        elif idx.layout.kind in ("columns", "stacked") and idx.unvalued_cols:
            upto[idx.table.name] = idx.layout.first_data_row + 50
    if not upto:
        return []
    formulas = _formula_cells(path, upto)
    out: List[Candidate] = []
    for idx in indexes:
        rows = formulas.get(idx.table.name) or {}
        lay = idx.layout
        if lay.kind == "wide":
            for number, label in idx.unvalued_rows.items():
                if rows.get(number, set()) & set(lay.period_cols):
                    out.append(
                        Candidate(
                            idx.table.name,
                            label,
                            "row",
                            row=number,
                            col=lay.label_cols[0] if lay.label_cols else 0,
                        )
                    )
        elif lay.kind in ("columns", "stacked"):
            for c, header in idx.unvalued_cols.items():
                if any(
                    c in cols for n, cols in rows.items() if n >= lay.first_data_row
                ):
                    out.append(Candidate(idx.table.name, header, "column", col=c))
    return out


def _uncalculated_sheets(path: Path, indexes: Sequence[TableIndex]) -> List[str]:
    """Sheets with no readable layout whose top rows are formulas without results."""
    names = [
        i.table.name
        for i in indexes
        if i.table.kind == "xlsx" and i.layout.kind == "unknown"
    ]
    if not names:
        return []
    formulas = _formula_cells(path, {n: SAMPLE_ROWS for n in names})
    out = []
    for idx in indexes:
        rows = formulas.get(idx.table.name)
        if not rows or idx.table.name not in names:
            continue
        grid = idx.table.sample(SAMPLE_ROWS)
        empty = sum(
            1
            for number, cols in rows.items()
            for c in cols
            if number <= len(grid)
            and (c >= len(grid[number - 1]) or grid[number - 1][c] is None)
        )
        if empty:
            out.append(idx.table.name)
    return out


_NOT_CALCULATED = (
    "its cells are formulas that have never been calculated, so the file holds "
    "no values for it. Read the rows it adds up and compute it with run_python, "
    "or ask the user to open and save the file in Excel or LibreOffice, then "
    "call again"
)


def _problem_message(problems: List[Dict[str, Any]], path: Path) -> str:
    parts = []
    for p in problems:
        if p["status"] == "formulas_not_calculated":
            parts.append(
                f"{p['query']!r} is in {path.name} ({p['sheet']}!{p['cell']}, "
                f"{p['label']!r}), but {_NOT_CALCULATED}."
            )
        elif p["status"] == "ambiguous":
            names = "; ".join(
                f"{c['label']!r}"
                + (f" under {c['section']!r}" if c.get("section") else "")
                + f" ({c['sheet']}!{c.get('cell', '')})"
                for c in p["candidates"]
            )
            parts.append(
                f"{p['query']!r} is ambiguous — it could be {names}. {p['hint']}"
            )
        else:
            near = (
                ", ".join(
                    (
                        f"'{c['section']} > {c['label']}'"
                        if c.get("section")
                        else f"{c['label']!r}"
                    )
                    for c in p["nearest"]
                )
                or "none"
            )
            parts.append(
                f"{p['query']!r} is not in {path.name}. Nearest labels: {near}."
            )
    return " ".join(parts)


def chart_names(entries: Sequence[Dict[str, Any]]) -> List[str]:
    """One distinct legend name per series: the matched label, qualified on a clash."""
    names = [e["matched_label"] for e in entries]
    out = []
    for e, name in zip(entries, names):
        if names.count(name) > 1:
            qualifier = e.get("section") or e.get("sheet")
            name = f"{name} ({qualifier})" if qualifier else name
        while name in out:
            name += " *"
        out.append(name)
    return out


def _assemble(
    path: Path, selected: List[SeriesData], spec: PeriodSpec, notes: List[str]
) -> Dict[str, Any]:
    # Which frequency: the sheet's own, unless the request names periods only another has.
    use_other = False
    if spec.kind in ("list", "range"):
        wanted = list(spec.items) or [p for p in (spec.start, spec.end) if p]
        main_freqs = {p.freq for s in selected for p in s.points}
        other_freqs = {p.freq for s in selected for p in s.other_points}
        if wanted and all(
            w.freq in other_freqs and w.freq not in main_freqs for w in wanted
        ):
            use_other = True
    pts = [(s.other_points if use_other else s.points) for s in selected]
    with_data = {
        p for series in pts for p, pt in series.items() if pt.value is not None
    }
    if not with_data:
        raise SeriesLookupError(
            "The matched series have no numeric values: "
            + ", ".join(f"{s.match.candidate.label!r}" for s in selected)
            + ". The cells may be formulas saved without computed values; open and save the "
            "file in Excel, or read it with run_python.",
            {"status": "no_values"},
        )
    available = sorted(with_data, key=lambda p: p.sort_key)
    chosen, missing = select_periods(available, spec)
    if spec.kind == "range" or spec.kind == "list":
        # Ranges may include periods that exist but are blank for every series.
        every = {p for series in pts for p in series}
        chosen_all, _ = select_periods(sorted(every, key=lambda p: p.sort_key), spec)
        chosen = sorted(set(chosen) | set(chosen_all), key=lambda p: p.sort_key)
    if not chosen:
        span = sorted(with_data, key=lambda p: p.sort_key)
        raise SeriesLookupError(
            f"No data for {spec.describe()}; the series run {span[0].label} to {span[-1].label}.",
            {"status": "no_periods", "available": [span[0].label, span[-1].label]},
        )
    all_periods = sorted(
        {p for series in pts for p in series}, key=lambda p: p.sort_key
    )
    last_data = max(with_data, key=lambda p: p.sort_key)
    trailing = [p.label for p in all_periods if p.sort_key > last_data.sort_key]
    out_series = []
    footnotes_all: Dict[str, str] = {}
    sheets = []
    for s, series in zip(selected, pts):
        cand = s.match.candidate
        layout = s.index.layout
        footnotes_all.update(layout.footnotes)
        if cand.table not in sheets:
            sheets.append(cand.table)
        values, cells, flags, blank = [], [], [], []
        percent_all = True
        for p in chosen:
            pt = series.get(p)
            if pt is None:
                values.append(None)
                cells.append(None)
                blank.append(p.label)
                continue
            values.append(pt.value)
            cells.append(pt.cell)
            if pt.value is None:
                blank.append(p.label)
                if pt.raw not in (None, ""):
                    flags.append(
                        {
                            "period": p.label,
                            "cell": pt.cell,
                            "raw": str(pt.raw),
                            "meaning": "no value",
                        }
                    )
            else:
                percent_all = percent_all and pt.percent
                if pt.marker:
                    key = footnote_key(pt.marker)
                    flag = {
                        "period": p.label,
                        "cell": pt.cell,
                        "raw": str(pt.raw),
                        "marker": pt.marker,
                    }
                    if key and key in layout.footnotes:
                        flag["footnote"] = layout.footnotes[key]
                    flags.append(flag)
        units = s.units or cand.units or layout.units
        if units is None and percent_all and any(v is not None for v in values):
            units = "%"
        entry: Dict[str, Any] = {
            "query": s.query,
            "matched_label": cand.label,
            "match": s.match.how,
            "sheet": cand.table,
            "values": values,
            "cells": cells,
            "units": units,
        }
        scale = units_scale(units)
        if scale:
            entry["scale"] = scale
        if cand.section:
            entry["section"] = cand.section
        if cand.kind == "row":
            entry["label_cell"] = cand.cell
        else:
            entry["column"] = cand.cell
        live = [c for c in cells if c]
        if live:
            entry["cell_range"] = f"{live[0]}:{live[-1]}" if len(live) > 1 else live[0]
        if flags:
            entry["flags"] = flags
        if blank:
            entry["blank_periods"] = blank
        if s.match.how != "exact" and s.match.how != "exact_path":
            notes.append(f"{s.query!r} matched {cand.label!r} ({s.match.how})")
        out_series.append(entry)
    for s in selected:
        notes.extend(s.index.layout.axis_notes)
        if s.index.truncated:
            notes.append(
                f"sheet {s.index.table.name!r}: only the first {MAX_SCAN_ROWS:,} rows were read"
            )
        notes.extend(s.index.table.format_notes)
        if s.index.table.read_errors:
            notes.append(
                f"{s.index.table.name}: "
                + "; ".join(list(dict.fromkeys(s.index.table.read_errors))[:5])
            )
    if missing:
        notes.append("not in the file: " + ", ".join(missing))
    first, last = chosen[0].label, chosen[-1].label
    span = first if first == last else f"{first}-{last}"
    sheet_text = (
        ", ".join(f"sheet {name!r}" for name in sheets)
        if selected[0].index.table.kind == "xlsx"
        else ""
    )
    provenance = (
        f"Source: {path.name}" + (f", {sheet_text}" if sheet_text else "") + f", {span}"
    )
    units = {e["units"] for e in out_series if e.get("units")}
    if len(units) == 1:
        provenance += f"; {next(iter(units))}"
    result: Dict[str, Any] = {
        "status": "success",
        "file": str(path),
        "periods": [p.label for p in chosen],
        "periods_available": {
            "count": len(available),
            "first": available[0].label,
            "last": available[-1].label,
        },
        "frequency": FREQ_NAMES.get(chosen[0].freq, chosen[0].freq),
        "selection": spec.describe(),
        "series": out_series,
        "provenance": provenance,
        "layout": {
            i.table.name: i.layout.reason
            for i in {id(s.index): s.index for s in selected}.values()
        },
    }
    # The fields a slide-decks chart slide takes: categories, series, source.
    result["chart_input"] = {
        "categories": result["periods"],
        "series": {
            name: e["values"] for name, e in zip(chart_names(out_series), out_series)
        },
        "source": provenance,
    }
    if trailing:
        result["trailing_periods_without_data"] = trailing[:12]
    if footnotes_all:
        result["footnotes"] = footnotes_all
    if notes:
        result["notes"] = list(dict.fromkeys(notes))
    return result


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

_ORDINAL_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
}
_REVISION_RANK = [
    ("draft", 0),
    ("prelim", 1),
    ("preliminary", 1),
    ("advance", 1),
    ("flash", 1),
    ("estimate", 2),
    ("revised", 3),
    ("rev", 3),
    ("updated", 3),
    ("update", 3),
    ("final", 4),
]
_MONTH_WORD = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ASOF_RE = re.compile(
    r"(?:as\s+(?:of|at)|data\s+(?:through|to|up\s+to)|through|released?(?:\s+on)?|published|updated|"
    r"vintage|period\s+end(?:ing|ed)?|for\s+the\s+(?:quarter|period|year|month)\s+end(?:ing|ed))\s*:?\s*"
    r"(?P<when>[A-Za-z0-9 ,/.\-']{4,30})",
    re.I,
)


def filename_vintage(
    stem: str,
) -> Tuple[Optional[Period], Optional[Tuple[str, int]], str]:
    """Period, revision word and dataset key in a file name.

    "regional-output-second-quarter-2025_rev2" -> (2025Q2, ("rev", 3),
    "regional output").
    """
    text = stem.lower()
    text = re.sub(r"[_\-.]+", " ", text)
    text = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", text)
    found: List[Period] = []
    spans: List[Tuple[int, int]] = []

    def take(pattern: str, build) -> None:
        for m in re.finditer(pattern, text):
            if any(a < m.end() and m.start() < b for a, b in spans):
                continue
            p = build(m)
            if p is not None:
                found.append(p)
                spans.append((m.start(), m.end()))

    take(
        r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\s+(?:of\s+)?((?:19|20)\d{2})\b",
        lambda m: Period("Q", int(m.group(2)), _ORDINAL_WORDS[m.group(1)]),
    )
    take(
        r"\b((?:19|20)\d{2})\s*q\s*([1-4])\b",
        lambda m: Period("Q", int(m.group(1)), int(m.group(2))),
    )
    take(
        r"\bq\s*([1-4])\s*((?:19|20)\d{2}|\d{2})\b",
        lambda m: parse_period(f"Q{m.group(1)} {m.group(2)}"),
    )
    take(
        r"\b([1-4])\s*q\s*((?:19|20)\d{2}|\d{2})\b",
        lambda m: parse_period(f"{m.group(1)}Q{m.group(2)}"),
    )
    take(
        r"\b((?:19|20)\d{2})\s*h\s*([12])\b",
        lambda m: Period("H", int(m.group(1)), int(m.group(2))),
    )
    take(
        r"\bh\s*([12])\s*((?:19|20)\d{2})\b",
        lambda m: Period("H", int(m.group(2)), int(m.group(1))),
    )
    take(
        r"\b((?:19|20)\d{2})\s(0[1-9]|1[0-2])\s([0-2]\d|3[01])\b",
        lambda m: parse_period(f"{m.group(1)}-{m.group(2)}-{m.group(3)}"),
    )
    take(
        r"\b((?:19|20)\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])\b",
        lambda m: parse_period(f"{m.group(1)}-{m.group(2)}-{m.group(3)}"),
    )
    take(
        rf"\b({_MONTH_WORD})\s*((?:19|20)\d{{2}})\b",
        lambda m: parse_period(f"{m.group(1)} {m.group(2)}"),
    )
    take(
        r"\b((?:19|20)\d{2})\s(0[1-9]|1[0-2])\b",
        lambda m: Period("M", int(m.group(1)), int(m.group(2))),
    )
    take(r"\bfy\s*((?:19|20)\d{2}|\d{2})\b", lambda m: parse_period(f"FY{m.group(1)}"))
    take(r"\b((?:19|20)\d{2})\b", lambda m: Period("A", int(m.group(1))))
    hits = [(w, rank) for w, rank in _REVISION_RANK if re.search(rf"\b{w}\b", text)]
    revision: Optional[Tuple[str, int]] = (
        max(hits, key=lambda h: h[1]) if hits else None
    )
    vm = re.search(r"\b(?:v|ver|version|rev|r)\s?(\d{1,3})\b", text)
    if vm:
        revision = (f"v{vm.group(1)}", 10 + int(vm.group(1)))
    key = text
    for a, b in sorted(spans, reverse=True):
        key = key[:a] + " " + key[b:]
    key = re.sub(
        r"\b(?:v|ver|version|rev|r)\s?\d{1,3}\b|\b(?:draft|prelim|preliminary|advance|flash|estimate|revised|rev|"
        r"updated|update|final|copy|old|new|latest|current|q[1-4]|h[12]|of|quarter|first|second|third|fourth|"
        r"1st|2nd|3rd|4th)\b|\(\d+\)|\d+",
        " ",
        key,
    )
    key = re.sub(r"\s+", " ", key).strip()
    best = max(found, key=lambda p: p.sort_key) if found else None
    return best, revision, key


def _content_asof(lines: Sequence[str]) -> Optional[Period]:
    best = None
    for line in lines:
        for m in _ASOF_RE.finditer(line):
            frag = m.group("when").strip(" ,.;")
            words = frag.split()
            for n in range(min(len(words), 4), 0, -1):
                p = parse_period(
                    " ".join(words[:n]).strip(" ,."), bare_year_numbers=True
                )
                if p is not None:
                    if best is None or p.sort_key > best.sort_key:
                        best = p
                    break
    return best


@dataclass
class SheetSummary:
    name: str
    layout: Layout
    first: Optional[Period] = None
    last: Optional[Period] = None
    last_with_data: Optional[Period] = None
    count: int = 0
    labels: List[str] = field(default_factory=list)
    rows: int = 0
    truncated: bool = False
    errors: List[str] = field(default_factory=list)
    format_notes: List[str] = field(default_factory=list)


def summarize_table(table: Table) -> SheetSummary:
    """Layout, period coverage and series names of one table (streams it once)."""
    idx = index_table(table)
    layout = idx.layout
    summary = SheetSummary(
        table.name, layout, rows=idx.rows_scanned, truncated=idx.truncated
    )
    summary.errors = list(table.read_errors)
    summary.format_notes = list(table.format_notes)
    if layout.kind == "wide":
        periods = sorted(set(layout.period_cols.values()), key=lambda p: p.sort_key)
        if periods:
            summary.first, summary.last, summary.count = (
                periods[0],
                periods[-1],
                len(periods),
            )
        summary.labels = [
            f"{c.section} > {c.label}" if c.section else c.label for c in idx.candidates
        ]
        # Latest period with a number in any series row.
        rows = {c.row for c in idx.candidates}
        best = None
        cols_by_pos = sorted(
            layout.period_cols.items(), key=lambda kv: kv[1].sort_key, reverse=True
        )
        for number, row in table.rows():
            if number not in rows:
                continue
            for c, p in cols_by_pos:
                if best is not None and p.sort_key <= best.sort_key:
                    break
                if c < len(row) and read_number(row[c]).value is not None:
                    best = p
                    break
        summary.last_with_data = best
        return summary
    if layout.kind == "unknown":
        return summary
    read_period = _period_reader(idx)
    seen: set = set()
    first = last = last_data = None
    rows = 0
    pc = layout.period_col
    for number, row in table.rows():
        if number < layout.first_data_row:
            continue
        rows += 1
        if rows > MAX_SCAN_ROWS:
            summary.truncated = True
            break
        p = read_period(row[pc] if pc < len(row) else None)
        if p is None or p.freq != layout.freq:
            continue
        if len(seen) < 100_000:
            seen.add(p)
        if first is None or p.sort_key < first.sort_key:
            first = p
        if last is None or p.sort_key > last.sort_key:
            last = p
        if any(
            c < len(row) and read_number(row[c]).value is not None
            for c in layout.numeric_cols
        ):
            if last_data is None or p.sort_key > last_data.sort_key:
                last_data = p
    summary.rows = rows
    summary.first, summary.last, summary.last_with_data, summary.count = (
        first,
        last,
        last_data,
        len(seen),
    )
    summary.labels = [c.label for c in idx.candidates]
    return summary


def _sheet_dict(s: SheetSummary) -> Dict[str, Any]:
    lay = s.layout
    out: Dict[str, Any] = {"sheet": s.name, "layout": lay.kind, "how_read": lay.reason}
    if s.format_notes:
        out["read_as"] = s.format_notes
    if lay.kind == "unknown":
        if lay.title_lines or lay.notes:
            out["text"] = (lay.title_lines + lay.notes)[:5]
        return out
    groups = list(dict.fromkeys(t for t in lay.banner_text.values() if t))
    if groups:
        out["column_groups"] = groups[:12]
    out["header_rows"] = lay.header_rows
    if len(lay.header_rows) > 1:
        out["multi_row_header"] = True
    if lay.merged_header_cells:
        out["merged_header_cells"] = lay.merged_header_cells
    if lay.units:
        out["units"] = lay.units
    if lay.units_row:
        out["units_row"] = lay.units_row
    if lay.units_col is not None:
        out["units_column"] = column_letter(lay.units_col)
    if lay.freq:
        out["frequency"] = FREQ_NAMES.get(lay.freq, lay.freq)
    if s.first:
        cov = {"first": s.first.label, "last": s.last.label, "count": s.count}
        if s.last_with_data and s.last_with_data != s.last:
            cov["last_with_data"] = s.last_with_data.label
        out["periods"] = cov
    if lay.other_period_cols:
        others = sorted(set(lay.other_period_cols.values()), key=lambda p: p.sort_key)
        out["also_has"] = (
            f"{FREQ_NAMES.get(others[0].freq, others[0].freq)} columns: "
            + ", ".join(p.label for p in others[:8])
        )
    if lay.blank_columns:
        out["blank_spacer_columns"] = [column_letter(c) for c in lay.blank_columns][:20]
    if lay.title_lines:
        out["title"] = lay.title_lines[:3]
    if lay.footnotes:
        out["footnotes"] = dict(list(lay.footnotes.items())[:10])
    elif lay.notes:
        out["notes"] = lay.notes[:5]
    if s.labels:
        out["series_count"] = len(s.labels)
        out["series"] = s.labels[:15]
    if lay.kind != "wide":
        out["data_rows"] = s.rows
    if s.truncated:
        out["truncated"] = f"only the first {MAX_SCAN_ROWS:,} rows were read"
    if lay.axis_notes:
        out["axis_notes"] = lay.axis_notes
    if s.errors:
        out["read_errors"] = s.errors[:5]
    return out


@dataclass
class FileSummary:
    path: Path
    kind: str
    sheets: List[SheetSummary] = field(default_factory=list)
    error: Optional[str] = None
    name_period: Optional[Period] = None
    revision: Optional[Tuple[str, int]] = None
    key: str = ""
    asof: Optional[Period] = None
    _labels: Optional[set] = None

    @property
    def coverage_end(self) -> Optional[Period]:
        ends = [
            s.last_with_data or s.last
            for s in self.sheets
            if (s.last_with_data or s.last)
        ]
        return max(ends, key=lambda p: p.sort_key) if ends else None

    @property
    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def rank_key(self) -> Tuple:
        end = self.coverage_end
        return (
            end.position if end else float("-inf"),
            self.asof.position if self.asof else float("-inf"),
            self.name_period.position if self.name_period else float("-inf"),
            self.revision[1] if self.revision else -1,
            self.mtime,
        )

    def label_set(self) -> set:
        if self._labels is None:
            self._labels = {
                normalize_label(lab) for s in self.sheets for lab in s.labels[:2000]
            }
        return self._labels


def _same_dataset(a: FileSummary, b: FileSummary) -> bool:
    if a.key and a.key == b.key and len(a.key) >= 3:
        return True
    la, lb = a.label_set(), b.label_set()
    if len(la) >= 3 and len(lb) >= 3:
        jacc = len(la & lb) / len(la | lb)
        if jacc >= 0.6:
            return True
    return False


def _period_text(p: Optional[Period]) -> str:
    return p.label if p else "unknown period"


def _rank_reason(newest: FileSummary, other: FileSummary) -> str:
    a, b = newest.rank_key(), other.rank_key()
    names = [
        "its data runs later",
        "a later as-of date in the file",
        "a later period in the file name",
        "a later revision in the file name",
        "it was modified later",
    ]
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return names[i]
    return "nothing tells them apart; check with the user"


def _is_link(entry: Path) -> bool:
    # A symlink or a Windows junction resolves somewhere else.
    return os.path.normcase(os.path.realpath(entry)) != os.path.normcase(
        os.path.abspath(entry)
    )


_INVENTORIED = DATA_SUFFIXES + UNREADABLE_SPREADSHEET_SUFFIXES + NARRATIVE_SUFFIXES


def _walk(folder: Path, recursive: bool) -> Tuple[List[Path], List[str]]:
    """Data files and documents under *folder*, and the names of other files.

    Files are sorted as they are met; linked folders are not entered.

    Raises:
        DataFileError: more than :data:`MAX_WALK_FILES` files.
    """
    kept: List[Path] = []
    other: List[str] = []
    seen = 0
    pending = [folder]
    while pending:
        current = pending.pop()
        for entry in current.iterdir():
            if entry.is_dir():
                if recursive and not _is_link(entry):
                    pending.append(entry)
                continue
            if not entry.is_file():
                continue
            seen += 1
            if seen > MAX_WALK_FILES:
                raise DataFileError(
                    f"{folder} holds more than {MAX_WALK_FILES:,} files"
                    + (" with its subfolders" if recursive else "")
                    + ": too many to inventory in one call. Narrow the folder: "
                    "pass the subfolder that holds the data files"
                    + (", or call with recursive=false." if recursive else ".")
                )
            name = entry.name
            if (
                name.lower().endswith(_INVENTORIED)
                or name.startswith("~$")
                or name.startswith(".~lock")
            ):
                kept.append(entry)
            elif len(other) < 50:
                other.append(name)
    return sorted(kept, key=lambda p: str(p).lower()), sorted(other, key=str.lower)


def inventory_folder(
    folder: Path,
    *,
    recursive: bool = False,
    max_files: int = 300,
    may_read=None,
    only: Optional[Sequence[Path]] = None,
    budget: int = INVENTORY_BUDGET_CHARS,
) -> Dict[str, Any]:
    """List the data files in *folder* with layout, coverage and vintage ranking.

    Args:
        folder: The directory to inventory.
        recursive: Include subfolders.
        max_files: Stop after this many files and report the rest as not read.
        may_read: Optional ``callable(path) -> bool``, e.g. the agent's
            allowed-folders check. A file it refuses is reported as not read.
        only: Inventory just these files of *folder* instead of walking it.
        budget: Most characters the result may take as JSON. Detail beyond it
            is cut, least useful first, and ``truncated`` says what and how to
            get it.

    Raises:
        DataFileError: not a folder, or too many files to walk.
    """
    if not folder.is_dir():
        raise DataFileError(f"{folder} is not a folder.")
    if only is not None:
        paths, other = sorted(only, key=lambda p: str(p).lower()), []
    else:
        paths, other = _walk(folder, recursive)
    files: List[FileSummary] = []
    narrative: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for path in paths:
        name = path.name
        suffix = path.suffix.lower()
        if name.startswith("~$") or name.startswith(".~lock"):
            skipped.append({"file": name, "reason": "an Office lock file, not data"})
            continue
        if may_read is not None and not may_read(path):
            skipped.append(
                {"file": name, "reason": "outside the folders this agent may read"}
            )
            continue
        if suffix in NARRATIVE_SUFFIXES:
            period, revision, key = filename_vintage(path.stem)
            entry = {
                "file": name,
                "kind": "narrative",
                "size_bytes": path.stat().st_size,
                "use": "read with read_file for the facts ledger",
            }
            if period:
                entry["period_in_name"] = period.label
            narrative.append(entry)
            continue
        if len(files) >= max_files:
            skipped.append(
                {
                    "file": name,
                    "reason": f"over the {max_files}-file limit for one call",
                }
            )
            continue
        fs = FileSummary(path, suffix.lstrip("."))
        fs.name_period, fs.revision, fs.key = filename_vintage(path.stem)
        try:
            with open_tables(path) as tables:
                for table in tables:
                    fs.sheets.append(summarize_table(table))
        except DataFileError as e:
            fs.error = str(e)
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            log.warning("inventory could not read %s: %s", path, e)
            fs.error = f"could not read: {type(e).__name__}: {e}"
        lines = [ln for s in fs.sheets for ln in s.layout.title_lines + s.layout.notes]
        fs.asof = _content_asof(lines)
        files.append(fs)

    # Group into datasets and rank by vintage.
    parent = list(range(len(files)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            if (
                files[i].error is None
                and files[j].error is None
                and _same_dataset(files[i], files[j])
            ):
                parent[find(j)] = find(i)
    groups: Dict[int, List[int]] = {}
    for i in range(len(files)):
        groups.setdefault(find(i), []).append(i)

    datasets = []
    role: Dict[int, str] = {}
    for members in groups.values():
        ranked = sorted(members, key=lambda i: files[i].rank_key(), reverse=True)
        newest = files[ranked[0]]
        name = newest.key or newest.path.stem
        entry: Dict[str, Any] = {
            "dataset": name,
            "newest": newest.path.name,
            "files": [files[i].path.name for i in ranked],
        }
        if newest.coverage_end:
            entry["data_through"] = newest.coverage_end.label
        role[ranked[0]] = "newest" if len(ranked) > 1 else "only version"
        if len(ranked) > 1:
            parts = [
                f"{newest.path.name} (through {_period_text(newest.coverage_end)}, newest)"
            ]
            for i in ranked[1:]:
                role[i] = f"older version of {newest.path.name}"
            for i in ranked[1:4]:
                parts.append(
                    f"{files[i].path.name} (through {_period_text(files[i].coverage_end)})"
                )
            if len(ranked) > 4:
                parts.append(f"{len(ranked) - 4} more listed in files")
            count = "two" if len(ranked) == 2 else str(len(ranked))
            entry["note"] = (
                f"{count} versions of the same series: "
                + " and ".join(parts)
                + f". Use {newest.path.name}; older files are for comparison only. Picked because "
                + _rank_reason(newest, files[ranked[1]])
                + "."
            )
            if (
                newest.coverage_end
                and files[ranked[1]].coverage_end
                and newest.coverage_end == files[ranked[1]].coverage_end
            ):
                entry[
                    "note"
                ] += " Both cover the same periods: say which file you used and why, and compare overlapping values."
        datasets.append(entry)

    out_files = []
    for i, fs in enumerate(files):
        st = fs.path.stat()
        d: Dict[str, Any] = {
            "file": fs.path.name,
            "kind": fs.kind,
            "size_bytes": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
        if recursive:
            d["path"] = os.path.relpath(fs.path, folder)
        if fs.error:
            d["error"] = fs.error
        if fs.name_period:
            d["period_in_name"] = fs.name_period.label
        if fs.revision:
            d["revision_in_name"] = fs.revision[0]
        if fs.asof:
            d["as_of_in_content"] = fs.asof.label
        if fs.coverage_end:
            d["data_through"] = fs.coverage_end.label
        d["role"] = role.get(i, "unread")
        d["sheets"] = [_sheet_dict(s) for s in fs.sheets]
        out_files.append(d)
    # The files to use come first: a reader that cuts the list loses older copies.
    out_files.sort(key=lambda d: d["role"].startswith("older version"))

    lines = []
    for d in out_files:
        cover = []
        for s in d["sheets"]:
            if s.get("periods"):
                per = s["periods"]
                cover.append(
                    f"{s['sheet']}: {per['first']}..{per.get('last_with_data', per['last'])}"
                )
        lines.append(
            f"{d['file']} — {d['role']}"
            + (f" — {'; '.join(cover[:3])}" if cover else "")
            + (f" — ERROR: {d['error']}" if d.get("error") else "")
        )
    # What the skill reads first comes first.
    result: Dict[str, Any] = {
        "status": "success",
        "folder": str(folder),
        "summary": lines,
        "datasets": datasets,
        "data_files": out_files,
    }
    if narrative:
        result["narrative_sources"] = narrative
    if other:
        result["other_files"] = other
    if skipped:
        result["not_read"] = skipped
    if not out_files and not narrative:
        result["message"] = (
            "No data files (.xlsx, .xlsm, .csv, .tsv, .json) or documents in this folder."
        )
    return _fit_inventory(result, budget)


def _json_size(value: Any) -> int:
    # As the agent measures a tool result before it cuts one.
    return len(json.dumps(value, ensure_ascii=False, default=str))


_SHEET_CORE = ("sheet", "layout", "frequency", "units", "periods", "series_count")


def _fit_inventory(result: Dict[str, Any], budget: int) -> Dict[str, Any]:
    """Cut *result* to *budget* characters of JSON, least useful detail first.

    Nothing is cut silently: ``truncated`` lists each cut and how to get the
    detail back. The newest copy of each dataset is the last thing to go.
    """
    if _json_size(result) <= budget:
        return result
    files: List[Dict[str, Any]] = result["data_files"]
    older = [d for d in files if d["role"].startswith("older version")]
    cut: List[str] = []

    def drop_series(targets: List[Dict[str, Any]]) -> None:
        for d in targets:
            for sheet in d.get("sheets", []):
                sheet.pop("series", None)

    def core_sheets() -> None:
        for d in files:
            d["sheets"] = [
                {k: sheet[k] for k in _SHEET_CORE if k in sheet}
                for sheet in d.get("sheets", [])
            ]

    def drop_sheets() -> None:
        for d in files:
            d.pop("sheets", None)

    def drop_older_files() -> None:
        result["data_files"] = [d for d in files if d not in older]
        result["summary"] = [
            line for line in result["summary"] if "— older version of " not in line
        ]

    def drop_files() -> None:
        result["data_files"] = []
        result["summary"] = [
            f"{len(files)} data file(s) in {len(result['datasets'])} dataset(s); "
            "datasets[].newest names the file to use for each"
        ]

    def short_notes() -> None:
        for entry in result["datasets"]:
            if "note" in entry:
                entry["note"] = (
                    f"Use {entry['newest']}; the other "
                    f"{len(entry['files']) - 1} are older copies, for comparison only."
                )

    def short_datasets() -> None:
        for entry in result["datasets"]:
            entry.pop("note", None)
            copies = len(entry["files"])
            if copies > 1:
                entry["copies"] = copies
            del entry["files"]

    def cap_lists() -> None:
        for key, keep in (
            ("narrative_sources", 40),
            ("other_files", 10),
            ("not_read", 20),
        ):
            items = result.get(key) or []
            if len(items) > keep:
                result[key] = items[:keep]
                cut.append(f"{len(items) - keep} more entries of {key}")

    steps = [
        (lambda: drop_series(older), "series names of older copies"),
        (lambda: drop_series(files), "series names (series_count is kept)"),
        (core_sheets, "sheet detail other than layout, units and periods"),
        (drop_sheets, "per-sheet detail"),
        (cap_lists, ""),
        (short_notes, "the reason in datasets[].note"),
        (drop_older_files, "data_files and summary entries of older copies"),
        (drop_files, "data_files and the per-file summary"),
        (short_datasets, "datasets[].note and datasets[].files (copies counts them)"),
    ]
    for step, what in steps:
        step()
        if what:
            cut.append(what)
        result["truncated"] = _truncation(cut)
        if _json_size(result) <= budget:
            break
    else:
        # Still too large: keep the datasets that fit, in order.
        datasets = result["datasets"]
        kept = len(datasets)
        while kept and _json_size(result) > budget:
            kept = max(0, kept - max(1, kept // 10))
            result["datasets"] = datasets[:kept]
            result["truncated"] = _truncation(
                cut + [f"{len(datasets) - kept} more datasets"]
            )
    # The notice goes before the lists, where a reader cannot miss it.
    head = ("status", "folder", "truncated", "summary", "datasets")
    return {
        **{k: result[k] for k in head if k in result},
        **{k: v for k, v in result.items() if k not in head},
    }


def _truncation(cut: List[str]) -> Dict[str, Any]:
    return {
        "cut_to_fit": list(cut),
        "how_to_get_it": (
            "Call inventory_data_files again on a subfolder, or with the path of "
            "one data file for its full detail."
        ),
    }
