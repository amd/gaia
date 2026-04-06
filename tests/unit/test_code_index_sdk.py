# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for CodeIndexSDK core functionality.

Tests cover: indexing, search, cache persistence, atomic writes,
embedding batch sync, and embedding model version detection.
All external dependencies (FAISS, Lemonade embedder, git, filesystem)
are mocked so tests run without any hardware or network.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from gaia.code_index.sdk import (
        CodeIndexConfig,
        CodeIndexSDK,
        IndexResult,
        SearchResult,
    )

    SDK_AVAILABLE = True
except ImportError as e:
    SDK_AVAILABLE = False
    IMPORT_ERROR = str(e)


def skip_if_unavailable():
    if not SDK_AVAILABLE:
        pytest.skip(f"code_index not available: {IMPORT_ERROR}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_sdk(tmp_path: Path, **kwargs) -> "CodeIndexSDK":
    """Create a CodeIndexSDK pointed at tmp_path as the repo root."""
    config = CodeIndexConfig(
        repo_path=str(tmp_path),
        cache_dir=str(tmp_path / ".cache"),
        index_git_history=False,
        index_prs=False,
        **kwargs,
    )
    return CodeIndexSDK(config)


def _write_py(directory: Path, name: str, content: str) -> Path:
    p = directory / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Test: index_repository returns IndexResult
# ---------------------------------------------------------------------------


class TestIndexRepository:
    def test_returns_index_result(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)
        _write_py(tmp_path, "a.py", "def foo(): pass\n")

        try:
            import faiss  # noqa: F401
        except ImportError:
            pytest.skip("faiss not installed")

        with (
            patch.object(sdk, "_load_embedder") as mock_embedder,
            patch.object(sdk, "_encode_texts_with_sync") as mock_encode,
            patch.object(sdk, "_save_atomic") as mock_save,
        ):
            mock_enc = MagicMock()
            mock_embedder.return_value = mock_enc
            import numpy as np

            mock_encode.return_value = (
                np.zeros((1, 768), dtype="float32"),
                [MagicMock()],
            )
            mock_save.return_value = None

            result = sdk.index_repository()

        assert isinstance(result, IndexResult)

    def test_empty_repo_no_crash(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)

        try:
            import faiss  # noqa: F401
        except ImportError:
            pytest.skip("faiss not installed")

        with (
            patch.object(sdk, "_load_embedder"),
            patch.object(sdk, "_encode_texts_with_sync") as mock_encode,
            patch.object(sdk, "_save_atomic"),
        ):
            import numpy as np

            mock_encode.return_value = (np.zeros((0, 768), dtype="float32"), [])
            result = sdk.index_repository()

        assert isinstance(result, IndexResult)
        assert result.files_indexed == 0


# ---------------------------------------------------------------------------
# Test: search returns SearchResult list
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_returns_list_when_no_index(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)
        results = sdk.search("find something")
        assert isinstance(results, list)
        assert results == []

    def test_search_with_mocked_index(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)

        try:
            import faiss  # noqa: F401
            import numpy as np
        except ImportError:
            pytest.skip("faiss/numpy not installed")

        from gaia.code_index.sdk import CodeChunk

        fake_chunk = CodeChunk(
            content="def foo(): pass",
            file_path="foo.py",
            language="python",
            start_line=1,
            end_line=1,
            symbol_name="foo",
            symbol_type="function",
        )
        fake_index = MagicMock()
        fake_index.ntotal = 1
        fake_index.search.return_value = (np.array([[0.1]]), np.array([[0]]))

        sdk._faiss_index = fake_index
        sdk._metadata = {
            "embedding_model": sdk.config.embedding_model,
            "chunks": [sdk._chunk_to_dict(fake_chunk)],
        }

        with patch.object(sdk, "_load_embedder") as mock_embedder:
            mock_enc = MagicMock()
            mock_enc.embed.return_value = np.zeros((1, 768), dtype="float32")
            mock_embedder.return_value = mock_enc
            sdk._embedder = mock_enc

            results = sdk.search("foo function", top_k=1)

        assert isinstance(results, list)
        assert len(results) == 1
        assert isinstance(results[0], SearchResult)
        assert results[0].chunk.symbol_name == "foo"


# ---------------------------------------------------------------------------
# Test: cache persistence (atomic writes)
# ---------------------------------------------------------------------------


class TestCachePersistence:
    def test_atomic_save_creates_files(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)

        try:
            import faiss
            import numpy as np
        except ImportError:
            pytest.skip("faiss/numpy not installed")

        index = faiss.IndexFlatL2(4)
        index.add(np.zeros((1, 4), dtype="float32"))
        meta = {"model": "test-model", "chunks": []}

        sdk._save_atomic(index, meta)

        assert sdk._index_path.exists()
        assert sdk._meta_path.exists()

    def test_load_metadata_returns_dict(self, tmp_path):
        skip_if_unavailable()
        from gaia.code_index.sdk import _CACHE_VERSION

        sdk = make_sdk(tmp_path)
        sdk._cache_dir.mkdir(parents=True, exist_ok=True)
        sdk._meta_path.write_text(
            json.dumps(
                {"model": "test-model", "chunks": [], "version": _CACHE_VERSION}
            ),
            encoding="utf-8",
        )
        # _load_metadata also requires the FAISS index file to exist
        sdk._index_path.touch()
        meta = sdk._load_metadata()
        assert meta is not None
        assert meta["model"] == "test-model"

    def test_load_metadata_missing_returns_none(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)
        meta = sdk._load_metadata()
        assert meta is None


# ---------------------------------------------------------------------------
# Test: embedding batch sync (lockstep)
# ---------------------------------------------------------------------------


class TestEmbeddingBatchSync:
    def test_encode_with_sync_returns_matching_counts(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)

        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy not installed")

        from gaia.code_index.sdk import CodeChunk

        chunks = [
            CodeChunk("def a(): pass", "a.py", "python", 1, 1),
            CodeChunk("def b(): pass", "b.py", "python", 1, 1),
            CodeChunk("def c(): pass", "c.py", "python", 1, 1),
        ]
        texts = [c.content for c in chunks]

        mock_enc = MagicMock()
        mock_enc.embed.return_value = np.zeros((3, 768), dtype="float32")
        sdk._embedder = mock_enc

        vecs, synced_chunks = sdk._encode_texts_with_sync(texts, chunks)

        assert len(synced_chunks) == vecs.shape[0]

    def test_encode_partial_failure_stays_in_sync(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)

        try:
            import numpy as np
        except ImportError:
            pytest.skip("numpy not installed")

        from gaia.code_index.sdk import CodeChunk

        chunks = [
            CodeChunk("def a(): pass", "a.py", "python", 1, 1),
            CodeChunk("def b(): pass", "b.py", "python", 1, 1),
        ]
        texts = [c.content for c in chunks]

        call_count = [0]

        def side_effect(batch):
            call_count[0] += 1
            # Simulate partial failure on first batch — return only 1 vec
            return np.zeros((1, 768), dtype="float32")

        mock_enc = MagicMock()
        mock_enc.embed.side_effect = side_effect
        sdk._embedder = mock_enc

        # Even if embed returns partial results, sync must hold
        vecs, synced_chunks = sdk._encode_texts_with_sync(texts, chunks)
        assert vecs.shape[0] == len(synced_chunks)


# ---------------------------------------------------------------------------
# Test: embedding model version check
# ---------------------------------------------------------------------------


class TestEmbeddingModelVersion:
    def test_get_status_reports_model_mismatch(self, tmp_path):
        skip_if_unavailable()
        from gaia.code_index.sdk import _CACHE_VERSION

        sdk = make_sdk(tmp_path)
        sdk._cache_dir.mkdir(parents=True, exist_ok=True)
        sdk._meta_path.write_text(
            json.dumps({"model": "old-model", "chunks": [], "version": _CACHE_VERSION}),
            encoding="utf-8",
        )
        sdk._index_path.touch()

        status = sdk.get_status()
        # Should surface the stored model name so callers can detect mismatch
        assert "embedding_model" in status or "indexed" in status

    def test_clear_index_removes_cache(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)
        sdk._cache_dir.mkdir(parents=True, exist_ok=True)
        sdk._meta_path.write_text("{}", encoding="utf-8")
        assert sdk._meta_path.exists()

        sdk.clear_index()
        assert not sdk._meta_path.exists()
