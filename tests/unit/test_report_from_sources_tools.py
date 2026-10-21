# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The report-from-sources skill's tools.py: manifest contract, registration, path rules."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("openpyxl")

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.skills.format import parse_skill_file  # noqa: E402
from gaia.skills.loader import (  # noqa: E402
    register_skill_tools,
    unregister_skill_tools,
)

SKILL_DIR = (
    Path(__file__).resolve().parents[2] / "hub" / "skills" / "report-from-sources"
)
TOOLS = ("inventory_data_files", "series_from_workbook", "chart_into_workbook")


@pytest.fixture()
def tools():
    skill = parse_skill_file(SKILL_DIR)
    before = dict(_TOOL_REGISTRY)
    registered = register_skill_tools(skill)
    try:
        yield {
            name.split("/", 1)[1]: entry["function"]
            for name, entry in registered.items()
        }
    finally:
        unregister_skill_tools(skill.name)
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)


def test_manifest_matches_tools_py_and_registers_namespaced():
    skill = parse_skill_file(SKILL_DIR)
    assert tuple(skill.tool_names) == TOOLS
    before = dict(_TOOL_REGISTRY)
    try:
        registered = register_skill_tools(skill)
        assert sorted(registered) == sorted(f"report-from-sources/{t}" for t in TOOLS)
        for t in TOOLS:
            assert t not in _TOOL_REGISTRY, "skill tools must only appear namespaced"
    finally:
        unregister_skill_tools(skill.name)
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)


def test_skill_body_leads_with_the_tools():
    skill = parse_skill_file(SKILL_DIR)
    body = skill.body
    first_section = body.split("## 2.")[0]
    assert "Call `inventory_data_files(folder)` first" in first_section
    assert "series_from_workbook" in body and "chart_into_workbook" in body
    assert "Fallback when the tool can't parse" in first_section


def _book(path: Path) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Item", "2025Q4", "2026Q1", "2026Q2"])
    ws.append(["Revenue", 1.5, 2.5, 3.5])
    wb.save(path)
    return path


def test_relative_paths_are_refused(tools):
    out = tools["series_from_workbook"]("data.xlsx", ["Revenue"])
    assert out["status"] == "error" and "absolute" in out["error"]


def test_paths_outside_gaia_allowed_paths_are_refused(tools, tmp_path, monkeypatch):
    inside, outside = tmp_path / "in", tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    src = _book(outside / "book.xlsx")
    monkeypatch.setenv("GAIA_ALLOWED_PATHS", str(inside))
    assert (
        "GAIA_ALLOWED_PATHS"
        in tools["series_from_workbook"](str(src), ["Revenue"])["error"]
    )
    assert "GAIA_ALLOWED_PATHS" in tools["inventory_data_files"](str(outside))["error"]
    ok = _book(inside / "book.xlsx")
    assert tools["series_from_workbook"](str(ok), ["Revenue"])["status"] == "success"
    refused = tools["chart_into_workbook"](
        str(outside / "c.xlsx"), str(ok), ["Revenue"]
    )
    assert refused["status"] == "error" and not (outside / "c.xlsx").exists()


def test_inventory_skips_secret_files(tools, tmp_path):
    _book(tmp_path / "book.xlsx")
    (tmp_path / "credentials.json").write_text('{"data": [{"k": 1}]}', encoding="utf-8")
    out = tools["inventory_data_files"](str(tmp_path))
    assert [f["file"] for f in out["data_files"]] == ["book.xlsx"]
    assert any(n["file"] == "credentials.json" for n in out["not_read"])


def test_chart_tool_never_writes_the_source(tools, tmp_path):
    src = _book(tmp_path / "book.xlsx")
    before = src.read_bytes()
    out = tools["chart_into_workbook"](str(src), str(src), ["Revenue"])
    assert out["status"] == "error" and src.read_bytes() == before
    out = tools["chart_into_workbook"](
        str(tmp_path / "chart.csv"), str(src), ["Revenue"]
    )
    assert out["status"] == "error"


def test_chart_tool_round_trip_cites_the_source(tools, tmp_path):
    src = _book(tmp_path / "book.xlsx")
    out = tools["chart_into_workbook"](
        str(tmp_path / "chart.xlsx"),
        str(src),
        ["revenue"],
        periods="last 2",
        title="Revenue",
    )
    assert out["status"] == "success" and out["verified"]
    assert out["read_back"] == {"Revenue": [2.5, 3.5]}
    assert out["source"]["series"][0]["cell_range"] == "C2:D2"
    assert out["source"]["provenance"].startswith("Source: book.xlsx")


def test_lookup_errors_come_back_as_structured_results(tools, tmp_path):
    src = _book(tmp_path / "book.xlsx")
    out = tools["series_from_workbook"](str(src), ["Headcount"])
    assert out["status"] == "error" and out["problems"][0]["status"] == "not_found"
    out = tools["series_from_workbook"](str(src), ["Revenue"], periods="sometime")
    assert out["status"] == "error" and "last 5" in out["error"]
    out = tools["series_from_workbook"](str(tmp_path / "missing.xlsx"), ["Revenue"])
    assert "does not exist" in out["error"]
    # A string instead of a list still works: semicolons separate labels.
    out = tools["series_from_workbook"](str(src), "Revenue")
    assert out["status"] == "success"
