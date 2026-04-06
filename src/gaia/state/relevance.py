"""
Embedding Relevance - Semantic Similarity for Event Scoring

Provides embedding-based relevance scoring using sentence transformers
for semantic similarity between agent context and events.

Features:
    - Sentence transformer embeddings (optional)
    - Cosine similarity scoring
    - Fallback to rule-based scoring
    - Efficient batch embedding

Example:
    >>> from gaia.state.relevance import EmbeddingRelevance
    >>> relevance = EmbeddingRelevance()
    >>> score = relevance.score_event(
    ...     event_text="CodeAgent fixed bug in parser",
    ...     query="CodeAgent code changes"
    ... )
    >>> scores = relevance.rank_events(events, query="debugging")
"""

import threading
from typing import Dict, List, Optional, Tuple, Any
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class EmbeddingRelevance:
    """
    Semantic similarity scoring via embeddings.

    The EmbeddingRelevance provides:
    1. Sentence transformer embeddings for semantic similarity
    2. Cosine similarity scoring (0.0 - 1.0)
    3. Batch embedding for efficiency
    4. Graceful fallback to rule-based scoring

    Models:
        - Default: all-MiniLM-L6-v2 (fast, small, good quality)
        - Alternative: all-mpnet-base-v2 (slower, better quality)

    Fallback:
        If sentence-transformers not installed, falls back to
        keyword-based relevance scoring using Jaccard similarity.

    Thread Safety:
        - RLock protection for all scoring operations
        - Safe for concurrent access from multiple threads

    Example:
        >>> relevance = EmbeddingRelevance(model="all-MiniLM-L6-v2")
        >>> events = ["CodeAgent wrote tests", "Pipeline failed"]
        >>> scores = relevance.score_against_query(events, "code quality")
        >>> print(scores)  # [0.85, 0.32]
    """

    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        use_gpu: bool = False,
    ):
        """
        Initialize embedding relevance scorer.

        Args:
            model: Sentence transformer model name
                   (default: all-MiniLM-L6-v2)
            use_gpu: Enable GPU acceleration (default: False)

        Example:
            >>> relevance = EmbeddingRelevance()
            >>> relevance.is_available()
            True  # if sentence-transformers installed
        """
        self.model_name = model
        self.use_gpu = use_gpu
        self._model = None
        self._available = False
        self._lock = threading.RLock()
        self._np = None  # numpy module reference

        self._load_model()

    def _load_model(self) -> None:
        """
        Load sentence transformer model.

        Attempts to import and initialize sentence-transformers.
        Gracefully handles ImportError by enabling fallback mode.
        """
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np

            device = "cuda" if self.use_gpu else "cpu"
            self._model = SentenceTransformer(self.model_name, device=device)
            self._np = np
            self._available = True

            logger.info(
                f"EmbeddingRelevance loaded with {self.model_name} on {device}",
                extra={"gpu": self.use_gpu}
            )

        except ImportError as e:
            logger.warning(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers. "
                "Falling back to keyword-based scoring."
            )
            self._available = False
            self._model = None
            self._np = None

    def is_available(self) -> bool:
        """
        Check if embedding model is available.

        Returns:
            True if sentence-transformers is installed and loaded,
            False if using fallback keyword scoring

        Example:
            >>> relevance = EmbeddingRelevance()
            >>> relevance.is_available()
            True
        """
        return self._available

    def embed(self, text: str) -> Any:
        """
        Generate embedding for text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector (numpy array)

        Raises:
            RuntimeError: If model not available

        Example:
            >>> relevance = EmbeddingRelevance()
            >>> emb = relevance.embed("Hello world")
            >>> emb.shape
            (384,)  # for all-MiniLM-L6-v2
        """
        with self._lock:
            if not self._available:
                raise RuntimeError(
                    "Embedding model not available. "
                    "Install sentence-transformers or use fallback scoring."
                )

            embedding = self._model.encode(
                text,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            return embedding

    def embed_many(self, texts: List[str]) -> Any:
        """
        Generate embeddings for multiple texts efficiently.

        Uses batch encoding for better performance.

        Args:
            texts: List of texts to embed

        Returns:
            Embedding matrix (n_texts x embedding_dim)

        Raises:
            RuntimeError: If model not available

        Example:
            >>> relevance = EmbeddingRelevance()
            >>> texts = ["Hello", "World", "Test"]
            >>> embs = relevance.embed_many(texts)
            >>> embs.shape
            (3, 384)
        """
        with self._lock:
            if not self._available:
                raise RuntimeError("Embedding model not available")

            embeddings = self._model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                batch_size=32,
                show_progress_bar=False,
            )
            return embeddings

    def cosine_similarity(
        self,
        embedding1: Any,
        embedding2: Any,
    ) -> float:
        """
        Compute cosine similarity between two embeddings.

        For normalized embeddings, this is equivalent to dot product.

        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector

        Returns:
            Similarity score (0.0 - 1.0 for normalized embeddings)

        Example:
            >>> emb1 = relevance.embed("Hello")
            >>> emb2 = relevance.embed("Hi")
            >>> relevance.cosine_similarity(emb1, emb2)
            0.75
        """
        # For normalized embeddings, dot product = cosine similarity
        similarity = self._np.dot(embedding1, embedding2)
        # Clamp to [0, 1] range
        return float(self._np.clip(similarity, 0.0, 1.0))

    def score_event(
        self,
        event_text: str,
        query: str,
        event_embedding: Optional[Any] = None,
    ) -> float:
        """
        Score event relevance against query.

        Args:
            event_text: Event text to score
            query: Query/relevance target
            event_embedding: Optional pre-computed event embedding

        Returns:
            Relevance score (0.0 - 1.0)

        Example:
            >>> relevance = EmbeddingRelevance()
            >>> score = relevance.score_event(
            ...     "CodeAgent wrote unit tests",
            ...     "testing"
            ... )
            >>> print(score)
            0.82
        """
        with self._lock:
            if not self._available:
                # Fallback to keyword scoring
                return self._keyword_score(event_text, query)

            try:
                # Get embeddings
                if event_embedding is None:
                    event_emb = self.embed(event_text)
                else:
                    event_emb = event_embedding

                query_emb = self.embed(query)

                # Compute similarity
                return self.cosine_similarity(event_emb, query_emb)

            except Exception as e:
                logger.warning(f"Embedding scoring failed: {e}, using fallback")
                return self._keyword_score(event_text, query)

    def score_events_batch(
        self,
        event_texts: List[str],
        query: str,
    ) -> List[float]:
        """
        Score multiple events against query efficiently.

        Uses batch embedding for performance.

        Args:
            event_texts: List of event texts to score
            query: Query/relevance target

        Returns:
            List of relevance scores (0.0 - 1.0)

        Example:
            >>> relevance = EmbeddingRelevance()
            >>> texts = ["CodeAgent wrote tests", "Pipeline failed"]
            >>> scores = relevance.score_events_batch(texts, "code quality")
            >>> print(scores)
            [0.85, 0.32]
        """
        with self._lock:
            if not self._available:
                return [self._keyword_score(text, query) for text in event_texts]

            try:
                # Batch embed events
                event_embs = self.embed_many(event_texts)
                query_emb = self.embed(query)

                # Compute similarities (matrix multiplication)
                similarities = event_embs @ query_emb.T
                scores = self._np.clip(similarities.flatten(), 0.0, 1.0)
                return scores.tolist()

            except Exception as e:
                logger.warning(f"Batch scoring failed: {e}, using fallback")
                return [self._keyword_score(text, query) for text in event_texts]

    def rank_events(
        self,
        events: List[Dict[str, Any]],
        query: str,
        top_k: Optional[int] = None,
    ) -> List[Tuple[Dict[str, Any], float]]:
        """
        Rank events by relevance to query.

        Args:
            events: List of event dictionaries to rank
            query: Query/relevance target
            top_k: Return only top K events (None = all)

        Returns:
            List of (event, score) tuples sorted by score descending

        Example:
            >>> events = [
            ...     {"event_type": "file_created", "payload": {"path": "test.py"}},
            ...     {"event_type": "phase_enter", "phase": "PLANNING"}
            ... ]
            >>> ranked = relevance.rank_events(events, "code changes")
            >>> for event, score in ranked:
            ...     print(f"{score:.2f}: {event['event_type']}")
        """
        # Extract text from events
        event_texts = [self._event_to_text(event) for event in events]

        # Score events
        scores = self.score_events_batch(event_texts, query)

        # Pair and sort
        ranked = list(zip(events, scores))
        ranked.sort(key=lambda x: x[1], reverse=True)

        if top_k is not None:
            return ranked[:top_k]
        return ranked

    def _event_to_text(self, event: Dict[str, Any]) -> str:
        """
        Convert event dict to text for embedding.

        Creates a textual representation combining:
        - Phase and agent information
        - Event type
        - Payload summary

        Args:
            event: Event dictionary

        Returns:
            Textual representation of event
        """
        parts = []

        # Agent and phase
        agent = event.get("agent_id", "system")
        phase = event.get("phase", "N/A")
        parts.append(f"[{phase}] {agent}")

        # Event type
        event_type = event.get("event_type", "unknown")
        parts.append(event_type)

        # Payload summary
        payload = event.get("payload", {})
        if payload:
            payload_text = " ".join(f"{k}: {v}" for k, v in payload.items())
            parts.append(payload_text)

        return " ".join(parts)

    def _keyword_score(self, text1: str, text2: str) -> float:
        """
        Fallback keyword-based relevance scoring.

        Computes Jaccard similarity between word sets:
            Jaccard(A, B) = |A ∩ B| / |A ∪ B|

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score (0.0 - 1.0)

        Example:
            >>> relevance._keyword_score("hello world", "hello there")
            0.333...  # 1 word in common / 3 unique words
        """
        # Tokenize (simple lowercase split)
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())

        # Jaccard similarity
        intersection = len(words1 & words2)
        union = len(words1 | words2)

        if union == 0:
            return 0.0
        return intersection / union

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get model information.

        Returns:
            Dictionary with:
            - model_name: Model identifier
            - available: Whether model is loaded
            - gpu_enabled: Whether GPU acceleration is enabled
            - fallback_mode: Whether using keyword fallback

        Example:
            >>> relevance = EmbeddingRelevance()
            >>> info = relevance.get_model_info()
            >>> print(info["model_name"])
            'all-MiniLM-L6-v2'
        """
        return {
            "model_name": self.model_name,
            "available": self._available,
            "gpu_enabled": self.use_gpu,
            "fallback_mode": not self._available,
        }
