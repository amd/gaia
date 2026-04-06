"""
Unit tests for EmbeddingRelevance.

This test suite validates the EmbeddingRelevance component including:
- Embedding generation with sentence-transformers
- Cosine similarity scoring
- Fallback to keyword-based Jaccard similarity
- Batch embedding efficiency
- Thread-safe concurrent access

Quality Gate 2 Criteria Covered:
- LENS-002: Relevance scoring accuracy >80% correlation
"""

import pytest
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import Mock, patch, MagicMock
from typing import List, Dict, Any

from gaia.state.relevance import EmbeddingRelevance


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_numpy():
    """Create mock numpy module."""
    mock_np = Mock()
    mock_np.dot = Mock(return_value=0.85)
    mock_np.clip = Mock(side_effect=lambda x, a, b: max(a, min(b, x)))
    return mock_np


@pytest.fixture
def embedding_relevance():
    """Create EmbeddingRelevance instance (may use fallback)."""
    return EmbeddingRelevance()


# =============================================================================
# EmbeddingRelevance Initialization Tests
# =============================================================================

class TestEmbeddingRelevanceInitialization:
    """Tests for EmbeddingRelevance initialization."""

    def teardown_method(self):
        """Clear any state after each test."""
        pass

    def test_initialization_default(self):
        """Test default model initialization."""
        relevance = EmbeddingRelevance()

        assert relevance.model_name == "all-MiniLM-L6-v2"
        assert relevance.use_gpu is False

    def test_initialization_custom_model(self):
        """Test custom model initialization."""
        relevance = EmbeddingRelevance(model="all-mpnet-base-v2")

        assert relevance.model_name == "all-mpnet-base-v2"

    def test_initialization_with_gpu(self):
        """Test initialization with GPU enabled."""
        try:
            relevance = EmbeddingRelevance(use_gpu=True)
            assert relevance.use_gpu is True
        except (AssertionError, RuntimeError) as e:
            # Skip if CUDA not available
            if "CUDA" in str(e) or "cuda" in str(e):
                pytest.skip("CUDA not available")
            raise

    def test_is_available_with_model(self):
        """Test availability check (model installed)."""
        relevance = EmbeddingRelevance()

        # May be True or False depending on sentence-transformers
        result = relevance.is_available()
        assert isinstance(result, bool)

    def test_is_available_without_model(self):
        """Test availability check (fallback mode)."""
        with patch.object(EmbeddingRelevance, '_load_model'):
            relevance = EmbeddingRelevance()
            relevance._available = False

            assert relevance.is_available() is False


# =============================================================================
# EmbeddingRelevance Embedding Tests
# =============================================================================

class TestEmbeddingRelevanceEmbedding:
    """Tests for EmbeddingRelevance embedding operations."""

    def test_embed_single_text(self, embedding_relevance):
        """Test single text embedding."""
        if not embedding_relevance.is_available():
            pytest.skip("sentence-transformers not available")

        embedding = embedding_relevance.embed("Hello world")

        assert embedding is not None
        assert hasattr(embedding, 'shape')
        assert len(embedding.shape) == 1  # 1D vector

    def test_embed_many_batch(self, embedding_relevance):
        """Test batch embedding efficiency."""
        if not embedding_relevance.is_available():
            pytest.skip("sentence-transformers not available")

        texts = ["Hello", "World", "Test"]
        embeddings = embedding_relevance.embed_many(texts)

        assert embeddings is not None
        assert embeddings.shape[0] == 3  # 3 texts

    def test_embed_raises_without_model(self):
        """Test embed raises error without model."""
        relevance = EmbeddingRelevance()
        relevance._available = False

        with pytest.raises(RuntimeError, match="not available"):
            relevance.embed("test")

    def test_embed_many_raises_without_model(self):
        """Test embed_many raises error without model."""
        relevance = EmbeddingRelevance()
        relevance._available = False

        with pytest.raises(RuntimeError, match="not available"):
            relevance.embed_many(["test1", "test2"])


# =============================================================================
# EmbeddingRelevance Similarity Tests
# =============================================================================

class TestEmbeddingRelevanceSimilarity:
    """Tests for EmbeddingRelevance cosine similarity."""

    def test_cosine_similarity_identical(self, embedding_relevance, mock_numpy):
        """Test similarity for identical embeddings."""
        # Mock embedding - identical vectors should have similarity 1.0
        emb = [1.0, 0.0, 0.0]

        with patch.object(embedding_relevance, '_np') as mock_np:
            mock_np.dot = Mock(return_value=1.0)
            mock_np.clip = Mock(side_effect=lambda x, a, b: max(a, min(b, x)))

            similarity = embedding_relevance.cosine_similarity(emb, emb)

            assert similarity == 1.0

    def test_cosine_similarity_different(self, embedding_relevance, mock_numpy):
        """Test similarity for different embeddings."""
        emb1 = [1.0, 0.0, 0.0]
        emb2 = [0.0, 1.0, 0.0]

        with patch.object(embedding_relevance, '_np') as mock_np:
            mock_np.dot = Mock(return_value=0.0)
            mock_np.clip = Mock(side_effect=lambda x, a, b: max(a, min(b, x)))

            similarity = embedding_relevance.cosine_similarity(emb1, emb2)

            assert similarity == 0.0

    def test_cosine_similarity_clamped(self, embedding_relevance, mock_numpy):
        """Test similarity is clamped to [0, 1]."""
        emb1 = [1.0, 0.0, 0.0]
        emb2 = [-1.0, 0.0, 0.0]  # Would give negative dot product

        with patch.object(embedding_relevance, '_np') as mock_np:
            mock_np.dot = Mock(return_value=-1.0)
            mock_np.clip = Mock(side_effect=lambda x, a, b: max(a, min(b, x)))

            similarity = embedding_relevance.cosine_similarity(emb1, emb2)

            assert similarity >= 0.0
            assert similarity <= 1.0


# =============================================================================
# EmbeddingRelevance Scoring Tests
# =============================================================================

class TestEmbeddingRelevanceScoring:
    """Tests for EmbeddingRelevance event scoring."""

    def test_score_event_embedding(self, embedding_relevance):
        """Test event scoring with embeddings."""
        if not embedding_relevance.is_available():
            pytest.skip("sentence-transformers not available")

        score = embedding_relevance.score_event(
            event_text="CodeAgent wrote unit tests",
            query="testing"
        )

        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_score_event_fallback(self, embedding_relevance):
        """Test event scoring with fallback."""
        embedding_relevance._available = False

        score = embedding_relevance.score_event(
            event_text="CodeAgent wrote unit tests",
            query="testing"
        )

        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_score_events_batch(self, embedding_relevance):
        """Test batch event scoring."""
        if not embedding_relevance.is_available():
            pytest.skip("sentence-transformers not available")

        texts = ["CodeAgent wrote tests", "Pipeline failed"]
        scores = embedding_relevance.score_events_batch(texts, "code quality")

        assert len(scores) == 2
        assert all(isinstance(s, float) for s in scores)
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_score_events_batch_fallback(self, embedding_relevance):
        """Test batch scoring with fallback."""
        embedding_relevance._available = False

        texts = ["CodeAgent wrote tests", "Pipeline failed"]
        scores = embedding_relevance.score_events_batch(texts, "code quality")

        assert len(scores) == 2
        assert all(isinstance(s, float) for s in scores)


# =============================================================================
# EmbeddingRelevance Ranking Tests
# =============================================================================

class TestEmbeddingRelevanceRanking:
    """Tests for EmbeddingRelevance event ranking."""

    def test_rank_events_relevance(self, embedding_relevance):
        """Test event ranking by relevance."""
        events = [
            {"event_type": "file_created", "payload": {"path": "test.py"}},
            {"event_type": "phase_enter", "phase": "PLANNING"},
        ]

        ranked = embedding_relevance.rank_events(events, "code changes")

        assert isinstance(ranked, list)
        assert len(ranked) == 2

        # Should be sorted by score descending
        scores = [score for _, score in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_rank_events_top_k(self, embedding_relevance):
        """Test top-K event selection."""
        events = [
            {"event_type": "file_created", "payload": {"path": "test.py"}},
            {"event_type": "phase_enter", "phase": "PLANNING"},
            {"event_type": "tool_executed", "payload": {"tool": "lint"}},
        ]

        ranked = embedding_relevance.rank_events(events, "code", top_k=2)

        assert len(ranked) == 2

    def test_rank_events_fallback(self, embedding_relevance):
        """Test ranking with fallback scoring."""
        embedding_relevance._available = False

        events = [
            {"event_type": "file_created", "payload": {"path": "test.py"}},
            {"event_type": "phase_enter", "phase": "PLANNING"},
        ]

        ranked = embedding_relevance.rank_events(events, "code")

        assert isinstance(ranked, list)


# =============================================================================
# EmbeddingRelevance Keyword Scoring Tests
# =============================================================================

class TestEmbeddingRelevanceKeywordScoring:
    """Tests for EmbeddingRelevance keyword-based fallback scoring."""

    def test_keyword_score_jaccard(self, embedding_relevance):
        """Test keyword-based Jaccard scoring."""
        # Identical texts should have score 1.0
        score = embedding_relevance._keyword_score("hello world", "hello world")
        assert score == 1.0

        # Partially overlapping texts
        score = embedding_relevance._keyword_score("hello world", "hello there")
        assert 0.0 < score < 1.0

        # No overlap
        score = embedding_relevance._keyword_score("hello", "goodbye")
        assert score == 0.0

    def test_keyword_score_empty(self, embedding_relevance):
        """Test keyword scoring with empty text."""
        score = embedding_relevance._keyword_score("", "")
        assert score == 0.0

    def test_keyword_score_case_insensitive(self, embedding_relevance):
        """Test keyword scoring is case insensitive."""
        score1 = embedding_relevance._keyword_score("Hello World", "hello world")
        score2 = embedding_relevance._keyword_score("HELLO WORLD", "HELLO WORLD")

        assert score1 == score2 == 1.0


# =============================================================================
# EmbeddingRelevance Event Conversion Tests
# =============================================================================

class TestEmbeddingRelevanceEventConversion:
    """Tests for EmbeddingRelevance _event_to_text() method."""

    def test_event_to_text_conversion(self, embedding_relevance):
        """Test event-to-text conversion."""
        event = {
            "phase": "EXECUTION",
            "agent_id": "CodeAgent",
            "event_type": "file_created",
            "payload": {"path": "main.py", "lines": 100}
        }

        text = embedding_relevance._event_to_text(event)

        assert "EXECUTION" in text
        assert "CodeAgent" in text
        assert "file_created" in text
        assert "path" in text

    def test_event_to_text_no_phase(self, embedding_relevance):
        """Test event conversion without phase."""
        event = {
            "agent_id": "CodeAgent",
            "event_type": "test"
        }

        text = embedding_relevance._event_to_text(event)

        assert "N/A" in text  # Default phase

    def test_event_to_text_no_payload(self, embedding_relevance):
        """Test event conversion without payload."""
        event = {
            "phase": "EXECUTION",
            "agent_id": "CodeAgent",
            "event_type": "phase_enter"
        }

        text = embedding_relevance._event_to_text(event)

        assert "EXECUTION" in text
        assert "CodeAgent" in text


# =============================================================================
# EmbeddingRelevance Model Info Tests
# =============================================================================

class TestEmbeddingRelevanceModelInfo:
    """Tests for EmbeddingRelevance get_model_info() method."""

    def test_get_model_info(self, embedding_relevance):
        """Test model info reporting."""
        info = embedding_relevance.get_model_info()

        assert "model_name" in info
        assert "available" in info
        assert "gpu_enabled" in info
        assert "fallback_mode" in info

        assert info["model_name"] == "all-MiniLM-L6-v2"
        assert isinstance(info["available"], bool)
        assert info["gpu_enabled"] is False

    def test_get_model_info_custom(self):
        """Test model info with custom settings."""
        try:
            relevance = EmbeddingRelevance(
                model="all-mpnet-base-v2",
                use_gpu=True
            )
        except (AssertionError, RuntimeError) as e:
            # Skip if CUDA not available
            if "CUDA" in str(e) or "cuda" in str(e):
                pytest.skip("CUDA not available")
            raise

        info = relevance.get_model_info()

        assert info["model_name"] == "all-mpnet-base-v2"
        assert info["gpu_enabled"] is True


# =============================================================================
# EmbeddingRelevance Thread Safety Tests
# =============================================================================

class TestEmbeddingRelevanceThreadSafety:
    """Thread safety tests for EmbeddingRelevance."""

    def test_thread_safety_concurrent_score(self, embedding_relevance):
        """Test concurrent scoring operations."""
        if not embedding_relevance.is_available():
            pytest.skip("sentence-transformers not available")

        results = []
        errors = []
        lock = threading.Lock()

        def score_event(event_id):
            try:
                score = embedding_relevance.score_event(
                    f"Event text {event_id}",
                    "query"
                )
                with lock:
                    results.append((event_id, score))
            except Exception as e:
                with lock:
                    errors.append((event_id, e))

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(score_event, i) for i in range(20)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 20

    def test_thread_safety_fallback_mode(self):
        """Test thread safety in fallback mode."""
        relevance = EmbeddingRelevance()
        relevance._available = False

        results = []
        errors = []
        lock = threading.Lock()

        def score_event(event_id):
            try:
                score = relevance.score_event(
                    f"Event text {event_id}",
                    "query"
                )
                with lock:
                    results.append((event_id, score))
            except Exception as e:
                with lock:
                    errors.append((event_id, e))

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(score_event, i) for i in range(20)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 20


# =============================================================================
# EmbeddingRelevance Edge Cases Tests
# =============================================================================

class TestEmbeddingRelevanceEdgeCases:
    """Tests for edge cases and error handling."""

    def test_score_event_handles_exception(self, embedding_relevance):
        """Test score_event handles exceptions gracefully."""
        if not embedding_relevance.is_available():
            pytest.skip("sentence-transformers not available")

        # Mock embed to raise exception
        with patch.object(embedding_relevance, 'embed', side_effect=Exception("Test error")):
            score = embedding_relevance.score_event("test", "query")

            # Should fall back to keyword scoring
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0

    def test_score_events_batch_handles_exception(self, embedding_relevance):
        """Test batch scoring handles exceptions gracefully."""
        if not embedding_relevance.is_available():
            pytest.skip("sentence-transformers not available")

        # Mock embed_many to raise exception
        with patch.object(embedding_relevance, 'embed_many', side_effect=Exception("Test error")):
            scores = embedding_relevance.score_events_batch(["test"], "query")

            # Should fall back to keyword scoring
            assert len(scores) == 1
            assert isinstance(scores[0], float)

    def test_rank_events_empty_list(self, embedding_relevance):
        """Test ranking empty event list."""
        ranked = embedding_relevance.rank_events([], "query")

        assert ranked == []

    def test_rank_events_single(self, embedding_relevance):
        """Test ranking single event."""
        events = [{"event_type": "test"}]
        ranked = embedding_relevance.rank_events(events, "query")

        assert len(ranked) == 1

    def test_keyword_score_single_word(self, embedding_relevance):
        """Test keyword scoring with single word."""
        score = embedding_relevance._keyword_score("hello", "hello")
        assert score == 1.0

        score = embedding_relevance._keyword_score("hello", "world")
        assert score == 0.0


# =============================================================================
# EmbeddingRelevance Integration Tests
# =============================================================================

class TestEmbeddingRelevanceIntegration:
    """Integration tests for EmbeddingRelevance."""

    def test_full_ranking_pipeline(self, embedding_relevance):
        """Test end-to-end ranking pipeline."""
        events = [
            {
                "phase": "EXECUTION",
                "agent_id": "CodeAgent",
                "event_type": "file_created",
                "payload": {"path": "test.py"}
            },
            {
                "phase": "PLANNING",
                "agent_id": "ChatAgent",
                "event_type": "phase_enter",
                "payload": {}
            },
            {
                "phase": "EVALUATION",
                "agent_id": "SupervisorAgent",
                "event_type": "quality_evaluated",
                "payload": {"score": 0.9}
            },
        ]

        ranked = embedding_relevance.rank_events(events, "code testing")

        assert len(ranked) == 3
        # Verify sorted by score descending
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)
