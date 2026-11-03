# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for RAG tools helper functions (extract_page_from_chunk).

Tests the pure functions in gaia.agents.tools.rag_tools that were
modified in the Agent UI PR to improve nil-RAG handling and page extraction.
"""

from gaia.agents.tools.rag_tools import extract_page_from_chunk, split_entries_at_pages


class TestExtractPageFromChunk:
    """Tests for the extract_page_from_chunk utility function."""

    # -- Strategy 1: [Page X] format in current chunk --

    def test_page_bracket_format(self):
        """Extract page from [Page N] format."""
        assert extract_page_from_chunk("[Page 1] Introduction text") == 1

    def test_page_bracket_format_large_number(self):
        """Extract large page number."""
        assert extract_page_from_chunk("[Page 142] more text") == 142

    def test_text_before_a_mid_chunk_marker_is_on_the_previous_page(self):
        """A chunk that crosses into page 5 starts on page 4."""
        assert extract_page_from_chunk("Content at end [Page 5]") == 4
        assert extract_page_from_chunk("tail of 141\n[Page 142]\nhead") == 141

    def test_image_text_marker_inside_a_page_keeps_that_page(self):
        text = "revenue grew 40% in Q3.\n\n---\n[Page 7]\n**Content Extracted from Images:**\n"
        assert extract_page_from_chunk(text) == 7
        chunks = ["[Page 7]\nrevenue", text]
        assert extract_page_from_chunk(chunks[1], 1, chunks) == 7

    def test_text_before_a_page_start_uses_the_earlier_chunks_page(self):
        chunks = ["[Page 3] a", "[Page 4] b", "end of four\n[Page 5]\nnext"]
        assert extract_page_from_chunk(chunks[2], 2, chunks) == 4

    def test_mid_chunk_marker_on_page_one_never_gives_page_zero(self):
        assert extract_page_from_chunk("cover text [Page 1] body") == 1

    def test_a_crossing_chunk_cites_where_it_starts_not_the_next_page(self):
        """The payment clause sits at the end of page 29; the chunk runs into 30."""
        chunks = [
            "[Page 29]\n12.1 The Customer shall pay",
            "within 14 days of the invoice date.\n[Page 30]\n12.8 Migration",
        ]
        assert extract_page_from_chunk(chunks[1], 1, chunks) == 29

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

    def test_backward_search_uses_the_last_marker_of_the_previous_chunk(self):
        chunks = ["[Page 7] a [Page 8] b [Page 9] c", "continues page 9"]
        assert extract_page_from_chunk(chunks[1], 1, chunks) == 9

    def test_backward_search_stops_at_another_document(self):
        """A text file indexed after a PDF never inherits the PDF's last page.

        A .txt answer was cited as "page 1667" because the lookback walked
        into the 1,667-page handbook indexed just before it.
        """
        chunks = ["[Page 1667] end of the handbook", "Fed decision text"]
        owners = {0: "handbook.pdf", 1: "fed.txt"}
        result = extract_page_from_chunk(
            chunks[1], chunk_index=1, all_chunks=chunks, chunk_to_file=owners
        )
        assert result is None

    def test_backward_search_still_walks_its_own_document(self):
        chunks = ["[Page 4] start", "continues", "target"]
        owners = {0: "a.pdf", 1: "a.pdf", 2: "a.pdf"}
        result = extract_page_from_chunk(
            chunks[2], chunk_index=2, all_chunks=chunks, chunk_to_file=owners
        )
        assert result == 4

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


class TestSplitEntriesAtPages:
    """Every entry's 'page' field holds for all its text, which is what gets cited."""

    @staticmethod
    def _entry(content, page=22):
        return {"chunk_id": 1, "content": content, "page": page}

    def test_a_chunk_crossing_a_page_splits_at_the_marker(self):
        entries = split_entries_at_pages(
            [self._entry("Tail of 22. [Page 23]\nWater main breaks: 137.")]
        )
        assert [(e["page"], e["content"]) for e in entries] == [
            (22, "Tail of 22."),
            (23, "[Page 23]\nWater main breaks: 137."),
        ]

    def test_entries_are_renumbered_for_display(self):
        entries = split_entries_at_pages(
            [self._entry("a [Page 23]\nb [Page 24]\nc"), self._entry("d", page=30)]
        )
        assert [e["chunk_id"] for e in entries] == [1, 2, 3, 4]
        assert [e["page"] for e in entries] == [22, 23, 24, 30]

    def test_a_chunk_starting_at_its_marker_stays_whole(self):
        entries = split_entries_at_pages([self._entry("[Page 22]\nbody", page=22)])
        assert [(e["page"], e["content"]) for e in entries] == [(22, "[Page 22]\nbody")]

    def test_an_in_page_label_does_not_split(self):
        text = "Slide body [Page 3] **Speaker Notes:**\nnotes"
        assert split_entries_at_pages([self._entry(text, page=3)]) == [
            {"chunk_id": 1, "content": text, "page": 3}
        ]

    def test_a_chunk_with_no_page_is_left_alone(self):
        entry = {"chunk_id": 1, "content": "a note quoting [Page 9] of a book"}
        assert split_entries_at_pages([entry]) == [entry]
