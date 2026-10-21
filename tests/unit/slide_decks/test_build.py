# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Building each slide type, templates, preferences and incremental edits."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
from slidedeck.build import add_slide, build_deck, update_slide
from slidedeck.outline import OutlineError
from slidedeck.style import THEMES
from slidedeck.verify import chart_series

A = "http://schemas.openxmlformats.org/drawingml/2006/main"

CHART = {
    "type": "chart",
    "title": "Revenue rose in four of five quarters",
    "categories": ["Q1", "Q2", "Q3", "Q4", "Q5"],
    "series": {"Revenue": [4.1, 4.6, 4.4, 5.0, 5.6]},
    "y_title": "Revenue ($M)",
    "number_format": "0.0",
    "source": "finance.xlsx, Q1-Q5",
}

ONE_OF_EACH = {
    "title": {
        "type": "title",
        "title": "Harbor Logistics annual review",
        "subtitle": "Board meeting",
        "date": "March 2026",
    },
    "section": {
        "type": "section",
        "title": "Results",
        "subtitle": "What happened this year",
    },
    "agenda": {
        "type": "agenda",
        "title": "Agenda",
        "items": ["Results", "Risks", "Plan"],
    },
    "bullets": {
        "type": "bullets",
        "title": "Volume grew 8% while costs held flat",
        "bullets": ["Volume up 8%", ["Driven by two new lanes"], "Costs flat at $31M"],
        "notes": "Say the lanes.",
    },
    "two_column": {
        "type": "two_column",
        "title": "Option B ships sooner but costs more",
        "left": {"heading": "Option A", "bullets": ["$1.2M", "June"]},
        "right": {"heading": "Option B", "bullets": ["$1.5M", "April"]},
    },
    "chart": CHART,
    "table": {
        "type": "table",
        "title": "Two depots missed their target",
        "columns": ["Depot", "Target", "Actual"],
        "rows": [["Ashford", 120, 131], ["Brook", 95, 90], ["Carrow", 80, 74]],
        "source": "depots.xlsx, FY2025",
    },
    "kpi": {
        "type": "kpi",
        "title": "On-time delivery hit a record 97%",
        "metrics": [
            {"label": "On time", "value": 0.97, "format": "0%", "delta": "+2 pts"},
            {"label": "Claims", "value": 41, "delta": "-12", "good": "down"},
        ],
        "source": "ops.xlsx, FY2025",
    },
    "quote": {
        "type": "quote",
        "title": "Customers value the new tracking most",
        "quote": "I finally know when the truck arrives.",
        "attribution": "Retail customer",
    },
    "diagram": {
        "type": "diagram",
        "diagram": "process",
        "title": "A shipment passes four checkpoints",
        "items": ["Pick", "Pack", "Load", "Deliver"],
    },
    "timeline": {
        "type": "timeline",
        "title": "The new hub opens in June",
        "milestones": [
            {"date": "Jan", "title": "Lease signed"},
            {"date": "Mar", "title": "Fit-out"},
            {"date": "Jun", "title": "Opening"},
        ],
    },
}


def shapes_named(slide, prefix):
    return [s for s in slide.shapes if (s.name or "").startswith(prefix)]


def run_sizes(shape):
    return [
        r.font.size.pt
        for p in shape.text_frame.paragraphs
        for r in p.runs
        if r.font.size
    ]


@pytest.mark.parametrize("kind", list(ONE_OF_EACH))
def test_each_slide_type_builds_and_verifies(kind, out):
    r = build_deck({"slides": [ONE_OF_EACH[kind]]}, out())
    assert r["slides"] == 1
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
    prs = Presentation(r["path"])
    slide = prs.slides[0]
    title = next(
        p
        for p in slide.placeholders
        if p.placeholder_format.type
        in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE)
    )
    assert title.text_frame.text == ONE_OF_EACH[kind]["title"]
    assert slide.shapes[0].shape_id == title.shape_id, "title first in reading order"
    for shape in slide.shapes:
        if shape.has_text_frame:
            for size in run_sizes(shape):
                assert size >= 11
            for p in shape.text_frame.paragraphs:
                for r_ in p.runs:
                    if r_.text.strip():
                        assert (
                            r_.font.size is not None
                        ), f"{kind}: every run has an explicit size"


def test_chart_is_native_with_exact_values_and_alt_text(out):
    r = build_deck({"slides": [CHART]}, out())
    slide = Presentation(r["path"]).slides[0]
    charts = [s for s in slide.shapes if s.has_chart]
    assert len(charts) == 1 and not [
        s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE
    ]
    got = chart_series(charts[0].chart)
    assert got[0]["name"] == "Revenue" and got[0]["values"] == [4.1, 4.6, 4.4, 5.0, 5.6]
    assert got[0]["categories"] == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    descr = charts[0]._element.xpath("./p:nvGraphicFramePr/p:cNvPr/@descr")[0]
    assert "Column chart" in descr and "Revenue" in descr
    ch = charts[0].chart
    assert not ch.has_legend, "single series: no legend"
    assert ch.value_axis.minimum_scale == 0
    assert ch.value_axis.axis_title.text_frame.text == "Revenue ($M)"


@pytest.mark.parametrize(
    "ctype",
    [
        "column",
        "column_stacked",
        "column_stacked_100",
        "bar",
        "bar_stacked",
        "line",
        "line_plain",
        "area",
        "area_stacked",
        "doughnut",
    ],
)
def test_every_category_chart_type_round_trips(ctype, out):
    spec = dict(
        CHART,
        chart_type=ctype,
        series={"A": [1, 2, 3, 4, 5], "B": [2, 3, 4, 5, 6]},
        title="Both series rise steadily each quarter",
    )
    r = build_deck({"slides": [spec]}, out())
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]


def test_pie_and_scatter(out):
    pie = {
        "type": "chart",
        "chart_type": "pie",
        "title": "Retail is half of all orders",
        "categories": ["Retail", "Trade", "Online"],
        "series": {"Share": [50, 30, 20]},
        "source": "orders.csv, 2025",
    }
    sc = {
        "type": "chart",
        "chart_type": "scatter",
        "title": "Bigger stores sell more per hour",
        "series": [{"name": "Stores", "points": [[100, 5], [200, 9], [300, 14]]}],
        "x_title": "Floor area (m2)",
        "y_title": "Sales per hour",
        "source": "stores.csv, 2025",
    }
    r = build_deck({"slides": [pie, sc]}, out())
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
    sc_chart = [s for s in Presentation(r["path"]).slides[1].shapes if s.has_chart][
        0
    ].chart
    assert chart_series(sc_chart)[0]["values"] == [5.0, 9.0, 14.0]


def test_combo_with_secondary_axis_is_one_native_chart(out):
    spec = {
        "type": "chart",
        "chart_type": "combo",
        "title": "Orders climbed while margin held near 31%",
        "categories": ["Jan", "Feb", "Mar", "Apr"],
        "series": [
            {"name": "Orders", "values": [120, 132, 128, 140]},
            {
                "name": "Margin",
                "values": [0.30, 0.31, 0.305, 0.312],
                "type": "line",
                "axis": "secondary",
                "number_format": "0%",
            },
        ],
        "y_title": "Orders",
        "y2_title": "Margin",
        "source": "ops.csv, Jan-Apr",
    }
    r = build_deck({"slides": [spec]}, out())
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
    gf = [s for s in Presentation(r["path"]).slides[0].shapes if s.has_chart][0]
    plot_area = gf.chart._chartSpace.chart.plotArea
    assert (
        len(plot_area.xpath("c:barChart")) == 1
        and len(plot_area.xpath("c:lineChart")) == 1
    )
    assert len(plot_area.xpath("c:valAx")) == 2
    vals = {s["name"]: s["values"] for s in chart_series(gf.chart)}
    assert vals == {
        "Orders": [120, 132, 128, 140],
        "Margin": [0.30, 0.31, 0.305, 0.312],
    }
    sec = plot_area.xpath("c:valAx")[1]
    assert sec.find(f"{{{plot_area.nsmap['c']}}}axPos").get("val") == "r"


def test_secondary_axis_on_horizontal_bars_is_an_error(out):
    spec = dict(
        CHART,
        chart_type="bar",
        series=[
            {"name": "A", "values": [1, 2, 3, 4, 5]},
            {"name": "B", "values": [1, 2, 3, 4, 5], "axis": "secondary"},
        ],
    )
    with pytest.raises(OutlineError, match="horizontal bars"):
        build_deck({"slides": [spec]}, out())


def test_highlight_puts_the_message_series_in_the_accent(out):
    spec = dict(
        CHART,
        series={"West": [1, 2, 3, 4, 5], "East": [1, 1, 1, 1, 1]},
        highlight="West",
        title="West grew while East stayed flat",
    )
    r = build_deck({"slides": [spec]}, out())
    ch = [s for s in Presentation(r["path"]).slides[0].shapes if s.has_chart][0].chart
    fills = {s.name: str(s.format.fill.fore_color.rgb) for s in ch.plots[0].series}
    assert fills["West"] == THEMES["light"]["colors"]["primary"]
    assert fills["East"] != fills["West"]


def test_table_aligns_numbers_right_and_sizes_rows(out):
    r = build_deck({"slides": [ONE_OF_EACH["table"]]}, out())
    table = [s for s in Presentation(r["path"]).slides[0].shapes if s.has_table][
        0
    ].table
    from pptx.enum.text import PP_ALIGN

    assert table.cell(1, 0).text_frame.paragraphs[0].alignment == PP_ALIGN.LEFT
    assert table.cell(1, 1).text_frame.paragraphs[0].alignment == PP_ALIGN.RIGHT
    assert table.cell(0, 0).text_frame.paragraphs[0].runs[0].font.bold


def test_kpi_numerals_are_large_and_delta_carries_direction_in_text(out):
    r = build_deck({"slides": [ONE_OF_EACH["kpi"]]}, out())
    tile = shapes_named(Presentation(r["path"]).slides[0], "KPI tile")[0]
    sizes = run_sizes(tile)
    assert max(sizes) >= 54
    assert "▲" in tile.text_frame.text


@pytest.mark.parametrize(
    "layout",
    ["process", "flow", "cycle", "pyramid", "matrix", "grid", "boxes", "tree", "stack"],
)
def test_every_diagram_layout(layout, out):
    items = [
        {"title": t, "text": f"{t} detail"} for t in ("Plan", "Build", "Test", "Ship")
    ]
    spec = {
        "type": "diagram",
        "diagram": layout,
        "title": "Four stages take an idea to customers",
        "items": items,
    }
    if layout == "flow":
        spec["items"] = [dict(it, id=it["title"]) for it in items]
        spec["edges"] = [["Plan", "Build"], ["Build", "Test", "pass"], ["Test", "Ship"]]
    if layout == "tree":
        spec["items"] = [
            {"id": "ceo", "title": "Chief executive"},
            {"id": "ops", "title": "Operations", "parent": "ceo"},
            {"id": "fin", "title": "Finance", "parent": "ceo"},
        ]
    if layout == "grid":
        spec["items"] = [dict(it, row=k // 2, col=k % 2) for k, it in enumerate(items)]
    r = build_deck({"slides": [spec]}, out())
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
    assert not r["warnings"], r["warnings"]


def test_matrix_with_points_and_roadmap_lanes(out):
    matrix = {
        "type": "diagram",
        "diagram": "matrix",
        "title": "Two projects are quick wins",
        "items": [
            {"title": "Big bets"},
            {"title": "Quick wins"},
            {"title": "Money pits"},
            {"title": "Fill-ins"},
        ],
        "x_axis": {"label": "Effort", "low": "Low", "high": "High"},
        "y_axis": {"label": "Value"},
        "points": [
            {"label": "Portal", "x": 0.2, "y": 0.8},
            {"label": "Billing", "x": 0.7, "y": 0.3},
        ],
    }
    road = {
        "type": "timeline",
        "title": "Both workstreams finish by Q4",
        "periods": ["Q1", "Q2", "Q3", "Q4"],
        "lanes": [
            {
                "name": "Platform",
                "items": [
                    {"label": "Migrate", "start": "Q1", "end": "Q2", "status": "done"},
                    {"label": "Harden", "start": "Q3", "end": "Q4"},
                ],
            },
            {
                "name": "Sales",
                "items": [
                    {"label": "Hire", "start": 0, "end": 0},
                    {"label": "Ramp", "start": 1, "end": 3},
                    {"label": "Overlap", "start": 2, "end": 3},
                ],
            },
        ],
    }
    r = build_deck({"slides": [matrix, road]}, out())
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
    bars = shapes_named(Presentation(r["path"]).slides[1], "Bar ")
    assert len(bars) == 5
    tops = sorted({b.top for b in bars if b.name.startswith("Bar 2.")})
    assert len(tops) == 2, "overlapping bars in one lane go on separate rows"


def test_image_slide_keeps_aspect_ratio_and_alt_text(out, tmp_path):
    from PIL import Image

    img = tmp_path / "photo.png"
    Image.new("RGB", (400, 200), "steelblue").save(img)
    r = build_deck(
        {
            "slides": [
                {
                    "type": "image",
                    "title": "The new depot is twice the old floor area",
                    "path": str(img),
                    "alt": "Aerial photo of the depot",
                    "caption": "Ashford depot, May 2026",
                }
            ]
        },
        out(),
    )
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
    pic = [
        s
        for s in Presentation(r["path"]).slides[0].shapes
        if s.shape_type == MSO_SHAPE_TYPE.PICTURE
    ][0]
    assert abs(pic.width / pic.height - 2.0) < 0.01
    assert pic._element.xpath("./p:nvPicPr/p:cNvPr/@descr") == [
        "Aerial photo of the depot"
    ]


def test_text_that_cannot_fit_is_kept_whole_and_reported(out):
    long = [
        "This bullet is deliberately long so that the slide overflows at the minimum size "
        * 2
    ] * 12
    r = build_deck(
        {
            "slides": [
                {
                    "type": "bullets",
                    "title": "Too much text on one slide",
                    "bullets": long,
                }
            ]
        },
        out(),
    )
    assert any("overflow" in w for w in r["warnings"])
    text = Presentation(r["path"]).slides[0].placeholders[1].text_frame.text
    assert text.count("deliberately long") == 24, "nothing was cut"
    assert r["verify"]["status"] == "fail"


def test_allow_split_continues_on_extra_slides(out):
    bullets = [
        f"Point {i}: a line of moderate length about the results" for i in range(30)
    ]
    r = build_deck(
        {
            "allow_split": True,
            "slides": [
                {
                    "type": "bullets",
                    "title": "All thirty findings in one list",
                    "bullets": bullets,
                }
            ],
        },
        out(),
    )
    assert r["slides"] >= 2 and r["slide_map"]["1"] == list(range(1, r["slides"] + 1))
    titles = [s.shapes.title.text for s in Presentation(r["path"]).slides]
    assert titles[1].endswith("(cont.)")
    rows = [[f"Row {i}", i] for i in range(40)]
    r = build_deck(
        {
            "allow_split": True,
            "slides": [
                {
                    "type": "table",
                    "title": "Every account in one table",
                    "columns": ["Account", "Units"],
                    "rows": rows,
                    "source": "crm.csv",
                }
            ],
        },
        out("t.pptx"),
    )
    assert r["slides"] >= 2
    total = sum(
        len(s.shapes[i].table.rows) - 1
        for s in Presentation(r["path"]).slides
        for i in range(len(s.shapes))
        if s.shapes[i].has_table
    )
    assert total == 40


# ---------------------------------------------------------------- themes and templates


@pytest.mark.parametrize("theme", list(THEMES))
def test_each_theme_builds_every_type_and_passes(theme, out):
    r = build_deck({"slides": list(ONE_OF_EACH.values())}, out(), theme=theme)
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
    assert r["verify"]["style_score"] >= 90
    prs = Presentation(r["path"])
    names = [layout.name for layout in prs.slide_layouts]
    assert names == [
        "Title Slide",
        "Title and Content",
        "Section Header",
        "Two Content",
        "Comparison",
        "Title Only",
        "Blank",
    ]


def _custom_template(path: Path, *, potx: bool = False, sample_slides: int = 2) -> Path:
    """An invented corporate template: renamed layouts, its own colours and fonts."""
    prs = Presentation()
    names = {
        "Title Slide": "Cover",
        "Title and Content": "Headline + Text",
        "Title Only": "Headline Only",
        "Section Header": "Section Break",
        "Two Content": "Side by Side",
    }
    for layout in prs.slide_layouts:
        if layout.name in names:
            layout._element.cSld.set("name", names[layout.name])
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    part = prs.slide_master.part.part_related_by(RT.THEME)
    root = etree.fromstring(part.blob)
    ns = {"a": A}
    acc = root.find(".//a:clrScheme/a:accent1", ns)
    for c in list(acc):
        acc.remove(c)
    etree.SubElement(acc, f"{{{A}}}srgbClr").set("val", "7A1F5C")
    root.find(".//a:minorFont/a:latin", ns).set("typeface", "Georgia")
    root.find(".//a:majorFont/a:latin", ns).set("typeface", "Georgia")
    part._blob = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    for _ in range(sample_slides):
        prs.slides.add_slide(prs.slide_layouts[1]).shapes.title.text = "Sample"
    buf = io.BytesIO()
    prs.save(buf)
    data = buf.getvalue()
    if potx:
        src = zipfile.ZipFile(io.BytesIO(data))
        o = io.BytesIO()
        with zipfile.ZipFile(o, "w") as dst:
            for item in src.infolist():
                blob = src.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    blob = blob.replace(
                        b"presentationml.presentation.main+xml",
                        b"presentationml.template.main+xml",
                    )
                dst.writestr(item, blob)
        data = o.getvalue()
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("suffix", [".pptx", ".potx"])
def test_user_template_layouts_theme_and_fonts_are_used(suffix, out, tmp_path):
    tpl = _custom_template(tmp_path / f"corp{suffix}", potx=suffix == ".potx")
    r = build_deck(
        {"slides": list(ONE_OF_EACH.values())}, out(), template_path=str(tpl)
    )
    assert r["slides"] == len(ONE_OF_EACH), "the template's sample slides were removed"
    assert any("sample slide" in n for n in r["notes"])
    assert r["look_from"] == "template (this request)"
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
    prs = Presentation(r["path"])
    used = [s.slide_layout.name for s in prs.slides]
    assert (
        used[0] == "Cover"
        and "Headline Only" in used
        and "Headline + Text" in used
        and "Section Break" in used
    )
    fonts = {
        r_.font.name
        for s in prs.slides
        for sh in s.shapes
        if sh.has_text_frame
        for p in sh.text_frame.paragraphs
        for r_ in p.runs
    }
    assert fonts == {"Georgia"}
    kpi_slide = prs.slides[list(ONE_OF_EACH).index("kpi")]
    tile_value = shapes_named(kpi_slide, "KPI tile")[0].text_frame.paragraphs[0].runs[0]
    assert (
        str(tile_value.font.color.rgb) == "7A1F5C"
    ), "the template's accent is the primary"


def test_template_without_title_only_layout_falls_back(out, tmp_path):
    prs = Presentation()
    keep = {"Title Slide", "Title and Content"}
    master = prs.slide_master
    lst = master._element.find(
        "{http://schemas.openxmlformats.org/presentationml/2006/main}sldLayoutIdLst"
    )
    for el in list(lst):
        part = master.part.related_part(
            el.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
        )
        if part.slide_layout.name not in keep:
            lst.remove(el)
    tpl = tmp_path / "thin.pptx"
    prs.save(tpl)
    r = build_deck(
        {"slides": [CHART, ONE_OF_EACH["bullets"]]}, out(), template_path=str(tpl)
    )
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]
    assert any("Title Only" in n for n in r["notes"])


def test_template_cannot_be_the_output(tmp_path):
    tpl = _custom_template(tmp_path / "t.pptx", sample_slides=0)
    with pytest.raises(OutlineError, match="template itself"):
        build_deck({"slides": [CHART]}, str(tpl), template_path=str(tpl))


# ---------------------------------------------------------------- preferences


def test_prefs_aspect_footer_notes_and_numbers(out):
    prefs = {
        "aspect": "4:3",
        "footer": {"text": "Confidential"},
        "speaker_notes": False,
        "number_format": {"decimals": 1},
    }
    deck = {
        "slides": [
            ONE_OF_EACH["bullets"],
            {
                "type": "table",
                "title": "Unit costs fell in both plants",
                "columns": ["Plant", "Cost"],
                "rows": [["A", 12.345], ["B", 9]],
                "source": "costs.xlsx",
            },
        ]
    }
    r = build_deck(deck, out(), prefs=prefs)
    prs = Presentation(r["path"])
    assert abs(prs.slide_width / prs.slide_height - 4 / 3) < 0.01
    assert (
        not prs.slides[0].has_notes_slide
        or not prs.slides[0].notes_slide.notes_text_frame.text
    )
    assert any("notes are off" in n for n in r["notes"])
    footer = shapes_named(prs.slides[0], "Slide number")[0].text_frame.text
    assert "Confidential" in footer
    cells = [
        prs.slides[1].shapes[i]
        for i in range(len(prs.slides[1].shapes))
        if prs.slides[1].shapes[i].has_table
    ][0].table
    assert cells.cell(1, 1).text == "12.3" and cells.cell(2, 1).text == "9.0"
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]


def test_explicit_style_beats_saved_prefs(out):
    deck = {"style": {"theme": "dark"}, "slides": [ONE_OF_EACH["bullets"]]}
    r = build_deck(deck, out(), prefs={"theme": "minimal"})
    assert r["theme"] == "dark"
    r = build_deck(
        {"slides": [ONE_OF_EACH["bullets"]]}, out("b.pptx"), prefs={"theme": "minimal"}
    )
    assert r["theme"] == "minimal"


def test_density_preference_sets_body_size_floor(out):
    many = {
        "type": "bullets",
        "title": "Seven reasons the pilot worked well",
        "bullets": [f"Reason number {i} explained in a few words" for i in range(7)],
    }
    lo = build_deck({"slides": [many]}, out("lo.pptx"), prefs={"density": "minimal"})
    hi = build_deck({"slides": [many]}, out("hi.pptx"), prefs={"density": "detailed"})
    assert lo["verify"]["summary"].get("too_many_bullets") == 1
    assert "too_many_bullets" not in hi["verify"]["summary"]


def test_prefer_tables_turns_data_slides_into_tables(out):
    data = dict(CHART, type="data")
    r = build_deck({"slides": [data]}, out(), prefs={"prefer": "tables"})
    slide = Presentation(r["path"]).slides[0]
    assert any(s.has_table for s in slide.shapes) and not any(
        s.has_chart for s in slide.shapes
    )
    r = build_deck({"slides": [data]}, out("c.pptx"))
    assert any(s.has_chart for s in Presentation(r["path"]).slides[0].shapes)


def test_brand_colour_and_logo(out, tmp_path):
    from PIL import Image

    logo = tmp_path / "logo.png"
    Image.new("RGB", (300, 100), "darkgreen").save(logo)
    r = build_deck(
        {"slides": [ONE_OF_EACH["title"], ONE_OF_EACH["kpi"]]},
        out(),
        prefs={"colors": {"primary": "#1E5631"}, "logo_path": str(logo)},
    )
    prs = Presentation(r["path"])
    assert all(shapes_named(s, "Logo") for s in prs.slides)
    value_run = (
        shapes_named(prs.slides[1], "KPI tile")[0].text_frame.paragraphs[0].runs[0]
    )
    assert str(value_run.font.color.rgb) == "1E5631"
    assert r["verify"]["status"] == "pass", r["verify"]["issues"]


def test_invalid_prefs_are_refused_with_the_reason(out):
    with pytest.raises(OutlineError, match="density"):
        build_deck({"slides": [CHART]}, out(), prefs={"density": "extreme"})


# ---------------------------------------------------------------- incremental edits


def test_add_and_update_slides_keep_theme_and_numbering(out):
    path = out()
    build_deck(
        {"slides": [ONE_OF_EACH["title"], ONE_OF_EACH["bullets"]]}, path, theme="dark"
    )
    r = add_slide(path, [CHART, ONE_OF_EACH["table"]])
    assert r["slides"] == 4 and r["changed_slides"] == [3, 4]
    r = add_slide(path, ONE_OF_EACH["section"], position=2)
    assert r["changed_slides"] == [2]
    r = update_slide(path, 4, ONE_OF_EACH["kpi"])
    prs = Presentation(path)
    titles = [s.shapes.title.text for s in prs.slides]
    assert titles == [
        "Harbor Logistics annual review",
        "Results",
        "Volume grew 8% while costs held flat",
        "On-time delivery hit a record 97%",
        "Two depots missed their target",
    ]
    bg = prs.slide_master.background.fill.fore_color.rgb
    assert (
        str(bg) == THEMES["dark"]["colors"]["background"]
    ), "the deck kept its own theme"
    nums = [
        shapes_named(s, "Slide number")[0].text_frame.text for s in list(prs.slides)[1:]
    ]
    assert nums == ["2", "3", "4", "5"]
    with pytest.raises(OutlineError, match="outside"):
        update_slide(path, 9, CHART)
    from slidedeck.verify import verify_deck

    assert verify_deck(path, expected_slides=5, render=False)["status"] == "pass"
