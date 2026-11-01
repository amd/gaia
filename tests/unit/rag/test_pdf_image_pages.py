# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Indexing reads images only on pages with little text of their own."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from gaia.rag.sdk import IMAGE_PAGE_TEXT_LIMIT, RAGSDK, RAGConfig

fitz = pytest.importorskip("pymupdf")


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (400, 300), "gray").save(out, format="PNG")
    return out.getvalue()


def _pdf(path: Path, *page_texts: str) -> Path:
    """One page per text, each holding the same picture."""
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(40, 40, 560, 300), text, fontsize=8)
        page.insert_image(fitz.Rect(40, 320, 440, 620), stream=_png())
    doc.save(path)
    doc.close()
    return path


def _extract(tmp_path: Path, pdf: Path, **config):
    vlm = MagicMock()
    vlm.check_availability.return_value = True
    vlm.extract_from_page_images.side_effect = lambda images, page_num: [
        {
            "image_num": 1,
            "text": f"read page {page_num}",
            "dimensions": "1x1",
            "size_kb": 1,
        }
    ]
    sdk = RAGSDK(
        RAGConfig(
            cache_dir=str(tmp_path / ".gaia"),
            show_stats=False,
            read_images_later=False,
            **config,
        )
    )
    with (
        patch("gaia.llm.VLMClient", return_value=vlm),
        patch("gaia.rag.sdk.resident_vision_model", return_value="vision-model"),
    ):
        text, _pages, metadata = sdk._extract_text_from_pdf(str(pdf))
    return vlm, text, metadata


def test_only_the_page_short_of_text_goes_to_the_vision_model(tmp_path):
    report = "Net revenue rose across the group this year. " * 12
    pdf = _pdf(tmp_path / "mixed.pdf", report, "Figure 1")

    vlm, text, metadata = _extract(tmp_path, pdf)

    assert [c.kwargs["page_num"] for c in vlm.extract_from_page_images.mock_calls] == [
        2
    ]
    assert "read page 2" in text
    assert "read page 1" not in text
    assert metadata["image_pages_skipped"] == 1


def test_a_scan_with_no_text_is_read(tmp_path):
    vlm, text, metadata = _extract(tmp_path, _pdf(tmp_path / "scan.pdf", ""))

    assert "read page 1" in text
    assert metadata["image_pages_skipped"] == 0


def test_the_limit_is_on_the_page_text_not_the_whole_document(tmp_path):
    long_page = "x" * (IMAGE_PAGE_TEXT_LIMIT + 50)
    short_page = "x" * (IMAGE_PAGE_TEXT_LIMIT - 50)
    pdf = _pdf(tmp_path / "edge.pdf", " ".join(long_page), short_page)

    vlm, _text, _metadata = _extract(tmp_path, pdf)

    assert vlm.extract_from_page_images.call_count == 1
