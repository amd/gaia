# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The native .xlsx chart writer behind the chart_into_workbook tool.

Every chart is reopened from the saved file and its values compared with the
input; these tests also prove the comparison catches a chart that drifted.
"""

from __future__ import annotations

import zipfile

import pytest

pytest.importorskip("openpyxl")

from tests.fixtures.report_from_sources_data import (  # noqa: E402
    library_call,
    skill_lib,
)

X = skill_lib("xlsx_chart")

CATS = ["2025Q3", "2025Q4", "2026Q1", "2026Q2"]
SERIES = {"Net revenue": [10.5, 11.25, None, 12.0], "Costs": [7, 8, 9, 10]}


@pytest.fixture(autouse=True)
def _in_a_skill_call():
    # The writer opens and saves through gaia.skills.context.
    with library_call():
        yield


@pytest.mark.parametrize(
    "chart_type", ["line", "column", "bar", "stacked_column", "stacked_bar", "area"]
)
def test_each_chart_type_round_trips(tmp_path, chart_type):
    out = tmp_path / f"{chart_type}.xlsx"
    result = X.write_workbook_chart(
        out,
        CATS,
        SERIES,
        chart_type=chart_type,
        title="Revenue and costs",
        y_axis_title="$ millions",
        source_note="Source: book.xlsx, 2025Q3-2026Q2",
    )
    assert result["verified"] and result["read_back"] == {
        k: list(v) for k, v in SERIES.items()
    }
    from openpyxl import load_workbook

    ws = load_workbook(out)[result["sheet"]]
    assert ws["A1"].value == "Revenue and costs"
    assert any(
        c.value == "Source: book.xlsx, 2025Q3-2026Q2"
        for row in ws.iter_rows()
        for c in row
    )
    assert ws.cell(row=4, column=2).number_format == "#,##0.00"


def test_combo_puts_secondary_series_on_a_line(tmp_path):
    out = tmp_path / "combo.xlsx"
    result = X.write_workbook_chart(
        out, CATS, SERIES, chart_type="combo", secondary=["Costs"]
    )
    assert result["verified"]
    with zipfile.ZipFile(out) as zf:
        xml = zf.read("xl/charts/chart1.xml").decode()
    assert "<c:barChart>" in xml.replace(" ", "") or "barChart" in xml
    assert "lineChart" in xml


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"chart_type": "pie3d"}, "not one of"),
        ({"chart_type": "combo"}, "secondary_labels"),
        (
            {"chart_type": "combo", "secondary": ["Net revenue", "Costs"]},
            "some \\(not all\\)",
        ),
    ],
)
def test_bad_requests(tmp_path, kwargs, message):
    with pytest.raises(X.ChartWriteError, match=message):
        X.write_workbook_chart(tmp_path / "x.xlsx", CATS, SERIES, **kwargs)


def test_misaligned_or_empty_series(tmp_path):
    with pytest.raises(X.ChartWriteError, match="4 periods"):
        X.write_workbook_chart(tmp_path / "x.xlsx", CATS, {"A": [1, 2]})
    with pytest.raises(X.ChartWriteError, match="no values"):
        X.write_workbook_chart(tmp_path / "x.xlsx", CATS, {"A": [None] * 4})


def test_adds_a_sheet_to_an_existing_workbook_and_keeps_its_chart(tmp_path):
    out = tmp_path / "book.xlsx"
    first = X.write_workbook_chart(out, CATS, SERIES, title="First")
    second = X.write_workbook_chart(
        out, CATS, {"Costs": SERIES["Costs"]}, title="First"
    )
    assert second["sheet"] != first["sheet"] and second["verified"]
    with zipfile.ZipFile(out) as zf:
        assert sum(n.startswith("xl/charts/chart") for n in zf.namelist()) == 2


def test_refuses_workbooks_openpyxl_would_damage(tmp_path):
    out = tmp_path / "pivot.xlsx"
    X.write_workbook_chart(out, CATS, SERIES)
    with zipfile.ZipFile(out, "a") as zf:
        zf.writestr("xl/pivotTables/pivotTable1.xml", "<pivotTableDefinition/>")
    before = out.read_bytes()
    with pytest.raises(X.ChartWriteError, match="openpyxl\\s+would drop"):
        X.write_workbook_chart(out, CATS, SERIES)
    assert out.read_bytes() == before


def test_verification_catches_a_changed_value(tmp_path):
    out = tmp_path / "v.xlsx"
    result = X.write_workbook_chart(out, CATS, SERIES)
    from openpyxl import load_workbook

    wb = load_workbook(out)
    wb[result["sheet"]].cell(row=2, column=2, value=99)
    wb.save(out)
    check = X.verify_workbook_chart(out, result["sheet"], CATS, SERIES)
    assert not check["verified"] and "Net revenue" in check["mismatches"][0]


def test_before_save_runs_once_the_chart_is_valid(tmp_path):
    """The undo copy is taken just before the save, never for a refused chart."""
    seen = []
    with pytest.raises(X.ChartWriteError):
        X.write_workbook_chart(
            tmp_path / "x.xlsx",
            CATS,
            SERIES,
            chart_type="pie3d",
            before_save=seen.append,
        )
    assert not seen and not (tmp_path / "x.xlsx").exists()
    X.write_workbook_chart(tmp_path / "x.xlsx", CATS, SERIES, before_save=seen.append)
    assert seen == [tmp_path / "x.xlsx"]
