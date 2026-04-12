#!/usr/bin/env python3
# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Test suite for GAIA RAG (Retrieval-Augmented Generation) functionality
"""

import os
import pickle
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

# Test imports
try:
    from gaia.chat.sdk import AgentConfig, AgentSDK
    from gaia.rag.sdk import CACHE_HEADER, RAGSDK, RAGConfig, RAGResponse, quick_rag

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

        assert config.model == "Qwen3.5-35B-A3B-GGUF"
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
            patch("gaia.rag.sdk.AgentSDK") as mock_chat,
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

            # Mock AgentSDK
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

    def test_corrupted_cache_recovery(self, mock_dependencies):
        """Test that corrupted cache files are deleted and re-indexed."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir, show_stats=False)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                # Create fake PDF file
                fake_pdf = Path(temp_dir) / "test.pdf"
                fake_pdf.write_text("dummy")

                # First indexing creates a valid cache
                rag1 = RAGSDK(config)
                result1 = rag1.index_document(str(fake_pdf))
                assert result1.get("success") is True

                # Find the cache file and corrupt it
                cache_files = list(Path(temp_dir).glob("*.pkl"))
                assert len(cache_files) == 1
                cache_file = cache_files[0]
                cache_file.write_bytes(b"corrupted data")

                # Second indexing should detect corruption, delete cache, and re-index
                rag2 = RAGSDK(config)
                result2 = rag2.index_document(str(fake_pdf))
                assert result2.get("success") is True
                assert not result2.get("from_cache")

                # Corrupted cache file should have been replaced with a valid one
                assert cache_file.exists()
                assert cache_file.read_bytes().startswith(CACHE_HEADER)

    def test_cache_checksum_mismatch_recovery(self, mock_dependencies):
        """Test that a cache with valid header but wrong checksum is rejected."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir, show_stats=False)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                fake_pdf = Path(temp_dir) / "test.pdf"
                fake_pdf.write_text("dummy")

                # First indexing creates a valid cache
                rag1 = RAGSDK(config)
                result1 = rag1.index_document(str(fake_pdf))
                assert result1.get("success") is True

                # Write a file with valid header but wrong checksum
                cache_files = list(Path(temp_dir).glob("*.pkl"))
                assert len(cache_files) == 1
                cache_file = cache_files[0]
                payload = pickle.dumps({"chunks": [], "full_text": "", "metadata": {}})
                with open(cache_file, "wb") as f:
                    f.write(CACHE_HEADER)
                    f.write(
                        b"0000000000000000000000000000000000000000000000000000000000000000\n"
                    )
                    f.write(payload)

                # Should detect mismatch, delete, and re-index
                rag2 = RAGSDK(config)
                result2 = rag2.index_document(str(fake_pdf))
                assert result2.get("success") is True
                assert not result2.get("from_cache")

    def test_oversized_cache_rejected(self, mock_dependencies):
        """Test that a cache file exceeding MAX_CACHE_SIZE is rejected."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir, show_stats=False)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                fake_pdf = Path(temp_dir) / "test.pdf"
                fake_pdf.write_text("dummy")

                # First indexing creates a valid cache
                rag1 = RAGSDK(config)
                result1 = rag1.index_document(str(fake_pdf))
                assert result1.get("success") is True

                # Simulate an oversized cache by temporarily lowering the limit
                cache_files = list(Path(temp_dir).glob("*.pkl"))
                assert len(cache_files) == 1
                original_size = cache_files[0].stat().st_size

                with patch("gaia.rag.sdk.MAX_CACHE_SIZE", original_size - 1):
                    rag2 = RAGSDK(config)
                    result2 = rag2.index_document(str(fake_pdf))
                    assert result2.get("success") is True
                    assert not result2.get("from_cache")

    def test_old_format_cache_migration(self, mock_dependencies):
        """Test that old-format cache files (no header) are rebuilt."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir, show_stats=False)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                fake_pdf = Path(temp_dir) / "test.pdf"
                fake_pdf.write_text("dummy")

                # First indexing creates a valid cache
                rag1 = RAGSDK(config)
                result1 = rag1.index_document(str(fake_pdf))
                assert result1.get("success") is True

                # Overwrite with old-format cache (plain pickle, no header)
                cache_files = list(Path(temp_dir).glob("*.pkl"))
                assert len(cache_files) == 1
                cache_file = cache_files[0]
                old_data = {"chunks": ["old chunk"], "full_text": "old", "metadata": {}}
                with open(cache_file, "wb") as f:
                    pickle.dump(old_data, f)

                # Should detect missing header, delete, and re-index
                rag2 = RAGSDK(config)
                result2 = rag2.index_document(str(fake_pdf))
                assert result2.get("success") is True
                assert not result2.get("from_cache")

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

    def test_query_uses_consistent_snapshot_during_state_swap(self, mock_dependencies):
        """Test that query returns data from one consistent snapshot."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir, show_stats=False)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                old_file = str((Path(temp_dir) / "old.md").absolute())
                new_file = str((Path(temp_dir) / "new.md").absolute())
                old_index = Mock()
                search_started = threading.Event()
                allow_finish = threading.Event()

                def _search(*_args, **_kwargs):
                    search_started.set()
                    assert allow_finish.wait(timeout=1.0)
                    return np.array([[0.25]]), np.array([[0]])

                old_index.search.side_effect = _search
                rag.index = old_index
                rag.chunks = ["old chunk"]
                rag.chunk_to_file = {0: old_file}
                rag.indexed_files = {old_file}

                result = {}

                def _run_query():
                    result["response"] = rag.query("What changed?")

                worker = threading.Thread(target=_run_query)
                worker.start()

                assert search_started.wait(timeout=1.0)

                with rag._state_lock:
                    rag.index = Mock()
                    rag.chunks = ["new chunk"]
                    rag.chunk_to_file = {0: new_file}
                    rag.indexed_files = {new_file}

                allow_finish.set()
                worker.join(timeout=1.0)

                assert "response" in result
                response = result["response"]
                assert response.chunks == ["old chunk"]
                assert response.source_files == ["old.md"]
                assert response.query_metadata["source_files"] == ["old.md"]
                assert response.query_metadata["total_indexed_files"] == 1
                assert response.query_metadata["total_indexed_chunks"] == 1

    def test_remove_document_preserves_state_when_rebuild_fails(
        self, mock_dependencies
    ):
        """Test that failed rebuilds do not publish partial remove state."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir, show_stats=False)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                file_a = str((Path(temp_dir) / "doc_a.md").absolute())
                file_b = str((Path(temp_dir) / "doc_b.md").absolute())
                original_index = Mock()

                rag.index = original_index
                rag.chunks = ["chunk-a", "chunk-b"]
                rag.indexed_files = {file_a, file_b}
                rag.file_to_chunk_indices = {file_a: [0], file_b: [1]}
                rag.chunk_to_file = {0: file_a, 1: file_b}
                rag.file_indices = {file_a: Mock(), file_b: Mock()}
                rag.file_embeddings = {
                    file_a: np.array([[0.1, 0.2, 0.3, 0.4]]),
                    file_b: np.array([[0.5, 0.6, 0.7, 0.8]]),
                }
                rag.file_metadata = {
                    file_a: {"full_text": "A"},
                    file_b: {"full_text": "B"},
                }
                rag.file_access_times = {file_a: 1, file_b: 2}
                rag.file_index_times = {file_a: 1.0, file_b: 2.0}

                with patch.object(
                    rag, "_create_vector_index", side_effect=RuntimeError("boom")
                ):
                    assert rag.remove_document(file_a) is False

                assert rag.index is original_index
                assert rag.chunks == ["chunk-a", "chunk-b"]
                assert rag.indexed_files == {file_a, file_b}
                assert rag.file_to_chunk_indices == {file_a: [0], file_b: [1]}
                assert rag.chunk_to_file == {0: file_a, 1: file_b}
                assert file_a in rag.file_indices
                assert file_a in rag.file_embeddings
                assert file_a in rag.file_metadata
                assert rag.file_access_times[file_a] == 1
                assert rag.file_index_times[file_a] == 1.0


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
            patch("gaia.chat.sdk.create_client") as mock_create_client,
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

            # Mock LLM client factory - create_client() returns mock instance
            mock_llm_instance = Mock()
            mock_create_client.return_value = mock_llm_instance

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
        """Test enabling RAG in AgentSDK."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = AgentConfig()
        chat = AgentSDK(config)

        # Enable RAG
        chat.enable_rag(documents=["test.pdf"])

        assert chat.rag_enabled is True
        assert chat.rag is not None

    def test_rag_disabling(self, mock_chat_dependencies):
        """Test disabling RAG in AgentSDK."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = AgentConfig()
        chat = AgentSDK(config)

        # Enable then disable RAG
        chat.enable_rag()
        chat.disable_rag()

        assert chat.rag_enabled is False
        assert chat.rag is None

    def test_add_document(self, mock_chat_dependencies):
        """Test adding documents to RAG."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = AgentConfig()
        chat = AgentSDK(config)

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

        config = AgentConfig()
        chat = AgentSDK(config)

        with pytest.raises(ValueError) as exc_info:
            chat.add_document("test.pdf")

        assert "RAG not enabled" in str(exc_info.value)

    def test_message_enhancement(self, mock_chat_dependencies):
        """Test message enhancement with RAG."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        config = AgentConfig()
        chat = AgentSDK(config)

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

        with tempfile.TemporaryDirectory():
            config = AgentConfig()
            chat = AgentSDK(config)

            with pytest.raises(ValueError) as exc_info:
                chat.send("")

            assert "Message cannot be empty" in str(exc_info.value)


class TestMemoryLimits:
    """Test memory limit enforcement and LRU eviction."""

    @pytest.fixture
    def mock_dependencies(self):
        """Mock external dependencies."""
        with (
            patch("gaia.llm.vlm_client.VLMClient") as mock_vlm_class,
            patch("gaia.llm.lemonade_client.LemonadeClient") as mock_lemonade,
            patch("gaia.rag.sdk.PdfReader") as mock_pdf,
            patch("gaia.rag.sdk.SentenceTransformer") as mock_st,
            patch("gaia.rag.sdk.faiss") as mock_faiss,
            patch("gaia.rag.sdk.AgentSDK") as mock_chat,
        ):
            mock_vlm_instance = Mock()
            mock_vlm_instance.check_availability.return_value = False
            mock_vlm_class.return_value = mock_vlm_instance

            mock_lemonade_instance = Mock()
            mock_lemonade_instance.embeddings.return_value = {
                "data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]
            }
            mock_lemonade.return_value = mock_lemonade_instance

            mock_pdf_instance = Mock()
            mock_pdf_instance.pages = [Mock()]
            mock_pdf_instance.pages[0].extract_text.return_value = (
                "Sample PDF content for testing."
            )
            mock_pdf.return_value = mock_pdf_instance

            mock_embedder = Mock()
            mock_embedder.encode.return_value = np.array([[0.1, 0.2, 0.3, 0.4]])
            mock_st.return_value = mock_embedder

            mock_index = Mock()
            mock_index.search.return_value = (np.array([[0.5]]), np.array([[0]]))
            mock_faiss.IndexFlatL2.return_value = mock_index

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

    def test_index_rejected_at_file_limit_eviction_disabled(self, mock_dependencies):
        """Test that indexing is rejected when file limit reached and eviction disabled."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(
                cache_dir=temp_dir,
                max_indexed_files=1,
                enable_lru_eviction=False,
                show_stats=False,
            )

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                # Create two temp PDF files
                pdf1 = Path(temp_dir) / "doc1.pdf"
                pdf1.write_text("dummy content 1")
                pdf2 = Path(temp_dir) / "doc2.pdf"
                pdf2.write_text("dummy content 2")

                # First doc should succeed
                result1 = rag.index_document(str(pdf1))
                assert result1["success"] is True

                # Second doc should be rejected
                result2 = rag.index_document(str(pdf2))
                assert result2["success"] is False
                assert result2.get("memory_limit_reached") is True
                assert "Memory limit" in result2.get("error", "")

    def test_index_rejected_at_chunk_limit_eviction_disabled(self, mock_dependencies):
        """Test that indexing is rejected when chunk limit reached and eviction disabled."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(
                cache_dir=temp_dir,
                max_total_chunks=2,
                enable_lru_eviction=False,
                show_stats=False,
            )

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                # Create first PDF
                pdf1 = Path(temp_dir) / "doc1.pdf"
                pdf1.write_text("dummy content 1")

                # Index first doc
                result1 = rag.index_document(str(pdf1))
                assert result1["success"] is True

                # If already at or over chunk limit, second doc should be rejected
                if len(rag.chunks) >= config.max_total_chunks:
                    pdf2 = Path(temp_dir) / "doc2.pdf"
                    pdf2.write_text("dummy content 2")

                    result2 = rag.index_document(str(pdf2))
                    assert result2["success"] is False
                    assert result2.get("memory_limit_reached") is True

    def test_lru_eviction_makes_room_for_new_doc(self, mock_dependencies):
        """Test that LRU eviction allows new doc when at file limit."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(
                cache_dir=temp_dir,
                max_indexed_files=1,
                enable_lru_eviction=True,
                show_stats=False,
            )

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                # Create two temp PDF files with different names
                pdf_a = Path(temp_dir) / "doc_a.pdf"
                pdf_a.write_text("content for doc A")
                pdf_b = Path(temp_dir) / "doc_b.pdf"
                pdf_b.write_text("content for doc B")

                # Index file A - should succeed
                result_a = rag.index_document(str(pdf_a))
                assert result_a["success"] is True

                # Index file B - should succeed (A gets evicted by _check_memory_limits)
                result_b = rag.index_document(str(pdf_b))
                assert result_b["success"] is True

                # File A should have been evicted, file B should remain
                abs_a = str(pdf_a.absolute())
                abs_b = str(pdf_b.absolute())
                assert abs_a not in rag.indexed_files
                assert abs_b in rag.indexed_files

    def test_stats_include_memory_limit_fields(self, mock_dependencies):
        """Test that stats dict includes memory limit information."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir, show_stats=False)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                # Create and index a PDF
                fake_pdf = Path(temp_dir) / "test.pdf"
                fake_pdf.write_text("dummy")

                result = rag.index_document(str(fake_pdf))

                assert result["success"] is True
                assert "max_indexed_files" in result
                assert "max_total_chunks" in result
                assert result["max_indexed_files"] == 100
                assert result["max_total_chunks"] == 10000

    def test_preflight_rejection_logged(self, mock_dependencies, caplog):
        """Test that pre-flight capacity rejection is logged when eviction is disabled.

        When enable_lru_eviction=False and the file limit is reached,
        _has_indexing_capacity returns False BEFORE _check_memory_limits
        is called, so the rejection happens at the pre-flight check.
        """
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        import logging

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(
                cache_dir=temp_dir,
                max_indexed_files=1,
                enable_lru_eviction=False,
                show_stats=False,
            )

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                # Create two temp PDF files
                pdf1 = Path(temp_dir) / "doc1.pdf"
                pdf1.write_text("dummy content 1")
                pdf2 = Path(temp_dir) / "doc2.pdf"
                pdf2.write_text("dummy content 2")

                # Index first doc
                rag.index_document(str(pdf1))

                # Index second doc with log capture
                with caplog.at_level(logging.WARNING):
                    rag.index_document(str(pdf2))

                # Check that a relevant warning was logged
                log_messages = " ".join(r.message for r in caplog.records)
                assert any(
                    keyword in log_messages
                    for keyword in ["Memory limit", "cannot", "eviction"]
                ), f"Expected memory limit warning in logs, got: {log_messages}"

    def test_eviction_failure_logged(self, mock_dependencies, caplog):
        """Test that a warning is logged when LRU eviction fails to free memory.

        With enable_lru_eviction=True, _has_indexing_capacity passes
        the pre-flight check (eviction could theoretically free space).
        After indexing, _check_memory_limits calls _evict_lru_document,
        which calls remove_document. If remove_document returns False,
        eviction fails and a warning about exceeding the file limit is logged.
        """
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        import logging

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(
                cache_dir=temp_dir,
                max_indexed_files=1,
                enable_lru_eviction=True,
                show_stats=False,
            )

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                rag = RAGSDK(config)

                # Create two temp PDF files
                pdf1 = Path(temp_dir) / "doc1.pdf"
                pdf1.write_text("dummy content 1")
                pdf2 = Path(temp_dir) / "doc2.pdf"
                pdf2.write_text("dummy content 2")

                # Index first doc - succeeds normally
                result1 = rag.index_document(str(pdf1))
                assert result1["success"] is True

                # Mock remove_document to return False so eviction fails
                with (
                    patch.object(rag, "remove_document", return_value=False),
                    caplog.at_level(logging.WARNING),
                ):
                    result2 = rag.index_document(str(pdf2))

                # The document still gets indexed (success=True) but
                # _check_memory_limits logs a warning about eviction failure
                assert result2["success"] is True

                # Verify warning about eviction failure was logged
                log_messages = " ".join(r.message for r in caplog.records)
                assert any(
                    keyword in log_messages
                    for keyword in [
                        "eviction failed",
                        "Failed to evict",
                        "Cannot meet file limit",
                    ]
                ), f"Expected eviction failure warning in logs, got: {log_messages}"

    def test_cache_load_tracks_access_times(self, mock_dependencies):
        """Test that loading from cache sets file_access_times and file_index_times."""
        if not RAG_AVAILABLE:
            pytest.skip(f"RAG dependencies not available: {IMPORT_ERROR}")

        with tempfile.TemporaryDirectory() as temp_dir:
            config = RAGConfig(cache_dir=temp_dir, show_stats=False)

            with patch("gaia.rag.sdk.RAGSDK._check_dependencies"):
                # First pass: index document to create cache
                rag1 = RAGSDK(config)

                fake_pdf = Path(temp_dir) / "test.pdf"
                fake_pdf.write_text("dummy")

                result1 = rag1.index_document(str(fake_pdf))
                assert result1["success"] is True

                file_path = str(fake_pdf.absolute())

                # Second pass: new RAGSDK instance loads from cache
                rag2 = RAGSDK(config)

                # Clear any state to ensure cache path is exercised
                rag2.indexed_files.clear()
                rag2.chunks.clear()
                rag2.file_access_times.clear()
                rag2.file_index_times.clear()

                result2 = rag2.index_document(str(fake_pdf))
                assert result2["success"] is True

                # Verify that access/index times were set during cache load
                assert file_path in rag2.file_access_times
                assert file_path in rag2.file_index_times


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
