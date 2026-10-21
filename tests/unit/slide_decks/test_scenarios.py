# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""End to end: varied, generated decks build, verify clean, and keep their data.

Every scenario is invented by a seeded generator. For each deck the test
asserts the exact slide count, that every chart is native and holds exactly
the outline's values, and that verify_deck finds no overflow or error. Build
time is measured per deck; a 12-slide deck must build and self-check in
seconds.
"""

from __future__ import annotations

import random
import time

import pytest
from pptx import Presentation
from slidedeck.blueprints import outline_from_blueprint
from slidedeck.build import build_deck
from slidedeck.outline import normalize_outline
from slidedeck.verify import chart_series, verify_deck

from .test_build import _custom_template

PERIODS = ["Q3 25", "Q4 25", "Q1 26", "Q2 26", "Q3 26"]
TIMINGS: dict = {}


def _series(rng, names, n, lo, hi, decimals=1):
    return {
        name: [round(rng.uniform(lo, hi), decimals) for _ in range(n)] for name in names
    }


def quarterly_review(rng):
    metrics = [
        ("Revenue ($M)", "Revenue", 3, 9),
        ("Gross margin", "Margin", 0.25, 0.45),
        ("New customers", "Customers", 40, 160),
        ("Support tickets", "Tickets", 200, 900),
        ("Average deal ($K)", "Deal size", 12, 40),
        ("Uptime", "Uptime", 0.97, 0.999),
    ]
    charts = []
    for k, (unit, name, lo, hi) in enumerate(metrics):
        pct = lo < 1
        data = _series(
            rng,
            [f"{name} {r}" for r in ("North", "South")] if k % 2 == 0 else [name],
            5,
            lo,
            hi,
            3 if pct else 1,
        )
        charts.append(
            {
                "type": "chart",
                "chart_type": [
                    "column",
                    "line",
                    "bar",
                    "line_plain",
                    "column_stacked",
                    "area",
                ][k],
                "title": f"{name} moved {rng.choice(['up', 'down'])} over the last five quarters",
                "categories": PERIODS,
                "series": data,
                "number_format": "0.0%" if pct else "#,##0.0",
                "y_title": unit,
                "source": f"metrics_{k}.xlsx, Q3 2025-Q3 2026",
                "notes": f"Explain the {name.lower()} trend.",
            }
        )
    return {
        "expected_slides": 12,
        "slides": [
            {
                "type": "title",
                "title": "Westbrook Tools Q3 2026 business review",
                "subtitle": "Leadership team | October 2026",
            },
            {
                "type": "agenda",
                "title": "Agenda",
                "items": ["Summary", "Metrics", "Plan vs actual", "Next steps"],
            },
            {
                "type": "bullets",
                "kind": "executive_summary",
                "title": "Growth held while margins slipped one point",
                "bullets": [
                    "Revenue up 6% on Q2",
                    "Margin 33%, down 1 point",
                    "Uptime 99.6%",
                ],
            },
            {
                "type": "kpi",
                "title": "Three of four headline metrics improved",
                "metrics": [
                    {
                        "label": "Revenue",
                        "value": 8.4,
                        "unit": "M",
                        "format": "$0.0",
                        "delta": "+6%",
                    },
                    {
                        "label": "Margin",
                        "value": 0.33,
                        "format": "0%",
                        "delta": "-1 pt",
                    },
                    {"label": "Customers", "value": 152, "delta": "+18"},
                    {
                        "label": "Uptime",
                        "value": 0.996,
                        "format": "0.0%",
                        "delta": "+0.2 pts",
                    },
                ],
                "source": "kpis.xlsx, Q3 2026",
            },
            *charts,
            {
                "type": "table",
                "title": "Two regions beat plan and two missed",
                "columns": ["Region", "Plan ($M)", "Actual ($M)", "Variance"],
                "rows": [
                    ["North", 2.0, 2.3, "15%"],
                    ["South", 2.2, 2.1, "-5%"],
                    ["East", 1.9, 2.0, "5%"],
                    ["West", 2.1, 2.0, "-5%"],
                ],
                "formats": {"Plan ($M)": "0.0", "Actual ($M)": "0.0"},
                "source": "plan.xlsx, Q3 2026",
            },
            {
                "type": "table",
                "title": "Next steps: who does what by when",
                "columns": ["Action", "Owner", "Due"],
                "rows": [
                    ["Reprice the starter tier", "Pricing lead", "15 Nov"],
                    ["Hire two support agents", "Support manager", "1 Dec"],
                ],
            },
        ],
    }


def framework_2x2(rng):
    return outline_from_blueprint(
        "one_slide_framework",
        1,
        {
            "framework": {
                "title": "Three initiatives sit in the quick-win quadrant",
                "items": [
                    {"title": "Big bets", "text": "High value, high effort"},
                    {"title": "Quick wins", "text": "High value, low effort"},
                    {"title": "Money pits", "text": "Low value, high effort"},
                    {"title": "Fill-ins", "text": "Low value, low effort"},
                ],
                "x_axis": {"label": "Effort", "low": "Low", "high": "High"},
                "y_axis": {"label": "Value", "low": "Low", "high": "High"},
                "highlight": 1,
            }
        },
        variant="2x2",
    )["outline"]


def process_deck(rng):
    steps = ["Request", "Triage", "Quote", "Approve", "Schedule", "Complete"]
    return {
        "slides": [
            {"type": "title", "title": "How a repair request becomes a finished job"},
            {
                "type": "diagram",
                "diagram": "process",
                "title": "Every repair passes six steps in about four days",
                "items": [
                    {"title": s, "text": f"Day {i + 1}: {s.lower()} owner acts"}
                    for i, s in enumerate(steps)
                ],
            },
            {
                "type": "diagram",
                "diagram": "flow",
                "title": "Large jobs need a second approval",
                "items": [
                    {"id": "r", "title": "Request"},
                    {"id": "s", "title": "Small job"},
                    {"id": "l", "title": "Large job"},
                    {"id": "a", "title": "Second approval"},
                    {"id": "d", "title": "Done"},
                ],
                "edges": [
                    ["r", "s", "< $5k"],
                    ["r", "l", ">= $5k"],
                    ["l", "a"],
                    ["s", "d"],
                    ["a", "d"],
                ],
            },
        ]
    }


def roadmap_deck(rng):
    return outline_from_blueprint(
        "roadmap",
        4,
        {
            "title": {"title": "Field app roadmap 2027", "subtitle": "Product team"},
            "goal": {
                "title": "Crews finish every job without signal by 2028",
                "bullets": ["All forms work offline", "Sync within a minute of signal"],
            },
            "roadmap": {
                "title": "Offline mode lands before the busy season",
                "periods": ["Q1", "Q2", "Q3", "Q4"],
                "lanes": [
                    {
                        "name": "Mobile",
                        "items": [
                            {"label": "Offline mode", "start": "Q1", "end": "Q2"},
                            {"label": "Photo upload", "start": "Q3", "end": "Q4"},
                        ],
                    },
                    {
                        "name": "Platform",
                        "items": [
                            {
                                "label": "Sync service",
                                "start": "Q1",
                                "end": "Q1",
                                "status": "done",
                            },
                            {"label": "Scale-out", "start": "Q2", "end": "Q4"},
                        ],
                    },
                    {
                        "name": "Support",
                        "items": [{"label": "Training", "start": "Q2", "end": "Q3"}],
                    },
                ],
            },
            "milestones": {
                "title": "Five dates decide the year",
                "milestones": [
                    {"date": "Feb", "title": "Beta"},
                    {"date": "Apr", "title": "Offline GA"},
                    {"date": "Jun", "title": "Busy season"},
                    {"date": "Sep", "title": "Photo beta"},
                    {"date": "Nov", "title": "Photo GA"},
                ],
            },
        },
    )["outline"]


def pitch_deck(rng):
    return outline_from_blueprint(
        "pitch",
        6,
        {
            "title": {
                "title": "Shelfwise: stock counts in minutes, not days",
                "subtitle": "Seed round",
            },
            "problem": {
                "title": "Small grocers lose a day a week to stock counts",
                "bullets": ["Counts take 6-8 hours", "Errors cause 4% waste"],
            },
            "solution": {
                "title": "A phone camera counts a shelf in ten seconds",
                "bullets": ["Scan the shelf", "Get the reorder list"],
            },
            "market": {
                "title": "Forty thousand independent grocers need it",
                "metrics": [
                    {"label": "Stores", "value": 40000},
                    {
                        "label": "Annual spend",
                        "value": 240,
                        "unit": "M",
                        "format": "$0",
                    },
                ],
                "source": "industry survey, 2026",
            },
            "traction": {
                "title": "Pilots tripled in two quarters",
                "chart_type": "column",
                "categories": ["Q1", "Q2", "Q3"],
                "series": {"Pilot stores": [4, 7, 13]},
                "y_title": "Stores",
                "source": "pilot log, 2026",
            },
            "ask": {
                "title": "We are raising $2M to reach 500 stores",
                "metrics": [
                    {"label": "Raise", "value": 2, "unit": "M", "format": "$0"},
                    {"label": "Stores by 2028", "value": 500},
                ],
                "source": "plan, 2026",
            },
        },
    )["outline"]


def training_deck(rng):
    return {
        "require_notes": True,
        "slides": [
            {
                "type": "title",
                "title": "Handling a customer escalation",
                "subtitle": "Support onboarding, module 3",
                "notes": "Welcome everyone.",
            },
            {
                "type": "agenda",
                "title": "What you will be able to do",
                "items": [
                    "Spot an escalation early",
                    "Calm the conversation",
                    "Hand over cleanly",
                ],
                "notes": "Read the objectives.",
            },
            {
                "type": "bullets",
                "title": "An escalation starts before the customer says so",
                "bullets": [
                    "Repeated contact in 48 hours",
                    "Words like 'again' or 'still'",
                ],
                "notes": "Give the two signals.",
            },
            {
                "type": "diagram",
                "diagram": "cycle",
                "title": "Acknowledge, act, confirm, follow up",
                "items": ["Acknowledge", "Act", "Confirm", "Follow up"],
                "notes": "Walk the loop.",
            },
            {
                "type": "two_column",
                "title": "Say what you will do, not what you cannot",
                "left": {"heading": "Instead of", "bullets": ["That's not my team"]},
                "right": {
                    "heading": "Say",
                    "bullets": ["I'll get billing on this now"],
                },
                "notes": "Role-play.",
            },
            {
                "type": "bullets",
                "title": "Remember the three signals and the loop",
                "bullets": ["Repeat contact", "Frustration words", "Acknowledge first"],
                "notes": "Close.",
            },
        ],
    }


def comparison_deck(rng):
    return outline_from_blueprint(
        "decision",
        4,
        {
            "title": {"title": "Choosing a warehouse system"},
            "recommendation": {
                "title": "We recommend Vendor B for its faster rollout",
                "bullets": ["Live in 10 weeks, not 24", "Costs 8% more over 3 years"],
            },
            "options": {
                "title": "Vendor B wins on time and support, loses on price",
                "columns": ["Criterion", "Vendor A", "Vendor B", "Vendor C"],
                "rows": [
                    ["3-year cost ($K)", 410, 443, 395],
                    ["Rollout (weeks)", 24, 10, 30],
                    ["Support hours", "8x5", "24x7", "8x5"],
                    ["Reference sites", 3, 5, 1],
                ],
                "source": "vendor proposals, Sep 2026",
            },
            "decision_needed": {
                "title": "We need a decision by 15 November",
                "bullets": ["Approve Vendor B", "Release the first payment"],
            },
        },
    )["outline"]


def template_deck(rng):
    return {
        "slides": [
            {
                "type": "title",
                "title": "Riverside council services report",
                "subtitle": "Committee, autumn",
            },
            {
                "type": "bullets",
                "title": "Response times improved in every service",
                "bullets": ["Bins: 2.1 days", "Potholes: 6 days"],
            },
            {
                "type": "chart",
                "title": "Pothole repairs got faster each quarter",
                "chart_type": "line",
                "categories": PERIODS,
                "series": _series(rng, ["Days to repair"], 5, 4, 12),
                "y_title": "Days",
                "source": "works.xlsx",
            },
            {
                "type": "two_column",
                "title": "Two services still miss their target",
                "left": {"heading": "On target", "bullets": ["Bins", "Libraries"]},
                "right": {"heading": "Missing", "bullets": ["Planning", "Parking"]},
            },
        ]
    }


def research_deck(rng):
    data = _series(rng, ["Satisfied"], 4, 0.4, 0.9, 2)
    return {
        "slides": [
            {"type": "title", "title": "Commuter survey: what riders want"},
            {
                "type": "bullets",
                "kind": "executive_summary",
                "title": "Riders value reliability over speed",
                "bullets": ["62% rank reliability first", "Speed ranks third"],
            },
            {
                "type": "chart",
                "chart_type": "auto",
                "intent": "share",
                "title": "Reliability tops the list of priorities",
                "categories": ["Reliability", "Price", "Speed"],
                "series": {"Share": [62, 23, 15]},
                "source": "survey, n=1,204, Aug 2026",
            },
            {
                "type": "chart",
                "title": "Satisfaction rose after the timetable change",
                "categories": ["Q1", "Q2", "Q3", "Q4"],
                "series": data,
                "number_format": "0%",
                "y_title": "Satisfied riders",
                "source": "survey waves, 2026",
            },
            {
                "type": "quote",
                "title": "Riders describe the cost of a late train",
                "quote": "One late train means I miss the school pickup.",
                "attribution": "Weekday rider",
            },
        ]
    }


SCENARIOS = [
    ("quarterly_review_12", quarterly_review, {}, 12),
    ("framework_2x2_1", framework_2x2, {}, 1),
    ("process_6_steps", process_deck, {}, 3),
    ("roadmap", roadmap_deck, {}, 4),
    ("pitch_kpis", pitch_deck, {}, 6),
    ("training_notes", training_deck, {"theme": "minimal"}, 6),
    ("comparison_table", comparison_deck, {"theme": "dark"}, 4),
    ("template_based", template_deck, {"template": True}, 4),
    (
        "research_4x3",
        research_deck,
        {"prefs": {"aspect": "4:3", "density": "minimal"}},
        5,
    ),
]


def _build(name, gen, opts, out, tmp_path):
    outline = gen(random.Random(name))
    kwargs = {}
    if opts.get("theme"):
        kwargs["theme"] = opts["theme"]
    if opts.get("template"):
        kwargs["template_path"] = str(_custom_template(tmp_path / "corp.pptx"))
    if opts.get("prefs"):
        kwargs["prefs"] = opts["prefs"]
    started = time.perf_counter()
    result = build_deck(outline, out(f"{name}.pptx"), **kwargs)
    TIMINGS[name] = time.perf_counter() - started
    return outline, result


@pytest.mark.parametrize("name, gen, opts, n", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_scenario_builds_clean_with_exact_data(name, gen, opts, n, out, tmp_path):
    outline, result = _build(name, gen, opts, out, tmp_path)
    assert result["slides"] == n, result
    v = result["verify"]
    assert v["status"] == "pass", v["issues"]
    assert not {
        "text_overflow",
        "table_overflow",
        "overlap",
        "out_of_bounds",
        "tiny_font",
        "contrast",
    } & set(v["summary"]), v["issues"]
    assert v["style_score"] >= 85, v["issues"]
    normalized = normalize_outline(outline)
    prs = Presentation(result["path"])
    for spec, slide in zip(normalized["slides"], prs.slides):
        if spec["type"] != "chart":
            continue
        frames = [s for s in slide.shapes if s.has_chart]
        assert (
            len(frames) == 1
        ), f"{name}: chart slide {spec['index']} holds a native chart"
        got = {s["name"]: s["values"] for s in chart_series(frames[0].chart)}
        want = {s["name"]: s["values"] for s in spec["chart"]["series"]}
        assert got == want, f"{name}: slide {spec['index']} values"
    if name == "quarterly_review_12":
        assert sum(1 for s in prs.slides for sh in s.shapes if sh.has_chart) == 6
        assert TIMINGS[name] < 8.0, f"12-slide deck took {TIMINGS[name]:.2f}s"
    print(
        f"\n{name}: {result['slides']} slides built+checked in {TIMINGS[name]:.2f}s (build_seconds={result['build_seconds']})"
    )


@pytest.mark.parametrize(
    "name", ["quarterly_review_12", "pitch_kpis", "template_based", "research_4x3"]
)
def test_scenario_renders_with_no_overflow(name, out, tmp_path, renderer):
    gen, opts = next((g, o) for n, g, o, _c in SCENARIOS if n == name)
    _outline, result = _build(name, gen, opts, out, tmp_path)
    rep = verify_deck(
        result["path"], expected_slides=result["slides"], renderer=renderer
    )
    assert rep["render"]["rendered"], rep["render"]
    assert not {"render_overflow", "render_offslide"} & set(rep["summary"]), rep[
        "issues"
    ]
    assert rep["status"] == "pass", rep["issues"]
