# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""read_file returns the full text of Word, PDF and Excel files, in marked parts.

Asked to reconcile a term sheet against a draft agreement, the agent read the
.docx with read_file, got ``[Binary file, N bytes]``, fell back to indexing and
five retrieval queries, and missed a closing date that differed between the two
— no query had asked for it. A document small enough to read should be read.
"""

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.document_text import FULL_READ_MAX_READS
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.security import PathValidator

docx = pytest.importorskip("docx")

BUDGET = 2000


@pytest.fixture
def registry():
    saved = dict(_TOOL_REGISTRY)
    try:
        yield _TOOL_REGISTRY
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def read_file(tmp_path, registry):
    host = FileIOToolsMixin()
    host.path_validator = PathValidator()
    host.path_validator.allowed_paths.add(tmp_path.resolve())
    host._truncation_budget = lambda: (3000, BUDGET)
    host.register_file_io_tools()
    return registry["read_file"]["function"]


def _docx(path, paragraphs, table=None):
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if table:
        grid = document.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                grid.cell(r, c).text = value
    document.save(str(path))
    return path


def _pdf(path, pages):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    pdf = canvas.Canvas(str(path))
    for lines in pages:
        y = 800
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 14
        pdf.showPage()
    pdf.save()
    return path


def _read_all(read_file, path):
    """Follow next_offset to the end, the way the model is told to."""
    parts, offset = [], 0
    while True:
        result = read_file(str(path), offset=offset)
        assert result["status"] == "success", result
        parts.append(result["content"])
        if result["next_offset"] is None:
            return "".join(parts), result
        assert "offset=" in result["note"]
        offset = result["next_offset"]


def test_a_small_docx_comes_back_whole(read_file, tmp_path):
    path = _docx(
        tmp_path / "terms.docx",
        ["Closing Date: November 14", "Option pool: 12%"],
        table=[["Term", "Value"], ["Major Purchaser", "$250,000"]],
    )

    result = read_file(str(path))

    assert result["status"] == "success"
    assert result["file_type"] == "docx"
    assert "Closing Date: November 14" in result["content"]
    assert "Major Purchaser | $250,000" in result["content"]
    assert result["next_offset"] is None
    assert "note" not in result and "hint" not in result


def test_a_long_docx_is_read_in_marked_parts_that_add_up(read_file, tmp_path):
    paragraphs = [f"Section {i}: the parties agree to clause {i}." for i in range(300)]
    path = _docx(tmp_path / "agreement.docx", paragraphs)

    first = read_file(str(path))
    assert first["next_offset"] is not None
    assert len(first["content"]) <= BUDGET
    assert first["total_chars"] > BUDGET
    assert f"offset={first['next_offset']}" in first["note"]

    text, last = _read_all(read_file, path)
    assert text.splitlines() == paragraphs
    assert "note" not in last


def test_parts_end_on_a_line_break(read_file, tmp_path):
    path = _docx(tmp_path / "a.docx", [f"Line {i} " + "x" * 40 for i in range(200)])
    first = read_file(str(path))
    assert first["content"].endswith("\n")


def test_limit_shrinks_a_part_but_never_past_the_budget(read_file, tmp_path):
    path = _docx(tmp_path / "a.docx", ["y" * 50] * 200)
    assert len(read_file(str(path), limit=300)["content"]) <= 300
    assert len(read_file(str(path), limit=10 * BUDGET)["content"]) <= BUDGET


def test_a_very_long_document_says_retrieval_is_cheaper(read_file, tmp_path):
    words = "z" * 60
    path = _docx(tmp_path / "big.docx", [words] * (FULL_READ_MAX_READS * BUDGET // 50))
    first = read_file(str(path))
    assert "index_document" in first["hint"]
    assert "hint" not in read_file(str(path), offset=first["next_offset"])


def test_an_offset_past_the_end_is_an_error(read_file, tmp_path):
    path = _docx(tmp_path / "a.docx", ["short"])
    result = read_file(str(path), offset=10_000)
    assert result["status"] == "error"
    assert "past the end" in result["error"]


def test_line_ranges_work_on_extracted_text(read_file, tmp_path):
    path = _docx(tmp_path / "a.docx", ["first", "second", "third"])
    result = read_file(str(path), start_line=2, end_line=2)
    assert result["status"] == "success"
    assert result["content"] == "     2\tsecond\n"


def test_a_pdf_is_read_page_by_page(read_file, tmp_path):
    path = _pdf(
        tmp_path / "datasheet.pdf",
        [["Model A datasheet"], ["Dual HDMI: up to 4Kp30 when both are used"]],
    )
    result = read_file(str(path))
    assert result["status"] == "success"
    assert result["file_type"] == "pdf" and result["pages"] == 2
    assert "[Page 2]" in result["content"]
    assert "4Kp30 when both are used" in result["content"]


def test_a_pdf_without_a_text_layer_says_so(read_file, tmp_path):
    path = _pdf(tmp_path / "scan.pdf", [[]])
    result = read_file(str(path))
    assert result["status"] == "error"
    assert "no text layer" in result["error"]


def test_a_corrupt_document_is_an_error_not_binary_noise(read_file, tmp_path):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"not a zip")
    result = read_file(str(path))
    assert result["status"] == "error"
    assert "broken.docx" in result["error"]


def test_an_xlsx_is_read_sheet_by_sheet(read_file, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Prices"
    sheet.append(["Part", "Unit price"])
    sheet.append(["Board", 67.5])
    path = tmp_path / "bom.xlsx"
    workbook.save(path)

    result = read_file(str(path))
    assert result["status"] == "success"
    assert result["sheets"] == 1
    assert result["content"] == "[Sheet: Prices]\nPart | Unit price\nBoard | 67.5"
    assert "warning" not in result


def test_formulas_without_saved_values_are_flagged_not_silently_blank(
    read_file, tmp_path
):
    """openpyxl writes formulas but never computes them, like any script."""
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Qty", "Unit", "Total"])
    sheet.append([40, 6.59, "=A2*B2"])
    path = tmp_path / "bom.xlsx"
    workbook.save(path)

    result = read_file(str(path))
    assert result["content"].splitlines()[-1] == "40 | 6.59"
    assert "1 formula cell(s) have no saved value" in result["warning"]


@pytest.mark.parametrize("extension", [".pptx", ".doc", ".odt"])
def test_formats_it_cannot_extract_point_at_index_document(
    read_file, tmp_path, extension
):
    path = tmp_path / f"deck{extension}"
    path.write_bytes(b"PK\x03\x04")
    result = read_file(str(path))
    assert result["status"] == "error"
    assert "index_document" in result["error"]


def test_paging_does_not_re_extract_an_unchanged_file(read_file, tmp_path, monkeypatch):
    from gaia.agents.tools import document_text

    path = _docx(tmp_path / "a.docx", ["w" * 50] * 200)
    calls = []
    real = document_text.extract_document_text
    monkeypatch.setattr(
        document_text,
        "extract_document_text",
        lambda p: calls.append(p) or real(p),
    )
    first = read_file(str(path))
    read_file(str(path), offset=first["next_offset"])
    assert len(calls) == 1


def test_the_search_mixins_read_file_extracts_too(tmp_path, registry):
    """The doc profile's read_file comes from FileSearchToolsMixin."""

    class Host(FileSearchToolsMixin):
        pass

    host = Host()
    host._truncation_budget = lambda: (3000, BUDGET)
    host.register_file_search_tools()
    path = _docx(tmp_path / "memo.docx", ["Recommendation: negotiate"])
    result = registry["read_file"]["function"](str(path))
    assert result["status"] == "success"
    assert "Recommendation: negotiate" in result["content"]


class TestThePromptRule:
    """The rule exists only where it is a choice, and names the tools it means."""

    class _Host(FileIOToolsMixin):
        _tools_registry = {"read_file": {}, "index_document": {}}

    def test_it_tells_the_model_to_read_a_few_documents_in_full(self):
        text = self._Host().get_document_reading_system_prompt()
        assert "read_file" in text and "in full" in text
        assert "next_offset" in text
        assert ".docx" in text and ".pdf" in text

    def test_it_keeps_retrieval_for_large_collections(self):
        text = self._Host().get_document_reading_system_prompt()
        assert "large collection" in text

    def test_it_is_short(self):
        assert len(self._Host().get_document_reading_system_prompt()) < 600

    @pytest.mark.parametrize("tools", [{"read_file": {}}, {"index_document": {}}, {}])
    def test_it_is_absent_without_both_ways_to_read(self, tools):
        class Host(FileIOToolsMixin):
            _tools_registry = tools

        assert Host().get_document_reading_system_prompt() == ""

    @pytest.mark.parametrize(
        "profile,advertises",
        [("chat", False), ("doc", True), ("file", False), ("full", True)],
    )
    def test_profiles_that_can_index_say_so(self, profile, advertises):
        from tests.unit.test_profilespec_characterization import (
            chat_agent_build_context,
        )

        with chat_agent_build_context(profile) as agent:
            agent._register_tools()
            assert bool(agent.get_document_reading_system_prompt()) is advertises


def test_a_tiny_limit_on_a_document_warns_that_it_counts_characters(
    read_file, tmp_path
):
    path = _docx(tmp_path / "a.docx", ["y" * 50] * 200)
    result = read_file(str(path), limit=300)
    assert "characters, not lines" in result["warning"]
    # The part is still marked partial with where to continue.
    assert result["next_offset"] is not None
    assert f"offset={result['next_offset']}" in result["note"]


def test_a_partial_pdf_read_says_where_the_next_part_starts(read_file, tmp_path):
    pages = [[f"Page {p} line {i} " + "q" * 40 for i in range(40)] for p in range(6)]
    path = _pdf(tmp_path / "long.pdf", pages)
    first = read_file(str(path))
    assert first["status"] == "success" and first["next_offset"] is not None
    assert f"offset={first['next_offset']}" in first["note"]
    assert "Read every part" in first["note"]
