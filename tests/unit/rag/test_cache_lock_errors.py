# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A cache folder the user cannot write is not a busy cache, nor a bad document."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools import rag_tools
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig, _cache_lock_target
from gaia.utils import file_lock as file_lock_mod
from tests.unit.denied_folder import cannot_create_files_in


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _RagHost(RAGToolsMixin):
    def __init__(self, rag):
        self.rag = rag
        self.indexed_files = set()
        self.current_session = None
        self.max_chunks = 5
        self.rebuild_system_prompt = MagicMock()


def _tool(name, **kwargs):
    return _TOOL_REGISTRY[name]["function"](**kwargs)


@pytest.fixture
def make_sdk(tmp_path):
    pytest.importorskip("faiss", reason="RAGSDK requires faiss-cpu")

    def make(cache_dir):
        with patch("gaia.rag.sdk.AgentSDK"):
            rag = RAGSDK(
                RAGConfig(
                    cache_dir=str(cache_dir),
                    allowed_paths=[str(tmp_path)],
                    show_stats=False,
                )
            )
        rag._get_hmac_key = lambda: b"test-index-key" * 3
        rag._load_embedder = lambda: None
        rag._encode_texts = lambda texts, **kw: np.ones((len(texts), 4), "float32")
        return rag

    return make


def test_an_unwritable_cache_folder_is_reported_as_such(make_sdk, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    doc = tmp_path / "notes.txt"
    doc.write_text("the vacation policy is 20 days", encoding="utf-8")
    rag = make_sdk(cache)
    with cannot_create_files_in(cache):
        stats = rag.index_document(str(doc))
    assert stats["success"] is False
    assert stats["error_type"] == "cache_unwritable", stats
    assert stats["cache_dir"] == str(cache)
    assert str(cache) in stats["error"]
    assert "GAIA operation" not in stats["error"]


def test_a_cache_another_process_holds_is_busy(make_sdk, tmp_path, monkeypatch):
    monkeypatch.setattr(file_lock_mod, "LOCK_TIMEOUT_SECONDS", 0.2)
    cache = tmp_path / "cache"
    doc = tmp_path / "notes.txt"
    doc.write_text("the vacation policy is 20 days", encoding="utf-8")
    rag = make_sdk(cache)
    held, release = threading.Event(), threading.Event()

    def other_process():
        with file_lock_mod.file_lock(_cache_lock_target(str(cache / "x.json"))):
            held.set()
            release.wait(30)

    holder = threading.Thread(target=other_process)
    holder.start()
    assert held.wait(10)
    try:
        stats = rag.index_document(str(doc))
    finally:
        release.set()
        holder.join()
    assert stats["error_type"] == "cache_busy", stats
    assert "1 other GAIA operation was ahead" in stats["error"]
    assert stats["cache_dir"] == str(cache)


@pytest.mark.parametrize(
    "error_type, says",
    [
        ("cache_unwritable", "fix that folder's permissions"),
        ("cache_busy", "Another process is using the document cache"),
        ("cache_lock_error", "could not open the lock file"),
    ],
)
def test_index_document_hint_matches_the_cause(tmp_path, error_type, says):
    doc = tmp_path / "notes.txt"
    doc.write_text("hello", encoding="utf-8")
    cache = str(tmp_path / "cache")
    rag = MagicMock()
    rag.index_document.return_value = {
        "success": False,
        "file_name": "notes.txt",
        "error": "lock failed",
        "error_type": error_type,
        "cache_dir": cache,
    }
    _RagHost(rag).register_rag_tools()
    result = _tool("index_document", file_path=str(doc))
    assert result["status"] == "error"
    assert result["error_type"] == error_type
    assert says in result["hint"]
    assert cache in result["hint"]
    assert "check the file" not in result["hint"]


def test_auto_index_on_query_carries_the_cache_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(rag_tools, "INDEX_FOREGROUND_BUDGET_S", 5, raising=False)
    doc = tmp_path / "notes.txt"
    doc.write_text("hello", encoding="utf-8")
    cache = str(tmp_path / "cache")
    rag = MagicMock()
    rag.indexed_files = set()
    rag.index_document.return_value = {
        "success": False,
        "error": "lock failed",
        "error_type": "cache_unwritable",
        "cache_dir": cache,
    }
    host = _RagHost(rag)
    host.register_rag_tools()
    result = _tool("query_specific_file", file_path=str(doc), query="vacation")
    assert result["status"] == "error", result
    assert result["error_type"] == "cache_unwritable"
    assert cache in result["hint"]
