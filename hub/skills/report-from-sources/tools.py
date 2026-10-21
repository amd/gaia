# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tools provided by the ``report-from-sources`` starter skill.

Three calls a small model gets wrong when it improvises them in ``run_python``:

- ``inventory_data_files``: what each data file in a folder covers, through
  which period, and which file is the newest copy of each dataset;
- ``series_from_workbook``: named series for chosen periods, matched by label
  (ambiguity reported, never guessed), values exactly as stored, every value
  cited to its cell;
- ``chart_into_workbook``: the same series written into an .xlsx as a table
  plus a native Excel chart, reread and compared before it reports success.

The logic lives in ``report_from_sources_lib`` beside this file and is
unit-tested there. Inputs are only read. ``chart_into_workbook`` writes the one
file it is given and refuses to write over a source.

Path scope: every path goes through the agent's allowed-folders check
(``gaia.skills.context``), the one ``read_file`` and ``write_file`` use, so a
folder the user approved this session works and secret files are refused.
``folder``, ``file_path`` and ``output_path`` are read-checked before a tool
runs; ``chart_into_workbook`` write-checks its output itself.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from report_from_sources_lib.periods import PeriodSpecError
from report_from_sources_lib.series import (
    SeriesLookupError,
    chart_names,
    extract_series,
    inventory_folder,
)
from report_from_sources_lib.tables import DataFileError
from report_from_sources_lib.xlsx_chart import ChartWriteError, write_workbook_chart

from gaia.agents.base.tools import tool
from gaia.agents.tools.path_access import readable_entry
from gaia.logger import get_logger
from gaia.skills import context as skill_context
from gaia.skills.errors import SkillPermissionError

log = get_logger(__name__)


class _PathRefused(ValueError):
    pass


def _guard(text: str, *, write: bool = False) -> Path:
    """The resolved path, or :class:`_PathRefused` saying why it may not be used."""
    if not text or not str(text).strip():
        raise _PathRefused("No path given.")
    raw = os.path.expanduser(str(text).strip().strip('"'))
    if not os.path.isabs(raw):
        raise _PathRefused(
            f"{text!r} is not an absolute path. Pass the full path, e.g. C:\\Users\\me\\Documents\\data.xlsx."
        )
    try:
        if write:
            return skill_context.check_write(raw)
        return skill_context.check_read(raw)
    except SkillPermissionError as e:
        raise _PathRefused(str(e)) from e


def _readable(path: Path) -> bool:
    """Whether a file found in the inventoried folder may be read (never asks)."""
    return readable_entry(skill_context.current().agent, path)


def _error(message: str, **extra: Any) -> Dict[str, Any]:
    return {"status": "error", "error": message, **extra}


def _labels(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.replace("\n", ";").split(";") if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


@tool(timeout=600, display_label="Inventorying data files")
def inventory_data_files(folder: str, recursive: bool = False) -> dict:
    """List the data files in a folder and say which is the newest copy of each dataset. Call this first.

    For every .xlsx/.xlsm/.csv/.tsv/.json file: each sheet's layout, header
    rows (multi-row and merged headers), units, period coverage (first, last,
    last with data) and series names. Groups files that hold the same dataset
    and ranks them by vintage, naming older copies. PDFs, Word files and
    other documents are listed as narrative sources to read with read_file.

    Args:
        folder: Absolute path of the folder.
        recursive: Also look in subfolders.
    """
    try:
        path = _guard(folder)
        if not path.is_dir():
            return _error(
                f"{path} is not a folder. Pass the folder that holds the files."
            )
        return inventory_folder(path, recursive=bool(recursive), readable=_readable)
    except (_PathRefused, SkillPermissionError) as e:
        return _error(str(e))
    except (DataFileError, OSError) as e:
        return _error(str(e))


@tool(timeout=600, display_label="Reading series")
def series_from_workbook(
    file_path: str,
    labels: List[str],
    sheet: str = "",
    periods: str = "last 12",
    orientation: str = "auto",
    filters: str = "",
) -> dict:
    """Read named series from a spreadsheet, CSV or JSON file for chosen periods, cited to their cells.

    Matches each label to a row or column (case, footnote markers and units
    ignored) and echoes the label it matched. Two close labels are reported
    as ambiguous with the candidates; a missing one lists the nearest labels.
    Values come back exactly as stored, aligned on one period axis, with each
    value's cell, units, footnote flags and a provenance line. chart_input is
    ready to chart.

    Args:
        file_path: Absolute path of the .xlsx/.xlsm/.csv/.tsv/.json file.
        labels: Series names, e.g. ["Net revenue", "Operating costs"]. Use
            "Section > Label" when the same label sits under two sections.
        sheet: Sheet name; empty searches every sheet.
        periods: "last 5", "all", "2024Q1:2026Q2", "2023..2025" or "2025Q4, 2026Q1".
        orientation: "auto", "periods_in_columns" or "periods_in_rows".
        filters: For long tables split by other columns: "region=East; channel=Online".
    """
    try:
        path = _guard(file_path)
        if not path.is_file():
            return _error(
                f"{path} does not exist. Run inventory_data_files on its folder to see what is there."
            )
        return extract_series(
            path,
            _labels(labels),
            sheet=sheet or None,
            periods=periods,
            orientation=orientation,
            filters=filters or None,
        )
    except (_PathRefused, SkillPermissionError) as e:
        return _error(str(e))
    except SeriesLookupError as e:
        return _error(str(e), **e.payload)
    except (DataFileError, PeriodSpecError, OSError) as e:
        return _error(str(e))


@tool(timeout=600, display_label="Charting into workbook")
def chart_into_workbook(
    output_path: str,
    file_path: str,
    labels: List[str],
    sheet: str = "",
    periods: str = "last 12",
    chart_type: str = "line",
    title: str = "",
    y_axis_title: str = "",
    number_format: str = "",
    secondary_labels: Optional[List[str]] = None,
    source_note: str = "",
    filters: str = "",
) -> dict:
    """Chart named series from a source file into an .xlsx as a native Excel chart, checked on reopen.

    Reads the series exactly as series_from_workbook does (so no number is
    retyped), writes them to a new sheet as a table with a native chart over
    it, a source and period footnote, and axis title and number format; then
    reopens the file and confirms the chart's values equal the source.

    Args:
        output_path: Absolute path of the .xlsx to create, or one to add a sheet to.
        file_path: Absolute path of the source data file.
        labels: Series names to chart, as for series_from_workbook.
        sheet: Source sheet; empty searches every sheet.
        periods: As for series_from_workbook, e.g. "last 8".
        chart_type: line, column, bar, stacked_column, stacked_bar, area or combo.
        title: Chart and sheet title.
        y_axis_title: Value axis title, e.g. "$ millions".
        number_format: Excel number format, e.g. "#,##0.0"; empty picks one from the data.
        secondary_labels: For combo: the series drawn as lines on the right axis.
        source_note: Extra text for the footnote; the source file, sheet and period are always added.
        filters: As for series_from_workbook.
    """
    try:
        src = _guard(file_path)
        out = _guard(output_path)
        if not src.is_file():
            return _error(f"{src} does not exist.")
        if out == src:
            return _error(
                "output_path is the source file; sources are never written. Choose a new file name."
            )
        if out.suffix.lower() != ".xlsx":
            return _error(f"{out.name}: write the chart to an .xlsx file.")
        if not out.parent.is_dir():
            return _error(f"The folder {out.parent} does not exist.")
        # Write-checked last: it asks before replacing an existing workbook.
        out = _guard(output_path, write=True)
        result = extract_series(
            src,
            _labels(labels),
            sheet=sheet or None,
            periods=periods,
            filters=filters or None,
        )
        names = chart_names(result["series"])
        series = dict(zip(names, (e["values"] for e in result["series"])))
        wanted_secondary = _labels(secondary_labels)
        secondary = []
        for label in wanted_secondary:
            hit = [
                n
                for n, e in zip(names, result["series"])
                if label in (e["query"], e["matched_label"], n)
            ]
            if not hit:
                return _error(
                    f"secondary_labels {label!r} is not one of the charted series: {names}."
                )
            secondary.extend(hit)
        note = result["provenance"] + (
            f". {source_note.strip()}" if source_note and source_note.strip() else ""
        )
        from gaia.agents.base.file_snapshots import before_change

        before_change(out, "chart_into_workbook")
        written = write_workbook_chart(
            out,
            result["periods"],
            series,
            chart_type=chart_type,
            title=title,
            y_axis_title=y_axis_title or (result["series"][0].get("units") or ""),
            number_format=number_format,
            source_note=note,
            secondary=secondary,
        )
        written["source"] = {
            "provenance": result["provenance"],
            "series": [
                {
                    "query": e["query"],
                    "matched_label": e["matched_label"],
                    "cell_range": e.get("cell_range"),
                    "sheet": e["sheet"],
                }
                for e in result["series"]
            ],
        }
        if result.get("notes"):
            written["notes"] = result["notes"]
        return written
    except (_PathRefused, SkillPermissionError) as e:
        return _error(str(e))
    except SeriesLookupError as e:
        return _error(str(e), **e.payload)
    except (
        DataFileError,
        PeriodSpecError,
        ChartWriteError,
        OSError,
        zipfile.BadZipFile,
    ) as e:
        return _error(str(e))
