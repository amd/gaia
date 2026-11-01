# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""PDF rendering and image extraction on PDFium (pypdfium2), with real PDFs.

PyMuPDF (AGPL-3.0) was replaced by pypdfium2. Every PDF here is built in
``tmp_path`` with reportlab or pypdf, so the tests run GAIA's real code against
the real library: nothing below the GAIA function is mocked.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

pdfium = pytest.importorskip("pypdfium2")
pytest.importorskip("reportlab")

from PIL import Image  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from gaia.rag.pdf_utils import (  # noqa: E402
    PdfImageExtractionError,
    extract_images_from_page_pdfium,
    get_image_positions_on_page,
)
from gaia.utils import PdfRenderError, pdf_page_to_image  # noqa: E402

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PAGE = (200, 300)


def _pdf(path: Path, pages) -> Path:
    """*pages* is a list of pages; each page a list of ``(PIL image, x, y)``."""
    c = canvas.Canvas(str(path), pagesize=PAGE)
    for placements in pages:
        c.drawString(10, 10, "page text")
        for image, x, y in placements:
            c.drawImage(
                ImageReader(image), x, y, width=image.width, height=image.height
            )
        c.showPage()
    c.save()
    return path


def _red(size=(64, 32)):
    return Image.new("RGB", size, (255, 0, 0))


def _encrypted(path: Path, user_password: str) -> Path:
    plain = _pdf(path.with_suffix(".plain.pdf"), [[(_red(), 20, 50)]])
    writer = PdfWriter(clone_from=PdfReader(str(plain)))
    writer.encrypt(user_password=user_password, owner_password="owner-secret")
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _truncated(path: Path) -> Path:
    full = _pdf(path.with_suffix(".full.pdf"), [[(_red(), 20, 50)]])
    data = full.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    return path


# ---------------------------------------------------------------------------
# Embedded image extraction
# ---------------------------------------------------------------------------


class TestImageExtraction:
    def test_the_embedded_image_comes_back_ready_for_the_vlm(self, tmp_path):
        pdf = _pdf(tmp_path / "one.pdf", [[(_red(), 20, 50)]])

        (image,) = extract_images_from_page_pdfium(str(pdf), page_num=1)

        assert set(image) == {"image_bytes", "width", "height", "format", "size_kb"}
        assert image["image_bytes"].startswith(PNG_SIGNATURE)
        assert (image["width"], image["height"]) == (64, 32)
        decoded = Image.open(io.BytesIO(image["image_bytes"])).convert("RGB")
        assert decoded.getpixel((10, 10)) == (255, 0, 0)

    def test_a_page_with_no_images_returns_an_empty_list(self, tmp_path):
        pdf = _pdf(tmp_path / "text.pdf", [[]])
        assert extract_images_from_page_pdfium(str(pdf), page_num=1) == []

    def test_page_numbers_are_one_based(self, tmp_path):
        pdf = _pdf(tmp_path / "two.pdf", [[], [(_red(), 20, 50)]])
        assert extract_images_from_page_pdfium(str(pdf), page_num=1) == []
        assert len(extract_images_from_page_pdfium(str(pdf), page_num=2)) == 1

    def test_two_different_images_are_both_returned(self, tmp_path):
        blue = Image.new("RGB", (40, 40), (0, 0, 255))
        pdf = _pdf(tmp_path / "pair.pdf", [[(_red(), 20, 50), (blue, 20, 150)]])

        sizes = {
            (i["width"], i["height"])
            for i in extract_images_from_page_pdfium(str(pdf), page_num=1)
        }
        assert sizes == {(64, 32), (40, 40)}

    def test_one_image_placed_twice_reaches_the_vlm_once(self, tmp_path):
        """reportlab reuses one XObject for a repeated image, as real PDFs do."""
        red = _red()
        pdf = _pdf(tmp_path / "twice.pdf", [[(red, 20, 50), (red, 20, 150)]])
        assert len(extract_images_from_page_pdfium(str(pdf), page_num=1)) == 1

    def test_images_too_small_on_the_page_are_left_out(self, tmp_path):
        pdf = _pdf(
            tmp_path / "logo.pdf",
            [[(_red((10, 10)), 20, 50), (_red((120, 120)), 20, 100)]],
        )

        assert len(extract_images_from_page_pdfium(str(pdf), page_num=1)) == 2
        (image,) = extract_images_from_page_pdfium(str(pdf), page_num=1, min_cover=0.02)
        assert image["width"] == 120

    def test_a_grayscale_image_is_extracted(self, tmp_path):
        gray = Image.new("L", (30, 30), 128)
        pdf = _pdf(tmp_path / "gray.pdf", [[(gray, 20, 50)]])
        (image,) = extract_images_from_page_pdfium(str(pdf), page_num=1)
        assert (image["width"], image["height"]) == (30, 30)

    def test_an_oversized_image_is_downscaled(self, tmp_path):
        big = Image.new("RGB", (2400, 600), (0, 128, 0))
        c = canvas.Canvas(str(tmp_path / "big.pdf"), pagesize=PAGE)
        c.drawImage(ImageReader(big), 0, 0, width=200, height=50)
        c.save()
        (image,) = extract_images_from_page_pdfium(
            str(tmp_path / "big.pdf"), page_num=1
        )
        assert max(image["width"], image["height"]) <= 1600

    def test_the_last_page_of_a_long_document_is_reachable(self, tmp_path):
        pages = [[] for _ in range(299)] + [[(_red(), 20, 50)]]
        pdf = _pdf(tmp_path / "long.pdf", pages)
        assert len(extract_images_from_page_pdfium(str(pdf), page_num=300)) == 1


class TestExtractionFailsLoudly:
    """A file PDFium cannot open is an error, never an image-less page."""

    def test_a_corrupt_pdf_raises_naming_the_file(self, tmp_path):
        bad = tmp_path / "broken.pdf"
        bad.write_bytes(b"%PDF-1.7\nnot actually a pdf\n")

        with pytest.raises(PdfImageExtractionError) as excinfo:
            extract_images_from_page_pdfium(str(bad), page_num=1)

        message = str(excinfo.value)
        assert "broken.pdf" in message and "damaged" in message
        assert ".." not in message
        assert excinfo.value.page_num == 1
        assert excinfo.value.images == []
        assert isinstance(excinfo.value.__cause__, pdfium.PdfiumError)

    def test_a_truncated_download_raises(self, tmp_path):
        with pytest.raises(PdfImageExtractionError, match="damaged"):
            extract_images_from_page_pdfium(
                str(_truncated(tmp_path / "cut.pdf")), page_num=1
            )

    def test_an_empty_file_raises(self, tmp_path):
        empty = tmp_path / "empty.pdf"
        empty.write_bytes(b"")
        with pytest.raises(PdfImageExtractionError):
            extract_images_from_page_pdfium(str(empty), page_num=1)

    def test_a_password_protected_pdf_raises_saying_so(self, tmp_path):
        locked = _encrypted(tmp_path / "locked.pdf", user_password="secret")
        with pytest.raises(PdfImageExtractionError, match="password"):
            extract_images_from_page_pdfium(str(locked), page_num=1)

    def test_an_owner_password_only_pdf_opens_like_any_viewer(self, tmp_path):
        """Permission-restricted PDFs (most SEC filings) need no password to read."""
        restricted = _encrypted(tmp_path / "restricted.pdf", user_password="")
        assert len(extract_images_from_page_pdfium(str(restricted), page_num=1)) == 1

    def test_a_page_past_the_end_raises_with_the_page_count(self, tmp_path):
        pdf = _pdf(tmp_path / "one.pdf", [[]])
        with pytest.raises(PdfImageExtractionError, match="it has 1 pages"):
            extract_images_from_page_pdfium(str(pdf), page_num=2)

    def test_page_zero_is_not_silently_the_last_page(self, tmp_path):
        """page_num is 1-based; 0 must not wrap around to index -1."""
        pdf = _pdf(tmp_path / "two.pdf", [[], [(_red(), 20, 50)]])
        with pytest.raises(PdfImageExtractionError):
            extract_images_from_page_pdfium(str(pdf), page_num=0)

    def test_a_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            extract_images_from_page_pdfium(str(tmp_path / "nope.pdf"), page_num=1)

    def test_an_undecodable_image_is_reported_with_the_ones_that_did_decode(
        self, tmp_path, mocker
    ):
        blue = Image.new("RGB", (40, 40), (0, 0, 255))
        pdf = _pdf(tmp_path / "pair.pdf", [[(_red(), 20, 50), (blue, 20, 150)]])
        real = pdfium.PdfImage.get_bitmap
        calls = {"n": 0}

        def flaky(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise pdfium.PdfiumError("bad image stream")
            return real(self, *args, **kwargs)

        mocker.patch.object(pdfium.PdfImage, "get_bitmap", flaky)

        with pytest.raises(PdfImageExtractionError, match="bad image stream") as e:
            extract_images_from_page_pdfium(str(pdf), page_num=1)
        assert len(e.value.images) == 1


# ---------------------------------------------------------------------------
# Image positions
# ---------------------------------------------------------------------------


def test_image_positions_are_top_left_origin_points(tmp_path):
    pdf = _pdf(tmp_path / "pos.pdf", [[(_red(), 20, 50)]])

    (pos,) = get_image_positions_on_page(str(pdf), page_num=0)

    # reportlab places the image's bottom-left at (20, 50) in a 300pt-tall page.
    assert pos["bbox"] == pytest.approx([20, 300 - 82, 84, 300 - 50])
    assert pos["position_y"] == pytest.approx(218)
    assert (pos["width"], pos["height"]) == (64, 32)
    assert pos["image_index"] == 0


def test_image_positions_of_a_corrupt_pdf_raise(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"garbage")
    with pytest.raises(PdfImageExtractionError):
        get_image_positions_on_page(str(bad), page_num=0)


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------


class TestPageRendering:
    @pytest.mark.parametrize("scale,size", [(1.0, (200, 300)), (2.0, (400, 600))])
    def test_a_page_renders_to_a_png_at_the_requested_scale(
        self, tmp_path, scale, size
    ):
        pdf = _pdf(tmp_path / "page.pdf", [[(_red(), 20, 50)]])

        png = pdf_page_to_image(pdf, page=0, scale=scale)

        assert png.startswith(PNG_SIGNATURE)
        rendered = Image.open(io.BytesIO(png))
        assert rendered.size == size

    def test_the_render_shows_the_page_content(self, tmp_path):
        pdf = _pdf(tmp_path / "page.pdf", [[(_red(), 20, 50)]])
        rendered = Image.open(io.BytesIO(pdf_page_to_image(pdf, scale=1.0)))
        # Inside the red image (top-left origin): x=50, y=300-66.
        assert rendered.convert("RGB").getpixel((50, 234)) == (255, 0, 0)
        assert rendered.convert("RGB").getpixel((150, 30)) == (255, 255, 255)

    def test_a_page_past_the_end_raises(self, tmp_path):
        pdf = _pdf(tmp_path / "page.pdf", [[]])
        with pytest.raises(PdfRenderError, match="it has 1 pages"):
            pdf_page_to_image(pdf, page=1)

    def test_a_negative_page_raises(self, tmp_path):
        pdf = _pdf(tmp_path / "page.pdf", [[]])
        with pytest.raises(PdfRenderError):
            pdf_page_to_image(pdf, page=-1)

    def test_a_corrupt_pdf_raises_naming_the_file(self, tmp_path):
        bad = tmp_path / "broken.pdf"
        bad.write_bytes(b"%PDF-1.7\nnot a pdf\n")
        with pytest.raises(PdfRenderError, match="broken.pdf") as excinfo:
            pdf_page_to_image(bad)
        assert ".." not in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, pdfium.PdfiumError)

    def test_a_password_protected_pdf_raises(self, tmp_path):
        locked = _encrypted(tmp_path / "locked.pdf", user_password="secret")
        with pytest.raises(PdfRenderError, match="password"):
            pdf_page_to_image(locked)


# ---------------------------------------------------------------------------
# The eval's image-only PDF builder
# ---------------------------------------------------------------------------


def test_image_only_pdf_keeps_pages_and_size_and_drops_the_text(tmp_path):
    from gaia.eval.retrieval.hard_cases import image_only_pdf

    src = _pdf(tmp_path / "src.pdf", [[], [(_red(), 20, 50)]])
    dest = tmp_path / "scanned.pdf"

    image_only_pdf(src, dest, dpi=72)

    reader = PdfReader(str(dest))
    assert len(reader.pages) == 2
    for page in reader.pages:
        assert (float(page.mediabox.width), float(page.mediabox.height)) == (
            pytest.approx(PAGE[0], abs=1),
            pytest.approx(PAGE[1], abs=1),
        )
        assert not (page.extract_text() or "").strip()
    assert len(extract_images_from_page_pdfium(str(dest), page_num=1)) == 1


# ---------------------------------------------------------------------------
# The RAG indexer drives the real extractor
# ---------------------------------------------------------------------------


def _sdk():
    from gaia.rag.sdk import RAGSDK, RAGConfig

    sdk = RAGSDK.__new__(RAGSDK)
    sdk.config = RAGConfig(show_stats=False)
    sdk.log = __import__("logging").getLogger("test-rag")
    return sdk


def _vlm(mocker):
    vlm = mocker.MagicMock()
    vlm.check_availability.return_value = True
    vlm.extract_from_page_images.side_effect = lambda images, page_num: [
        {"image_num": 1, "text": "CHART TEXT", "size_kb": 1}
    ]
    mocker.patch("gaia.llm.VLMClient", return_value=vlm)
    return vlm


def test_the_indexer_sends_the_pages_real_image_to_the_vlm(tmp_path, mocker):
    pdf = _pdf(tmp_path / "doc.pdf", [[(_red(), 20, 50)]])
    vlm = _vlm(mocker)

    full_text, pages, metadata = _sdk()._extract_text_from_pdf(str(pdf))

    (images,), kwargs = vlm.extract_from_page_images.call_args
    assert kwargs == {"page_num": 1}
    assert (images[0]["width"], images[0]["height"]) == (64, 32)
    assert "CHART TEXT" in full_text and "page text" in full_text
    assert metadata.get("pdf_status") != "degraded"


def test_a_partly_extracted_page_is_indexed_and_marked_degraded(tmp_path, mocker):
    pdf = _pdf(tmp_path / "doc.pdf", [[(_red(), 20, 50)]])
    vlm = _vlm(mocker)
    partial = {"image_bytes": PNG_SIGNATURE, "width": 1, "height": 1, "size_kb": 1}
    mocker.patch(
        "gaia.rag.pdf_utils.extract_images_from_page_pdfium",
        side_effect=PdfImageExtractionError("1 image(s) bad", 1, [partial]),
    )

    full_text, _, metadata = _sdk()._extract_text_from_pdf(str(pdf))

    (images,), _ = vlm.extract_from_page_images.call_args
    assert images == [partial]
    assert "CHART TEXT" in full_text
    assert metadata["degraded_pages"] == [1]
    assert "1 image(s) bad" in metadata["page_warnings"][1]
