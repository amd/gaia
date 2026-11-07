# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Searches across every document serve an edited file's current text (#6939)."""

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import rag_tools
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig

OLD = "The launch is on Tuesday in Lisbon. " * 30
EDITED = OLD.replace("Lisbon", "Madrid")
CALL_DEADLINE_S = 5.0


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _make(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    config = RAGConfig(
        cache_dir=str(tmp_path / "cache"),
        allowed_paths=[str(tmp_path)],
        use_llm_chunking=False,
    )
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(config)
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **_kwargs: np.ones(
        (len(texts), 4), dtype="float32"
    )
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    return sdk


class _RagHost(RAGToolsMixin):
    max_chunks = 5

    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query: str):
        return [query]


def _tool(name):
    return _TOOL_REGISTRY[name]["function"]


def _write_keeping_stat(path: Path, text: str):
    """Rewrite *path* with the same size and modification time as before."""
    st = path.stat()
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


@pytest.fixture
def edited(tmp_path):
    """A host whose one indexed document was edited without changing its stat."""
    doc = tmp_path / "plan.txt"
    doc.write_text(OLD, encoding="utf-8")
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]
    _RagHost(sdk).register_rag_tools()
    _write_keeping_stat(doc, EDITED)
    return sdk, doc


def test_query_documents_serves_the_edited_text(edited):
    sdk, doc = edited

    result = _tool("query_documents")(query="Where is the launch?")

    assert result["status"] == "success", result
    served = " ".join(chunk["content"] for chunk in result["chunks"])
    assert "Madrid" in served
    assert "Lisbon" not in served
    assert sdk.is_current(str(doc))


def test_search_indexed_chunks_serves_the_edited_text(edited):
    sdk, doc = edited

    old = _tool("search_indexed_chunks")(pattern="Lisbon")
    new = _tool("search_indexed_chunks")(pattern="Madrid")

    assert old["status"] == "success", old
    assert old["count"] == 0
    assert new["count"] > 0
    assert sdk.is_current(str(doc))


# --- the guard with a mocked library -------------------------------------


def _mock_rag(doc: Path, chunk: str):
    rag = MagicMock()
    rag.index = object()
    rag.chunks = [chunk]
    rag.chunk_to_file = {0: str(doc)}
    rag.file_metadata = {}
    rag._retrieve_chunks.return_value = ([chunk], [1.0])
    return rag


def _call(name, **kwargs):
    holder = {}
    worker = threading.Thread(
        target=lambda: holder.update(result=_tool(name)(**kwargs)), daemon=True
    )
    worker.start()
    worker.join(CALL_DEADLINE_S)
    assert not worker.is_alive(), f"{name} blocked on a slow re-index"
    return holder["result"]


@pytest.mark.parametrize(
    "name, kwargs",
    [
        ("query_documents", {"query": "launch"}),
        ("search_indexed_chunks", {"pattern": "launch"}),
    ],
)
def test_a_current_file_is_not_reindexed(tmp_path, name, kwargs):
    doc = tmp_path / "doc.txt"
    rag = _mock_rag(doc, "the launch")
    rag.is_current.return_value = True
    _RagHost(rag).register_rag_tools()

    result = _call(name, **kwargs)

    assert result["status"] == "success", result
    rag.index_document.assert_not_called()


@pytest.mark.parametrize(
    "name, kwargs",
    [
        ("query_documents", {"query": "launch"}),
        ("search_indexed_chunks", {"pattern": "launch"}),
    ],
)
def test_a_slow_reindex_reports_in_progress(tmp_path, monkeypatch, name, kwargs):
    monkeypatch.setattr(rag_tools, "INDEX_FOREGROUND_BUDGET_S", 0.2)
    doc = tmp_path / "doc.txt"
    rag = _mock_rag(doc, "the launch")
    rag.is_current.return_value = False
    release = threading.Event()

    def index_document(path, progress_callback=None, wait_callback=None):
        release.wait(30)
        return {"success": True}

    rag.index_document.side_effect = index_document
    _RagHost(rag).register_rag_tools()

    try:
        result = _call(name, **kwargs)
    finally:
        release.set()

    assert result["status"] == "in_progress", result


def test_a_failed_reindex_is_an_error_not_old_text(tmp_path):
    doc = tmp_path / "doc.txt"
    rag = _mock_rag(doc, "the launch")
    rag.is_current.return_value = False
    rag.index_document.return_value = {"success": False, "error": "file locked"}
    _RagHost(rag).register_rag_tools()

    result = _call("query_documents", query="launch")

    assert result["status"] == "error", result
    assert "file locked" in result["error"]
    assert "chunks" not in result


def test_a_reindex_that_raises_is_an_error_not_a_crash(tmp_path):
    doc = tmp_path / "doc.txt"
    rag = _mock_rag(doc, "the launch")
    rag.is_current.return_value = False
    rag.index_document.side_effect = RuntimeError("index corrupted")
    _RagHost(rag).register_rag_tools()

    result = _call("query_documents", query="launch")

    assert result["status"] == "error", result
    assert "index corrupted" in result["error"]


def test_a_reindex_forgets_evicted_documents(tmp_path):
    doc = tmp_path / "doc.txt"
    other = str(tmp_path / "evicted.txt")
    rag = _mock_rag(doc, "the launch")
    rag.is_current.side_effect = [False, True]
    rag.index_document.return_value = {"success": True, "evicted": [other]}
    host = _RagHost(rag)
    host.indexed_files.add(other)
    host.register_rag_tools()

    result = _call("search_indexed_chunks", pattern="launch")

    assert result["status"] == "success", result
    assert other not in host.indexed_files
