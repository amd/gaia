# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document with no pages is cited without one.

The search tools told the model to cite "page X" for every file, so answers from
a markdown or text file opened with a note explaining there was no page.
"""

from gaia.agents.tools.rag_tools import (
    _chunks_have_pages,
    _documents_instruction,
    _file_instruction,
)


def test_a_chunk_with_a_page_counts_as_paged():
    assert _chunks_have_pages([{"content": "a"}, {"content": "b", "page": 3}])
    assert not _chunks_have_pages([{"content": "a"}, {"content": "b"}])


def test_paged_file_instruction_is_unchanged():
    assert _file_instruction("report.pdf", 4, has_pages=True) == (
        "Use these chunks from report.pdf to answer the question. Read through ALL 4 "
        "chunks completely before answering.\n\nCRITICAL CITATION REQUIREMENT:\n"
        "Your answer MUST start with: 'According to report.pdf, page X:' where X is "
        "the page number from the chunk's 'page' field.\n\nExample: If chunk has "
        "'page': 2, say 'According to report.pdf, page 2:'\nIf info from multiple "
        "pages, say 'According to report.pdf, pages 2 and 5:'"
    )


def test_paged_documents_instruction_is_unchanged():
    assert _documents_instruction(has_pages=True) == (
        "Use the provided document chunks to answer the user's question.\n\n"
        "CRITICAL CITATION REQUIREMENT:\n"
        "When referencing a document, you MUST include its FULL ABSOLUTE FILE PATH "
        "from the 'source_file' field of each chunk. This allows the user to click "
        "and open the file directly.\n\n"
        "Format: 'According to <absolute_path> (page X):'\n"
        "Example: 'According to C:\\Users\\john\\docs\\report.pdf (page 2):'\n"
        "If multiple pages: 'According to C:\\Users\\john\\docs\\report.pdf "
        "(pages 2, 5):'\n\n"
        "IMPORTANT: Always use the full path exactly as given in source_file. "
        "Do NOT shorten it to just the filename."
    )


def test_unpaged_instructions_ask_for_no_page():
    for text in (
        _file_instruction("policy.md", 2, has_pages=False),
        _documents_instruction(has_pages=False),
    ):
        assert "page X" not in text
        assert "(page" not in text
        assert "no page numbers, so cite none" in text
    assert "'According to policy.md:'" in _file_instruction(
        "policy.md", 2, has_pages=False
    )


def test_mixed_results_ask_for_pages_only_where_chunks_have_them():
    mixed = _documents_instruction(has_pages=True, all_paged=False)
    assert "(page X)" in mixed
    assert "cite those by path alone" in mixed
    assert "cite those by path alone" not in _documents_instruction(has_pages=True)
