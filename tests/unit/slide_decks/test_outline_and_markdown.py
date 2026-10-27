# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Outline validation (every problem at once, nothing guessed) and Markdown input."""

from __future__ import annotations

import pytest
from slidedeck.markdown import outline_from_markdown
from slidedeck.outline import OutlineError, normalize_outline


def norm(*slides, **deck):
    return normalize_outline({"slides": list(slides), **deck})


def test_accepts_json_text_and_a_bare_list():
    assert (
        len(
            normalize_outline('{"slides": [{"type": "title", "title": "A"}]}')["slides"]
        )
        == 1
    )
    assert (
        normalize_outline([{"type": "title", "title": "A"}])["slides"][0]["type"]
        == "title"
    )


def test_every_problem_is_reported_together():
    with pytest.raises(OutlineError) as e:
        norm(
            {"type": "wizard", "title": "x"},
            {
                "type": "chart",
                "title": "c",
                "categories": ["a", "b"],
                "series": {"S": [1, "lots"]},
            },
            {"type": "kpi", "title": "k"},
        )
    probs = e.value.problems
    assert len(probs) >= 3
    assert (
        any("wizard" in p for p in probs)
        and any("'lots' is not a number" in p for p in probs)
        and any("metrics" in p for p in probs)
    )


def test_malformed_json_names_the_position():
    with pytest.raises(OutlineError, match="line 1"):
        normalize_outline('{"slides": [')


def test_non_json_text_points_at_markdown_tool():
    with pytest.raises(OutlineError, match="build_deck_from_markdown"):
        normalize_outline("# My deck")


def test_expected_slide_count_must_match():
    with pytest.raises(OutlineError, match="expected_slides"):
        norm({"type": "title", "title": "a"}, expected_slides=3)


def test_aliases_map_to_canonical_types():
    s = norm(
        {"type": "metrics", "title": "t", "metrics": [{"label": "a", "value": 1}]},
        {"type": "2x2", "title": "m", "items": ["a", "b", "c", "d"]},
        {"type": "roadmap", "title": "r", "milestones": [{"date": "Q1", "title": "x"}]},
    )["slides"]
    assert [x["type"] for x in s] == ["kpi", "diagram", "timeline"]
    assert s[1]["layout"] == "matrix"


# ---------------------------------------------------------------- bullets


def test_bullets_nest_by_list_dict_and_indent():
    s = norm(
        {
            "type": "bullets",
            "title": "t",
            "bullets": [
                "a",
                ["a1", ["a1x"]],
                {"text": "b", "sub": ["b1"]},
                "  - c-indented",
            ],
        }
    )["slides"][0]
    assert [(t, lvl) for t, lvl, _b in s["paras"]] == [
        ("a", 0),
        ("a1", 1),
        ("a1x", 2),
        ("b", 0),
        ("b1", 1),
        ("c-indented", 1),
    ]


# ---------------------------------------------------------------- chart data shapes


def chart(**kw):
    return norm({"type": "chart", "title": "t", **kw})["slides"][0]["chart"]


def test_chart_data_tool_shape_is_accepted_as_is():
    c = chart(
        data={
            "categories": ["Q1", "Q2"],
            "series": {"Revenue": [1.5, 2.0], "Cost": [1.0, 1.1]},
            "source": "f.xlsx, 2026",
        }
    )
    assert c["categories"] == ["Q1", "Q2"] and [s["name"] for s in c["series"]] == [
        "Revenue",
        "Cost",
    ]
    assert c["source"] == "f.xlsx, 2026"


def test_plain_values_list_needs_categories():
    with pytest.raises(OutlineError, match="categories"):
        chart(values=[1, 2, 3])
    c = chart(values=[1, 2, 3], categories=["a", "b", "c"], series_name="Units")
    assert c["series"][0]["name"] == "Units" and c["series"][0]["values"] == [1, 2, 3]


def test_csv_text_and_rows_with_header():
    c = chart(csv="Month,Sales,Returns\nJan,10,1\nFeb,12,2\n")
    assert c["categories"] == ["Jan", "Feb"] and c["series"][1]["values"] == [1.0, 2.0]
    c = chart(rows=[["Region", "Units"], ["N", "1,200"], ["S", "900"]])
    assert c["series"][0]["values"] == [1200.0, 900.0]
    c = chart(rows=[{"Region": "N", "Units": 5}, {"Region": "S", "Units": 7}])
    assert c["categories"] == ["N", "S"]


def test_csv_with_semicolons_and_crlf():
    c = chart(csv="Week;Visits\r\nW1;100\r\nW2;150\r\n")
    assert c["series"][0]["values"] == [100.0, 150.0]


def test_csv_path_is_refused_with_the_route_to_take():
    with pytest.raises(OutlineError, match="categories"):
        chart(csv_path="C:/data/sales.csv")


def test_length_mismatch_and_negative_pie_are_errors():
    with pytest.raises(OutlineError, match="one value per category"):
        chart(categories=["a", "b", "c"], series={"S": [1, 2]})
    with pytest.raises(OutlineError, match="negative"):
        chart(chart_type="pie", categories=["a", "b"], series={"S": [3, -1]})
    with pytest.raises(OutlineError, match="one series"):
        chart(chart_type="pie", categories=["a"], series={"S": [1], "T": [2]})


def test_scatter_points_in_several_shapes():
    c = chart(
        chart_type="scatter",
        series=[
            {"name": "A", "points": [[1, 2], [3, 4]]},
            {"name": "B", "x": [1, 2], "y": [5, 6]},
        ],
    )
    assert c["series"][0]["points"] == [(1.0, 2.0), (3.0, 4.0)] and c["series"][1][
        "values"
    ] == [5.0, 6.0]


def test_auto_chart_type_and_ranking_sort():
    c = chart(
        chart_type="auto",
        categories=[f"2026-{m:02d}" for m in range(1, 13)],
        series={"S": list(range(12))},
    )
    assert c["chart_type"] == "line" and c["chart_reason"]
    c = chart(
        chart_type="bar",
        intent="ranking",
        categories=["a", "b", "c"],
        series={"S": [2, 9, 5]},
    )
    # Bars draw bottom-up, so the largest comes last in the data.
    assert c["categories"] == ["a", "c", "b"] and c["series"][0]["values"] == [2, 5, 9]


def test_missing_values_are_gaps_not_zeros():
    c = chart(categories=["a", "b", "c"], series={"S": [1, None, ""]})
    assert c["series"][0]["values"] == [1.0, None, None]


# ---------------------------------------------------------------- other types


def test_table_formats_by_column_name_and_unknown_column_is_an_error():
    t = norm(
        {
            "type": "table",
            "title": "t",
            "columns": ["A", "B"],
            "rows": [[1, 2]],
            "formats": {"B": "0.0"},
        }
    )["slides"][0]["table"]
    assert t["formats"] == {1: "0.0"}
    with pytest.raises(OutlineError, match="unknown column"):
        norm(
            {
                "type": "table",
                "title": "t",
                "columns": ["A"],
                "rows": [[1]],
                "formats": {"Z": "0"},
            }
        )


def test_flow_edges_must_name_items():
    with pytest.raises(OutlineError, match="'nope'"):
        norm(
            {
                "type": "diagram",
                "diagram": "flow",
                "title": "f",
                "items": [{"id": "a", "title": "A"}],
                "edges": [["a", "nope"]],
            }
        )


def test_roadmap_bars_must_use_known_periods():
    with pytest.raises(OutlineError, match="Q9"):
        norm(
            {
                "type": "timeline",
                "title": "r",
                "periods": ["Q1", "Q2"],
                "lanes": [
                    {"name": "L", "items": [{"label": "x", "start": "Q1", "end": "Q9"}]}
                ],
            }
        )


def test_image_must_exist(tmp_path):
    with pytest.raises(OutlineError, match="does not exist"):
        norm({"type": "image", "title": "i", "path": str(tmp_path / "missing.png")})


# ---------------------------------------------------------------- markdown


MD = """# Field Operations Review
Quarter ending September 2026
Prepared by the operations team

## Agenda
- Results
- Risks

## Uptime improved to 99.4% after the network upgrade
- Outages fell from 9 to 3
  - Two of the three were planned
- Mean repair time 41 minutes
Source: incidents.csv, Jul-Sep 2026
Notes:
Mention the upgrade vendor.

## Two sites carry most of the backlog
| Site | Open tickets |
|---|---|
| North | 1,240 |
| South | 310 |
Source: tickets.xlsx, 30 Sep 2026

## Option A is cheaper; option B is faster
### Option A
- $1.2M
### Option B
- Ships in April

## Customers noticed the change
> Calls drop less than they did — Survey respondent

## Orders rose every month
```slide
{"type": "chart", "chart_type": "line", "categories": ["Jul", "Aug", "Sep"], "series": {"Orders": [100, 120, 140]}, "source": "orders.csv"}
```

## Appendix
"""


def test_markdown_becomes_an_outline_with_every_slide_type():
    slides = outline_from_markdown(MD)["slides"]
    types = [s["type"] for s in slides]
    assert types == [
        "title",
        "agenda",
        "bullets",
        "table",
        "two_column",
        "quote",
        "chart",
        "section",
    ]
    cover, _agenda, bullets, table, two, quote, ch, _sec = slides
    assert cover["subtitle"] == "Quarter ending September 2026"
    assert (
        bullets["source"] == "incidents.csv, Jul-Sep 2026"
        and "vendor" in bullets["notes"]
    )
    assert bullets["bullets"][1] == {
        "text": "Two of the three were planned",
        "level": 1,
    }
    assert table["rows"][0] == ["North", 1240]
    assert two["left"]["heading"] == "Option A"
    assert quote["attribution"] == "Survey respondent"
    assert ch["series"] == {"Orders": [100, 120, 140]}
    normalize_outline({"slides": slides})  # and it validates


def test_markdown_with_crlf_and_front_matter():
    md = "---\ntitle: x\n---\r\n## Only slide\r\n- point one\r\n"
    slides = outline_from_markdown(md)["slides"]
    assert slides == [
        {
            "title": "Only slide",
            "type": "bullets",
            "bullets": [{"text": "point one", "level": 0}],
        }
    ]


def test_empty_markdown_is_an_error():
    with pytest.raises(OutlineError):
        outline_from_markdown("   \n")


def test_bad_slide_json_block_is_named():
    with pytest.raises(OutlineError, match="not valid JSON"):
        outline_from_markdown("## A\n```slide\n{oops}\n```\n")


STAGES_MD = """## Ferry service grows from one route to a network
Diagram: process
### One route
Harbour to island, hourly.
- Commuters
- Day trips
### Two routes
- Adds the north pier
### Network
- Four piers, shared tickets
Note: Left to right: more boats, crews and piers.
"""


def test_headed_sections_with_a_diagram_line_become_a_process_diagram(out):
    from slidedeck.build import build_deck

    outline = outline_from_markdown(STAGES_MD)
    (slide,) = outline["slides"]
    assert slide["type"] == "diagram" and slide["diagram"] == "process"
    assert [i["title"] for i in slide["items"]] == [
        "One route",
        "Two routes",
        "Network",
    ]
    assert slide["items"][0]["text"] == "Harbour to island, hourly."
    assert [b["text"] for b in slide["items"][0]["bullets"]] == [
        "Commuters",
        "Day trips",
    ]
    assert slide["footnote"].startswith("Left to right")
    assert build_deck(outline, out())["slides"] == 1


def test_three_headed_sections_are_cards_not_a_wall_of_bold_lines():
    md = "## Three pilots ran this term\n### North\n- 40 pupils\n### South\n- 32 pupils\n### East\n- 28 pupils\n"
    (slide,) = outline_from_markdown(md)["slides"]
    assert slide["type"] == "diagram" and slide["diagram"] == "boxes"
    assert len(slide["items"]) == 3


def test_top_level_bullets_with_a_diagram_line_are_the_steps():
    md = "## Orders move in three steps\nDiagram: process\n- Pick\n  - Scan the shelf tag\n- Pack\n- Ship\n"
    (slide,) = outline_from_markdown(md)["slides"]
    assert [i["title"] for i in slide["items"]] == ["Pick", "Pack", "Ship"]
    assert slide["items"][0]["bullets"] == [{"text": "Scan the shelf tag", "level": 0}]


def test_an_axis_line_names_what_grows_along_a_process():
    md = "## Orders move in three steps\nDiagram: process\nAxis: More handling cost\n- Pick\n- Pack\n- Ship\n"
    (slide,) = outline_from_markdown(md)["slides"]
    assert slide["x_axis"] == "More handling cost"


def test_a_one_slide_request_gets_the_slide_without_a_cover():
    md = "# Ferry plan\nOctober 2026\n\n## Ferry service grows to a network\n- Four piers\n"
    outline = outline_from_markdown(md, expected_slides=1)
    assert [s["type"] for s in outline["slides"]] == ["bullets"]
    assert outline["expected_slides"] == 1
    two = outline_from_markdown(md, expected_slides=2)
    assert [s["type"] for s in two["slides"]] == ["title", "bullets"]


def test_latex_arrows_from_the_model_become_symbols_on_the_slide():
    from slidedeck.outline import normalize_outline

    out = normalize_outline(
        {
            "slides": [
                {
                    "type": "bullets",
                    "title": r"Assist $\rightarrow$ Autonomy",
                    "bullets": [
                        r"Concept $\to$ draft",
                        r"$\Downarrow \text{ Increasing Complexity}$",
                        "Costs $5 and $10, 50\\% growth",
                    ],
                }
            ]
        }
    )
    slide = out["slides"][0]
    assert slide["title"] == "Assist → Autonomy"
    assert [p[0] for p in slide["paras"]] == [
        "Concept → draft",
        "⇓ Increasing Complexity",
        "Costs $5 and $10, 50% growth",
    ]


def test_latex_in_a_process_axis_becomes_symbols():
    from slidedeck.markdown import outline_from_markdown
    from slidedeck.outline import normalize_outline

    md = (
        "## Stages\nDiagram: process\n### A\n- one\n### B\n- two\n### C\n- three\n"
        "Axis: Complexity $\\rightarrow$ Compute\n"
    )
    slide = normalize_outline(outline_from_markdown(md))["slides"][0]
    assert "Complexity → Compute" in str(slide)
    assert "rightarrow" not in str({k: v for k, v in slide.items() if k != "raw"})
