# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Removing a document reuses the stored vectors instead of re-embedding (#3536)."""

import hashlib
import threading
from unittest.mock import patch

import numpy as np
import pytest

from gaia.rag.sdk import RAGSDK, RAGConfig

DIM = 8


def _vector(text: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "little")
    return np.random.default_rng(seed).random(DIM).astype("float32")


@pytest.fixture
def rag(tmp_path):
    pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(
            RAGConfig(cache_dir=str(tmp_path / "cache"), allowed_paths=[str(tmp_path)])
        )
    sdk.encoded = []

    def encode(texts, **kwargs):
        sdk.encoded.extend(texts)
        return np.stack([_vector(t) for t in texts]) if texts else np.zeros((0, DIM))

    sdk._load_embedder = lambda: None
    sdk._encode_texts = encode
    sdk._get_hmac_key = lambda: b"test-index-key" * 3
    return sdk


def _index(rag, tmp_path, count, prefix="doc"):
    paths = []
    for n in range(count):
        doc = tmp_path / f"{prefix}{n}.txt"
        doc.write_text(f"{prefix} number {n} talks about topic {n}.", encoding="utf-8")
        assert rag.index_document(str(doc))["success"]
        paths.append(str(doc))
    return paths


def _assert_vectors_match_chunks(rag):
    """Row i of the index is the embedding of chunk i, for every chunk."""
    assert rag.index.ntotal == len(rag.chunks)
    stored = rag.index.reconstruct_n(0, rag.index.ntotal)
    expected = np.stack([_vector(c) for c in rag.chunks])
    np.testing.assert_array_equal(stored, expected)


@pytest.mark.parametrize("victim", [0, 2, 4])
def test_remove_makes_no_embedding_calls(rag, tmp_path, victim):
    paths = _index(rag, tmp_path, 5)
    rag.encoded.clear()

    assert rag.remove_document(paths[victim])

    assert rag.encoded == []
    assert paths[victim] not in rag.indexed_files
    _assert_vectors_match_chunks(rag)


def test_search_after_remove_still_finds_the_right_chunk(rag, tmp_path):
    paths = _index(rag, tmp_path, 4)
    assert rag.remove_document(paths[1])
    for path in (paths[0], paths[2], paths[3]):
        (row,) = rag.file_to_chunk_indices[path]
        query = _vector(rag.chunks[row])[None, :]
        _, ids = rag.index.search(query, 1)
        assert ids[0][0] == row
        assert rag.chunk_to_file[row] == path


def test_removing_every_document_one_by_one(rag, tmp_path):
    paths = _index(rag, tmp_path, 3)
    rag.encoded.clear()
    for path in reversed(paths):
        assert rag.remove_document(path)
    assert rag.encoded == []
    assert rag.index is None
    assert rag.chunks == []


def test_remove_leaves_the_previous_index_object_untouched(rag, tmp_path):
    """A query holding a snapshot keeps a consistent index/chunks pair."""
    paths = _index(rag, tmp_path, 3)
    snapshot = rag._snapshot_query_state()
    before = snapshot["index"].reconstruct_n(0, snapshot["index"].ntotal).copy()

    assert rag.remove_document(paths[0])

    assert snapshot["index"] is not rag.index
    assert snapshot["index"].ntotal == len(snapshot["chunks"]) == 3
    np.testing.assert_array_equal(snapshot["index"].reconstruct_n(0, 3), before)


def test_eviction_of_several_documents_makes_no_embedding_calls(rag, tmp_path):
    paths = _index(rag, tmp_path, 4)
    rag.config.max_indexed_files = 1
    rag.encoded.clear()

    newcomer = tmp_path / "new.txt"
    newcomer.write_text("A brand new document.", encoding="utf-8")
    result = rag.index_document(str(newcomer))

    assert result["success"]
    assert result["evicted"] == paths
    # Only the new document is embedded; the four evictions cost nothing.
    assert rag.encoded == list(rag.chunks)
    _assert_vectors_match_chunks(rag)


def test_failed_reindex_restores_the_old_generation(rag, tmp_path):
    paths = _index(rag, tmp_path, 3)
    before = rag.index.reconstruct_n(0, rag.index.ntotal).copy()
    chunks_before = list(rag.chunks)

    with patch.object(rag, "index_document", return_value={"success": False}):
        assert not rag.reindex_document(paths[1])["success"]

    assert rag.chunks == chunks_before
    np.testing.assert_array_equal(rag.index.reconstruct_n(0, 3), before)


def test_a_vector_count_mismatch_fails_the_removal_loudly(rag, tmp_path):
    paths = _index(rag, tmp_path, 3)
    rag.index.add(_vector("stray")[None, :])
    chunks_before = list(rag.chunks)

    with patch.object(rag.log, "error") as logged:
        assert not rag.remove_document(paths[0])

    assert "re-index" in logged.call_args.args[0]
    assert rag.chunks == chunks_before
    assert paths[0] in rag.indexed_files


def test_remove_unknown_document_is_a_no_op(rag, tmp_path):
    paths = _index(rag, tmp_path, 2)
    rag.encoded.clear()
    assert not rag.remove_document(str(tmp_path / "never-indexed.txt"))
    assert rag.encoded == []
    assert rag.indexed_files == set(paths)


def test_concurrent_removals_keep_index_and_chunks_aligned(rag, tmp_path):
    paths = _index(rag, tmp_path, 8)
    rag.encoded.clear()
    threads = [
        threading.Thread(target=rag.remove_document, args=(p,)) for p in paths[:6]
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert rag.encoded == []
    assert rag.indexed_files == set(paths[6:])
    _assert_vectors_match_chunks(rag)
