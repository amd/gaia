#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Test suite for GAIA RAG (Retrieval-Augmented Generation) functionality
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

# Test imports
try:
    from gaia.chat.sdk import ChatConfig, ChatSDK
    from gaia.rag.sdk import RAGSDK, RAGConfig, RAGResponse, quick_rag

    RAG_AVAILABLE = True
except ImportError as e:
    RAG_AVAILABLE = False
    IMPORT_ERROR = str(e)


class TestRAGConfig:
    """Test RAG configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = RAGConfig()

        assert config.model == "Qwen3-Coder-30B-A3B-Instruct-GGUF"
        assert config.max_tokens == 1024
        assert config.chunk_size == 500
        assert config.chunk_overlap == 100
        assert config.max_chunks == 5
        assert config.embedding_model == "nomic-embed-text-v2-moe-GGUF"
        assert config.cache_dir == ".gaia"
        assert config.show_stats is False
        assert config.use_local_llm is True

    def test_custom_config(self):
        """Test custom configuration values."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = RAGConfig(
            model="custom-model", chunk_size=1000, max_chunks=5, show_stats=True
        )

        assert config.model == "custom-model"
        assert config.chunk_size == 1000
        assert config.max_chunks == 5
        assert config.show_stats is True


class TestRAGResponse:
    """Test RAG response objects."""

    def test_response_creation(self):
        """Test creating RAG response."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        response = RAGResponse(
            text="Sample answer",
            chunks=["chunk1", "chunk2"],
            chunk_scores=[0.8, 0.6],
            stats={"tokens": 100},
        )

        assert response.text == "Sample answer"
        assert response.chunks == ["chunk1", "chunk2"]
        assert response.chunk_scores == [0.8, 0.6]
        assert response.stats == {"tokens": 100}

    def test_response_defaults(self):
        """Test RAG response with defaults."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        response = RAGResponse(text="Sample answer")

        assert response.text == "Sample answer"
        assert response.chunks is None
        assert response.chunk_scores is None
        assert response.stats is None


class TestRAGSDK:
    """Test RAG SDK functionality."""

    @pytest.fixture
    def mock_dependencies(self):
        """Mock external dependencies."""
        # Mock VLMClient and LemonadeClient at the module level where they're defined
        with (
            patch("gaia.llm.vlm_client.VLMClient") as mock_vlm_class,
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_lemonade,
            patch("gaia.rag.sdk.PdfReader") as mock_pdf,
            patch("gaia.rag.sdk.SentenceTransformer") as mock_st,
            patch("gaia.rag.sdk.faiss") as mock_faiss,
            patch("gaia.rag.sdk.ChatSDK") as mock_chat,
        ):

            # Mock VLMClient to prevent connection attempts
            mock_vlm_instance = Mock()
            mock_vlm_instance.check_availability.return_value = False
            mock_vlm_class.return_value = mock_vlm_instance

            # Mock LemonadeClient for embeddings
            mock_lemonade_instance = Mock()
            # Return OpenAI-compatible format: {"data": [{"embedding": [...]}]}
            mock_lemonade_instance.embeddings.return_value = {
                "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
            }
            mock_lemonade.return_value = mock_lemonade_instance

            # Mock PDF reader
            mock_pdf_instance = Mock()
            mock_pdf_instance.pages = [Mock()]
            mock_pdf_instance.pages[0].extract_text.return_value = (
                "Sample PDF content for testing."
            )
            mock_pdf.return_value = mock_pdf_instance

            # Mock sentence transformer
            mock_embedder = Mock()
            mock_embedder.encode.return_value = np.array([[0.1, 0.2, 0.3, 0.4]])
            mock_st.return_value = mock_embedder

            # Mock FAISS
            mock_index = Mock()
            mock_index.search.return_value = (np.array([[0.5]]), np.array([[0]]))
            mock_faiss.IndexFlatL2.return_value = mock_index

            # Mock ChatSDK
            mock_chat_response = Mock()
            mock_chat_response.text = "Mocked LLM response"
            mock_chat_response.stats = {"tokens": 50}
            mock_chat_instance = Mock()
            mock_chat_instance.send.return_value = mock_chat_response
            mock_chat.return_value = mock_chat_instance

            yield {
                "pdf": mock_pdf,
                "embedder": mock_embedder,
                "index": mock_index,
                "chat": mock_chat_instance,
                "vlm": mock_vlm_instance,
                "lemonade": mock_lemonade_instance,
            }

    def test_sdk_initialization(self, mock_dependencies):
        """Test SDK initialization."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                assert rag.config == config
                assert rag.embedder is None
                assert rag.index is None
                assert rag.chunks == []
                assert rag.indexed_files == set()
                assert os.path.exists(temp_dir)

    def test_dependency_checking(self):
        """Test dependency checking."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        # Test when dependencies are missing
        with (
            patch("gaia.rag.sdk.PdfReader", None),
            patch("gaia.rag.sdk.SentenceTransformer", None),
            patch("gaia.rag.sdk.faiss", None),
        ):

            with pytest.raises(ImportError) as exc_info:
                RAGSDK()

            assert "Missing required RAG dependencies" in str(exc_info.value)

    def test_text_chunking(self, mock_dependencies):
        """Test text chunking functionality."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir, chunk_size=50, chunk_overlap=10)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                # Create a longer text to ensure multiple chunks
                text = """This is the first paragraph with enough content to create multiple chunks.

                This is the second paragraph that continues the document with more information.

                This is the third paragraph adding even more content to ensure we get multiple chunks.

                This is the fourth paragraph with additional content for testing purposes.

                This is the fifth and final paragraph to complete the test document."""
                chunks = rag._split_text_into_chunks(text)

                assert len(chunks) > 1
                assert all(isinstance(chunk, str) for chunk in chunks)

    def test_document_indexing(self, mock_dependencies):
        """Test document indexing."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                # Create a fake PDF file
                fake_pdf = Path(temp_dir) / "test.pdf"
                fake_pdf.write_text("dummy")

                result = rag.index_document(str(fake_pdf))

                assert isinstance(result, dict)
                assert result.get("success") is True
                assert len(rag.chunks) > 0
                assert rag.index is not None
                assert str(fake_pdf.absolute()) in rag.indexed_files

    def test_document_querying(self, mock_dependencies):
        """Test document querying."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir)

            with (
                patch("gaia.rag.sdk.RAGSDK._check_dependencies"),
                patch("gaia.rag.sdk.RAGSDK._encode_texts") as mock_encode,
            ):
                rag = RAGSDK(config)

                # Mock _encode_texts to return proper embeddings
                mock_encode.return_value = np.array([[0.1, 0.2, 0.3, 0.4]])

                # Set up mock state
                rag.chunks = ["Sample chunk 1", "Sample chunk 2"]
                rag.index = mock_dependencies["index"]
                rag.embedder = mock_dependencies["embedder"]
                rag.chat = mock_dependencies["chat"]
                rag.chunk_to_file = {0: "test.pdf", 1: "test.pdf"}
                rag.indexed_files = {"test.pdf"}

                response = rag.query("What is this about?")

                assert isinstance(response, RAGResponse)
                assert response.text == "Mocked LLM response"
                assert response.chunks is not None
                assert response.chunk_scores is not None

    def test_query_without_index(self, mock_dependencies):
        """Test querying without indexed documents."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                with pytest.raises(ValueError) as exc_info:
                    rag.query("What is this about?")

                assert "No documents indexed" in str(exc_info.value)

    def test_cache_functionality(self, mock_dependencies):
        """Test caching functionality."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                # Create fake PDF file
                fake_pdf = Path(temp_dir) / "test.pdf"
                fake_pdf.write_text("dummy")

                # First indexing
                rag1 = RAGSDK(config)
                result1 = rag1.index_document(str(fake_pdf))
                assert isinstance(result1, dict)
                assert result1.get("success") is True

                # Second indexing should use cache
                rag2 = RAGSDK(config)
                result2 = rag2.index_document(str(fake_pdf))
                assert isinstance(result2, dict)
                assert result2.get("success") is True

    def test_status_reporting(self, mock_dependencies):
        """Test status reporting."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                status = rag.get_status()

                assert "indexed_files" in status
                assert "total_chunks" in status
                assert "cache_dir" in status
                assert "embedding_model" in status
                assert "config" in status

                assert status["indexed_files"] == 0
                assert status["total_chunks"] == 0
                assert status["cache_dir"] == temp_dir


class TestQuickRAG:
    """Test quick RAG functionality."""

    @patch("gaia.rag.sdk.os.path.exists")
    @patch("gaia.rag.sdk.RAGSDK")
    def test_quick_rag_success(self, mock_rag_class, mock_exists):
        """Test successful quick RAG query."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        # Mock file exists check
        mock_exists.return_value = True

        # Mock RAG instance
        mock_rag = Mock()
        mock_rag.index_document.return_value = {"success": True}
        mock_response = Mock()
        mock_response.text = "Quick answer"
        mock_rag.query.return_value = mock_response
        mock_rag_class.return_value = mock_rag

        result = quick_rag("test.pdf", "What is this?")

        assert result == "Quick answer"
        mock_exists.assert_called_once_with("test.pdf")
        mock_rag.index_document.assert_called_once_with("test.pdf")
        mock_rag.query.assert_called_once_with("What is this?")

    @patch("gaia.rag.sdk.os.path.exists")
    @patch("gaia.rag.sdk.RAGSDK")
    def test_quick_rag_index_failure(self, mock_rag_class, mock_exists):
        """Test quick RAG with indexing failure."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        # Mock file exists check
        mock_exists.return_value = True

        # Mock RAG instance
        mock_rag = Mock()
        mock_rag.index_document.return_value = {
            "success": False,
            "error": "Test error",
        }
        mock_rag_class.return_value = mock_rag

        with pytest.raises(ValueError) as exc_info:
            quick_rag("test.pdf", "What is this?")

        assert "Failed to index document" in str(exc_info.value)


class TestChatIntegration:
    """Test RAG integration with Chat SDK."""

    @pytest.fixture
    def mock_chat_dependencies(self):
        """Mock chat and RAG dependencies."""
        with (
            patch("gaia.llm.vlm_client.VLMClient") as mock_vlm_class,
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_lemonade,
            patch("gaia.chat.sdk.LLMClient") as mock_llm,
            patch("gaia.rag.sdk.RAGSDK") as mock_rag_class,
        ):

            # Mock VLMClient to prevent connection attempts
            mock_vlm_instance = Mock()
            mock_vlm_instance.check_availability.return_value = False
            mock_vlm_class.return_value = mock_vlm_instance

            # Mock LemonadeClient for embeddings
            mock_lemonade_instance = Mock()
            # Return OpenAI-compatible format: {"data": [{"embedding": [...]}]}
            mock_lemonade_instance.embeddings.return_value = {
                "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
            }
            mock_lemonade.return_value = mock_lemonade_instance

            # Mock LLM client
            mock_llm_instance = Mock()
            mock_llm.return_value = mock_llm_instance

            # Mock RAG SDK
            mock_rag = Mock()
            mock_rag.index_document.return_value = {"success": True}
            mock_response = Mock()
            mock_response.chunks = ["chunk1", "chunk2"]
            # Add chunk_metadata as a list that can be iterated with zip()
            mock_response.chunk_metadata = [
                {"source_file": "test.pdf", "relevance_score": 0.9},
                {"source_file": "test.pdf", "relevance_score": 0.8},
            ]
            mock_response.source_files = ["test.pdf"]
            mock_rag.query.return_value = mock_response
            mock_rag_class.return_value = mock_rag

            yield {
                "llm": mock_llm_instance,
                "rag": mock_rag,
                "vlm": mock_vlm_instance,
                "lemonade": mock_lemonade_instance,
            }

    def test_rag_enabling(self, mock_chat_dependencies):
        """Test enabling RAG in ChatSDK."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = ChatConfig()
        chat = ChatSDK(config)

        # Enable RAG
        chat.enable_rag(documents=["test.pdf"])

        assert chat.rag_enabled is True
        assert chat.rag is not None

    def test_rag_disabling(self, mock_chat_dependencies):
        """Test disabling RAG in ChatSDK."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = ChatConfig()
        chat = ChatSDK(config)

        # Enable then disable RAG
        chat.enable_rag()
        chat.disable_rag()

        assert chat.rag_enabled is False
        assert chat.rag is None

    def test_add_document(self, mock_chat_dependencies):
        """Test adding documents to RAG."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = ChatConfig()
        chat = ChatSDK(config)

        # Setup mock to return success dict
        mock_chat_dependencies["rag"].index_document.return_value = {"success": True}

        # Enable RAG and add document
        chat.enable_rag()
        result = chat.add_document("test.pdf")

        # add_document returns the result from index_document (dict, not bool despite type hint)
        assert isinstance(result, dict)
        assert result.get("success") is True
        mock_chat_dependencies["rag"].index_document.assert_called_with("test.pdf")

    def test_add_document_without_rag(self, mock_chat_dependencies):
        """Test adding document without RAG enabled."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = ChatConfig()
        chat = ChatSDK(config)

        with pytest.raises(ValueError) as exc_info:
            chat.add_document("test.pdf")

        assert "RAG not enabled" in str(exc_info.value)

    def test_message_enhancement(self, mock_chat_dependencies):
        """Test message enhancement with RAG."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = ChatConfig()
        chat = ChatSDK(config)

        # Enable RAG
        chat.enable_rag()

        # Test message enhancement
        original_message = "What is AI?"
        enhanced, metadata = chat._enhance_with_rag(original_message)

        assert original_message in enhanced
        assert "Context" in enhanced


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_missing_dependencies_import(self):
        """Test behavior when dependencies are missing."""
        # This test runs regardless of dependency availability

        # Test dependency checking method directly instead of import-time behavior
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with (
            patch("gaia.rag.sdk.PdfReader", None),
            patch("gaia.rag.sdk.SentenceTransformer", None),
            patch("gaia.rag.sdk.faiss", None),
        ):

            with pytest.raises(ImportError) as exc_info:
                RAGSDK()._check_dependencies()

            assert "Missing required RAG dependencies" in str(exc_info.value)

    def test_invalid_pdf_file(self):
        """Test handling of invalid PDF files."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                # Test non-existent file
                result = rag.index_document("nonexistent.pdf")
                assert isinstance(result, dict)
                assert result.get("success") is False
                assert "error" in result
                assert "File not found" in result["error"]

    def test_empty_query(self):
        """Test handling of empty queries."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = ChatConfig()
            chat = ChatSDK(config)

            with pytest.raises(ValueError) as exc_info:
                chat.send("")

            assert "Message cannot be empty" in str(exc_info.value)


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
