# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn Markdown into a deck outline.

Conventions (all optional):

- ``# Title`` (first line) is the title slide; the next paragraph is its subtitle.
- ``## Heading`` (or a ``---`` line) starts a slide; the heading is its title.
  A ``##`` slide with no content is a section divider; one titled Agenda or
  Contents whose body is a list becomes an agenda.
- ``-``, ``*``, ``+`` and ``1.`` lists are bullets; indent 2+ spaces per level.
- Exactly two ``###`` headings on a slide make a two-column slide.
- A pipe table is a table slide; ``![alt](path)`` an image slide; a slide
  that is only a ``>`` block is a quote slide.
- ``Source: ...`` and ``Note: ...`` lines become the source and footnote lines;
  ``Notes:`` (or ``Speaker notes:``) starts the speaker notes.
- A fenced ```` ```chart line ```` block (type after ``chart``: column, bar,
  line, pie...; default auto) holds optional ``y_title:``/``source:``/
  ``format:``/``highlight:`` lines, then CSV: a header (category, series...)
  and one row per category. ```` ```table ```` takes the same CSV for a table;
  ```` ```kpi ```` takes ``label,value,delta,note`` rows for KPI tiles.
- A fenced ```` ```slide ```` (or ```` ```json ````) block holds any slide
  fields as JSON, merged into the slide.
- ``Must show: a; b; c`` before the first slide lists required_items: things
  the request needs visible on the slides.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .outline import OutlineError

_BULLET = re.compile(r"^(\s*)(?:[-*+•]|\d+[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_REQUIRED = re.compile(
    r"^\s*(must[\s_-]*show|required(?:[\s_-]*items)?)\s*:\s*(.+)$", re.I
)
_IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")


def _cells(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _number(cell: str) -> Any:
    t = cell.replace(",", "").strip()
    if re.fullmatch(r"[-+]?\d+", t):
        return int(t)
    if re.fullmatch(r"[-+]?\d*\.\d+", t):
        return float(t)
    return cell


_BLOCK_OPTIONS = {
    "title": "title",
    "type": "chart_type",
    "chart_type": "chart_type",
    "y_title": "y_title",
    "y axis": "y_title",
    "x_title": "x_title",
    "x axis": "x_title",
    "y2_title": "y2_title",
    "source": "source",
    "format": "number_format",
    "number_format": "number_format",
    "highlight": "highlight",
    "intent": "intent",
    "legend": "legend",
    "footnote": "footnote",
}


def _csv_block(title: str, lang: str, lines: List[str]) -> Dict[str, Any]:
    """A ```chart / ```table / ```kpi block: optional 'key: value' lines, then CSV."""
    parts = re.split(r"[\s:]+", lang.strip(), maxsplit=1)
    kind = parts[0]
    options: Dict[str, Any] = {}
    body: List[str] = []
    for line in lines:
        m = re.match(r"^\s*([A-Za-z_ 0-9]+?)\s*:\s*(.+?)\s*$", line)
        if not body and m and m.group(1).strip().lower() in _BLOCK_OPTIONS:
            options[_BLOCK_OPTIONS[m.group(1).strip().lower()]] = m.group(2)
        elif line.strip():
            body.append(line)
    csv_text = "\n".join(body)
    if not body:
        raise OutlineError(
            [
                f"slide {title!r}: the ```{kind} block has no rows. Put a header line, then one line per row, e.g.\nQuarter,Revenue\nQ1,4.1"
            ]
        )
    if kind == "chart":
        out: Dict[str, Any] = {
            "type": "chart",
            "chart_type": (
                parts[1].strip() if len(parts) > 1 and parts[1].strip() else "auto"
            ),
            "csv": csv_text,
        }
        out.update(options)
        return out
    if kind == "table":
        out = {"type": "table", "csv": csv_text}
        out.update(
            {k: v for k, v in options.items() if k in ("title", "source", "footnote")}
        )
        return out
    from .outline import _read_csv

    rows = _read_csv(csv_text)
    header = [h.strip().lower() for h in rows[0]]
    col = {name: i for i, name in enumerate(header)}
    label_i = col.get("label", col.get("metric", col.get("name", 0)))
    value_i = col.get("value", 1 if len(header) > 1 else 0)
    metrics = []
    for r in rows[1:]:
        m: Dict[str, Any] = {
            "label": r[label_i].strip() if label_i < len(r) else "",
            "value": _number(r[value_i]) if value_i < len(r) else None,
        }
        for key in ("delta", "change", "note", "unit", "good"):
            if key in col and col[key] < len(r) and r[col[key]].strip():
                m["delta" if key == "change" else key] = r[col[key]].strip()
        metrics.append(m)
    out = {"type": "kpi", "metrics": metrics}
    out.update(
        {k: v for k, v in options.items() if k in ("title", "source", "footnote")}
    )
    return out


def _slide_from_block(title: str, lines: List[str]) -> Dict[str, Any]:
    slide: Dict[str, Any] = {"title": title.strip()}
    bullets: List[Any] = []
    text: List[str] = []
    notes: List[str] = []
    quote: List[str] = []
    columns: List[Dict[str, Any]] = []
    table: List[List[str]] = []
    in_notes = False
    fence: Optional[List[str]] = None
    fence_lang = ""
    extra: Dict[str, Any] = {}
    diagram = ""
    target = bullets

    def flush_table():
        nonlocal table
        if len(table) >= 2:
            slide.setdefault("columns_table", table[0])
            slide.setdefault("rows_table", [[_number(c) for c in r] for r in table[1:]])
        table = []

    for raw in lines:
        line = raw.rstrip("\n")
        if fence is not None:
            if line.strip().startswith("```"):
                body = "\n".join(fence).strip()
                kind = (
                    re.split(r"[\s:]+", fence_lang, maxsplit=1)[0] if fence_lang else ""
                )
                if kind in (
                    "chart",
                    "table",
                    "kpi",
                    "kpis",
                    "metrics",
                ) and not body.startswith("{"):
                    extra.update(_csv_block(title, fence_lang, fence))
                else:
                    try:
                        extra.update(json.loads(body or "{}"))
                    except json.JSONDecodeError as exc:
                        raise OutlineError(
                            [
                                f"slide {title!r}: the ```{fence_lang or 'slide'} block is not valid JSON ({exc.msg}). "
                                "For a chart, a CSV block is easier:\n```chart line\ny_title: Revenue ($M)\n"
                                "Quarter,Revenue\nQ1,4.1\nQ2,4.6\n```"
                            ]
                        ) from exc
                fence = None
            else:
                fence.append(line)
            continue
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip().lower()
            first = re.split(r"[\s:]+", lang, maxsplit=1)[0] if lang else ""
            if first in (
                "slide",
                "json",
                "chart",
                "diagram",
                "table",
                "kpi",
                "kpis",
                "metrics",
                "",
            ):
                fence = []
                fence_lang = lang
                continue
        if in_notes:
            notes.append(line)
            continue
        s = line.strip()
        if not s:
            flush_table()
            continue
        m = re.match(r"^(speaker\s+notes|notes)\s*:\s*(.*)$", s, re.I)
        if m:
            in_notes = True
            if m.group(2):
                notes.append(m.group(2))
            continue
        m = re.match(r"^sources?\s*:\s*(.*)$", s, re.I)
        if m:
            slide["source"] = m.group(1).strip()
            continue
        m = re.match(r"^(?:note|footnote)\s*:\s*(.*)$", s, re.I)
        if m:
            slide["footnote"] = m.group(1).strip()
            continue
        m = re.match(r"^(?:x[\s_-]*)?axis\s*:\s*(.+)$", s, re.I)
        if m:
            slide["x_axis"] = m.group(1).strip()
            continue
        m = re.match(r"^(?:diagram|layout)\s*:\s*([\w -]+)$", s, re.I)
        if m:
            diagram = m.group(1).strip().lower()
            continue
        if s.startswith("### "):
            columns.append({"heading": s[4:].strip(), "bullets": []})
            target = columns[-1]["bullets"]
            continue
        if s.startswith("|"):
            if not _TABLE_SEP.match(s):
                table.append(_cells(s))
            continue
        m = _IMAGE.match(s)
        if m:
            slide["image"] = {"alt": m.group(1), "path": m.group(2)}
            continue
        if s.startswith(">"):
            quote.append(s.lstrip("> ").strip())
            continue
        m = _BULLET.match(line)
        if m:
            indent = len(m.group(1).replace("\t", "    "))
            level = (
                indent // 2
                if indent < 4
                else indent // 4 + (1 if indent % 4 >= 2 else 0)
            )
            target.append({"text": m.group(2).strip(), "level": min(level, 4)})
            continue
        if columns:
            col = columns[-1]
            col["text"] = f"{col['text']} {s}".strip() if col.get("text") else s
            continue
        text.append(s)
    flush_table()

    if notes:
        slide["notes"] = "\n".join(n.strip() for n in notes).strip()
    if extra:
        slide.update(extra)
        slide.setdefault(
            "type",
            (
                "chart"
                if ("series" in extra or "chart_type" in extra)
                else slide.get("type")
            ),
        )
        commentary = [b for b in bullets] + [{"text": t, "level": 0} for t in text]
        if commentary and "bullets" not in slide:
            if slide.get("type") == "table":
                # A table slide has no bullet area: keep the words visible as its note line.
                note = "; ".join(c["text"] for c in commentary)
                slide["footnote"] = "; ".join(
                    x for x in (slide.get("footnote"), note) if x
                )
            else:
                slide["bullets"] = commentary
        return {
            k: v
            for k, v in slide.items()
            if v is not None and k not in ("columns_table", "rows_table", "image")
        }
    if "columns_table" in slide:
        slide["type"] = "table"
        slide["columns"] = slide.pop("columns_table")
        slide["rows"] = slide.pop("rows_table")
        if text:
            slide["footnote"] = slide.get("footnote") or " ".join(text)
        return slide
    if "image" in slide:
        img = slide.pop("image")
        slide.update(
            {"type": "image", "path": img["path"], "alt": img["alt"] or slide["title"]}
        )
        if bullets:
            slide["bullets"] = bullets
        if text:
            slide["caption"] = " ".join(text)
        return slide
    if diagram or len(columns) >= 3:
        return _diagram_slide(slide, diagram or "boxes", columns, bullets, text)
    if len(columns) == 2:
        slide["type"] = "two_column"
        slide["left"], slide["right"] = columns
        return slide
    if quote and not bullets and not text:
        slide["type"] = "quote"
        q = " ".join(quote)
        m = re.match(r"^(.*?)\s+[—–-]{1,2}\s*([^—–-]+)$", q)
        if m:
            slide["quote"], slide["attribution"] = (
                m.group(1).strip(' "“”'),
                m.group(2).strip(),
            )
        else:
            slide["quote"] = q.strip(' "“”')
        return slide
    for col in columns:  # a single "###" heading: keep it as a bold line
        bullets.append({"text": col["heading"], "bold": True})
        if col.get("text"):
            bullets.append({"text": col["text"], "level": 1})
        bullets.extend(
            {"text": b["text"], "level": b["level"] + 1} for b in col["bullets"]
        )
    if (
        re.match(r"^(agenda|contents|outline|today)\b", slide["title"], re.I)
        and bullets
        and not text
    ):
        slide["type"] = "agenda"
        slide["items"] = [b["text"] for b in bullets if b.get("level", 0) == 0]
        return slide
    if not bullets and not text and not quote:
        slide["type"] = "section"
        return slide
    slide["type"] = "bullets"
    if text:
        slide["text"] = text
    if bullets:
        slide["bullets"] = bullets
    if quote:
        slide.setdefault("text", []).append("“" + " ".join(quote) + "”")
    return slide


def _diagram_slide(
    slide: Dict[str, Any],
    kind: str,
    columns: List[Dict[str, Any]],
    bullets: List[Dict[str, Any]],
    text: List[str],
) -> Dict[str, Any]:
    """A slide of ``###`` headings (or top-level bullets) drawn as a diagram.

    Each heading, or each top-level bullet, is one box or step; the bullets
    under it are its detail.
    """
    if columns:
        items = [
            {"title": c["heading"], "text": c.get("text", ""), "bullets": c["bullets"]}
            for c in columns
        ]
        loose = bullets
    else:
        items, loose = [], []
        for b in bullets:
            if b.get("level", 0) == 0:
                items.append({"title": b["text"], "bullets": []})
            elif items:
                items[-1]["bullets"].append(
                    {"text": b["text"], "level": b["level"] - 1}
                )
    lines = [b["text"] for b in loose] + text
    if lines:
        slide["footnote"] = "; ".join(
            x for x in (slide.get("footnote"), " ".join(lines)) if x
        )
    slide.update({"type": "diagram", "diagram": kind, "items": items})
    return slide


def outline_from_markdown(md: str) -> Dict[str, Any]:
    """Parse Markdown into ``{"slides": [...]}``. Raises OutlineError when empty."""
    if not md or not md.strip():
        raise OutlineError(["The Markdown is empty."])
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # "Must show: a; b" lines (outside code blocks) list the required_items.
    required: List[str] = []
    kept: List[str] = []
    fenced = False
    for line in lines:
        if line.strip().startswith("```"):
            fenced = not fenced
        m = None if fenced else _REQUIRED.match(line)
        if m:
            required += [x.strip() for x in re.split(r"[;|]", m.group(2)) if x.strip()]
        else:
            kept.append(line)
    lines = kept
    # Drop YAML front matter.
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            lines = lines[end + 1 :]
    slides: List[Dict[str, Any]] = []
    title: Optional[str] = None
    block: List[str] = []
    deck_title = None
    cover_lines: List[str] = []
    in_fence = False
    stage = "start"

    def close():
        nonlocal block, title
        if title is not None:
            slides.append(_slide_from_block(title, block))
        elif stage == "cover" and deck_title is not None:
            pass
        title, block = None, []

    for line in lines:
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
        if (
            not in_fence
            and re.match(r"^#\s+\S", s)
            and deck_title is None
            and not slides
            and title is None
        ):
            deck_title = s[2:].strip()
            stage = "cover"
            continue
        if not in_fence and (
            re.match(r"^##\s+\S", s) or (s == "---" and (title is not None or block))
        ):
            if stage == "cover":
                stage = "body"
            close()
            title = s[3:].strip() if s.startswith("##") else ""
            continue
        if stage == "cover":
            cover_lines.append(line)
        else:
            if title is None and s:
                title = ""
            block.append(line)
    close()
    out: List[Dict[str, Any]] = []
    if deck_title is not None:
        sub = [
            line.strip()
            for line in cover_lines
            if line.strip()
            and not line.strip().lower().startswith(("notes:", "speaker notes:"))
        ]
        cover: Dict[str, Any] = {"type": "title", "title": deck_title}
        if sub:
            cover["subtitle"] = sub[0]
            if len(sub) > 1:
                cover["date"] = " | ".join(sub[1:3])
        out.append(cover)
    for s in slides:
        if not s.get("title") and s.get("type") not in ("quote",):
            s["title"] = ""
        out.append(s)
    if not out:
        raise OutlineError(
            ["No slides found: start each slide with a '## Title' line."]
        )
    result: Dict[str, Any] = {"slides": out}
    if required:
        result["required_items"] = required
    return result
