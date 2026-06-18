# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the content-keyed embedding cache (issue #1743).

Covers the cache itself plus its wiring into MemoryMixin._embed_text and
RAGSDK._encode_query: a second identical embed must make zero backend calls,
and a model/dim change must invalidate (miss).
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from gaia.llm.embedding_cache import EmbeddingCache


class TestEmbeddingCache:
    def test_hit_returns_stored_vector(self):
        cache = EmbeddingCache()
        vec = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert cache.get("m", 3, "hello") is None
        cache.put("m", 3, "hello", vec)
        out = cache.get("m", 3, "hello")
        assert out is not None
        np.testing.assert_array_equal(out, vec)
        assert cache.hits == 1
        assert cache.misses == 1  # the first lookup

    def test_model_change_invalidates(self):
        cache = EmbeddingCache()
        cache.put("model-a", 3, "hello", np.zeros(3, dtype=np.float32))
        assert cache.get("model-b", 3, "hello") is None

    def test_dim_change_invalidates(self):
        cache = EmbeddingCache()
        cache.put("m", 768, "hello", np.zeros(768, dtype=np.float32))
        assert cache.get("m", 384, "hello") is None

    def test_distinct_text_misses(self):
        cache = EmbeddingCache()
        cache.put("m", 3, "hello", np.zeros(3, dtype=np.float32))
        assert cache.get("m", 3, "goodbye") is None

    def test_returned_vector_is_a_copy(self):
        cache = EmbeddingCache()
        cache.put("m", 3, "t", np.array([1.0, 2.0, 3.0], dtype=np.float32))
        out = cache.get("m", 3, "t")
        out[0] = 99.0  # mutate caller's copy
        # cache must be unaffected
        np.testing.assert_array_equal(
            cache.get("m", 3, "t"), np.array([1.0, 2.0, 3.0], dtype=np.float32)
        )

    def test_lru_eviction(self):
        cache = EmbeddingCache(max_entries=2)
        cache.put("m", 1, "a", np.zeros(1, dtype=np.float32))
        cache.put("m", 1, "b", np.zeros(1, dtype=np.float32))
        cache.get("m", 1, "a")  # touch 'a' so 'b' is now LRU
        cache.put("m", 1, "c", np.zeros(1, dtype=np.float32))  # evicts 'b'
        assert cache.get("m", 1, "a") is not None
        assert cache.get("m", 1, "c") is not None
        assert cache.get("m", 1, "b") is None
        assert len(cache) == 2

    def test_invalid_max_entries(self):
        with pytest.raises(ValueError):
            EmbeddingCache(max_entries=0)


class TestMemoryEmbedTextCaching:
    """MemoryMixin._embed_text serves identical text from the cache."""

    def _host(self):
        from gaia.agents.base.memory import EMBEDDING_DIM, MemoryMixin

        host = MemoryMixin()
        mock_embedder = MagicMock()
        vec = np.random.rand(EMBEDDING_DIM).astype(np.float32).tolist()
        mock_embedder.embed.return_value = [vec]
        host._embedder = mock_embedder
        return host, mock_embedder

    def test_second_identical_embed_makes_zero_backend_calls(self):
        host, mock_embedder = self._host()

        first = host._embed_text("the same query")
        second = host._embed_text("the same query")

        assert mock_embedder.embed.call_count == 1
        np.testing.assert_array_equal(first, second)

    def test_distinct_text_calls_backend_again(self):
        host, mock_embedder = self._host()
        host._embed_text("query one")
        host._embed_text("query two")
        assert mock_embedder.embed.call_count == 2


class TestRagEncodeQueryCaching:
    """RAGSDK._encode_query serves identical queries from the cache."""

    def _sdk(self):
        from gaia.rag.sdk import RAGSDK

        sdk = RAGSDK.__new__(RAGSDK)  # skip heavy __init__
        sdk.config = MagicMock()
        sdk.config.embedding_model = "nomic-embed-text-v2-moe-GGUF"
        sdk._embedding_cache = None
        sdk._encode_texts = MagicMock(
            return_value=np.random.rand(1, 768).astype(np.float32)
        )
        return sdk

    def test_second_identical_query_makes_zero_encode_calls(self):
        sdk = self._sdk()
        first = sdk._encode_query("what is gaia?")
        second = sdk._encode_query("what is gaia?")

        assert sdk._encode_texts.call_count == 1
        assert first.shape == (1, 768)
        np.testing.assert_array_equal(first, second)

    def test_distinct_query_encodes_again(self):
        sdk = self._sdk()
        sdk._encode_query("query a")
        sdk._encode_query("query b")
        assert sdk._encode_texts.call_count == 2
