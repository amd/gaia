# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""document_scripts() names the writing systems an indexed file is written in."""

from unittest.mock import patch

from gaia.rag.sdk import RAGSDK, RAGConfig


def _sdk(chunks_by_file):
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(RAGConfig(use_llm_chunking=False))
    for path, chunks in chunks_by_file.items():
        start = len(sdk.chunks)
        sdk.chunks.extend(chunks)
        sdk.file_to_chunk_indices[path] = list(range(start, start + len(chunks)))
    return sdk


def test_arabic_report_with_some_english_lists_arabic_first():
    sdk = _sdk({"/r.pdf": ["صافي الدين 12.4 مليار درهم", "Group revenue AED"]})
    assert sdk.document_scripts("/r.pdf") == ["Arabic", "Latin"]


def test_minor_scripts_under_ten_percent_are_ignored():
    sdk = _sdk({"/t.pdf": ["The Customer shall pay all charges " * 20 + "شركة"]})
    assert sdk.document_scripts("/t.pdf") == ["Latin"]


def test_presentation_form_letters_count_as_arabic():
    sdk = _sdk({"/p.pdf": ["\ufee3\ufee0\ufef4\ufe8e\ufead"]})
    assert sdk.document_scripts("/p.pdf") == ["Arabic"]


def test_unknown_or_letterless_files_have_no_scripts():
    sdk = _sdk({"/n.pdf": ["2025 | 72.9 | %"]})
    assert sdk.document_scripts("/n.pdf") == []
    assert sdk.document_scripts("/missing.pdf") == []
