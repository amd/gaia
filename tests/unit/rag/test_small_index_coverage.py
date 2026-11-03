# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A question over a small index sees every chunk, not just the top five.

Eight one-paragraph regional reports came back five at a time, so "total
revenue across all eight" was summed over five.
"""

from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin, top_or_whole_index

_REGIONS = ["West", "East", "North", "South", "Central", "Pacific", "Gulf", "Mountain"]


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _RagHost(RAGToolsMixin):
    max_chunks = 5

    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None

    def _generate_search_keys(self, query):
        return [query]


def _reports(tmp_path, body_chars=300):
    chunks = [f"{r} Q2 2026 revenue report. " + "x" * body_chars for r in _REGIONS]
    files = [str(tmp_path / f"{r.lower()}.md") for r in _REGIONS]
    rag = MagicMock()
    rag.chunks = chunks
    rag.index = MagicMock()
    rag.chunk_to_file = dict(enumerate(files))
    rag.file_to_chunk_indices = {f: [i] for i, f in enumerate(files)}
    rag._retrieve_chunks.return_value = (chunks[:5], [0.9, 0.8, 0.7, 0.6, 0.5])
    return rag


def _query_documents(rag):
    _RagHost(rag).register_rag_tools()
    return _TOOL_REGISTRY["query_documents"]["function"](query="total Q2 revenue")


def test_eight_short_reports_all_reach_the_agent(tmp_path):
    result = _query_documents(_reports(tmp_path))

    assert result["status"] == "success", result
    sources = {
        c["source_file"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        for c in result["chunks"]
    }
    assert sources == {f"{r.lower()}.md" for r in _REGIONS}
    # Retrieved chunks stay first, in score order.
    assert [c["content"].split()[0] for c in result["chunks"][:5]] == _REGIONS[:5]


def test_a_large_index_still_returns_the_top_five(tmp_path):
    result = _query_documents(_reports(tmp_path, body_chars=3000))

    assert result["num_chunks"] == 5


def test_whole_index_keeps_ranked_order_and_adds_the_rest_once():
    ranked = [("b", 0.9), ("a", 0.4)]

    kept = top_or_whole_index(ranked, ["a", "b", "c", "c"], limit=1, chunk_chars=10)

    assert kept == [("b", 0.9), ("a", 0.4), ("c", 0.0)]


def test_index_over_budget_is_cut_to_the_limit():
    ranked = [("b" * 6, 0.9), ("a" * 6, 0.4)]

    kept = top_or_whole_index(ranked, ["a" * 6, "b" * 6], limit=1, chunk_chars=10)

    assert kept == [("b" * 6, 0.9)]
