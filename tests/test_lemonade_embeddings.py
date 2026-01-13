# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for Lemonade server embeddings integration.

These tests validate that the Lemonade server can generate embeddings
using the nomic-embed-text-v2-moe-GGUF model for RAG applications.
"""

import pytest

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError


class TestLemonadeEmbeddings:
    """Test suite for Lemonade embeddings API."""

    @pytest.fixture
    def client(self):
        """Create Lemonade client for testing."""
        return LemonadeClient()

    @pytest.fixture
    def embedding_model(self):
        """Default embedding model to use."""
        return "nomic-embed-text-v2-moe-GGUF"

    def test_embeddings_method_exists(self, client):
        """Test that embeddings method exists on client."""
        assert hasattr(
            client, "embeddings"
        ), "LemonadeClient should have embeddings method"

    def test_single_text_embedding(self, client, embedding_model):
        """Test embedding a single text string."""
        response = client.embeddings("Hello world", model=embedding_model, timeout=30)

        assert isinstance(response, dict), "Response should be a dictionary"
        assert "data" in response, "Response should have 'data' field"
        assert len(response["data"]) == 1, "Should have one embedding for one text"

        embedding = response["data"][0]["embedding"]
        assert len(embedding) == 768, "Nomic embeddings should be 768 dimensions"
        assert all(
            isinstance(x, float) for x in embedding[:5]
        ), "Embedding values should be floats"

    def test_multiple_texts_embedding(self, client, embedding_model):
        """Test embedding multiple texts in a batch."""
        test_texts = [
            "The vision is a resilient energy future",
            "Oil and gas regulations in British Columbia",
            "Piping and instrumentation diagram requirements",
        ]

        response = client.embeddings(test_texts, model=embedding_model, timeout=30)

        assert isinstance(response, dict), "Response should be a dictionary"
        assert "data" in response, "Response should have 'data' field"
        assert len(response["data"]) == len(
            test_texts
        ), f"Should have {len(test_texts)} embeddings"

        for i, item in enumerate(response["data"]):
            embedding = item["embedding"]
            assert len(embedding) == 768, f"Embedding {i} should be 768 dimensions"

    def test_embedding_batch_processing(self, client, embedding_model):
        """Test that large batches can be processed."""
        # Create 50 test texts (typical batch size)
        test_texts = [f"Sample text number {i}" for i in range(50)]

        response = client.embeddings(test_texts, model=embedding_model, timeout=60)

        assert isinstance(response, dict), "Response should be a dictionary"
        assert len(response["data"]) == 50, "Should have 50 embeddings"

        # Verify all embeddings are valid
        for item in response["data"]:
            embedding = item["embedding"]
            assert len(embedding) == 768, "All embeddings should be 768 dimensions"

    def test_embedding_consistency(self, client, embedding_model):
        """Test that same text produces same embedding."""
        text = "Test consistency"

        # Generate embedding twice
        response1 = client.embeddings([text], model=embedding_model, timeout=30)
        response2 = client.embeddings([text], model=embedding_model, timeout=30)

        emb1 = response1["data"][0]["embedding"]
        emb2 = response2["data"][0]["embedding"]

        # Should be identical (deterministic)
        assert emb1 == emb2, "Same text should produce identical embeddings"

    def test_invalid_model_name(self, client):
        """Test error handling for invalid model name."""
        with pytest.raises(LemonadeClientError) as exc_info:
            client.embeddings(["test"], model="invalid-model-name", timeout=10)

        assert (
            "not registered" in str(exc_info.value).lower()
            or "error" in str(exc_info.value).lower()
        )


if __name__ == "__main__":
    # Allow running directly for quick testing
    pytest.main([__file__, "-v", "-s"])
