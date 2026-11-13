# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Images left unread while the vision model was down are read once it's back (#4799).

A PDF indexed without the vision model cached its text-only extraction for
good: every later index reused it, so charts and scans stayed unsearchable
until the cache was cleared, and nothing told the user.
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
pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")

from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from gaia.rag.sdk import RAGSDK, RAGConfig  # noqa: E402

DIM = 8
REPORT = "Net revenue rose across the group this year. " * 12


def _vector(text: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "little")
    return np.random.default_rng(seed).random(DIM).astype("float32")


def _pdf(path: Path, *page_texts: str, picture: bool = True) -> Path:
    """One page per text, each with the same picture unless *picture* is False."""
    image = io.BytesIO()
    Image.new("RGB", (400, 300), "gray").save(image, format="PNG")
    c = canvas.Canvas(str(path), pagesize=(600, 800))
    for text in page_texts:
        lines = c.beginText(40, 760)
        lines.setFont("Helvetica", 8)
        for start in range(0, len(text), 100):
            lines.textLine(text[start : start + 100])
        c.drawText(lines)
        if picture:
            image.seek(0)
            c.drawImage(ImageReader(image), 40, 80, width=400, height=300)
        c.showPage()
    c.save()
    return path


def _vlm(up: bool) -> MagicMock:
    vlm = MagicMock()
    vlm.check_availability.return_value = up
    vlm.extract_from_page_images.side_effect = lambda images, page_num: [
        {
            "image_num": 1,
            "text": f"chart on page {page_num}",
            "dimensions": "1x1",
            "size_kb": 1,
        }
    ]
    return vlm


def _sdk(tmp_path: Path) -> RAGSDK:
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                vlm_model="vision-model",
            )
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **kw: (
        np.stack([_vector(t) for t in texts]) if texts else np.zeros((0, DIM))
    )
    sdk._get_hmac_key = lambda: b"test-index-key" * 3
    return sdk


def _index(
    tmp_path: Path, pdf: Path, vlm: MagicMock, sdk: RAGSDK | None = None
) -> tuple[dict, RAGSDK]:
    """Index *pdf*, in a fresh SDK sharing one cache (a new session) unless given one."""
    sdk = sdk or _sdk(tmp_path)
    with (
        patch("gaia.llm.VLMClient", return_value=vlm),
        patch("gaia.rag.sdk.resident_vision_model", return_value="vision-model"),
    ):
        result = sdk.index_document(str(pdf))
    assert result["success"], result
    return result, sdk


def test_the_index_result_says_which_images_went_unread(tmp_path):
    pdf = _pdf(tmp_path / "q3.pdf", REPORT, "Figure 1")

    result, _ = _index(tmp_path, pdf, _vlm(up=False))

    # Page 1 has enough text that its picture would be skipped anyway.
    assert result["unread_image_pages"] == 1
    assert "1 page(s) was not read" in result["unread_images_note"]
    assert "vision-model" in result["unread_images_note"]


def test_once_the_vision_model_is_up_the_cached_file_is_read_again(tmp_path):
    pdf = _pdf(tmp_path / "q3.pdf", REPORT, "Figure 1")
    _index(tmp_path, pdf, _vlm(up=False))

    vlm = _vlm(up=True)
    result, sdk = _index(tmp_path, pdf, vlm)

    assert not result.get("from_cache")
    assert "unread_image_pages" not in result
    assert any("chart on page 2" in chunk for chunk in sdk.chunks)

    # The new cache holds the image text, so the next session just loads it.
    again = _vlm(up=True)
    result, sdk = _index(tmp_path, pdf, again)
    assert result["from_cache"]
    again.check_availability.assert_not_called()
    assert any("chart on page 2" in chunk for chunk in sdk.chunks)


def test_in_the_same_session_indexing_again_reads_the_images(tmp_path):
    pdf = _pdf(tmp_path / "q3.pdf", REPORT, "Figure 1")
    _, sdk = _index(tmp_path, pdf, _vlm(up=False))

    result, sdk = _index(tmp_path, pdf, _vlm(up=True), sdk)

    assert not result.get("already_indexed")
    assert result["reindexed"]
    assert "unread_image_pages" not in result
    assert any("chart on page 2" in chunk for chunk in sdk.chunks)
    # The old text was replaced, not left beside the new.
    assert len(sdk.indexed_files) == 1
    assert len(sdk.chunks) == len(sdk.file_to_chunk_indices[str(pdf.absolute())])


def test_in_the_same_session_with_the_model_still_down_it_stays_indexed(tmp_path):
    pdf = _pdf(tmp_path / "q3.pdf", REPORT, "Figure 1")
    _, sdk = _index(tmp_path, pdf, _vlm(up=False))

    vlm = _vlm(up=False)
    result, _ = _index(tmp_path, pdf, vlm, sdk)

    assert result["already_indexed"]
    vlm.extract_from_page_images.assert_not_called()


def test_while_the_vision_model_is_still_down_the_cache_is_used_and_still_flagged(
    tmp_path,
):
    pdf = _pdf(tmp_path / "q3.pdf", REPORT, "Figure 1")
    _index(tmp_path, pdf, _vlm(up=False))

    vlm = _vlm(up=False)
    result, _ = _index(tmp_path, pdf, vlm)

    assert result["from_cache"]
    assert result["unread_image_pages"] == 1
    vlm.extract_from_page_images.assert_not_called()


def test_a_file_with_no_unread_images_is_not_read_again(tmp_path):
    pdf = _pdf(tmp_path / "plain.pdf", REPORT, picture=False)
    first, _ = _index(tmp_path, pdf, _vlm(up=False))
    assert "unread_image_pages" not in first

    vlm = _vlm(up=True)
    result, _ = _index(tmp_path, pdf, vlm)

    assert result["from_cache"]
    vlm.check_availability.assert_not_called()


def test_with_images_turned_off_nothing_is_flagged_or_read_again(tmp_path):
    pdf = _pdf(tmp_path / "q3.pdf", REPORT, "Figure 1")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(
                cache_dir=str(tmp_path / "cache"),
                allowed_paths=[str(tmp_path)],
                vlm_model="vision-model",
                use_vlm=False,
            )
        )
    with patch("gaia.llm.VLMClient", return_value=_vlm(up=True)):
        _text, _pages, metadata = sdk._extract_text_from_pdf(str(pdf))

    assert metadata["unread_image_pages"] == 0
    assert sdk._images_readable_now(metadata) is False


def test_a_powerpoint_keeps_its_unread_count_in_the_cache_record(tmp_path):
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"stub")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(cache_dir=str(tmp_path / "cache"), allowed_paths=[str(tmp_path)])
        )
    extracted = {
        "vlm_slides": 0,
        "total_images": 0,
        "vlm_checked": True,
        "vlm_available": False,
        "unread_image_pages": 3,
    }
    with patch.object(
        sdk, "_extract_text_from_pptx", return_value=("[Page 1] Agenda", 3, extracted)
    ):
        _text, metadata = sdk._extract_text_from_file(str(deck))

    assert metadata["vlm_available"] is False
    assert metadata["unread_image_pages"] == 3
