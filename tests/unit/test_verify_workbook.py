# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""verify_workbook finds the formula mistakes openpyxl never computes.

A local model built a workbook whose SUMIFS criteria walked across columns, a
SUM covered a text column and a variance pointed at the wrong cell; nothing
recalculated the file, so the agent reported it as done.
"""

import hashlib
import re
import zipfile
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from gaia.agents.registry import KNOWN_TOOLS  # noqa: E402
from gaia.agents.tools import workbook_check  # noqa: E402
from gaia.agents.tools.office_tools import OfficeToolsMixin  # noqa: E402
from gaia.agents.tools.workbook_check import (  # noqa: E402
    _criterion_matches,
    check_workbook,
    find_soffice,
)
from gaia.security import PathValidator  # noqa: E402


def _static(path: Path) -> dict:
    return check_workbook(path, recalc=False)


def _problems(result: dict, bucket: str) -> dict:
    return {e["cell"]: e for e in result[bucket]}


@pytest.fixture
def data_book():
    """A data sheet plus an empty summary sheet to put formulas on."""
    wb = openpyxl.Workbook()
    data = wb.active
    data.title = "Data"
    data.append(["Region", "Quarter", "Sales", "Cost"])
    for row in [("East", "Q1", 10, 4), ("West", "Q1", 20, 8), ("East", "Q2", 15, 5)]:
        data.append(row)
    summary = wb.create_sheet("Summary")
    summary.append(["Region", "Sales"])
    summary.append(["East"])
    return wb


def _save(wb, tmp_path, name="book.xlsx") -> Path:
    path = tmp_path / name
    wb.save(path)
    return path


def test_correct_formulas_pass(data_book, tmp_path):
    ws = data_book["Summary"]
    ws["B2"] = "=SUMIFS(Data!C:C,Data!A:A,A2)"
    ws["B3"] = "=SUM(Data!C2:C4)"
    ws["B4"] = "=B2/B3"
    result = _static(_save(data_book, tmp_path))

    assert result["error_count"] == 0, result["errors"]
    assert result["warning_count"] == 0, result["warnings"]
    assert result["formula_count"] == 3


def test_sumifs_criteria_on_the_wrong_column_is_flagged(data_book, tmp_path):
    data_book["Summary"]["B2"] = "=SUMIFS(Data!D:D,Data!B:B,A2)"
    result = _static(_save(data_book, tmp_path))

    warning = _problems(result, "warnings")["Summary!B2"]
    assert "matches no cell in Data!B:B" in warning["problem"]


def test_sum_over_a_text_column_is_an_error(data_book, tmp_path):
    data_book["Summary"]["B2"] = "=SUM(Data!A2:A4)"
    result = _static(_save(data_book, tmp_path))

    assert "finds only text" in _problems(result, "errors")["Summary!B2"]["problem"]


def test_sum_over_empty_cells_is_an_error(data_book, tmp_path):
    data_book["Summary"]["B2"] = "=SUM(Data!F2:F4)"
    result = _static(_save(data_book, tmp_path))

    assert "only empty cells" in _problems(result, "errors")["Summary!B2"]["problem"]


def test_a_header_above_the_numbers_is_not_a_text_warning(data_book, tmp_path):
    data_book["Summary"]["B2"] = "=SUM(Data!C:C)"
    result = _static(_save(data_book, tmp_path))

    assert result["warning_count"] == 0 and result["error_count"] == 0


def test_numbers_stored_as_text_among_numbers_warn(data_book, tmp_path):
    data_book["Data"]["C5"] = "1,200"
    data_book["Data"]["C6"] = 7
    data_book["Summary"]["B2"] = "=SUM(Data!C2:C6)"
    result = _static(_save(data_book, tmp_path))

    assert "skips 1 text cell" in _problems(result, "warnings")["Summary!B2"]["problem"]


def test_arithmetic_on_an_empty_cell_warns(data_book, tmp_path):
    data_book["Summary"]["B2"] = "=Data!C2-Data!C9"
    result = _static(_save(data_book, tmp_path))

    assert (
        "Data!C9, which is empty"
        in _problems(result, "warnings")["Summary!B2"]["problem"]
    )


@pytest.mark.parametrize(
    "formula,code",
    [
        ("=Data!C2/Data!F2", "#DIV/0!"),
        ("=Data!A2*2", "#VALUE!"),
        ("=Missing!A1", "#REF!"),
        ("=#REF!+1", "#REF!"),
        ("=Bogus*2", "#NAME?"),
        ("=FOOBAR(1)", "#NAME?"),
        ("=STDEV.S(Data!C2:C4)", "#NAME?"),
        ('=SUMIFS(Data!C2:C4,Data!A2:A3,"East")', "#VALUE!"),
    ],
)
def test_static_errors_name_the_excel_error(data_book, tmp_path, formula, code):
    data_book["Summary"]["B2"] = formula
    result = _static(_save(data_book, tmp_path))

    assert _problems(result, "errors")["Summary!B2"]["error"] == code


def test_prefixed_newer_functions_are_accepted(data_book, tmp_path):
    data_book["Summary"]["B2"] = "=_xlfn.STDEV.S(Data!C2:C4)"
    result = _static(_save(data_book, tmp_path))

    assert result["error_count"] == 0


def test_defined_names_resolve(data_book, tmp_path):
    from openpyxl.workbook.defined_name import DefinedName

    data_book.defined_names["Sales"] = DefinedName("Sales", attr_text="Data!$C$2:$C$4")
    data_book["Summary"]["B2"] = "=SUM(Sales)"
    result = _static(_save(data_book, tmp_path))

    assert result["error_count"] == 0, result["errors"]


def test_circular_references_are_reported_with_their_cells(data_book, tmp_path):
    ws = data_book["Summary"]
    ws["B2"] = "=B3+1"
    ws["B3"] = "=B2+1"
    ws["C2"] = "=SUM(C1:C3)"
    result = _static(_save(data_book, tmp_path))

    cycles = sorted(sorted(c) for c in result["circular_references"])
    assert cycles == [["Summary!B2", "Summary!B3"], ["Summary!C2"]]
    assert result["error_count"] == 2


@pytest.mark.parametrize(
    "criterion,value,expected",
    [
        ("East", "east", True),
        ("East", "West", False),
        (">=10", 12, True),
        (">=10", 9, False),
        ("<>East", "West", True),
        ("E*", "East", True),
        (10.0, 10, True),
        (10.0, "10", True),
    ],
)
def test_criterion_matching_follows_excel(criterion, value, expected):
    assert _criterion_matches(criterion, value) is expected


def test_without_libreoffice_static_checks_still_run_and_say_so(
    data_book, tmp_path, monkeypatch
):
    monkeypatch.setattr(workbook_check, "find_soffice", lambda: None)
    data_book["Summary"]["B2"] = "=SUM(Data!A2:A4)"
    result = check_workbook(_save(data_book, tmp_path))

    assert result["recalculated"] is False
    assert "LibreOffice is not installed" in result["recalc_note"]
    assert result["error_count"] == 1


def test_a_failed_recalculation_is_reported_not_hidden(
    data_book, tmp_path, monkeypatch
):
    monkeypatch.setattr(workbook_check, "find_soffice", lambda: "soffice")

    def broken(*_a, **_k):
        raise RuntimeError("LibreOffice exited with code 1")

    monkeypatch.setattr(workbook_check, "recalculate", broken)
    data_book["Summary"]["B2"] = "=SUM(Data!C2:C4)"
    result = check_workbook(_save(data_book, tmp_path))

    assert result["recalculated"] is False
    assert "exited with code 1" in result["recalc_error"]


def test_cached_values_from_excel_are_used_when_not_recalculated(tmp_path, monkeypatch):
    """A file Excel saved carries computed values; they are read, not ignored."""
    monkeypatch.setattr(workbook_check, "find_soffice", lambda: None)
    wb = openpyxl.Workbook()
    wb.active["A1"] = 2
    wb.active["A2"] = "=A1*2"
    path = _save(wb, tmp_path)
    monkeypatch.setattr(
        workbook_check,
        "_computed_values",
        lambda _p: {"Sheet": {(1, 1): 2, (2, 1): "#DIV/0!"}},
    )
    result = check_workbook(path)

    assert "cached values" in result["values_source"]
    assert _problems(result, "errors")["Sheet!A2"]["error"] == "#DIV/0!"


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_libreoffice_recalculation_computes_values_and_leaves_the_file_alone(
    data_book, tmp_path
):
    ws = data_book["Summary"]
    ws["B2"] = "=SUMIFS(Data!C:C,Data!A:A,A2)"
    ws["B3"] = "=IF(ISNUMBER(A2),A2*2,0)"
    path = _save(data_book, tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = check_workbook(path)

    assert result["recalculated"] is True
    assert result["computed_values"]["Summary!B2"] == 25
    # The static #VALUE! guess is overruled by the computed value.
    assert "Summary!B3" not in _problems(result, "errors")
    assert "Summary!B3" in _problems(result, "warnings")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def _with_cached_values(path: Path, cached: dict) -> Path:
    """A copy of *path* whose formula cells carry stale cached values.

    This is what an Excel-saved file looks like after openpyxl edits a cell a
    formula depends on: the old result stays in each formula's ``<v>``.
    """
    stale = path.with_name("stale.xlsx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(stale, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                xml = data.decode("utf-8")
                for cell, value in cached.items():
                    xml, n = re.subn(
                        rf'(<c r="{cell}"[^>]*>\s*<f>[^<]*</f>)(\s*<v>[^<]*</v>|\s*<v\s*/>)?',
                        rf"\g<1><v>{value}</v>",
                        xml,
                    )
                    assert n == 1, (cell, xml)
                data = xml.encode("utf-8")
            zout.writestr(item, data)
    return stale


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_libreoffice_recomputes_values_an_earlier_save_cached(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = 5
    ws["B1"] = "=A1*2"
    ws["A2"] = 0
    ws["B2"] = "=1/A2"
    path = _with_cached_values(_save(wb, tmp_path), {"B1": 999, "B2": 5})

    result = check_workbook(path)

    assert result["recalculated"] is True
    assert result["computed_values"]["Sheet!B1"] == 10
    assert _problems(result, "errors")["Sheet!B2"]["error"] == "#DIV/0!"


# ── the tool ────────────────────────────────────────────────────────────────


class _Host(OfficeToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator


@pytest.fixture
def verify(tmp_path, monkeypatch):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.setattr(workbook_check, "find_soffice", lambda: None)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    validator = PathValidator(allowed_paths=[str(allowed)])
    validator.set_access_prompt(lambda _p: False)
    _Host(validator).register_office_tools()
    yield _TOOL_REGISTRY["verify_workbook"]["function"], allowed
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def test_tool_checks_a_workbook_in_scope(verify, data_book):
    tool, allowed = verify
    data_book["Summary"]["B2"] = "=SUM(Data!A2:A4)"
    result = tool(file_path=str(_save(data_book, allowed)))

    assert result["status"] == "success"
    assert result["error_count"] == 1


def test_tool_refuses_a_workbook_outside_scope(verify, data_book, tmp_path):
    tool, _ = verify
    outside = tmp_path / "outside"
    outside.mkdir()
    result = tool(file_path=str(_save(data_book, outside)))

    assert result["status"] == "error"
    assert "not in allowed paths" in result["error"]


def test_tool_rejects_other_formats_and_broken_files(verify):
    tool, allowed = verify
    (allowed / "notes.csv").write_text("a,b\n", encoding="utf-8")
    (allowed / "broken.xlsx").write_bytes(b"not a zip")

    assert ".xlsx and .xlsm" in tool(file_path=str(allowed / "notes.csv"))["error"]
    assert "Could not open" in tool(file_path=str(allowed / "broken.xlsx"))["error"]
    assert tool(file_path=str(allowed / "missing.xlsx"))["status"] == "error"


def test_mixin_is_advertised_by_name():
    assert KNOWN_TOOLS["office"] == (
        "gaia.agents.tools.office_tools",
        "OfficeToolsMixin",
    )
