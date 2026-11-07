# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document edited after indexing is re-indexed, not served stale (#4738)."""

import errno
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.utils import file_lock

OLD = "The launch is on Tuesday in Lisbon. " * 30
NEW = "The launch moved to Friday in Oslo. " * 30


def _make(tmp_path):
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


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "plan.txt"
    path.write_text(OLD, encoding="utf-8")
    return path


def _served(sdk):
    return " ".join(sdk.chunks)


def _write_keeping_stat(path: Path, text: str):
    """Rewrite *path* with the same size and modification time as before."""
    st = path.stat()
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_edited_document_is_reindexed(tmp_path, doc):
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]

    doc.write_text(NEW, encoding="utf-8")
    result = sdk.index_document(str(doc))

    assert result["success"], result
    assert not result.get("already_indexed")
    assert result.get("reindexed") is True
    assert "Oslo" in _served(sdk)
    assert "Lisbon" not in _served(sdk)
    assert len(sdk.indexed_files) == 1


def test_unchanged_document_is_not_reembedded(tmp_path, doc):
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]
    encoded = sdk.encoded

    result = sdk.index_document(str(doc))

    assert result["already_indexed"] is True
    assert sdk.encoded == encoded


def test_same_size_edit_with_restored_mtime_is_caught(tmp_path, doc):
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]
    same_size = OLD.replace("Lisbon", "Madrid")
    assert len(same_size) == len(OLD)

    _write_keeping_stat(doc, same_size)
    result = sdk.index_document(str(doc))

    assert result.get("reindexed") is True
    assert "Madrid" in _served(sdk)
    assert "Lisbon" not in _served(sdk)


def test_edit_then_revert_is_still_current(tmp_path, doc):
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]

    doc.write_text(NEW, encoding="utf-8")
    doc.write_text(OLD, encoding="utf-8")

    assert sdk.index_document(str(doc))["already_indexed"] is True


def test_is_current_tracks_edits_and_removal(tmp_path, doc):
    sdk = _make(tmp_path)
    assert sdk.is_current(str(doc)) is False
    assert sdk.index_document(str(doc))["success"]
    assert sdk.is_current(str(doc)) is True

    doc.write_text(NEW, encoding="utf-8")
    assert sdk.is_current(str(doc)) is False

    assert sdk.remove_document(str(doc))
    assert sdk.is_current(str(doc)) is False


def test_reindex_builds_without_holding_the_index_lock(tmp_path, doc):
    """The minutes-long part of a re-index (extract + embed) runs without the
    index lock: another thread can take the lock meanwhile, and the old
    version stays served until the new one swaps in (#6911)."""
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]
    doc.write_text(NEW, encoding="utf-8")

    building = threading.Event()
    release = threading.Event()
    real_encode = sdk._encode_texts

    def encode(texts, **kwargs):
        building.set()
        release.wait(timeout=10)
        return real_encode(texts, **kwargs)

    sdk._encode_texts = encode
    outcome = {}

    def run():
        outcome["result"] = sdk.reindex_document(str(doc))

    worker = threading.Thread(target=run)
    worker.start()
    assert building.wait(timeout=10), "the re-embed never started"

    observations = {}
    observations["lock_free"] = sdk._state_lock.acquire(blocking=False)
    if observations["lock_free"]:
        sdk._state_lock.release()
    observations["old_served"] = "Lisbon" in _served(sdk)

    release.set()
    worker.join(timeout=30)
    assert not worker.is_alive()

    assert outcome["result"]["success"], outcome
    assert (
        observations["lock_free"] is True
    ), "the index lock was held during the re-embed"
    assert observations["old_served"] is True
    assert "Oslo" in _served(sdk)
    assert "Lisbon" not in _served(sdk)


def test_a_failed_reindex_keeps_documents_indexed_by_others(tmp_path, doc):
    """Another writer's document, indexed while a re-index builds, survives
    that re-index failing. Restoring a whole-state snapshot would wipe it,
    and the shared vector index grows in place, so only the failed file is
    cleaned up (#6917)."""
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]
    doc.write_text(NEW, encoding="utf-8")

    other = tmp_path / "other.txt"
    other.write_text("Another document entirely. " * 20, encoding="utf-8")
    real_encode = sdk._encode_texts
    state = {"indexed_other": False}

    def encode(texts, **kwargs):
        if not state["indexed_other"]:
            state["indexed_other"] = True
            # Another writer publishes a document while the re-index of *doc*
            # is still building, then the embedder dies.
            assert sdk.index_document(str(other))["success"]
            raise RuntimeError("embedder exploded")
        return real_encode(texts, **kwargs)

    sdk._encode_texts = encode
    result = sdk.reindex_document(str(doc))

    assert not result["success"], result
    assert str(other) in sdk.indexed_files
    assert "Another document entirely" in _served(sdk)


def test_reindex_failure_restores_the_previous_state(tmp_path, doc):
    """A re-index that fails partway leaves the old version indexed and
    served; the swap happens only on success."""
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]
    doc.write_text(NEW, encoding="utf-8")

    def boom(texts, **kwargs):
        raise RuntimeError("embedder exploded")

    sdk._encode_texts = boom
    result = sdk.reindex_document(str(doc))

    assert not result["success"], result
    assert "Lisbon" in _served(sdk)
    assert "Oslo" not in _served(sdk)
    assert len(sdk.indexed_files) == 1


def test_edit_that_cannot_be_indexed_reports_an_error(tmp_path, doc):
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]

    doc.write_text("   \n\t  \n", encoding="utf-8")
    result = sdk.index_document(str(doc))

    assert result["success"] is False
    assert not result.get("already_indexed")
    assert result.get("error")


def test_edit_racing_an_older_index_publishes_the_newer_text(tmp_path, doc):
    """Another writer publishes the old bytes while this call embeds the new ones."""
    sdk = _make(tmp_path)
    real = sdk._create_vector_index
    raced = []

    def _racing(*args, **kwargs):
        if not raced:
            raced.append(True)
            doc.write_text(OLD, encoding="utf-8")
            assert sdk.index_document(str(doc))["success"]
            doc.write_text(NEW, encoding="utf-8")
        return real(*args, **kwargs)

    doc.write_text(NEW, encoding="utf-8")
    sdk._create_vector_index = _racing
    result = sdk.index_document(str(doc))

    assert result["success"], result
    assert not result.get("already_indexed")
    assert "Oslo" in _served(sdk)
    assert "Lisbon" not in _served(sdk)
    assert sdk.is_current(str(doc)) is True


def test_reindex_failure_keeps_the_fingerprint_consistent(tmp_path, doc):
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]
    doc.write_text("   ", encoding="utf-8")
    assert sdk.index_document(str(doc))["success"] is False

    # The old generation was restored; restoring its bytes makes it current again.
    doc.write_text(OLD, encoding="utf-8")
    assert sdk.is_current(str(doc)) is True
    assert sdk.index_document(str(doc))["already_indexed"] is True


def test_locked_indexed_document_reports_the_lock_and_keeps_its_index(
    tmp_path, doc, monkeypatch
):
    sdk = _make(tmp_path)
    assert sdk.index_document(str(doc))["success"]
    served = _served(sdk)
    monkeypatch.setattr(file_lock, "_WINDOWS", True)

    def _locked(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", str(doc))

    monkeypatch.setattr(sdk, "_safe_open", _locked)
    result = sdk.index_document(str(doc))

    assert result["success"] is False
    assert result["error_type"] == "file_locked"
    assert _served(sdk) == served
    assert str(doc.absolute()) in sdk.indexed_files


# --- the agent's index_document tool -------------------------------------


@pytest.fixture
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


def test_tool_reindexes_an_edited_document(tmp_path, doc, _clean_registry):
    sdk = _make(tmp_path)
    _RagHost(sdk).register_rag_tools()
    index = _TOOL_REGISTRY["index_document"]["function"]

    first = index(file_path=str(doc))
    assert first["status"] == "success", first
    again = index(file_path=str(doc))
    assert again["already_indexed"] is True

    doc.write_text(NEW, encoding="utf-8")
    edited = index(file_path=str(doc))

    assert edited["status"] == "success", edited
    assert edited["already_indexed"] is False
    assert edited["reindexed"] is True
    assert "Oslo" in _served(sdk)
    assert "Lisbon" not in _served(sdk)
