# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A file-scoped query must serve the file's current bytes (#6932).

An edited document re-indexes inside ``query_specific_file`` with the same
foreground budget the index tool uses, so the answer is never built from the
old generation and nothing stale is served silently.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import rag_tools
from gaia.agents.tools.rag_tools import RAGToolsMixin

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
    max_chunks = 5

    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.rebuild_system_prompt = MagicMock()

    def _generate_search_keys(self, query: str):
        return [query]


def _call(name, **kwargs):
    holder = {}
    worker = threading.Thread(
        target=lambda: holder.update(result=_TOOL_REGISTRY[name]["function"](**kwargs)),
        daemon=True,
    )
    worker.start()
    worker.join(CALL_DEADLINE_S)
    assert not worker.is_alive(), f"{name} blocked on a slow re-index"
    return holder["result"]


def _host_for(rag):
    host = _RagHost(rag)
    host.register_rag_tools()
    return host


def test_a_stale_file_reindexes_before_the_scoped_query(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("old text", encoding="utf-8")
    rag = MagicMock()
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = False  # the file changed since indexing
    rag.file_metadata = {}
    rag.index_document.return_value = {"success": True, "file_name": "doc.txt"}
    rag._retrieve_chunks_from_file.return_value = (["current text"], [1.0])
    rag.chunks = ["current text"]
    rag.chunk_to_file = {0: str(doc)}
    host = _host_for(rag)

    result = _call("query_specific_file", file_path=str(doc), query="what changed?")

    assert result["status"] == "success", result
    rag.index_document.assert_called_once()
    rag._retrieve_chunks_from_file.assert_called_once()


def test_a_current_file_skips_the_re_index(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("same text", encoding="utf-8")
    rag = MagicMock()
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = True
    rag.file_metadata = {}
    rag._retrieve_chunks_from_file.return_value = (["text"], [1.0])
    rag.chunks = ["text"]
    rag.chunk_to_file = {0: str(doc)}
    host = _host_for(rag)

    result = _call("query_specific_file", file_path=str(doc), query="what?")

    assert result["status"] == "success", result
    rag.index_document.assert_not_called()


def test_a_slow_reindex_reports_in_progress(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("old text", encoding="utf-8")
    rag = MagicMock()
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = False

    release = threading.Event()

    def index_document(path, progress_callback=None, wait_callback=None):
        release.wait(30)
        return {"success": True, "file_name": "doc.txt"}

    rag.index_document.side_effect = index_document
    host = _host_for(rag)

    result = _call("query_specific_file", file_path=str(doc), query="what?")

    assert result["status"] == "in_progress", result
    release.set()


def test_a_failed_reindex_keeps_the_actionable_error_types(tmp_path):
    """A locked file and an unavailable embedder keep their specific advice
    when the re-index happens inside a scoped query (#6937 review)."""
    doc = tmp_path / "report.docx"
    doc.write_text("old text", encoding="utf-8")
    rag = MagicMock()
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = False
    rag._generate_search_keys.return_value = ["q"]
    rag.index_document.return_value = {
        "success": False,
        "error": "cache lock",
        "error_type": "cache_locked",
    }
    host = _host_for(rag)

    with patch.object(rag_tools, "_CACHE_LOCK_HINTS", {"cache_locked": "close it"}):
        result = _call(
            "query_specific_file", file_path=str(doc), query="what?"
        )

    assert result["status"] == "error", result
    assert result.get("hint") == "close it"


def test_a_successful_reindex_forgets_evicted_documents(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("old text", encoding="utf-8")
    other = tmp_path / "evicted.txt"
    other.write_text("victim " * 40, encoding="utf-8")
    rag = MagicMock()
    rag.indexed_files = {str(doc), str(other)}
    rag.is_current.return_value = False
    rag._generate_search_keys.return_value = ["q"]
    rag._retrieve_chunks_from_file.return_value = (["current text"], [1.0])
    rag.chunks = ["current text"]
    rag.chunk_to_file = {0: str(doc)}
    rag.index_document.return_value = {
        "success": True,
        "file_name": "doc.txt",
        "evicted": [str(other)],
    }
    host = _host_for(rag)

    result = _call(
        "query_specific_file", file_path=str(doc), query="what?"
    )

    assert result["status"] == "success", result
    assert str(other) not in host.indexed_files


def test_a_failed_reindex_reports_the_error(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("old text", encoding="utf-8")
    rag = MagicMock()
    rag.indexed_files = {str(doc)}
    rag.is_current.return_value = False
    rag.index_document.return_value = {"success": False, "error": "file locked"}
    host = _host_for(rag)

    result = _call("query_specific_file", file_path=str(doc), query="what?")

    assert result["status"] == "error", result
    assert "Re-indexing doc.txt failed" in result["error"]
    assert "file locked" in result["error"]
