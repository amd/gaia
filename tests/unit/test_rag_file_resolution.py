# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests that per-file RAG tools resolve the requested file without guessing.

Asking about ``report.pdf`` must not silently answer from ``q3_report.pdf``;
when a request genuinely matches several indexed files the tool returns an
ambiguity error listing the candidates instead of picking one.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin, _match_indexed_files

Q3 = "/docs/finance/q3_report.pdf"
REPORT = "/docs/finance/report.pdf"
OTHER_REPORT = "/archive/report.pdf"


class TestMatchIndexedFiles:
    def test_exact_path_wins_over_substring(self):
        assert _match_indexed_files({Q3, REPORT}, REPORT) == [REPORT]

    def test_exact_basename_wins_over_substring(self):
        assert _match_indexed_files({Q3, REPORT}, "report.pdf") == [REPORT]

    def test_trailing_components_disambiguate_same_basename(self):
        files = {REPORT, OTHER_REPORT}
        assert _match_indexed_files(files, "finance/report.pdf") == [REPORT]

    def test_same_basename_in_two_dirs_returns_all_candidates(self):
        files = {REPORT, OTHER_REPORT, Q3}
        assert _match_indexed_files(files, "report.pdf") == [OTHER_REPORT, REPORT]

    def test_single_substring_match_still_resolves(self):
        assert _match_indexed_files({Q3, "/docs/notes.md"}, "q3_rep") == [Q3]

    def test_multiple_substring_matches_return_all(self):
        files = {Q3, "/docs/q3_summary.pdf"}
        assert _match_indexed_files(files, "q3_") == sorted(files)

    def test_backslash_path_matches_forward_slash_index(self):
        assert _match_indexed_files({Q3, REPORT}, "finance\\report.pdf") == [REPORT]

    def test_no_match_returns_empty(self):
        assert _match_indexed_files({Q3}, "budget.xlsx") == []


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _RagHost(Agent, RAGToolsMixin):
    def __init__(self, rag, **kwargs):
        self.rag = rag
        self.max_chunks = 5
        super().__init__(**kwargs)

    def _register_tools(self):
        self.register_rag_tools()

    def _generate_search_keys(self, query):
        return [query]


def _fake_rag(indexed_files):
    rag = MagicMock()
    rag.indexed_files = set(indexed_files)
    rag.chunks = ["chunk text"]
    rag.file_metadata = {}
    rag.file_to_chunk_indices = {}
    rag._retrieve_chunks_from_file.return_value = (["chunk text"], [0.9])
    return rag


def _tool(name):
    return _TOOL_REGISTRY[name]["function"]


def _make_host(rag):
    with patch("gaia.agents.base.agent.AgentSDK"):
        return _RagHost(rag, skip_lemonade=True, silent_mode=True)


class TestQuerySpecificFile:
    def test_exact_basename_queries_that_file_not_the_substring_match(self):
        rag = _fake_rag({Q3, REPORT})
        _make_host(rag)

        result = _tool("query_specific_file")(file_path="report.pdf", query="revenue")

        assert result.get("status") != "error", result
        searched = {c.args[1] for c in rag._retrieve_chunks_from_file.call_args_list}
        assert searched == {REPORT}

    def test_ambiguous_request_returns_candidates_and_queries_nothing(self):
        rag = _fake_rag({REPORT, OTHER_REPORT})
        _make_host(rag)

        result = _tool("query_specific_file")(file_path="report.pdf", query="revenue")

        assert result["status"] == "error"
        assert "Ambiguous" in result["error"]
        assert result["candidates"] == [OTHER_REPORT, REPORT]
        rag._retrieve_chunks_from_file.assert_not_called()

    def test_single_substring_match_still_queries_it(self):
        rag = _fake_rag({Q3, "/docs/notes.md"})
        _make_host(rag)

        result = _tool("query_specific_file")(file_path="q3_rep", query="revenue")

        assert result.get("status") != "error", result
        searched = {c.args[1] for c in rag._retrieve_chunks_from_file.call_args_list}
        assert searched == {Q3}


class TestDumpDocument:
    def test_ambiguous_request_returns_candidates(self):
        rag = _fake_rag({Q3, "/docs/q3_summary.pdf"})
        _make_host(rag)

        result = _tool("dump_document")(file_name="q3_")

        assert result["status"] == "error"
        assert result["candidates"] == [Q3, "/docs/q3_summary.pdf"]
