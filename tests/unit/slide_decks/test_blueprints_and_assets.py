# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Blueprints scale to exact counts and flag unfilled slots; committed assets match the code."""

from __future__ import annotations

import json
import zipfile

import pytest
from lxml import etree
from slidedeck.blueprint_data import BLUEPRINTS
from slidedeck.blueprints import (
    get_blueprint,
    list_blueprints,
    outline_from_blueprint,
    plan_slots,
)
from slidedeck.build import build_deck
from slidedeck.outline import OutlineError, normalize_outline
from slidedeck.style import THEMES, resolve_tokens
from slidedeck.theme import make_theme_presentation

from .conftest import SKILL_DIR

NAMES = [bp["name"] for bp in BLUEPRINTS]


def counts(bp):
    lo, hi = bp["min_slides"], bp["max_slides"]
    return sorted({lo, min(lo + 1, hi), bp["default_slides"], (lo + hi) // 2, hi})


def test_the_expected_blueprints_ship():
    assert set(NAMES) >= {
        "quarterly_review",
        "executive_summary",
        "pitch",
        "project_status",
        "training",
        "research_findings",
        "decision",
        "roadmap",
        "one_slide_framework",
    }
    listed = list_blueprints()
    assert all(b["slots"] and b["use_for"] for b in listed)


@pytest.mark.parametrize("name", NAMES)
def test_every_count_in_range_gives_exactly_that_many_slides(name):
    bp = get_blueprint(name)
    for n in range(bp["min_slides"], bp["max_slides"] + 1):
        r = outline_from_blueprint(name, n)
        assert len(r["outline"]["slides"]) == n
        assert r["outline"]["expected_slides"] == n
        assert len(r["todo"]) == n, "nothing filled yet: every slot is a TODO"
        normalize_outline(r["outline"])  # the TODO outline still validates
    with pytest.raises(OutlineError, match="supports"):
        outline_from_blueprint(name, bp["max_slides"] + 1)


@pytest.mark.parametrize("name", NAMES)
def test_scaling_is_deterministic_and_keeps_storyline_order(name):
    bp = get_blueprint(name)
    order = [s["id"] for s in bp["slots"]]
    for n in counts(bp):
        a = [t["slot"] for t in outline_from_blueprint(name, n)["slots"]]
        b = [t["slot"] for t in outline_from_blueprint(name, n)["slots"]]
        assert a == b
        positions = [order.index(k.split("#")[0]) for k in a]
        assert positions == sorted(positions)


def test_longer_decks_keep_everything_a_shorter_one_had():
    bp = get_blueprint("quarterly_review")
    prev: set = set()
    for n in range(2, bp["max_slides"] + 1):
        now = {s["id"] + f"#{k}" for s, k in plan_slots(bp, n)[0]}
        assert prev <= now, f"growing to {n} dropped {prev - now}"
        prev = now


def test_one_slide_uses_the_designated_slot():
    r = outline_from_blueprint("quarterly_review", 1)
    assert [t["slot"] for t in r["slots"]] == ["exec_summary"]
    assert {d["slot"] for d in r["dropped"]} >= {"trend", "kpis"}


def test_fills_by_slot_copy_and_list():
    trend = {
        "title": "Revenue rose each quarter",
        "categories": ["Q1", "Q2"],
        "series": {"Revenue": [1, 2]},
        "source": "f.xlsx",
    }
    r = outline_from_blueprint(
        "quarterly_review",
        12,
        {
            "trend": [trend, dict(trend, title="Margin held at 30%")],
            "trend#3": dict(trend, title="Costs fell"),
        },
    )
    titles = [s["title"] for s in r["outline"]["slides"]]
    assert (
        "Revenue rose each quarter" in titles
        and "Margin held at 30%" in titles
        and "Costs fell" in titles
    )
    with pytest.raises(OutlineError, match="unknown slot"):
        outline_from_blueprint("pitch", 5, {"nope": {}})
    with pytest.raises(OutlineError, match="variant"):
        outline_from_blueprint("one_slide_framework", 1, variant="hexagon")


def test_unfilled_slots_build_but_fail_verification(out):
    r = outline_from_blueprint("project_status", 4)
    result = build_deck(r["outline"], out())
    assert result["slides"] == 4
    assert result["verify"]["status"] == "fail"
    assert result["verify"]["summary"]["unfilled_slot"] >= 4
    assert any("TODO" in w for w in result["warnings"])


def test_a_filled_blueprint_verifies_clean(out):
    r = outline_from_blueprint(
        "executive_summary",
        3,
        {
            "title": {
                "title": "Moving support to 24x7 coverage",
                "subtitle": "Operations committee",
            },
            "exec_summary": {
                "title": "Round-the-clock support pays back in 14 months",
                "bullets": [
                    "Night tickets wait 9 hours today",
                    "Two hires close the gap",
                    "Payback in 14 months",
                ],
            },
            "evidence": {
                "title": "Night-time tickets wait six times longer",
                "chart_type": "bar",
                "categories": ["Day", "Evening", "Night"],
                "series": {"Hours to first reply": [1.5, 3.1, 9.0]},
                "y_title": "Hours",
                "source": "helpdesk export, Jun-Aug 2026",
            },
        },
    )
    assert not r["todo"]
    result = build_deck(r["outline"], out())
    assert result["verify"]["status"] == "pass", result["verify"]["issues"]


@pytest.mark.parametrize(
    "variant", ["2x2", "pyramid", "cycle", "process", "stack", "canvas"]
)
def test_framework_variants_build(variant, out):
    items = [
        {"title": t, "text": f"{t} in one line"}
        for t in ("Alpha", "Bravo", "Charlie", "Delta")
    ]
    if variant == "canvas":
        items = [dict(it, row=k // 2, col=k % 2) for k, it in enumerate(items)]
    r = outline_from_blueprint(
        "one_slide_framework",
        1,
        {
            "framework": {
                "title": "Four parts make up the operating model",
                "items": items,
            }
        },
        variant=variant,
    )
    result = build_deck(r["outline"], out())
    assert result["slides"] == 1
    assert result["verify"]["status"] == "pass", result["verify"]["issues"]


# ---------------------------------------------------------------- committed assets


def test_blueprint_json_files_match_the_data_module():
    folder = SKILL_DIR / "templates" / "blueprints"
    on_disk = {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")
    }
    assert on_disk == {
        bp["name"]: bp for bp in BLUEPRINTS
    }, "run: python util/gen_slide_deck_assets.py"


def _parts(path_or_prs):
    """The XML parts that define a template, canonicalised (no timestamps)."""
    import io

    if not isinstance(path_or_prs, (str, bytes)) and hasattr(path_or_prs, "save"):
        buf = io.BytesIO()
        path_or_prs.save(buf)
        z = zipfile.ZipFile(buf)
    else:
        z = zipfile.ZipFile(path_or_prs)
    out = {}
    for name in z.namelist():
        if name.startswith(
            ("ppt/slideMasters/", "ppt/slideLayouts/", "ppt/theme/")
        ) and name.endswith(".xml"):
            out[name] = etree.tostring(etree.fromstring(z.read(name)), method="c14n")
    return out


@pytest.mark.parametrize("theme", list(THEMES))
def test_theme_template_files_match_the_tokens(theme):
    committed = SKILL_DIR / "templates" / f"{theme}.pptx"
    assert committed.is_file()
    fresh = make_theme_presentation(resolve_tokens({"theme": theme}))
    assert _parts(str(committed)) == _parts(
        fresh
    ), "run: python util/gen_slide_deck_assets.py"


@pytest.mark.parametrize("theme", list(THEMES))
def test_a_committed_template_works_as_a_user_template(theme, out):
    deck = {
        "slides": [
            {"type": "title", "title": "Template check"},
            {
                "type": "bullets",
                "title": "The committed template builds a clean deck",
                "bullets": ["One", "Two"],
            },
        ]
    }
    r = build_deck(
        deck, out(), template_path=str(SKILL_DIR / "templates" / f"{theme}.pptx")
    )
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
