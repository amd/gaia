# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keyword hits compete with semantic matches by rank, not by a fixed boost (#4737).

The old fixed 0.6-0.8 keyword boost outranked every dense score (typically
0.3-0.6), so any keyword hit filled the slots ahead of the semantic matches
and a 10-document library answered only 15% of questions. The rankings are
now fused by reciprocal rank fusion, so the top of each list competes on
equal terms and a chunk both rankings like wins outright.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import (
    _RRF_K,
    RAGToolsMixin,
    _adaptive_max,
    _fuse_dense_and_keyword,
    _fuse_extra_scores,
)

# A chunk about refunds, with none of the query's words in it.
SEMANTIC_BEST = "The refund window closes after thirty days of delivery."
SEMANTIC_SECOND = "Returns are processed once the warehouse confirms receipt."
# Matches "cancellation" and "fee" side by side, but dense search misses it.
KEYWORD_ONLY = "The cancellation fee schedule is published yearly."
# Matches the same words and dense search likes it too.
BOTH = "The cancellation fee is charged when a booking is cancelled."


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _RagHost(RAGToolsMixin):
    def __init__(self, rag, debug=False):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.max_chunks = 5
        self.debug = debug
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query):
        return [query]


def _rag(doc: Path, chunks, dense=([], []), dense_from_file=([], [])):
    path = str(doc.resolve())
    rag = MagicMock()
    rag.index = object()
    rag.chunks = list(chunks)
    rag.chunk_to_file = {i: path for i in range(len(chunks))}
    rag.indexed_files = {path}
    rag.file_to_chunk_indices = {path: list(range(len(chunks)))}
    rag.file_metadata = {path: {"full_text": "\n".join(chunks)}}
    rag._retrieve_chunks.return_value = dense
    rag._retrieve_chunks_from_file.return_value = dense_from_file
    return rag


def _tools(doc, chunks, **kwargs):
    rag = _rag(doc, chunks, **kwargs)
    _RagHost(rag).register_rag_tools()
    return (
        _TOOL_REGISTRY["query_documents"]["function"],
        _TOOL_REGISTRY["query_specific_file"]["function"],
        rag,
    )


def _contents(result):
    assert result["status"] == "success", result
    return [c["content"] for c in result["chunks"]]


def _fillers(n, start=0):
    return [f"Staff meeting notes for week {n}." for n in range(start, start + n)]


def test_fusion_ranks_a_chunk_both_rankings_like_first():
    dense = [("a", 0.58), ("b", 0.55)]
    keyword = ["b", "c"]

    assert [chunk for chunk, _ in _fuse_dense_and_keyword(dense, keyword)] == [
        "b",
        "a",
        "c",
    ]


def test_fusion_lets_each_top_rank_compete_on_equal_terms():
    dense = [("a", 0.58), ("b", 0.55)]
    keyword = ["c"]

    # The keyword top ties the dense top (1/61 each) and the dense score
    # breaks the tie, so the semantic match stays first; the keyword hit
    # no longer sits above every semantic match.
    assert [chunk for chunk, _ in _fuse_dense_and_keyword(dense, keyword)] == [
        "a",
        "c",
        "b",
    ]


def test_adaptive_max_tiers():
    assert _adaptive_max(10, 5) == 5
    assert _adaptive_max(60, 5) == 10
    assert _adaptive_max(150, 5) == 20
    assert _adaptive_max(300, 5) == 25


def test_extra_scores_rank_after_the_dense_list():
    scored = _fuse_extra_scores(5, ["x"], [("e1", 0.4), ("e2", 0.3)])

    assert [chunk for chunk, _ in scored] == ["e1", "e2"]
    # Ranked just after the five dense chunks, rescaled to a 0-1 scale:
    # (k + 1) / 2 * 1/66, (k + 1) / 2 * 1/67.
    assert abs(scored[0][1] - (_RRF_K + 1) / 2 / 66) < 1e-9
    assert abs(scored[1][1] - (_RRF_K + 1) / 2 / 67) < 1e-9


def test_relevance_scores_stay_on_a_zero_to_one_scale(tmp_path):
    # A chunk ranked first by both lists scores exactly 1.0.
    fused = _fuse_dense_and_keyword([("a", 0.58), ("b", 0.55)], ["a", "c"])
    assert fused[0][0] == "a"
    assert fused[0][1] == pytest.approx(1.0)
    assert all(0.0 <= score <= 1.0 for _, score in fused)

    # ...and the scores a search reports stay in range too.
    doc = tmp_path / "policy.md"
    chunks = [SEMANTIC_BEST, BOTH, KEYWORD_ONLY] + _fillers(3)
    search, _one_file, _rag = _tools(
        doc, chunks, dense=([SEMANTIC_BEST, BOTH], [0.58, 0.55])
    )
    result = search(query="cancellation fee")

    assert result["status"] == "success", result
    scores = [c["relevance_score"] for c in result["chunks"]]
    assert scores
    assert all(0.0 <= score <= 1.0 for score in scores)


def test_keyword_hit_no_longer_outranks_the_top_dense_match(tmp_path):
    doc = tmp_path / "policy.md"
    chunks = [SEMANTIC_BEST, SEMANTIC_SECOND, KEYWORD_ONLY] + _fillers(3)
    search, _one_file, _rag = _tools(
        doc, chunks, dense=([SEMANTIC_BEST, SEMANTIC_SECOND], [0.58, 0.55])
    )

    found = _contents(search(query="cancellation fee policy"))

    assert found[:3] == [SEMANTIC_BEST, KEYWORD_ONLY, SEMANTIC_SECOND]


def test_chunk_both_rankings_like_wins(tmp_path):
    doc = tmp_path / "policy.md"
    chunks = [SEMANTIC_BEST, BOTH, KEYWORD_ONLY] + _fillers(3)
    search, _one_file, _rag = _tools(
        doc, chunks, dense=([SEMANTIC_BEST, BOTH], [0.58, 0.55])
    )

    found = _contents(search(query="cancellation fee"))

    assert found[:3] == [BOTH, SEMANTIC_BEST, KEYWORD_ONLY]


def test_specific_file_keyword_hit_no_longer_outranks_dense(tmp_path):
    doc = tmp_path / "policy.md"
    chunks = [SEMANTIC_BEST, SEMANTIC_SECOND, KEYWORD_ONLY] + _fillers(3)
    _search, one_file, _rag = _tools(
        doc,
        chunks,
        dense_from_file=([SEMANTIC_BEST, SEMANTIC_SECOND], [0.58, 0.55]),
    )

    found = _contents(one_file(file_path=str(doc), query="cancellation fee policy"))

    assert found[:3] == [SEMANTIC_BEST, KEYWORD_ONLY, SEMANTIC_SECOND]


def test_keyword_pool_covers_every_slot(tmp_path):
    # Sixty chunks, so the cutoff is 10: the old pool of 5 keyword hits
    # would leave three of the eight matching chunks out.
    doc = tmp_path / "policy.md"
    matching = [
        f"The cancellation fee for case {n} is listed here. " + "Details follow. " * 40
        for n in range(8)
    ]
    chunks = matching + [
        f"Staff meeting notes for week {n}. " + "Nothing relevant here. " * 40
        for n in range(52)
    ]
    search, _one_file, _rag = _tools(doc, chunks, dense=([], []))

    found = _contents(search(query="cancellation fee"))

    assert sorted(found) == sorted(matching)


def test_debug_mode_reports_chunk_indices_without_a_scan(tmp_path):
    doc = tmp_path / "policy.md"
    chunks = [SEMANTIC_BEST, SEMANTIC_SECOND, KEYWORD_ONLY] + _fillers(3)
    rag = _rag(
        doc,
        chunks,
        dense=([SEMANTIC_BEST, SEMANTIC_SECOND], [0.58, 0.55]),
    )
    _RagHost(rag, debug=True).register_rag_tools()
    search = _TOOL_REGISTRY["query_documents"]["function"]

    result = search(query="cancellation fee policy")

    assert result["status"] == "success", result
    debug = result["debug_info"]
    assert debug["total_chunks_before_dedup"] == 3
    assert debug["total_chunks_after_dedup"] == 3
    assert debug["chunks_before_dedup_indices"] == [0, 1, 2]
    assert debug["chunks_after_dedup_indices"] == [0, 2, 1]
