# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Charts from rows and files, what a deck is expected to hold, slide numbers, and
process diagrams with labelled rows.

Every deck is built through the skill's own tool functions inside a bound
call and read back with python-pptx. All content here is invented.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pptx import Presentation

from gaia.skills import parse_skill_file
from gaia.skills.context import bind
from gaia.skills.loader import register_skill_tools, unregister_skill_tools

from .conftest import SKILL_DIR


class _Host:
    _incognito = False


@pytest.fixture
def fns(monkeypatch, tmp_path):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "userhome"))
    skill = parse_skill_file(SKILL_DIR)
    before = dict(_TOOL_REGISTRY)
    registered = register_skill_tools(skill)
    try:
        with bind(_Host(), skill):
            yield {k.split("/", 1)[1]: v["function"] for k, v in registered.items()}
    finally:
        unregister_skill_tools(skill.name)
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)


def _charts(path) -> list:
    return [
        shape.chart
        for slide in Presentation(str(path)).slides
        for shape in slide.shapes
        if getattr(shape, "has_chart", False) and shape.has_chart
    ]


def _texts(slide) -> dict:
    return {
        s.name: s.text_frame.text
        for s in slide.shapes
        if getattr(s, "has_text_frame", False) and s.has_text_frame
    }


COVER = {"type": "title", "title": "Harbour ferry service review"}
TEXT_ONLY = {
    "type": "bullets",
    "title": "Ridership recovered on every route this year",
    "bullets": ["More sailings at the weekend", "A new pier opened in spring"],
}
ROWS = [
    ["Quarter", "North route", "South route"],
    ["Q1", 41.5, 38.2],
    ["Q2", 44.0, 39.1],
    ["Q3", 52.3, 40.4],
]


# ----------------------------------------------------------------------
# Charts from rows and from files
# ----------------------------------------------------------------------


def test_a_deck_with_charts_from_rows_holds_native_charts_with_those_values(
    fns, tmp_path
):
    deck = tmp_path / "ferries.pptx"
    built = fns["build_deck"](
        outline={
            "slides": [
                COVER,
                {
                    "type": "chart",
                    "chart_type": "line",
                    "title": "The north route grew fastest through the year",
                    "rows": ROWS,
                    "y_title": "Passengers (thousands)",
                    "source": "ticket system, 2026",
                },
                {
                    "type": "chart",
                    "chart_type": "column",
                    "title": "Weekend sailings carry most of the growth",
                    "rows": [
                        {"Day": "Weekday", "Passengers": 61.0},
                        {"Day": "Weekend", "Passengers": 76.8},
                    ],
                    "source": "ticket system, 2026",
                },
            ]
        },
        output_path=str(deck),
        expect={"min_charts": 2, "slides": 3},
    )
    assert built["status"] == "ok", built
    assert built["verify"]["counts"] == {"slides": 3, "charts": 2, "tables": 0}
    assert all(e["ok"] for e in built["verify"]["expect"])
    first, second = _charts(deck)
    assert [s.name for s in first.plots[0].series] == ["North route", "South route"]
    assert list(first.plots[0].categories) == ["Q1", "Q2", "Q3"]
    assert list(first.plots[0].series[0].values) == [41.5, 44.0, 52.3]
    assert list(second.plots[0].series[0].values) == [61.0, 76.8]


def test_chart_rows_are_read_from_a_workbook_range_and_a_csv_file(fns, tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Routes"
    ws["A1"] = "A note above the table"
    for r, row in enumerate(ROWS, 3):
        for c, value in enumerate(row, 2):
            ws.cell(row=r, column=c, value=value)
    book = tmp_path / "routes.xlsx"
    wb.save(book)
    csv_file = tmp_path / "days.csv"
    csv_file.write_text("Day,Passengers\nWeekday,61\nWeekend,76.8\n", encoding="utf-8")

    deck = tmp_path / "from_files.pptx"
    built = fns["build_deck"](
        outline={
            "slides": [
                {
                    "type": "chart",
                    "title": "The north route grew fastest through the year",
                    "data_path": str(book),
                    "sheet": "Routes",
                    "range": "B3:D6",
                },
                {
                    "title": "Weekend sailings carry most of the growth",
                    "csv_path": str(csv_file),
                },
            ]
        },
        output_path=str(deck),
        expect={"min_charts": 2},
    )
    assert built["status"] == "ok", built
    first, second = _charts(deck)
    assert list(first.plots[0].categories) == ["Q1", "Q2", "Q3"]
    assert list(first.plots[0].series[1].values) == [38.2, 39.1, 40.4]
    assert list(second.plots[0].series[0].values) == [61.0, 76.8]
    # The file is named as the source when the caller gave none.
    text = "\n".join(
        t for s in Presentation(str(deck)).slides for t in _texts(s).values()
    )
    assert "routes.xlsx" in text and "B3:D6" in text


def test_a_markdown_chart_block_reads_its_rows_from_a_file(fns, tmp_path):
    csv_file = tmp_path / "routes.csv"
    csv_file.write_text(
        "\n".join(",".join(str(c) for c in row) for row in ROWS), encoding="utf-8"
    )
    md = (
        "## The north route grew fastest through the year\n"
        "```chart line\n"
        f"data: {csv_file.as_posix()}\n"
        "y_title: Passengers (thousands)\n"
        "```\n"
    )
    deck = tmp_path / "md.pptx"
    built = fns["build_deck_from_markdown"](
        markdown=md, output_path=str(deck), expect={"charts": 1}
    )
    assert built["status"] == "ok", built
    (chart,) = _charts(deck)
    assert list(chart.plots[0].series[0].values) == [41.5, 44.0, 52.3]


def test_series_in_rows_turns_a_wide_sheet_into_one_series_per_row(fns, tmp_path):
    csv_file = tmp_path / "wide.csv"
    csv_file.write_text(
        "Route,Q1,Q2,Q3\nNorth,41.5,44,52.3\nSouth,38.2,39.1,40.4\n", encoding="utf-8"
    )
    deck = tmp_path / "wide.pptx"
    built = fns["build_deck"](
        outline={
            "slides": [
                {
                    "type": "chart",
                    "title": "The north route grew fastest through the year",
                    "data_path": str(csv_file),
                    "series_in": "rows",
                }
            ]
        },
        output_path=str(deck),
    )
    assert built["status"] == "ok", built
    (chart,) = _charts(deck)
    assert [s.name for s in chart.plots[0].series] == ["North", "South"]
    assert list(chart.plots[0].categories) == ["Q1", "Q2", "Q3"]


@pytest.mark.parametrize(
    "fields, says",
    [
        ({"data_path": "{dir}/missing.csv"}, "does not exist"),
        ({"data_path": "{dir}/routes.csv", "range": "nonsense"}, "not a cell range"),
        ({"data_path": "{dir}/routes.csv", "range": "A40:B50"}, "header row"),
        ({"data_path": "{dir}/notes.docx"}, "read from .csv and .xlsx"),
    ],
)
def test_a_file_or_range_that_gives_no_rows_is_said_with_what_to_pass(
    fns, tmp_path, fields, says
):
    (tmp_path / "routes.csv").write_text("Day,Passengers\nWeekday,61\n", "utf-8")
    (tmp_path / "notes.docx").write_bytes(b"not a table")
    slide = {
        "type": "chart",
        "title": "Weekend sailings carry most of the growth",
        **{k: v.format(dir=tmp_path.as_posix()) for k, v in fields.items()},
    }
    deck = tmp_path / "bad.pptx"
    built = fns["build_deck"](
        outline={"slides": [slide]},
        output_path=str(deck),
        expect={"min_charts": 1},
    )
    # The deck is still built, as text, and nothing reports a chart.
    assert built["status"] == "built_with_issues", built
    assert not _charts(deck)
    said = " ".join(built["warnings"])
    assert says in said and "'rows'" in said
    assert built["verify"]["counts"]["charts"] == 0


def test_too_much_data_for_a_slide_asks_for_a_range(fns, tmp_path):
    big = tmp_path / "big.csv"
    big.write_text(
        "Day,Value\n" + "\n".join(f"d{i},{i}" for i in range(400)), encoding="utf-8"
    )
    built = fns["build_deck"](
        outline={
            "slides": [
                {"type": "chart", "title": "Daily values", "data_path": str(big)}
            ]
        },
        output_path=str(tmp_path / "big.pptx"),
    )
    said = " ".join(built["warnings"])
    assert "400 data rows" in said and "range=" in said


# ----------------------------------------------------------------------
# What the deck is expected to hold
# ----------------------------------------------------------------------


def test_a_deck_short_of_the_charts_asked_for_fails_with_the_call_that_adds_one(
    fns, tmp_path
):
    deck = tmp_path / "no_charts.pptx"
    numbers = {
        "type": "bullets",
        "title": "Ridership rose on both routes in every quarter",
        "bullets": [
            "North route 41.5, 44.0 and 52.3 thousand",
            "South route 38.2, 39.1 and 40.4 thousand",
        ],
    }
    built = fns["build_deck"](
        outline={"slides": [COVER, numbers, TEXT_ONLY]},
        output_path=str(deck),
        expect={"min_charts": 1},
    )
    assert built["status"] == "built_with_issues"
    (issue,) = [i for i in built["verify"]["issues"] if i["check"] == "expected_charts"]
    assert issue["severity"] == "error"
    assert "0 native charts" in issue["detail"] and "at least 1" in issue["detail"]
    # The fix names the slide that holds the numbers and a call that runs as written.
    assert "index=2" in issue["fix"] and '"type": "chart"' in issue["fix"]
    assert '"rows"' in issue["fix"] and "data_path" in issue["fix"]
    assert "slides_built" in built["next"]

    report = fns["verify_deck"](path=str(deck), render=False, expect={"min_charts": 1})
    assert report["status"] == "fail"
    assert report["expect"] == [
        {"expect": "min_charts", "wanted": 1, "found": 0, "ok": False}
    ]

    fixed = fns["update_slide"](
        deck_path=str(deck),
        index=2,
        slide={"type": "chart", "title": numbers["title"], "rows": ROWS, "source": "x"},
    )
    assert fixed["status"] == "ok", fixed
    report = fns["verify_deck"](path=str(deck), render=False, expect={"min_charts": 1})
    assert not [i for i in report["issues"] if i["check"] == "expected_charts"]
    assert report["counts"]["charts"] == 1


def test_tables_and_the_slide_count_are_expected_the_same_way(fns, tmp_path):
    deck = tmp_path / "t.pptx"
    built = fns["build_deck"](
        outline={"slides": [COVER, TEXT_ONLY]},
        output_path=str(deck),
        expect={"tables": 1, "slides": 4},
    )
    checks = {i["check"] for i in built["verify"]["issues"]}
    assert {"expected_tables", "slide_count"} <= checks
    table_fix = next(
        i["fix"] for i in built["verify"]["issues"] if i["check"] == "expected_tables"
    )
    assert '"type": "table"' in table_fix and "add_slide" in table_fix


def test_an_expectation_that_cannot_be_checked_is_said_not_skipped(fns, tmp_path):
    deck = tmp_path / "e.pptx"
    built = fns["build_deck"](
        outline={"slides": [COVER, TEXT_ONLY]},
        output_path=str(deck),
        expect={"diagrams": 2, "min_charts": "many"},
    )
    said = [
        i["detail"]
        for i in built["verify"]["issues"]
        if i["check"] == "expect_not_checked"
    ]
    assert len(said) == 2
    assert all(
        "min_charts, min_tables, slides" in d or "whole number" in d for d in said
    )


def test_numbers_written_as_text_are_a_warning_with_the_chart_call(fns, tmp_path):
    deck = tmp_path / "numbers.pptx"
    built = fns["build_deck"](
        outline={
            "slides": [
                COVER,
                {
                    "type": "bullets",
                    "title": "Ridership rose on both routes in every quarter",
                    "bullets": [
                        "North route 41.5, 44.0 and 52.3 thousand",
                        "South route 38.2, 39.1 and 40.4 thousand",
                    ],
                },
                TEXT_ONLY,
            ]
        },
        output_path=str(deck),
    )
    found = [i for i in built["verify"]["issues"] if i["check"] == "numbers_as_text"]
    assert [i["slide"] for i in found] == [2]
    assert found[0]["severity"] == "warning"
    assert f"update_slide(deck_path=" in found[0]["fix"]
    assert "index=2" in found[0]["fix"] and '"type": "chart"' in found[0]["fix"]
    assert "Ridership rose on both routes" in found[0]["fix"]
    # A warning, not a failure: the deck may be right as it is.
    assert built["verify"]["status"] == "pass"
    assert built["verify"]["style_score"] < 100


def test_a_table_pasted_as_text_comes_back_as_the_rows_of_the_chart_call(fns, tmp_path):
    deck = tmp_path / "pasted.pptx"
    fns["build_deck"](
        outline={
            "slides": [
                {
                    "type": "bullets",
                    "title": "Ridership by quarter",
                    "bullets": [
                        "Quarter | North | South",
                        "Q1 | 41.5 | 38.2",
                        "Q2 | 44.0 | 39.1",
                    ],
                }
            ]
        },
        output_path=str(deck),
    )
    report = fns["verify_deck"](path=str(deck), render=False)
    (issue,) = [i for i in report["issues"] if i["check"] == "numbers_as_text"]
    assert "table of values is written as lines of text" in issue["detail"]
    assert '["Q1", 41.5, 38.2]' in issue["fix"]


def test_text_without_numbers_and_slides_with_a_chart_are_not_flagged(fns, tmp_path):
    deck = tmp_path / "plain.pptx"
    built = fns["build_deck"](
        outline={
            "slides": [
                COVER,
                TEXT_ONLY,
                {
                    "type": "bullets",
                    "title": "The 2026 plan has three parts",
                    "bullets": [
                        "1. Open the pier in 2026",
                        "2. Add sailings by 2027",
                        "Revenue up 12% on last year",
                    ],
                },
                {
                    "type": "chart",
                    "title": "The north route grew fastest through the year",
                    "rows": ROWS,
                    "bullets": ["41.5 to 52.3 thousand", "South 38.2 to 40.4"],
                    "source": "ticket system, 2026",
                },
            ]
        },
        output_path=str(deck),
    )
    assert "numbers_as_text" not in built["verify"]["summary"]


# ----------------------------------------------------------------------
# Slide numbers
# ----------------------------------------------------------------------


def _deck(fns, tmp_path) -> Path:
    deck = tmp_path / "edit.pptx"
    built = fns["build_deck"](
        outline={
            "slides": [
                COVER,
                TEXT_ONLY,
                {
                    "type": "bullets",
                    "title": "Two piers need repairs before winter",
                    "bullets": ["East pier decking", "West pier lighting"],
                },
            ]
        },
        output_path=str(deck),
    )
    assert built["status"] == "ok", built
    return deck


def _titles(deck) -> list:
    return [s.shapes.title.text_frame.text for s in Presentation(str(deck)).slides]


def test_index_one_does_not_overwrite_the_cover_with_a_content_slide(fns, tmp_path):
    deck = _deck(fns, tmp_path)
    before = _titles(deck)
    refused = fns["update_slide"](
        deck_path=str(deck),
        index=1,
        slide={
            "type": "bullets",
            "title": "Ridership recovered faster than planned",
            "bullets": ["Weekend sailings", "The new pier"],
        },
    )
    assert refused["status"] == "error"
    said = " ".join(refused["problems"])
    assert "slide 1 is the cover" in said and "Harbour ferry service review" in said
    assert "numbered from 1" in said and "replace_cover=True" in said
    # The slide that index=2 names is spelled out, so the next call is right.
    assert "2 ('Ridership recovered on every route this year')" in said
    assert _titles(deck) == before


def test_a_replacement_names_the_slide_that_was_there(fns, tmp_path):
    deck = _deck(fns, tmp_path)
    done = fns["update_slide"](
        deck_path=str(deck),
        index=2,
        slide={
            "type": "bullets",
            "title": "Ridership recovered faster than planned",
            "bullets": ["Weekend sailings", "The new pier"],
        },
    )
    assert done["status"] == "ok", done
    assert done["replaced"] == {
        "index": 2,
        "title": "Ridership recovered on every route this year",
        "kind": "diagram",  # two short bullets are drawn as cards
        "with": "Ridership recovered faster than planned",
    }
    assert done["slide_titles"][0].startswith("1. Harbour ferry service review [title")
    assert done["slide_titles"][1].startswith("2. Ridership recovered faster")
    assert "Slide 2" in done["next"] and "from 1" in done["next"]
    assert _titles(deck)[0] == "Harbour ferry service review"


def test_the_cover_is_replaced_by_a_cover_or_when_asked(fns, tmp_path):
    deck = _deck(fns, tmp_path)
    new_cover = fns["update_slide"](
        deck_path=str(deck),
        index=1,
        slide={"type": "title", "title": "Harbour ferries: autumn review"},
    )
    assert new_cover["status"] == "ok", new_cover
    asked = fns["update_slide"](
        deck_path=str(deck),
        index=1,
        slide={"type": "bullets", "title": "Summary of the review", "bullets": ["A"]},
        replace_cover=True,
    )
    assert asked["status"] == "ok", asked
    assert asked["replaced"]["kind"] == "title"
    assert _titles(deck)[0] == "Summary of the review"


def test_a_number_outside_the_deck_lists_the_slides(fns, tmp_path):
    deck = _deck(fns, tmp_path)
    for call in (
        lambda: fns["update_slide"](deck_path=str(deck), index=0, slide=TEXT_ONLY),
        lambda: fns["update_slide"](deck_path=str(deck), index=9, slide=TEXT_ONLY),
        lambda: fns["add_slide"](deck_path=str(deck), slide=TEXT_ONLY, position=9),
    ):
        out = call()
        assert out["status"] == "error"
        said = " ".join(out["problems"])
        assert "numbered from 1" in said and "the cover counts as slide 1" in said
        assert "3. Two piers need repairs before winter" in said


def test_added_slides_come_back_with_every_slide_numbered(fns, tmp_path):
    deck = _deck(fns, tmp_path)
    added = fns["add_slide"](
        deck_path=str(deck),
        slide={"type": "section", "title": "Repairs"},
        position=2,
    )
    assert added["changed_slides"] == [2]
    assert [t.split(" [")[0] for t in added["slide_titles"]] == [
        "1. Harbour ferry service review",
        "2. Repairs",
        "3. Ridership recovered on every route this year",
        "4. Two piers need repairs before winter",
    ]


def test_the_tool_descriptions_state_the_numbering_and_the_chart_data(fns):
    update = inspect.getdoc(fns["update_slide"])
    assert "numbered from 1" in update and "the cover is slide 1" in update
    assert "replace_cover" in update
    assert "position=2 puts it right after the cover" in inspect.getdoc(
        fns["add_slide"]
    )
    for name in ("build_deck", "build_deck_from_markdown"):
        doc = inspect.getdoc(fns[name])
        assert "expect" in doc and "min_charts" in doc
        assert "data" in doc and "range" in doc
    # Diagram rows and the full field list live in the references, off the schema budget.
    assert "references/outline-json.md" in inspect.getdoc(fns["build_deck"])
    assert "references/markdown-format.md" in inspect.getdoc(
        fns["build_deck_from_markdown"]
    )
    assert '"rows"' in inspect.getdoc(fns["build_deck"])
    assert "```chart" in inspect.getdoc(fns["build_deck_from_markdown"])
    assert "expect" in inspect.getdoc(fns["verify_deck"])


# ----------------------------------------------------------------------
# Process diagrams keep what each step was given
# ----------------------------------------------------------------------

STEPS = ["Collect", "Sort", "Pack", "Ship"]


def test_a_process_with_labelled_rows_draws_one_cell_per_step(fns, tmp_path):
    deck = tmp_path / "process.pptx"
    built = fns["build_deck"](
        outline={
            "slides": [
                {
                    "type": "diagram",
                    "diagram": "process",
                    "title": "Each parcel passes four stations before it leaves",
                    "items": [
                        {
                            "title": "Collect",
                            "text": "From the counter",
                            "bullets": ["Weigh", "Label"],
                        },
                        {"title": "Sort", "text": "By district\nBy size\nBy priority"},
                        {"title": "Pack", "bullets": ["Crate", "Seal"]},
                        ["Ship", "Load the van", "Scan out"],
                    ],
                    "dimensions": [
                        {"label": "Staff", "values": ["2", "4", "3", "1"]},
                        {"label": "Minutes", "values": [5, 12, 8, 3]},
                        "Handling cost",
                        "Tracking detail",
                    ],
                    "axis": "Time since drop-off",
                }
            ]
        },
        output_path=str(deck),
        expected_slides=1,
    )
    assert built["status"] == "ok", built
    assert built["verify"]["status"] == "pass", built["verify"]["issues"]
    slide = Presentation(str(deck)).slides[0]
    texts = _texts(slide)
    # Every step keeps each of its own lines, as separate paragraphs.
    assert texts["Step 1 detail"].split("\n") == ["From the counter", "Weigh", "Label"]
    assert texts["Step 2 detail"].split("\n") == [
        "By district",
        "By size",
        "By priority",
    ]
    assert texts["Step 4 detail"].split("\n") == ["Load the van", "Scan out"]
    # Each valued dimension is a labelled row with a cell under every step.
    assert texts["Dimension 1 label"] == "Staff"
    assert [texts[f"Dimension 1 step {k}"] for k in range(1, 5)] == ["2", "4", "3", "1"]
    assert [texts[f"Dimension 2 step {k}"] for k in range(1, 5)] == [
        "5",
        "12",
        "8",
        "3",
    ]
    # A bare label is its own arrow along the steps, and the axis is kept too.
    assert texts["Dimension 3 arrow"] == "Handling cost"
    assert texts["Dimension 4 arrow"] == "Tracking detail"
    assert texts["Axis arrow"] == "Time since drop-off"
    shapes = {s.name: s for s in slide.shapes}
    prs = Presentation(str(deck))
    for k in range(1, 5):
        cell, head = shapes[f"Dimension 1 step {k}"], shapes[f"Step {k}"]
        assert cell.left == head.left, "each cell sits under its step"
    for s in slide.shapes:
        assert s.left >= 0 and s.top >= 0
        assert s.left + s.width <= prs.slide_width
        assert s.top + s.height <= prs.slide_height
    rows = [
        shapes[f"Dimension {r} {'step 1' if r < 3 else 'arrow'}"] for r in range(1, 5)
    ]
    assert all(a.top + a.height <= b.top for a, b in zip(rows, rows[1:]))


def test_markdown_dimension_lines_and_several_axis_lines_are_drawn_as_rows(
    fns, tmp_path
):
    md = (
        "## Each parcel passes four stations before it leaves\n"
        "Diagram: process\n"
        "Dimension: Staff | 2 | 4 | 3 | 1\n"
        "Axis: Handling cost\n"
        "Axis: Tracking detail\n"
        "### Collect\n- Weigh\n- Label\n"
        "### Sort\n- By district\n"
        "### Pack\n- Crate\n"
        "### Ship\n- Scan out\n"
    )
    deck = tmp_path / "md_process.pptx"
    built = fns["build_deck_from_markdown"](markdown=md, output_path=str(deck))
    assert built["status"] == "ok", built
    texts = _texts(Presentation(str(deck)).slides[0])
    assert [texts[f"Dimension 1 step {k}"] for k in range(1, 5)] == ["2", "4", "3", "1"]
    assert texts["Dimension 2 arrow"] == "Handling cost"
    assert texts["Dimension 3 arrow"] == "Tracking detail"
    assert texts["Step 1 detail"].split("\n") == ["Weigh", "Label"]


def test_fields_a_step_carries_are_drawn_not_dropped(fns, tmp_path):
    deck = tmp_path / "extra.pptx"
    built = fns["build_deck"](
        outline={
            "slides": [
                {
                    "type": "diagram",
                    "diagram": "process",
                    "title": "Each parcel passes four stations before it leaves",
                    "items": [
                        {"title": t, "staff": n, "risk_level": r}
                        for t, n, r in zip(
                            STEPS, (2, 4, 3, 1), ("Low", "Low", "Medium", "High")
                        )
                    ]
                    + [],
                }
            ]
        },
        output_path=str(deck),
    )
    assert built["status"] == "ok", built
    texts = _texts(Presentation(str(deck)).slides[0])
    assert texts["Dimension 1 label"] == "Staff"
    assert texts["Dimension 2 label"] == "Risk level"
    assert [texts[f"Dimension 2 step {k}"] for k in range(1, 5)] == [
        "Low",
        "Low",
        "Medium",
        "High",
    ]
    assert any("drawn as rows under the steps" in n for n in built["notes"])


def test_a_slide_sent_without_a_type_is_drawn_from_its_fields_not_as_a_cover(
    fns, tmp_path
):
    deck = tmp_path / "one.pptx"
    fns["build_deck"](outline={"slides": [TEXT_ONLY]}, output_path=str(deck))
    done = fns["update_slide"](
        deck_path=str(deck),
        index=1,
        slide={
            "layout": "diagram_process",
            "title": "Each parcel passes four stations before it leaves",
            "stages": [{"title": t, "detail": f"{t} the parcel"} for t in STEPS],
        },
    )
    assert done["status"] == "ok", done
    texts = _texts(Presentation(str(deck)).slides[0])
    assert (
        texts["Step 3"].endswith("Pack") and texts["Step 3 detail"] == "Pack the parcel"
    )
    assert not [w for w in done["warnings"] if "tagline" in w]


def test_content_that_is_not_drawn_is_an_issue_and_costs_the_score(fns, tmp_path):
    deck = tmp_path / "dropped.pptx"
    slide = {
        "type": "diagram",
        "diagram": "cycle",
        "title": "Each parcel passes four stations before it leaves",
        "items": STEPS,
    }
    clean = fns["build_deck"](outline={"slides": [slide]}, output_path=str(deck))
    assert clean["status"] == "ok" and clean["verify"]["style_score"] == 100
    built = fns["build_deck"](
        outline={
            "slides": [
                {
                    **slide,
                    "dimensions": [{"label": "Staff", "values": [2, 4, 3, 1]}],
                    "handover_rules": ["Collect is staffed all day", "Ship runs twice"],
                }
            ]
        },
        output_path=str(tmp_path / "dropped2.pptx"),
    )
    assert built["status"] == "built_with_issues"
    (issue,) = [i for i in built["verify"]["issues"] if i["check"] == "content_dropped"]
    assert "'handover_rules'" in issue["detail"] and "'dimensions'" in issue["detail"]
    assert "update_slide" in issue["fix"]
    assert built["verify"]["style_score"] < 100
    assert any("process" in w and "'Staff'" in w for w in built["warnings"])

    edited = fns["update_slide"](
        deck_path=str(deck), index=1, slide={**slide, "handover_rules": ["A", "B"]}
    )
    assert edited["status"] == "built_with_issues"
    assert edited["issues"][0]["check"] == "content_dropped"
    assert "not on the slide" in edited["next"]


def test_more_steps_than_one_row_holds_says_the_rows_were_left_out(fns, tmp_path):
    built = fns["build_deck"](
        outline={
            "slides": [
                {
                    "type": "diagram",
                    "diagram": "process",
                    "title": "Each parcel passes eight stations before it leaves",
                    "items": [f"Station {k}" for k in range(1, 9)],
                    "dimensions": [{"label": "Staff", "values": list(range(8))}],
                }
            ]
        },
        output_path=str(tmp_path / "eight.pptx"),
    )
    assert built["status"] == "built_with_issues"
    assert "content_dropped" in built["verify"]["summary"]
    assert built["verify"]["style_score"] < 100


def test_several_things_folded_into_one_line_under_a_process_get_the_row_syntax(
    fns, tmp_path
):
    md = (
        "## Each parcel passes four stations before it leaves\n"
        "Diagram: process\n"
        "Rising along the way: handling cost, tracking detail, staff time, paperwork\n"
        "### Collect\n- Weigh\n### Sort\n- By district\n### Pack\n- Crate\n"
    )
    built = fns["build_deck_from_markdown"](
        markdown=md, output_path=str(tmp_path / "folded.pptx")
    )
    (hint,) = [w for w in built["warnings"] if "in one line of text" in w]
    assert "lists 4 things" in hint and '"dimensions"' in hint
    assert "Dimension: <label>" in hint


class _Asking:
    """A stand-in agent in a chat turn: the user's request, and nothing read yet."""

    _incognito = False

    def __init__(self, request: str) -> None:
        self._original_user_input = request

    def seen_sources(self):
        return [("user", {}, self._original_user_input)]


def test_rows_read_from_the_users_file_are_not_called_unsourced(tmp_path, monkeypatch):
    from gaia.agents.base.tools import _TOOL_REGISTRY

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    csv_file = tmp_path / "routes.csv"
    csv_file.write_text(
        "\n".join(",".join(str(c) for c in row) for row in ROWS), encoding="utf-8"
    )
    skill = parse_skill_file(SKILL_DIR)
    before = dict(_TOOL_REGISTRY)
    registered = register_skill_tools(skill)
    try:
        build = registered["slide-decks/build_deck"]["function"]
        slide = {"type": "chart", "title": "The north route grew fastest"}
        with bind(_Asking("One chart slide from routes.csv."), skill):
            from_file = build(
                outline={"slides": [{**slide, "data_path": str(csv_file)}]},
                output_path=str(tmp_path / "file.pptx"),
            )
            typed = build(
                outline={"slides": [{**slide, "rows": ROWS, "source": "routes.csv"}]},
                output_path=str(tmp_path / "typed.pptx"),
            )
    finally:
        unregister_skill_tools(skill.name)
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)
    assert from_file["status"] == "ok", from_file
    assert not [w for w in from_file["warnings"] if "in nothing the user said" in w]
    # The same values typed into the call are still checked against the sources.
    assert [w for w in typed["warnings"] if "in nothing the user said" in w]
