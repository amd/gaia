# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A PDF with arrays past pypdf's 100,000-element default still indexes.

Microsoft's FY2025 10-K has one, and failed with pypdf's bare "Reached maximum
array element count." GAIA raises that cap to a bounded higher one, and a file
past even that fails with a message that says what to do.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, NameObject, NumberObject
from reportlab.pdfgen import canvas

from gaia.rag import pdf_utils
from gaia.rag.sdk import RAGSDK, PDFExtractionError, RAGConfig

_CORPUS = Path(os.environ.get("GAIA_REAL_CORPUS", "C:/gtest/mdqa/real/docs"))
_MSFT_10K = _CORPUS / "finance" / "microsoft_10k_fy2025.pdf"


def _pdf_with_large_array(path: Path, elements: int) -> Path:
    """A one-page PDF of text whose page carries an *elements*-long array."""
    text_pdf = path.with_suffix(".text.pdf")
    c = canvas.Canvas(str(text_pdf))
    c.drawString(72, 720, "Revenue grew 15 percent to 281.7 billion dollars.")
    c.save()
    writer = PdfWriter(clone_from=str(text_pdf))
    writer.pages[0][NameObject("/GaiaTestArray")] = ArrayObject(
        NumberObject(0) for _ in range(elements)
    )
    writer.write(str(path))
    return path


@pytest.fixture
def rag(tmp_path: Path) -> RAGSDK:
    config = RAGConfig(
        cache_dir=str(tmp_path / ".gaia"),
        show_stats=False,
        use_local_llm=False,
        use_vlm=False,
        allowed_paths=[str(tmp_path)],
    )
    with (
        patch.object(RAGSDK, "_check_dependencies", return_value=None),
        patch("gaia.rag.sdk.AgentSDK", autospec=True),
        patch("gaia.llm.VLMClient", return_value=MagicMock()),
    ):
        yield RAGSDK(config=config)


def test_pdf_with_an_array_past_the_pypdf_default_is_read(rag, tmp_path):
    pdf = _pdf_with_large_array(tmp_path / "filing.pdf", 150_000)

    text, pages, _ = rag._extract_text_from_pdf(str(pdf))

    assert pages == 1
    assert "281.7 billion" in text


def test_pdf_past_the_raised_cap_fails_with_a_plain_message(rag, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_utils, "PDF_MAX_ARRAY_ELEMENTS", 1_000)
    pdf = _pdf_with_large_array(tmp_path / "filing.pdf", 5_000)

    with pytest.raises(PDFExtractionError) as raised:
        rag._extract_text_from_pdf(str(pdf))

    message = str(raised.value)
    assert "filing.pdf" in message
    assert "too complex" in message
    assert "Print" in message
    assert "Reached maximum array element count" not in message.splitlines()[0]


def test_the_raised_cap_does_not_leak_past_the_read(rag, tmp_path):
    from pypdf import get_configuration

    before = get_configuration().array_maximum_element_count
    rag._extract_text_from_pdf(str(_pdf_with_large_array(tmp_path / "a.pdf", 10)))

    assert get_configuration().array_maximum_element_count == before


def test_agent_pdf_reader_reads_an_array_past_the_pypdf_default(tmp_path):
    from gaia.agents.tools.document_text import _pdf_text

    text, info = _pdf_text(str(_pdf_with_large_array(tmp_path / "f.pdf", 150_000)))

    assert "281.7 billion" in text
    assert info == {"pages": 1}


def test_agent_pdf_reader_past_the_cap_says_what_to_do(tmp_path, monkeypatch):
    from gaia.agents.tools.document_text import _pdf_text

    monkeypatch.setattr(pdf_utils, "PDF_MAX_ARRAY_ELEMENTS", 1_000)

    with pytest.raises(ValueError, match=r"f\.pdf is too complex .* print it"):
        _pdf_text(str(_pdf_with_large_array(tmp_path / "f.pdf", 5_000)))


@pytest.mark.skipif(not _MSFT_10K.exists(), reason="real 10-K not on this machine")
def test_real_microsoft_10k_indexes(rag):
    text, pages, _ = rag._extract_text_from_pdf(str(_MSFT_10K))

    assert pages == 158
    assert "Microsoft" in text
