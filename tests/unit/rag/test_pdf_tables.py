# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A PDF table reaches the index as whole rows, each value named by its column."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pypdf")
pytest.importorskip("reportlab")

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from gaia.agents.tools.rag_tools import (  # noqa: E402
    _TABLE_MATH_NOTE,
    _computes_table_math,
)
from gaia.rag.pdf_tables import looks_tabular, tables_as_rows  # noqa: E402
from gaia.rag.sdk import RAGSDK, RAGConfig  # noqa: E402

HEADER = ["Row", "Item code", "Description", "Category", "Unit price (EUR)"]
COLUMNS_X = [60, 110, 190, 360, 450]
CATEGORIES = ["Valves", "Pumps", "Sensors"]


def _row(n: int) -> list:
    return [
        str(n),
        f"PT-{4000 + 13 * n}",
        f"{CATEGORIES[n % 3][:-1]} assembly type A{n}",
        CATEGORIES[n % 3],
        f"{100 + n * 7.31:.2f}",
    ]


def _price_table_pdf(path: Path, rows_per_page=(31, 29), repeat_header=True) -> None:
    """Each cell drawn on its own, as office tools write a table."""
    pdf = canvas.Canvas(str(path), pagesize=A4)
    n = 0
    for page, count in enumerate(rows_per_page):
        y = 800
        if page == 0:
            pdf.drawString(60, y, "Spare Parts Price Table 2026")
            y -= 20
            pdf.drawString(60, y, "All prices in EUR, excluding VAT.")
            y -= 30
        if page == 0 or repeat_header:
            for x, cell in zip(COLUMNS_X, HEADER):
                pdf.drawString(x, y, cell)
            y -= 22
        for _ in range(count):
            n += 1
            for x, cell in zip(COLUMNS_X, _row(n)):
                pdf.drawString(x, y, cell)
            y -= 22
        pdf.showPage()
    pdf.save()


@pytest.fixture
def rag(tmp_path: Path):
    fake_vlm = MagicMock(name="VLMClient")
    fake_vlm.check_availability.return_value = False
    with (
        patch.object(RAGSDK, "_check_dependencies", return_value=None),
        patch("gaia.rag.sdk.AgentSDK", autospec=True),
        patch("gaia.llm.VLMClient", return_value=fake_vlm),
    ):
        yield RAGSDK(
            config=RAGConfig(
                cache_dir=str(tmp_path / ".gaia"),
                allowed_paths=[str(tmp_path)],
                show_stats=False,
                use_local_llm=False,
            )
        )


def _named_row(n: int) -> str:
    return " | ".join(f"{h}: {v}" for h, v in zip(HEADER, _row(n)))


def test_each_row_is_one_line_naming_its_columns(rag, tmp_path):
    pdf = tmp_path / "price_table.pdf"
    _price_table_pdf(pdf)

    text = rag._extract_text_from_pdf(str(pdf))[0]

    lines = text.splitlines()
    assert _named_row(52) in lines
    assert _named_row(1) in lines
    assert "Spare Parts Price Table 2026" in text


def test_a_table_continuing_without_its_header_keeps_the_column_names(rag, tmp_path):
    pdf = tmp_path / "price_table.pdf"
    _price_table_pdf(pdf, repeat_header=False)

    text = rag._extract_text_from_pdf(str(pdf))[0]

    assert _named_row(52) in text.splitlines()


def test_chunking_never_splits_a_row(rag, tmp_path):
    pdf = tmp_path / "price_table.pdf"
    _price_table_pdf(pdf)

    chunks = rag._split_text_into_chunks(rag._extract_text_from_pdf(str(pdf))[0])

    for n in range(1, 61):
        assert any(_named_row(n) in chunk for chunk in chunks), n


def test_prose_is_left_alone():
    prose = (
        "The board met on Tuesday and approved the budget for the coming year.\n"
        "Members discussed the new depot and the timeline for its opening."
    )
    assert not looks_tabular(prose)
    assert tables_as_rows(prose, prose, None) == (prose, None)


def test_a_table_whose_cells_are_not_in_the_plain_text_is_left_alone():
    plain = "\n".join(["Alpha", "Beta", "Gamma"] * 6)
    layout = "\n".join(["a      b      c", "1      2      3", "4      5      6"])
    assert tables_as_rows(plain, layout, None)[0] == plain


class _Agent:
    def __init__(self, tools):
        self._tools_registry = {name: object() for name in tools}


def _search_returning(content):
    def search():
        return {"status": "success", "chunks": [{"content": content}]}

    return search


def test_table_rows_send_arithmetic_to_run_python():
    search = _computes_table_math(_Agent({"run_python"}))(
        _search_returning(_named_row(5) + "\n" + _named_row(6))
    )
    assert search()["instruction"].endswith(_TABLE_MATH_NOTE)


def test_no_note_without_table_rows_or_without_run_python():
    prose = _search_returning("The pump costs 120 EUR and ships in May.")
    assert "instruction" not in _computes_table_math(_Agent({"run_python"}))(prose)()
    rows = _search_returning(_named_row(5))
    assert "instruction" not in _computes_table_math(_Agent(set()))(rows)()
