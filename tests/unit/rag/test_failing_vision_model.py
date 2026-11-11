# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A vision model that fails on every image stops indexing with its name (#4741).

It used to be retried on every page and each failure turned into a page warning,
so a crashing model indexed a scan as blank pages, took minutes doing it, and
ended in a hint to download the model that was already there.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("reportlab")

from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from gaia.llm.vlm_client import VLMExtractionError  # noqa: E402
from gaia.rag.sdk import (  # noqa: E402
    RAGSDK,
    VISION_FAILURE_LIMIT,
    EmptyPDFError,
    RAGConfig,
    VisionModelFailedError,
)

CRASH = "Backend error: backend_watchdog_reset"
DIM = 8


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 300), "gray").save(out, format="PNG")
    return out.getvalue()


def _scan(path: Path, pages: int) -> Path:
    """A PDF of *pages* pages holding only a picture, like a scan with no OCR layer."""
    c = canvas.Canvas(str(path), pagesize=(600, 800))
    picture = ImageReader(io.BytesIO(_png()))
    for _ in range(pages):
        c.drawImage(picture, 40, 80, width=400, height=300)
        c.showPage()
    c.save()
    return path


def _vlm(*outcomes) -> MagicMock:
    """A vision model answering each page with the next outcome: True reads, False fails."""
    script = list(outcomes)
    # Past the script, the last outcome repeats.
    after = outcomes[-1]

    def read(images, page_num):
        if not (script.pop(0) if script else after):
            raise VLMExtractionError(CRASH, page_num, 1)
        return [
            {
                "image_num": 1,
                "text": f"read page {page_num}",
                "dimensions": "1x1",
                "size_kb": 1,
            }
        ]

    vlm = MagicMock()
    vlm.check_availability.return_value = True
    vlm.vlm_model = "vision-model"
    vlm.extract_from_page_images.side_effect = read
    return vlm


def _sdk(tmp_path: Path) -> RAGSDK:
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                vlm_model="vision-model",
                show_stats=False,
            )
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **kw: (
        np.stack(
            [
                np.random.default_rng(
                    int.from_bytes(hashlib.sha256(t.encode()).digest()[:4], "little")
                )
                .random(DIM)
                .astype("float32")
                for t in texts
            ]
        )
        if texts
        else np.zeros((0, DIM))
    )
    sdk._get_hmac_key = lambda: b"test-index-key" * 3
    return sdk


def _patched(vlm: MagicMock):
    return (
        patch("gaia.llm.VLMClient", return_value=vlm),
        patch("gaia.rag.sdk.resident_vision_model", return_value="vision-model"),
    )


def _extract_pdf(tmp_path: Path, pdf: Path, vlm: MagicMock):
    a, b = _patched(vlm)
    with a, b:
        return _sdk(tmp_path)._extract_text_from_pdf(str(pdf))


class TestAPdf:
    def test_a_model_that_fails_every_image_stops_with_its_name(self, tmp_path):
        vlm = _vlm(False)
        pdf = _scan(tmp_path / "scan.pdf", 10)

        with pytest.raises(VisionModelFailedError) as raised:
            _extract_pdf(tmp_path, pdf, vlm)

        message = str(raised.value)
        assert "vision-model" in message
        assert CRASH in message
        assert "scan.pdf" in message
        assert isinstance(raised.value.__cause__, VLMExtractionError)
        # Not one wait per page: a 10-page scan stops after the limit.
        assert vlm.extract_from_page_images.call_count == VISION_FAILURE_LIMIT

    def test_the_error_is_not_the_download_hint(self, tmp_path):
        with pytest.raises(VisionModelFailedError) as raised:
            _extract_pdf(tmp_path, _scan(tmp_path / "scan.pdf", 3), _vlm(False))

        assert not isinstance(raised.value, EmptyPDFError)
        assert "download" not in str(raised.value).lower()

    def test_one_bad_image_before_good_ones_is_a_degraded_page(self, tmp_path):
        text, _pages, metadata = _extract_pdf(
            tmp_path, _scan(tmp_path / "scan.pdf", 3), _vlm(False, True)
        )

        assert metadata["pdf_status"] == "degraded"
        assert metadata["degraded_pages"] == [1]
        assert CRASH in metadata["page_warnings"][1]
        assert "read page 2" in text and "read page 3" in text

    def test_a_model_that_has_read_a_page_is_not_given_up_on(self, tmp_path):
        text, _pages, metadata = _extract_pdf(
            tmp_path, _scan(tmp_path / "scan.pdf", 4), _vlm(True, False)
        )

        assert "read page 1" in text
        assert metadata["degraded_pages"] == [2, 3, 4]


class TestIndexDocument:
    def test_the_failure_reaches_the_caller_with_its_own_type(self, tmp_path):
        pdf = _scan(tmp_path / "scan.pdf", 5)
        sdk = _sdk(tmp_path)
        a, b = _patched(_vlm(False))
        with a, b:
            stats = sdk.index_document(str(pdf))

        assert not stats.get("success")
        assert stats["error_type"] == "vision_model_failed"
        assert "vision-model" in stats["error"]
        assert str(pdf.resolve()) not in sdk.indexed_files


pptx = pytest.importorskip("pptx")
from pptx.util import Inches  # noqa: E402


def _deck(path: Path, slides: int) -> Path:
    prs = pptx.Presentation()
    for _ in range(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_picture(io.BytesIO(_png()), Inches(1), Inches(1))
    prs.save(str(path))
    return path


class TestAPowerPoint:
    def test_a_model_that_fails_every_slide_stops_with_its_name(self, tmp_path):
        vlm = _vlm(False)
        deck = _deck(tmp_path / "deck.pptx", 6)
        a, b = _patched(vlm)
        with (
            a,
            b,
            patch(
                "gaia.rag.pptx_utils.convert_pptx_to_pdf",
                side_effect=RuntimeError("no PowerPoint here"),
            ),
            pytest.raises(VisionModelFailedError) as raised,
        ):
            _sdk(tmp_path)._extract_text_from_pptx(str(deck))

        assert "vision-model" in str(raised.value)
        assert vlm.extract_from_page_images.call_count == VISION_FAILURE_LIMIT

    def test_the_converted_pdf_path_does_not_fall_back_and_retry(self, tmp_path):
        vlm = _vlm(False)
        deck = _deck(tmp_path / "deck.pptx", 6)
        converted = _scan(tmp_path / "converted.pdf", 6)
        a, b = _patched(vlm)
        with (
            a,
            b,
            patch(
                "gaia.rag.pptx_utils.convert_pptx_to_pdf",
                return_value=str(converted),
            ),
            pytest.raises(VisionModelFailedError),
        ):
            _sdk(tmp_path)._extract_text_from_pptx(str(deck))

        # The python-pptx fallback would ask the same broken model again.
        assert vlm.extract_from_page_images.call_count == VISION_FAILURE_LIMIT
