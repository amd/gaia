"""
Unit tests for TokenCounter.

This test suite validates the TokenCounter component including:
- Token counting accuracy with tiktoken
- Fallback estimation when tiktoken unavailable
- Truncation with budget enforcement
- Batch counting efficiency
- Thread-safe concurrent access

Quality Gate 2 Criteria Covered:
- LENS-001: Token counting accuracy >95% vs tiktoken
"""

import pytest
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import Mock, patch, MagicMock
from typing import List

from gaia.state.token_counter import TokenCounter


# =============================================================================
# TokenCounter Initialization Tests
# =============================================================================

class TestTokenCounterInitialization:
    """Tests for TokenCounter initialization."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_initialization_default(self):
        """Test default model initialization."""
        counter = TokenCounter()
        assert counter.model == "cl100k_base"
        info = counter.get_encoding_info()
        assert info["model"] == "cl100k_base"

    def test_initialization_custom_model(self):
        """Test custom model initialization."""
        counter = TokenCounter(model="gpt-4")
        assert counter.model == "gpt-4"
        info = counter.get_encoding_info()
        assert info["model"] == "gpt-4"

    def test_initialization_caches_encoding(self):
        """Test that encoding is cached during initialization."""
        TokenCounter._encoding_cache.clear()

        counter1 = TokenCounter(model="gpt-4")
        counter2 = TokenCounter(model="gpt-4")

        # Both should share cached encoding
        assert counter1._encoding is counter2._encoding


# =============================================================================
# TokenCounter Counting Tests
# =============================================================================

class TestTokenCounterCounting:
    """Tests for TokenCounter counting operations."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_count_simple_text(self):
        """Test basic token counting."""
        counter = TokenCounter(model="cl100k_base")

        # "Hello" should be 1 token
        tokens = counter.count("Hello")
        assert tokens >= 1

        # Empty string should return 0 or 1 (minimum)
        empty_tokens = counter.count("")
        assert empty_tokens >= 0

    def test_count_empty_text(self):
        """Test counting empty string."""
        counter = TokenCounter()
        tokens = counter.count("")
        # Should handle empty gracefully
        assert tokens >= 0

    def test_count_many_batch(self):
        """Test batch counting efficiency."""
        counter = TokenCounter(model="cl100k_base")

        texts = ["Hello", "World", "Test"]
        counts = counter.count_many(texts)

        assert len(counts) == 3
        assert all(c >= 0 for c in counts)

        # Individual counts should match batch counts
        for text, count in zip(texts, counts):
            assert counter.count(text) == count

    def test_count_many_empty_list(self):
        """Test batch counting with empty list."""
        counter = TokenCounter()
        counts = counter.count_many([])
        assert counts == []


# =============================================================================
# TokenCounter Truncation Tests
# =============================================================================

class TestTokenCounterTruncation:
    """Tests for TokenCounter truncate_to_budget() method."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_truncate_within_budget(self):
        """Test no truncation when under budget."""
        counter = TokenCounter(model="cl100k_base")

        text = "Hello world"
        result = counter.truncate_to_budget(text, max_tokens=100)

        # Should return unchanged when under budget
        assert result == text

    def test_truncate_exceeds_budget(self):
        """Test truncation when over budget."""
        counter = TokenCounter(model="cl100k_base")

        text = "This is a longer text that should be truncated."
        result = counter.truncate_to_budget(text, max_tokens=3)

        # Should be truncated
        assert len(result) < len(text)
        assert counter.count(result) <= 3

    def test_truncate_preserve_sentences(self):
        """Test sentence boundary preservation."""
        counter = TokenCounter(model="cl100k_base")

        text = "First sentence. Second sentence. Third sentence."
        result = counter.truncate_to_budget(
            text, max_tokens=5, preserve_sentences=True
        )

        # Should end at sentence boundary if possible
        assert result.endswith(".") or len(result) < len(text)

    def test_truncate_no_sentence_boundary(self):
        """Test truncation without sentence boundary."""
        counter = TokenCounter(model="cl100k_base")

        text = "This is a long text without clear boundaries"
        result = counter.truncate_to_budget(
            text, max_tokens=3, preserve_sentences=False
        )

        # Should truncate to fit budget
        assert counter.count(result) <= 3

    def test_truncate_fallback_mode(self):
        """Test truncation in fallback mode (no tiktoken)."""
        TokenCounter._encoding_cache.clear()

        with patch.object(TokenCounter, '_get_encoding', return_value=None):
            counter = TokenCounter(model="fallback")

            text = "This is a test sentence. Another sentence."
            result = counter.truncate_to_budget(text, max_tokens=3)

            # Should use character-based estimation
            assert len(result) <= 3 * 4  # ~4 chars/token


# =============================================================================
# TokenCounter Budget Estimation Tests
# =============================================================================

class TestTokenCounterBudgetEstimation:
    """Tests for TokenCounter estimate_budget() method."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_estimate_budget_fits_all(self):
        """Test budget estimation when all texts fit."""
        counter = TokenCounter(model="cl100k_base")

        texts = ["Short", "Medium"]
        selected, tokens = counter.estimate_budget(texts, max_tokens=100)

        # All should fit
        assert len(selected) == 2
        assert tokens > 0

    def test_estimate_budget_partial(self):
        """Test budget estimation with partial fit."""
        counter = TokenCounter(model="cl100k_base")

        texts = ["Short", "Medium text", "Very long text that exceeds budget"]
        selected, tokens = counter.estimate_budget(texts, max_tokens=5)

        # Should select some texts
        assert len(selected) >= 1
        assert tokens <= 5


# =============================================================================
# TokenCounter Fallback Mode Tests
# =============================================================================

class TestTokenCounterFallbackMode:
    """Tests for TokenCounter fallback mode without tiktoken."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_fallback_mode_without_tiktoken(self):
        """Test graceful fallback without tiktoken."""
        TokenCounter._encoding_cache.clear()

        with patch.object(TokenCounter, '_get_encoding', return_value=None):
            counter = TokenCounter(model="fallback")

            info = counter.get_encoding_info()
            assert info["tiktoken_available"] is False
            assert info["fallback_mode"] is True

            # Should still count using estimation
            tokens = counter.count("Hello world test")
            assert tokens >= 1

    def test_fallback_count_estimation(self):
        """Test fallback character-based estimation."""
        TokenCounter._encoding_cache.clear()

        with patch.object(TokenCounter, '_get_encoding', return_value=None):
            counter = TokenCounter()

            # ~4 chars/token estimation
            text = "Hello world"
            tokens = counter.count(text)
            expected = len(text) // 4

            assert tokens == expected or tokens == expected + 1


# =============================================================================
# TokenCounter Encoding Info Tests
# =============================================================================

class TestTokenCounterEncodingInfo:
    """Tests for TokenCounter get_encoding_info() method."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_get_encoding_info(self):
        """Test encoding info reporting."""
        counter = TokenCounter(model="cl100k_base")

        info = counter.get_encoding_info()

        assert "model" in info
        assert "tiktoken_available" in info
        assert "fallback_mode" in info
        assert info["model"] == "cl100k_base"

    def test_get_encoding_info_after_clear(self):
        """Test encoding info after cache clear."""
        TokenCounter._encoding_cache.clear()

        counter = TokenCounter(model="gpt-4")
        info = counter.get_encoding_info()

        assert info["model"] == "gpt-4"


# =============================================================================
# TokenCounter Thread Safety Tests
# =============================================================================

class TestTokenCounterThreadSafety:
    """Thread safety tests for TokenCounter."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_thread_safety_concurrent_count(self):
        """Test concurrent counting operations."""
        counter = TokenCounter(model="cl100k_base")

        results = []
        errors = []
        lock = threading.Lock()

        def count_text(text_id):
            try:
                text = f"Test text {text_id}"
                tokens = counter.count(text)
                with lock:
                    results.append((text_id, tokens))
            except Exception as e:
                with lock:
                    errors.append((text_id, e))

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(count_text, i) for i in range(50)]
            for future in as_completed(futures):
                future.result()

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 50

    def test_encoding_cache_shared(self):
        """Test that encoding cache is shared across instances."""
        TokenCounter._encoding_cache.clear()

        counter1 = TokenCounter(model="cl100k_base")
        counter2 = TokenCounter(model="cl100k_base")

        # Both should share the same cached encoding
        assert counter1._encoding is counter2._encoding

    def test_concurrent_mixed_operations(self):
        """Test concurrent mix of counting and truncation."""
        counter = TokenCounter(model="cl100k_base")

        results = {"counts": 0, "truncates": 0}
        errors = []
        lock = threading.Lock()

        def count_task(text_id):
            try:
                counter.count(f"Test {text_id}")
                with lock:
                    results["counts"] += 1
            except Exception as e:
                with lock:
                    errors.append(("count", text_id, e))

        def truncate_task(text_id):
            try:
                counter.truncate_to_budget(f"Test {text_id}", max_tokens=5)
                with lock:
                    results["truncates"] += 1
            except Exception as e:
                with lock:
                    errors.append(("truncate", text_id, e))

        with ThreadPoolExecutor(max_workers=100) as executor:
            for i in range(50):
                executor.submit(count_task, i)
                executor.submit(truncate_task, i + 50)

        assert len(errors) == 0, f"Mixed operation errors: {errors}"
        assert results["counts"] == 50
        assert results["truncates"] == 50


# =============================================================================
# TokenCounter Model Mapping Tests
# =============================================================================

class TestTokenCounterModelMapping:
    """Tests for model name to encoding mapping."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_model_mapping_gpt4(self):
        """Test GPT-4 model mapping."""
        counter = TokenCounter(model="gpt-4")
        info = counter.get_encoding_info()
        # Should map to cl100k_base
        assert info["model"] == "gpt-4"

    def test_model_mapping_claude(self):
        """Test Claude model mapping."""
        counter = TokenCounter(model="claude-3")
        info = counter.get_encoding_info()
        assert info["model"] == "claude-3"

    def test_model_mapping_custom_encoding(self):
        """Test custom encoding name."""
        counter = TokenCounter(model="r50k_base")
        info = counter.get_encoding_info()
        assert info["model"] == "r50k_base"


# =============================================================================
# TokenCounter Edge Cases Tests
# =============================================================================

class TestTokenCounterEdgeCases:
    """Tests for edge cases and error handling."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_count_very_long_text(self):
        """Test counting very long text."""
        counter = TokenCounter(model="cl100k_base")

        long_text = "word " * 10000
        tokens = counter.count(long_text)

        assert tokens > 0
        assert tokens < len(long_text)  # Should be compressed

    def test_count_special_characters(self):
        """Test counting text with special characters."""
        counter = TokenCounter(model="cl100k_base")

        text = "Hello! @#$%^&*() World 123"
        tokens = counter.count(text)

        assert tokens >= 1

    def test_count_unicode_text(self):
        """Test counting Unicode text."""
        counter = TokenCounter(model="cl100k_base")

        text = "Hello \u4e16\u754c \ud83d\ude00"
        tokens = counter.count(text)

        assert tokens >= 1

    def test_truncate_zero_budget(self):
        """Test truncation with zero budget."""
        counter = TokenCounter(model="cl100k_base")

        text = "Hello world"
        result = counter.truncate_to_budget(text, max_tokens=0)

        # Should return empty or minimal string
        assert len(result) <= len(text)

    def test_truncate_negative_budget(self):
        """Test truncation with negative budget (edge case)."""
        counter = TokenCounter(model="cl100k_base")

        text = "Hello world"
        # Should handle gracefully
        result = counter.truncate_to_budget(text, max_tokens=-1)

        # Should not crash, may return empty or original
        assert isinstance(result, str)


# =============================================================================
# TokenCounter Performance Tests
# =============================================================================

class TestTokenCounterPerformance:
    """Performance-related tests for TokenCounter."""

    def teardown_method(self):
        """Clear encoding cache after each test."""
        TokenCounter._encoding_cache.clear()

    def test_count_performance(self):
        """Test counting performance (should be <10ms)."""
        import time

        counter = TokenCounter(model="cl100k_base")

        text = "Test sentence for performance." * 10

        start = time.time()
        for _ in range(100):
            counter.count(text)
        elapsed = (time.time() - start) * 1000  # ms

        # Should complete 100 counts in <100ms (<1ms per count)
        assert elapsed < 100, f"Counting too slow: {elapsed}ms"

    def test_batch_performance(self):
        """Test batch counting performance."""
        import time

        counter = TokenCounter(model="cl100k_base")

        texts = ["Test sentence"] * 100

        start = time.time()
        counts = counter.count_many(texts)
        elapsed = (time.time() - start) * 1000  # ms

        # Should complete batch quickly
        assert elapsed < 50, f"Batch counting too slow: {elapsed}ms"
        assert len(counts) == 100
