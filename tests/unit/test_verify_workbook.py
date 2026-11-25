# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""verify_workbook finds the formula mistakes openpyxl never computes, and
verify_document tells a real .docx or .pptx from text saved under that name.

A local model built a workbook whose SUMIFS criteria walked across columns, a
SUM covered a text column and a variance pointed at the wrong cell; nothing
recalculated the file, so the agent reported it as done. Another saved plain
text as a .docx.
"""

import gc
import hashlib
import importlib.util
import inspect
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.security import PathValidator
from gaia.skills import parse_skill_file
from gaia.skills.context import SkillContextError
from gaia.skills.manager import SkillManager

openpyxl = pytest.importorskip("openpyxl")

SKILL_DIR = Path(__file__).resolve().parents[2] / "hub" / "skills" / "office-docs"


def _load(name: str, filename: str):
    """Import one of the skill's modules from its folder, as the loader does."""
    spec = importlib.util.spec_from_file_location(name, SKILL_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SKILL_DIR))
    try:
        sys.modules[name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SKILL_DIR))
    return module


# Registered under the names tools.py imports them by, so both see one module.
workbook_check = _load("workbook_check", "workbook_check.py")
document_check = _load("document_check", "document_check.py")
office_tools = _load("office_docs_tools_under_test", "tools.py")
_criterion_matches = workbook_check._criterion_matches
check_workbook = workbook_check.check_workbook
soffice_installed = workbook_check.soffice_installed
needs_libreoffice = pytest.mark.skipif(
    not soffice_installed(), reason="LibreOffice not installed"
)


def _open(path: Path):
    return open(path, "rb")


def _static(path: Path) -> dict:
    return check_workbook(path, opener=_open)


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


def test_sum_over_empty_input_cells_is_a_warning(tmp_path):
    """A template's entry cells are empty until the user fills them in."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item", "Amount"])
    ws.append(["Rent"])
    ws.append(["Food"])
    ws.append(["Total", "=SUM(B2:B3)"])
    result = _static(_save(wb, tmp_path))

    assert result["error_count"] == 0, result["errors"]
    assert result["status"] == "success" and result["ok"] is True
    warning = _problems(result, "warnings")["Sheet!B4"]
    assert "only empty cells" in warning["problem"]
    assert "cells the user will fill in" in warning["problem"]


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
    assert result["status"] == "errors_found"
    assert result["ok"] is False


@pytest.mark.parametrize(
    "formula,code",
    [
        ("=Data!C2/Data!F2", "#DIV/0!"),
        ("=Data!A2*2", "#VALUE!"),
        ("=Data!G2+30", "#VALUE!"),  # G2 is the text "2024-03-01"
        ("=Data!G3*100", "#VALUE!"),  # G3 is the text "5%"
    ],
)
def test_value_dependent_findings_are_warnings_without_a_recalculation(
    data_book, tmp_path, formula, code
):
    """Excel reads "5%" and a date string as numbers, and an empty divisor may
    be an input: only a computed value can call these errors."""
    data_book["Data"]["G2"] = "2024-03-01"
    data_book["Data"]["G3"] = "5%"
    data_book["Summary"]["B2"] = formula
    result = _static(_save(data_book, tmp_path))

    assert result["error_count"] == 0, result["errors"]
    warning = _problems(result, "warnings")["Summary!B2"]
    assert warning["possible_error"] == code
    assert "not recalculated" in warning["problem"]
    assert result["status"] == "success"


def _fake_recalculation(monkeypatch, values: dict):
    """A recalculator whose copy holds *values*, as ``{sheet: {(row, col): v}}``."""
    copy = Path("recalculated.xlsx")
    real = workbook_check._computed_values
    monkeypatch.setattr(
        workbook_check,
        "_computed_values",
        lambda source: values if source is copy else real(source),
    )
    return lambda _path: copy


def test_a_computed_error_confirms_a_value_dependent_finding(
    data_book, tmp_path, monkeypatch
):
    ws = data_book["Summary"]
    ws["B2"] = "=Data!C2/Data!F2"
    ws["B3"] = "=Data!A2*2"
    recalculator = _fake_recalculation(
        monkeypatch, {"Summary": {(2, 2): "#DIV/0!", (3, 2): 7}}
    )
    result = check_workbook(
        _save(data_book, tmp_path), opener=_open, recalculator=recalculator
    )

    error = _problems(result, "errors")["Summary!B2"]
    assert error["error"] == "#DIV/0!" and error["computed"] == "#DIV/0!"
    warning = _problems(result, "warnings")["Summary!B3"]
    assert "computed value is 7" in warning["problem"]
    assert "possible_error" not in warning
    assert result["status"] == "errors_found"
    assert "next" not in result


@pytest.mark.parametrize(
    "formula",
    [
        "=IFERROR(A6/Data!F2,0)",
        '=IF(Data!F2="","",Data!C2/Data!F2)',
        "=IF(ISNUMBER(Data!A2),Data!A2*2,0)",
        "=_xlfn.IFNA(Data!A2*2,0)",
    ],
)
def test_arithmetic_inside_an_error_guard_is_not_flagged(data_book, tmp_path, formula):
    data_book["Summary"]["B2"] = formula
    result = _static(_save(data_book, tmp_path))

    assert result["error_count"] == 0, result["errors"]
    assert result["warning_count"] == 0, result["warnings"]


@pytest.mark.parametrize(
    "formula",
    [
        "=_xlfn.LET(x,Data!C2*2,x+x)",
        "=_xlfn.LET(rate,0.2,net,Data!C2,net*rate)",
        "=_xlfn.LET(_xlpm.x,Data!C2,_xlpm.x+1)",  # how Excel stores it
        "=_xlfn.LAMBDA(a,b,a+b)(Data!C2,Data!C3)",
    ],
)
def test_let_and_lambda_variables_are_not_unknown_names(data_book, tmp_path, formula):
    data_book["Summary"]["B2"] = formula
    result = _static(_save(data_book, tmp_path))

    assert result["error_count"] == 0, result["errors"]


def test_a_table_name_is_a_known_name(data_book, tmp_path):
    from openpyxl.worksheet.table import Table

    data_book["Data"].add_table(Table(displayName="Sales", ref="A1:D4"))
    ws = data_book["Summary"]
    ws["B2"] = "=ROWS(Sales)"
    ws["B3"] = "=ROWS(Purchases)"
    result = _static(_save(data_book, tmp_path))

    errors = _problems(result, "errors")
    assert "Summary!B2" not in errors
    assert errors["Summary!B3"]["error"] == "#NAME?"


def test_a_table_name_means_its_data_rows(data_book, tmp_path):
    """``=SUM(Labels)`` over a table of text is caught: the name resolved."""
    from openpyxl.worksheet.table import Table

    data_book["Data"].add_table(Table(displayName="Labels", ref="A1:B4"))
    data_book["Summary"]["B2"] = "=SUM(Labels)"
    result = _static(_save(data_book, tmp_path))

    problem = _problems(result, "errors")["Summary!B2"]["problem"]
    assert "SUM over Data!A2:B4 finds only text" in problem


def test_a_finding_repeated_in_one_formula_is_reported_once(data_book, tmp_path):
    data_book["Summary"]["B2"] = "=Bogus+Bogus*Bogus"
    result = _static(_save(data_book, tmp_path))

    assert [e["cell"] for e in result["errors"]] == ["Summary!B2"]


def test_a_workbook_of_typed_totals_is_not_a_success(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item", "Amount"])
    ws.append(["Rent", 900])
    ws.append(["Food", 300])
    ws.append(["Total", 1200])
    result = _static(_save(wb, tmp_path))

    assert result["status"] == "no_formulas"
    assert result["ok"] is False
    assert result["summary"] == workbook_check.NO_FORMULAS
    assert "every total here is a typed value" in result["summary"]
    assert "next" not in result


def test_a_workbook_with_no_numbers_has_nothing_to_check(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.append(["Name", "Team"])
    wb.active.append(["Ada", "Platform"])
    result = _static(_save(wb, tmp_path))

    assert result["status"] == "success" and result["ok"] is True
    assert "nothing to check" in result["summary"]


def test_without_computed_values_the_result_says_what_to_do(data_book, tmp_path):
    data_book["Summary"]["B2"] = "=SUM(Data!C2:C4)"
    result = check_workbook(
        _save(data_book, tmp_path),
        opener=_open,
        not_recalculated=workbook_check.NO_LIBREOFFICE,
    )

    assert "computed_values" not in result
    assert result["recalculated"] is False
    assert result["next"].startswith(
        "Formulas were not recalculated (LibreOffice is not installed). Compute "
        "the same figures with pandas in run_python"
    )


def _rows_and_aggregates(rows: int, aggregates: int):
    """A data sheet with a formula on every row and a summary of SUMIFS."""
    wb = openpyxl.Workbook()
    data = wb.active
    data.title = "Data"
    data.append(["Region", "Quantity", "Price", "Amount"])
    for i in range(2, rows + 2):
        data.append([f"R{i % aggregates}", i % 7 + 1, 2.5, f"=B{i}*C{i}"])
    summary = wb.create_sheet("Summary")
    summary.append(["Region", "Amount"])
    for k in range(aggregates):
        summary.append([f"R{k}", f"=SUMIFS(Data!D:D,Data!A:A,A{k + 2})"])
    summary.append(["Total", f"=SUM(B2:B{aggregates + 1})"])
    return wb


def _check_seconds(path: Path) -> float:
    """CPU seconds for one check, without a garbage collection landing in it."""
    gc.collect()
    gc.disable()
    try:
        started = time.process_time()
        result = _static(path)
        elapsed = time.process_time() - started
    finally:
        gc.enable()
    assert result["error_count"] == 0, result["errors"][:3]
    return elapsed


def test_checking_time_grows_linearly_with_the_sheet(tmp_path):
    """Each reference used to scan every formula on its sheet: 20,000 row
    formulas under 50 whole-column aggregates took over a minute.

    Four times the rows must cost about four times as long; that scan made
    it twelve times or more. A ratio of CPU times holds on a loaded runner,
    where a wall-clock limit did not."""
    rows = 1_500
    small = _save(_rows_and_aggregates(rows, 50), tmp_path, "small.xlsx")
    large = _save(_rows_and_aggregates(4 * rows, 50), tmp_path, "large.xlsx")

    small_times, large_times = [], []
    for _ in range(3):
        small_times.append(_check_seconds(small))
        large_times.append(_check_seconds(large))
    ratio = min(large_times) / max(min(small_times), 0.01)

    assert _static(large)["formula_count"] == 4 * rows + 51
    assert ratio < 8, (
        f"4x the rows took {ratio:.1f}x as long ({min(small_times):.2f}s vs "
        f"{min(large_times):.2f}s of CPU): the check no longer scales "
        "linearly with the sheet"
    )


def test_a_formula_under_a_range_is_still_a_dependency(tmp_path):
    """The index must find formulas inside ranges, whole columns and rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "=SUM(B:B)"
    ws["B5"] = "=C5+1"
    ws["C5"] = "=SUM(A1:A3)"
    ws["E1"] = "=SUM(2:2)"
    ws["F2"] = "=E1"
    result = _static(_save(wb, tmp_path))

    cycles = sorted(sorted(c) for c in result["circular_references"])
    assert cycles == [["Sheet!A1", "Sheet!B5", "Sheet!C5"], ["Sheet!E1", "Sheet!F2"]]


def test_computed_values_put_totals_first_and_say_how_many(tmp_path, monkeypatch):
    wb = _rows_and_aggregates(200, 5)
    computed = {
        "Data": {(r, 4): float(r) for r in range(2, 202)},
        "Summary": {(r, 2): 100.0 * r for r in range(2, 8)},
    }
    result = check_workbook(
        _save(wb, tmp_path),
        opener=_open,
        recalculator=_fake_recalculation(monkeypatch, computed),
    )

    values = result["computed_values"]
    assert len(values) == workbook_check.MAX_VALUES
    assert {f"Summary!B{r}" for r in range(2, 8)} <= set(values)
    assert values["Summary!B7"] == 700.0
    assert result["computed_values_note"].startswith("60 of 206 shown")


def test_few_computed_values_are_all_shown(data_book, tmp_path, monkeypatch):
    data_book["Summary"]["B2"] = "=SUM(Data!C2:C4)"
    result = check_workbook(
        _save(data_book, tmp_path),
        opener=_open,
        recalculator=_fake_recalculation(monkeypatch, {"Summary": {(2, 2): 45}}),
    )

    assert result["computed_values"] == {"Summary!B2": 45}
    assert "computed_values_note" not in result


def test_a_workbook_over_the_cell_limit_is_refused(data_book, tmp_path, monkeypatch):
    path = _save(data_book, tmp_path)
    monkeypatch.setattr(workbook_check, "MAX_CELLS", 10)

    with pytest.raises(workbook_check.WorkbookTooLarge) as refused:
        _static(path)

    assert "more than 10 cells" in str(refused.value)
    assert "smaller workbook" in str(refused.value)


def test_a_workbook_over_the_size_limit_is_refused(data_book, tmp_path, monkeypatch):
    path = _save(data_book, tmp_path)
    monkeypatch.setattr(workbook_check, "MAX_FILE_BYTES", 100)

    with pytest.raises(workbook_check.WorkbookTooLarge) as refused:
        _static(path)

    assert "smaller workbook" in str(refused.value)


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


def test_a_failed_recalculation_is_reported_not_hidden(data_book, tmp_path):
    def broken(_path):
        raise RuntimeError("LibreOffice exited with code 1")

    data_book["Summary"]["B2"] = "=SUM(Data!C2:C4)"
    result = check_workbook(
        _save(data_book, tmp_path), opener=_open, recalculator=broken
    )

    assert result["recalculated"] is False
    assert "exited with code 1" in result["recalc_error"]
    assert "the recalculation failed" in result["next"]


def test_cached_values_from_excel_are_used_when_not_recalculated(tmp_path, monkeypatch):
    """A file Excel saved carries computed values; they are read, not ignored."""
    wb = openpyxl.Workbook()
    wb.active["A1"] = 2
    wb.active["A2"] = "=A1*2"
    path = _save(wb, tmp_path)
    monkeypatch.setattr(
        workbook_check,
        "_computed_values",
        lambda _p: {"Sheet": {(1, 1): 2, (2, 1): "#DIV/0!"}},
    )
    result = check_workbook(path, opener=_open)

    assert "cached values" in result["values_source"]
    assert "next" not in result
    assert _problems(result, "errors")["Sheet!A2"]["error"] == "#DIV/0!"


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


@needs_libreoffice
def test_libreoffice_recomputes_values_an_earlier_save_cached(agent, folders):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = 5
    ws["B1"] = "=A1*2"
    ws["A2"] = 0
    ws["B2"] = "=1/A2"
    path = _with_cached_values(_save(wb, folders[0]), {"B1": 999, "B2": 5})

    result = _verify(agent, path)

    assert result["recalculated"] is True, result
    assert result["computed_values"]["Sheet!B1"] == 10
    assert _problems(result, "errors")["Sheet!B2"]["error"] == "#DIV/0!"


# ── the tool, run by an agent ───────────────────────────────────────────────


class _HostAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def folders(tmp_path):
    """``books`` is in the agent's scope, ``elsewhere`` is not."""
    books, elsewhere = tmp_path / "books", tmp_path / "elsewhere"
    books.mkdir()
    elsewhere.mkdir()
    return books, elsewhere


@pytest.fixture
def gaia_home(monkeypatch):
    """A short GAIA_HOME: LibreOffice crashes on Windows when its profile path
    is as deep as pytest's tmp_path makes it."""
    home = Path(tempfile.mkdtemp(prefix="gh-"))
    monkeypatch.setenv("GAIA_HOME", str(home))
    yield home
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def agent(tmp_path, folders, gaia_home):
    """An agent allowed into ``books`` only, with office-docs loaded as bundled."""
    root = tmp_path / "bundled"
    shutil.copytree(SKILL_DIR, root / "office-docs")
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _HostAgent(silent_mode=True, skip_lemonade=True)
    validator = PathValidator(allowed_paths=[str(folders[0])])
    validator.set_access_prompt(lambda path: False)  # the user says no
    built.path_validator = validator
    built._instance_tools = {}
    manager = SkillManager(
        agent_skill_dirs=[root],
        user_skills_root=tmp_path / "no-user-skills",
        include_claude_roots=False,
    )
    built.load_skill("office-docs", manager=manager)
    yield built
    for name in list(built.loaded_skills):
        built.unload_skill(name)


def _verify(agent, path) -> dict:
    return agent._execute_tool("office-docs/verify_workbook", {"file_path": str(path)})


@pytest.fixture
def no_libreoffice(monkeypatch):
    monkeypatch.setattr(workbook_check, "soffice_installed", lambda: False)


def test_tool_checks_a_workbook(agent, folders, data_book, no_libreoffice):
    data_book["Summary"]["B2"] = "=SUM(Data!A2:A4)"
    result = _verify(agent, _save(data_book, folders[0]))

    assert result["status"] == "errors_found"
    assert result["ok"] is False
    assert result["error_count"] == 1
    assert result["recalculated"] is False
    assert "LibreOffice is not installed" in result["next"]
    assert "computed_values" not in result


def test_tool_refuses_a_workbook_outside_scope(agent, folders, data_book):
    path = _save(data_book, folders[1])
    with patch.object(workbook_check, "check_workbook") as check:
        result = _verify(agent, path)

    assert result["status"] == "error"
    assert "not in allowed paths" in result["error"]
    check.assert_not_called()


def test_tool_checks_a_workbook_in_a_folder_approved_this_session(
    agent, folders, data_book, no_libreoffice
):
    agent.path_validator.add_allowed_path(str(folders[1]))
    data_book["Summary"]["B2"] = "=SUM(Data!C2:C4)"

    result = _verify(agent, _save(data_book, folders[1]))

    assert result["status"] == "success"
    assert result["ok"] is True
    assert result["error_count"] == 0


def test_tool_rejects_other_formats_and_broken_files(agent, folders, no_libreoffice):
    books = folders[0]
    (books / "notes.csv").write_text("a,b\n", encoding="utf-8")
    (books / "broken.xlsx").write_bytes(b"not a zip")
    (books / "memo.docx").write_bytes(b"anything")

    assert ".xlsx and .xlsm" in _verify(agent, books / "notes.csv")["error"]
    broken = _verify(agent, books / "broken.xlsx")["error"]
    assert "Could not open" in broken and "Workbook.save" in broken
    assert "verify_document" in _verify(agent, books / "memo.docx")["error"]
    missing = _verify(agent, books / "missing.xlsx")
    assert missing["status"] == "error"
    assert "No file at" in missing["error"]


def test_tool_reports_a_bug_in_the_check_as_a_bug(
    agent, folders, data_book, no_libreoffice
):
    """Only an unreadable file is "rebuild it": a KeyError inside the check
    used to be reported as one, sending the agent to rebuild a good workbook."""
    path = _save(data_book, folders[0])
    with patch.object(workbook_check._Checker, "run", side_effect=KeyError("oops")):
        result = _verify(agent, path)

    # The agent's own handler for a tool that raised: a failure, with a trace.
    assert result["status"] == "error"
    assert "oops" in result["error_brief"]
    assert "Could not open" not in str(result)
    assert "Rebuild" not in str(result)


def test_tool_refuses_a_workbook_over_the_cell_limit(
    agent, folders, data_book, no_libreoffice, monkeypatch
):
    monkeypatch.setattr(workbook_check, "MAX_CELLS", 10)
    result = _verify(agent, _save(data_book, folders[0]))

    assert result["status"] == "error"
    assert "more than 10 cells" in result["error"]
    assert "smaller workbook" in result["error"]


def test_tool_reads_the_workbook_through_the_skill_context(
    agent, folders, data_book, no_libreoffice
):
    """Every read of the user's file goes through ``open_read``."""
    data_book["Summary"]["B2"] = "=SUM(Data!C2:C4)"
    path = _save(data_book, folders[0])
    opened = []
    # The agent loaded its own copy of tools.py; patch the one it calls.
    tool = agent._tools_registry["office-docs/verify_workbook"]["function"]
    tool_globals = inspect.unwrap(tool).__globals__
    real = tool_globals["open_read"]

    def spy(target, mode="r", **kwargs):
        opened.append((Path(target), mode))
        return real(target, mode, **kwargs)

    with (
        patch.dict(tool_globals, {"open_read": spy}),
        patch.object(openpyxl, "load_workbook", wraps=openpyxl.load_workbook) as load,
    ):
        result = _verify(agent, path)

    assert result["status"] == "success"
    assert opened == [(path.resolve(), "rb")] * 2
    assert all(not isinstance(call.args[0], (str, Path)) for call in load.mock_calls)


def test_recalculation_runs_only_inside_the_skill_call(data_book, tmp_path):
    """Outside a tool call there is no agent scope to check the copy against."""
    with pytest.raises(SkillContextError):
        workbook_check.recalculate(_save(data_book, tmp_path))


def test_the_skill_declares_what_the_recalculation_needs():
    from gaia.skills.audit import audit_skill

    skill = SkillManager(
        agent_skill_dirs=[SKILL_DIR.parent], include_claude_roots=False
    ).load("office-docs")
    declared = {str(p) for p in skill.parsed_permissions()}
    assert {"program:run:soffice", "scratch:write"} <= declared
    assert audit_skill(SKILL_DIR).verdict == "ALLOW"


@needs_libreoffice
def test_libreoffice_recalculates_a_copy_in_the_calls_temp_folder(
    agent, folders, data_book, gaia_home
):
    ws = data_book["Summary"]
    ws["B2"] = "=SUMIFS(Data!C:C,Data!A:A,A2)"
    ws["B3"] = "=IF(ISNUMBER(A2),A2*2,0)"
    path = _save(data_book, folders[0])
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = _verify(agent, path)

    assert result["recalculated"] is True, result
    assert result["status"] == "success" and "next" not in result
    assert result["computed_values"]["Summary!B2"] == 25
    # Text behind an ISNUMBER guard is never multiplied: no finding at all.
    assert "Summary!B3" not in _problems(result, "errors")
    assert "Summary!B3" not in _problems(result, "warnings")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    temp_root = gaia_home / "skill-temp" / "office-docs"
    assert not list(temp_root.iterdir()), "the call's temporary folder was kept"


def test_the_skill_registers_its_tools_under_its_namespace(tmp_path):
    """The manifest matches tools.py, so the loader registers the tools."""
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.skills.loader import register_skill_tools, unregister_skill_tools

    before = dict(_TOOL_REGISTRY)
    try:
        registered = register_skill_tools(parse_skill_file(SKILL_DIR))

        assert set(registered) == {
            "office-docs/verify_workbook",
            "office-docs/verify_document",
        }
        for entry in registered.values():
            assert entry["parameters"]["file_path"]["required"] is True
    finally:
        unregister_skill_tools("office-docs")
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)


def test_skill_md_body_stays_lean_with_rules_and_linked_references():
    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split("\n---\n", 2)[-1]
    assert len(body) // 4 <= 1200
    assert "formulas, not hard-coded values" in body
    assert "Verify before handing over" in body
    # The first rule: a model that saves text under a .docx name fails before
    # any other rule matters.
    rules = body.split("## Hard rules", 1)[1].split("## Procedure", 1)[0]
    first = " ".join(rules.strip().split("\n- ")[0].split())
    assert first.startswith("- **Save with the library** (`Workbook.save`")
    assert "Never use write_file to create a .docx, .xlsx or .pptx" in first
    flat = " ".join(body.split())
    assert "office-docs/verify_document(file_path=...)" in flat
    assert "say the workbook's formulas were not recalculated" in flat
    assert "For any slide or deck, load the `slide-decks` skill" in flat
    refs = set(re.findall(r"`(references/[\w-]+\.md)`", body))
    assert refs == {
        "references/excel.md",
        "references/powerpoint.md",
        "references/word.md",
    }
    for ref in sorted(refs):
        text = (SKILL_DIR / ref).read_text(encoding="utf-8")
        for block in re.findall(r"```python\n(.*?)\n```", text, re.S):
            compile(block, ref, "exec")


# ── verify_document ─────────────────────────────────────────────────────────

docx = pytest.importorskip("docx")
pptx = pytest.importorskip("pptx")


def _check_document(agent, path) -> dict:
    return agent._execute_tool("office-docs/verify_document", {"file_path": str(path)})


def _memo(path: Path) -> Path:
    doc = docx.Document()
    doc.add_heading("Office Move Plan", level=1)
    doc.add_paragraph("To: Facilities team | From: Operations")
    doc.add_heading("Details", level=2)
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Item"
    table.rows[1].cells[0].text = "Desks"
    doc.add_heading("Next steps", level=2)
    doc.add_paragraph("Order new access cards", style="List Bullet")
    doc.save(path)
    return path


def _deck(path: Path) -> Path:
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Sign-ups rose each month"
    data = CategoryChartData()
    data.categories = ["Jan", "Feb"]
    data.add_series("Sign-ups", (120, 150))
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(6), Inches(4), data
    )
    prs.slides.add_slide(prs.slide_layouts[6])  # blank: no title
    prs.save(path)
    return path


def test_verify_document_counts_what_a_word_file_holds(agent, folders):
    path = _memo(folders[0] / "memo.docx")
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = _check_document(agent, path)

    assert result["status"] == "success", result
    assert result["kind"] == "word"
    assert result["headings"] == {"1": 1, "2": 2}
    assert result["heading_text"] == ["Office Move Plan", "Details", "Next steps"]
    assert result["tables"] == 1
    assert result["pictures"] == 0
    assert result["paragraphs"] == 7  # two of them inside the table
    assert result["words"] == 18
    assert result["warnings"] == []
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_verify_document_counts_pictures_in_a_word_file(agent, folders):
    import base64

    pixel = folders[0] / "pixel.png"
    pixel.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAC"
            "hwGA60e6kgAAAABJRU5ErkJggg=="
        )
    )
    doc = docx.Document()
    doc.add_heading("Floor plan", level=1)
    doc.add_picture(str(pixel))
    path = folders[0] / "plan.docx"
    doc.save(path)

    assert _check_document(agent, path)["pictures"] == 1


def test_verify_document_counts_what_a_deck_holds(agent, folders):
    result = _check_document(agent, _deck(folders[0] / "deck.pptx"))

    assert result["status"] == "success", result
    assert result["kind"] == "powerpoint"
    assert result["slides"] == 2
    assert result["titles"] == ["Sign-ups rose each month", ""]
    assert result["charts"] == 1
    assert result["pictures"] == 0
    assert result["slides_without_title"] == [2]
    assert result["warnings"] == [
        "Slide(s) with no title: 2. Give every slide a title."
    ]


@pytest.mark.parametrize(
    "name,what,fix",
    [
        ("memo.docx", "is not a Word file", "Document.save"),
        ("deck.pptx", "is not a PowerPoint file", "Presentation.save"),
    ],
)
def test_text_saved_under_an_office_name_is_an_error(agent, folders, name, what, fix):
    path = folders[0] / name
    path.write_text("# Office Move Plan\n\nTo: Facilities team\n", encoding="utf-8")

    result = _check_document(agent, path)

    assert result["status"] == "error"
    assert what in result["error"]
    assert "plain text saved under" in result["error"]
    assert "python-" in result["error"] and fix in result["error"]
    assert "never write" in result["error"] and "write_file" in result["error"]


def test_another_kind_of_office_file_under_a_docx_name_is_an_error(
    agent, folders, data_book
):
    deck = _deck(folders[0] / "deck.pptx")
    as_word = folders[0] / "renamed.docx"
    shutil.copyfile(deck, as_word)
    workbook_as_word = _save(data_book, folders[0], "book.docx")

    assert "it is a PowerPoint file saved under a .docx name" in (
        _check_document(agent, as_word)["error"]
    )
    assert "it is an Excel workbook saved under a .docx name" in (
        _check_document(agent, workbook_as_word)["error"]
    )


def test_verify_document_sends_a_workbook_to_verify_workbook(agent, folders, data_book):
    result = _check_document(agent, _save(data_book, folders[0]))

    assert result["status"] == "error"
    assert "office-docs/verify_workbook" in result["error"]


def test_verify_document_rejects_other_formats_and_missing_files(agent, folders):
    notes = folders[0] / "notes.md"
    notes.write_text("# Notes\n", encoding="utf-8")

    assert ".docx and .pptx" in _check_document(agent, notes)["error"]
    missing = _check_document(agent, folders[0] / "missing.docx")
    assert missing["status"] == "error"
    assert "No file at" in missing["error"]


def test_verify_document_refuses_a_file_outside_scope(agent, folders):
    path = _memo(folders[1] / "memo.docx")
    with patch.object(document_check, "check_document") as check:
        result = _check_document(agent, path)

    assert result["status"] == "error"
    assert "not in allowed paths" in result["error"]
    check.assert_not_called()


def test_verify_document_warns_about_an_empty_or_unstructured_file(agent, folders):
    empty = folders[0] / "empty.docx"
    docx.Document().save(empty)
    flat = docx.Document()
    flat.add_paragraph("# Office Move Plan")
    flat.add_paragraph("The move is on 14 June.")
    flat.save(folders[0] / "flat.docx")
    no_slides = folders[0] / "none.pptx"
    pptx.Presentation().save(no_slides)

    assert "The document is empty" in _check_document(agent, empty)["warnings"][0]
    warnings = _check_document(agent, folders[0] / "flat.docx")["warnings"]
    assert "No heading styles" in warnings[0]
    assert "Markdown markup typed as text" in warnings[1]
    result = _check_document(agent, no_slides)
    assert result["slides"] == 0
    assert "The deck has no slides" in result["warnings"][0]


def test_a_style_based_on_a_heading_counts_as_a_heading(agent, folders):
    from docx.enum.style import WD_STYLE_TYPE

    doc = docx.Document()
    style = doc.styles.add_style("Section", WD_STYLE_TYPE.PARAGRAPH)
    style.base_style = doc.styles["Heading 2"]
    doc.add_paragraph("Scope", style="Section")
    path = folders[0] / "styled.docx"
    doc.save(path)

    assert _check_document(agent, path)["headings"] == {"2": 1}


def test_a_damaged_package_is_an_error_not_a_crash(agent, folders):
    path = folders[0] / "damaged.docx"
    with (
        zipfile.ZipFile(_memo(folders[0] / "memo.docx")) as zin,
        zipfile.ZipFile(path, "w") as zout,
    ):
        for item in zin.infolist():
            if item.filename != "word/document.xml":
                zout.writestr(item, zin.read(item.filename))

    result = _check_document(agent, path)

    assert result["status"] == "error"
    assert "its part 'word/document.xml' is missing" in result["error"]


def test_the_reference_examples_build_files_the_tool_accepts(agent, folders):
    """The Word and PowerPoint examples in the references, run as written."""
    pixel = folders[0] / "floor_plan.png"
    import base64

    pixel.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAC"
            "hwGA60e6kgAAAABJRU5ErkJggg=="
        )
    )
    built = {}
    for ref in ("word.md", "powerpoint.md"):
        text = (SKILL_DIR / "references" / ref).read_text(encoding="utf-8")
        code = re.findall(r"```python\n(.*?)\n```", text, re.S)[0]
        code = code.replace("C:\\Users\\me\\Documents\\", f"{folders[0]}{os.sep}")
        assert "\\me\\" not in code, f"{ref} has a path the test can't map"
        scope: dict = {}
        exec(compile(code, ref, "exec"), scope)  # pylint: disable=exec-used
        built[ref] = _check_document(agent, scope["path"])

    word, deck = built["word.md"], built["powerpoint.md"]
    assert word["status"] == "success", word
    assert word["headings"] == {"1": 1, "2": 1}
    assert (word["tables"], word["pictures"], word["warnings"]) == (1, 1, [])
    assert deck["status"] == "success", deck
    assert (deck["slides"], deck["charts"], deck["warnings"]) == (2, 1, [])
