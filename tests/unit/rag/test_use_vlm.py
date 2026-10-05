# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``RAGConfig.use_vlm=False`` skips image text extraction even with a VLM installed.

The retrieval benchmark's PR gate relies on it: whether a VLM is installed
otherwise changes a PDF's chunks, so two machines would not score alike.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.rag.sdk import RAGSDK, RAGConfig

PDF = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "pdf"
    / "Oil-and-Gas-Activity-Operations-Manual-1-10.pdf"
)


def _sdk(tmp_path, **overrides):
    config = RAGConfig(
        cache_dir=str(tmp_path / "cache"), allowed_paths=[str(PDF.parent)], **overrides
    )
    with patch("gaia.rag.sdk.AgentSDK"):
        return RAGSDK(config)


def test_default_cache_key_is_unchanged_and_vlm_off_gets_its_own(tmp_path):
    default = _sdk(tmp_path)._chunking_fingerprint()
    assert _sdk(tmp_path, use_vlm=True)._chunking_fingerprint() == default
    assert _sdk(tmp_path, use_vlm=False)._chunking_fingerprint() != default


@pytest.mark.skipif(not PDF.is_file(), reason="committed test PDF missing")
def test_vlm_off_never_asks_the_vlm_even_when_it_is_available(tmp_path):
    vlm = MagicMock()
    vlm.check_availability.return_value = True
    with patch("gaia.llm.VLMClient", return_value=vlm):
        text, pages, metadata = _sdk(tmp_path, use_vlm=False)._extract_text_from_pdf(
            str(PDF)
        )
    vlm.check_availability.assert_not_called()
    vlm.extract_from_page_images.assert_not_called()
    assert metadata["vlm_available"] is False
    assert pages == 10 and "[Page 1]" in text
