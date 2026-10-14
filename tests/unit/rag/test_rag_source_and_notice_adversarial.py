# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hostile inputs for RAG chunk sources and the inference notice (#5089).

Same-named documents in different folders, non-ASCII paths, chunks whose file
was deleted or re-indexed, and every provider/model combination the notice
can be asked to describe.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.llm.inference_location import (
    InferenceLocation,
    resolve_inference_location,
)
from gaia.ui.sse_handler import SSEOutputHandler


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


def _library(tmp_path):
    """Two documents with the same name in different folders, one of them
    under a non-ASCII path, each with its own text."""
    a = tmp_path / "Q3" / "report.pdf"
    b = tmp_path / "Ärger 日本 – 2026" / "report.pdf"
    for doc in (a, b):
        doc.parent.mkdir(parents=True)
        doc.write_bytes(b"%PDF-1.4")
    texts = {
        str(a): ["Revenue in Q3 was $4.1M.", "Churn fell to 2%."],
        str(b): ["Umsatz stieg um 12 Prozent.", "日本の売上は横ばい。"],
    }
    rag = MagicMock()
    rag.index = object()
    rag.indexed_files = {str(a), str(b)}
    rag.chunks = texts[str(a)] + texts[str(b)]
    rag.chunk_to_file = {0: str(a), 1: str(a), 2: str(b), 3: str(b)}
    rag.file_to_chunk_indices = {str(a): [0, 1], str(b): [2, 3]}
    rag.file_metadata = {}

    def per_file(_query, path):
        return list(texts[path]), [0.9, 0.8]

    rag._retrieve_chunks_from_file.side_effect = per_file
    rag._retrieve_chunks.return_value = (list(rag.chunks), [0.9, 0.85, 0.8, 0.75])
    return rag, a, b, texts


def _tools(rag):
    _RagHost(rag).register_rag_tools()
    return {
        name: _TOOL_REGISTRY[name]["function"]
        for name in ("query_documents", "query_specific_file")
    }


# ── Same name, different folders ─────────────────────────────────────────


def test_same_named_files_each_cite_their_own_folder(tmp_path):
    rag, a, b, texts = _library(tmp_path)
    tools = _tools(rag)
    for doc in (a, b):
        result = tools["query_specific_file"](file_path=str(doc), query="sales")
        assert result["status"] == "success", result
        assert {c["source_file"] for c in result["chunks"]} == {str(doc.resolve())}
        assert result["source_files"] == [str(doc.resolve())]
        assert [c["content"] for c in result["chunks"]] == texts[str(doc)]


def test_bare_name_matching_two_folders_is_ambiguous_not_a_guess(tmp_path):
    rag, *_ = _library(tmp_path)
    result = _tools(rag)["query_specific_file"](file_path="report.pdf", query="x")
    assert result["status"] == "error"
    assert "source_files" not in result
    rag._retrieve_chunks_from_file.assert_not_called()


def test_library_search_keeps_same_named_sources_apart(tmp_path):
    rag, a, b, texts = _library(tmp_path)
    result = _tools(rag)["query_documents"](query="sales")
    assert result["status"] == "success", result
    by_text = {c["content"]: c["source_file"] for c in result["chunks"]}
    for doc in (a, b):
        for text in texts[str(doc)]:
            assert by_text[text] == str(doc.resolve())
    assert sorted(result["source_files"]) == sorted(
        [str(a.resolve()), str(b.resolve())]
    )


def test_cards_show_the_non_ascii_path_and_the_right_folder(tmp_path):
    rag, _, b, _ = _library(tmp_path)
    result = _tools(rag)["query_specific_file"](file_path=str(b), query="x")
    handler = SSEOutputHandler()
    handler.pretty_print_json(result, title="Result")
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    (card,) = [e["result_data"] for e in events if "result_data" in e]
    assert {c["sourcePath"] for c in card["chunks"]} == {str(b.resolve())}
    assert {c["source"] for c in card["chunks"]} == {"report.pdf"}
    assert "Ärger 日本 – 2026" in card["source_files"][0]


# ── Deleted and re-indexed files ─────────────────────────────────────────


def test_chunks_of_a_deleted_file_still_name_it(tmp_path):
    rag, a, _, texts = _library(tmp_path)
    a.unlink()
    result = _tools(rag)["query_specific_file"](file_path=str(a), query="x")
    assert result["status"] == "success", result
    assert {c["source_file"] for c in result["chunks"]} == {str(a.resolve())}


def test_chunks_no_longer_in_the_index_never_borrow_another_files_path(tmp_path):
    """After a re-index the retrieved text may be missing from ``rag.chunks``;
    such a chunk must not be credited to whatever file sits at index -1."""
    rag, a, b, _ = _library(tmp_path)
    stale = "Revenue in Q3 was $3.9M (old revision)."
    rag._retrieve_chunks.return_value = ([stale, rag.chunks[2]], [0.95, 0.9])
    result = _tools(rag)["query_documents"](query="revenue")
    sources = {c["content"]: c["source_file"] for c in result["chunks"]}
    assert sources[stale] in ("", str(a.resolve()))
    assert sources[stale] != str(b.resolve())
    assert sources[rag.chunks[2]] == str(b.resolve())
    assert "" not in result["source_files"]


def test_reindexed_file_text_not_found_keeps_the_queried_file(tmp_path):
    rag, a, _, _ = _library(tmp_path)
    rag._retrieve_chunks_from_file.side_effect = None
    rag._retrieve_chunks_from_file.return_value = (["brand-new text"], [0.9])
    result = _tools(rag)["query_specific_file"](file_path=str(a), query="x")
    assert result["chunks"][0]["source_file"] == str(a.resolve())


# ── The notice never leaks internal wording ──────────────────────────────

_INTERNAL = ("via lemonade", "lemonade", "remote", "amd.", "fireworks.", "accounts/")

_COMBOS = [
    dict(model="Gemma-4-E4B-it-GGUF"),
    dict(model="user.Qwen3.8-Flash-Next-GGUF"),
    dict(model="Qwen3.6-35B-A3B-GGUF"),
    dict(model="amd.DeepSeek-V4.1-Flash"),
    dict(model="amd.Llama-4/Maverick"),
    dict(model="fireworks.accounts/fireworks/models/kimi-k2p6"),
    dict(model="fireworks.accounts/fireworks/models/"),
    dict(model="fireworks.kimi"),
    dict(model="claude-opus-5", use_claude=True),
    dict(model="claude-sonnet-5", use_claude=True),
    dict(model="gpt-5", use_openai=True),
    dict(model=""),
    dict(model=None),
    dict(model="   "),
]


@pytest.mark.parametrize("combo", _COMBOS, ids=lambda c: repr(c))
def test_notice_never_names_internal_routing(combo):
    loc = resolve_inference_location(**combo)
    for text in (loc.notice(), loc.placement(), loc.model_name):
        lowered = text.lower()
        for word in _INTERNAL:
            assert word not in lowered, (word, text)
    assert loc.notice().startswith("Using ")
    assert loc.model_name.strip()


@pytest.mark.parametrize("provider", ["amd", "fireworks", "some-new-cloud"])
def test_notice_strips_the_prefix_of_any_provider_a_live_lookup_names(provider):
    loc = resolve_inference_location(
        f"{provider}.DeepSeek-V4.1-Flash", cloud_provider_lookup=lambda _m: provider
    )
    assert loc.notice().startswith("Using DeepSeek-V4.1-Flash on ")
    assert "lemonade" not in loc.notice().lower()


@pytest.mark.parametrize("lookup_answer", [None, "", 42])
def test_notice_is_clean_when_a_live_lookup_says_local_or_answers_junk(
    lookup_answer,
):
    loc = resolve_inference_location(
        "Gemma-4-E4B-it-GGUF", cloud_provider_lookup=lambda _m: lookup_answer
    )
    assert loc.notice().startswith("Using Gemma-4-E4B-it-GGUF on this machine.")
    assert "lemonade" not in loc.notice().lower()


def test_local_notice_promises_nothing_leaves_and_cloud_says_what_does():
    local = InferenceLocation("lemonade", "Gemma-4-E4B-it-GGUF", remote=False)
    assert "on this machine" in local.notice()
    assert "cloud provider" in local.notice()
    for provider in ("amd", "fireworks", "claude", "openai"):
        cloud = InferenceLocation(provider, f"{provider}.m", remote=True)
        assert "(cloud)" in cloud.notice()
        assert "are sent there" in cloud.notice()
        assert "on this machine" not in cloud.notice()


def test_session_response_notice_is_clean_for_cloud_sessions():
    from gaia.ui.routers.sessions import _resolved_session_response

    assert callable(_resolved_session_response)
    for model in ("amd.DeepSeek-V4.1-Flash", "fireworks.accounts/x/models/y"):
        notice = resolve_inference_location(model).notice()
        assert "Lemonade" not in notice
        assert ".accounts" not in notice
