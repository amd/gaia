# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Write series into an .xlsx as a data table plus a native Excel chart, then prove it.

The chart references the cells the table was written to, so the user can edit
a number and watch the chart move. After saving, the file is reopened and every
series the chart points at is read back from its cell range and compared with
the values that were passed in; any difference is an error, not a warning.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Sequence

from gaia.logger import get_logger

log = get_logger(__name__)

CHART_TYPES = (
    "line",
    "column",
    "bar",
    "stacked_column",
    "stacked_bar",
    "area",
    "combo",
)

#: Parts openpyxl does not carry through a load/save, so an existing workbook
#: holding them is never rewritten.
_FRAGILE_PARTS = (
    "xl/pivotTables/",
    "xl/pivotCache/",
    "xl/vbaProject.bin",
    "xl/ctrlProps/",
    "xl/threadedComments/",
    "xl/slicers/",
    "xl/timelines/",
)

#: Drawing content other than charts (shapes, text boxes, pictures, groups),
#: which openpyxl does not keep.
_NON_CHART_DRAWING = re.compile(rb"<(?:\w+:)?(?:sp|pic|grpSp|cxnSp)[\s>]")


def _workbook_parts(path: Path) -> Dict[str, Any]:
    """What an existing workbook holds that a load/save could lose."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        fragile = sorted(
            {p for name in names for p in _FRAGILE_PARTS if name.startswith(p)}
        )
        for name in names:
            if name.startswith("xl/drawings/drawing") and name.endswith(".xml"):
                if _NON_CHART_DRAWING.search(zf.read(name)):
                    fragile.append(f"{name} (shapes or pictures)")
        charts = sum(
            1 for n in names if n.startswith("xl/charts/chart") and n.endswith(".xml")
        )
    return {"fragile": fragile, "charts": charts}


class ChartWriteError(ValueError):
    """A chart that cannot be written as asked, with what to do instead."""


def _check_series(categories: Sequence[Any], series: Dict[str, Sequence[Any]]) -> None:
    if not categories:
        raise ChartWriteError("No categories (periods) to chart.")
    if not series:
        raise ChartWriteError("No series to chart.")
    for name, values in series.items():
        if len(values) != len(categories):
            raise ChartWriteError(
                f"Series {name!r} has {len(values)} values for {len(categories)} periods."
            )
        if all(v is None for v in values):
            raise ChartWriteError(f"Series {name!r} has no values to chart.")


def _unique_title(existing: Sequence[str], wanted: str) -> str:
    base = re.sub(r"[\[\]\*\?/\\:]", " ", wanted).strip()[:31] or "Chart"
    name, n = base, 2
    lower = {s.lower() for s in existing}
    while name.lower() in lower:
        suffix = f" ({n})"
        name = base[: 31 - len(suffix)] + suffix
        n += 1
    return name


def _auto_format(series: Dict[str, Sequence[Any]]) -> str:
    decimals = 0
    for values in series.values():
        for v in values:
            if isinstance(v, float) and not v.is_integer():
                text = repr(v)
                if "e" not in text and "." in text:
                    decimals = max(decimals, min(len(text.split(".")[1]), 3))
    return "#,##0" + ("." + "0" * decimals if decimals else "")


def write_workbook_chart(
    output_path: Path,
    categories: Sequence[str],
    series: Dict[str, Sequence[Any]],
    *,
    chart_type: str = "line",
    title: str = "",
    y_axis_title: str = "",
    x_axis_title: str = "",
    number_format: str = "",
    source_note: str = "",
    sheet_name: str = "",
    secondary: Sequence[str] = (),
) -> Dict[str, Any]:
    """Add a sheet with the series as a table and a native chart over it.

    Args:
        output_path: .xlsx to create, or an existing one to add a sheet to.
        categories: Period labels, oldest first.
        series: ``{series name: values}``, each aligned to *categories*;
            None leaves a gap.
        chart_type: One of :data:`CHART_TYPES`. ``combo`` draws *secondary*
            series as lines on a right-hand axis over columns for the rest.
        number_format: Excel format for the cells and value axis; derived
            from the data when empty.
        source_note: Written under the table and in the chart title area.

    Returns:
        ``{"status": "success", "sheet", "data_range", "verified": True, ...}``.

    Raises:
        ChartWriteError: bad input, an existing workbook openpyxl would damage,
            or a saved chart whose values differ from *series*.
    """
    from openpyxl import Workbook, load_workbook
    from openpyxl.chart import AreaChart, BarChart, LineChart, Reference
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    chart_type = (chart_type or "line").strip().lower()
    if chart_type not in CHART_TYPES:
        raise ChartWriteError(
            f"chart_type {chart_type!r} is not one of {', '.join(CHART_TYPES)}."
        )
    _check_series(categories, series)
    secondary = [s for s in secondary if s]
    if chart_type == "combo":
        unknown = [s for s in secondary if s not in series]
        if not secondary or unknown or len(secondary) == len(series):
            raise ChartWriteError(
                "combo needs secondary_labels naming some (not all) of the charted series "
                f"for the line on the right axis; got {secondary or 'none'}"
                + (f", not charted: {unknown}" if unknown else "")
                + f". Charted: {list(series)}."
            )
    if output_path.suffix.lower() != ".xlsx":
        raise ChartWriteError(f"{output_path.name}: write the chart to an .xlsx file.")

    charts_before = 0
    if output_path.exists():
        try:
            parts = _workbook_parts(output_path)
        except zipfile.BadZipFile as e:
            raise ChartWriteError(
                f"{output_path.name} exists but is not an .xlsx workbook: {e}"
            ) from e
        if parts["fragile"]:
            raise ChartWriteError(
                f"{output_path.name} already holds {', '.join(parts['fragile'])}, which openpyxl "
                "would drop on save. Write the chart to a new .xlsx file instead, or build the "
                "whole workbook in one run_python script."
            )
        charts_before = parts["charts"]
        wb = load_workbook(output_path)
        new_file = False
    else:
        wb = Workbook()
        new_file = True

    ws = wb.active if new_file else wb.create_sheet()
    ws.title = _unique_title(
        [] if new_file else [s for s in wb.sheetnames if s != ws.title],
        sheet_name or title or "Chart data",
    )
    fmt = number_format or _auto_format(series)

    names = list(series)
    header_row = 3 if title else 1
    if title:
        ws.cell(row=1, column=1, value=title).font = Font(bold=True, size=13)
    ws.cell(row=header_row, column=1, value="Period").font = Font(bold=True)
    for j, name in enumerate(names, start=2):
        ws.cell(row=header_row, column=j, value=name).font = Font(bold=True)
    for i, cat in enumerate(categories, start=header_row + 1):
        ws.cell(row=i, column=1, value=str(cat))
        for j, name in enumerate(names, start=2):
            cell = ws.cell(row=i, column=j, value=series[name][i - header_row - 1])
            cell.number_format = fmt
    last_row = header_row + len(categories)
    note_row = last_row + 2
    if source_note:
        ws.cell(row=note_row, column=1, value=source_note).font = Font(
            italic=True, size=9
        )
    ws.column_dimensions["A"].width = 12
    for j in range(2, len(names) + 2):
        ws.column_dimensions[get_column_letter(j)].width = max(
            12, min(40, len(names[j - 2]) + 2)
        )

    cats = Reference(ws, min_col=1, min_row=header_row + 1, max_row=last_row)

    def build(kind: str, cols: List[int]):
        if kind in ("column", "stacked_column", "bar", "stacked_bar", "combo"):
            chart = BarChart()
            chart.type = "bar" if kind in ("bar", "stacked_bar") else "col"
            if kind.startswith("stacked"):
                chart.grouping = "stacked"
                chart.overlap = 100
        elif kind == "area":
            chart = AreaChart()
        else:
            chart = LineChart()
        for col in cols:
            chart.add_data(
                Reference(ws, min_col=col, min_row=header_row, max_row=last_row),
                titles_from_data=True,
            )
        chart.set_categories(cats)
        return chart

    primary_cols = [j for j, n in enumerate(names, start=2) if n not in secondary]
    chart = build(
        chart_type,
        primary_cols if chart_type == "combo" else list(range(2, len(names) + 2)),
    )
    chart.title = title or None
    chart.y_axis.title = y_axis_title or None
    chart.x_axis.title = x_axis_title or None
    chart.y_axis.number_format = fmt
    # openpyxl 3.1 marks axes deleted by default; Excel then hides them.
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.height, chart.width = 9, 18
    if chart_type == "combo":
        line = LineChart()
        for j, n in enumerate(names, start=2):
            if n in secondary:
                line.add_data(
                    Reference(ws, min_col=j, min_row=header_row, max_row=last_row),
                    titles_from_data=True,
                )
        line.set_categories(cats)
        line.y_axis.axId = 200
        line.y_axis.title = " / ".join(secondary)
        line.y_axis.crosses = "max"
        line.y_axis.delete = False
        chart += line
    anchor = f"{get_column_letter(len(names) + 3)}{header_row}"
    ws.add_chart(chart, anchor)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)

    check = verify_workbook_chart(output_path, ws.title, categories, series)
    charts_after = _workbook_parts(output_path)["charts"]
    if charts_after != charts_before + 1:
        check["verified"] = False
        check["mismatches"].append(
            f"the workbook had {charts_before} chart(s) before and {charts_after} after; expected {charts_before + 1}"
        )
    if not check["verified"]:
        raise ChartWriteError(
            f"The chart saved in {output_path.name} does not read back as written: "
            + "; ".join(check["mismatches"][:5])
        )
    return {
        "status": "success",
        "file": str(output_path),
        "sheet": ws.title,
        "chart_type": chart_type,
        "data_range": f"'{ws.title}'!A{header_row}:{get_column_letter(len(names) + 1)}{last_row}",
        "chart_anchor": anchor,
        "number_format": fmt,
        "created_file": new_file,
        **check,
    }


_REF_RE = re.compile(
    r"^'?(?P<sheet>.+?)'?!\$?(?P<c1>[A-Z]+)\$?(?P<r1>\d+)(?::\$?(?P<c2>[A-Z]+)\$?(?P<r2>\d+))?$"
)


def _ref_values(wb: Any, ref: str) -> List[Any]:
    from openpyxl.utils import column_index_from_string

    m = _REF_RE.match(ref)
    if not m:
        raise ChartWriteError(f"chart reference {ref!r} is not a cell range")
    ws = wb[m.group("sheet").replace("''", "'")]
    c1, r1 = column_index_from_string(m.group("c1")), int(m.group("r1"))
    c2 = column_index_from_string(m.group("c2")) if m.group("c2") else c1
    r2 = int(m.group("r2")) if m.group("r2") else r1
    return [
        ws.cell(row=r, column=c).value
        for r in range(r1, r2 + 1)
        for c in range(c1, c2 + 1)
    ]


def verify_workbook_chart(
    path: Path, sheet: str, categories: Sequence[Any], series: Dict[str, Sequence[Any]]
) -> Dict[str, Any]:
    """Reopen *path* and compare the chart on *sheet* with the expected series.

    Returns:
        ``{"verified": bool, "charts_on_sheet": n, "read_back": {...}, "mismatches": [...]}``.
    """
    from openpyxl import load_workbook

    wb = load_workbook(path)
    ws = wb[sheet]
    charts = list(getattr(ws, "_charts", []))
    mismatches: List[str] = []
    read_back: Dict[str, List[Any]] = {}
    if not charts:
        return {
            "verified": False,
            "charts_on_sheet": 0,
            "mismatches": [f"no chart on sheet {sheet!r}"],
        }
    chart = charts[-1]
    plots = [chart] + list(getattr(chart, "_charts", []))[1:]
    seen_series = []
    for plot in plots:
        for ser in plot.series:
            name_ref = (
                ser.tx.strRef.f
                if ser.tx is not None and ser.tx.strRef is not None
                else None
            )
            name = _ref_values(wb, name_ref)[0] if name_ref else None
            values = _ref_values(wb, ser.val.numRef.f)
            cats = _ref_values(
                wb, ser.cat.strRef.f if ser.cat.strRef is not None else ser.cat.numRef.f
            )
            seen_series.append(name)
            read_back[str(name)] = values
            if [str(c) for c in cats] != [str(c) for c in categories]:
                mismatches.append(f"{name}: categories {cats} != {list(categories)}")
            expected = series.get(name)
            if expected is None:
                mismatches.append(f"chart has series {name!r} that was not asked for")
            elif list(values) != list(expected):
                mismatches.append(f"{name}: values {values} != {list(expected)}")
    missing = [n for n in series if n not in seen_series]
    if missing:
        mismatches.append(f"series missing from the chart: {missing}")
    return {
        "verified": not mismatches,
        "charts_on_sheet": len(charts),
        "read_back": read_back,
        "mismatches": mismatches,
    }
