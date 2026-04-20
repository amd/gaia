# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for PDF extraction error handling in gaia.rag.sdk (#451).

Covers the three failure modes RAGSDK._extract_text_from_pdf must surface
with actionable guidance:

- Encrypted / password-protected PDFs  -> EncryptedPDFError
- Corrupted / truncated PDFs           -> CorruptedPDFError
- PDFs with no extractable text        -> EmptyPDFError

Fixtures are built programmatically with pypdf so the tests remain hermetic
and don't require committing binary fixture files to the repo.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pypdf = pytest.importorskip("pypdf")

from pypdf import PdfWriter  # noqa: E402

from gaia.rag.sdk import (  # noqa: E402
    RAGSDK,
    CorruptedPDFError,
    EmptyPDFError,
    EncryptedPDFError,
    PDFExtractionError,
    RAGConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rag(tmp_path: Path) -> RAGSDK:
    """
    A RAGSDK instance scoped to tmp_path with heavy ML deps stubbed out.

    The unit under test (`_extract_text_from_pdf` and the error-handling
    branch of `index_document`) does not need sentence-transformers, faiss,
    or a live Lemonade server, so we bypass the import-time dependency
    check and skip chat/LLM initialization. We also stub the VLM client so
    the blank-PDF test doesn't spend ~4s timing out against localhost:8000.
    """
    config = RAGConfig(
        cache_dir=str(tmp_path / ".gaia"),
        show_stats=False,
        use_local_llm=False,
    )

    fake_vlm = MagicMock(name="VLMClient")
    fake_vlm.check_availability.return_value = False

    with (
        patch.object(RAGSDK, "_check_dependencies", return_value=None),
        patch("gaia.rag.sdk.AgentSDK", autospec=True) as mock_agent_sdk,
        patch("gaia.llm.VLMClient", return_value=fake_vlm),
    ):
        mock_agent_sdk.return_value = MagicMock(name="AgentSDK")
        instance = RAGSDK(config=config)

    # The VLM import inside _extract_text_from_pdf is lazy, so keep the patch
    # active for the lifetime of the test instance via a context wrapper.
    instance._test_vlm_patch = patch("gaia.llm.VLMClient", return_value=fake_vlm)
    instance._test_vlm_patch.start()
    yield instance
    instance._test_vlm_patch.stop()


def _write_blank_pdf(path: Path) -> None:
    """Write a minimal valid single-page PDF with no text content."""
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(path, "wb") as f:
        writer.write(f)


def _write_encrypted_pdf(path: Path, password: str = "hunter2") -> None:
    """Write a password-protected PDF with a single blank page."""
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt(user_password=password, owner_password=password + "-owner")
    with open(path, "wb") as f:
        writer.write(f)


def _write_corrupted_pdf(path: Path) -> None:
    """
    Write an obviously invalid PDF — bytes that don't match the PDF header.
    This triggers pypdf's PdfStreamError/PdfReadError at construction time.
    """
    path.write_bytes(b"%PDF-1.7\nnot actually a pdf, just garbage\n")


def _write_truncated_pdf(path: Path) -> None:
    """Write a valid PDF then truncate it to simulate a corrupted download."""
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with open(path, "wb") as f:
        writer.write(f)
    data = path.read_bytes()
    # Keep only the first third — far past the header, well before EOF marker.
    path.write_bytes(data[: max(10, len(data) // 3)])


# ---------------------------------------------------------------------------
# Encrypted PDFs
# ---------------------------------------------------------------------------


class TestEncryptedPDF:
    def test_raises_encrypted_error(self, rag: RAGSDK, tmp_path: Path) -> None:
        pdf = tmp_path / "secret.pdf"
        _write_encrypted_pdf(pdf)

        with pytest.raises(EncryptedPDFError) as excinfo:
            rag._extract_text_from_pdf(str(pdf))

        msg = str(excinfo.value)
        # Actionable remediation must be present so users know how to recover.
        assert "password-protected" in msg
        assert "qpdf" in msg or "pdftk" in msg
        # File name should be in the message (not just the absolute path).
        assert "secret.pdf" in msg

    def test_is_value_error_subclass(self) -> None:
        """Existing `except ValueError:` sites keep working (backward-compat)."""
        assert issubclass(EncryptedPDFError, ValueError)
        assert issubclass(EncryptedPDFError, PDFExtractionError)

    def test_status_code(self) -> None:
        assert EncryptedPDFError.status == "encrypted"

    def test_index_document_surfaces_encrypted_status(
        self, rag: RAGSDK, tmp_path: Path
    ) -> None:
        """index_document() should convert the exception to a stats dict."""
        pdf = tmp_path / "locked.pdf"
        _write_encrypted_pdf(pdf)

        stats = rag.index_document(str(pdf))

        assert stats["success"] is False
        assert stats.get("pdf_status") == "encrypted"
        assert "password-protected" in stats["error"]


# ---------------------------------------------------------------------------
# Corrupted PDFs
# ---------------------------------------------------------------------------


class TestCorruptedPDF:
    def test_raises_corrupted_error_on_garbage(
        self, rag: RAGSDK, tmp_path: Path
    ) -> None:
        pdf = tmp_path / "garbage.pdf"
        _write_corrupted_pdf(pdf)

        with pytest.raises(CorruptedPDFError) as excinfo:
            rag._extract_text_from_pdf(str(pdf))

        msg = str(excinfo.value)
        assert "corrupted" in msg.lower() or "not a valid pdf" in msg.lower()
        assert "garbage.pdf" in msg

    def test_raises_corrupted_error_on_truncated(
        self, rag: RAGSDK, tmp_path: Path
    ) -> None:
        pdf = tmp_path / "truncated.pdf"
        _write_truncated_pdf(pdf)

        with pytest.raises(CorruptedPDFError):
            rag._extract_text_from_pdf(str(pdf))

    def test_status_code(self) -> None:
        assert CorruptedPDFError.status == "corrupted"

    def test_chained_from_pypdf_error(self, rag: RAGSDK, tmp_path: Path) -> None:
        """
        The original pypdf exception should be preserved via `raise ... from e`
        so operators can see the underlying failure in logs/tracebacks.
        """
        pdf = tmp_path / "garbage.pdf"
        _write_corrupted_pdf(pdf)

        with pytest.raises(CorruptedPDFError) as excinfo:
            rag._extract_text_from_pdf(str(pdf))

        assert excinfo.value.__cause__ is not None

    def test_index_document_surfaces_corrupted_status(
        self, rag: RAGSDK, tmp_path: Path
    ) -> None:
        pdf = tmp_path / "bad.pdf"
        _write_corrupted_pdf(pdf)

        stats = rag.index_document(str(pdf))

        assert stats["success"] is False
        assert stats.get("pdf_status") == "corrupted"


# ---------------------------------------------------------------------------
# Empty / no-text PDFs
# ---------------------------------------------------------------------------


class TestEmptyPDF:
    def test_raises_empty_error_on_blank_pdf(self, rag: RAGSDK, tmp_path: Path) -> None:
        pdf = tmp_path / "blank.pdf"
        _write_blank_pdf(pdf)

        with pytest.raises(EmptyPDFError) as excinfo:
            rag._extract_text_from_pdf(str(pdf))

        msg = str(excinfo.value)
        # Message should hint at OCR as the remediation path — otherwise users
        # see "no text" and have no idea how to proceed.
        assert "OCR" in msg or "ocr" in msg.lower() or "ocrmypdf" in msg.lower()
        assert "blank.pdf" in msg

    def test_status_code(self) -> None:
        assert EmptyPDFError.status == "empty"

    def test_index_document_surfaces_empty_status(
        self, rag: RAGSDK, tmp_path: Path
    ) -> None:
        pdf = tmp_path / "scan.pdf"
        _write_blank_pdf(pdf)

        stats = rag.index_document(str(pdf))

        assert stats["success"] is False
        assert stats.get("pdf_status") == "empty"


# ---------------------------------------------------------------------------
# Regression guard — the error classes should not swallow each other
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_all_errors_inherit_pdfextractionerror(self) -> None:
        assert issubclass(EncryptedPDFError, PDFExtractionError)
        assert issubclass(CorruptedPDFError, PDFExtractionError)
        assert issubclass(EmptyPDFError, PDFExtractionError)

    def test_pdfextractionerror_is_valueerror(self) -> None:
        """
        Kept as a ValueError so pre-existing `except ValueError:` blocks
        (including callers in index_document and reindex_document) stay
        compatible after this change.
        """
        assert issubclass(PDFExtractionError, ValueError)

    def test_distinct_status_codes(self) -> None:
        statuses = {
            EncryptedPDFError.status,
            CorruptedPDFError.status,
            EmptyPDFError.status,
        }
        assert statuses == {"encrypted", "corrupted", "empty"}
