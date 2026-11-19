# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""read_file answers with what the file holds, and names the best next tool.

A workbook too long to read came back as an error that said only "call
index_document", which sent the model to retrieval and away from a loaded
skill's own tool for that file; a .pptx could not be read at all; and an HTML
file came back as markup several times the size of its text.
"""

import pytest

from gaia.agents.base.completion import CompletionEvidence
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.install_hints import missing_package_hint
from gaia.agents.tools import document_text
from gaia.agents.tools.document_text import (
    INDEX_ONLY_DOCUMENTS,
    OVERVIEW_COLUMNS,
    OVERVIEW_ROWS,
    other_routes,
    skill_tools_for,
)
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator

openpyxl = pytest.importorskip("openpyxl")

BUDGET = 2000


def _skill_tool(description, **params):
    return {
        "skill": "some-skill",
        "description": description,
        "parameters": {name: {"description": text} for name, text in params.items()},
    }


@pytest.fixture
def host(tmp_path):
    made = FileIOToolsMixin()
    made.path_validator = PathValidator()
    made.path_validator.allowed_paths.add(tmp_path.resolve())
    made._truncation_budget = lambda: (3000, BUDGET)
    saved = dict(_TOOL_REGISTRY)
    try:
        made.register_file_io_tools()
        made.read_file = _TOOL_REGISTRY["read_file"]["function"]
        yield made
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def read_file(host):
    return host.read_file


def _workbook(path, sheets):
    book = openpyxl.Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    book.save(str(path))
    return path


@pytest.fixture
def long_workbook(tmp_path, monkeypatch):
    """A workbook past the extraction limit, made small by lowering the limit."""
    monkeypatch.setattr(document_text, "MAX_EXTRACT_CHARS", 2000)
    wide = [[f"col{c}" for c in range(30)]] + [
        [r * 100 + c for c in range(30)] for r in range(200)
    ]
    return _workbook(
        tmp_path / "book.xlsx",
        {"Wide": wide, "Notes": [["key", "value"], ["owner", "ops"]]},
    )


# ------------------------------------------------------------ a long workbook


def test_a_workbook_too_long_to_read_comes_back_as_its_sheets_and_first_rows(
    read_file, long_workbook
):
    result = read_file(str(long_workbook))

    assert result["status"] == "success", result
    assert result["sheets"] == 2
    assert result["sheet_sizes"] == [
        {"sheet": "Wide", "rows": 201, "columns": 30},
        {"sheet": "Notes", "rows": 2, "columns": 2},
    ]
    overview = result["overview"]
    assert "[Sheet: Wide] 201 rows x 30 columns" in overview
    assert "[Sheet: Notes] 2 rows x 2 columns" in overview
    assert "col0 | col1" in overview and "owner | ops" in overview


def test_the_overview_is_bounded_in_rows_and_columns(read_file, long_workbook):
    wide = read_file(str(long_workbook))["overview"].split("\n\n")[0].splitlines()

    assert len(wide) == 1 + OVERVIEW_ROWS
    assert wide[1].count(" | ") == OVERVIEW_COLUMNS
    assert wide[1].endswith(f"(+{30 - OVERVIEW_COLUMNS} more columns)")


def test_the_overview_says_it_is_not_the_data(read_file, long_workbook):
    result = read_file(str(long_workbook))

    assert "too long to read in full" in result["note"]
    assert "not the data" in result["note"]
    # Not the file's text: nothing may take it for a full read of the file.
    assert "content" not in result and "next_offset" not in result


def test_the_overview_says_how_to_read_a_range_when_code_can_run(host, long_workbook):
    host._tools_registry = {"read_file": {}, "run_python": {}, "index_document": {}}

    note = host.read_file(str(long_workbook))["note"]

    assert "To read a range of rows, use run_python" in note
    assert note.index("run_python") < note.index("index_document")


def test_a_tool_the_agent_lacks_is_not_named(host, long_workbook):
    host._tools_registry = {"read_file": {}}

    note = host.read_file(str(long_workbook))["note"]

    assert "run_python" not in note and "index_document" not in note


def test_a_loaded_skills_tool_for_the_file_type_is_named_first(host, long_workbook):
    host._tools_registry = {
        "read_file": {},
        "run_python": {},
        "index_document": {},
        "some-skill/read_table": _skill_tool(
            "Read rows of an .xlsx or .csv table.", file_path="The table to read."
        ),
    }

    note = host.read_file(str(long_workbook))["note"]

    assert "A loaded skill has a tool for .xlsx files; use it first" in note
    assert (
        note.index("some-skill/read_table")
        < note.index("run_python")
        < note.index("index_document")
    )


def test_line_ranges_on_a_long_workbook_name_the_other_routes(host, long_workbook):
    host._tools_registry = {"read_file": {}, "index_document": {}}

    result = host.read_file(str(long_workbook), start_line=1, end_line=5)

    assert result["status"] == "error"
    assert "too long to read in full" in result["error"]
    assert "index_document" in result["error"]


def test_a_long_workbook_is_measured_once(read_file, long_workbook, monkeypatch):
    calls = []
    real = document_text.extract_document_text
    monkeypatch.setattr(
        document_text,
        "extract_document_text",
        lambda path: calls.append(path) or real(path),
    )

    read_file(str(long_workbook))
    read_file(str(long_workbook))

    assert len(calls) == 1


# ----------------------------------------------- which skill tools get named


@pytest.mark.parametrize(
    "entry, named",
    [
        (_skill_tool("Read an .xlsx workbook.", file_path="The file."), True),
        (_skill_tool("Read a table.", path="An .XLSX or .csv file."), True),
        # Says nothing about the type.
        (_skill_tool("Read a table.", file_path="The file."), False),
        # Another type whose name starts the same way.
        (_skill_tool("Read an .xlsxm archive.", file_path="The file."), False),
        # Takes no file.
        (_skill_tool("Explain .xlsx formulas.", question="What to explain."), False),
        # Not a skill's tool.
        (
            {
                "description": "Read an .xlsx workbook.",
                "parameters": {"file_path": {"description": "The file."}},
            },
            False,
        ),
    ],
)
def test_a_skill_tool_is_named_only_when_it_declares_the_type_and_takes_a_file(
    entry, named
):
    class Host:
        _tools_registry = {"x/tool": entry}

    assert skill_tools_for(Host(), "data/book.xlsx") == (["x/tool"] if named else [])


def test_a_host_that_does_not_list_its_tools_gets_the_retrieval_route():
    assert "index_document('a.epub')" in other_routes(object(), "a.epub")


# ------------------------------------------------------------------- pptx


def _deck(path):
    pptx = pytest.importorskip("pptx")
    deck = pptx.Presentation()
    first = deck.slides.add_slide(deck.slide_layouts[1])
    first.shapes.title.text = "Garden plan"
    first.placeholders[1].text = "Plant tomatoes\nWater daily"
    first.notes_slide.notes_text_frame.text = "Start with the beds."
    second = deck.slides.add_slide(deck.slide_layouts[5])
    second.shapes.title.text = "Tools"
    deck.save(str(path))
    return path


def test_a_pptx_is_read_slide_by_slide_with_its_notes(read_file, tmp_path):
    result = read_file(str(_deck(tmp_path / "deck.pptx")))

    assert result["status"] == "success", result
    assert result["file_type"] == "pptx" and result["slides"] == 2
    text = result["content"]
    assert text.index("[Slide 1]") < text.index("Garden plan")
    assert "Plant tomatoes" in text and "Water daily" in text
    assert "[Notes]\nStart with the beds." in text
    assert text.index("[Slide 2]") < text.index("Tools")
    assert result["next_offset"] is None


def test_a_pptx_can_be_read_by_line_range(read_file, tmp_path):
    result = read_file(str(_deck(tmp_path / "deck.pptx")), start_line=1, end_line=2)

    assert result["status"] == "success", result
    assert "[Slide 1]" in result["content"]


def test_without_python_pptx_the_error_says_how_to_get_it(
    read_file, tmp_path, monkeypatch
):
    import sys

    path = _deck(tmp_path / "deck.pptx")
    monkeypatch.setitem(sys.modules, "pptx", None)

    result = read_file(str(path))

    assert result["status"] == "error"
    assert "requires python-pptx" in result["error"]
    assert missing_package_hint("python-pptx") in result["error"]


def test_a_file_that_is_not_a_presentation_says_so(read_file, tmp_path):
    pytest.importorskip("pptx")
    path = tmp_path / "deck.pptx"
    path.write_bytes(b"PK\x03\x04")

    result = read_file(str(path))

    assert result["status"] == "error"
    assert "Could not read PowerPoint file deck.pptx" in result["error"]


def test_pptx_is_no_longer_index_only():
    assert INDEX_ONLY_DOCUMENTS == frozenset({".epub"})


def test_a_format_it_cannot_extract_names_a_loaded_skills_tool_first(host, tmp_path):
    host._tools_registry = {
        "index_document": {},
        "some-skill/read_book": _skill_tool(
            "Read the chapters of an .epub book.", file_path="The book."
        ),
    }
    path = tmp_path / "book.epub"
    path.write_bytes(b"PK\x03\x04")

    error = host.read_file(str(path))["error"]

    assert error.index("some-skill/read_book") < error.index("index_document")


# -------------------------------------------------------------- line ranges


def _lines(tmp_path, count=20):
    path = tmp_path / "notes.txt"
    path.write_text("\n".join(f"line {i}" for i in range(1, count + 1)))
    return path


def test_a_limit_beside_a_start_line_counts_lines(read_file, tmp_path):
    """It was refused with "not both", and the retry cost a model round trip."""
    result = read_file(str(_lines(tmp_path)), start_line=3, limit=4)

    assert result["status"] == "success", result
    assert (result["start_line"], result["end_line"]) == (3, 6)
    assert result["content"].splitlines()[0].endswith("line 3")


def test_an_end_line_wins_over_a_limit(read_file, tmp_path):
    result = read_file(str(_lines(tmp_path)), start_line=3, end_line=4, limit=10)

    assert (result["start_line"], result["end_line"]) == (3, 4)


@pytest.mark.parametrize("kwargs", [{"end_line": 2}, {"limit": 2}, {}])
def test_a_start_line_of_zero_is_the_first_line(read_file, tmp_path, kwargs):
    result = read_file(str(_lines(tmp_path)), start_line=0, **kwargs)

    assert result["status"] == "success", result
    assert result["start_line"] == 1
    assert result["content"].splitlines()[0].endswith("line 1")
    if kwargs:
        assert result["end_line"] == 2


def test_a_negative_start_line_is_still_an_error(read_file, tmp_path):
    result = read_file(str(_lines(tmp_path)), start_line=-3)

    assert result["status"] == "error" and "1-based" in result["error"]


def test_a_limit_of_no_lines_is_an_error(read_file, tmp_path):
    result = read_file(str(_lines(tmp_path)), start_line=3, limit=0)

    assert result["status"] == "error" and "1 or more lines" in result["error"]


def test_a_character_offset_beside_a_start_line_says_which_to_drop(read_file, tmp_path):
    result = read_file(str(_lines(tmp_path)), offset=10, start_line=1)

    assert result["status"] == "error"
    assert "drop offset" in result["error"]


# -------------------------------------------------------------------- html

_PAGE = (
    "<html><head><title>Bread</title><style>p { color: red }</style>"
    "<script>var tracked = 1;</script></head><body>"
    "<h1>Steps</h1><p>Bake on <b>Sunday</b>.</p>"
    "<table><tr><td>Flour</td><td>500 g</td></tr></table></body></html>"
)


def _page(tmp_path, markup=_PAGE, name="bread.html"):
    pytest.importorskip("bs4")
    path = tmp_path / name
    path.write_text(markup, encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["bread.html", "bread.HTM"])
def test_an_html_file_is_read_as_its_text(read_file, tmp_path, name):
    result = read_file(str(_page(tmp_path, name=name)))

    assert result["status"] == "success", result
    assert result["file_type"] == "html"
    text = result["content"]
    assert "Steps" in text and "Bake on Sunday." in text
    assert "Flour | 500 g" in text
    assert "<" not in text and "tracked" not in text and "color" not in text
    assert "raw=true" in result["markup"]


def test_raw_returns_the_markup(read_file, tmp_path):
    result = read_file(str(_page(tmp_path)), raw=True)

    assert result["status"] == "success"
    assert result["content"] == _PAGE


def test_a_line_range_of_an_html_file_is_its_markup(read_file, tmp_path):
    result = read_file(str(_page(tmp_path)), start_line=1, end_line=1)

    assert "<h1>Steps</h1>" in result["content"]


def test_a_long_html_file_pages_through_its_text(read_file, tmp_path):
    body = "".join(f"<p>paragraph {i} of the recipe</p>" for i in range(400))
    path = _page(tmp_path, f"<html><body>{body}</body></html>")

    first = read_file(str(path))
    rest = read_file(str(path), offset=first["next_offset"])

    assert len(first["content"]) <= BUDGET
    assert first["next_offset"] and "offset=" in first["note"]
    assert rest["status"] == "success" and "<p>" not in rest["content"]


def test_the_text_view_does_not_unlock_an_edit_of_the_markup(host, tmp_path):
    from gaia.agents.tools.file_edit import file_read_record

    path = _page(tmp_path)
    host.read_file(str(path))
    assert file_read_record(host).refusal(str(path)) is not None

    host.read_file(str(path), raw=True)
    assert file_read_record(host).refusal(str(path)) is None


def test_an_html_file_this_turn_wrote_reads_back_as_written(host, tmp_path):
    path = _page(tmp_path)
    evidence = CompletionEvidence("Write `bread.html`", str(tmp_path))
    host._completion_evidence = evidence
    written = {"file_path": str(path)}
    evidence.record("write_file", written, written, True)

    result = host.read_file(str(path))

    assert result["content"] == _PAGE


def test_a_page_with_no_text_says_so(read_file, tmp_path):
    path = _page(tmp_path, "<html><body><script>render()</script></body></html>")

    result = read_file(str(path))

    assert result["status"] == "success" and not result["content"].strip()
    assert "nothing is left" in result["markup"]
