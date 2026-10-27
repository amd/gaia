# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Parse and validate a declarative deck outline.

An outline is a dict (or JSON text) with a ``slides`` list, or just the list.
Every slide has a ``type``; everything else depends on it. Validation collects
every problem across the outline and raises them together, so a model fixes
the whole outline in one pass instead of one error per call. Nothing is
guessed: a chart value that is not a number, a series of the wrong length, or
an unknown slide type is an error, never a silent zero or a skipped slide.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .style import looks_like_periods, recommend_chart
from .text import is_number, parse_number

SLIDE_TYPES = (
    "title",
    "section",
    "agenda",
    "bullets",
    "two_column",
    "chart",
    "table",
    "kpi",
    "quote",
    "image",
    "diagram",
    "timeline",
)

ALIASES = {
    "text": "bullets",
    "content": "bullets",
    "summary": "bullets",
    "closing": "bullets",
    "executive_summary": "bullets",
    "takeaways": "bullets",
    "two-column": "two_column",
    "two_col": "two_column",
    "twocolumn": "two_column",
    "columns": "two_column",
    "comparison": "two_column",
    "pros_cons": "two_column",
    "metrics": "kpi",
    "kpis": "kpi",
    "kpi_tiles": "kpi",
    "metric": "kpi",
    "stats": "kpi",
    "roadmap": "timeline",
    "gantt": "timeline",
    "milestones": "timeline",
    "framework": "diagram",
    "process": "diagram",
    "flow": "diagram",
    "flowchart": "diagram",
    "cycle": "diagram",
    "pyramid": "diagram",
    "matrix": "diagram",
    "2x2": "diagram",
    "grid": "diagram",
    "canvas": "diagram",
    "hierarchy": "diagram",
    "org_chart": "diagram",
    "tree": "diagram",
    "boxes": "diagram",
    "pillars": "diagram",
    "picture": "image",
    "photo": "image",
    "section_header": "section",
    "divider": "section",
    "appendix": "section",
    "cover": "title",
    "title_slide": "title",
    "graph": "chart",
    "data": "chart",
    "exec_summary": "bullets",
    "next_steps": "bullets",
    "recommendation": "bullets",
    "stack": "diagram",
    "layers": "diagram",
    "layered": "diagram",
}

#: The diagram layout a type alias implies, when the slide names none.
ALIAS_LAYOUT = {
    "process": "process",
    "flow": "flow",
    "flowchart": "flow",
    "cycle": "cycle",
    "pyramid": "pyramid",
    "matrix": "matrix",
    "2x2": "matrix",
    "grid": "grid",
    "canvas": "grid",
    "hierarchy": "tree",
    "org_chart": "tree",
    "tree": "tree",
    "boxes": "boxes",
    "pillars": "boxes",
    "stack": "stack",
    "layers": "stack",
    "layered": "stack",
    "framework": None,
}

DIAGRAM_LAYOUTS = (
    "process",
    "flow",
    "cycle",
    "pyramid",
    "matrix",
    "grid",
    "boxes",
    "tree",
    "stack",
)

DIAGRAM_LAYOUT_ALIASES = {
    "steps": "process",
    "chevrons": "process",
    "funnel": "pyramid",
    "2x2": "matrix",
    "quadrant": "matrix",
    "quadrants": "matrix",
    "canvas": "grid",
    "pillars": "boxes",
    "columns": "boxes",
    "hierarchy": "tree",
    "org_chart": "tree",
    "flowchart": "flow",
    "loop": "cycle",
    "layers": "stack",
    "layered": "stack",
    "layered_stack": "stack",
    "architecture": "stack",
}

CHART_TYPES = {
    "column": "column",
    "column_clustered": "column",
    "clustered_column": "column",
    "column_stacked": "column_stacked",
    "stacked_column": "column_stacked",
    "column_stacked_100": "column_stacked_100",
    "bar": "bar",
    "bar_clustered": "bar",
    "horizontal_bar": "bar",
    "bar_stacked": "bar_stacked",
    "stacked_bar": "bar_stacked",
    "bar_stacked_100": "bar_stacked_100",
    "line": "line",
    "line_markers": "line",
    "line_plain": "line_plain",
    "area": "area",
    "area_stacked": "area_stacked",
    "stacked_area": "area_stacked",
    "pie": "pie",
    "doughnut": "doughnut",
    "donut": "doughnut",
    "scatter": "scatter",
    "xy": "scatter",
    "xy_scatter": "scatter",
    "scatter_lines": "scatter_lines",
    "combo": "combo",
    "auto": "auto",
}

#: Series types allowed inside a combo / overlay.
OVERLAY_TYPES = ("column", "line", "area")

DATA_SLIDE_TYPES = ("chart", "table", "kpi")


class OutlineError(ValueError):
    """The outline cannot be built as written. ``problems`` lists every issue."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        head = (
            f"{len(problems)} problem(s) in the outline:\n- "
            if len(problems) > 1
            else ""
        )
        super().__init__(head + "\n- ".join(problems) if head else problems[0])


_LATEX_SYMBOLS = {
    "rightarrow": "\u2192",
    "to": "\u2192",
    "longrightarrow": "\u2192",
    "Rightarrow": "\u21d2",
    "leftarrow": "\u2190",
    "Leftarrow": "\u21d0",
    "leftrightarrow": "\u2194",
    "uparrow": "\u2191",
    "downarrow": "\u2193",
    "Uparrow": "\u21d1",
    "Downarrow": "\u21d3",
    "times": "\u00d7",
    "le": "\u2264",
    "leq": "\u2264",
    "ge": "\u2265",
    "geq": "\u2265",
    "approx": "\u2248",
    "pm": "\u00b1",
    "cdot": "\u00b7",
}
_LATEX_SYMBOL_RE = re.compile(
    r"\\(" + "|".join(sorted(_LATEX_SYMBOLS, key=len, reverse=True)) + r")(?![A-Za-z])"
)
_LATEX_MATH_RE = re.compile(r"\$([^$\n]{1,80})\$")
_LATEX_TEXT_RE = re.compile(r"\\text\{([^{}]*)\}")


def _plain(text: str) -> str:
    """Models write arrows as LaTeX (``$\\rightarrow$``); a slide shows the symbol."""
    if "\\" not in text:
        return text

    def math(m: "re.Match[str]") -> str:
        if "\\" not in m.group(1):
            return m.group(0)
        inner = _LATEX_TEXT_RE.sub(r"\1", m.group(1))
        inner = _LATEX_SYMBOL_RE.sub(lambda s: _LATEX_SYMBOLS[s.group(1)], inner)
        return m.group(0) if "\\" in inner else " ".join(inner.split())

    text = _LATEX_MATH_RE.sub(math, text)
    text = _LATEX_SYMBOL_RE.sub(lambda s: _LATEX_SYMBOLS[s.group(1)], text)
    return re.sub(r"\\([%&#_$])", r"\1", text)


_PATH_KEY_RE = re.compile(r"path|image|src|file|url", re.I)


def _plain_fields(value: Any, key: str = "") -> Any:
    """Every display string of a raw slide through :func:`_plain`; paths untouched."""
    if isinstance(value, str):
        return value if _PATH_KEY_RE.search(key) else _plain(value)
    if isinstance(value, dict):
        return {k: _plain_fields(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain_fields(v, key) for v in value]
    return value


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


#: Field names small models reach for, mapped to the ones the builder reads.
FIELD_SYNONYMS = {
    "bullet_points": "bullets",
    "bulletpoints": "bullets",
    "bullet": "bullets",
    "key_points": "bullets",
    "lines": "bullets",
    "body": "text",
    "paragraph": "text",
    "paragraphs": "text",
    "header": "title",
    "slide_title": "title",
    "name": "title",
    "note": "notes",
    "speaker_note": "notes",
    "presenter_notes": "notes",
    "charttype": "chart_type",
    "chart_kind": "chart_type",
    "graph_type": "chart_type",
    "x_labels": "categories",
    "x": "categories",
    "data_series": "series",
    "datasets": "series",
    "table_rows": "rows",
    "headers": "columns",
    "column_names": "columns",
    "kpis": "metrics",
    "stats": "metrics",
    "numbers": "metrics",
    "image_path": "path",
    "file": "path",
    "dates": "milestones",
    "source_note": "source",
    "citation": "source",
}

#: Fields every slide may carry.
COMMON_FIELDS = {
    "type",
    "title",
    "subtitle",
    "notes",
    "source",
    "sources",
    "footnote",
    "allow_split",
    "kind",
    "todo",
    "slot",
    "headline",
    "heading",
    "tagline",
    "speaker_notes",
}

#: Fields each slide type reads, beyond the common ones.
TYPE_FIELDS = {
    "title": {"author", "presenter", "date", "organization"},
    "section": {"number"},
    "agenda": {"items", "bullets", "highlight", "current"},
    "bullets": {"text", "bullets", "points", "items", "content"},
    "two_column": {"left", "right", "columns"},
    "chart": {
        "chart_type",
        "chart",
        "data",
        "categories",
        "labels",
        "series",
        "values",
        "series_name",
        "csv",
        "rows",
        "columns",
        "number_format",
        "format",
        "data_labels",
        "legend",
        "x_title",
        "x_axis_title",
        "category_axis_title",
        "y_title",
        "y_axis_title",
        "value_axis_title",
        "units",
        "y2_title",
        "secondary_axis_title",
        "y_min",
        "y_max",
        "y2_number_format",
        "chart_title",
        "gridlines",
        "smooth",
        "highlight",
        "axis_group",
        "intent",
        "sort",
        "bullets",
        "takeaways",
        "callouts",
        "csv_path",
        "y",
    },
    "table": {
        "columns",
        "header",
        "rows",
        "data",
        "csv",
        "formats",
        "number_formats",
        "align",
        "total_row",
        "highlight_rows",
        "col_widths",
        "category_label",
        "csv_path",
    },
    "kpi": {"metrics", "items", "bullets", "takeaways"},
    "quote": {"quote", "text", "attribution", "author", "by"},
    "image": {"path", "image", "src", "caption", "alt", "alt_text", "bullets"},
    "diagram": {
        "diagram",
        "kind",
        "layout",
        "items",
        "steps",
        "nodes",
        "levels",
        "quadrants",
        "cells",
        "stages",
        "style",
        "direction",
        "center",
        "hub",
        "highlight",
        "columns",
        "x_axis",
        "y_axis",
        "points",
        "bullets",
        "edges",
        "links",
        "connections",
        "numbered",
    },
    "timeline": {
        "periods",
        "columns",
        "lanes",
        "tracks",
        "workstreams",
        "milestones",
        "items",
        "events",
        "highlight",
    },
}

#: A corrected, minimal shape per slide type, quoted in error messages.
EXAMPLES = {
    "title": '{"type": "title", "title": "Q3 business review", "subtitle": "Leadership team, October"}',
    "section": '{"type": "section", "title": "Results"}',
    "agenda": '{"type": "agenda", "title": "Agenda", "items": ["Results", "Risks", "Next steps"]}',
    "bullets": '{"type": "bullets", "title": "Sales grew 7% in Q3", "bullets": ["First point", ["a sub-point"], "Second point"]}',
    "two_column": '{"type": "two_column", "title": "Option B is faster", "left": {"heading": "Option A", "bullets": ["$1.2M"]}, "right": {"heading": "Option B", "bullets": ["$1.5M"]}}',
    "chart": '{"type": "chart", "title": "Revenue rose every quarter", "chart_type": "column", "categories": ["Q1", "Q2", "Q3"], "series": {"Revenue": [4.1, 4.6, 5.0]}, "y_title": "Revenue ($M)", "source": "sales.xlsx, 2026"}',
    "table": '{"type": "table", "title": "North beat plan", "columns": ["Region", "Plan", "Actual"], "rows": [["North", 100, 112], ["South", 90, 88]], "source": "plan.xlsx, Q3"}',
    "kpi": '{"type": "kpi", "title": "Every metric improved", "metrics": [{"label": "Revenue", "value": 12.4, "unit": "M", "delta": "+6%"}], "source": "kpis.xlsx, Q3"}',
    "quote": '{"type": "quote", "title": "Customers want speed", "quote": "Faster delivery matters most.", "attribution": "Survey respondent"}',
    "image": '{"type": "image", "title": "The new site", "path": "C:/Users/me/site.png", "alt": "Photo of the new site"}',
    "diagram": '{"type": "diagram", "diagram": "process", "title": "Four steps to launch", "items": [{"title": "Plan", "text": "Scope"}, {"title": "Build"}, {"title": "Test"}, {"title": "Ship"}]}',
    "timeline": '{"type": "timeline", "title": "Launch lands in March", "milestones": [{"date": "Jan", "title": "Pilot"}, {"date": "Mar", "title": "Launch"}]}',
}


def _canonical_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Rename common synonym fields; a 'content' string becomes 'text'."""
    out = dict(raw)
    for key in list(out):
        low = key.strip().lower().replace("-", "_").replace(" ", "_")
        target = FIELD_SYNONYMS.get(low)
        if low != key and low not in out:
            out[low] = out.pop(key)
            key = low
        if target and target not in out:
            out[target] = out.pop(key)
    if "content" in out:
        content = out["content"]
        if isinstance(content, str) and "text" not in out:
            out["text"] = out.pop("content")
        elif isinstance(content, list) and "bullets" not in out:
            out["bullets"] = out.pop("content")
    return out


def _carries_content(value: Any) -> bool:
    """Lists, objects and sentences are content; a short token ("wide", 3) is a setting."""
    if isinstance(value, (list, dict)):
        return bool(value)
    return isinstance(value, str) and (len(value) > 30 or len(value.split()) > 3)


def _unknown_fields(raw: Dict[str, Any], stype: str) -> List[str]:
    import difflib

    known = COMMON_FIELDS | TYPE_FIELDS.get(stype, set())
    out = []
    for key, value in raw.items():
        if key in known or not _carries_content(value):
            continue
        guess = difflib.get_close_matches(key, sorted(known), n=1, cutoff=0.6)
        hint = (
            f"did you mean '{guess[0]}'?"
            if guess
            else f"a {stype} slide reads: {', '.join(sorted(TYPE_FIELDS.get(stype, set()))[:12])}."
        )
        out.append(
            f"unknown field '{key}' ({hint}) Its content would not appear on the slide."
        )
    return out


def load_outline(outline: Any) -> Dict[str, Any]:
    """Accept a dict, a list of slides, or JSON text."""
    if isinstance(outline, (bytes, bytearray)):
        outline = outline.decode("utf-8")
    if isinstance(outline, str):
        text = outline.strip()
        if not text:
            raise OutlineError(["The outline is empty."])
        if not text.startswith(("{", "[")):
            raise OutlineError(
                [
                    "The outline is not JSON. Pass an object like "
                    '{"slides": [{"type": "title", "title": "..."}, ...]}, '
                    "or write Markdown and call build_deck_from_markdown instead."
                ]
            )
        try:
            outline = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OutlineError(
                [
                    f"The outline is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}."
                ]
            ) from exc
    if isinstance(outline, list):
        outline = {"slides": outline}
    if isinstance(outline, dict) and "slides" not in outline:
        for wrapper in ("deck", "presentation", "outline"):
            if isinstance(outline.get(wrapper), dict):
                outline = {
                    **{k: v for k, v in outline.items() if k != wrapper},
                    **outline[wrapper],
                }
                break
        for alias in ("pages", "slide_list"):
            if isinstance(outline.get(alias), list):
                outline = {**outline, "slides": outline[alias]}
                break
    if not isinstance(outline, dict):
        raise OutlineError(
            [
                f"The outline must be an object with a 'slides' list, not {type(outline).__name__}."
            ]
        )
    slides = outline.get("slides")
    if not isinstance(slides, list) or not slides:
        raise OutlineError(
            [
                "The outline has no 'slides' list (or it is empty). Use: "
                '{"slides": [' + EXAMPLES["title"] + ", " + EXAMPLES["bullets"] + "]}"
            ]
        )
    return outline


# ----------------------------------------------------------------------
# Bullets
# ----------------------------------------------------------------------


def normalize_bullets(items: Any, level: int = 0) -> List[Tuple[str, int, bool]]:
    """Flatten bullets into ``(text, level, bold)``.

    Accepts strings; ``{"text", "level", "bold", "sub"/"children"/"bullets"}``;
    and a nested list right after an item, which holds that item's children.
    Leading spaces or "- " markers inside a string set the level too.
    """
    out: List[Tuple[str, int, bool]] = []
    if items is None:
        return out
    if isinstance(items, str):
        items = [line for line in items.splitlines() if line.strip()]
    for item in _as_list(items):
        if isinstance(item, (list, tuple)):
            out += normalize_bullets(item, level + 1)
        elif isinstance(item, dict):
            text = _text(item.get("text") or item.get("title") or item.get("label"))
            lvl = item.get("level")
            lvl = int(lvl) if isinstance(lvl, (int, float)) else level
            if text:
                out.append((text, max(0, min(lvl, 4)), bool(item.get("bold"))))
            for key in ("sub", "children", "bullets", "items"):
                if item.get(key):
                    out += normalize_bullets(item[key], lvl + 1)
        else:
            raw = str(item)
            stripped = raw.lstrip(" \t")
            indent = len(raw) - len(stripped)
            extra = indent // 2
            for marker in ("- ", "* ", "• ", "– "):
                if stripped.startswith(marker):
                    stripped = stripped[len(marker) :]
                    break
            text = stripped.strip()
            if text:
                out.append((text, max(0, min(level + extra, 4)), False))
    return out


# ----------------------------------------------------------------------
# Data: chart series and tables
# ----------------------------------------------------------------------


def _num(value: Any, where: str, problems: List[str]) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if is_number(value):
        return float(value)
    parsed = parse_number(value)
    if parsed is None:
        problems.append(f"{where}: {value!r} is not a number.")
    return parsed


def _read_csv(text: str) -> List[List[str]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [
        row
        for row in csv.reader(io.StringIO(text), dialect)
        if any(c.strip() for c in row)
    ]


def _rows_from_slide(
    slide: Dict[str, Any], where: str, problems: List[str]
) -> Optional[Tuple[List[str], List[List[Any]]]]:
    """Header + rows from ``csv`` text or ``rows`` (+ ``columns``)."""
    rows: Optional[List[List[Any]]] = None
    header: Optional[List[str]] = None
    if slide.get("csv") is not None:
        parsed = _read_csv(str(slide["csv"]))
        if not parsed:
            problems.append(f"{where}: 'csv' holds no rows.")
            return None
        header, rows = [c.strip() for c in parsed[0]], parsed[1:]
    elif slide.get("csv_path"):
        problems.append(
            f"{where}: csv_path is not read by the deck builder. Extract the series first "
            "(the chart-data tool, or pandas in run_python) and pass 'categories' + 'series', "
            "or paste the CSV text as 'csv'."
        )
        return None
    elif slide.get("rows") is not None:
        raw = slide["rows"]
        if raw and all(isinstance(r, dict) for r in raw):
            header = list(dict.fromkeys(k for r in raw for k in r))
            rows = [[r.get(k) for k in header] for r in raw]
        else:
            rows = [list(r) if isinstance(r, (list, tuple)) else [r] for r in raw]
            if slide.get("columns") is not None:
                header = [_text(c) for c in slide["columns"]]
            elif rows:
                header, rows = [_text(c) for c in rows[0]], rows[1:]
    if rows is None or header is None:
        return None
    return header, rows


def normalize_chart(
    slide: Dict[str, Any], where: str, problems: List[str]
) -> Dict[str, Any]:
    """Normalize a chart slide's data into one shape.

    Accepts the chart-data shape ``{"categories": [...], "series": {name:
    values}, "source": "..."}`` (also nested under ``data``), a list of series
    objects, a single ``values`` list, CSV text/path, or ``rows`` with a
    header. Returns ``{"chart_type", "categories", "series": [{"name",
    "values", "type", "axis", "points"}], ...}``.
    """
    spec = dict(slide)
    data = spec.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            spec.setdefault(key, value)
    elif isinstance(data, list):
        spec.setdefault("rows", data)
    elif isinstance(data, str):
        spec.setdefault("csv", data)

    raw_type = (
        str(spec.get("chart_type") or spec.get("chart") or "column").strip().lower()
    )
    raw_type = raw_type.replace("-", "_").replace(" ", "_")
    chart_type = CHART_TYPES.get(raw_type)
    if chart_type is None:
        problems.append(
            f"{where}: chart_type {raw_type!r} is not supported. Use one of: "
            + ", ".join(sorted(set(CHART_TYPES.values())))
            + "."
        )
        chart_type = "column"

    categories: List[Any] = list(_as_list(spec.get("categories") or spec.get("labels")))
    series_in = spec.get("series")
    series: List[Dict[str, Any]] = []

    if series_in is None and spec.get("values") is not None:
        series_in = [
            {
                "name": spec.get("series_name") or spec.get("name") or "Series 1",
                "values": spec["values"],
            }
        ]
    if series_in is None:
        table = _rows_from_slide(spec, where, problems)
        if table:
            header, rows = table
            if chart_type in ("scatter", "scatter_lines"):
                series_in = [
                    {
                        "name": header[j] if j < len(header) else f"Series {j}",
                        "points": [[r[0], r[j]] for r in rows if len(r) > j],
                    }
                    for j in range(1, len(header))
                ]
            else:
                categories = [_text(r[0]) if r else "" for r in rows]
                series_in = [
                    {
                        "name": header[j] if j < len(header) else f"Series {j}",
                        "values": [r[j] if len(r) > j else None for r in rows],
                    }
                    for j in range(1, len(header))
                ]
    if isinstance(series_in, dict):
        # {"Revenue": [..]} or {"Revenue": {"values": [...], "type": "line"}}
        listed = []
        for name, value in series_in.items():
            if isinstance(value, dict):
                listed.append({"name": name, **value})
            else:
                listed.append({"name": name, "values": value})
        series_in = listed
    if not series_in:
        problems.append(
            f"{where}: a chart needs data — 'categories' + 'series' ({{name: values}}), "
            "'values', 'csv', or 'rows'."
        )
        series_in = []

    scatter = chart_type in ("scatter", "scatter_lines")
    for k, s in enumerate(_as_list(series_in), 1):
        if not isinstance(s, dict):
            if isinstance(s, (list, tuple)):
                s = {"name": f"Series {k}", "values": list(s)}
            else:
                problems.append(
                    f"{where}: series {k} must be an object with 'name' and 'values'."
                )
                continue
        name = _text(s.get("name") or s.get("label") or f"Series {k}")
        sw = f"{where}, series '{name}'"
        entry: Dict[str, Any] = {
            "name": name,
            "type": (str(s.get("type")).lower() if s.get("type") else None),
            "axis": (
                "secondary"
                if str(s.get("axis", "")).lower() in ("secondary", "right", "y2", "2")
                or s.get("secondary")
                else "primary"
            ),
            "number_format": s.get("number_format"),
            "color": s.get("color"),
        }
        if scatter:
            pts = s.get("points")
            if pts is None and s.get("x") is not None:
                pts = list(
                    zip(_as_list(s.get("x")), _as_list(s.get("y") or s.get("values")))
                )
            if pts is None and s.get("values") is not None and categories:
                pts = list(zip(categories, _as_list(s.get("values"))))
            points = []
            for j, p in enumerate(_as_list(pts)):
                if isinstance(p, dict):
                    p = (p.get("x"), p.get("y"))
                if not isinstance(p, (list, tuple)) or len(p) < 2:
                    problems.append(f"{sw}: point {j + 1} must be [x, y].")
                    continue
                x = _num(p[0], f"{sw} point {j + 1} x", problems)
                y = _num(p[1], f"{sw} point {j + 1} y", problems)
                if x is not None and y is not None:
                    points.append((x, y))
            if not points:
                problems.append(f"{sw}: no [x, y] points.")
            entry["points"] = points
            entry["values"] = [y for _x, y in points]
        else:
            vals = s.get("values")
            if vals is None:
                vals = s.get("data") or s.get("y")
            values = [
                _num(v, f"{sw} value {j + 1}", problems)
                for j, v in enumerate(_as_list(vals))
            ]
            entry["values"] = values
            if entry["type"] and entry["type"] not in OVERLAY_TYPES:
                problems.append(
                    f"{sw}: series type {entry['type']!r} must be one of {', '.join(OVERLAY_TYPES)}."
                )
                entry["type"] = None
        series.append(entry)

    reason = ""
    if chart_type == "auto":
        if series and all(not s.get("points") for s in series):
            chart_type, reason = recommend_chart(
                categories, series, intent=str(spec.get("intent") or "")
            )
        else:
            chart_type, reason = "scatter", "x/y points: scatter"
        scatter = chart_type in ("scatter", "scatter_lines")
        if scatter and not any(s.get("points") for s in series):
            chart_type, reason, scatter = (
                "column",
                "no x/y points for a scatter: columns",
                False,
            )
    sort = str(spec.get("sort") or "").lower()
    if (
        sort in ("auto", "")
        and chart_type in ("bar", "bar_stacked")
        and categories
        and not looks_like_periods(categories)
        and str(spec.get("intent") or "").lower()
        in ("compare", "ranking", "comparison", "share")
        and len(series) >= 1
    ):
        sort = "desc"
    if (
        sort in ("desc", "asc")
        and series
        and categories
        and all(len(s["values"]) == len(categories) for s in series)
    ):
        order = sorted(
            range(len(categories)),
            key=lambda i: (series[0]["values"][i] is None, series[0]["values"][i] or 0),
            reverse=(sort == "desc"),
        )
        if sort == "desc":
            order = sorted(
                range(len(categories)), key=lambda i: -(series[0]["values"][i] or 0)
            )
        if chart_type.startswith("bar"):
            order = list(
                reversed(order)
            )  # horizontal bars draw the first category at the bottom
        categories = [categories[i] for i in order]
        for s in series:
            s["values"] = [s["values"][i] for i in order]
    if not scatter:
        if not categories and series:
            n = max(len(s["values"]) for s in series)
            categories = [str(i + 1) for i in range(n)]
            problems.append(
                f"{where}: no 'categories' given for {n} values. Add the labels "
                "(periods, regions, ...) so the chart says what each bar is."
            )
        for s in series:
            if len(s["values"]) != len(categories):
                problems.append(
                    f"{where}, series '{s['name']}': {len(s['values'])} values for "
                    f"{len(categories)} categories. Every series needs one value per category."
                )
        if chart_type == "pie" and len(series) > 1:
            problems.append(
                f"{where}: a pie chart shows one series; got {len(series)}. Use a "
                "column/bar chart to compare series, or a doughnut for rings."
            )
        if chart_type in ("pie", "doughnut"):
            for s in series:
                if any(v is not None and v < 0 for v in s["values"]):
                    problems.append(
                        f"{where}, series '{s['name']}': a {chart_type} cannot show negative values."
                    )
        if (
            chart_type not in ("combo",)
            and any(s["type"] for s in series)
            and chart_type in ("pie", "doughnut")
        ):
            problems.append(
                f"{where}: per-series 'type' only works on column/bar/line/area/combo charts."
            )

    legend = spec.get("legend", "auto")
    if legend is True:
        legend = "bottom"
    if legend is False or (
        isinstance(legend, str) and legend.lower() in ("none", "off", "hide")
    ):
        legend = "none"
    return {
        "chart_type": chart_type,
        "categories": [_text(c) for c in categories] if not scatter else [],
        "series": series,
        "number_format": spec.get("number_format") or spec.get("format"),
        "data_labels": spec.get("data_labels", "auto"),
        "legend": str(legend).lower() if legend else "auto",
        "x_title": _text(
            spec.get("x_title")
            or spec.get("x_axis_title")
            or spec.get("category_axis_title")
        ),
        "y_title": _text(
            spec.get("y_title")
            or spec.get("y_axis_title")
            or spec.get("value_axis_title")
            or spec.get("units")
        ),
        "y2_title": _text(spec.get("y2_title") or spec.get("secondary_axis_title")),
        "y_min": spec.get("y_min"),
        "y_max": spec.get("y_max"),
        "y2_number_format": spec.get("y2_number_format"),
        "chart_title": _text(spec.get("chart_title")),
        "gridlines": spec.get("gridlines", True),
        "smooth": bool(spec.get("smooth", False)),
        "source": _text(spec.get("source")),
        "highlight": spec.get("highlight"),
        "axis_group": _text(spec.get("axis_group")),
        "chart_reason": reason,
    }


def normalize_table(
    slide: Dict[str, Any], where: str, problems: List[str]
) -> Dict[str, Any]:
    spec = dict(slide)
    data = spec.get("data")
    header: List[str] = []
    rows: List[List[Any]] = []
    if isinstance(data, dict) and "categories" in data and "series" in data:
        cats = _as_list(data["categories"])
        ser = data["series"]
        if isinstance(ser, dict):
            ser = [{"name": k, "values": v} for k, v in ser.items()]
        header = [
            _text(spec.get("category_label") or data.get("category_label") or "")
        ] + [_text(s.get("name")) for s in ser]
        rows = [
            [c]
            + [
                (
                    _as_list(s.get("values"))[i]
                    if i < len(_as_list(s.get("values")))
                    else None
                )
                for s in ser
            ]
            for i, c in enumerate(cats)
        ]
        spec.setdefault("source", data.get("source"))
    else:
        if isinstance(data, list):
            spec.setdefault("rows", data)
        elif isinstance(data, str):
            spec.setdefault("csv", data)
        if spec.get("header") is not None and spec.get("columns") is None:
            spec["columns"] = spec["header"]
        table = _rows_from_slide(spec, where, problems)
        if table:
            header, rows = table
    if not header and not rows:
        problems.append(
            f"{where}: a table needs 'columns' + 'rows' (or 'csv', or a list of row objects)."
        )
    width = max([len(header)] + [len(r) for r in rows]) if (header or rows) else 0
    if header and len(header) < width:
        problems.append(
            f"{where}: {len(header)} column names for rows up to {width} cells wide."
        )
    header = [_text(h) for h in header] + [""] * (width - len(header))
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    formats = spec.get("formats") or spec.get("number_formats") or {}
    if isinstance(formats, list):
        formats = {i: f for i, f in enumerate(formats) if f}
    resolved_formats: Dict[int, str] = {}
    for key, code in dict(formats).items():
        idx = _column_index(key, header)
        if idx is None:
            problems.append(f"{where}: format given for unknown column {key!r}.")
        else:
            resolved_formats[idx] = str(code)
    align: Dict[int, str] = {}
    for key, value in dict(spec.get("align") or {}).items():
        idx = _column_index(key, header)
        if idx is not None:
            align[idx] = str(value).lower()
    return {
        "columns": header,
        "rows": rows,
        "formats": resolved_formats,
        "align": align,
        "total_row": bool(spec.get("total_row")),
        "highlight_rows": [
            int(i)
            for i in _as_list(spec.get("highlight_rows"))
            if isinstance(i, (int, float))
        ],
        "col_widths": spec.get("col_widths"),
        "source": _text(spec.get("source")),
    }


def _column_index(key: Any, header: List[str]) -> Optional[int]:
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        i = int(key)
        return i if 0 <= i < len(header) else None
    for i, h in enumerate(header):
        if h.strip().lower() == str(key).strip().lower():
            return i
    return None


def _items(value: Any) -> List[Dict[str, Any]]:
    out = []
    for item in _as_list(value):
        if isinstance(item, dict):
            d = dict(item)
            d["title"] = _text(
                d.get("title") or d.get("label") or d.get("name") or d.get("heading")
            )
            text = (
                d.get("text")
                or d.get("detail")
                or d.get("description")
                or d.get("body")
            )
            d["text"] = _text(text) if not isinstance(text, list) else ""
            d["bullets"] = normalize_bullets(
                d.get("bullets") or (text if isinstance(text, list) else None)
            )
            out.append(d)
        elif isinstance(item, (list, tuple)) and item:
            out.append(
                {
                    "title": _text(item[0]),
                    "text": " ".join(_text(x) for x in item[1:]),
                    "bullets": [],
                }
            )
        else:
            out.append({"title": _text(item), "text": "", "bullets": []})
    return out


def normalize_slide(
    raw: Any, index: int, problems: List[str], base_dir: Optional[Path] = None
) -> Dict[str, Any]:
    where = f"slide {index}"
    if isinstance(raw, str):
        raw = {"type": "bullets", "title": raw}
    if not isinstance(raw, dict):
        problems.append(
            f"{where}: must be an object, got {type(raw).__name__}. For example: {EXAMPLES['bullets']}"
        )
        return {"type": "bullets", "index": index, "title": "", "paras": []}
    raw = _plain_fields(_canonical_fields(raw))
    before = len(problems)
    s = _normalize_slide(raw, index, problems, base_dir)
    for issue in _unknown_fields(raw, s["type"]):
        problems.append(f"slide {index} ({s['type']}): {issue}")
    if len(problems) > before:
        problems.append(
            f"slide {index}: a valid {s['type']} slide looks like {EXAMPLES[s['type']]}"
        )
    return s


def _normalize_slide(
    raw: Dict[str, Any], index: int, problems: List[str], base_dir: Optional[Path]
) -> Dict[str, Any]:
    where = f"slide {index}"
    raw_type = (
        str(raw.get("type") or "").strip().lower().replace("-", "_").replace(" ", "_")
    )
    if not raw_type:
        # Infer the obvious cases rather than refuse them.
        if raw.get("chart_type") or raw.get("series"):
            raw_type = "chart"
        elif raw.get("rows") or raw.get("csv"):
            raw_type = "table"
        elif raw.get("metrics") or raw.get("kpis"):
            raw_type = "kpi"
        elif raw.get("bullets") or raw.get("points") or raw.get("text"):
            raw_type = "bullets"
        elif raw.get("quote"):
            raw_type = "quote"
        elif raw.get("milestones") or raw.get("lanes"):
            raw_type = "timeline"
        elif raw.get("path"):
            raw_type = "image"
        elif index == 1:
            raw_type = "title"
        else:
            raw_type = "bullets"
    stype = ALIASES.get(raw_type, raw_type.replace("-", "_"))
    if stype not in SLIDE_TYPES:
        problems.append(
            f"{where}: type {raw_type!r} is not supported. Use one of: {', '.join(SLIDE_TYPES)}."
        )
        stype = "bullets"
    where = f"slide {index} ({stype})"
    s: Dict[str, Any] = {
        "type": stype,
        "index": index,
        "title": _text(raw.get("title") or raw.get("headline") or raw.get("heading")),
        "subtitle": _text(raw.get("subtitle") or raw.get("tagline")),
        "notes": "\n".join(
            _text(n) for n in _as_list(raw.get("notes") or raw.get("speaker_notes"))
        ),
        "source": (
            _text(raw.get("source") or raw.get("sources"))
            if not isinstance(raw.get("sources"), list)
            else "; ".join(_text(x) for x in raw["sources"])
        ),
        "footnote": _text(raw.get("footnote")),
        "allow_split": raw.get("allow_split"),
        "kind": str(raw.get("kind") or raw_type).lower(),
        "todo": bool(raw.get("todo")),
        "slot": _text(raw.get("slot")),
        "data_slide": raw_type == "data",
        "raw": raw,
    }
    if isinstance(raw.get("source"), list):
        s["source"] = "; ".join(_text(x) for x in raw["source"])
    if stype == "title":
        extra = [
            _text(raw.get(k))
            for k in ("author", "presenter", "date", "organization")
            if raw.get(k)
        ]
        s["meta"] = " | ".join(extra)
    elif stype == "section":
        s["number"] = _text(raw.get("number"))
    elif stype == "agenda":
        s["items"] = [
            t
            for t, _lvl, _b in normalize_bullets(raw.get("items") or raw.get("bullets"))
        ]
        hl = raw.get("highlight", raw.get("current"))
        s["highlight"] = int(hl) if isinstance(hl, (int, float)) else None
        if not s["items"]:
            problems.append(f"{where}: an agenda needs 'items'.")
    elif stype == "bullets":
        paras = []
        if raw.get("text"):
            for line in _as_list(raw["text"]):
                paras.append((_text(line), -1, False))  # -1: plain paragraph, no bullet
        paras += normalize_bullets(
            raw.get("bullets") or raw.get("points") or raw.get("items")
        )
        s["paras"] = paras
        if not paras:
            problems.append(f"{where}: no 'bullets' or 'text'.")
    elif stype == "two_column":
        cols = []
        for key in ("left", "right"):
            col = raw.get(key)
            if (
                col is None
                and isinstance(raw.get("columns"), list)
                and len(raw["columns"]) == 2
            ):
                col = raw["columns"][0 if key == "left" else 1]
            if isinstance(col, dict):
                cols.append(
                    {
                        "heading": _text(col.get("heading") or col.get("title")),
                        "paras": (
                            [(_text(col["text"]), -1, False)] if col.get("text") else []
                        )
                        + normalize_bullets(
                            col.get("bullets") or col.get("items") or col.get("points")
                        ),
                    }
                )
            else:
                cols.append({"heading": "", "paras": normalize_bullets(col)})
        s["columns"] = cols
        if not any(c["paras"] or c["heading"] for c in cols):
            problems.append(
                f"{where}: needs 'left' and 'right' (each a list of bullets or {{heading, bullets}})."
            )
    elif stype == "chart":
        s["chart"] = normalize_chart(raw, where, problems)
        if not s["source"]:
            s["source"] = s["chart"]["source"]
        s["paras"] = normalize_bullets(
            raw.get("bullets") or raw.get("takeaways") or raw.get("callouts")
        )
    elif stype == "table":
        s["table"] = normalize_table(raw, where, problems)
        if not s["source"]:
            s["source"] = s["table"]["source"]
    elif stype == "kpi":
        metrics = []
        for m in _as_list(raw.get("metrics") or raw.get("kpis") or raw.get("items")):
            if not isinstance(m, dict):
                problems.append(
                    f"{where}: each metric must be an object with 'label' and 'value'."
                )
                continue
            if m.get("value") is None:
                problems.append(f"{where}: metric {m.get('label')!r} has no 'value'.")
            metrics.append(
                {
                    "label": _text(m.get("label") or m.get("name") or m.get("title")),
                    "value": m.get("value"),
                    "format": m.get("format") or m.get("number_format"),
                    "unit": _text(m.get("unit")),
                    "delta": _text(m.get("delta") or m.get("change")),
                    "good": str(
                        m.get("good") or m.get("good_direction") or "up"
                    ).lower(),
                    "note": _text(m.get("note") or m.get("detail") or m.get("context")),
                }
            )
        if not metrics:
            problems.append(
                f"{where}: needs 'metrics': [{{label, value, delta?, note?}}]."
            )
        if len(metrics) > 8:
            problems.append(
                f"{where}: {len(metrics)} metrics; at most 8 tiles stay readable. Split them over two slides."
            )
        s["metrics"] = metrics
        s["paras"] = normalize_bullets(raw.get("bullets") or raw.get("takeaways"))
    elif stype == "quote":
        s["quote"] = _text(raw.get("quote") or raw.get("text"))
        s["attribution"] = _text(
            raw.get("attribution") or raw.get("author") or raw.get("by")
        )
        if not s["quote"]:
            problems.append(f"{where}: needs 'quote'.")
    elif stype == "image":
        path = _text(raw.get("path") or raw.get("image") or raw.get("src"))
        if path and base_dir is not None and not Path(path).is_absolute():
            path = str(base_dir / path)
        s["path"] = path
        s["caption"] = _text(raw.get("caption"))
        s["alt"] = _text(
            raw.get("alt") or raw.get("alt_text") or s["caption"] or s["title"]
        )
        s["paras"] = normalize_bullets(raw.get("bullets"))
        if not path:
            problems.append(f"{where}: needs 'path' to an image file.")
        elif not Path(path).is_file():
            problems.append(f"{where}: image {path} does not exist.")
    elif stype == "diagram":
        layout = (
            str(raw.get("diagram") or raw.get("kind") or raw.get("layout") or "")
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        layout = DIAGRAM_LAYOUT_ALIASES.get(layout, layout)
        if not layout:
            layout = ALIAS_LAYOUT.get(raw_type) or ""
        if not layout:
            if raw.get("quadrants"):
                layout = "matrix"
            elif raw.get("edges") or raw.get("links"):
                layout = "flow"
            elif raw.get("cells"):
                layout = "grid"
            elif raw.get("levels"):
                layout = "pyramid"
            else:
                layout = "process"
        if layout not in DIAGRAM_LAYOUTS:
            problems.append(
                f"{where}: diagram {layout!r} is not supported. Use one of: {', '.join(DIAGRAM_LAYOUTS)}."
            )
            layout = "boxes"
        s["layout"] = layout
        items = _items(
            raw.get("items")
            or raw.get("steps")
            or raw.get("nodes")
            or raw.get("levels")
            or raw.get("quadrants")
            or raw.get("cells")
            or raw.get("stages")
        )
        s["items"] = items
        s["style"] = str(raw.get("style") or "").lower()
        s["direction"] = (
            "down"
            if str(raw.get("direction", "")).lower()
            in ("down", "vertical", "top_down", "tb")
            else "right"
        )
        s["center"] = _text(raw.get("center") or raw.get("hub"))
        s["highlight"] = raw.get("highlight")
        s["columns_n"] = (
            raw.get("columns") if isinstance(raw.get("columns"), int) else None
        )
        s["x_axis"] = raw.get("x_axis") or {}
        s["y_axis"] = raw.get("y_axis") or {}
        s["points"] = _as_list(raw.get("points"))
        s["paras"] = normalize_bullets(raw.get("bullets"))
        edges = []
        for e in _as_list(
            raw.get("edges") or raw.get("links") or raw.get("connections")
        ):
            if isinstance(e, dict):
                edges.append(
                    (
                        _text(e.get("from") or e.get("source")),
                        _text(e.get("to") or e.get("target")),
                        _text(e.get("label")),
                    )
                )
            elif isinstance(e, (list, tuple)) and len(e) >= 2:
                edges.append(
                    (_text(e[0]), _text(e[1]), _text(e[2]) if len(e) > 2 else "")
                )
            else:
                problems.append(
                    f"{where}: edge {e!r} must be [from, to] or {{from, to}}."
                )
        if layout == "tree":
            for it in items:
                if it.get("parent"):
                    edges.append(
                        (_text(it["parent"]), _text(it.get("id") or it["title"]), "")
                    )
        s["edges"] = edges
        if layout == "matrix" and len(items) not in (0, 4):
            problems.append(
                f"{where}: a 2x2 matrix takes exactly 4 quadrants (top-left, top-right, bottom-left, bottom-right); got {len(items)}."
            )
        if layout == "matrix" and not items and not s["points"]:
            problems.append(
                f"{where}: a matrix needs 4 'items' (quadrants) and/or 'points'."
            )
        elif layout != "matrix" and not items:
            problems.append(f"{where}: a {layout} diagram needs 'items'.")
        if layout == "cycle" and not 3 <= len(items) <= 8:
            problems.append(f"{where}: a cycle takes 3 to 8 items; got {len(items)}.")
        if layout in ("process",) and len(items) > 10:
            problems.append(
                f"{where}: {len(items)} process steps will not read; keep it to 10 or fewer (group steps into phases)."
            )
        if layout in ("flow", "tree"):
            ids = {_text(it.get("id") or it["title"]) for it in items}
            for a, b, _l in edges:
                for end in (a, b):
                    if end not in ids:
                        problems.append(
                            f"{where}: edge end {end!r} is not an item id or title."
                        )
    elif stype == "timeline":
        s["periods"] = [
            _text(p) for p in _as_list(raw.get("periods") or raw.get("columns"))
        ]
        lanes = []
        for lane in _as_list(
            raw.get("lanes") or raw.get("tracks") or raw.get("workstreams")
        ):
            if not isinstance(lane, dict):
                problems.append(
                    f"{where}: each lane must be {{name, items: [{{label, start, end}}]}}."
                )
                continue
            bars = []
            for it in _as_list(lane.get("items")):
                if not isinstance(it, dict):
                    problems.append(
                        f"{where}: lane {lane.get('name')!r} item {it!r} must be {{label, start, end}}."
                    )
                    continue
                bars.append(
                    {
                        "label": _text(it.get("label") or it.get("title")),
                        "start": it.get("start"),
                        "end": it.get("end", it.get("start")),
                        "status": _text(it.get("status")),
                    }
                )
            lanes.append(
                {"name": _text(lane.get("name") or lane.get("title")), "items": bars}
            )
        s["lanes"] = lanes
        s["items"] = _items(
            raw.get("milestones") or raw.get("items") or raw.get("events")
        )
        for it in s["items"]:
            it["date"] = _text(it.get("date") or it.get("when"))
        if lanes:
            if not s["periods"]:
                problems.append(
                    f"{where}: a roadmap with lanes needs 'periods' (the column labels, e.g. quarters)."
                )
            for lane in lanes:
                for bar in lane["items"]:
                    for key in ("start", "end"):
                        v = bar[key]
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            if not 0 <= int(v) < max(1, len(s["periods"])):
                                problems.append(
                                    f"{where}: {bar['label']!r} {key}={v} is outside the {len(s['periods'])} periods (0-based index)."
                                )
                        elif _text(v) not in s["periods"]:
                            problems.append(
                                f"{where}: {bar['label']!r} {key}={v!r} is not one of the periods {s['periods']}."
                            )
        elif not s["items"]:
            problems.append(
                f"{where}: a timeline needs 'milestones' [{{date, title, text}}] or 'lanes' + 'periods'."
            )
    return s


def normalize_outline(outline: Any, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Validate *outline* and return it in canonical form. Raises OutlineError."""
    data = load_outline(outline)
    problems: List[str] = []
    slides = [
        normalize_slide(raw, i, problems, base_dir)
        for i, raw in enumerate(data["slides"], 1)
    ]
    expected = data.get("expected_slides") or data.get("slide_count")
    if expected is not None:
        try:
            expected = int(expected)
        except (TypeError, ValueError):
            problems.append(f"expected_slides {expected!r} is not a whole number.")
            expected = None
    if expected is not None and expected != len(slides):
        problems.append(
            f"The outline has {len(slides)} slides but expected_slides is {expected}. "
            "Add or remove slides so the count is exactly what was asked."
        )
    required_raw = data.get("required_items") or data.get("must_show") or []
    if isinstance(required_raw, str):
        required_raw = [x for x in required_raw.split(";")]
    if not isinstance(required_raw, list):
        problems.append(
            'required_items must be a list of short strings, e.g. ["Revenue $12.4M", "3 site closures"].'
        )
        required_raw = []
    required = [_text(x) for x in required_raw if _text(x)]
    if problems:
        raise OutlineError(problems)
    return {
        "title": _text(data.get("title")),
        "theme": data.get("theme"),
        "footer": _text(data.get("footer")),
        "slide_numbers": data.get("slide_numbers", True) is not False,
        "allow_split": bool(data.get("allow_split", False)),
        "expected_slides": expected,
        "require_notes": bool(data.get("require_notes", False)),
        "style": data.get("style") if isinstance(data.get("style"), dict) else {},
        "required_items": required,
        "slides": slides,
    }


def expected_chart_slides(
    normalized: Dict[str, Any],
) -> List[Tuple[int, Dict[str, Any]]]:
    """``(outline index, chart spec)`` for every chart slide, for round-trip checks."""
    return [
        (s["index"], s["chart"]) for s in normalized["slides"] if s["type"] == "chart"
    ]
