# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document too big to index inside the tool budget must not hang (#4744).

A 1,667-page PDF embeds for longer than the 600 s tool limit on a CPU
embedder, so ``index_document`` sat silent for ten minutes and was abandoned.
The tool now hands a slow index to a background thread after a short budget
and says so; the SDK embeds outside its state lock so searches keep working.
"""

import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import rag_tools
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig

# The tool call itself runs on a thread joined with this deadline, so a
# regression back to a blocking index fails instead of hanging the suite.
CALL_DEADLINE_S = 5.0


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture(autouse=True)
def _short_budget(monkeypatch):
    monkeypatch.setattr(rag_tools, "INDEX_FOREGROUND_BUDGET_S", 0.2, raising=False)


class _RagHost(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.rebuild_system_prompt = MagicMock()


def _call(name, **kwargs):
    holder = {}
    worker = threading.Thread(
        target=lambda: holder.update(result=_TOOL_REGISTRY[name]["function"](**kwargs)),
        daemon=True,
    )
    worker.start()
    worker.join(CALL_DEADLINE_S)
    assert not worker.is_alive(), f"{name} blocked on a slow index"
    return holder["result"]


@pytest.fixture
def slow_rag():
    """A RAG whose indexing embeds 40 of 100 chunks, then waits for release."""
    release = threading.Event()
    rag = MagicMock()
    rag.indexed_files = set()

    def index_document(path, progress_callback=None):
        if progress_callback is not None:
            progress_callback(0, 100)
            progress_callback(40, 100)
        assert release.wait(30)
        rag.indexed_files.add(path)
        return {"success": True, "file_name": "big.pdf", "num_chunks": 100}

    rag.index_document.side_effect = index_document
    yield rag, release
    release.set()


def test_slow_index_returns_in_progress_then_success(tmp_path, slow_rag):
    rag, release = slow_rag
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.register_rag_tools()

    first = _call("index_document", file_path=str(doc))
    assert first["status"] == "in_progress", first
    assert "background" in first["message"]
    assert "40 of 100 chunks embedded" in first["message"]
    assert first["total_chunks"] == 100
    assert "read the relevant part of the file directly" in first["hint"]
    assert str(doc) not in host.indexed_files

    # Asking again waits on the same job instead of starting a second index.
    again = _call("index_document", file_path=str(doc))
    assert again["status"] == "in_progress"
    assert rag.index_document.call_count == 1

    release.set()
    done = _call("index_document", file_path=str(doc))
    assert done["status"] == "success", done
    assert rag.index_document.call_count == 1
    assert str(doc) in host.indexed_files
    host.rebuild_system_prompt.assert_called()


def test_query_specific_file_does_not_block_on_auto_index(tmp_path, slow_rag):
    rag, _ = slow_rag
    doc = tmp_path / "big.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.register_rag_tools()

    result = _call("query_specific_file", file_path=str(doc), query="needle")
    assert result["status"] == "in_progress", result
    assert result["file_name"] == "big.pdf"


def test_fast_index_answers_in_the_same_call(tmp_path):
    rag = MagicMock()
    rag.index_document.return_value = {"success": True, "file_name": "small.txt"}
    doc = tmp_path / "small.txt"
    doc.write_text("hello", encoding="utf-8")
    host = _RagHost(rag)
    host.register_rag_tools()

    result = _call("index_document", file_path=str(doc))
    assert result["status"] == "success", result
    assert str(doc) in host.indexed_files


def test_background_failure_is_reported_not_swallowed(tmp_path):
    rag = MagicMock()
    rag.index_document.side_effect = ConnectionError("embedder unreachable")
    doc = tmp_path / "broken.pdf"
    doc.write_bytes(b"%PDF-1.4")
    host = _RagHost(rag)
    host.register_rag_tools()

    result = _call("index_document", file_path=str(doc))
    assert result["status"] == "error"
    assert "embedder unreachable" in result["error"]


@pytest.fixture
def sdk(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        rag = RAGSDK(
            RAGConfig(cache_dir=str(tmp_path / "cache"), allowed_paths=[str(tmp_path)])
        )
    rag._load_embedder = lambda: None
    rag._encode_texts = lambda texts, **kwargs: np.ones(
        (len(texts), 4), dtype="float32"
    )
    rag._get_hmac_key = lambda: b"test-index-key" * 3
    return rag


def test_index_document_reports_embedding_progress(sdk, tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("content", encoding="utf-8")
    seen = []
    with (
        patch("gaia.rag.sdk.PROGRESS_SLICE_CHUNKS", 2),
        patch.object(
            sdk, "_split_text_into_chunks", return_value=["a", "b", "c", "d", "e"]
        ),
    ):
        result = sdk.index_document(
            str(doc), progress_callback=lambda *a: seen.append(a)
        )
    assert result["success"]
    assert seen == [(0, 5), (2, 5), (4, 5), (5, 5)]
    assert sdk.index.ntotal == 5


def test_searches_are_not_locked_out_while_a_document_embeds(sdk, tmp_path):
    first = tmp_path / "first.txt"
    first.write_text("Already searchable.", encoding="utf-8")
    assert sdk.index_document(str(first))["success"]

    embedding = threading.Event()
    release = threading.Event()
    encode = sdk._encode_texts

    def slow_encode(texts, **kwargs):
        embedding.set()
        assert release.wait(10)
        return encode(texts, **kwargs)

    sdk._encode_texts = slow_encode
    second = tmp_path / "second.txt"
    second.write_text("Slow to embed.", encoding="utf-8")
    worker = threading.Thread(target=sdk.index_document, args=(str(second),))
    worker.start()
    try:
        assert embedding.wait(5)
        acquired = sdk._state_lock.acquire(timeout=1)
        assert acquired, "index_document held the state lock while embedding"
        sdk._state_lock.release()
        assert sdk._snapshot_query_state()["indexed_file_count"] == 1
    finally:
        release.set()
        worker.join(10)
    assert sdk.indexed_files == {str(first), str(second)}
