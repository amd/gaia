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
        CodeChunk,
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
                [
                    CodeChunk(
                        content="def foo(): pass",
                        file_path="a.py",
                        language="python",
                        start_line=1,
                        end_line=1,
                    )
                ],
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

    def test_honors_configured_max_file_size_mb_above_parser_default(self, tmp_path):
        """A file the caller explicitly allowed (via max_file_size_mb) must
        not be silently dropped by the parser's own hardcoded 1MB default.

        chunk_code_file()'s default max_size_mb is 1.0 regardless of what
        the SDK is configured for — index_repository() must pass the
        configured value through, or raising max_file_size_mb to admit a
        3MB file at discovery is pointless once chunking re-rejects it.
        """
        skip_if_unavailable()
        # 1.2MB of content — over the parser's hardcoded 1MB default, under
        # the 2MB this SDK instance is configured to allow.
        big_content = "x = 1\n" * 200_000
        assert len(big_content.encode("utf-8")) > 1024 * 1024

        sdk = make_sdk(tmp_path, max_file_size_mb=2)
        _write_py(tmp_path, "big.py", big_content)

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

            def fake_encode(texts, chunks):
                return np.zeros((len(chunks), 768), dtype="float32"), chunks

            mock_encode.side_effect = fake_encode
            result = sdk.index_repository()

        # The chunker actually saw text to embed for this file, i.e. it
        # wasn't silently zeroed out by the hardcoded 1MB parser default
        # (if it had been, chunks_to_embed would be empty and
        # _encode_texts_with_sync would never be called at all).
        assert mock_encode.called, (
            "big.py's chunks never reached the embedder — the parser's "
            "1MB default likely overrode the configured max_file_size_mb"
        )
        embed_texts, embed_chunks = mock_encode.call_args.args
        assert len(embed_chunks) >= 1
        assert all(c.file_path == "big.py" for c in embed_chunks)
        assert result.chunks_created == len(embed_chunks)


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

    def test_search_raises_on_model_mismatch(self, tmp_path):
        """Index built with model A + querying with model B must fail loudly.

        Previously returned [] silently, hiding the misconfiguration.
        """
        skip_if_unavailable()
        try:
            import faiss  # noqa: F401
        except ImportError:
            pytest.skip("faiss not installed")

        sdk = make_sdk(tmp_path, embedding_model="model-B")
        # Plant an in-memory index built with a different model.
        sdk._faiss_index = MagicMock()
        sdk._faiss_index.ntotal = 1
        sdk._metadata = {
            "embedding_model": "model-A",
            "chunks": [],
        }

        with pytest.raises(ValueError, match="Embedding-model mismatch"):
            sdk.search("anything")

    def test_search_raises_when_query_encoding_fails(self, tmp_path):
        """Lemonade outage during search must surface as RuntimeError, not [].

        Previously a bare `except Exception: return []` swallowed transport
        errors, making "Lemonade down" indistinguishable from "no matches".
        """
        skip_if_unavailable()
        try:
            import faiss  # noqa: F401
        except ImportError:
            pytest.skip("faiss not installed")

        sdk = make_sdk(tmp_path)
        sdk._faiss_index = MagicMock()
        sdk._faiss_index.ntotal = 1
        sdk._metadata = {
            "embedding_model": sdk.config.embedding_model,
            "chunks": [],
        }

        with (
            patch.object(sdk, "_load_embedder"),
            patch.object(
                sdk, "_encode_texts", side_effect=ConnectionError("backend dead")
            ),
        ):
            with pytest.raises(RuntimeError, match="Query encoding failed"):
                sdk.search("anything")

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
            mock_enc.embeddings.return_value = {
                "data": [{"embedding": np.zeros(768, dtype="float32").tolist()}]
            }
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

    def test_atomic_save_overwrites_existing_cache_on_reindex(self, tmp_path):
        """A second save over an already-populated cache dir must succeed.

        Path.rename() raises FileExistsError on Windows when the destination
        exists (POSIX rename() silently replaces it) — so every re-index
        after the first index_repository() call would crash on Windows.
        Regression for that platform gap; must use Path.replace().
        """
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)

        try:
            import faiss
            import numpy as np
        except ImportError:
            pytest.skip("faiss/numpy not installed")

        index1 = faiss.IndexFlatL2(4)
        index1.add(np.zeros((1, 4), dtype="float32"))
        sdk._save_atomic(index1, {"model": "test-model", "chunks": []})
        assert sdk._index_path.exists()
        assert sdk._meta_path.exists()

        # Second save over the same, already-existing cache files.
        index2 = faiss.IndexFlatL2(4)
        index2.add(np.ones((2, 4), dtype="float32"))
        sdk._save_atomic(index2, {"model": "test-model", "chunks": [1, 2]})

        assert sdk._index_path.exists()
        assert sdk._meta_path.exists()
        reloaded = json.loads(sdk._meta_path.read_text(encoding="utf-8"))
        assert reloaded["chunks"] == [1, 2]

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
        mock_enc.embeddings.return_value = {
            "data": [
                {"embedding": np.zeros(768, dtype="float32").tolist()} for _ in chunks
            ]
        }
        sdk._embedder = mock_enc

        vecs, synced_chunks = sdk._encode_texts_with_sync(texts, chunks)

        assert len(synced_chunks) == 3
        assert vecs.shape[0] == 3
        assert len(synced_chunks) == vecs.shape[0]

    def test_encode_partial_failure_stays_in_sync(self, tmp_path):
        """A batch stuck returning fewer vectors than requested (not a
        transient load race — every retry gets the same short result) must
        still fall back to one-by-one and keep chunks/vectors aligned,
        rather than silently zipping mismatched lists together.
        """
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

        def side_effect(input_texts, model=None, timeout=None):
            # A 2-text batch always comes back with only 1 vector (stuck
            # partial failure); a single-text call (the one-by-one fallback)
            # succeeds — this is what forces recovery through the fallback.
            n = 1 if len(input_texts) > 1 else len(input_texts)
            return {
                "data": [
                    {"embedding": np.zeros(768, dtype="float32").tolist()}
                    for _ in range(n)
                ]
            }

        mock_enc = MagicMock()
        mock_enc.embeddings.side_effect = side_effect
        sdk._embedder = mock_enc

        with patch("gaia.code_index.sdk.time.sleep"):
            vecs, synced_chunks = sdk._encode_texts_with_sync(texts, chunks)

        # One-by-one fallback recovers both chunks despite the stuck batch.
        assert vecs.shape[0] == len(synced_chunks) == 2


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


# ---------------------------------------------------------------------------
# Test: fail-loudly contract on infrastructure failures
# ---------------------------------------------------------------------------


class TestFailLoudly:
    def test_load_embedder_raises_with_actionable_message(self, tmp_path):
        """_load_embedder must surface Lemonade-down with a hint, not swallow."""
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)

        fake_client = MagicMock()
        fake_client.health_check.side_effect = ConnectionError("connection refused")
        sdk._llm_client = fake_client

        with pytest.raises(RuntimeError, match=r"Lemonade Server"):
            sdk._load_embedder()

    def test_ensure_index_loaded_raises_on_corrupt_faiss(self, tmp_path):
        """A corrupt FAISS file must raise — silent False would mask cache rot."""
        skip_if_unavailable()
        try:
            import faiss  # noqa: F401
        except ImportError:
            pytest.skip("faiss not installed")

        sdk = make_sdk(tmp_path)
        # Plant valid metadata + a deliberately-corrupt index file.
        sdk._cache_dir.mkdir(parents=True, exist_ok=True)
        from gaia.code_index.sdk import _CACHE_VERSION

        sdk._meta_path.write_text(
            json.dumps(
                {
                    "version": _CACHE_VERSION,
                    "embedding_model": sdk.config.embedding_model,
                    "chunks": [{"chunk_type": "code"}],
                }
            ),
            encoding="utf-8",
        )
        sdk._index_path.write_bytes(b"not a real faiss index")

        with pytest.raises(RuntimeError, match=r"FAISS index"):
            sdk._ensure_index_loaded()


# ---------------------------------------------------------------------------
# Test: batch-embedding race — backend returns 0/short vectors with NO
# exception (e.g. the embedding model is still warming up). Previously this
# silently degraded to a ~25x-slower one-by-one fallback (or, for the
# query-encode path used by search(), silently returned an empty result
# indistinguishable from "no matches found"). See docs/plans/code-index-review.mdx.
# ---------------------------------------------------------------------------


class TestBatchEmbeddingRace:
    def _mock_embedder_with_data(self, np, n_per_call):
        """A mock ``.embeddings()`` that always returns *n_per_call* vectors,
        regardless of how many texts were requested — simulating a backend
        that answers HTTP 200 but comes back short with no error.
        """
        mock_enc = MagicMock()
        mock_enc.embeddings.return_value = {
            "data": [
                {"embedding": np.zeros(768, dtype="float32").tolist()}
                for _ in range(n_per_call)
            ]
        }
        return mock_enc

    def test_encode_texts_retries_batch_before_failing(self, tmp_path):
        """A short-without-exception response must be retried as a whole
        batch (the 'not ready yet' race) — not immediately treated as final.
        """
        skip_if_unavailable()
        import numpy as np

        sdk = make_sdk(tmp_path)
        mock_enc = self._mock_embedder_with_data(np, n_per_call=0)
        sdk._embedder = mock_enc

        with patch("gaia.code_index.sdk.time.sleep"):
            with pytest.raises(RuntimeError, match="Embedding backend returned"):
                sdk._encode_texts(["hello world"])

        # 1 initial + 2 retries = 3 calls to the same batch, never falls
        # back to a silent partial/empty result.
        assert mock_enc.embeddings.call_count == 3

    def test_encode_texts_recovers_if_batch_becomes_ready_on_retry(self, tmp_path):
        """If the backend comes back fully on a retry, no error is raised —
        this is the intended 'transient load race' recovery path.
        """
        skip_if_unavailable()
        import numpy as np

        sdk = make_sdk(tmp_path)
        mock_enc = MagicMock()
        empty = {"data": []}
        full = {"data": [{"embedding": np.zeros(768, dtype="float32").tolist()}]}
        mock_enc.embeddings.side_effect = [empty, full]
        sdk._embedder = mock_enc

        with patch("gaia.code_index.sdk.time.sleep"):
            result = sdk._encode_texts(["hello world"])

        assert result.shape == (1, 768)
        assert mock_enc.embeddings.call_count == 2

    def test_search_raises_not_empty_when_query_encoding_returns_nothing(
        self, tmp_path
    ):
        """search() must fail loudly, not return [], when the backend never
        produces a query vector — previously indistinguishable from a
        genuine "no matches" result.
        """
        skip_if_unavailable()
        try:
            import faiss  # noqa: F401
            import numpy as np
        except ImportError:
            pytest.skip("faiss/numpy not installed")

        sdk = make_sdk(tmp_path)
        sdk._faiss_index = MagicMock()
        sdk._faiss_index.ntotal = 1
        sdk._metadata = {"embedding_model": sdk.config.embedding_model, "chunks": []}

        mock_enc = self._mock_embedder_with_data(np, n_per_call=0)
        sdk._embedder = mock_enc

        with (
            patch.object(sdk, "_load_embedder"),
            patch("gaia.code_index.sdk.time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="Query encoding failed"):
                sdk.search("anything")

    def test_encode_with_sync_falls_back_only_after_batch_retries_exhausted(
        self, tmp_path
    ):
        """The bulk-index path may still fall back to one-by-one for a
        batch that's genuinely stuck short — but only after retrying the
        batch itself, not on the first short response.
        """
        skip_if_unavailable()
        import numpy as np

        from gaia.code_index.sdk import CodeChunk

        sdk = make_sdk(tmp_path)
        chunks = [CodeChunk("def a(): pass", "a.py", "python", 1, 1)]
        texts = [c.content for c in chunks]

        mock_enc = self._mock_embedder_with_data(np, n_per_call=0)
        sdk._embedder = mock_enc

        with patch("gaia.code_index.sdk.time.sleep"):
            vecs, synced_chunks = sdk._encode_texts_with_sync(texts, chunks)

        # Batch retried 3x (all short), then one-by-one also short -> the
        # chunk is dropped, not silently paired with a garbage/zero vector.
        assert vecs.shape[0] == len(synced_chunks) == 0


# ---------------------------------------------------------------------------
# Test: filesystem failure modes during discovery/read
# ---------------------------------------------------------------------------


class TestFilesystemFailureModes:
    def test_deleted_between_discovery_and_read_is_skipped_not_crashed(self, tmp_path):
        """A file that existed when os.walk() listed it but is gone by the
        time _read_file_safe() opens it (deleted mid-scan, another process
        racing a rewrite, etc.) must be skipped, not raise.
        """
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)
        never_created = tmp_path / "vanished.py"

        result = sdk._read_file_safe(str(never_created))

        assert result is None

    def test_binary_file_with_py_extension_is_skipped(self, tmp_path):
        """Extension is not trusted — content is sniffed for null bytes so a
        binary file mislabeled `.py` doesn't get chunked/embedded as text.
        """
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)
        fake_py = tmp_path / "not_really_python.py"
        fake_py.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02" * 100)

        result = sdk._read_file_safe(str(fake_py))

        assert result is None

    def test_unreadable_file_is_skipped_not_crashed(self, tmp_path):
        """A permission error while reading must be caught, not propagated —
        one unreadable file must not abort the whole index."""
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)
        f = tmp_path / "locked.py"
        f.write_text("def x(): pass\n", encoding="utf-8")

        with patch(
            "pathlib.Path.read_text", side_effect=PermissionError("access denied")
        ):
            result = sdk._read_file_safe(str(f))

        assert result is None


class TestDroppedChunksAreRetriedNextIndex:
    """A file that loses a chunk to a failed embedding must stay un-hashed.

    Recording its hash marks it "indexed"; every later run then skips it as
    unchanged and the missing chunks stay unsearchable until the cache is
    wiped (the silent hole the hardening pass otherwise closed).
    """

    def test_file_with_dropped_chunk_is_left_unhashed(self, tmp_path):
        skip_if_unavailable()
        sdk = make_sdk(tmp_path)
        _write_py(tmp_path, "kept.py", "def kept(): pass\n")
        _write_py(tmp_path, "hole.py", "def hole(): pass\n")

        try:
            import faiss  # noqa: F401
        except ImportError:
            pytest.skip("faiss not installed")

        import numpy as np

        saved = {}

        def fake_save(index, meta):
            saved["meta"] = meta

        def fake_encode(texts, chunks):
            # Simulate the embedder silently dropping every chunk of hole.py.
            kept = [c for c in chunks if c.file_path != "hole.py"]
            return np.zeros((len(kept), 8), dtype="float32"), kept

        with (
            patch.object(sdk, "_load_embedder"),
            patch.object(sdk, "_encode_texts_with_sync", side_effect=fake_encode),
            patch.object(sdk, "_save_atomic", side_effect=fake_save),
        ):
            result = sdk.index_repository()

        hashes = saved["meta"]["file_hashes"]
        assert "kept.py" in hashes, "the fully-embedded file must be hashed"
        assert (
            "hole.py" not in hashes
        ), "a file that lost chunks must be re-tried on the next index"
        assert result.chunks_dropped >= 1
