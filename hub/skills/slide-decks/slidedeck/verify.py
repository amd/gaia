# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check a saved .pptx: structure, layout, data fidelity, style and accessibility.

Works on any deck, not only ones this skill built. Every check reads the file
itself — inherited font sizes are resolved through the placeholder, layout
and master, colours through the theme — and every finding names the slide,
the shape, what is wrong and how to fix it. Optionally compares a rendered PDF of
the deck (LibreOffice, run by the tool layer) against the shape boxes.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.oxml.ns import qn
from pptx.util import Emu

from .style import (
    LARGE_BOLD_PT,
    LARGE_TEXT_PT,
    MIN_CONTRAST,
    MIN_CONTRAST_LARGE,
    NON_ACTION_TITLE_KINDS,
    contrast,
    count_words,
    mix,
    norm_hex,
    rules_for,
    style_score,
    tokens_from_theme_xml,
)
from .text import EMU_PER_PT, LINE_SPACING, MEASURER, FontSpec

EMU_IN = 914400
TITLE_TYPES = (
    PP_PLACEHOLDER.TITLE,
    PP_PLACEHOLDER.CENTER_TITLE,
    PP_PLACEHOLDER.VERTICAL_TITLE,
)
FOOTER_NAMES = (
    "source",
    "slide number",
    "footer",
    "footnote",
    "caption",
    "logo",
    "date",
)
MAX_ISSUES = 80
RENDER_TIMEOUT_S = 240
_TODO = re.compile(r"\bTODO\b|\[\s*fill[^\]]*\]|<\s*fill[^>]*>|lorem ipsum", re.I)
_SOURCE = re.compile(r"\b(sources?|data|based on)\s*:", re.I)
_UNIT = re.compile(
    r"[%$€£¥]|\b(usd|eur|gbp|units?|hours?|hrs|days?|ms|sec|min|km|kg|mb|gb|tb|pts?|pp|m|k|bn|mn|thousands?|millions?|billions?|count|#|per)\b",
    re.I,
)
_CHART_WORDS = re.compile(
    r"\b(chart|graph|plot|trend|over time|by month|by quarter|by region|share|growth|vs\.?|versus)\b",
    re.I,
)


def pt(emu: float) -> float:
    return emu / EMU_PER_PT


def inch(emu: float) -> float:
    return emu / EMU_IN


class Issues:
    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def add(
        self,
        slide: Optional[int],
        check: str,
        severity: str,
        detail: str,
        fix: str = "",
        shape: str = "",
    ) -> None:
        item: Dict[str, Any] = {
            "slide": slide,
            "check": check,
            "severity": severity,
            "detail": detail,
        }
        if shape:
            item["shape"] = shape
        if fix:
            item["fix"] = fix
        self.items.append(item)


# ----------------------------------------------------------------------
# Theme and inheritance
# ----------------------------------------------------------------------


class DeckContext:
    """Resolves inherited sizes, fonts and colours for one presentation."""

    def __init__(self, prs) -> None:
        self.prs = prs
        self.W, self.H = int(prs.slide_width), int(prs.slide_height)
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT

        blob = None
        theme: Dict[str, Any] = {"colors": {}, "fonts": {}}
        try:
            blob = prs.slide_master.part.part_related_by(RT.THEME).blob
            theme = tokens_from_theme_xml(blob)
        except KeyError:
            pass
        self.theme_colors = theme.get("colors", {})
        self.fonts = theme.get("fonts", {})
        self.scheme = self._scheme(blob) if blob else {}
        self.default_size = 18.0
        dts = prs.part._element.find(qn("p:defaultTextStyle"))  # noqa: SLF001
        if dts is not None:
            r = dts.find(f"{qn('a:lvl1pPr')}/{qn('a:defRPr')}")
            if r is not None and r.get("sz"):
                self.default_size = int(r.get("sz")) / 100

    @staticmethod
    def _scheme(blob: bytes) -> Dict[str, str]:
        from lxml import etree

        ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
        root = etree.fromstring(blob)
        out = {}
        scheme = root.find(".//a:clrScheme", ns)
        if scheme is None:
            return out
        for el in scheme:
            name = el.tag.split("}")[1]
            if len(el):
                c = el[0]
                v = (
                    norm_hex(c.get("lastClr") or "")
                    if c.tag.endswith("sysClr")
                    else norm_hex(c.get("val") or "")
                )
                if v:
                    out[name] = v
        alias = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}
        for k, v in alias.items():
            if v in out:
                out[k] = out[v]
        return out

    # -- colours ------------------------------------------------------

    def color_of(self, el) -> Optional[str]:
        """Hex colour of an ``a:solidFill``-like element (srgb/scheme/sys + lum mods)."""
        if el is None:
            return None
        child = el.find(qn("a:srgbClr"))
        base = None
        if child is not None:
            base = norm_hex(child.get("val") or "")
        else:
            child = el.find(qn("a:schemeClr"))
            if child is not None:
                base = self.scheme.get(child.get("val", ""))
            else:
                child = el.find(qn("a:sysClr"))
                if child is not None:
                    base = norm_hex(child.get("lastClr") or "")
        if base is None or child is None:
            return base
        mod = child.find(qn("a:lumMod"))
        off = child.find(qn("a:lumOff"))
        if mod is not None or off is not None:
            m = int(mod.get("val")) / 100000 if mod is not None else 1.0
            o = int(off.get("val")) / 100000 if off is not None else 0.0
            if o > 0:
                base = mix(base, o)
            elif m < 1:
                base = mix(base, -(1 - m))
        tint = child.find(qn("a:tint"))
        if tint is not None:
            base = mix(base, 1 - int(tint.get("val")) / 100000)
        shade = child.find(qn("a:shade"))
        if shade is not None:
            base = mix(base, -(1 - int(shade.get("val")) / 100000))
        return base

    def slide_background(self, slide) -> Optional[str]:
        for owner in (slide, slide.slide_layout, slide.slide_layout.slide_master):
            bg = owner._element.find(f"{qn('p:cSld')}/{qn('p:bg')}")  # noqa: SLF001
            if bg is None:
                continue
            pr = bg.find(qn("p:bgPr"))
            if pr is not None:
                c = self.color_of(pr.find(qn("a:solidFill")))
                if c:
                    return c
                if (
                    pr.find(qn("a:blipFill")) is not None
                    or pr.find(qn("a:gradFill")) is not None
                ):
                    return None  # an image or gradient: contrast cannot be judged here
            ref = bg.find(qn("p:bgRef"))
            if ref is not None:
                c = self.color_of(ref)
                if c:
                    return c
        return self.scheme.get("lt1", "FFFFFF")

    def shape_fill(self, shape) -> Optional[str]:
        sppr = shape._element.find(qn("p:spPr"))  # noqa: SLF001
        if sppr is None:
            return None
        if sppr.find(qn("a:noFill")) is not None:
            return None
        sf = sppr.find(qn("a:solidFill"))
        if sf is not None:
            return self.color_of(sf)
        style = shape._element.find(qn("p:style"))  # noqa: SLF001
        if (
            style is not None
            and sppr.find(qn("a:gradFill")) is None
            and sppr.find(qn("a:blipFill")) is None
        ):
            fr = style.find(qn("a:fillRef"))
            if fr is not None and fr.get("idx") not in ("0", None):
                return self.color_of(fr)
        return None

    # -- inherited text properties -------------------------------------

    def _ph_chain(self, shape):
        """lstStyle-bearing elements from the slide placeholder up to the master."""
        chain = []
        if not getattr(shape, "is_placeholder", False):
            return chain, None
        try:
            pf = shape.placeholder_format
        except ValueError:
            return chain, None
        layout = (
            shape.part.slide_layout if hasattr(shape.part, "slide_layout") else None
        )
        kind = pf.type
        if layout is not None:
            lp = next(
                (p for p in layout.placeholders if p.placeholder_format.idx == pf.idx),
                None,
            )
            if lp is None:
                lp = next(
                    (
                        p
                        for p in layout.placeholders
                        if p.placeholder_format.type == kind
                    ),
                    None,
                )
            if lp is not None:
                chain.append(lp._element)  # noqa: SLF001
            master = layout.slide_master
            mtype = (
                PP_PLACEHOLDER.TITLE
                if kind in TITLE_TYPES
                else (
                    PP_PLACEHOLDER.BODY
                    if kind
                    in (
                        PP_PLACEHOLDER.BODY,
                        PP_PLACEHOLDER.OBJECT,
                        PP_PLACEHOLDER.SUBTITLE,
                    )
                    else kind
                )
            )
            mp = next(
                (p for p in master.placeholders if p.placeholder_format.type == mtype),
                None,
            )
            if mp is not None:
                chain.append(mp._element)  # noqa: SLF001
        return chain, kind

    def _master_style(self, kind):
        styles = self.prs.slide_master._element.find(qn("p:txStyles"))  # noqa: SLF001
        if styles is None:
            return None
        if kind in TITLE_TYPES:
            return styles.find(qn("p:titleStyle"))
        if kind in (
            PP_PLACEHOLDER.BODY,
            PP_PLACEHOLDER.OBJECT,
            PP_PLACEHOLDER.SUBTITLE,
        ):
            return styles.find(qn("p:bodyStyle"))
        return styles.find(qn("p:otherStyle"))

    def _lvl_attr(
        self, holder, level: int, attr: str, child: Optional[str] = "a:defRPr"
    ):
        if holder is None:
            return None
        ppr = holder.find(qn(f"a:lvl{level + 1}pPr"))
        if ppr is None:
            return None
        el = ppr.find(qn(child)) if child else ppr
        return None if el is None else el.get(attr)

    def text_props(self, shape, para, run) -> Dict[str, Any]:
        """Effective size (pt), bold, font family and colour of one run."""
        level = para.level
        rpr = run._r.find(qn("a:rPr")) if run is not None else None  # noqa: SLF001
        size = rpr.get("sz") if rpr is not None else None
        bold = rpr.get("b") if rpr is not None else None
        latin = rpr.find(qn("a:latin")) if rpr is not None else None
        family = latin.get("typeface") if latin is not None else None
        color = self.color_of(rpr.find(qn("a:solidFill"))) if rpr is not None else None
        holders = []
        txbody = shape._element.find(qn("p:txBody"))  # noqa: SLF001
        if txbody is None:
            txbody = shape._element.find(qn("a:txBody"))  # noqa: SLF001
        if txbody is not None:
            holders.append(txbody.find(qn("a:lstStyle")))
        chain, kind = self._ph_chain(shape)
        for el in chain:
            tb = el.find(qn("p:txBody"))
            if tb is not None:
                holders.append(tb.find(qn("a:lstStyle")))
        if kind is not None:
            holders.append(self._master_style(kind))
        else:
            dts = self.prs.part._element.find(qn("p:defaultTextStyle"))  # noqa: SLF001
            holders.append(dts)
        assumed = False
        for h in holders:
            if size is None:
                size = self._lvl_attr(h, level, "sz")
            if bold is None:
                bold = self._lvl_attr(h, level, "b")
            if family is None and h is not None:
                ppr = h.find(qn(f"a:lvl{level + 1}pPr"))
                if ppr is not None:
                    d = ppr.find(qn("a:defRPr"))
                    if d is not None:
                        lat = d.find(qn("a:latin"))
                        if lat is not None:
                            family = lat.get("typeface")
                        if color is None:
                            color = self.color_of(d.find(qn("a:solidFill")))
        if size is None:
            size = self.default_size * 100
            assumed = True
        is_title = kind in TITLE_TYPES
        if family in (None, "+mn-lt", "+mn-ea"):
            family = (
                self.fonts.get("body", "Calibri")
                if not (family is None and is_title)
                else self.fonts.get("title", "Calibri")
            )
        elif family in ("+mj-lt", "+mj-ea"):
            family = self.fonts.get("title", "Calibri")
        if color is None:
            color = self.scheme.get("tx1") or self.scheme.get("dk1")
        return {
            "size": int(size) / 100,
            "bold": bold in ("1", "true"),
            "family": family,
            "color": color,
            "assumed": assumed,
        }


# ----------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------


def bbox(shape) -> Optional[Tuple[int, int, int, int]]:
    """(left, top, right, bottom) in EMU, accounting for 90/270 rotation."""
    if shape.left is None or shape.width is None:
        return None
    l, t, w, h = int(shape.left), int(shape.top), int(shape.width), int(shape.height)
    rot = 0.0
    try:
        rot = float(shape.rotation or 0) % 180
    except (AttributeError, ValueError):
        pass
    if 45 <= rot < 135:
        cx, cy = l + w / 2, t + h / 2
        l, t, w, h = int(cx - h / 2), int(cy - w / 2), h, w
    return l, t, l + w, t + h


def _area(b) -> float:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _intersect(a, b) -> Tuple[int, int, int, int]:
    return max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])


def _has_text(shape) -> bool:
    return bool(
        getattr(shape, "has_text_frame", False)
        and shape.has_text_frame
        and shape.text_frame.text.strip()
    )


def _is_footerish(shape, H: int) -> bool:
    name = (shape.name or "").lower()
    if any(n in name for n in FOOTER_NAMES):
        return True
    if getattr(shape, "is_placeholder", False):
        try:
            if shape.placeholder_format.type in (
                PP_PLACEHOLDER.FOOTER,
                PP_PLACEHOLDER.SLIDE_NUMBER,
                PP_PLACEHOLDER.DATE,
            ):
                return True
        except ValueError:
            pass
    b = bbox(shape)
    if b and b[1] > H * 0.86 and _has_text(shape) and len(shape.text_frame.text) < 400:
        return True
    return False


def _kind(shape) -> str:
    """content | text | decor | connector | footer-ish categories for overlap rules."""
    st = shape.shape_type
    if st in (MSO_SHAPE_TYPE.LINE,) or shape.__class__.__name__ == "Connector":
        return "connector"
    if getattr(shape, "has_chart", False) and shape.has_chart:
        return "chart"
    if getattr(shape, "has_table", False) and shape.has_table:
        return "table"
    if st == MSO_SHAPE_TYPE.PICTURE or shape.__class__.__name__ == "Picture":
        return "picture"
    if st == MSO_SHAPE_TYPE.GROUP:
        return "group"
    if _has_text(shape):
        return "text"
    return "decor"


# ----------------------------------------------------------------------
# Text fit estimate
# ----------------------------------------------------------------------


def _insets(tf) -> Tuple[int, int, int, int]:
    bp = tf._txBody.find(qn("a:bodyPr"))  # noqa: SLF001
    d = {"lIns": 91440, "tIns": 45720, "rIns": 91440, "bIns": 45720}
    if bp is not None:
        for k in d:
            if bp.get(k) is not None:
                d[k] = int(bp.get(k))
    return d["lIns"], d["tIns"], d["rIns"], d["bIns"]


def _autofit(tf) -> Tuple[str, float]:
    bp = tf._txBody.find(qn("a:bodyPr"))  # noqa: SLF001
    if bp is None:
        return "none", 1.0
    if bp.find(qn("a:spAutoFit")) is not None:
        return "shape", 1.0
    na = bp.find(qn("a:normAutofit"))
    if na is not None:
        return "shrink", int(na.get("fontScale", "100000")) / 100000
    return "none", 1.0


def estimate_text_height(
    ctx: DeckContext, shape, width_pt: float, scale: float = 1.0
) -> Tuple[float, float, List[Tuple[float, str]]]:
    """(needed height pt, smallest size pt, per-paragraph (size, text))."""
    total = 0.0
    smallest = 999.0
    paras = []
    for i, p in enumerate(shape.text_frame.paragraphs):
        runs = list(p.runs)
        text = "".join(r.text for r in runs)
        if runs:
            props = [ctx.text_props(shape, p, r) for r in runs]
            size = max(pr["size"] for pr in props) * scale
            smallest = min(smallest, min(pr["size"] for pr in props) * scale)
            bold = any(pr["bold"] for pr in props)
            family = props[0]["family"]
        else:
            size, bold, family = (
                ctx.default_size * scale,
                False,
                ctx.fonts.get("body", "Calibri"),
            )
        ppr = p._p.find(qn("a:pPr"))  # noqa: SLF001
        marl = (
            int(ppr.get("marL"))
            if ppr is not None and ppr.get("marL")
            else (int(Emu(int((20 + 23 * p.level) * EMU_PER_PT))) if p.level else 0)
        )
        spacing = LINE_SPACING
        before = 0.0
        if ppr is not None:
            ls = ppr.find(f"{qn('a:lnSpc')}/{qn('a:spcPct')}")
            if ls is not None:
                spacing = LINE_SPACING * int(ls.get("val")) / 100000
            sb = ppr.find(f"{qn('a:spcBef')}/{qn('a:spcPts')}")
            if sb is not None:
                before = int(sb.get("val")) / 100
        lines = (
            MEASURER.line_count(
                text, FontSpec(family, bold), size, max(1.0, width_pt - pt(marl))
            )
            if text
            else 1
        )
        total += lines * size * spacing + (before if i else 0)
        paras.append((size, text))
    return total, (smallest if smallest < 999 else 0.0), paras


# ----------------------------------------------------------------------
# Charts
# ----------------------------------------------------------------------


def chart_series(chart) -> List[Dict[str, Any]]:
    out = []
    for plot in chart.plots:
        try:
            cats = [str(c) for c in plot.categories]
        except (AttributeError, ValueError, KeyError):
            cats = []
        for s in plot.series:
            try:
                vals = list(s.values)
            except (AttributeError, ValueError, KeyError):
                vals = []
            out.append(
                {
                    "name": s.name,
                    "values": vals,
                    "categories": cats,
                    "plot": plot.__class__.__name__,
                }
            )
    return out


def workbook_values(chart) -> Optional[Dict[str, List[Any]]]:
    """Series name -> values as stored in the chart's embedded workbook."""
    try:
        blob = chart.part.chart_workbook.xlsx_part.blob
    except AttributeError:
        return None
    if not blob:
        return None
    from openpyxl import load_workbook

    try:
        wb = load_workbook(BytesIO(blob), data_only=True, read_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        return None
    try:
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    if not rows:
        return {}
    header = rows[0]
    out: Dict[str, List[Any]] = {}
    for j, name in enumerate(header):
        if name is None or j == 0:
            continue
        out.setdefault(str(name), [r[j] if j < len(r) else None for r in rows[1:]])
    return out


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _is_plot_image(shape) -> bool:
    """A picture that is a rendered chart (matplotlib/plotly/Excel export)."""
    try:
        blob = shape.image.blob
    except (AttributeError, ValueError, KeyError):
        return False
    head = blob[:8192]
    if any(
        tag in head
        for tag in (
            b"matplotlib",
            b"Matplotlib",
            b"plotly",
            b"Plotly",
            b"ggplot",
            b"Chart",
            b"bokeh",
            b"seaborn",
        )
    ):
        return True
    descr = (
        shape._element.xpath("./*[1]/p:cNvPr/@descr")
        if hasattr(shape, "_element")
        else []
    )  # noqa: SLF001
    name = (shape.name or "") + " " + " ".join(descr)
    return bool(re.search(r"\b(chart|plot|graph|figure|fig\.?)\b", name, re.I))


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


_NORM = re.compile(r"[^\w%$€£]+", re.UNICODE)


def _tokens(text: str) -> List[str]:
    return [t for t in (_NORM.sub("", w).lower() for w in text.split()) if t]


def render_check(
    pdf: Path, prs, ctx: DeckContext, issues: Issues, render_dir: Optional[Path]
) -> Dict[str, Any]:
    """Compare the rendered PDF against the boxes text was meant to stay in."""
    import pymupdf

    doc = pymupdf.open(str(pdf))
    try:
        pngs = []
        if doc.page_count != len(prs.slides):
            issues.add(
                None,
                "render",
                "warning",
                f"the renderer produced {doc.page_count} pages for {len(prs.slides)} slides.",
            )
        sx = doc[0].rect.width / pt(ctx.W) if doc.page_count else 1.0
        for i, (page, slide) in enumerate(zip(doc, prs.slides), 1):
            words = page.get_text("words")  # x0, y0, x1, y1, word, block, line, n
            toks = [
                (
                    w[0] / sx,
                    w[1] / sx,
                    w[2] / sx,
                    w[3] / sx,
                    _NORM.sub("", w[4]).lower(),
                )
                for w in words
            ]
            for shape in slide.shapes:
                if not _has_text(shape) or _kind(shape) != "text":
                    continue
                b = bbox(shape)
                if b is None:
                    continue
                seq = _tokens(shape.text_frame.text)
                if not seq:
                    continue
                left, top, right, bottom = (pt(v) for v in b)
                col = [
                    t
                    for t in toks
                    if left - 3 <= (t[0] + t[2]) / 2 <= right + 3 and t[1] >= top - 6
                ]
                last = seq[-3:]
                found_y = None
                for k in range(len(col) - len(last) + 1):
                    if [c[4] for c in col[k : k + len(last)]] == last:
                        y1 = max(c[3] for c in col[k : k + len(last)])
                        # The shape's closing words: the last occurrence in its column.
                        if found_y is None or y1 > found_y:
                            found_y = y1
                if found_y is None:
                    continue
                if found_y > bottom + 4:
                    issues.add(
                        i,
                        "render_overflow",
                        "error",
                        f"rendered text ends {found_y - bottom:.0f}pt below its box.",
                        "Shorten the text or give it a larger box.",
                        shape.name,
                    )
                if found_y > pt(ctx.H) + 1:
                    issues.add(
                        i,
                        "render_offslide",
                        "error",
                        "rendered text runs off the bottom of the slide.",
                        "Shorten the text.",
                        shape.name,
                    )
            if render_dir is not None:
                target = render_dir / f"slide_{i:02d}.png"
                page.get_pixmap(dpi=80).save(str(target))
                pngs.append(str(target))
    finally:
        doc.close()
    out: Dict[str, Any] = {"rendered": True}
    if pngs:
        out["pngs"] = pngs
    return out


# ----------------------------------------------------------------------
# The verifier
# ----------------------------------------------------------------------


def _slide_title(slide) -> Tuple[Optional[Any], str]:
    for ph in slide.placeholders:
        try:
            if ph.placeholder_format.type in TITLE_TYPES:
                return ph, ph.text_frame.text.strip() if ph.has_text_frame else ""
        except ValueError:
            continue
    return None, ""


def _slide_kind(slide, title_ph) -> str:
    layout = (slide.slide_layout.name or "").lower()
    if title_ph is not None:
        try:
            if (
                title_ph.placeholder_format.type == PP_PLACEHOLDER.CENTER_TITLE
                or "title slide" in layout
            ):
                return "title"
        except ValueError:
            pass
    if "section" in layout:
        return "section"
    kinds = Counter(_kind(s) for s in slide.shapes)
    if kinds["chart"]:
        return "chart"
    if kinds["table"]:
        return "table"
    if any((s.name or "").startswith("KPI tile") for s in slide.shapes):
        return "kpi"
    if kinds["picture"] and not any(
        _kind(s) == "picture" and (s.name or "") == "Logo" for s in slide.shapes
    ):
        return "image"
    if (
        kinds["decor"] + kinds["connector"] >= 3
        or sum(1 for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE)
        >= 3
    ):
        return "diagram"
    return "text"


def _color_set(ctx: DeckContext, slide) -> Counter:
    seen: Counter = Counter()
    for shape in slide.shapes:
        k = _kind(shape)
        if k in ("picture", "chart", "group"):
            continue
        f = ctx.shape_fill(shape) if k != "table" else None
        if f:
            seen[f] += 1
        if _has_text(shape):
            for p in shape.text_frame.paragraphs:
                for r in p.runs:
                    rpr = r._r.find(qn("a:rPr"))  # noqa: SLF001
                    c = (
                        ctx.color_of(rpr.find(qn("a:solidFill")))
                        if rpr is not None
                        else None
                    )
                    if c:
                        seen[c] += 1
    return seen


def _on_theme(color: str, theme_hexes: Sequence[str]) -> bool:
    """True when *color* is a theme colour or a tint/shade of one."""
    c = color.upper()
    if c in theme_hexes or c in ("FFFFFF", "000000", "111827"):
        return True
    cc = [int(c[i : i + 2], 16) for i in (0, 2, 4)]
    for t in theme_hexes:
        tt = [int(t[i : i + 2], 16) for i in (0, 2, 4)]
        # Tint toward white: every channel moves the same fraction of the way to 255.
        fr, ok = [], True
        for x, y in zip(cc, tt):
            if 255 - y <= 3:
                ok = ok and x >= 252
            else:
                fr.append((x - y) / (255 - y))
        if ok and fr and all(0 <= f <= 1 for f in fr) and max(fr) - min(fr) < 0.04:
            return True
        # Shade toward black: every channel scales by the same factor.
        sh, ok = [], True
        for x, y in zip(cc, tt):
            if y <= 3:
                ok = ok and x <= 3
            else:
                sh.append(x / y)
        if ok and sh and all(0 <= f <= 1 for f in sh) and max(sh) - min(sh) < 0.04:
            return True
    return False


def _hue_families(colors: Iterable[str]) -> int:
    """Distinct colours, counting tints/shades of one colour and greys once."""
    bases: List[str] = []
    greys = False
    for c in sorted(
        colors, key=lambda h: sum(int(h[i : i + 2], 16) for i in (0, 2, 4))
    ):
        r, g, b = (int(c[i : i + 2], 16) for i in (0, 2, 4))
        if max(r, g, b) - min(r, g, b) < 18:
            greys = True
            continue
        if not any(_on_theme(c, [base]) or _on_theme(base, [c]) for base in bases):
            bases.append(c.upper())
    return len(bases) + (1 if greys else 0)


# ----------------------------------------------------------------------
# Required content
# ----------------------------------------------------------------------

_REQ_TOKEN = re.compile(r"\d+(?:\.\d+)?%?|[^\W\d_]+")
_REQ_STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "are",
    "was",
    "were",
    "has",
    "have",
    "its",
    "into",
    "over",
    "per",
    "our",
    "their",
    "than",
    "then",
    "also",
    "but",
    "not",
    "all",
}


def _req_norm(text: str) -> str:
    t = (text or "").lower().replace("−", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)  # 1,240 -> 1240


def _req_tokens(text: str) -> List[str]:
    return _REQ_TOKEN.findall(_req_norm(text))


def required_item_found(item: str, text: str) -> bool:
    """Whether *item* is stated in *text*: verbatim, or by its numbers and most of its words.

    Every number in the item must appear; at least three quarters of its other
    significant words must too (a word of four letters or more also matches
    by its first five, so "site" finds "sites" and "closures" finds "closure").
    """
    compact = lambda x: " ".join(_req_tokens(x))  # noqa: E731
    want = compact(item)
    if want and want in compact(text):
        return True
    toks = [
        t
        for t in _req_tokens(item)
        if t not in _REQ_STOP and (len(t) > 2 or t[0].isdigit())
    ]
    if not toks:
        return False
    have = set(_req_tokens(text))
    have_bare = {h.rstrip("%") for h in have}
    for n in (t for t in toks if t[0].isdigit()):
        if n not in have and n.rstrip("%") not in have_bare:
            return False
    words = [t for t in toks if not t[0].isdigit()]
    if not words:
        return True

    def present(w: str) -> bool:
        return w in have or (len(w) >= 4 and any(h.startswith(w[:5]) for h in have))

    hit = sum(1 for w in words if present(w))
    return hit / len(words) >= 0.75


def visible_text(slide) -> str:
    """Everything an audience can read on *slide*: text, table cells, chart labels and values."""
    parts: List[str] = []

    def walk(shapes):
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                walk(shape.shapes)
                continue
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                parts.extend(c.text for row in shape.table.rows for c in row.cells)
            if getattr(shape, "has_chart", False) and shape.has_chart:
                ch = shape.chart
                try:
                    if ch.has_title:
                        parts.append(ch.chart_title.text_frame.text)
                except (AttributeError, ValueError):
                    pass
                for s in chart_series(ch):
                    parts.append(str(s["name"]))
                    parts.extend(str(c) for c in s["categories"])
                    parts.extend(_fmt_value(v) for v in s["values"] if v is not None)

    walk(slide.shapes)
    return "\n".join(parts)


def _fmt_value(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def check_required(prs, items: Sequence[str], issues: "Issues") -> List[Dict[str, Any]]:
    """Each required item must be on a slide; in the notes only is a failure."""
    slides = list(prs.slides)
    texts = [visible_text(s) for s in slides]
    notes = [
        s.notes_slide.notes_text_frame.text if s.has_notes_slide else "" for s in slides
    ]
    out = []
    for item in items:
        where = next(
            (i for i, t in enumerate(texts, 1) if required_item_found(item, t)), None
        )
        in_notes = where is None and any(required_item_found(item, n) for n in notes)
        out.append({"item": item, "slide": where, "only_in_notes": in_notes})
        if where is not None:
            continue
        if in_notes:
            issues.add(
                None,
                "required_in_notes_only",
                "error",
                f"{item!r} is only in the speaker notes, which the audience never sees.",
                "Put it on a slide: in a title, a bullet, a KPI tile or a chart label. Keep density limits by cutting other words, never by moving requested content to the notes.",
            )
        else:
            issues.add(
                None,
                "required_missing",
                "error",
                f"{item!r} does not appear on any slide.",
                "Add it to the slide it belongs on (title, bullet, tile, table or chart), with its number and period.",
            )
    return out


def verify_deck(
    path: str,
    expected_slides: Optional[int] = None,
    require_native_charts: bool = True,
    *,
    outline: Optional[Dict[str, Any]] = None,
    slide_map: Optional[Sequence[Tuple[int, List[int]]]] = None,
    require_notes: bool = False,
    render: bool = True,
    render_dir: Optional[str] = None,
    prefs: Optional[Dict[str, Any]] = None,
    tokens: Optional[Dict[str, Any]] = None,
    rules: Optional[Dict[str, Any]] = None,
    min_font_pt: Optional[float] = None,
    renderer: Optional[Callable[[Path], Path]] = None,
    required_items: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Check a saved deck and return a compact report.

    *renderer* turns the deck into a PDF (the tool layer runs LibreOffice
    through the skill context); without one, rendering is skipped and said so.
    """
    p = Path(path)
    prs = Presentation(str(p))
    ctx = DeckContext(prs)
    R = rules_for(prefs, rules)
    if min_font_pt is not None:
        R["min_font_pt"] = float(min_font_pt)
    if R.get("speaker_notes") is True:
        require_notes = True
    issues = Issues()
    W, H = ctx.W, ctx.H
    slides = list(prs.slides)
    n = len(slides)
    if expected_slides is not None and n != int(expected_slides):
        issues.add(
            None,
            "slide_count",
            "error",
            f"the deck has {n} slides; {expected_slides} were asked for.",
            "Add or remove slides (merge or split content) until the count is exact.",
        )

    # Outline mapping: outline index -> built slide numbers.
    expected: Dict[int, Dict[str, Any]] = {}
    if outline is not None:
        o_slides = outline.get("slides", [])
        mapping = (
            dict(slide_map)
            if slide_map
            else {s["index"]: [s["index"]] for s in o_slides}
        )
        for s in o_slides:
            for num in mapping.get(s["index"], []):
                expected[num] = s

    theme_hexes: List[str] = []
    if tokens:
        theme_hexes = [
            h for h in tokens.get("colors", {}).values() if isinstance(h, str)
        ] + list(tokens.get("palette", []))
    else:
        theme_hexes = list(ctx.scheme.values())
    theme_hexes = [h.upper() for h in theme_hexes if norm_hex(h)]

    families: Counter = Counter()
    titles: List[str] = []
    overview: List[str] = []
    title_pos: List[Tuple[int, int, int]] = []
    kinds_seq: List[str] = []
    word_counts: List[int] = []
    mx_in = 0.5
    for idx, slide in enumerate(slides, 1):
        title_ph, title = _slide_title(slide)
        spec = expected.get(idx)
        kind = _slide_kind(slide, title_ph)
        spec_kind = (spec or {}).get("kind") or (spec or {}).get("type") or kind
        kinds_seq.append(spec.get("type") if spec else kind)
        titles.append(title)
        bg = ctx.slide_background(slide)
        shapes = list(slide.shapes)

        # --- title -----------------------------------------------------
        if title_ph is None or not title:
            issues.add(
                idx,
                "no_title",
                "error",
                "the slide has no title in a title placeholder.",
                "Give every slide a title; screen readers and the outline view use it.",
            )
        else:
            if kind not in ("title", "section"):
                title_pos.append((idx, int(title_ph.left or 0), int(title_ph.top or 0)))
            words = count_words(title)
            is_action_kind = (
                spec_kind not in NON_ACTION_TITLE_KINDS
                and kind not in ("title", "section")
                and not re.match(
                    r"^(agenda|appendix|contents|questions|thank you|next steps|q\s*&\s*a)\b",
                    title,
                    re.I,
                )
            )
            if words > R["max_title_words"] and kind != "title":
                issues.add(
                    idx,
                    "title_length",
                    "warning",
                    f"title has {words} words (limit {R['max_title_words']}).",
                    "Cut it to one claim of about 12-15 words.",
                    title_ph.name,
                )
            if (
                is_action_kind
                and R["title_style"] == "action"
                and words < R["min_action_title_words"]
            ):
                issues.add(
                    idx,
                    "topic_title",
                    "warning",
                    f"title {title!r} is a topic label, not a takeaway.",
                    "Write the conclusion as a sentence, e.g. 'Revenue grew 12% on West demand'.",
                    title_ph.name,
                )
            if title.endswith(".") and not title.endswith("..."):
                issues.add(
                    idx,
                    "title_period",
                    "info",
                    "title ends with a period.",
                    "Drop the full stop from titles.",
                    title_ph.name,
                )
            if title_ph.width:
                lines_needed = 0
                for para in title_ph.text_frame.paragraphs:
                    if para.runs:
                        pr = ctx.text_props(title_ph, para, para.runs[0])
                        li, _t, ri, _b = _insets(title_ph.text_frame)
                        lines_needed += MEASURER.line_count(
                            para.text,
                            FontSpec(pr["family"], pr["bold"]),
                            pr["size"],
                            pt(int(title_ph.width) - li - ri),
                        )
                if lines_needed > R["max_title_lines"]:
                    issues.add(
                        idx,
                        "title_lines",
                        "warning",
                        f"title wraps to {lines_needed} lines.",
                        "Keep titles to two lines.",
                        title_ph.name,
                    )
        if (
            title_ph is not None
            and shapes
            and shapes[0] is not title_ph
            and shapes[0].shape_id != title_ph.shape_id
        ):
            first_content = next(
                (s for s in shapes if _kind(s) not in ("decor", "connector")), None
            )
            if (
                first_content is not None
                and first_content.shape_id != title_ph.shape_id
            ):
                issues.add(
                    idx,
                    "reading_order",
                    "warning",
                    "the title is not the first shape in reading order.",
                    "Move the title to the back of the z-order (first in the selection pane).",
                )

        # --- per-shape checks --------------------------------------------
        body_words = 0
        bullets = 0
        content: List[Tuple[Any, Tuple[int, int, int, int], str]] = []
        has_chart = has_table_num = has_kpi = False
        source_text = ""
        pictures_not_chart = []
        for shape in shapes:
            k = _kind(shape)
            b = bbox(shape)
            footerish = _is_footerish(shape, H)
            if b is not None:
                if (
                    b[0] < -12700
                    or b[1] < -12700
                    or b[2] > W + 12700
                    or b[3] > H + 12700
                ):
                    issues.add(
                        idx,
                        "out_of_bounds",
                        "error",
                        f"'{shape.name}' extends past the slide edge.",
                        "Move or resize it inside the slide.",
                        shape.name,
                    )
                elif k not in ("decor", "connector") and not (
                    k == "picture" and _area(b) > 0.8 * W * H
                ):
                    edge = int(0.25 * EMU_IN)
                    if (
                        b[0] < edge
                        or b[2] > W - edge
                        or b[1] < edge * 0.8
                        or b[3] > H - edge * 0.8
                    ):
                        issues.add(
                            idx,
                            "margin",
                            "warning",
                            f"'{shape.name}' sits within 0.25in of the slide edge.",
                            "Keep content inside the margins (0.6in sides).",
                            shape.name,
                        )
            if (
                getattr(shape, "is_placeholder", False)
                and shape.has_text_frame
                and not shape.text_frame.text.strip()
            ):
                issues.add(
                    idx,
                    "empty_placeholder",
                    "error",
                    f"placeholder '{shape.name}' is empty (shows 'Click to add text' when editing).",
                    "Fill it or delete it.",
                    shape.name,
                )
            if _has_text(shape):
                text = shape.text_frame.text
                if _TODO.search(text):
                    issues.add(
                        idx,
                        "unfilled_slot",
                        "error",
                        f"'{shape.name}' still holds placeholder text: {text.strip()[:60]!r}.",
                        "Fill the slot from the sources, or drop the slide.",
                        shape.name,
                    )
                if footerish or _SOURCE.search(text):
                    source_text += " " + text
                tf = shape.text_frame
                is_title = title_ph is not None and shape.shape_id == title_ph.shape_id
                is_body = not is_title and not footerish
                fill = ctx.shape_fill(shape) or bg
                for para in tf.paragraphs:
                    ptext = "".join(r.text for r in para.runs).strip()
                    if not ptext:
                        continue
                    for r in para.runs:
                        if not r.text.strip():
                            continue
                        pr = ctx.text_props(shape, para, r)
                        families[pr["family"]] += 1
                        size = pr["size"]
                        if size < R["min_font_pt"]:
                            issues.add(
                                idx,
                                "tiny_font",
                                "error",
                                f"'{shape.name}' has {size:.0f}pt text (floor {R['min_font_pt']:.0f}pt).",
                                "Enlarge it or cut text; sources/footnotes may go to 11-12pt, nothing smaller.",
                                shape.name,
                            )
                        elif is_body and size < R["min_dense_pt"]:
                            issues.add(
                                idx,
                                "small_font",
                                "warning",
                                f"'{shape.name}' has {size:.0f}pt text; only sources and footnotes go below {R['min_dense_pt']}pt.",
                                "Cut words so the text can be larger.",
                                shape.name,
                            )
                        if fill and pr["color"]:
                            ratio = contrast(pr["color"], fill)
                            large = size >= LARGE_TEXT_PT or (
                                pr["bold"] and size >= LARGE_BOLD_PT
                            )
                            need = MIN_CONTRAST_LARGE if large else MIN_CONTRAST
                            if ratio < need:
                                issues.add(
                                    idx,
                                    "contrast",
                                    "error",
                                    f"text #{pr['color']} on #{fill} in '{shape.name}' has contrast {ratio:.1f}:1 (needs {need}:1).",
                                    "Use the theme's dark text on light fills and light text on dark fills.",
                                    shape.name,
                                )
                                break
                    if is_body and k == "text" and not _is_label_shape(shape):
                        body_words += count_words(ptext)
                        if _is_bullet(shape, para):
                            bullets += 1
                            if count_words(ptext) > R["max_bullet_words"]:
                                issues.add(
                                    idx,
                                    "bullet_length",
                                    "warning",
                                    f"a bullet has {count_words(ptext)} words (limit {R['max_bullet_words']}): {ptext[:50]!r}.",
                                    "Cut each bullet to one short phrase.",
                                    shape.name,
                                )
                            if para.level > R["max_nesting"]:
                                issues.add(
                                    idx,
                                    "nesting",
                                    "warning",
                                    f"bullets nest {para.level} levels deep.",
                                    "Use one level of sub-bullets at most.",
                                    shape.name,
                                )
                # fit estimate
                if k == "text" and b is not None and shape.width and shape.height:
                    li, ti, ri, bi = _insets(tf)
                    mode, scale = _autofit(tf)
                    need, _small, _p = estimate_text_height(
                        ctx, shape, pt(int(shape.width) - li - ri), scale
                    )
                    avail = pt(int(shape.height) - ti - bi)
                    if mode == "shape":
                        if pt(b[1]) + need + pt(ti + bi) > pt(H) + 2:
                            issues.add(
                                idx,
                                "text_overflow",
                                "error",
                                f"'{shape.name}' grows to fit its text and runs off the slide.",
                                "Shorten the text.",
                                shape.name,
                            )
                        elif need > avail * 1.04 + 2:
                            issues.add(
                                idx,
                                "text_overflow",
                                "error",
                                f"'{shape.name}' grows to fit its text: about {need:.0f}pt tall against the {avail:.0f}pt its frame records, over whatever is below it.",
                                "Shorten the text or make room for it.",
                                shape.name,
                            )
                    elif need > avail * 1.04 + 2:
                        issues.add(
                            idx,
                            "text_overflow",
                            "error",
                            f"'{shape.name}' needs about {need:.0f}pt of height but has {avail:.0f}pt.",
                            "Shorten the text, split the slide, or enlarge the box.",
                            shape.name,
                        )
            if k == "chart":
                has_chart = True
                _check_chart(idx, shape, issues, R, bg, spec)
            if k == "table":
                if _check_table(idx, shape, ctx, issues, R, bg):
                    has_table_num = True
            if (shape.name or "").startswith("KPI tile"):
                has_kpi = True
            if k == "picture" and (shape.name or "") != "Logo":
                descr = shape._element.xpath(
                    "./p:nvPicPr/p:cNvPr/@descr"
                )  # noqa: SLF001
                if not (descr and descr[0].strip()):
                    issues.add(
                        idx,
                        "alt_text",
                        "warning",
                        f"picture '{shape.name}' has no alt text.",
                        "Add a description of what the picture shows.",
                        shape.name,
                    )
                if require_native_charts and (
                    _is_plot_image(shape) or (spec and spec.get("type") == "chart")
                ):
                    pictures_not_chart.append(shape)
            if (
                k in ("text", "chart", "table", "picture")
                and b is not None
                and not footerish
            ):
                content.append((shape, b, k))
        if pictures_not_chart:
            issues.add(
                idx,
                "picture_not_chart",
                "error",
                f"{len(pictures_not_chart)} picture(s) look like a chart image ({pictures_not_chart[0].name}); charts must be native so the numbers stay editable.",
                "Rebuild it as a chart slide (build_deck type 'chart').",
                pictures_not_chart[0].name,
            )
        if (
            spec
            and spec.get("type") == "chart"
            and not has_chart
            and require_native_charts
            and not pictures_not_chart
        ):
            issues.add(
                idx,
                "missing_chart",
                "error",
                "the outline asked for a chart but the slide has no native chart.",
                "Rebuild the slide.",
            )
        if spec and spec.get("type") == "chart" and has_chart:
            _round_trip(idx, slide, spec.get("chart") or {}, issues)
        if spec and spec.get("todo"):
            issues.add(
                idx,
                "unfilled_slot",
                "error",
                f"blueprint slot '{spec.get('slot') or spec.get('title')}' was never filled.",
                "Fill it from the sources or remove it.",
            )

        # --- overlap and alignment --------------------------------------------
        for a_i in range(len(content)):
            sa, ba, ka = content[a_i]
            for b_i in range(a_i + 1, len(content)):
                sb, bb, kb = content[b_i]
                inter = _intersect(ba, bb)
                iw, ih = inter[2] - inter[0], inter[3] - inter[1]
                if iw <= int(0.04 * EMU_IN) or ih <= int(0.04 * EMU_IN):
                    continue
                if _area(inter) < 0.02 * min(_area(ba), _area(bb)):
                    continue
                if (sa.name or "").startswith("Background") or (
                    sb.name or ""
                ).startswith("Background"):
                    continue
                issues.add(
                    idx,
                    "overlap",
                    "error",
                    f"'{sa.name}' and '{sb.name}' overlap ({inch(iw):.2f}in x {inch(ih):.2f}in).",
                    "Move one of them or shrink both so they do not cover each other.",
                    sa.name,
                )
        lefts = sorted({b[0] for _s, b, _k in content if b[2] - b[0] >= EMU_IN})
        tol = int(R["align_tolerance_in"] * EMU_IN)
        near = [
            (x, y) for x, y in zip(lefts, lefts[1:]) if int(0.02 * EMU_IN) < y - x < tol
        ]
        if near:
            issues.add(
                idx,
                "misaligned",
                "warning",
                f"{len(near)} pair(s) of shapes have left edges {inch(near[0][1] - near[0][0]):.2f}in apart, almost but not quite aligned.",
                "Snap them to the same left edge or grid column.",
            )

        # --- density, sources, notes, colours ------------------------------------
        exec_kind = str((spec or {}).get("kind") or "").startswith("exec")
        limit = R["exec_max_body_words"] if exec_kind else R["max_body_words"]
        word_counts.append(body_words)
        if body_words > limit:
            issues.add(
                idx,
                "too_many_words",
                "warning",
                f"{body_words} words of body text (limit {limit} for {R['density']} density).",
                "Cut words or split the slide. Never move content the request asks to show into the notes; only extra detail goes there or to an appendix.",
            )
        if bullets > R["max_bullets"]:
            issues.add(
                idx,
                "too_many_bullets",
                "warning",
                f"{bullets} bullets (limit {R['max_bullets']}).",
                "Group them into 3-5 points or split the slide.",
            )
        data_slide = has_chart or has_table_num or has_kpi or bool(pictures_not_chart)
        if data_slide and not _SOURCE.search(source_text):
            issues.add(
                idx,
                "missing_source",
                "error",
                "a chart/table/metric slide with no source line.",
                "Add 'Source: <file or system>, <period>' at the foot of the slide.",
            )
        notes = (
            slide.notes_slide.notes_text_frame.text.strip()
            if slide.has_notes_slide
            else ""
        )
        if require_notes and not notes and kind not in ("title", "section"):
            issues.add(
                idx,
                "missing_notes",
                "error",
                "no speaker notes.",
                "Add the talking points to the notes.",
            )
        colors = _color_set(ctx, slide)
        hues = _hue_families(colors)
        if hues > R["max_colors_per_slide"]:
            issues.add(
                idx,
                "too_many_colors",
                "warning",
                f"{hues} distinct colours on the slide (tints of one colour count once).",
                "Use the theme: one accent for the message, greys for context.",
            )
        off = [c for c in colors if theme_hexes and not _on_theme(c, theme_hexes)]
        if off:
            issues.add(
                idx,
                "off_theme_color",
                "warning",
                f"colour(s) outside the theme: {', '.join('#' + c for c in off[:4])}.",
                "Use theme colours (or tints of them) so the deck stays consistent.",
            )
        n_charts = sum(1 for s in shapes if _kind(s) == "chart")
        n_tables = sum(1 for s in shapes if _kind(s) == "table")
        bits = [kind]
        if n_charts:
            bits.append(f"{n_charts} chart")
        if n_tables:
            bits.append(f"{n_tables} table")
        if notes:
            bits.append("notes")
        src = re.search(r"(?:sources?|data)\s*:\s*([^\n]{0,60})", source_text, re.I)
        if src:
            bits.append(f"source: {src.group(1).strip()}")
        overview.append(f"{idx}. {title or '(no title)'} [{', '.join(bits)}]")

    # --- deck-level checks ------------------------------------------------------
    counts = Counter(t.lower() for t in titles if t)
    for t, c in counts.items():
        if c > 1 and not t.endswith("(cont.)"):
            issues.add(
                None,
                "duplicate_title",
                "warning",
                f"{c} slides share the title {t!r}.",
                "Give every slide its own title; titles alone should tell the story.",
            )
    if len(title_pos) >= 3:
        common = Counter(
            (round(l / 12700 / 4), round(t / 12700 / 4)) for _i, l, t in title_pos
        ).most_common(1)[0][0]
        for i, l, t in title_pos:
            if (
                abs(round(l / 12700 / 4) - common[0]) > 2
                or abs(round(t / 12700 / 4) - common[1]) > 2
            ):
                issues.add(
                    i,
                    "title_position",
                    "warning",
                    "the title sits in a different place from the other slides.",
                    "Use the same layout so titles do not jump between slides.",
                )
    if len(families) > R["max_font_families"]:
        issues.add(
            None,
            "font_families",
            "warning",
            f"{len(families)} font families in use: {', '.join(f for f, _c in families.most_common(5))}.",
            f"Use at most {R['max_font_families']} (one for titles, one for body).",
        )
    run_len, prev = 0, None
    for i, k in enumerate(kinds_seq, 1):
        run_len = run_len + 1 if k == prev and k not in ("title", "section") else 1
        prev = k
        if run_len == R["max_same_layout_run"] + 1:
            issues.add(
                i,
                "layout_run",
                "info",
                f"{run_len} '{k}' slides in a row.",
                "Vary the layout (a chart, a KPI slide, a diagram) to keep attention.",
            )
    if n > R["section_dividers_after"] and "section" not in kinds_seq:
        issues.add(
            None,
            "no_sections",
            "info",
            f"{n} slides with no section dividers.",
            "Add section slides so the audience knows where they are.",
        )
    if outline is not None:
        groups: Dict[str, List[Tuple[int, Any]]] = {}
        for num, s in expected.items():
            g = (s.get("chart") or {}).get("axis_group")
            if g:
                groups.setdefault(g, []).append((num, s))
        for g, members in groups.items():
            scales = set()
            for num, _s in members:
                ch = next(
                    (sh.chart for sh in slides[num - 1].shapes if _kind(sh) == "chart"),
                    None,
                )
                if ch is not None:
                    try:
                        scales.add(
                            (ch.value_axis.minimum_scale, ch.value_axis.maximum_scale)
                        )
                    except (ValueError, AttributeError):
                        pass
            if len(scales) > 1:
                issues.add(
                    None,
                    "axis_consistency",
                    "warning",
                    f"charts in axis group {g!r} use different value-axis scales.",
                    "Set the same y_min/y_max on comparable charts.",
                )

    required = list(required_items or []) + [
        x
        for x in ((outline or {}).get("required_items") or [])
        if x not in (required_items or [])
    ]
    required_report = check_required(prs, required, issues) if required else []

    render_info: Dict[str, Any] = {"rendered": False}
    if render and renderer is None:
        render_info["reason"] = (
            "no renderer was available, so slides were not rendered; the overflow checks are estimates from font metrics."
        )
    elif render:
        try:
            pdf = renderer(p)
        except (RuntimeError, OSError) as exc:
            render_info["reason"] = f"rendering failed: {exc}"
        else:
            render_info = render_check(
                pdf, prs, ctx, issues, Path(render_dir) if render_dir else None
            )

    items = issues.items
    errors = [i for i in items if i["severity"] == "error"]
    summary = Counter(i["check"] for i in items)
    shown = sorted(
        items,
        key=lambda i: (
            {"error": 0, "warning": 1, "info": 2}[i["severity"]],
            i["slide"] or 0,
        ),
    )[:MAX_ISSUES]
    report: Dict[str, Any] = {
        "status": "pass" if not errors else "fail",
        "path": str(p),
        "slides": n,
        "errors": len(errors),
        "warnings": sum(1 for i in items if i["severity"] == "warning"),
        "style_score": style_score(items, n),
        "summary": dict(summary),
        "issues": shown,
        "titles": titles,
        "slides_overview": overview,
        "limits": {
            k: R[k]
            for k in (
                "density",
                "max_bullets",
                "max_bullet_words",
                "max_body_words",
                "max_title_words",
                "min_font_pt",
                "min_dense_pt",
            )
        },
        "render": render_info,
        **({"required_items": required_report} if required_report else {}),
        "text_metrics": MEASURER.method(FontSpec(ctx.fonts.get("body", "Calibri"))),
    }
    if len(items) > MAX_ISSUES:
        report["issues_not_shown"] = len(items) - MAX_ISSUES
    return report


def _is_label_shape(shape) -> bool:
    """Short labels inside diagrams and tiles are not body copy."""
    name = (shape.name or "").lower()
    return any(
        name.startswith(p)
        for p in (
            "kpi tile",
            "step ",
            "node ",
            "stage ",
            "level ",
            "layer ",
            "box ",
            "cell ",
            "quadrant",
            "milestone",
            "period ",
            "lane ",
            "bar ",
            "point ",
            "edge label",
            "x axis",
            "y axis",
            "cycle",
            "timeline",
        )
    )


def _is_bullet(shape, para) -> bool:
    ppr = para._p.find(qn("a:pPr"))  # noqa: SLF001
    if ppr is not None:
        if ppr.find(qn("a:buNone")) is not None:
            return False
        if (
            ppr.find(qn("a:buChar")) is not None
            or ppr.find(qn("a:buAutoNum")) is not None
        ):
            return True
    if getattr(shape, "is_placeholder", False):
        try:
            return shape.placeholder_format.type in (
                PP_PLACEHOLDER.BODY,
                PP_PLACEHOLDER.OBJECT,
            )
        except ValueError:
            return False
    return False


def _check_chart(
    idx: int,
    shape,
    issues: Issues,
    R: Dict[str, Any],
    bg: Optional[str],
    spec: Optional[Dict[str, Any]],
) -> None:
    chart = shape.chart
    name = shape.name
    descr = shape._element.xpath("./p:nvGraphicFramePr/p:cNvPr/@descr")  # noqa: SLF001
    if not (descr and descr[0].strip()):
        issues.add(
            idx,
            "alt_text",
            "warning",
            f"chart '{name}' has no alt text.",
            "Describe what the chart shows (type, series, period).",
            name,
        )
    try:
        ctype = str(chart.chart_type)
    except (KeyError, ValueError):
        ctype = ""
    if "THREE_D" in ctype or "3D" in ctype:
        issues.add(
            idx,
            "chart_3d",
            "warning",
            "3-D chart.",
            "Use a flat chart; 3-D distorts the values.",
            name,
        )
    series = chart_series(chart)
    if (
        chart.has_legend
        and len(series) == 1
        and "PIE" not in ctype
        and "DOUGHNUT" not in ctype
    ):
        issues.add(
            idx,
            "chart_legend",
            "warning",
            "a legend on a single-series chart.",
            "Drop the legend; name the series in the title or axis.",
            name,
        )
    if len(series) > R["max_chart_series"]:
        issues.add(
            idx,
            "chart_series",
            "warning",
            f"{len(series)} series on one chart (limit {R['max_chart_series']}).",
            "Show the series that carries the message in the accent and the rest in grey, or split the chart.",
            name,
        )
    if (
        ("PIE" in ctype or "DOUGHNUT" in ctype)
        and series
        and len(series[0]["values"]) > R["max_pie_slices"]
    ):
        issues.add(
            idx,
            "pie_slices",
            "warning",
            f"a pie with {len(series[0]['values'])} slices (limit {R['max_pie_slices']}).",
            "Use a sorted bar chart.",
            name,
        )
    plots = [p.__class__.__name__ for p in chart.plots]
    if any(p in ("BarPlot",) for p in plots):
        try:
            mn = chart.value_axis.minimum_scale
            if mn is not None and mn > 0:
                issues.add(
                    idx,
                    "bar_baseline",
                    "warning",
                    f"bars start at {mn:g}, not zero.",
                    "Start the value axis at zero (or use a line chart for a narrow range).",
                    name,
                )
        except (ValueError, AttributeError):
            pass
    if not any(p in ("PiePlot", "DoughnutPlot") for p in plots):
        try:
            va = chart.value_axis
            fmt = (
                va.tick_labels.number_format
                if not va.tick_labels.number_format_is_linked
                else ""
            )
            titled = va.has_title and va.axis_title.text_frame.text.strip()
        except (ValueError, AttributeError):
            titled, fmt = True, ""
        slide_text = (
            " ".join(s.text_frame.text for s in shape.part.slide.shapes if _has_text(s))
            if hasattr(shape.part, "slide")
            else ""
        )
        if (
            not titled
            and not re.search(r"[%$€£]", fmt or "")
            and not _UNIT.search(slide_text)
        ):
            issues.add(
                idx,
                "axis_unit",
                "warning",
                "the value axis has no title and the slide states no unit.",
                "Add y_title with the unit, e.g. 'Revenue ($M)'.",
                name,
            )
    # Chart marks vs background (WCAG 1.4.11), only where values are not labelled.
    if bg:
        for plot in chart.plots:
            try:
                labelled = plot.has_data_labels
            except AttributeError:  # python-pptx cannot read XY-chart labels
                labelled = False
            for s in plot.series:
                fill = None
                sppr = s._element.find(qn("c:spPr"))  # noqa: SLF001
                if sppr is not None:
                    sf = sppr.find(qn("a:solidFill"))
                    if sf is None:
                        ln = sppr.find(qn("a:ln"))
                        sf = ln.find(qn("a:solidFill")) if ln is not None else None
                    if sf is not None and sf.find(qn("a:srgbClr")) is not None:
                        fill = sf.find(qn("a:srgbClr")).get("val")
                if fill and not labelled and contrast(fill, bg) < MIN_CONTRAST_LARGE:
                    issues.add(
                        idx,
                        "chart_contrast",
                        "warning",
                        f"series '{s.name}' (#{fill}) has {contrast(fill, bg):.1f}:1 contrast with the background and no data labels.",
                        "Use a stronger colour or label the values.",
                        name,
                    )
    # Cached values vs the embedded workbook.
    wbv = workbook_values(chart)
    if wbv:
        for s in series:
            if s["plot"] == "XyPlot":
                continue
            stored = wbv.get(str(s["name"]))
            if stored is None:
                continue
            if len(stored) != len(s["values"]) or not all(
                _same(a, b) for a, b in zip(stored, s["values"])
            ):
                issues.add(
                    idx,
                    "chart_data_mismatch",
                    "error",
                    f"series '{s['name']}' shows values that differ from its embedded data sheet.",
                    "Rebuild the chart so the editable data matches what is drawn.",
                    name,
                )


def _check_table(
    idx: int,
    shape,
    ctx: DeckContext,
    issues: Issues,
    R: Dict[str, Any],
    bg: Optional[str],
) -> bool:
    """Estimate the table's rendered height; return True when it holds numbers."""
    table = shape.table
    numeric = 0
    cells = 0
    total_h = 0.0
    for r_i, row in enumerate(table.rows):
        row_need = pt(int(row.height))
        for c_i, cell in enumerate(row.cells):
            text = cell.text_frame.text
            if text.strip():
                cells += 1
                if re.fullmatch(r"\s*[-+(]?[$€£]?\s*[\d,.]+\s*[%kKmMbB]?\)?\s*", text):
                    numeric += 1
            width = pt(
                int(table.columns[c_i].width)
                - int(cell.margin_left)
                - int(cell.margin_right)
            )
            need = 0.0
            for para in cell.text_frame.paragraphs:
                size = 18.0
                fam = ctx.fonts.get("body", "Calibri")
                bold = False
                if para.runs:
                    rpr = para.runs[0]._r.find(qn("a:rPr"))  # noqa: SLF001
                    if rpr is not None and rpr.get("sz"):
                        size = int(rpr.get("sz")) / 100
                    bold = rpr is not None and rpr.get("b") == "1"
                    if size < R["min_font_pt"]:
                        issues.add(
                            idx,
                            "tiny_font",
                            "error",
                            f"table text at {size:.0f}pt.",
                            "Use fewer rows/columns so the text can be larger.",
                            shape.name,
                        )
                need += (
                    MEASURER.line_count(
                        para.text, FontSpec(fam, bold), size, max(1.0, width)
                    )
                    * size
                    * LINE_SPACING
                )
            need += pt(int(cell.margin_top) + int(cell.margin_bottom))
            row_need = max(row_need, need)
        total_h += row_need
    b = bbox(shape)
    if b and pt(b[1]) + total_h > pt(ctx.H) - 6:
        issues.add(
            idx,
            "table_overflow",
            "error",
            f"the table grows to about {total_h:.0f}pt and runs past the bottom of the slide.",
            "Cut rows (about 7 rows x 5 columns is the limit for a slide) or split it.",
            shape.name,
        )
    if len(table.rows) > 12 or len(table.columns) > 7:
        issues.add(
            idx,
            "table_size",
            "warning",
            f"a {len(table.rows)} x {len(table.columns)} table.",
            "Show the rows that matter (about 7 x 5); put the full table in an appendix.",
            shape.name,
        )
    return cells > 0 and numeric / cells >= 0.2


def _round_trip(idx: int, slide, spec: Dict[str, Any], issues: Issues) -> None:
    """Chart values in the file must equal the outline's, series by series."""
    charts = [s.chart for s in slide.shapes if _kind(s) == "chart"]
    if not charts:
        return
    got = {s["name"]: s for c in charts for s in chart_series(c)}
    scatter = spec.get("chart_type") in ("scatter", "scatter_lines")
    for ser in spec.get("series", []):
        g = got.get(ser["name"])
        if g is None:
            issues.add(
                idx,
                "chart_values",
                "error",
                f"series '{ser['name']}' from the outline is not in the chart.",
                "Rebuild the chart from the outline data.",
            )
            continue
        want = ser["values"]
        if len(g["values"]) != len(want) or not all(
            _same(a, b) for a, b in zip(g["values"], want)
        ):
            issues.add(
                idx,
                "chart_values",
                "error",
                f"series '{ser['name']}' values differ from the outline.",
                "Rebuild the chart from the outline data.",
            )
        if (
            not scatter
            and spec.get("categories")
            and g["categories"]
            and [str(c) for c in spec["categories"]] != g["categories"]
        ):
            issues.add(
                idx,
                "chart_categories",
                "error",
                f"series '{ser['name']}' categories differ from the outline.",
                "Rebuild the chart from the outline data.",
            )
