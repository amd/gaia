"""
Token Counter - Accurate Token Counting for GAIA

Provides accurate token counting using tiktoken for GPT-style models,
with fallback estimation for local models (GGUF, etc.).

Features:
    - tiktoken integration for OpenAI/Claude models
    - Fallback estimation (~4 chars/token) for local models
    - Thread-safe counting operations
    - Budget enforcement with intelligent truncation

Example:
    >>> from gaia.state.token_counter import TokenCounter
    >>> counter = TokenCounter(model="gpt-4")
    >>> tokens = counter.count("Hello, world!")
    >>> counter.truncate_to_budget(text, max_tokens=100)
"""

import threading
from typing import Optional, List, Tuple, Dict, Any
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class TokenCounter:
    """
    Accurate token counter with model-specific encoding.

    The TokenCounter provides:
    1. tiktoken-based counting for OpenAI/Claude models
    2. Fallback estimation for unsupported models
    3. Budget enforcement with intelligent truncation
    4. Thread-safe concurrent access

    Supported Encodings:
        - cl100k_base (GPT-4, GPT-3.5-Turbo)
        - p50k_base (Codex, text-davinci-002)
        - r50k_base (GPT-3 models)

    Fallback Mode:
        When tiktoken is not installed, falls back to character-based
        estimation (~4 characters per token for English text).

    Thread Safety:
        - RLock protection for all counting operations
        - Shared encoding cache with thread-safe access
        - Safe for concurrent use in multi-threaded environments

    Example:
        >>> counter = TokenCounter(model="gpt-4")
        >>> counter.count("Hello")
        1
        >>> counter.count_many(["Hello", "World"])
        [1, 1]
    """

    # Class-level encoding cache shared across instances
    _encoding_cache: Dict[str, Any] = {}
    _cache_lock = threading.Lock()

    # Event type weights for relevance scoring
    EVENT_TYPE_WEIGHTS = {
        "decision_made": 2.0,
        "quality_evaluated": 2.0,
        "defect_discovered": 1.5,
        "defect_remediated": 1.5,
        "phase_enter": 1.0,
        "phase_exit": 1.0,
        "agent_selected": 1.0,
        "agent_executed": 1.0,
        "tool_executed": 0.8,
        "loop_back": 1.5,
    }

    def __init__(self, model: str = "cl100k_base"):
        """
        Initialize token counter with model-specific encoding.

        Args:
            model: Model name or encoding (default: cl100k_base)
                   Supports: gpt-4, gpt-3.5-turbo, cl100k_base,
                            p50k_base, r50k_base

        Example:
            >>> counter = TokenCounter("gpt-4")
            >>> counter.get_encoding_info()["tiktoken_available"]
            True  # if tiktoken installed
        """
        self.model = model
        self._encoding = self._get_encoding(model)
        self._lock = threading.RLock()

    def _get_encoding(self, model: str) -> Optional[Any]:
        """
        Get or create encoding for model.

        Uses class-level cache to share encodings across instances.
        Gracefully handles missing tiktoken by returning None.

        Args:
            model: Model name or encoding identifier

        Returns:
            tiktoken.Encoding object or None if unavailable
        """
        with self._cache_lock:
            if model in self._encoding_cache:
                cached = self._encoding_cache[model]
                if cached is None:
                    logger.debug(f"Using cached fallback mode for {model}")
                return cached

            # Try to load tiktoken
            try:
                import tiktoken

                # Map model names to encodings
                encoding_map = {
                    "gpt-4": "cl100k_base",
                    "gpt-3.5-turbo": "cl100k_base",
                    "gpt-35-turbo": "cl100k_base",
                    "claude-3": "cl100k_base",
                    "claude-2": "cl100k_base",
                    "codex": "p50k_base",
                    "text-davinci-002": "p50k_base",
                    "gpt-3": "r50k_base",
                    "text-davinci-001": "r50k_base",
                }

                encoding_name = encoding_map.get(model, model)

                try:
                    encoding = tiktoken.get_encoding(encoding_name)
                except ValueError:
                    # If encoding name not found, try as encoding directly
                    encoding = tiktoken.get_encoding(model)

                self._encoding_cache[model] = encoding
                logger.info(
                    f"Loaded tiktoken encoding for {model}",
                    extra={"encoding": encoding_name}
                )
                return encoding

            except ImportError:
                logger.warning(
                    f"tiktoken not installed, using fallback estimation for {model}. "
                    f"Install with: pip install tiktoken"
                )
                self._encoding_cache[model] = None
                return None

    def count(self, text: str) -> int:
        """
        Count tokens in text.

        Uses tiktoken for accurate counting when available.
        Falls back to ~4 chars/token estimation otherwise.

        Args:
            text: Text to count tokens for

        Returns:
            Token count (exact if tiktoken available, estimated otherwise)

        Example:
            >>> counter = TokenCounter("gpt-4")
            >>> counter.count("Hello, world!")
            4
        """
        with self._lock:
            if self._encoding is not None:
                return len(self._encoding.encode(text))
            else:
                # Fallback: ~4 chars/token for English
                return max(1, len(text) // 4)

    def count_many(self, texts: List[str]) -> List[int]:
        """
        Count tokens for multiple texts efficiently.

        Uses batch encoding when tiktoken is available for better performance.

        Args:
            texts: List of texts to count

        Returns:
            List of token counts

        Example:
            >>> counter = TokenCounter("gpt-4")
            >>> counter.count_many(["Hello", "World"])
            [1, 1]
        """
        with self._lock:
            if self._encoding is not None:
                # Batch encoding is more efficient
                try:
                    all_tokens = self._encoding.encode_many(texts)
                    return [len(tokens) for tokens in all_tokens]
                except Exception:
                    # Fallback to individual counting
                    return [len(self._encoding.encode(text)) for text in texts]
            else:
                return [max(1, len(text) // 4) for text in texts]

    def truncate_to_budget(
        self,
        text: str,
        max_tokens: int,
        preserve_sentences: bool = True,
    ) -> str:
        """
        Truncate text to fit within token budget.

        Intelligently truncates text while attempting to preserve sentence
        boundaries for better readability.

        Args:
            text: Text to truncate
            max_tokens: Maximum token budget
            preserve_sentences: If True, preserve sentence boundaries
                               (default: True)

        Returns:
            Truncated text within budget

        Example:
            >>> counter = TokenCounter("gpt-4")
            >>> long_text = "First sentence. Second sentence. Third sentence."
            >>> counter.truncate_to_budget(long_text, max_tokens=5)
            'First sentence.'
        """
        with self._lock:
            if self._encoding is not None:
                tokens = self._encoding.encode(text)
                if len(tokens) <= max_tokens:
                    return text

                # Truncate tokens
                truncated_tokens = tokens[:max_tokens]

                if preserve_sentences:
                    # Try to find sentence boundary
                    truncated_text = self._encoding.decode(truncated_tokens)
                    # Find last sentence-ending punctuation
                    for punct in [".", "!", "?", "\n"]:
                        last_idx = truncated_text.rfind(punct)
                        if last_idx > len(truncated_text) * 0.5:
                            return truncated_text[:last_idx + 1]
                    return truncated_text
                else:
                    return self._encoding.decode(truncated_tokens)
            else:
                # Fallback: character-based truncation
                max_chars = max_tokens * 4
                if len(text) <= max_chars:
                    return text

                truncated = text[:max_chars]
                if preserve_sentences:
                    for punct in [".", "!", "?", "\n"]:
                        last_idx = truncated.rfind(punct)
                        if last_idx > len(truncated) * 0.5:
                            return truncated[:last_idx + 1]
                return truncated

    def estimate_budget(
        self,
        texts: List[str],
        max_tokens: int,
    ) -> Tuple[List[str], int]:
        """
        Select texts that fit within token budget.

        Uses greedy selection: adds texts until budget is exceeded,
        then attempts to fit a truncated version of the next text.

        Args:
            texts: List of texts to select from
            max_tokens: Maximum total token budget

        Returns:
            Tuple of (selected_texts, total_tokens)

        Example:
            >>> texts = ["Short", "Medium text", "Very long text..."]
            >>> counter = TokenCounter("gpt-4")
            >>> selected, tokens = counter.estimate_budget(texts, max_tokens=10)
        """
        with self._lock:
            selected = []
            total_tokens = 0

            counts = self.count_many(texts)

            for text, count in zip(texts, counts):
                if total_tokens + count <= max_tokens:
                    selected.append(text)
                    total_tokens += count
                else:
                    # Try to fit partial text
                    remaining = max_tokens - total_tokens
                    if remaining > 0:
                        truncated = self.truncate_to_budget(text, remaining)
                        if truncated != text:
                            selected.append(truncated)
                            total_tokens += self.count(truncated)
                    break

            return selected, total_tokens

    def get_encoding_info(self) -> Dict[str, Any]:
        """
        Get information about current encoding.

        Returns:
            Dictionary with encoding details:
            - model: Model name
            - tiktoken_available: Whether tiktoken is available
            - fallback_mode: Whether using fallback estimation

        Example:
            >>> counter = TokenCounter("gpt-4")
            >>> info = counter.get_encoding_info()
            >>> info["tiktoken_available"]
            True
        """
        with self._lock:
            return {
                "model": self.model,
                "tiktoken_available": self._encoding is not None,
                "fallback_mode": self._encoding is None,
            }
