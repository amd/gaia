# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.apps.summarize.pdf_formatter.PDFFormatter``.

Exercises every formatting branch (single vs. multi-style summaries,
performance tables, original-content sections, empty/edge inputs) and
validates output by actually opening the generated PDFs with ``pypdf`` — the
covered ``SummarizerApp`` tests never invoke this renderer directly (#2003),
so a crash or garbled export here would otherwise ship unnoticed.
"""

import pytest

pytest.importorskip("reportlab")
pypdf = pytest.importorskip("pypdf")

import gaia.apps.summarize.pdf_formatter as pdf_formatter_module  # noqa: E402
from gaia.apps.summarize.pdf_formatter import PDFFormatter  # noqa: E402


@pytest.fixture
def formatter():
    return PDFFormatter()


def _pdf_text(path):
    assert path.exists()
    reader = pypdf.PdfReader(str(path))
    assert len(reader.pages) >= 1
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init_raises_import_error_without_reportlab(monkeypatch):
    monkeypatch.setattr(pdf_formatter_module, "HAS_REPORTLAB", False)

    with pytest.raises(ImportError, match="reportlab"):
        PDFFormatter()


def test_init_success_registers_custom_styles(formatter):
    for style_name in ("CustomTitle", "SectionHeader", "Metadata"):
        assert style_name in formatter.styles


# ---------------------------------------------------------------------------
# format_summary_as_pdf — single-style summary
# ---------------------------------------------------------------------------


def test_format_single_summary_with_text_items_and_participants(formatter, tmp_path):
    result = {
        "metadata": {
            "input_file": "/tmp/meeting_notes.txt",
            "input_type": "meeting",
            "timestamp": "2026-01-01T00:00:00",
            "model": "test-model",
            "processing_time_ms": 1234,
        },
        "summary": {
            "text": "Line one.\nLine two.",
            "items": ["Action item A", "Action item B"],
            "participants": ["Alice", "Bob"],
        },
    }
    output_path = tmp_path / "single.pdf"

    formatter.format_summary_as_pdf(result, output_path)

    text = _pdf_text(output_path)
    assert "meeting_notes.txt" in text
    assert "Action item A" in text
    assert "Alice" in text


def test_format_single_summary_missing_metadata_uses_defaults(formatter, tmp_path):
    result = {"summary": {"text": "Just a summary."}}
    output_path = tmp_path / "no_metadata.pdf"

    formatter.format_summary_as_pdf(result, output_path)

    text = _pdf_text(output_path)
    assert "Unknown" in text
    assert "Just a summary." in text


# ---------------------------------------------------------------------------
# format_summary_as_pdf — multi-style summaries
# ---------------------------------------------------------------------------


def test_format_multiple_summaries_dict_content_all_fields(formatter, tmp_path):
    result = {
        "metadata": {"input_file": "email.txt", "input_type": "email"},
        "summaries": {
            "brief": {"text": "Short version."},
            "detailed": {
                "items": ["point one", "point two"],
                "participants": [
                    {"name": "Carol", "role": "Engineer"},
                    "Plain Name",
                ],
                "sender": "carol@example.com",
                "recipients": ["dave@example.com", "erin@example.com"],
            },
        },
    }
    output_path = tmp_path / "multi.pdf"

    formatter.format_summary_as_pdf(result, output_path)

    text = _pdf_text(output_path)
    assert "Short version." in text
    assert "point one" in text
    assert "Carol" in text
    assert "Engineer" in text
    assert "Plain Name" in text
    assert "carol@example.com" in text
    assert "dave@example.com" in text


def test_format_multiple_summaries_string_content(formatter, tmp_path):
    result = {
        "summaries": {"quick": "Just a plain string summary."},
    }
    output_path = tmp_path / "string_summary.pdf"

    formatter.format_summary_as_pdf(result, output_path)

    text = _pdf_text(output_path)
    assert "Just a plain string summary." in text


# ---------------------------------------------------------------------------
# format_summary_as_pdf — performance section
# ---------------------------------------------------------------------------


def test_format_with_performance_metrics(formatter, tmp_path):
    result = {
        "summary": {"text": "Body."},
        "performance": {
            "total_tokens": 500,
            "prompt_tokens": 300,
            "completion_tokens": 200,
            "time_to_first_token_ms": 42,
            "tokens_per_second": 12.5,
            "processing_time_ms": 800,
        },
        "metadata": {"model": "test-model", "use_local_llm": True},
    }
    output_path = tmp_path / "performance.pdf"

    formatter.format_summary_as_pdf(result, output_path)

    text = _pdf_text(output_path)
    assert "Performance Metrics" in text
    assert "test-model" in text
    assert "500" in text


def test_format_with_aggregate_performance_fallback(formatter, tmp_path):
    result = {
        "summary": {"text": "Body."},
        "aggregate_performance": {
            "model_info": {"model": "aggregate-model", "local_llm": False},
            "total_tokens": 999,
        },
    }
    output_path = tmp_path / "aggregate_performance.pdf"

    formatter.format_summary_as_pdf(result, output_path)

    text = _pdf_text(output_path)
    assert "aggregate-model" in text
    assert "999" in text


# ---------------------------------------------------------------------------
# format_summary_as_pdf — original content section
# ---------------------------------------------------------------------------


def test_format_with_original_content_splits_paragraphs(formatter, tmp_path):
    result = {
        "summary": {"text": "Summary text."},
        "original_content": "Para one.\n\nPara two.\n\n\n\nPara three.",
    }
    output_path = tmp_path / "original_content.pdf"

    formatter.format_summary_as_pdf(result, output_path)

    text = _pdf_text(output_path)
    assert "Original Content" in text
    assert "Para one." in text
    assert "Para three." in text


# ---------------------------------------------------------------------------
# format_summary_as_pdf — empty / edge inputs
# ---------------------------------------------------------------------------


def test_format_empty_result_does_not_crash(formatter, tmp_path):
    output_path = tmp_path / "empty.pdf"

    formatter.format_summary_as_pdf({}, output_path)

    text = _pdf_text(output_path)
    assert "Unknown" in text


def test_format_summary_with_empty_items_and_participants(formatter, tmp_path):
    result = {"summary": {"items": [], "participants": []}}
    output_path = tmp_path / "empty_lists.pdf"

    # Should not raise even though items/participants are present but empty.
    formatter.format_summary_as_pdf(result, output_path)
    assert output_path.exists()


# ---------------------------------------------------------------------------
# _add_text_with_newlines
# ---------------------------------------------------------------------------


def test_add_text_with_newlines_converts_to_br(formatter):
    story = []
    formatter._add_text_with_newlines(story, "line1\nline2")

    assert len(story) == 1
    assert story[0].text == "line1<br/>line2"


@pytest.mark.parametrize("empty_value", [None, ""])
def test_add_text_with_newlines_skips_falsy_text(formatter, empty_value):
    story = []
    formatter._add_text_with_newlines(story, empty_value)

    assert story == []
