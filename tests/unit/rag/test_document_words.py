# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""document_words() lists the words an indexed file holds."""

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


def test_words_of_four_or_more_letters_lower_cased():
    sdk = _sdk({"/h.md": ["Contractors are NOT eligible.", "Dental: 30 days"]})
    assert sdk.document_words("/h.md") == {"contractors", "eligible", "dental", "days"}


def test_an_unknown_file_has_no_words():
    assert _sdk({}).document_words("/missing.md") == frozenset()


def test_a_reindexed_file_is_read_again():
    sdk = _sdk({"/h.md": ["vision benefits"]})
    assert sdk.document_words("/h.md") == {"vision", "benefits"}
    sdk.chunks.append("payroll schedule")
    sdk.file_to_chunk_indices["/h.md"] = [1]
    assert sdk.document_words("/h.md") == {"payroll", "schedule"}
