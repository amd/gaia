# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Text measurement and number formatting shared by the deck builder and verifier.

Both sides measure with the same code, so a deck the builder says fits is a
deck the verifier agrees fits. Widths come from the font file when one can be
found (PIL reads the real advance widths); otherwise from a per-character width
table. Every result says which of the two it used, so an estimate is never
presented as a measurement.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

EMU_PER_INCH = 914400
EMU_PER_PT = 12700

#: Single line spacing as a multiple of the font size (PowerPoint's default
#: for Calibri/Arial-class fonts is about 1.2).
LINE_SPACING = 1.2

#: Metric-compatible substitutes, so a deck in Calibri measures correctly on a
#: Linux box that only has Carlito.
_FONT_FILES: Dict[str, Tuple[str, ...]] = {
    "calibri": ("calibri", "carlito-regular", "carlito"),
    "calibri bold": ("calibrib", "carlito-bold"),
    "calibri light": ("calibril", "calibri", "carlito-regular"),
    "arial": ("arial", "liberationsans-regular", "arimo-regular"),
    "arial bold": ("arialbd", "liberationsans-bold", "arimo-bold"),
    "helvetica": ("arial", "liberationsans-regular", "helvetica"),
    "segoe ui": ("segoeui",),
    "segoe ui bold": ("segoeuib",),
    "aptos": ("aptos", "calibri", "carlito-regular"),
    "aptos bold": ("aptos-bold", "calibrib", "carlito-bold"),
    "georgia": ("georgia",),
    "cambria": ("cambria", "caladea-regular", "caladea"),
    "cambria bold": ("cambriab", "caladea-bold"),
    "georgia bold": ("georgiab",),
    "verdana": ("verdana", "dejavusans"),
    "verdana bold": ("verdanab", "dejavusans-bold"),
    "times new roman": ("times", "liberationserif-regular"),
    "times new roman bold": ("timesbd", "liberationserif-bold"),
    "tahoma": ("tahoma", "dejavusans"),
    "trebuchet ms": ("trebuc",),
    "century gothic": ("gothic",),
}

#: Advance widths in em for a Calibri/Arial-class sans, used when no font
#: file is available. Deliberately on the wide side: an estimate that errs
#: toward "does not fit" shrinks text a point too far rather than overflowing.
_NARROW = set("iljtfr.,;:'!|()[]{} \"`")
_WIDE = set("mwMW@%&")


def _fallback_char_width(ch: str) -> float:
    if ch in _NARROW:
        return 0.30
    if ch in _WIDE:
        return 0.86
    if ch.isupper():
        return 0.64
    if ch.isdigit():
        return 0.53
    if ord(ch) > 0x2E80:  # CJK and wider scripts take a full em
        return 1.0
    return 0.52


@lru_cache(maxsize=256)
def _try_font(filename: str):
    """A PIL font for *filename*, or None.

    PIL looks the bare file name up in the system font folders itself
    (Windows' Fonts folder, the XDG font folders on Linux, the Library font
    folders on macOS), so the skill never walks the file system.
    """
    from PIL import ImageFont

    try:
        # Measured at 1000 units so per-character rounding is negligible.
        return ImageFont.truetype(filename, 1000)
    except OSError:
        return None


def _stems(family: str, bold: bool) -> List[Tuple[str, bool]]:
    key = family.strip().lower()
    flat, dashed = key.replace(" ", ""), key.replace(" ", "-")
    out: List[Tuple[str, bool]] = []
    if bold:
        for stem in list(_FONT_FILES.get(f"{key} bold", ())) + [
            f"{flat}b",
            f"{flat}bd",
            f"{flat}-bold",
            f"{dashed}-bold",
        ]:
            out.append((stem, True))
    for stem in list(_FONT_FILES.get(key, ())) + [flat, f"{dashed}-regular"]:
        out.append((stem, not bold))
    return out


@lru_cache(maxsize=64)
def _font_file(family: str, bold: bool) -> Tuple[Optional[str], bool]:
    """``(file name, is_the_requested_weight)`` for *family*, or ``(None, False)``."""
    for stem, exact in _stems(family, bold):
        for ext in (".ttf", ".ttc", ".otf"):
            for name in (
                stem + ext,
                stem.capitalize() + ext,
                "-".join(w.capitalize() for w in stem.split("-")) + ext,
            ):
                if _try_font(name) is not None:
                    return name, exact
    return None, False


def _font_path(family: str, bold: bool) -> Optional[str]:
    return _font_file(family, bold)[0]


def _pil_font(name: str):
    return _try_font(name)


@dataclass(frozen=True)
class FontSpec:
    family: str = "Calibri"
    bold: bool = False


class TextMeasurer:
    """Measures text widths in points for a font family and size."""

    def __init__(self) -> None:
        self.sources: Dict[str, str] = {}

    def method(self, font: FontSpec) -> str:
        path = _font_path(font.family, font.bold)
        return f"font file {path}" if path else "estimated widths"

    def width_pt(self, text: str, font: FontSpec, size_pt: float) -> float:
        if not text:
            return 0.0
        path, exact = _font_file(font.family, font.bold)
        if path:
            try:
                length = _pil_font(path).getlength(text)
                self.sources[font.family] = f"font file {path}"
                # Bold measured with the regular face: widen to stay safe.
                widen = 1.08 if font.bold and not exact else 1.0
                return length / 1000.0 * size_pt * widen
            except (OSError, ValueError):
                pass
        self.sources[font.family] = "estimated widths"
        width = sum(_fallback_char_width(ch) for ch in text)
        if font.bold:
            width *= 1.07
        return width * size_pt

    def wrap(
        self, text: str, font: FontSpec, size_pt: float, width_pt: float
    ) -> List[str]:
        """Greedy word wrap; a word wider than the line breaks by character."""
        if width_pt <= 0:
            return [text]
        lines: List[str] = []
        for raw in text.split("\n"):
            words = raw.split(" ")
            line = ""
            for word in words:
                candidate = f"{line} {word}" if line else word
                if self.width_pt(candidate, font, size_pt) <= width_pt:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                    line = ""
                # The word alone may still be too wide: break it.
                while self.width_pt(word, font, size_pt) > width_pt and len(word) > 1:
                    cut = len(word)
                    while (
                        cut > 1 and self.width_pt(word[:cut], font, size_pt) > width_pt
                    ):
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                line = word
            lines.append(line)
        return lines

    def longest_word_pt(self, text: str, font: FontSpec, size_pt: float) -> float:
        return max((self.width_pt(w, font, size_pt) for w in text.split()), default=0.0)

    def line_count(
        self, text: str, font: FontSpec, size_pt: float, width_pt: float
    ) -> int:
        return len(self.wrap(text, font, size_pt, width_pt))


MEASURER = TextMeasurer()


@dataclass
class Para:
    """A paragraph to lay out: text, outline level, font, relative size."""

    text: str
    level: int = 0
    bold: bool = False
    scale: float = 1.0  # size relative to the block's base size
    indent_pt: float = 0.0  # left indent (bullet hanging indent included)
    space_before: float = 0.0  # in lines of this paragraph's own size
    font: Optional[FontSpec] = None


def block_height_pt(
    paras: Sequence[Para],
    base_pt: float,
    width_pt: float,
    font: FontSpec,
    *,
    min_pt: float = 0.0,
    line_spacing: float = LINE_SPACING,
) -> float:
    """Height in points of *paras* laid out at *base_pt* in a *width_pt* column."""
    total = 0.0
    for i, p in enumerate(paras):
        size = max(base_pt * p.scale, min_pt)
        f = p.font or FontSpec(font.family, p.bold or font.bold)
        if MEASURER.longest_word_pt(p.text, f, size) > width_pt - p.indent_pt:
            # A word that only fits by breaking it mid-word does not fit.
            return float("inf")
        lines = MEASURER.line_count(p.text, f, size, width_pt - p.indent_pt)
        total += lines * size * line_spacing
        if i:
            total += p.space_before * size
    return total


def fit_size(
    paras: Sequence[Para],
    width_pt: float,
    height_pt: float,
    font: FontSpec,
    max_pt: float,
    min_pt: float,
    *,
    step: float = 1.0,
    line_spacing: float = LINE_SPACING,
    floor: Optional[float] = None,
) -> Tuple[float, bool, float]:
    """Largest base size in [min_pt, max_pt] at which *paras* fit the box.

    Returns ``(size, fits, needed_height_pt)``. When nothing fits, ``size`` is
    ``min_pt`` and ``fits`` is False — the caller decides whether to split or
    warn; text is never cut.
    """
    floor = min_pt if floor is None else floor  # no scaled paragraph goes below this
    size = max_pt
    while size >= min_pt - 1e-6:
        need = block_height_pt(
            paras, size, width_pt, font, min_pt=floor, line_spacing=line_spacing
        )
        if need <= height_pt:
            return size, True, need
        size -= step
    need = block_height_pt(
        paras, min_pt, width_pt, font, min_pt=floor, line_spacing=line_spacing
    )
    return min_pt, False, need


# ----------------------------------------------------------------------
# Number formatting (Excel-style codes, as used by chart number formats)
# ----------------------------------------------------------------------

_PY_SPEC = re.compile(r"^\{?:?([<>^=]?[+\- ]?#?0?\d*[,_]?(?:\.\d+)?[%eEfFgGdn]?)\}?$")


def is_number(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not (isinstance(value, float) and math.isnan(value))
    return False


def parse_number(text) -> Optional[float]:
    """A number written as text ("$1,234.5", "12%", "(300)", "4.2M"), or None."""
    if is_number(text):
        return float(text)
    if not isinstance(text, str):
        return None
    s = text.strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    s = re.sub(r"^[^\d\-+.]+", "", s)  # leading currency symbols / words
    mult = 1.0
    m = re.match(r"^([-+]?[\d,]*\.?\d+)\s*([%kKmMbB]?)\s*$", s)
    if not m:
        return None
    num, unit = m.groups()
    try:
        value = float(num.replace(",", ""))
    except ValueError:
        return None
    if unit == "%":
        mult = 0.01
    elif unit in ("k", "K"):
        mult = 1e3
    elif unit in ("m", "M"):
        mult = 1e6
    elif unit in ("b", "B"):
        mult = 1e9
    value *= mult
    return -value if negative else value


def format_number(value, code: Optional[str] = None) -> str:
    """Format *value* with an Excel-style code ("#,##0.0", "0%", "$#,##0,,\\"M\\"").

    Also accepts a Python format spec (",.1f", "{:.0%}"). Non-numbers are
    returned as text unchanged.
    """
    if not is_number(value):
        return "" if value is None else str(value)
    if not code or code.lower() == "general":
        if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
            return f"{int(value):,}" if abs(value) >= 10000 else str(int(value))
        if isinstance(value, int):
            return f"{value:,}" if abs(value) >= 10000 else str(value)
        return f"{value:,.6g}" if abs(value) >= 10000 else f"{value:.6g}"
    py = _PY_SPEC.match(code)
    if py and (code.startswith("{") or code[-1] in "fFeEgGdn"):
        try:
            return format(value, py.group(1))
        except ValueError:
            pass
    # Excel code: use the positive section only, and its sign for negatives.
    section = code.split(";")[0]
    # Literal text: "..." or \x, plus bare currency/unit characters.
    prefix, suffix, core = "", "", section
    m = re.match(
        r'^((?:"[^"]*"|\\.|[^#0?.,%"\\])*)(.*?)((?:"[^"]*"|\\.|[^#0?.,%"\\])*)$',
        section,
    )
    if m:
        prefix, core, suffix = m.groups()

    def _lit(s: str) -> str:
        s = re.sub(r'"([^"]*)"', r"\1", s)
        return re.sub(r"\\(.)", r"\1", s)

    prefix, suffix = _lit(prefix), _lit(suffix)
    percent = "%" in core or "%" in suffix
    core = core.replace("%", "")
    scale_commas = len(core) - len(core.rstrip(","))
    core = core.rstrip(",")
    thousands = "," in core
    decimals = len(core.split(".")[1]) if "." in core else 0
    v = float(value)
    if percent:
        v *= 100
    v /= 1000**scale_commas
    text = f"{abs(v):,.{decimals}f}" if thousands else f"{abs(v):.{decimals}f}"
    sign = "-" if v < 0 and round(abs(v), decimals) != 0 else ""
    return f"{sign}{prefix}{text}{'%' if percent and '%' not in suffix else ''}{suffix}"


def iter_text(items: Iterable) -> Iterable[str]:
    for item in items:
        if item is None:
            continue
        yield str(item)
