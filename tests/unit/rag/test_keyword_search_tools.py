# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Document search reads each chunk's words once, not on every query (#6393)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag import keyword_search

_CHUNKS = [
    "The cancellation fee is 200 dollars.",
    "Office hours are nine to five.",
    "Parking is free for staff.",
]


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def reads(monkeypatch):
    read = []
    words = keyword_search._words
    monkeypatch.setattr(
        keyword_search, "_words", lambda text: read.append(text) or words(text)
    )
    return read


class _RagHost(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.max_chunks = 5
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query):
        return [query]


def _rag(doc: Path):
    path = str(doc.resolve())
    rag = MagicMock()
    rag.index = object()
    rag.chunks = list(_CHUNKS)
    rag.chunk_to_file = {i: path for i in range(len(_CHUNKS))}
    rag.indexed_files = {path}
    rag.file_to_chunk_indices = {path: list(range(len(_CHUNKS)))}
    rag.file_metadata = {path: {"full_text": "\n".join(_CHUNKS)}}
    rag._retrieve_chunks.return_value = ([], [])
    rag._retrieve_chunks_from_file.return_value = ([], [])
    return rag


def _texts(result):
    assert result["status"] == "success", result
    return [chunk["content"] for chunk in result["chunks"]]


def test_searches_read_the_chunks_once_and_follow_changes(tmp_path, reads):
    doc = tmp_path / "policy.md"
    rag = _rag(doc)
    _RagHost(rag).register_rag_tools()
    library = _TOOL_REGISTRY["query_documents"]["function"]
    one_file = _TOOL_REGISTRY["query_specific_file"]["function"]

    assert _texts(library(query="cancellation fee")) == [_CHUNKS[0]]
    assert _texts(library(query="office hours")) == [_CHUNKS[1]]
    assert _texts(one_file(file_path=str(doc), query="parking")) == [_CHUNKS[2]]
    assert sorted(set(reads) & set(_CHUNKS)) == sorted(_CHUNKS)
    assert len([text for text in reads if text in _CHUNKS]) == 3

    # Removing a document publishes a shorter list; kept chunks are not re-read.
    reads.clear()
    rag.chunks = list(_CHUNKS[1:])
    path = str(doc.resolve())
    rag.chunk_to_file = {0: path, 1: path}
    rag.file_to_chunk_indices = {path: [0, 1]}
    assert _texts(library(query="parking")) == [_CHUNKS[2]]
    assert reads == ["parking"]

    # A newly indexed chunk is read once and found.
    reads.clear()
    rag.chunks = rag.chunks + ["Refunds take ten days."]
    rag.chunk_to_file[2] = path
    rag.file_to_chunk_indices = {path: [0, 1, 2]}
    assert _texts(one_file(file_path=str(doc), query="refunds")) == [
        "Refunds take ten days."
    ]
    assert reads == ["Refunds take ten days.", "refunds"]
