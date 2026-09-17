# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Real-index regression tests for failed document indexing and replacement."""

import errno
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import numpy as np
import pytest

from gaia.rag.sdk import RAGSDK, RAGConfig


@pytest.fixture
def rag(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(cache_dir=str(tmp_path / "cache"), allowed_paths=[str(tmp_path)])
        )
    sdk._load_embedder = lambda: None
    sdk._encode_texts = lambda texts, **kwargs: np.ones(
        (len(texts), 4), dtype="float32"
    )
    sdk._get_hmac_key = lambda: b"test-index-key" * 3
    return sdk


def _assert_documents(rag, expected):
    assert rag.indexed_files == {str(path) for path in expected}
    assert len(rag.chunks) == len(expected)
    assert (rag.index.ntotal if rag.index is not None else 0) == len(expected)
    assert set(rag.chunk_to_file.values()) == rag.indexed_files
    assert set(rag.file_to_chunk_indices) == rag.indexed_files
    assert set(rag.file_indices) == rag.indexed_files
    assert set(rag.file_embeddings) == rag.indexed_files
    assert set(rag.file_metadata) == rag.indexed_files
    assert set(rag.file_access_times) == rag.indexed_files
    assert set(rag.file_index_times) == rag.indexed_files
    for path, text in expected.items():
        positions = rag.file_to_chunk_indices[str(path)]
        assert len(positions) == 1
        assert text in rag.chunks[positions[0]]


@pytest.mark.parametrize("with_existing", [False, True])
@pytest.mark.parametrize("failure", ["_save_cache", "_save_extracted_markdown"])
def test_cache_failure_retry_and_remove_leave_no_orphan_vectors(
    rag, tmp_path, with_existing, failure
):
    expected = {}
    if with_existing:
        existing = tmp_path / "existing.txt"
        existing.write_text("Keep the existing document.", encoding="utf-8")
        assert rag.index_document(str(existing))["success"]
        expected[existing] = "Keep the existing document."
    document = tmp_path / "new.txt"
    document.write_text("The new document.", encoding="utf-8")
    with patch.object(rag, failure, side_effect=OSError(errno.ENOSPC, "disk full")):
        assert not rag.index_document(str(document))["success"]
    _assert_documents(rag, expected)
    assert rag.index_document(str(document))["success"]
    _assert_documents(rag, {**expected, document: "The new document."})
    assert rag.remove_document(str(document))
    _assert_documents(rag, expected)


def test_cache_failure_at_capacity_does_not_evict_existing_document(rag, tmp_path):
    existing = tmp_path / "existing.txt"
    existing.write_text("Keep this document.", encoding="utf-8")
    assert rag.index_document(str(existing))["success"]
    rag.config.max_indexed_files = 1
    document = tmp_path / "new.txt"
    document.write_text("New document content.", encoding="utf-8")
    with patch.object(rag, "_save_cache", side_effect=OSError("disk full")):
        assert not rag.index_document(str(document))["success"]
    _assert_documents(rag, {existing: "Keep this document."})


@pytest.mark.parametrize("failure", ["cache", "extraction"])
@pytest.mark.parametrize("with_existing", [False, True])
def test_failed_replacement_preserves_previous_document(
    rag, tmp_path, failure, with_existing
):
    expected = {}
    document = tmp_path / "replace.txt"
    expected[document] = "Original document content."
    if with_existing:
        expected[tmp_path / "other.txt"] = "Unrelated document content."
    for path, text in expected.items():
        path.write_text(text, encoding="utf-8")
        assert rag.index_document(str(path))["success"]
    document.write_text("Replacement document content.", encoding="utf-8")
    method = "_save_cache" if failure == "cache" else "_extract_text_from_file"
    with patch.object(rag, method, side_effect=OSError("injected replacement failure")):
        result = rag.reindex_document(str(document))
    assert not result["success"]
    assert result["total_indexed_files"] == len(expected)
    assert result["total_chunks"] == len(expected)
    _assert_documents(rag, expected)
    assert rag.reindex_document(str(document))["success"]
    expected[document] = "Replacement document content."
    _assert_documents(rag, expected)


def test_concurrent_same_document_publishes_once_after_persistence(rag, tmp_path):
    document = tmp_path / "shared.txt"
    document.write_text("Shared document content.", encoding="utf-8")
    extracting = threading.Barrier(2)
    extract = rag._extract_text_from_file
    save = rag._save_cache

    def extract_together(path):
        result = extract(path)
        extracting.wait(timeout=5)
        return result

    def save_before_publication(path, data):
        _assert_documents(rag, {})
        save(path, data)

    with (
        patch.object(rag, "_extract_text_from_file", side_effect=extract_together),
        patch.object(rag, "_save_cache", side_effect=save_before_publication),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        results = list(executor.map(rag.index_document, [str(document)] * 2))
    assert all(result["success"] for result in results)
    assert sum(bool(result.get("already_indexed")) for result in results) == 1
    _assert_documents(rag, {document: "Shared document content."})
