# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Exact RAG matches retain enough provenance to cite the source page."""

import threading
from types import SimpleNamespace

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin


class _Host(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set(rag.indexed_files)
        self.current_session = None


def test_exact_matches_include_source_and_page(tmp_path):
    document = tmp_path / "harbor_policy.pdf"
    document.write_bytes(b"placeholder")
    chunks = [
        "[Page 1]\nGeneral harbor policy.",
        "[Page 2]\nThe badge-renewal passphrase for 2026 is copper-heron-64.",
    ]
    rag = SimpleNamespace(
        chunks=chunks,
        indexed_files={str(document)},
        chunk_to_file={0: str(document), 1: str(document)},
        file_to_chunk_indices={str(document): [0, 1]},
        _state_lock=threading.RLock(),
        freshness=lambda _path: "unchanged",
    )

    saved = dict(_TOOL_REGISTRY)
    try:
        _Host(rag).register_rag_tools()
        result = _TOOL_REGISTRY["search_indexed_chunks"]["function"](
            pattern="copper-heron-64"
        )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    assert result["status"] == "success", result
    assert result["count"] == 1
    assert result["match_details"] == [
        {
            "content": chunks[1],
            "source_file": str(document.resolve()),
            "page": 2,
        }
    ]
    assert "source_file" in result["instruction"]


def test_exact_match_in_a_cross_page_chunk_uses_the_matching_page(tmp_path):
    document = tmp_path / "cross_page.pdf"
    document.write_bytes(b"placeholder")
    chunk = "Tail of page one. [Page 2] The target is copper-heron-64."
    rag = SimpleNamespace(
        chunks=[chunk],
        indexed_files={str(document)},
        chunk_to_file={0: str(document)},
        file_to_chunk_indices={str(document): [0]},
        _state_lock=threading.RLock(),
        freshness=lambda _path: "unchanged",
    )

    saved = dict(_TOOL_REGISTRY)
    try:
        _Host(rag).register_rag_tools()
        result = _TOOL_REGISTRY["search_indexed_chunks"]["function"](
            pattern="copper-heron-64"
        )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    assert result["match_details"] == [
        {
            "content": "[Page 2] The target is copper-heron-64.",
            "source_file": str(document.resolve()),
            "page": 2,
        }
    ]


def test_exact_matches_without_page_do_not_invent_one(tmp_path):
    document = tmp_path / "notes.md"
    document.write_text("The passphrase is copper-heron-64.", encoding="utf-8")
    rag = SimpleNamespace(
        chunks=["The passphrase is copper-heron-64."],
        indexed_files={str(document)},
        chunk_to_file={0: str(document)},
        file_to_chunk_indices={str(document): [0]},
        _state_lock=threading.RLock(),
        freshness=lambda _path: "unchanged",
    )

    saved = dict(_TOOL_REGISTRY)
    try:
        _Host(rag).register_rag_tools()
        result = _TOOL_REGISTRY["search_indexed_chunks"]["function"](
            pattern="copper-heron-64"
        )
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    assert result["match_details"] == [
        {
            "content": "The passphrase is copper-heron-64.",
            "source_file": str(document.resolve()),
        }
    ]
    assert "page number when the field is absent" in result["instruction"]
