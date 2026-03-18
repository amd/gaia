# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for RAG tools helper functions (extract_page_from_chunk).

Tests the pure functions in gaia.agents.chat.tools.rag_tools that were
modified in the Agent UI PR to improve nil-RAG handling and page extraction.
"""

from gaia.agents.chat.tools.rag_tools import extract_page_from_chunk


class TestExtractPageFromChunk:
    """Tests for the extract_page_from_chunk utility function."""

    # -- Strategy 1: [Page X] format in current chunk --

    def test_page_bracket_format(self):
        """Extract page from [Page N] format."""
        assert extract_page_from_chunk("[Page 1] Introduction text") == 1

    def test_page_bracket_format_large_number(self):
        """Extract large page number."""
        assert extract_page_from_chunk("Some text [Page 142] more text") == 142

    def test_page_bracket_format_at_end(self):
        """Extract page when marker is at end of chunk."""
        assert extract_page_from_chunk("Content at end [Page 5]") == 5

    # -- Strategy 2: (Page X) format --

    def test_page_paren_format(self):
        """Extract page from (Page N) format."""
        assert extract_page_from_chunk("(Page 3) Some content") == 3

    def test_page_paren_format_embedded(self):
        """Extract page when paren format is embedded in text."""
        assert extract_page_from_chunk("See reference (Page 10) for details") == 10

    # -- Strategy 3: Backward search in previous chunks --

    def test_backward_search_finds_page_in_previous_chunk(self):
        """Find page by looking backwards in previous chunks."""
        chunks = [
            "[Page 1] First page content",
            "[Page 2] Second page content",
            "Content without page marker",
            "More content without page marker",
        ]
        result = extract_page_from_chunk(chunks[3], chunk_index=3, all_chunks=chunks)
        assert result == 2

    def test_backward_search_limited_to_5_chunks(self):
        """Backward search only looks back 5 chunks."""
        chunks = [
            "[Page 1] Very early content",
            "No page marker 1",
            "No page marker 2",
            "No page marker 3",
            "No page marker 4",
            "No page marker 5",
            "No page marker 6",
            "Target chunk without page marker",
        ]
        # chunk_index=7, looks back at indices 6,5,4,3 (max 5 back)
        # [Page 1] is at index 0, which is > 5 chunks back from index 7
        result = extract_page_from_chunk(chunks[7], chunk_index=7, all_chunks=chunks)
        assert result is None

    def test_backward_search_finds_closest_page(self):
        """Backward search returns the most recent page marker."""
        chunks = [
            "[Page 1] First",
            "[Page 5] Fifth",
            "No marker here",
        ]
        result = extract_page_from_chunk(chunks[2], chunk_index=2, all_chunks=chunks)
        assert result == 5

    # -- No page found --

    def test_no_page_marker_returns_none(self):
        """Return None when no page marker exists."""
        assert extract_page_from_chunk("Just some text without any page") is None

    def test_empty_string_returns_none(self):
        """Return None for empty string."""
        assert extract_page_from_chunk("") is None

    def test_no_page_no_chunks(self):
        """Return None when no chunks are provided for backward search."""
        result = extract_page_from_chunk("No page marker", chunk_index=0)
        assert result is None

    # -- Edge cases --

    def test_bracket_format_takes_priority_over_paren(self):
        """[Page X] format found first, so it takes priority."""
        assert extract_page_from_chunk("[Page 3] text (Page 5)") == 3

    def test_paren_used_when_no_bracket(self):
        """(Page X) used when [Page X] not present."""
        assert extract_page_from_chunk("text (Page 7) more") == 7

    def test_backward_search_with_negative_chunk_index(self):
        """Backward search with chunk_index=-1 (default) does nothing."""
        chunks = ["[Page 1] Content"]
        result = extract_page_from_chunk("No marker", chunk_index=-1, all_chunks=chunks)
        assert result is None

    def test_backward_search_with_none_all_chunks(self):
        """Backward search with all_chunks=None does nothing."""
        result = extract_page_from_chunk("No marker", chunk_index=5, all_chunks=None)
        assert result is None

    def test_page_zero(self):
        """Extract page 0 (edge case)."""
        assert extract_page_from_chunk("[Page 0] Cover page") == 0

    def test_multiple_page_markers_returns_first(self):
        """With multiple markers in one chunk, returns the first match."""
        result = extract_page_from_chunk("[Page 3] text [Page 7] more text")
        assert result == 3
