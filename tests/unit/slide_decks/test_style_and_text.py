# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Text measurement, number formats, design tokens, preferences and the ask rule."""

from __future__ import annotations

import pytest
from slidedeck import style
from slidedeck.text import (
    MEASURER,
    FontSpec,
    Para,
    fit_size,
    format_number,
    parse_number,
)

# ---------------------------------------------------------------- numbers


@pytest.mark.parametrize(
    "value, code, expected",
    [
        (1234567, "#,##0", "1,234,567"),
        (0.1234, "0.0%", "12.3%"),
        (12.4, "$0.0", "$12.4"),
        (4200000, '$#,##0.0,,"M"', "$4.2M"),
        (-3.456, "0.00", "-3.46"),
        (1234.5, ",.1f", "1,234.5"),
        (0.5, "{:.0%}", "50%"),
        (2024, None, "2024"),
        (12345, None, "12,345"),
        ("n/a", "0.0", "n/a"),
    ],
)
def test_format_number(value, code, expected):
    assert format_number(value, code) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1,234", 1234.0),
        ("12%", 0.12),
        ("$4.2M", 4.2e6),
        ("(300)", -300.0),
        ("3k", 3000.0),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_number(text, expected):
    got = parse_number(text)
    assert (got is None and expected is None) or got == pytest.approx(expected)


# ---------------------------------------------------------------- measuring


def test_wider_text_needs_more_lines():
    f = FontSpec("Calibri")
    short = MEASURER.line_count("Revenue grew", f, 24, 300)
    long = MEASURER.line_count(
        "Revenue grew twelve percent on stronger demand in the western region this quarter",
        f,
        24,
        300,
    )
    assert short == 1 and long >= 3


def test_bold_is_wider_than_regular():
    t = "Quarterly operating review"
    assert MEASURER.width_pt(t, FontSpec("Calibri", True), 20) > MEASURER.width_pt(
        t, FontSpec("Calibri"), 20
    )


def test_fit_shrinks_until_text_fits_and_reports_when_it_cannot():
    paras = [Para("A sentence of moderate length that wraps", level=-1)] * 4
    size, ok, need = fit_size(paras, 300, 200, FontSpec("Calibri"), 24, 12)
    assert ok and 12 <= size < 24 and need <= 200
    size, ok, need = fit_size(paras * 10, 300, 200, FontSpec("Calibri"), 24, 12)
    assert not ok and size == 12 and need > 200  # never cut: reported instead


def test_a_word_that_only_fits_by_breaking_does_not_fit():
    paras = [Para("Configuration", level=-1, bold=True)]
    size, ok, _need = fit_size(paras, 60, 100, FontSpec("Calibri"), 24, 12)
    assert not ok, "breaking a word mid-word must not count as fitting"


def test_unicode_and_cjk_measure():
    f = FontSpec("Calibri")
    assert MEASURER.width_pt("Überprüfung – naïve café", f, 18) > 0
    assert MEASURER.width_pt("季度业绩回顾", f, 18) > 0


# ---------------------------------------------------------------- colour


def test_wcag_contrast_reference_values():
    assert style.contrast("000000", "FFFFFF") == pytest.approx(21.0, rel=1e-3)
    assert style.contrast("767676", "FFFFFF") == pytest.approx(4.54, abs=0.02)
    assert style.contrast("FFFFFF", "FFFFFF") == pytest.approx(1.0)


@pytest.mark.parametrize("theme", list(style.THEMES))
def test_every_theme_meets_text_contrast(theme):
    t = style.resolve_tokens({"theme": theme})
    c = t["colors"]
    for key in ("text", "title", "muted"):
        assert style.contrast(c[key], c["background"]) >= style.MIN_CONTRAST, key
    assert style.contrast(c["on_primary"], c["primary"]) >= style.MIN_CONTRAST_LARGE
    # Chart palettes: the first four hues read against the background as marks.
    for hue in t["palette"][:3]:
        assert style.contrast(hue, c["background"]) >= style.MIN_CONTRAST_LARGE, hue


def test_a_too_light_brand_text_colour_is_repaired():
    t = style.resolve_tokens({"colors": {"text": "EEEEEE", "muted": "DDDDDD"}})
    assert style.contrast(t["colors"]["text"], t["colors"]["background"]) >= 4.5
    assert style.contrast(t["colors"]["muted"], t["colors"]["background"]) >= 4.5
    assert t["sources"]["colors.text"] == "contrast repair"


# ---------------------------------------------------------------- precedence


def test_precedence_request_over_prefs_over_template_over_defaults():
    template = {
        "colors": {"primary": "AA0000", "text": "111111"},
        "fonts": {"body": "Georgia"},
    }
    prefs = {"colors": {"primary": "00AA00"}, "density": "detailed"}
    request = {"colors": {"primary": "0000AA"}}
    t = style.resolve_tokens(request, prefs, template)
    assert t["colors"]["primary"] == "0000AA"
    assert t["sources"]["colors.primary"] == "request"
    assert t["density"] == "detailed"  # from prefs
    assert t["fonts"]["body"] == "Georgia"  # from the template
    t2 = style.resolve_tokens({}, prefs, template)
    assert t2["colors"]["primary"] == "00AA00"
    t3 = style.resolve_tokens({}, {}, template)
    assert t3["colors"]["primary"] == "AA0000"
    assert (
        style.resolve_tokens()["colors"]["primary"]
        == style.THEMES["light"]["colors"]["primary"]
    )


def test_a_template_named_in_the_request_outranks_saved_look():
    template = {"colors": {"primary": "AA0000"}, "fonts": {"body": "Georgia"}}
    prefs = {"theme": "dark", "colors": {"primary": "00AA00"}, "density": "minimal"}
    t = style.resolve_tokens({}, prefs, template, template_explicit=True)
    assert t["colors"]["primary"] == "AA0000" and t["fonts"]["body"] == "Georgia"
    assert t["density"] == "minimal", "non-look preferences still apply"


def test_brand_primary_leads_the_chart_palette():
    t = style.resolve_tokens({}, {"colors": {"primary": "#123456"}})
    assert t["palette"][0] == "123456"


# ---------------------------------------------------------------- density and rules


def test_density_changes_verifier_limits():
    lo, mid, hi = (
        style.rules_for({"density": d}) for d in ("minimal", "balanced", "detailed")
    )
    assert lo["max_bullets"] < mid["max_bullets"] < hi["max_bullets"]
    assert lo["max_body_words"] < mid["max_body_words"] < hi["max_body_words"]
    assert style.rules_for({}, {"max_bullets": 3})["max_bullets"] == 3


# ---------------------------------------------------------------- preferences


def test_prefs_validation_names_each_problem():
    assert style.validate_prefs({"density": "balanced", "theme": "dark"}) == []
    problems = style.validate_prefs(
        {"density": "huge", "colour": "red", "colors": {"primary": "blue"}}
    )
    joined = " ".join(problems)
    assert "density" in joined and "colour" in joined and "primary" in joined


def test_prefs_round_trip_through_a_text_store():
    store = {}
    saved = style.update_prefs(
        {}, {"density": "minimal", "colors": {"primary": "1F4E79"}}
    )
    store[style.PREFS_MEMORY_KEY] = (
        "User likes: " + style.prefs_to_text(saved) + " (from feedback)"
    )
    loaded = style.prefs_from_text(store[style.PREFS_MEMORY_KEY])
    assert loaded == saved
    assert style.prefs_from_text("nothing here") is None
    updated = style.update_prefs(loaded, {"speaker_notes": False})
    assert updated["density"] == "minimal" and updated["speaker_notes"] is False
    with pytest.raises(style.PrefsError):
        style.update_prefs(loaded, {"density": "very"})


@pytest.mark.parametrize(
    "feedback, expected",
    [
        ("less text please", {"density": "minimal"}),
        ("too wordy", {"density": "minimal"}),
        ("no speaker notes", {"speaker_notes": False}),
        ("use our blue #1F4E79", {"colors": {"primary": "1F4E79"}}),
        ("switch to a dark theme", {"theme": "dark"}),
        ("we prefer tables", {"prefer": "tables"}),
        ("great, thanks", {}),
    ],
)
def test_feedback_maps_to_preferences(feedback, expected):
    assert style.prefs_from_feedback(feedback) == expected


def test_feedback_preferences_validate():
    assert (
        style.validate_prefs(
            style.prefs_from_feedback("less text, no notes, dark theme, #224466")
        )
        == []
    )


# ---------------------------------------------------------------- asking


def test_no_question_when_nothing_material_is_ambiguous():
    assert style.clarifying_question() is None
    assert style.clarifying_question(external_audience=False) is None
    assert (
        style.clarifying_question(external_audience=True, branding_known=True) is None
    )


def test_one_question_with_choices_default_and_short_timeout():
    q = style.clarifying_question(external_audience=True)
    assert q and 3 <= len(q["choices"]) <= 4
    assert q["default_if_no_response"] in q["choices"]
    assert 0 < q["timeout_seconds"] <= style.MAX_QUESTION_TIMEOUT_S
    c = style.clarifying_question(conflicts=["10 slides", "5 slides"])
    assert c["default_if_no_response"] == "10 slides" and c["timeout_seconds"] <= 60


def test_style_offer_suggests_other_themes_density_and_notes():
    offers = style.style_offer(style.resolve_tokens({"theme": "light"}))
    assert any("Dark" in o for o in offers) and any("text" in o.lower() for o in offers)
    assert len(offers) <= 4


# ---------------------------------------------------------------- chart choice


@pytest.mark.parametrize(
    "cats, series, intent, expected",
    [
        (
            ["Q1 25", "Q2 25", "Q3 25", "Q4 25"],
            [{"values": [1, 2, 3, 4]}],
            "",
            "column",
        ),
        (
            [f"2026-{m:02d}" for m in range(1, 13)],
            [{"values": list(range(12))}],
            "",
            "line",
        ),
        (["North", "South", "East"], [{"values": [3, 2, 1]}], "share", "pie"),
        (
            ["A", "B", "C", "D", "E", "F", "G", "H"],
            [{"values": list(range(8))}],
            "share",
            "bar",
        ),
        (
            ["Alpha", "Beta"],
            [{"values": [1, 2]}, {"values": [3, 4]}],
            "share",
            "column_stacked_100",
        ),
        (["Long category name one", "Two"], [{"values": [1, 2]}], "", "bar"),
        (["x"], [{"values": [1]}], "correlation", "scatter"),
    ],
)
def test_recommend_chart(cats, series, intent, expected):
    assert style.recommend_chart(cats, series, intent=intent)[0] == expected
