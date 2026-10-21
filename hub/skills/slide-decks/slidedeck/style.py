# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Design tokens, user preferences, writing rules and style scoring.

This is the one place style lives. The theme-template generator, the builder
and the verifier all read it, so what gets built is what gets checked.

Precedence for every setting, highest first:

1. the explicit request — ``build_deck`` arguments and the outline's ``style``
2. the user's saved preferences (the ``prefs`` dict)
3. a user-supplied template's own theme (colours, fonts, size)
4. the skill defaults below
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ----------------------------------------------------------------------
# Colour maths (WCAG 2.x)
# ----------------------------------------------------------------------

_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def norm_hex(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    m = _HEX.match(value.strip())
    return m.group(1).upper() if m else None


def _channel(c: int) -> float:
    s = c / 255.0
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def luminance(hex_rgb: str) -> float:
    h = hex_rgb.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colours (1.0 .. 21.0)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(hex_rgb: str, tint: float) -> str:
    """Tint toward white (tint > 0) or shade toward black (tint < 0)."""
    h = hex_rgb.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if tint >= 0:
        r, g, b = (round(c + (255 - c) * tint) for c in (r, g, b))
    else:
        r, g, b = (round(c * (1 + tint)) for c in (r, g, b))
    return f"{r:02X}{g:02X}{b:02X}"


def best_text_on(fill: str, candidates: Sequence[str]) -> str:
    """The candidate text colour with the highest contrast on *fill*."""
    return max(candidates, key=lambda c: contrast(c, fill))


#: WCAG: 4.5:1 for body text, 3:1 for large text (>= 18pt, or >= 14pt bold)
#: and for chart marks against their neighbours (1.4.11).
MIN_CONTRAST = 4.5
MIN_CONTRAST_LARGE = 3.0
LARGE_TEXT_PT = 18.0
LARGE_BOLD_PT = 14.0

# ----------------------------------------------------------------------
# Design tokens
# ----------------------------------------------------------------------

#: Okabe-Ito hues, distinguishable under the common colour-vision
#: deficiencies. Orange and sky blue are low-contrast on white, so they come
#: after the four strong hues and are only used where values are labelled.
#: Yellow (#F0E442) is never used on white.
CHART_PALETTE_LIGHT = ["0072B2", "D55E00", "009E73", "CC79A7", "E69F00", "56B4E9"]
CHART_PALETTE_DARK = ["56B4E9", "E69F00", "009E73", "CC79A7", "F0E442", "D55E00"]
CONTEXT_GREY = "9CA3AF"

#: Layout in inches for a 13.333 x 7.5 in (16:9) slide. 4:3 (10 x 7.5 in)
#: keeps the same vertical zones and margins.
LAYOUT = {
    "width_in": 13.333,
    "height_in": 7.5,
    "margin_x": 0.6,
    "margin_top": 0.45,
    "columns": 12,
    "gutter": 0.25,
    "unit": 0.3,
    "title_top": 0.45,
    "title_bottom": 1.35,
    "content_top": 1.55,
    "content_bottom": 6.55,
    "footer_top": 6.75,
    "footer_bottom": 7.15,
}

ASPECTS = {"16:9": (13.333, 7.5), "4:3": (10.0, 7.5)}

#: Type scale in points. Sizes are always written explicitly on every run.
SIZES = {
    "title_max": 36,
    "title_min": 28,
    "title_floor": 24,  # an over-long title shrinks this far, with a warning
    "cover_title_max": 44,
    "subtitle": 24,
    "body_max": 24,
    "body_min": 18,
    "dense_min": 14,  # tables, diagram labels, chart text, dense pre-reads
    "footnote": 12,  # sources and footnotes only
    "footnote_min": 11,
    "kpi_max": 72,
    "kpi_min": 54,
}

THEMES: Dict[str, Dict[str, Any]] = {
    "light": {
        "label": "Light corporate",
        "fonts": {"title": "Calibri", "body": "Calibri"},
        "colors": {
            "background": "FFFFFF",
            "title": "1F2937",
            "text": "1F2937",
            "muted": "4B5563",
            "panel": "F3F4F6",
            "line": "D1D5DB",
            "primary": "0072B2",
            "highlight": "D55E00",
            "context": CONTEXT_GREY,
            "good": "047857",
            "bad": "B91C1C",
        },
        "palette": CHART_PALETTE_LIGHT,
        "title_bar": True,
    },
    "dark": {
        "label": "Dark",
        "fonts": {"title": "Calibri", "body": "Calibri"},
        "colors": {
            "background": "111827",
            "title": "F9FAFB",
            "text": "F3F4F6",
            "muted": "D1D5DB",
            "panel": "1F2937",
            "line": "4B5563",
            "primary": "56B4E9",
            "highlight": "E69F00",
            "context": "6B7280",
            "good": "34D399",
            "bad": "F87171",
        },
        "palette": CHART_PALETTE_DARK,
        "title_bar": True,
    },
    "minimal": {
        "label": "Minimal",
        "fonts": {"title": "Cambria", "body": "Calibri"},
        "colors": {
            "background": "FFFFFF",
            "title": "111827",
            "text": "1F2937",
            "muted": "4B5563",
            "panel": "F9FAFB",
            "line": "E5E7EB",
            "primary": "1F2937",
            "highlight": "0072B2",
            "context": CONTEXT_GREY,
            "good": "047857",
            "bad": "B91C1C",
        },
        "palette": ["0072B2", "D55E00", "009E73", "CC79A7", "E69F00", "56B4E9"],
        "title_bar": False,
    },
}

THEME_ALIASES = {
    "corporate": "light",
    "default": "light",
    "white": "light",
    "black": "dark",
    "night": "dark",
    "mono": "minimal",
    "clean": "minimal",
    "simple": "minimal",
}

# ----------------------------------------------------------------------
# Writing rules and density
# ----------------------------------------------------------------------

DENSITY: Dict[str, Dict[str, Any]] = {
    # A presented deck people glance at: very little text.
    "minimal": {
        "max_bullets": 4,
        "max_bullet_words": 8,
        "max_body_words": 30,
        "min_body_pt": 20,
    },
    # The default: one message per slide, readable from the back of the room.
    "balanced": {
        "max_bullets": 6,
        "max_bullet_words": 12,
        "max_body_words": 60,
        "min_body_pt": 18,
    },
    # A pre-read sent ahead of a meeting.
    "detailed": {
        "max_bullets": 8,
        "max_bullet_words": 20,
        "max_body_words": 110,
        "min_body_pt": 14,
    },
}

DEFAULT_RULES: Dict[str, Any] = {
    "max_title_words": 15,
    "max_title_lines": 2,
    "min_action_title_words": 4,  # fewer reads as a topic label, not a claim
    "max_nesting": 1,  # one level of sub-bullets
    "min_font_pt": 11,  # absolute floor (sources/footnotes)
    "min_dense_pt": 14,  # any non-footnote text
    "max_colors_per_slide": 6,
    "max_font_families": 2,
    "max_chart_series": 5,
    "max_pie_slices": 5,
    "max_same_layout_run": 3,
    "section_dividers_after": 10,
    "align_tolerance_in": 0.1,
    "exec_max_body_words": 30,
    **DENSITY["balanced"],
}

#: Slide kinds whose title is a cover, divider or label rather than a claim.
NON_ACTION_TITLE_KINDS = ("title", "section", "agenda", "quote", "appendix")


def rules_for(
    prefs: Optional[Dict[str, Any]] = None, overrides: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Verifier limits for the user's density and title style, plus explicit overrides."""
    rules = dict(DEFAULT_RULES)
    prefs = prefs or {}
    density = str(prefs.get("density") or "balanced").lower()
    rules.update(DENSITY.get(density, DENSITY["balanced"]))
    rules["density"] = density if density in DENSITY else "balanced"
    rules["title_style"] = str(prefs.get("title_style") or "action").lower()
    rules["speaker_notes"] = prefs.get("speaker_notes")
    for key, value in (overrides or {}).items():
        if key in rules and isinstance(value, (int, float, str, bool)):
            rules[key] = value
    return rules


# ----------------------------------------------------------------------
# Preferences
# ----------------------------------------------------------------------

#: The documented preference keys and their allowed values. ``None`` means
#: free-form of the given type.
PREFS_SCHEMA: Dict[str, Tuple[type, Optional[Sequence[str]]]] = {
    "theme": (str, tuple(THEMES) + tuple(THEME_ALIASES)),
    "colors": (
        dict,
        None,
    ),  # primary, highlight, background, text, muted, panel, palette
    "fonts": (dict, None),  # title, body
    "template_path": (str, None),
    "logo_path": (str, None),
    "density": (str, tuple(DENSITY)),
    "tone": (str, ("formal", "neutral", "casual")),
    "prefer": (str, ("charts", "tables", "balanced")),
    "chart": (dict, None),  # gridlines, data_labels, palette, legend
    "number_format": (dict, None),  # decimals, thousands, currency, percent_decimals
    "locale": (str, None),
    "date_format": (str, None),
    "speaker_notes": (bool, None),
    "footer": (dict, None),  # text, slide_numbers
    "title_style": (str, ("action", "topic")),
    "aspect": (str, tuple(ASPECTS)),
}

PREFS_MEMORY_KEY = "slide-decks preferences"
_PREFS_MARKER = "slide-decks preferences v1:"


class PrefsError(ValueError):
    pass


def validate_prefs(prefs: Any) -> List[str]:
    """Problems with a preferences dict, as fixable sentences (empty = valid)."""
    if prefs in (None, {}):
        return []
    if not isinstance(prefs, dict):
        return [f"prefs must be an object, not {type(prefs).__name__}."]
    problems = []
    for key, value in prefs.items():
        if key not in PREFS_SCHEMA:
            problems.append(
                f"prefs: unknown key {key!r}. Known keys: {', '.join(PREFS_SCHEMA)}."
            )
            continue
        typ, allowed = PREFS_SCHEMA[key]
        if value is None:
            continue
        if not isinstance(value, typ):
            problems.append(
                f"prefs.{key} must be a {typ.__name__}, got {type(value).__name__}."
            )
            continue
        if allowed and isinstance(value, str) and value.lower() not in allowed:
            problems.append(
                f"prefs.{key}={value!r} is not one of {', '.join(allowed)}."
            )
    for key in (
        "primary",
        "highlight",
        "background",
        "text",
        "muted",
        "panel",
        "title",
    ):
        v = (
            (prefs.get("colors") or {}).get(key)
            if isinstance(prefs.get("colors"), dict)
            else None
        )
        if v is not None and norm_hex(v) is None:
            problems.append(
                f"prefs.colors.{key}={v!r} is not a hex colour like '#1F4E79'."
            )
    pal = (
        (prefs.get("colors") or {}).get("palette")
        if isinstance(prefs.get("colors"), dict)
        else None
    )
    if pal is not None and (
        not isinstance(pal, list) or any(norm_hex(c) is None for c in pal)
    ):
        problems.append("prefs.colors.palette must be a list of hex colours.")
    return problems


def merge_prefs(*layers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Deep-merge preference layers, highest precedence FIRST."""
    out: Dict[str, Any] = {}
    for layer in reversed([x for x in layers if x]):
        for key, value in layer.items():
            if value is None:
                continue
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                merged = dict(out[key])
                merged.update({k: v for k, v in value.items() if v is not None})
                out[key] = merged
            else:
                out[key] = copy.deepcopy(value)
    return out


def update_prefs(
    saved: Optional[Dict[str, Any]], changes: Dict[str, Any]
) -> Dict[str, Any]:
    """Apply user feedback (e.g. ``{"density": "minimal"}``) to saved prefs. Raises PrefsError."""
    problems = validate_prefs(changes)
    if problems:
        raise PrefsError("; ".join(problems))
    return merge_prefs(changes, saved or {})


def prefs_to_text(prefs: Dict[str, Any]) -> str:
    """Serialise prefs for a text store (memory, a notes file)."""
    return (
        _PREFS_MARKER + " " + json.dumps(prefs, sort_keys=True, separators=(",", ":"))
    )


def prefs_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Recover prefs written by :func:`prefs_to_text` from text that may hold other words."""
    if not text:
        return None
    i = text.find(_PREFS_MARKER)
    if i < 0:
        return None
    rest = text[i + len(_PREFS_MARKER) :].strip()
    try:
        value, _end = json.JSONDecoder().raw_decode(rest)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def resolve_tokens(
    request: Optional[Dict[str, Any]] = None,
    prefs: Optional[Dict[str, Any]] = None,
    template_tokens: Optional[Dict[str, Any]] = None,
    *,
    template_explicit: bool = False,
) -> Dict[str, Any]:
    """Final design tokens: explicit request > saved prefs > template > defaults.

    *request* and *prefs* use the preferences schema (``theme``, ``colors``,
    ``fonts``, ``aspect``, ``chart``, ``footer``...). *template_tokens* is what
    :func:`tokens_from_theme_xml` read from a user template. A template
    saved in the preferences ranks below them; one named in this request
    (*template_explicit*) ranks with the request.
    """
    request, prefs = request or {}, dict(prefs or {})
    if template_explicit and template_tokens:
        # A template named in this request is part of the request: its look
        # outranks the saved theme, colours and fonts.
        for key in ("theme", "colors", "fonts"):
            prefs.pop(key, None)
    theme_name = request.get("theme") or prefs.get("theme") or "light"
    theme_name = THEME_ALIASES.get(str(theme_name).lower(), str(theme_name).lower())
    if theme_name not in THEMES:
        raise PrefsError(f"theme {theme_name!r} is not one of {', '.join(THEMES)}.")
    base = copy.deepcopy(THEMES[theme_name])
    tokens: Dict[str, Any] = {
        "theme": theme_name,
        "fonts": dict(base["fonts"]),
        "colors": dict(base["colors"]),
        "palette": list(base["palette"]),
        "title_bar": base["title_bar"],
        "aspect": "16:9",
        "chart": {"gridlines": True, "data_labels": "auto", "legend": "auto"},
        "footer": {"text": "", "slide_numbers": True},
        "number_format": {},
        "density": "balanced",
        "title_style": "action",
        "speaker_notes": None,
        "prefer": "balanced",
        "tone": "neutral",
        "logo_path": None,
        "sources": {},
    }
    # A template's look replaces the defaults, but not a theme the user chose.
    theme_chosen = bool(request.get("theme") or prefs.get("theme"))
    layers = [
        ("template", {} if theme_chosen else (template_tokens or {})),
        ("prefs", prefs),
        ("request", request),
    ]
    for origin, layer in layers:  # lowest precedence first; later layers overwrite
        if not layer:
            continue
        for key in ("fonts",):
            for k, v in (layer.get(key) or {}).items():
                if v:
                    tokens[key][k] = str(v)
                    tokens["sources"][f"{key}.{k}"] = origin
        colors = layer.get("colors") or {}
        for k, v in colors.items():
            if k == "palette":
                pal = [norm_hex(c) for c in (v or []) if norm_hex(c)]
                if pal:
                    tokens["palette"] = pal
                    tokens["sources"]["palette"] = origin
            elif norm_hex(v):
                tokens["colors"][k] = norm_hex(v)
                tokens["sources"][f"colors.{k}"] = origin
        if colors.get("primary") and not colors.get("palette") and origin != "template":
            # A brand colour leads the chart palette too.
            pal = [c for c in tokens["palette"] if c != norm_hex(colors["primary"])]
            tokens["palette"] = [norm_hex(colors["primary"])] + pal
        for key in ("chart", "footer", "number_format"):
            if isinstance(layer.get(key), dict):
                tokens[key].update(
                    {k: v for k, v in layer[key].items() if v is not None}
                )
                tokens["sources"][key] = origin
        if isinstance(layer.get("chart"), dict) and layer["chart"].get("palette"):
            pal = [norm_hex(c) for c in layer["chart"]["palette"] if norm_hex(c)]
            if pal:
                tokens["palette"] = pal
        for key in (
            "aspect",
            "density",
            "title_style",
            "speaker_notes",
            "prefer",
            "tone",
            "logo_path",
            "locale",
            "date_format",
        ):
            if layer.get(key) is not None:
                tokens[key] = layer[key]
                tokens["sources"][key] = origin
    if tokens["aspect"] not in ASPECTS:
        raise PrefsError(
            f"aspect {tokens['aspect']!r} is not one of {', '.join(ASPECTS)}."
        )
    _repair_contrast(tokens)
    return tokens


def _repair_contrast(tokens: Dict[str, Any]) -> None:
    """Keep text readable whatever colours the layers chose.

    A brand colour is honoured as a fill or accent; text drawn ON it gets
    whichever of the theme's dark/light text colours reads best, and a muted
    grey too light for its background is darkened until it passes 4.5:1.
    """
    c = tokens["colors"]
    bg = c["background"]
    for key in ("text", "title"):
        if contrast(c[key], bg) < MIN_CONTRAST:
            c[key] = best_text_on(bg, ["111827", "FFFFFF"])
            tokens["sources"][f"colors.{key}"] = "contrast repair"
    light_bg = luminance(bg) > 0.4
    for _ in range(12):
        if contrast(c["muted"], bg) >= MIN_CONTRAST:
            break
        c["muted"] = mix(c["muted"], -0.12 if light_bg else 0.18)
    c["on_primary"] = best_text_on(
        c["primary"], [c["text"], c["background"], "FFFFFF", "111827"]
    )
    c["on_highlight"] = best_text_on(
        c["highlight"], [c["text"], c["background"], "FFFFFF", "111827"]
    )
    c["on_panel"] = best_text_on(c["panel"], [c["text"], "111827", "FFFFFF"])


def tokens_from_theme_xml(theme_xml: bytes) -> Dict[str, Any]:
    """Colours and fonts from a template's theme part, in the prefs shape."""
    from lxml import etree

    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    root = etree.fromstring(theme_xml)
    scheme = root.find(".//a:clrScheme", ns)
    colors: Dict[str, Any] = {}

    def slot(name: str) -> Optional[str]:
        if scheme is None:
            return None
        el = scheme.find(f"a:{name}", ns)
        if el is None or not len(el):
            return None
        child = el[0]
        return norm_hex(child.get("val") or child.get("lastClr") or "")

    dk1, lt1, dk2, lt2 = slot("dk1"), slot("lt1"), slot("dk2"), slot("lt2")
    accents = [slot(f"accent{i}") for i in range(1, 7)]
    if dk1:
        colors["text"] = dk1
        colors["title"] = dk1
    if lt1:
        colors["background"] = lt1
    if lt2:
        colors["panel"] = lt2
    if dk2:
        colors["muted"] = dk2
    acc = [a for a in accents if a]
    if acc:
        colors["primary"] = acc[0]
        colors["highlight"] = acc[1] if len(acc) > 1 else acc[0]
        colors["palette"] = acc
    hl = slot("hlink")
    fol = slot("folHlink")
    # Our own generated templates park the highlight and context grey in the
    # hyperlink slots; a foreign template's hyperlink colours are left alone.
    name = root.get("name", "")
    if name.startswith("slide-decks") and hl:
        colors["highlight"] = hl
        if fol:
            colors["context"] = fol
    fonts = {}
    mj = root.find(".//a:majorFont/a:latin", ns)
    mn = root.find(".//a:minorFont/a:latin", ns)
    if mj is not None and mj.get("typeface"):
        fonts["title"] = mj.get("typeface")
    if mn is not None and mn.get("typeface"):
        fonts["body"] = mn.get("typeface")
    return {"colors": colors, "fonts": fonts, "theme_name": name}


# ----------------------------------------------------------------------
# Chart type from the data
# ----------------------------------------------------------------------

_PERIOD = re.compile(
    r"^(?:(?:19|20)\d\d(?:\s*[-/ ]?\s*(?:q[1-4]|h[12]|\d{1,2}))?|"
    r"(?:q[1-4]|h[12])\s*[-/ ]?\s*'?(?:\d{2}|\d{4})?|(?:fy|cy)\s*'?\d{2,4}(?:\s*q[1-4])?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?(?:\s*'?\d{2,4})?|"
    r"(?:week|wk|w|month|day|period|sprint)\s*\d+|"
    r"\d{4}-\d{2}(?:-\d{2})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)$",
    re.I,
)


def looks_like_periods(categories: Sequence[Any]) -> bool:
    cats = [str(c).strip() for c in categories if str(c).strip()]
    if len(cats) < 2:
        return False
    return sum(1 for c in cats if _PERIOD.match(c)) / len(cats) >= 0.8


def recommend_chart(
    categories: Sequence[Any], series: List[Dict[str, Any]], *, intent: str = ""
) -> Tuple[str, str]:
    """``(chart_type, reason)`` for data with no explicit chart type.

    *intent* ("trend", "share", "compare", "ranking", "distribution",
    "correlation") wins when given; otherwise the categories decide.
    """
    intent = (intent or "").lower().replace("-", "_")
    n_cat, n_ser = len(categories), len(series)
    values = [v for s in series for v in s.get("values", []) if v is not None]
    if intent in ("correlation", "relationship"):
        return "scatter", "a relationship between two measures: scatter"
    if intent in ("distribution", "histogram"):
        return "column", "a distribution over ordered bins: columns"
    if intent in ("share", "part_to_whole", "composition", "mix"):
        if n_ser == 1 and n_cat <= 4 and values and min(values) >= 0:
            return "pie", "one whole split into 4 or fewer parts: pie"
        if n_ser == 1:
            return "bar", "one whole with many parts: bars sorted largest first"
        return "column_stacked_100", "composition across groups: 100% stacked columns"
    if intent in ("compare", "ranking", "comparison"):
        return "bar", "a ranking: horizontal bars sorted largest first"
    periods = looks_like_periods(categories)
    if intent == "trend" or periods:
        if n_cat > 8 or n_ser > 2:
            return "line", "a time series with many periods or several series: line"
        return "column", "a few periods: columns"
    longest = max((len(str(c)) for c in categories), default=0)
    if n_cat > 6 or longest > 12:
        return (
            "bar",
            "many or long-named categories: horizontal bars sorted largest first",
        )
    return "column", "a few categories: columns"


# ----------------------------------------------------------------------
# Scoring and counting
# ----------------------------------------------------------------------

SEVERITY_WEIGHT = {"error": 10.0, "warning": 3.0, "info": 0.5}


def style_score(issues: Iterable[Dict[str, Any]], slides: int) -> int:
    """0-100. Deductions per issue, softened by deck length so a long deck
    is not punished for having more slides, but every issue still costs."""
    total = sum(SEVERITY_WEIGHT.get(i.get("severity", "warning"), 3.0) for i in issues)
    return max(0, min(100, round(100 - total * 4.0 / max(1, slides) ** 0.5)))


_WORD = re.compile(r"[^\s]+")


def count_words(text: str) -> int:
    return len([w for w in _WORD.findall(text or "") if re.search(r"\w", w)])


# ----------------------------------------------------------------------
# Feedback -> preferences, and when (not) to ask
# ----------------------------------------------------------------------

_FEEDBACK_RULES: List[Tuple[str, Dict[str, Any]]] = [
    (
        r"\b(less|fewer|shorter|minimal|sparser?)\b.*\b(text|words|bullets|copy)\b|\btoo (wordy|much text|dense)\b",
        {"density": "minimal"},
    ),
    (
        r"\b(more|fuller)\b.*\b(detail|text|words|context)\b|\bpre-?read\b|\btoo (sparse|thin)\b",
        {"density": "detailed"},
    ),
    (
        r"\b(no|without|drop|skip|remove)\b.*\b(speaker )?notes\b",
        {"speaker_notes": False},
    ),
    (r"\b(add|with|include|want)\b.*\b(speaker )?notes\b", {"speaker_notes": True}),
    (r"\bdark\b.*\b(theme|background|mode|slides)\b|\bdark mode\b", {"theme": "dark"}),
    (r"\b(light|white)\b.*\b(theme|background|slides)\b", {"theme": "light"}),
    (
        r"\b(minimal|clean|simple|plain)\b.*\b(theme|look|style|design)\b",
        {"theme": "minimal"},
    ),
    (r"\b(prefer|use|more)\b.*\btables?\b", {"prefer": "tables"}),
    (r"\b(prefer|use|more)\b.*\bcharts?\b", {"prefer": "charts"}),
    (r"\b(topic|short|label)\b.*\btitles?\b", {"title_style": "topic"}),
    (r"\b(action|sentence|headline)\b.*\btitles?\b", {"title_style": "action"}),
    (r"\b4:3\b|\bstandard (size|format)\b", {"aspect": "4:3"}),
    (r"\b16:9\b|\bwidescreen\b", {"aspect": "16:9"}),
    (
        r"\bno (slide )?numbers?\b|\bdrop (the )?slide numbers?\b",
        {"footer": {"slide_numbers": False}},
    ),
    (r"\b(no|without|drop|hide)\b.*\bgridlines?\b", {"chart": {"gridlines": False}}),
    (
        r"\b(no|without|drop|hide)\b.*\b(data )?labels?\b",
        {"chart": {"data_labels": False}},
    ),
    (r"\b(formal)\b", {"tone": "formal"}),
    (r"\b(casual|informal|friendly)\b", {"tone": "casual"}),
]
_HEX_IN_TEXT = re.compile(r"#([0-9A-Fa-f]{6})\b")
_FONT_IN_TEXT = re.compile(
    r"\b(?:font|typeface)\s*(?:is|=|:|to)?\s*[\"']?([A-Z][A-Za-z0-9 ]{2,30}?)[\"']?(?:\s+(?:for|font)\b|[.,;]|$)"
)


def prefs_from_feedback(feedback: str) -> Dict[str, Any]:
    """Preference changes a piece of style feedback asks for ('less text', 'use #1F4E79').

    Only what the words clearly say; anything else is left to the caller.
    """
    text = feedback or ""
    out: Dict[str, Any] = {}
    for pattern, change in _FEEDBACK_RULES:
        if re.search(pattern, text, re.I):
            out = merge_prefs(change, out)
    hexes = [h.upper() for h in _HEX_IN_TEXT.findall(text)]
    if hexes:
        out = merge_prefs(
            {
                "colors": {
                    "primary": hexes[0],
                    **({"highlight": hexes[1]} if len(hexes) > 1 else {}),
                }
            },
            out,
        )
    m = _FONT_IN_TEXT.search(text)
    if m:
        name = m.group(1).strip()
        out = merge_prefs({"fonts": {"title": name, "body": name}}, out)
    return out


#: The longest a pre-build question may wait before the default applies.
MAX_QUESTION_TIMEOUT_S = 60


def clarifying_question(
    *,
    external_audience: Optional[bool] = None,
    branding_known: bool = False,
    conflicts: Sequence[str] = (),
) -> Optional[Dict[str, Any]]:
    """At most one short question before building — or None (the usual case).

    Asks only for a material ambiguity: instructions that conflict, or a deck
    for an outside audience with no brand (template, colours) known. An
    unknown audience on its own is not a reason to ask; the default is used.
    The returned dict is ready for ``request_user_input`` and always carries
    a default that applies when nobody answers.
    """
    if conflicts:
        options = [str(c)[:60] for c in conflicts][:3]
        return {
            "message": "Two instructions conflict. Which should the deck follow?",
            "choices": options + (["Ask me later"] if len(options) < 4 else []),
            "default_if_no_response": options[0],
            "timeout_seconds": 45,
        }
    if external_audience and not branding_known:
        return {
            "message": "This deck goes outside the team. Use a brand template, or a built-in theme?",
            "choices": [
                "Light corporate theme",
                "Minimal theme",
                "Dark theme",
                "I'll give a template path",
            ],
            "default_if_no_response": "Light corporate theme",
            "timeout_seconds": 45,
        }
    return None


def style_offer(tokens: Dict[str, Any]) -> List[str]:
    """Quick style options to offer after delivery (theme / density / notes)."""
    out = []
    for name in THEMES:
        if name != tokens.get("theme"):
            out.append(f"{THEMES[name]['label']} theme")
    density = tokens.get("density", "balanced")
    out.append(
        "Less text per slide" if density != "minimal" else "More detail per slide"
    )
    out.append(
        "Add speaker notes"
        if tokens.get("speaker_notes") is not True
        else "No speaker notes"
    )
    return out[:4]
