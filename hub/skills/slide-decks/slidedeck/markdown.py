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
- A fenced ```` ```slide ```` (or ```` ```json ````) block holds slide fields as
  JSON, merged into the slide — the way to declare a chart or diagram.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .outline import OutlineError

_BULLET = re.compile(r"^(\s*)(?:[-*+•]|\d+[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
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
    extra: Dict[str, Any] = {}
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
                try:
                    extra.update(json.loads("\n".join(fence) or "{}"))
                except json.JSONDecodeError as exc:
                    raise OutlineError(
                        [
                            f"slide {title!r}: the ```slide block is not valid JSON ({exc.msg})."
                        ]
                    ) from exc
                fence = None
            else:
                fence.append(line)
            continue
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip().lower()
            if lang in ("slide", "json", "chart", "diagram", ""):
                fence = []
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
        if text and "bullets" not in slide:
            slide["bullets"] = text
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
    for col in columns:  # one or 3+ "###" headings: keep them as bold lines
        bullets.append({"text": col["heading"], "bold": True})
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


def outline_from_markdown(md: str) -> Dict[str, Any]:
    """Parse Markdown into ``{"slides": [...]}``. Raises OutlineError when empty."""
    if not md or not md.strip():
        raise OutlineError(["The Markdown is empty."])
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
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
    return {"slides": out}
