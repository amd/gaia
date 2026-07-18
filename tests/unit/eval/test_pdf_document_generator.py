# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.eval.pdf_document_generator.PDFDocumentGenerator``.

Mocks the Claude boundary (``ClaudeClient``) so no ``ANTHROPIC_API_KEY`` or
network access is required. Generated PDFs are validated by actually opening
them with ``pypdf`` — a generation bug here would quietly invalidate every
downstream eval that consumes these synthetic fixtures.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")

from gaia.eval.pdf_document_generator import PDFDocumentGenerator  # noqa: E402


def _usage_response(content, input_tokens=100, output_tokens=200):
    return {
        "content": content,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        "cost": {
            "input_cost": round(input_tokens / 1_000_000 * 3.0, 6),
            "output_cost": round(output_tokens / 1_000_000 * 15.0, 6),
            "total_cost": round(
                input_tokens / 1_000_000 * 3.0 + output_tokens / 1_000_000 * 15.0, 6
            ),
        },
    }


@pytest.fixture
def mock_claude_client(mocker):
    """Patch ClaudeClient so PDFDocumentGenerator never touches the network."""
    mock_cls = mocker.patch("gaia.eval.pdf_document_generator.ClaudeClient")
    instance = mock_cls.return_value
    instance.model = "test-claude-model"
    return instance


@pytest.fixture
def generator(mock_claude_client):
    return PDFDocumentGenerator()


def _open_pdf(path):
    """Open and sanity-check a generated PDF, returning the pypdf reader."""
    assert path.exists()
    reader = pypdf.PdfReader(str(path))
    assert len(reader.pages) >= 1
    return reader


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init_raises_valueerror_when_claude_client_fails(mocker):
    mocker.patch(
        "gaia.eval.pdf_document_generator.ClaudeClient",
        side_effect=Exception("ANTHROPIC_API_KEY not found"),
    )

    with pytest.raises(ValueError, match="Could not initialize Claude client"):
        PDFDocumentGenerator()


def test_init_success_sets_up_document_templates(generator):
    assert "technical_spec" in generator.document_templates
    assert "financial_report" in generator.document_templates
    assert len(generator.document_templates) == 8


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_is_roughly_four_chars_per_token(generator):
    assert generator._estimate_tokens("a" * 400) == 100
    assert generator._estimate_tokens("") == 0


# ---------------------------------------------------------------------------
# generate_document
# ---------------------------------------------------------------------------


def test_generate_document_unknown_doc_type_raises(generator):
    with pytest.raises(ValueError, match="Unknown document type"):
        generator.generate_document("not_a_real_type")


def test_generate_document_returns_content_and_metadata(generator, mock_claude_client):
    long_content = "Technical Spec\n\n" + ("Realistic section content. " * 100)
    mock_claude_client.get_completion_with_usage.return_value = _usage_response(
        long_content
    )

    content, metadata = generator.generate_document("technical_spec", target_tokens=500)

    assert content == long_content
    assert metadata["doc_type"] == "technical_spec"
    assert metadata["target_tokens"] == 500
    assert metadata["claude_model"] == "test-claude-model"
    assert metadata["claude_usage"]["total_tokens"] == 300
    assert metadata["claude_cost"]["total_cost"] > 0
    assert (
        metadata["sections"]
        == generator.document_templates["technical_spec"]["sections"]
    )
    # Content was long enough (>= 80% of target) — no extension call made.
    mock_claude_client.get_completion_with_usage.assert_called_once()


def test_generate_document_extends_when_too_short(generator, mock_claude_client):
    short_content = "Too short."  # ~2 tokens, far below 80% of any real target
    extension_content = "Additional generated content. " * 50

    mock_claude_client.get_completion_with_usage.side_effect = [
        _usage_response(short_content, input_tokens=50, output_tokens=10),
        _usage_response(extension_content, input_tokens=60, output_tokens=400),
    ]

    content, metadata = generator.generate_document("white_paper", target_tokens=500)

    assert content.startswith(short_content)
    assert extension_content in content
    assert mock_claude_client.get_completion_with_usage.call_count == 2
    # Usage/cost from both calls accumulated.
    assert metadata["claude_usage"]["input_tokens"] == 110
    assert metadata["claude_usage"]["output_tokens"] == 410


def test_generate_document_propagates_claude_failure(generator, mock_claude_client):
    mock_claude_client.get_completion_with_usage.side_effect = Exception("api down")

    with pytest.raises(RuntimeError, match="Failed to generate document"):
        generator.generate_document("research_report", target_tokens=500)


def test_extend_content_skips_call_when_already_at_target(
    generator, mock_claude_client
):
    content, usage, cost = generator._extend_content_with_claude(
        base_content="x" * 4000,  # 1000 tokens, already >= target
        target_tokens=500,
        doc_type="technical_spec",
        current_usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        current_cost={"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
    )

    assert content == "x" * 4000
    mock_claude_client.get_completion_with_usage.assert_not_called()


def test_extend_content_returns_original_on_failure(generator, mock_claude_client):
    mock_claude_client.get_completion_with_usage.side_effect = Exception("boom")
    base_usage = {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    base_cost = {"input_cost": 0.1, "output_cost": 0.2, "total_cost": 0.3}

    content, usage, cost = generator._extend_content_with_claude(
        base_content="short",
        target_tokens=500,
        doc_type="technical_spec",
        current_usage=base_usage,
        current_cost=base_cost,
    )

    assert content == "short"
    assert usage == base_usage
    assert cost == base_cost


# ---------------------------------------------------------------------------
# _create_pdf_from_content — direct PDF generation + validity checks
# ---------------------------------------------------------------------------


def test_create_pdf_from_content_produces_valid_pdf_with_headings(generator, tmp_path):
    content = (
        "OVERVIEW:\n"
        "This document describes the system.\n"
        "\n"
        "# Markdown Heading\n"
        "Body text under the markdown heading.\n"
        "\n"
        "ALL CAPS HEADING\n"
        "Final paragraph of body content.\n"
    )
    output_path = tmp_path / "doc.pdf"

    generator._create_pdf_from_content(content, output_path, "Test Document")

    reader = _open_pdf(output_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Test Document" in text
    assert "This document describes the system." in text
    assert "Body text under the markdown heading." in text


def test_create_pdf_from_content_empty_content_still_produces_valid_pdf(
    generator, tmp_path
):
    output_path = tmp_path / "empty.pdf"

    generator._create_pdf_from_content("", output_path, "Empty Document")

    reader = _open_pdf(output_path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Empty Document" in text


# ---------------------------------------------------------------------------
# generate_document_set — end-to-end into tmp_path
# ---------------------------------------------------------------------------


def test_generate_document_set_writes_pdfs_and_manifest(
    generator, mock_claude_client, tmp_path
):
    generator.document_templates = {
        "technical_spec": generator.document_templates["technical_spec"]
    }
    long_content = "Section content. " * 100
    mock_claude_client.get_completion_with_usage.return_value = _usage_response(
        long_content
    )

    result = generator.generate_document_set(
        tmp_path, target_tokens=500, count_per_type=1
    )

    pdfs_dir = tmp_path / "pdfs"
    pdf_path = pdfs_dir / "technical_spec.pdf"
    txt_path = pdfs_dir / "technical_spec.txt"
    manifest_path = pdfs_dir / "pdf_metadata.json"

    _open_pdf(pdf_path)
    assert txt_path.read_text(encoding="utf-8") == long_content
    assert result["output_directory"] == str(pdfs_dir)
    assert result["generated_files"] == [str(pdf_path)]
    assert result["metadata_file"] == str(manifest_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["generation_info"]["total_files"] == 1
    assert manifest["generation_info"]["document_types"] == ["technical_spec"]
    assert manifest["generation_info"]["total_claude_usage"]["total_tokens"] == 300
    assert len(manifest["documents"]) == 1
    doc_meta = manifest["documents"][0]
    assert doc_meta["pdf_filename"] == "technical_spec.pdf"
    assert doc_meta["txt_filename"] == "technical_spec.txt"
    assert doc_meta["file_size_bytes"] == len(long_content.encode("utf-8"))


def test_generate_document_set_multiple_per_type_uses_indexed_filenames(
    generator, mock_claude_client, tmp_path
):
    generator.document_templates = {
        "technical_spec": generator.document_templates["technical_spec"]
    }
    long_content = "Section content. " * 100
    mock_claude_client.get_completion_with_usage.return_value = _usage_response(
        long_content
    )

    result = generator.generate_document_set(
        tmp_path, target_tokens=500, count_per_type=2
    )

    pdfs_dir = tmp_path / "pdfs"
    assert (pdfs_dir / "technical_spec_1.pdf").exists()
    assert (pdfs_dir / "technical_spec_2.pdf").exists()
    assert len(result["generated_files"]) == 2
    for pdf_file in result["generated_files"]:
        _open_pdf(Path(pdf_file))
