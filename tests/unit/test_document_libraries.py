# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's document readers against real files from the real libraries.

Every file is written in ``tmp_path`` by the library that produces it in the
wild (reportlab, python-docx, python-pptx, openpyxl, xlsxwriter, Pillow) and
read back through GAIA's own code: the agent's document reader
(``gaia.agents.tools.document_text``), the RAG extractors, the deliverable
figure check, and the VLM image prep. No library is mocked.

Bad input is the point as much as good input: a corrupt, truncated, empty or
password-protected file must raise an error that says so, never read as an
empty document.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("pypdf")
pytest.importorskip("reportlab")
docx = pytest.importorskip("docx")
pptx = pytest.importorskip("pptx")
openpyxl = pytest.importorskip("openpyxl")

from PIL import Image  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from gaia.agents.base.deliverables import blocks_text, read_blocks  # noqa: E402
from gaia.agents.tools.document_text import extract_document_text  # noqa: E402
from gaia.rag.docx_utils import extract_docx_text  # noqa: E402
from gaia.rag.pdf_utils import count_images_in_page, prepare_image_for_vlm  # noqa: E402
from gaia.rag.pptx_utils import (  # noqa: E402
    count_images_in_slide,
    extract_images_from_slide,
    extract_text_from_slide,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _pdf(path: Path, pages: list[str], image: Image.Image | None = None) -> Path:
    c = canvas.Canvas(str(path), pagesize=(300, 300))
    for text in pages:
        if text:
            c.drawString(20, 250, text)
        if image is not None:
            c.drawImage(
                ImageReader(image), 20, 20, width=image.width, height=image.height
            )
        c.showPage()
    c.save()
    return path


def _corrupt(path: Path) -> Path:
    path.write_bytes(b"this is not a document at all")
    return path


def _truncated(path: Path) -> Path:
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    return path


# ---------------------------------------------------------------------------
# pypdf (text, image inventory) with reportlab-written PDFs
# ---------------------------------------------------------------------------


class TestPdfText:
    def test_text_is_read_with_page_markers(self, tmp_path):
        pdf = _pdf(tmp_path / "report.pdf", ["Revenue was 4471 units", "Page two"])

        text, meta = extract_document_text(str(pdf))

        assert "[Page 1]\nRevenue was 4471 units" in text
        assert "[Page 2]\nPage two" in text
        assert meta == {"pages": 2}

    def test_a_long_document_reads_every_page(self, tmp_path):
        pdf = _pdf(tmp_path / "long.pdf", [f"line {n}" for n in range(1, 501)])
        text, meta = extract_document_text(str(pdf))
        assert meta["pages"] == 500
        assert "[Page 500]\nline 500" in text

    def test_a_scan_with_no_text_layer_is_an_error_not_empty_text(self, tmp_path):
        pdf = _pdf(tmp_path / "scan.pdf", [""], image=Image.new("RGB", (40, 40)))
        with pytest.raises(ValueError, match="no text layer"):
            extract_document_text(str(pdf))

    def test_a_corrupt_pdf_is_an_error(self, tmp_path):
        with pytest.raises(ValueError, match="corrupted"):
            extract_document_text(str(_corrupt(tmp_path / "bad.pdf")))

    def test_a_truncated_pdf_is_an_error(self, tmp_path):
        pdf = _truncated(_pdf(tmp_path / "cut.pdf", ["some text"]))
        with pytest.raises(ValueError):
            extract_document_text(str(pdf))

    def test_a_password_protected_pdf_is_an_error_saying_so(self, tmp_path):
        plain = _pdf(tmp_path / "plain.pdf", ["secret text"])
        writer = PdfWriter(clone_from=PdfReader(str(plain)))
        writer.encrypt(user_password="pw", owner_password="owner")
        locked = tmp_path / "locked.pdf"
        with open(locked, "wb") as f:
            writer.write(f)

        with pytest.raises(ValueError, match="password-protected"):
            extract_document_text(str(locked))


class TestPdfImageInventory:
    def test_a_page_with_an_image_counts_it(self, tmp_path):
        pdf = _pdf(tmp_path / "img.pdf", ["x"], image=Image.new("RGB", (40, 40)))
        page = PdfReader(str(pdf)).pages[0]
        assert count_images_in_page(page, page_num=1) == (True, 1)

    def test_a_text_page_counts_none(self, tmp_path):
        page = PdfReader(str(_pdf(tmp_path / "t.pdf", ["x"]))).pages[0]
        assert count_images_in_page(page, page_num=1) == (False, 0)


# ---------------------------------------------------------------------------
# python-docx
# ---------------------------------------------------------------------------


def _docx(path: Path, paragraphs=(), table=None) -> Path:
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


class TestDocx:
    def test_paragraphs_and_tables_are_read_in_order(self, tmp_path):
        path = _docx(
            tmp_path / "memo.docx",
            ["Quarterly memo"],
            table=[["Region", "Sales"], ["West", "1200"]],
        )

        text = extract_docx_text(str(path))

        assert text.index("Quarterly memo") < text.index("West")
        assert "Region" in text and "1200" in text

    def test_the_agent_reader_returns_the_same_text(self, tmp_path):
        path = _docx(tmp_path / "memo.docx", ["Hello from Word"])
        text, meta = extract_document_text(str(path))
        assert "Hello from Word" in text and meta == {}

    def test_the_figure_check_reads_a_docx_table(self, tmp_path):
        path = _docx(tmp_path / "out.docx", table=[["Item", "Cost"], ["Bolt", "4.50"]])
        assert "| Bolt | 4.50 |" in blocks_text(read_blocks(str(path)))

    def test_an_empty_document_reads_as_empty_text(self, tmp_path):
        assert extract_docx_text(str(_docx(tmp_path / "empty.docx"))) == ""

    def test_a_corrupt_docx_is_an_error_naming_the_file(self, tmp_path):
        with pytest.raises(ValueError, match="bad.docx"):
            extract_docx_text(str(_corrupt(tmp_path / "bad.docx")))

    def test_a_truncated_docx_is_an_error(self, tmp_path):
        path = _truncated(_docx(tmp_path / "cut.docx", ["text"]))
        with pytest.raises(ValueError):
            extract_docx_text(str(path))


# ---------------------------------------------------------------------------
# python-pptx
# ---------------------------------------------------------------------------


def _pptx(path: Path, text="", image: Image.Image | None = None, table=None) -> Path:
    from pptx.util import Inches

    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    if text:
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        box.text_frame.text = text
    if image is not None:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        slide.shapes.add_picture(buf, Inches(1), Inches(3))
    if table:
        shape = slide.shapes.add_table(
            len(table), len(table[0]), Inches(5), Inches(1), Inches(3), Inches(1)
        )
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                shape.table.cell(r, c).text = value
    deck.save(str(path))
    return path


class TestPptx:
    def test_slide_text_and_the_embedded_picture_are_read(self, tmp_path):
        path = _pptx(
            tmp_path / "deck.pptx",
            text="Roadmap 2026",
            image=Image.new("RGB", (120, 60), (0, 0, 255)),
        )
        slide = pptx.Presentation(str(path)).slides[0]

        assert "Roadmap 2026" in extract_text_from_slide(slide, 1)
        assert count_images_in_slide(slide) == (True, 1)
        (image,) = extract_images_from_slide(slide, 1)
        assert image["image_bytes"].startswith(PNG_SIGNATURE)
        assert (image["width"], image["height"]) == (120, 60)

    def test_a_slide_without_pictures_has_none(self, tmp_path):
        slide = pptx.Presentation(str(_pptx(tmp_path / "d.pptx", "x"))).slides[0]
        assert count_images_in_slide(slide) == (False, 0)
        assert extract_images_from_slide(slide, 1) == []

    def test_the_figure_check_reads_a_slide_table(self, tmp_path):
        path = _pptx(tmp_path / "d.pptx", table=[["Quarter", "Units"], ["Q1", "300"]])
        assert "| Q1 | 300 |" in blocks_text(read_blocks(str(path)))

    def test_a_corrupt_pptx_is_an_error_naming_the_file(self, tmp_path):
        from gaia.rag.sdk import RAGSDK, RAGConfig

        sdk = RAGSDK.__new__(RAGSDK)
        sdk.config = RAGConfig(show_stats=False)
        sdk.log = __import__("logging").getLogger("test-rag")
        with pytest.raises(ValueError, match="bad.pptx"):
            sdk._extract_text_from_pptx(str(_corrupt(tmp_path / "bad.pptx")))


# ---------------------------------------------------------------------------
# openpyxl and xlsxwriter
# ---------------------------------------------------------------------------


def _rag(tmp_path):
    from gaia.rag.sdk import RAGSDK, RAGConfig

    config = RAGConfig(
        cache_dir=str(tmp_path / ".gaia"), show_stats=False, use_local_llm=False
    )
    with (
        patch.object(RAGSDK, "_check_dependencies", return_value=None),
        patch("gaia.rag.sdk.AgentSDK"),
    ):
        return RAGSDK(config=config)


class TestXlsx:
    def test_an_openpyxl_workbook_is_read_by_sheet(self, tmp_path):
        book = openpyxl.Workbook()
        book.active.title = "Parts"
        book.active.append(["part", "qty"])
        book.active.append(["bolt", 4])
        path = tmp_path / "bom.xlsx"
        book.save(path)

        text, meta = extract_document_text(str(path))

        assert "[Sheet: Parts]" in text and "bolt | 4" in text
        assert meta == {"sheets": 1}

    def test_an_openpyxl_formula_without_a_saved_value_is_flagged(self, tmp_path):
        """A script-written workbook has formulas but no computed values."""
        book = openpyxl.Workbook()
        book.active.append([2, 3, "=A1*B1"])
        path = tmp_path / "calc.xlsx"
        book.save(path)

        text, meta = extract_document_text(str(path))

        assert "2 | 3" in text
        assert "1 formula cell(s) have no saved value" in meta["warning"]

    @pytest.mark.xfail(
        strict=True,
        reason="xlsxwriter caches 0 for every formula, so the cell reads as a "
        "real 0 and the no-saved-value warning never fires",
    )
    def test_an_xlsxwriter_formula_without_a_saved_value_is_flagged(self, tmp_path):
        xlsxwriter = pytest.importorskip("xlsxwriter")
        path = tmp_path / "calc.xlsx"
        book = xlsxwriter.Workbook(str(path))
        sheet = book.add_worksheet()
        sheet.write_row(0, 0, [2, 3])
        sheet.write_formula(0, 2, "=A1*B1")
        book.close()

        text, meta = extract_document_text(str(path))

        assert "2 | 3" in text
        assert "1 formula cell(s) have no saved value" in meta["warning"]

    def test_the_figure_check_formats_cells_as_excel_shows_them(self, tmp_path):
        book = openpyxl.Workbook()
        book.active.append(["metric", "value"])
        book.active.append(["margin", 0.125])
        book.active["B2"].number_format = "0.0%"
        path = tmp_path / "kpi.xlsx"
        book.save(path)

        assert "| margin | 12.5% |" in blocks_text(read_blocks(str(path)))

    def test_rag_reads_the_workbook(self, tmp_path):
        book = openpyxl.Workbook()
        book.active.append(["city", "population"])
        book.active.append(["Austin", 979882])
        path = tmp_path / "cities.xlsx"
        book.save(path)

        text = _rag(tmp_path)._extract_text_from_xlsx(str(path))

        assert "Austin" in text and "979882" in text

    def test_rag_refuses_an_empty_workbook(self, tmp_path):
        from gaia.rag.sdk import SpreadsheetReadError

        path = tmp_path / "empty.xlsx"
        openpyxl.Workbook().save(path)
        with pytest.raises(SpreadsheetReadError, match="no cell values"):
            _rag(tmp_path)._extract_text_from_xlsx(str(path))

    def test_rag_refuses_a_corrupt_workbook(self, tmp_path):
        from gaia.rag.sdk import SpreadsheetReadError

        with pytest.raises(SpreadsheetReadError):
            _rag(tmp_path)._extract_text_from_xlsx(str(_corrupt(tmp_path / "b.xlsx")))

    def test_the_agent_reader_refuses_a_corrupt_workbook(self, tmp_path):
        with pytest.raises(ValueError, match="b.xlsx"):
            extract_document_text(str(_corrupt(tmp_path / "b.xlsx")))


# ---------------------------------------------------------------------------
# Pillow: what the vision model is sent
# ---------------------------------------------------------------------------


def _encoded(image: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


class TestImagePrep:
    def test_a_cmyk_jpeg_reaches_the_vlm_as_rgb_png(self):
        cmyk = Image.new("CMYK", (50, 40), (0, 255, 255, 0))
        prepared = prepare_image_for_vlm(_encoded(cmyk, "JPEG"))

        assert prepared["image_bytes"].startswith(PNG_SIGNATURE)
        decoded = Image.open(io.BytesIO(prepared["image_bytes"]))
        assert decoded.mode == "RGB" and decoded.size == (50, 40)

    def test_a_large_image_is_downscaled_keeping_its_aspect(self):
        prepared = prepare_image_for_vlm(_encoded(Image.new("RGB", (3200, 800)), "PNG"))
        assert (prepared["width"], prepared["height"]) == (1600, 400)

    def test_undecodable_bytes_raise_rather_than_returning_an_empty_image(self):
        from PIL import UnidentifiedImageError

        with pytest.raises(UnidentifiedImageError):
            prepare_image_for_vlm(b"not an image")

    def test_a_truncated_png_raises(self):
        data = _encoded(Image.effect_noise((200, 200), 64), "PNG")
        with pytest.raises(OSError):
            prepare_image_for_vlm(data[: len(data) // 2])
