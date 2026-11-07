# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Un-scoped document queries kick a background re-index for edited files
(#6939).

The query itself may still serve an edited file's old generation — the swap
happens when the background re-index finishes — but it names the files that
are being refreshed, and the next query serves their current text.
"""

import threading
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import rag_tools
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig

CALL_DEADLINE_S = 5.0

OLD = "The launch is on Tuesday in Lisbon. " * 30
NEW = "The launch moved to Friday in Oslo. " * 30


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture(autouse=True)
def _clean_kick_failures():
    rag_tools._KICK_FAILURES.clear()
    yield
    rag_tools._KICK_FAILURES.clear()


def _wait_no_jobs(rag, deadline_s: float = 10.0):
    """Wait until every background index job for *rag* has finished."""
    waited = time.monotonic() + deadline_s
    while time.monotonic() < waited:
        if not rag_tools.documents_still_indexing(rag):
            return True
        time.sleep(0.05)
    return False


class _RagHost(RAGToolsMixin):
    max_chunks = 5

    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query: str):
        return [query]

    def _no_documents_message(self):
        return "No documents are indexed."


def _call(name, **kwargs):
    holder = {}
    worker = threading.Thread(
        target=lambda: holder.update(result=_TOOL_REGISTRY[name]["function"](**kwargs)),
        daemon=True,
    )
    worker.start()
    worker.join(CALL_DEADLINE_S)
    assert not worker.is_alive(), f"{name} blocked"
    return holder["result"]


def _host_for(rag):
    host = _RagHost(rag)
    host.register_rag_tools()
    return host


def test_query_documents_kicks_a_reindex_and_names_the_file(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("old text", encoding="utf-8")
    rag = MagicMock()
    rag.index = MagicMock()
    rag.chunks = ["old text"]
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = False  # the file changed since indexing
    rag.file_metadata = {}
    rag._retrieve_chunks.return_value = (["old text"], [1.0])
    rag.chunk_to_file = {0: str(doc)}
    host = _host_for(rag)

    result = _call("query_documents", query="what?")

    assert result["status"] == "success", result
    rag.index_document.assert_called_once()
    assert result.get("reindexing_files") == ["doc.txt"]


def test_query_documents_skips_the_kick_when_everything_is_current(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("same text", encoding="utf-8")
    rag = MagicMock()
    rag.index = MagicMock()
    rag.chunks = ["same text"]
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = True
    rag.file_metadata = {}
    rag._retrieve_chunks.return_value = (["same text"], [1.0])
    rag.chunk_to_file = {0: str(doc)}
    host = _host_for(rag)

    result = _call("query_documents", query="what?")

    assert result["status"] == "success", result
    rag.index_document.assert_not_called()
    assert "reindexing_files" not in result


def test_search_indexed_chunks_also_kicks_the_reindex(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("old text", encoding="utf-8")
    rag = MagicMock()
    rag.chunks = ["old text"]
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = False
    host = _host_for(rag)

    result = _call("search_indexed_chunks", pattern="old")
    assert _wait_no_jobs(rag), "the background re-index never finished"

    assert result["status"] == "success", result
    rag.index_document.assert_called_once()
    assert result.get("reindexing_files") == ["doc.txt"]


def test_a_failed_reindex_is_reported_without_breaking_the_query(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("old text", encoding="utf-8")
    rag = MagicMock()
    rag.index = MagicMock()
    rag.chunks = ["old text"]
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = False
    rag.file_metadata = {}
    rag._retrieve_chunks.return_value = (["old text"], [1.0])
    rag.chunk_to_file = {0: str(doc)}
    rag.index_document.side_effect = RuntimeError("file locked")
    host = _host_for(rag)

    result = _call("query_documents", query="what?")
    assert _wait_no_jobs(rag), "the background re-index never finished"

    # The job's failure is not the query's: the answer still succeeds (serving
    # the old generation) and the file is reported as failed, not re-kicked
    # on every query afterwards.
    assert result["status"] == "success", result
    assert result.get("reindex_failed") == ["doc.txt"], result

    again = _call("query_documents", query="what?")
    assert again["status"] == "success", again
    assert again.get("reindex_failed") == ["doc.txt"], again
    assert (
        rag.index_document.call_count == 1
    ), "a failed re-index must not be re-kicked until its bytes change again"


def test_a_deleted_indexed_file_does_not_break_searches(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("old text", encoding="utf-8")
    rag = MagicMock()
    rag.index = MagicMock()
    rag.chunks = ["old text"]
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = False
    rag.file_metadata = {}
    rag._retrieve_chunks.return_value = (["old text"], [1.0])
    rag.chunk_to_file = {0: str(doc)}

    def index_document(path, **_kwargs):
        raise FileNotFoundError(str(path))

    rag.index_document.side_effect = index_document
    host = _host_for(rag)

    first = _call("query_documents", query="what?")
    assert first["status"] == "success", first
    assert first.get("reindex_failed") == ["doc.txt"], first

    second = _call("query_documents", query="what?")
    assert second["status"] == "success", second
    assert second.get("reindex_failed") == ["doc.txt"], second


def _real_sdk(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    config = RAGConfig(
        cache_dir=str(tmp_path / "cache"),
        allowed_paths=[str(tmp_path)],
        use_llm_chunking=False,
    )
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(config)
    sdk.encoded = 0

    def _encode(texts, **_kwargs):
        sdk.encoded += len(texts)
        return np.ones((len(texts), 4), dtype="float32")

    sdk._load_embedder = lambda: None
    sdk._encode_texts = _encode
    sdk._get_hmac_key = lambda: b"test-cache-key" * 3
    return sdk


def _wait_current(sdk, path: str, deadline_s: float = 10.0):
    waited = time.monotonic() + deadline_s
    while time.monotonic() < waited:
        if sdk.is_current(path):
            return True
        time.sleep(0.05)
    return False


def test_a_real_library_kicks_the_reindex_and_serves_current_text_afterwards(
    tmp_path,
):
    """End to end on the real SDK: the edited file's old text is served once
    with the file named as re-indexing, and the next query serves the
    current text (#6939)."""
    doc = tmp_path / "plan.txt"
    doc.write_text(OLD, encoding="utf-8")
    sdk = _real_sdk(tmp_path)
    assert sdk.index_document(str(doc))["success"]

    doc.write_text(NEW, encoding="utf-8")
    host = _RagHost(sdk)
    host.register_rag_tools()

    first = _call("query_documents", query="launch location")
    assert first["status"] == "success", first
    assert first.get("reindexing_files") == ["plan.txt"]
    served_first = " ".join(c.get("content", "") for c in first.get("chunks", []))
    assert "Lisbon" in served_first

    assert _wait_current(sdk, str(doc)), "the background re-index never finished"

    second = _call("query_documents", query="launch location")
    assert second["status"] == "success", second
    served_second = " ".join(c.get("content", "") for c in second.get("chunks", []))
    assert "Oslo" in served_second
    assert "Lisbon" not in served_second
    assert "reindexing_files" not in second
