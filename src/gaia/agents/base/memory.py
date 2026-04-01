# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
MemoryMixin: Persistent memory for any GAIA agent (v2).

Hooks into the Agent lifecycle at 3 points:
1. System prompt injection (get_memory_system_prompt)
2. Tool execution wrapper (_execute_tool) — auto-logs calls + learns from errors
3. Post-query storage (_after_process_query) — stores conversations + LLM extraction

Provides 5 LLM-facing tools: remember, recall, update_memory, forget, search_past_conversations.
Valid categories: fact, preference, error, skill, note, reminder.

v2 additions:
- Embedding pipeline (Lemonade nomic-embed-text-v2-moe-GGUF, 768-dim)
- FAISS IndexFlatIP for cosine similarity search
- Hybrid search: vector + BM25 + RRF fusion + cross-encoder reranking
- Complexity-aware recall depth (3/5/10 top_k)
- Mem0-style LLM extraction (ADD/UPDATE/DELETE/NOOP)
- Conversation consolidation (old sessions → knowledge)
- Background memory reconciliation (Hindsight-inspired)

Usage:
    class MyAgent(MemoryMixin, Agent):   # MemoryMixin MUST come before Agent
        def __init__(self, **kwargs):
            self.init_memory()          # Before super().__init__()
            super().__init__(**kwargs)

        def _register_tools(self):
            super()._register_tools()
            self.register_memory_tools()

Spec: docs/spec/agent-memory-architecture.md
"""

import concurrent.futures
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import numpy as np

from gaia.agents.base.memory_store import (
    MAX_CONTENT_LENGTH,
    VALID_CATEGORIES,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

#: Embedding model served by Lemonade — 768-dim, MOE architecture.
EMBEDDING_MODEL = "nomic-embed-text-v2-moe-GGUF"

#: Embedding dimensionality for nomic-embed-text-v2-moe.
EMBEDDING_DIM = 768

#: Cross-encoder model for reranking (~22 MB, runs on CPU).
CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

#: RRF fusion weights: 60% vector, 40% BM25.
RRF_WEIGHT_VECTOR = 0.6
RRF_WEIGHT_BM25 = 0.4

#: RRF smoothing constant (standard value from the original RRF paper).
RRF_K = 60

#: Cosine similarity threshold for reconciliation pair detection.
RECONCILE_SIMILARITY_THRESHOLD = 0.85

#: Minimum user input length (words) to trigger LLM extraction.
MIN_EXTRACTION_WORDS = 20

#: LLM extraction timeout in seconds.
EXTRACTION_TIMEOUT_S = 3

#: Consolidation age threshold in days.
CONSOLIDATION_AGE_DAYS = 14

#: Minimum turns for a session to be eligible for consolidation.
CONSOLIDATION_MIN_TURNS = 5


# ============================================================================
# Extraction Prompt (Mem0-inspired)
# ============================================================================

_EXTRACTION_PROMPT = """\
You are a memory manager. Given a conversation turn and the user's existing memory,
decide what knowledge operations to perform. Return a JSON array only.

Each item must have an "op" field:
- "add": New knowledge not already in memory
  Required: {op, category, content, entity?, domain?, confidence: 0.4}
- "update": Modify an existing memory item (correction, enrichment, or supersession)
  Required: {op, knowledge_id, content, entity?, domain?}
- "delete": Remove a memory item contradicted or invalidated by new information
  Required: {op, knowledge_id, reason}
- "noop": Information already captured accurately. Do not include in output.

Categories: fact, preference, error, skill, note, reminder
Entity format: type:name (person:sarah_chen, app:vscode, project:gaia)
Domain examples: journal, meeting, meeting:standup, research, deployment

Rules:
- Only extract information useful in FUTURE conversations
- Skip greetings, task confirmations, and ephemeral details
- Prefer "update" over "add" + "delete" when a fact has changed
- Use "delete" only when information is explicitly contradicted
- If nothing worth doing, return []

Existing memory:
{existing_items_json}

Conversation:
User: {user_input}
Assistant: {assistant_response}"""


# ============================================================================
# Consolidation Prompt
# ============================================================================

_CONSOLIDATION_PROMPT = """\
Summarize this conversation session in 2-3 sentences. Extract any durable knowledge worth preserving.
Return JSON only: {{"summary": "...", "knowledge": [{{"category": "...", "content": "...", "entity": "...or null"}}]}}
Only extract information useful in future conversations. If nothing worth extracting, return knowledge as [].

Session ({n_turns} turns, {first_ts} to {last_ts}):
{turns_text}"""


# ============================================================================
# Reconciliation Prompt
# ============================================================================

_RECONCILIATION_PROMPT = """\
Given these two memory items from the same user, classify their relationship.
Return JSON: {{"relationship": "reinforce|contradict|weaken|neutral", "action": "description"}}

Item A (stored {date_a}): {content_a}
Item B (stored {date_b}): {content_b}"""


# Memory tools that should NOT be logged to tool_history
_MEMORY_TOOLS = frozenset(
    {"remember", "recall", "update_memory", "forget", "search_past_conversations"}
)

# Module-level cache for the cross-encoder model (loaded once per process).
# _CROSS_ENCODER_UNAVAILABLE is a sentinel: once set, we stop retrying.
_cross_encoder_model = None
_CROSS_ENCODER_UNAVAILABLE = False


def _get_cross_encoder():
    """Lazy-load the cross-encoder reranking model. Cached at module level.

    Returns None (without retrying) if sentence-transformers is not installed
    or the model failed to load on a previous attempt.
    """
    global _cross_encoder_model, _CROSS_ENCODER_UNAVAILABLE
    if _CROSS_ENCODER_UNAVAILABLE:
        return None
    if _cross_encoder_model is not None:
        return _cross_encoder_model
    try:
        from sentence_transformers import CrossEncoder

        _cross_encoder_model = CrossEncoder(CROSS_ENCODER_MODEL)
        logger.info("[MemoryMixin] cross-encoder loaded: %s", CROSS_ENCODER_MODEL)
        return _cross_encoder_model
    except ImportError:
        logger.warning(
            "[MemoryMixin] sentence-transformers not installed; "
            "cross-encoder reranking disabled"
        )
        _CROSS_ENCODER_UNAVAILABLE = True
        return None
    except Exception as e:
        logger.warning("[MemoryMixin] cross-encoder load failed: %s", e)
        _CROSS_ENCODER_UNAVAILABLE = True
        return None


def _embedding_to_blob(vec: np.ndarray) -> bytes:
    """Convert a float32 numpy vector to a raw bytes BLOB for SQLite storage."""
    return vec.astype(np.float32).tobytes()


def _blob_to_embedding(blob: bytes) -> np.ndarray:
    """Convert a raw bytes BLOB back to a float32 numpy vector."""
    return np.frombuffer(blob, dtype=np.float32).copy()


class MemoryMixin:
    """
    Mixin that gives any Agent persistent memory across sessions (v2).

    Provides:
    - Working context via system prompt (preferences, facts, errors, upcoming)
    - Auto tool call logging with error learning
    - Conversation persistence with Mem0-style LLM extraction
    - Hybrid search: FAISS vector + BM25 FTS5 + RRF fusion + cross-encoder reranking
    - Conversation consolidation for old sessions
    - Background memory reconciliation for conflict detection
    - 5 CRUD tools for the LLM (remember, recall, update_memory, forget, search_past_conversations)
    """

    def init_memory(
        self, db_path: Optional[Path] = None, context: str = "global"
    ) -> None:
        """Initialize the memory subsystem (v2 startup sequence).

        Creates/gets a MemoryStore instance, validates Lemonade embedding
        connectivity, backfills embeddings, builds FAISS index, applies
        confidence decay, runs reconciliation, and consolidates old sessions.

        Call this BEFORE super().__init__() in your agent's __init__.

        Args:
            db_path: Optional path for the DB file. Default: ~/.gaia/memory.db
            context: Active context scope (e.g., 'work', 'personal', 'global').

        Raises:
            RuntimeError: If Lemonade embedding service is unreachable.
        """
        from gaia.agents.base.memory_store import MemoryStore

        # Step 1: Open/create DB, apply schema migrations
        self._memory_store = MemoryStore(db_path)

        self._memory_context = context
        self._auto_extract_enabled = True
        self._original_user_input: Optional[str] = None

        # Embedding infrastructure (lazy-init via _get_embedder)
        self._embedder = None

        # FAISS index state
        self._faiss_index = None
        self._faiss_id_map: List[str] = []  # faiss_position -> knowledge_id

        # Step 2: Validate Lemonade embedding service connectivity [HARD REQUIREMENT]
        try:
            embedder = self._get_embedder()
            # Validate connectivity with a small test embedding
            test_vec = self._embed_text("connectivity test")
            if test_vec.shape[0] != EMBEDDING_DIM:
                raise RuntimeError(
                    f"Embedding dimension mismatch: expected {EMBEDDING_DIM}, "
                    f"got {test_vec.shape[0]}"
                )
            logger.info(
                "[MemoryMixin] Lemonade embedding service validated (%d-dim)",
                EMBEDDING_DIM,
            )
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(
                f"Lemonade embedding service required for memory system: {e}"
            ) from e

        # Step 3: Backfill embeddings for items missing them
        backfilled = self._backfill_embeddings(limit=100)
        if backfilled > 0:
            logger.info("[MemoryMixin] backfilled %d embeddings", backfilled)

        # Step 4: Rebuild FAISS index from stored embeddings
        self._rebuild_faiss_index()

        # Step 5: apply_confidence_decay()
        self._memory_store.apply_confidence_decay()

        # Step 6: reconcile_memory() (max 20 pairs)
        try:
            recon = self.reconcile_memory(max_pairs=20)
            if recon.get("pairs_checked", 0) > 0:
                logger.info("[MemoryMixin] reconciliation: %s", recon)
        except Exception as e:
            logger.warning("[MemoryMixin] reconciliation failed: %s", e)

        # Step 7: consolidate_old_sessions() (max 5 sessions)
        try:
            consol = self.consolidate_old_sessions(max_sessions=5)
            if consol.get("consolidated", 0) > 0:
                logger.info("[MemoryMixin] consolidation: %s", consol)
        except Exception as e:
            logger.warning("[MemoryMixin] consolidation failed: %s", e)

        # Step 8: prune() (90-day hard delete)
        self._memory_store.prune()

        # Step 9: Generate session UUID
        self._memory_session_id = str(uuid4())

        logger.info(
            "[MemoryMixin] v2 initialized, session_id=%s context=%s",
            self._memory_session_id,
            context,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def memory_store(self):
        """Access the MemoryStore instance."""
        if not hasattr(self, "_memory_store"):
            raise RuntimeError("MemoryMixin not initialized. Call init_memory() first.")
        return self._memory_store

    @property
    def memory_session_id(self) -> str:
        """Current memory session ID.

        Raises RuntimeError if accessed before init_memory() is called.
        """
        if not hasattr(self, "_memory_session_id"):
            raise RuntimeError("MemoryMixin not initialized. Call init_memory() first.")
        return self._memory_session_id

    @property
    def memory_context(self) -> str:
        """Current active context (e.g., 'work', 'personal', 'global')."""
        return getattr(self, "_memory_context", "global")

    def set_memory_context(self, context: str) -> None:
        """Switch active context. Affects system prompt filtering and default store context.

        Empty or whitespace-only context defaults to "global".
        Rebuilds the cached system prompt immediately.
        """
        context = (context or "").strip() or "global"
        old = self._memory_context
        self._memory_context = context
        logger.info("[MemoryMixin] context switched %s → %s", old, context)
        if hasattr(self, "rebuild_system_prompt"):
            self.rebuild_system_prompt()

    # ==================================================================
    # Embedding Pipeline
    # ==================================================================

    def _get_embedder(self) -> Any:
        """Lazy-init cached LemonadeProvider for embeddings.

        NOT optional — raises RuntimeError if unavailable.

        Returns:
            LemonadeProvider instance configured for embedding.
        """
        if getattr(self, "_embedder", None) is not None:
            return self._embedder

        try:
            from gaia.llm.providers.lemonade import LemonadeProvider

            self._embedder = LemonadeProvider(model=EMBEDDING_MODEL)
            logger.debug("[MemoryMixin] LemonadeProvider initialized for embeddings")
            return self._embedder
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize Lemonade embedding provider: {e}"
            ) from e

    def _embed_text(self, text: str) -> np.ndarray:
        """Embed text via Lemonade (nomic-embed-text-v2-moe-GGUF, 768-dim).

        Required, not optional. Raises RuntimeError if embedding fails.

        Args:
            text: Text to embed.

        Returns:
            L2-normalized float32 numpy array of shape (768,).
        """
        embedder = self._get_embedder()
        try:
            # LemonadeProvider.embed() returns list[list[float]]
            results = embedder.embed([text], model=EMBEDDING_MODEL)
            vec = np.array(results[0], dtype=np.float32)

            # L2-normalize for cosine similarity via IndexFlatIP
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm

            return vec
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {e}") from e

    def _backfill_embeddings(self, limit: int = 100) -> int:
        """Embed items missing embeddings. Called on startup.

        Returns count of items backfilled.
        """
        store = self._memory_store
        items = store.get_items_without_embeddings(limit=limit)
        count = 0

        for item in items:
            try:
                vec = self._embed_text(item["content"])
                store.store_embedding(item["id"], _embedding_to_blob(vec))
                count += 1
            except Exception as e:
                logger.warning(
                    "[MemoryMixin] backfill embedding failed for %s: %s",
                    item["id"],
                    e,
                )

        return count

    # ==================================================================
    # FAISS Index Lifecycle
    # ==================================================================

    def _rebuild_faiss_index(self) -> None:
        """Build FAISS IndexFlatIP from stored embedding BLOBs.

        IndexFlatIP on L2-normalized vectors = cosine similarity.
        """
        try:
            import faiss
        except ImportError:
            logger.warning(
                "[MemoryMixin] faiss-cpu not installed; vector search disabled"
            )
            self._faiss_index = None
            self._faiss_id_map = []
            return

        store = self._memory_store
        # Get all active knowledge items that have embeddings
        items = store.get_items_with_embeddings(include_sensitive=True)

        index = faiss.IndexFlatIP(EMBEDDING_DIM)
        id_map = []

        for item in items:
            try:
                vec = _blob_to_embedding(item["embedding"])
                if vec.shape[0] != EMBEDDING_DIM:
                    logger.debug(
                        "[MemoryMixin] skipping embedding for %s: wrong dim %d",
                        item["id"],
                        vec.shape[0],
                    )
                    continue
                # Ensure L2 normalization
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                index.add(vec.reshape(1, -1))
                id_map.append(item["id"])
            except Exception as e:
                logger.debug(
                    "[MemoryMixin] skipping bad embedding for %s: %s",
                    item["id"],
                    e,
                )

        self._faiss_index = index
        self._faiss_id_map = id_map
        logger.info("[MemoryMixin] FAISS index rebuilt: %d vectors", index.ntotal)

    def _faiss_add(self, knowledge_id: str, vec: np.ndarray) -> None:
        """Add a single vector to the FAISS index (incremental update on store).

        Skips if the knowledge_id already exists (dedup safe).
        """
        if self._faiss_index is None:
            return
        try:
            # Avoid duplicate entries (e.g., when store() deduped to existing ID)
            if knowledge_id in self._faiss_id_map:
                return
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            self._faiss_index.add(vec.reshape(1, -1))
            self._faiss_id_map.append(knowledge_id)
        except Exception as e:
            logger.debug("[MemoryMixin] FAISS add failed: %s", e)

    def _faiss_remove(self, knowledge_id: str) -> None:
        """Remove a vector from FAISS index by knowledge_id.

        FAISS IndexFlatIP doesn't support direct removal, so we rebuild
        the index without the removed item. For small indexes (<10k) this
        is fast enough (<100ms).
        """
        if self._faiss_index is None or knowledge_id not in self._faiss_id_map:
            return
        try:
            idx = self._faiss_id_map.index(knowledge_id)

            import faiss

            # Reconstruct all vectors except the removed one
            n = self._faiss_index.ntotal
            if n <= 1:
                self._faiss_index = faiss.IndexFlatIP(EMBEDDING_DIM)
                self._faiss_id_map = []
                return

            all_vecs = np.zeros((n, EMBEDDING_DIM), dtype=np.float32)
            for i in range(n):
                all_vecs[i] = self._faiss_index.reconstruct(i)

            # Remove the target vector
            keep_vecs = np.delete(all_vecs, idx, axis=0)
            keep_ids = self._faiss_id_map[:idx] + self._faiss_id_map[idx + 1 :]

            new_index = faiss.IndexFlatIP(EMBEDDING_DIM)
            new_index.add(keep_vecs)
            self._faiss_index = new_index
            self._faiss_id_map = keep_ids
        except Exception as e:
            logger.debug("[MemoryMixin] FAISS remove failed, rebuilding: %s", e)
            self._rebuild_faiss_index()

    def _faiss_search(self, query_vec: np.ndarray, top_k: int) -> List[tuple]:
        """Search FAISS index for top_k nearest neighbors.

        Args:
            query_vec: L2-normalized query vector.
            top_k: Number of results to return.

        Returns:
            List of (knowledge_id, score) tuples, sorted by score descending.
        """
        if self._faiss_index is None or self._faiss_index.ntotal == 0:
            return []

        try:
            # Clamp top_k to index size
            k = min(top_k, self._faiss_index.ntotal)
            query = query_vec.reshape(1, -1).astype(np.float32)
            scores, indices = self._faiss_index.search(query, k)

            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx >= 0 and idx < len(self._faiss_id_map):
                    results.append((self._faiss_id_map[idx], float(score)))
            return results
        except Exception as e:
            logger.debug("[MemoryMixin] FAISS search failed: %s", e)
            return []

    # ==================================================================
    # Complexity-Aware Recall Depth
    # ==================================================================

    def _classify_query_complexity(self, query: str) -> int:
        """Returns adaptive top_k: 3 (simple), 5 (medium), 10 (complex).

        Simple: < 8 words, single entity.
        Medium: 8-20 words or how/why/explain.
        Complex: > 20 words or compare/across/all/history/everything.
        """
        words = query.split()
        complex_signals = {
            "compare",
            "across",
            "all",
            "history",
            "everything",
            "between",
            "throughout",
        }
        medium_signals = {
            "how",
            "why",
            "explain",
            "describe",
            "summarize",
        }

        word_set = {w.lower() for w in words}

        if len(words) > 20 or complex_signals & word_set:
            return 10
        # "what happened" as a two-word phrase trigger
        has_what_happened = "what" in word_set and "happened" in word_set
        if len(words) > 8 or medium_signals & word_set or has_what_happened:
            return 5
        return 3

    # ==================================================================
    # Hybrid Search Orchestration
    # ==================================================================

    def _hybrid_search(
        self,
        query: str,
        category: Optional[str] = None,
        context: Optional[str] = None,
        entity: Optional[str] = None,
        include_sensitive: bool = False,
        top_k: int = 5,
        time_from: Optional[str] = None,
        time_to: Optional[str] = None,
    ) -> List[Dict]:
        """Full hybrid search: vector + BM25 + RRF + cross-encoder reranking.

        1. Embed the query via _embed_text()
        2. FAISS cosine search: top_k × 4 candidates
        3. FTS5 BM25 via self._memory_store.search(): top_k × 4 candidates
        4. Deduplicate by ID, apply RRF fusion
        5. Cross-encoder reranking via ms-marco-MiniLM-L-6-v2
        6. Return final top_k
        7. Bump confidence + use_count

        Args:
            query: Search query text.
            category: Optional category filter.
            context: Optional context filter.
            entity: Optional entity filter.
            include_sensitive: Include sensitive items.
            top_k: Final number of results.
            time_from: ISO 8601 lower bound on created_at.
            time_to: ISO 8601 upper bound on created_at.

        Returns:
            List of knowledge dicts, ranked by relevance.
        """
        oversample = top_k * 4
        store = self._memory_store

        # Step 1: Embed the query (HARD REQUIREMENT — no BM25-only fallback)
        query_vec = self._embed_text(query)

        # Step 2: FAISS cosine search → get IDs, then batch-resolve from store
        # Use get_items_with_embeddings() with filters to pre-load a candidate
        # pool, then rank by FAISS similarity.  This avoids N individual DB
        # queries and handles filtering at the SQL level.
        vector_results = []
        faiss_hits = self._faiss_search(query_vec, oversample)
        if faiss_hits:
            faiss_hit_ids = {kid for kid, _ in faiss_hits}
            # Fetch candidate items from store with filters already applied.
            # We over-fetch (top_k=oversample*2) so filtering by the FAISS hit
            # set still yields enough items.
            candidate_pool = store.get_items_with_embeddings(
                category=category,
                context=context,
                entity=entity,
                include_sensitive=include_sensitive,
                top_k=max(oversample * 2, 200),
                time_from=time_from,
                time_to=time_to,
            )
            pool_by_id = {item["id"]: item for item in candidate_pool}

            # Preserve FAISS ranking order, only keep items that pass filters
            for kid, score in faiss_hits:
                item = pool_by_id.get(kid)
                if item is not None:
                    vector_results.append(item)

        # Step 3: FTS5 BM25 search
        bm25_results = store.search(
            query=query,
            category=category,
            context=context,
            entity=entity,
            include_sensitive=include_sensitive,
            top_k=oversample,
        )
        # Apply time filters on BM25 results
        if time_from or time_to:
            filtered = []
            for item in bm25_results:
                created = item.get("created_at", "")
                if time_from and created < time_from:
                    continue
                if time_to and created > time_to:
                    continue
                filtered.append(item)
            bm25_results = filtered

        # Filter out superseded items from BM25
        bm25_results = [r for r in bm25_results if not r.get("superseded_by")]

        # Step 4: Deduplicate by ID, apply RRF fusion
        # Assign ranks (0-based) within each result set
        vector_rank = {}
        for rank, item in enumerate(vector_results):
            vector_rank[item["id"]] = rank

        bm25_rank = {}
        for rank, item in enumerate(bm25_results):
            bm25_rank[item["id"]] = rank

        # Merge all unique items
        all_items: Dict[str, Dict] = {}
        for item in vector_results + bm25_results:
            if item["id"] not in all_items:
                all_items[item["id"]] = item

        if not all_items:
            return []

        # Compute RRF scores
        # Items missing from one list get a high rank (len of that list)
        max_vector_rank = len(vector_results)
        max_bm25_rank = len(bm25_results)

        rrf_scores = {}
        for kid in all_items:
            v_rank = vector_rank.get(kid, max_vector_rank)
            b_rank = bm25_rank.get(kid, max_bm25_rank)
            rrf_scores[kid] = RRF_WEIGHT_VECTOR / (RRF_K + v_rank) + RRF_WEIGHT_BM25 / (
                RRF_K + b_rank
            )

        # Sort by RRF score descending, take top_k × 2 for reranking
        sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)
        rerank_candidates = sorted_ids[: top_k * 2]

        # Step 5: Cross-encoder reranking
        cross_enc = _get_cross_encoder()
        if cross_enc is not None and rerank_candidates:
            try:
                pairs = [
                    (query, all_items[kid]["content"]) for kid in rerank_candidates
                ]
                ce_scores = cross_enc.predict(pairs)
                # Re-sort by cross-encoder score
                scored = list(zip(rerank_candidates, ce_scores))
                scored.sort(key=lambda x: x[1], reverse=True)
                rerank_candidates = [kid for kid, _ in scored]
            except Exception as e:
                logger.debug("[MemoryMixin] cross-encoder reranking failed: %s", e)

        # Step 6: Return final top_k
        final_ids = rerank_candidates[:top_k]
        results = [all_items[kid] for kid in final_ids]

        # Step 7: Bump confidence + use_count on recalled items.
        # Only bump items that were NOT already bumped by store.search()
        # (BM25 path).  store.search() internally bumps confidence for its
        # results, so we only bump vector-only items to avoid double-counting.
        if results:
            bm25_ids = set(bm25_rank.keys())
            for item in results:
                if item["id"] not in bm25_ids:
                    try:
                        store.update_confidence(item["id"], 0.02)
                        item["confidence"] = min(item["confidence"] + 0.02, 1.0)
                    except Exception:
                        pass

        return results

    # ==================================================================
    # Mem0-Style LLM Extraction
    # ==================================================================

    def _extract_via_llm(
        self,
        user_input: str,
        assistant_response: str,
        existing_items: List[Dict],
    ) -> List[Dict]:
        """Mem0-style extraction: conversation + existing memory → operations.

        Single LLM call returns JSON array of operations: ADD/UPDATE/DELETE/NOOP.
        Timeout: 3s.

        Args:
            user_input: The user's message.
            assistant_response: The assistant's response.
            existing_items: Top-10 relevant existing knowledge items.

        Returns:
            List of operation dicts with 'op' field.
        """
        # Format existing items for the prompt
        existing_json = json.dumps(
            [
                {
                    "id": item["id"],
                    "category": item["category"],
                    "content": item["content"],
                    "entity": item.get("entity"),
                    "domain": item.get("domain"),
                    "created_at": item.get("created_at", ""),
                }
                for item in existing_items
            ],
            indent=2,
        )

        prompt = _EXTRACTION_PROMPT.format(
            existing_items_json=existing_json,
            user_input=user_input[:2000],
            assistant_response=assistant_response[:2000],
        )

        try:
            # Use the agent's AgentSDK for LLM calls
            if not hasattr(self, "chat"):
                logger.warning("[MemoryMixin] no chat SDK available for extraction")
                return []

            # Enforce extraction timeout (spec: 3s)
            def _call_llm():
                return self.chat.send_messages(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt="You are a memory extraction engine. Return valid JSON only.",
                    temperature=0.1,
                    max_tokens=1024,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call_llm)
                try:
                    response = future.result(timeout=EXTRACTION_TIMEOUT_S)
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        "[MemoryMixin] extraction LLM call timed out (%ds)",
                        EXTRACTION_TIMEOUT_S,
                    )
                    return []

            raw_text = response.text if hasattr(response, "text") else str(response)

            # Strip thinking tags if present (Qwen3.5 models)
            raw_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL)

            # Extract JSON array from response
            raw_text = raw_text.strip()
            # Handle markdown code blocks
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)

            operations = json.loads(raw_text)

            if not isinstance(operations, list):
                logger.warning(
                    "[MemoryMixin] extraction returned non-list: %s",
                    type(operations).__name__,
                )
                return []

            # Validate each operation has required fields
            valid_ops = []
            for op in operations:
                if not isinstance(op, dict) or "op" not in op:
                    continue
                op_type = op["op"]
                if op_type == "add" and "content" in op and "category" in op:
                    if op["category"] in VALID_CATEGORIES:
                        valid_ops.append(op)
                elif op_type == "update" and "knowledge_id" in op and "content" in op:
                    valid_ops.append(op)
                elif op_type == "delete" and "knowledge_id" in op:
                    valid_ops.append(op)
                # noop is excluded from output per spec

            return valid_ops

        except json.JSONDecodeError as e:
            logger.warning("[MemoryMixin] extraction returned invalid JSON: %s", e)
            return []
        except Exception as e:
            logger.warning("[MemoryMixin] LLM extraction failed: %s", e)
            return []

    def _execute_extraction_operations(
        self,
        operations: List[Dict],
        existing_items: List[Dict],
    ) -> None:
        """Execute the operations returned by _extract_via_llm().

        ADD → store() + embed
        UPDATE → store new + supersede old
        DELETE → delete()
        """
        store = self._memory_store

        for op in operations:
            try:
                op_type = op["op"]

                if op_type == "add":
                    new_id = store.store(
                        category=op["category"],
                        content=op["content"],
                        confidence=op.get("confidence", 0.4),
                        entity=op.get("entity"),
                        domain=op.get("domain"),
                        source="llm_extract",
                        context=self._memory_context,
                    )
                    # Embed the new item
                    try:
                        vec = self._embed_text(op["content"])
                        store.store_embedding(new_id, _embedding_to_blob(vec))
                        self._faiss_add(new_id, vec)
                    except Exception as e:
                        logger.debug(
                            "[MemoryMixin] embedding new extraction failed: %s", e
                        )

                elif op_type == "update":
                    old_id = op["knowledge_id"]
                    existing_item = next(
                        (e for e in existing_items if e["id"] == old_id), {}
                    )
                    # Store new version
                    new_id = store.store(
                        category=op.get(
                            "category", existing_item.get("category", "fact")
                        ),
                        content=op["content"],
                        confidence=max(existing_item.get("confidence", 0.4), 0.4),
                        entity=op.get("entity"),
                        domain=op.get("domain"),
                        source="llm_extract",
                        context=self._memory_context,
                    )
                    # Mark old as superseded and remove from FAISS
                    store.update(old_id, superseded_by=new_id)
                    self._faiss_remove(old_id)
                    # Embed the new item
                    try:
                        vec = self._embed_text(op["content"])
                        store.store_embedding(new_id, _embedding_to_blob(vec))
                        self._faiss_add(new_id, vec)
                    except Exception as e:
                        logger.debug(
                            "[MemoryMixin] embedding updated extraction failed: %s",
                            e,
                        )

                elif op_type == "delete":
                    kid = op["knowledge_id"]
                    store.delete(kid)
                    self._faiss_remove(kid)

            except Exception as e:
                logger.warning(
                    "[MemoryMixin] extraction operation failed: op=%s err=%s",
                    op.get("op"),
                    e,
                )

    # ==================================================================
    # Conversation Consolidation
    # ==================================================================

    def consolidate_old_sessions(self, max_sessions: int = 5) -> Dict:
        """Distill old sessions (>14 days, >=5 turns) into knowledge items.

        Uses LLM to summarize each session and extract durable knowledge.

        Args:
            max_sessions: Maximum number of sessions to consolidate per run.

        Returns:
            Dict with {consolidated: int, extracted_items: int}.
        """
        store = self._memory_store
        result = {"consolidated": 0, "extracted_items": 0}

        try:
            session_ids = store.get_unconsolidated_sessions(
                older_than_days=CONSOLIDATION_AGE_DAYS,
                min_turns=CONSOLIDATION_MIN_TURNS,
                limit=max_sessions,
            )
        except Exception as e:
            logger.warning("[MemoryMixin] failed to get unconsolidated sessions: %s", e)
            return result

        if not session_ids:
            return result

        for session_id in session_ids:
            try:
                # Fetch turns for this session (up to 20, oldest first)
                turns = store.get_history(session_id, limit=20)
                if not turns:
                    continue

                # Build turns text
                turns_text_parts = []
                turn_ids = []
                for turn in turns:
                    role = turn.get("role", "user")
                    content = turn.get("content", "")[:500]
                    turns_text_parts.append(f"{role}: {content}")
                    if "id" in turn:
                        turn_ids.append(turn["id"])

                turns_text = "\n".join(turns_text_parts)

                first_ts = turns[0].get("timestamp", "unknown")
                last_ts = turns[-1].get("timestamp", "unknown")

                prompt = _CONSOLIDATION_PROMPT.format(
                    n_turns=len(turns),
                    first_ts=first_ts,
                    last_ts=last_ts,
                    turns_text=turns_text,
                )

                # LLM call
                if not hasattr(self, "chat"):
                    break

                response = self.chat.send_messages(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt="You are a conversation summarizer. Return valid JSON only.",
                    temperature=0.1,
                    max_tokens=1024,
                )

                raw_text = response.text if hasattr(response, "text") else str(response)
                raw_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL)
                raw_text = raw_text.strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)

                data = json.loads(raw_text)

                # Store summary as a note
                summary = data.get("summary", "")
                if summary:
                    summary_id = store.store(
                        category="note",
                        content=summary,
                        source="consolidation",
                        domain=f"session:{session_id[:8]}",
                        confidence=0.5,
                        context=self._memory_context,
                    )
                    # Embed the summary
                    try:
                        vec = self._embed_text(summary)
                        store.store_embedding(summary_id, _embedding_to_blob(vec))
                        self._faiss_add(summary_id, vec)
                    except Exception:
                        pass

                # Store extracted knowledge items
                knowledge_items = data.get("knowledge", [])
                for ki in knowledge_items:
                    if isinstance(ki, dict) and "content" in ki and "category" in ki:
                        if ki["category"] in VALID_CATEGORIES:
                            try:
                                kid = store.store(
                                    category=ki["category"],
                                    content=ki["content"],
                                    source="consolidation",
                                    entity=ki.get("entity"),
                                    confidence=0.5,
                                    context=self._memory_context,
                                )
                                # Embed
                                try:
                                    vec = self._embed_text(ki["content"])
                                    store.store_embedding(kid, _embedding_to_blob(vec))
                                    self._faiss_add(kid, vec)
                                except Exception:
                                    pass
                                result["extracted_items"] += 1
                            except Exception as e:
                                logger.debug(
                                    "[MemoryMixin] consolidation knowledge store failed: %s",
                                    e,
                                )

                # Mark turns as consolidated
                if turn_ids:
                    store.mark_turns_consolidated(turn_ids)

                result["consolidated"] += 1

            except json.JSONDecodeError as e:
                logger.warning(
                    "[MemoryMixin] consolidation JSON parse failed for %s: %s",
                    session_id[:8],
                    e,
                )
            except Exception as e:
                logger.warning(
                    "[MemoryMixin] consolidation failed for %s: %s",
                    session_id[:8],
                    e,
                )

        return result

    # ==================================================================
    # Background Memory Reconciliation (Hindsight-Inspired)
    # ==================================================================

    def reconcile_memory(self, max_pairs: int = 20) -> Dict:
        """Pairwise similarity check on active items for conflict detection.

        Finds pairs with >0.85 cosine similarity, then uses LLM to classify:
        reinforce/contradict/weaken/neutral.

        Args:
            max_pairs: Maximum number of pair classifications per run.

        Returns:
            Dict with {pairs_checked, reinforced, contradicted, weakened, neutral}.
        """
        result = {
            "pairs_checked": 0,
            "reinforced": 0,
            "contradicted": 0,
            "weakened": 0,
            "neutral": 0,
        }

        if self._faiss_index is None or self._faiss_index.ntotal < 2:
            return result

        store = self._memory_store

        # Get all active items with embeddings for pairwise comparison
        items = store.get_items_for_reconciliation()
        if len(items) < 2:
            return result

        # Build a mapping for quick lookup
        item_map = {}
        vectors = []
        ids = []
        for item in items:
            try:
                vec = _blob_to_embedding(item["embedding"])
                if vec.shape[0] != EMBEDDING_DIM:
                    continue
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                item_map[item["id"]] = item
                vectors.append(vec)
                ids.append(item["id"])
            except Exception:
                continue

        if len(vectors) < 2:
            return result

        # Stack all vectors and compute pairwise similarities
        mat = np.stack(vectors)
        # For efficiency with large sets, check each item against its top-K neighbors
        try:
            import faiss

            temp_index = faiss.IndexFlatIP(EMBEDDING_DIM)
            temp_index.add(mat)

            # Search each item for its top-5 neighbors
            n_neighbors = min(5, len(vectors))
            scores_all, indices_all = temp_index.search(mat, n_neighbors)
        except Exception as e:
            logger.debug("[MemoryMixin] reconciliation FAISS search failed: %s", e)
            return result

        # Collect high-similarity pairs
        pairs = []
        seen_pairs = set()
        for i in range(len(vectors)):
            for j_pos in range(n_neighbors):
                j = int(indices_all[i][j_pos])
                if j == i:
                    continue
                sim = float(scores_all[i][j_pos])
                if sim >= RECONCILE_SIMILARITY_THRESHOLD:
                    pair_key = tuple(sorted((ids[i], ids[j])))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        pairs.append((ids[i], ids[j], sim))

        # Sort by similarity descending, take top max_pairs
        pairs.sort(key=lambda x: x[2], reverse=True)
        pairs = pairs[:max_pairs]

        if not pairs or not hasattr(self, "chat"):
            return result

        for id_a, id_b, sim in pairs:
            try:
                item_a = item_map.get(id_a)
                item_b = item_map.get(id_b)
                if not item_a or not item_b:
                    continue

                # Skip already reconciled pairs
                meta_a = item_a.get("metadata") or {}
                meta_b = item_b.get("metadata") or {}
                if isinstance(meta_a, str):
                    meta_a = json.loads(meta_a) if meta_a else {}
                if isinstance(meta_b, str):
                    meta_b = json.loads(meta_b) if meta_b else {}

                # Check if already reconciled with each other
                reconciled_a = meta_a.get("reconciled_with", [])
                reconciled_b = meta_b.get("reconciled_with", [])
                if id_b in reconciled_a or id_a in reconciled_b:
                    continue

                prompt = _RECONCILIATION_PROMPT.format(
                    date_a=item_a.get("created_at", "unknown")[:10],
                    content_a=item_a.get("content", "")[:500],
                    date_b=item_b.get("created_at", "unknown")[:10],
                    content_b=item_b.get("content", "")[:500],
                )

                response = self.chat.send_messages(
                    messages=[{"role": "user", "content": prompt}],
                    system_prompt="You are a memory reconciliation engine. Return valid JSON only.",
                    temperature=0.1,
                    max_tokens=256,
                )

                raw_text = response.text if hasattr(response, "text") else str(response)
                raw_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL)
                raw_text = raw_text.strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                    raw_text = re.sub(r"\s*```$", "", raw_text)

                classification = json.loads(raw_text)
                relationship = classification.get("relationship", "neutral")

                result["pairs_checked"] += 1

                if relationship == "reinforce":
                    # Boost confidence of both items by +0.05
                    store.update_confidence(id_a, 0.05)
                    store.update_confidence(id_b, 0.05)
                    result["reinforced"] += 1

                elif relationship == "contradict":
                    # Supersede the older item, boost newer confidence +0.1
                    a_date = item_a.get("created_at", "")
                    b_date = item_b.get("created_at", "")
                    if a_date <= b_date:
                        # A is older, B is newer
                        store.update(id_a, superseded_by=id_b)
                        store.update_confidence(id_b, 0.1)
                        self._faiss_remove(id_a)
                    else:
                        # B is older, A is newer
                        store.update(id_b, superseded_by=id_a)
                        store.update_confidence(id_a, 0.1)
                        self._faiss_remove(id_b)
                    result["contradicted"] += 1

                elif relationship == "weaken":
                    # Reduce confidence of the older item by 0.1
                    a_date = item_a.get("created_at", "")
                    b_date = item_b.get("created_at", "")
                    if a_date <= b_date:
                        store.update_confidence(id_a, -0.1)
                    else:
                        store.update_confidence(id_b, -0.1)
                    result["weakened"] += 1

                else:
                    result["neutral"] += 1

                # Mark both items as reconciled with each other
                # Merge with existing metadata rather than clobbering
                try:
                    merged_a = dict(meta_a) if isinstance(meta_a, dict) else {}
                    if not isinstance(reconciled_a, list):
                        reconciled_a = []
                    reconciled_a.append(id_b)
                    merged_a["reconciled_with"] = reconciled_a
                    store.update(id_a, metadata=merged_a)

                    merged_b = dict(meta_b) if isinstance(meta_b, dict) else {}
                    if not isinstance(reconciled_b, list):
                        reconciled_b = []
                    reconciled_b.append(id_a)
                    merged_b["reconciled_with"] = reconciled_b
                    store.update(id_b, metadata=merged_b)
                except Exception:
                    pass

            except json.JSONDecodeError:
                logger.debug(
                    "[MemoryMixin] reconciliation JSON parse failed for pair %s/%s",
                    id_a[:8],
                    id_b[:8],
                )
            except Exception as e:
                logger.debug(
                    "[MemoryMixin] reconciliation failed for pair %s/%s: %s",
                    id_a[:8],
                    id_b[:8],
                    e,
                )

        return result

    # ------------------------------------------------------------------
    # Hook 1: System Prompt Injection
    # ------------------------------------------------------------------

    def get_memory_system_prompt(self) -> str:
        """Build the STABLE memory section for the system prompt.

        Contains only content that rarely changes: preferences, facts, known errors.
        Time and upcoming items are intentionally excluded — they are injected
        per-turn via get_memory_dynamic_context() to keep this prompt frozen for
        LLM KV-cache reuse.
        """
        if not hasattr(self, "_memory_store"):
            return ""

        try:
            return self._build_stable_memory_prompt()
        except Exception as e:
            logger.warning("[MemoryMixin] failed to build stable memory prompt: %s", e)
            return ""

    def get_memory_dynamic_context(self) -> str:
        """Build the per-turn dynamic context string: current time + upcoming items.

        This is prepended to the user message each turn (not the system prompt),
        so the system prompt stays frozen for KV-cache reuse.
        Returns empty string if nothing time-sensitive is active.
        """
        if not hasattr(self, "_memory_store"):
            return ""

        try:
            return self._build_dynamic_memory_context()
        except Exception as e:
            logger.debug("[MemoryMixin] failed to build dynamic context: %s", e)
            return ""

    def _build_stable_memory_prompt(self) -> str:
        """Stable memory: preferences + facts + known errors. No timestamps."""
        ctx = self._memory_context
        sections = []

        # 1. Preferences (active context + global, exclude sensitive)
        prefs = self._get_context_items("preference", ctx, limit=10)
        if prefs:
            pref_lines = [f"  - {p['content']}" for p in prefs]
            sections.append("Preferences:\n" + "\n".join(pref_lines))

        # 2. Top facts (active context + global, by confidence)
        facts = self._get_context_items("fact", ctx, limit=5)
        if facts:
            fact_lines = [
                f"  - {f['content']} (confidence: {f['confidence']:.2f})" for f in facts
            ]
            sections.append("Known facts:\n" + "\n".join(fact_lines))

        # 3. Top skills
        skills = self._get_context_items("skill", ctx, limit=3)
        if skills:
            skill_lines = [
                f"  - {s['content']} (confidence: {s['confidence']:.2f})"
                for s in skills
            ]
            sections.append("Skills:\n" + "\n".join(skill_lines))

        # 4. Known error patterns
        errors = self._get_context_items("error", ctx, limit=5)
        if errors:
            error_lines = [f"  - {e['content']}" for e in errors]
            sections.append("Known errors to avoid:\n" + "\n".join(error_lines))

        if not sections:
            return ""

        result = "=== MEMORY ===\n" + "\n\n".join(sections)
        # Hard cap: prevent context overflow if many large items exist.
        # 4000 chars ≈ 1000 tokens — sufficient for preferences/facts without
        # crowding the actual conversation context.
        if len(result) > 4000:
            result = result[:4000] + "\n... (memory truncated)"
        return result

    def _build_dynamic_memory_context(self) -> str:
        """Dynamic per-turn context: current time + upcoming/overdue items."""
        store = self._memory_store
        ctx = self._memory_context
        lines = []

        # Current time
        now = datetime.now().astimezone()
        time_str = now.strftime("%Y-%m-%dT%H:%M:%S%z") + f" ({now.strftime('%A')})"
        lines.append(f"Current time: {time_str}")

        # Upcoming/overdue items
        upcoming = store.get_upcoming(within_days=7, context=ctx)
        if upcoming:
            up_lines = []
            for item in upcoming[:10]:
                due = item.get("due_at", "")[:10] if item.get("due_at") else "?"
                try:
                    due_dt = datetime.fromisoformat(item["due_at"])
                    if due_dt.tzinfo is None:
                        due_dt = due_dt.astimezone()
                    label = "OVERDUE" if due_dt < now else "DUE"
                    up_lines.append(f"  - [{label} {due}] {item['content']}")
                except (ValueError, KeyError, TypeError):
                    up_lines.append(f"  - [DUE {due}] {item['content']}")
            lines.append("Upcoming/overdue:\n" + "\n".join(up_lines))
            lines.append(
                "After mentioning a time-sensitive item, call update_memory "
                "to set reminded_at so you don't repeat yourself."
            )

        return "[GAIA Memory Context]\n" + "\n\n".join(lines)

    def _get_context_items(
        self, category: str, context: str, limit: int = 10
    ) -> List[Dict]:
        """Get non-sensitive knowledge items for active context + global.

        Uses a single DB query (get_by_category_contexts) instead of two
        sequential get_by_category() calls, halving the DB round-trips.
        """
        return self._memory_store.get_by_category_contexts(
            category, context, limit=limit
        )

    # ------------------------------------------------------------------
    # Hook 2: process_query Override (dynamic context injection)
    # ------------------------------------------------------------------

    def process_query(self, user_input, **kwargs):
        """Prepend per-turn dynamic context (time + upcoming) to the user message.

        The system prompt is left frozen so the LLM inference engine can reuse
        its KV cache across turns. Only the small dynamic section (current time,
        upcoming/overdue items) is injected per-turn by prepending it to the
        user message.
        """
        # Save original so _after_process_query stores the clean user text
        self._original_user_input = user_input

        # Prepend dynamic context to the user message
        dynamic = self.get_memory_dynamic_context()
        augmented = f"{dynamic}\n\n{user_input}" if dynamic else user_input

        return super().process_query(augmented, **kwargs)

    # ------------------------------------------------------------------
    # Hook 3: _execute_tool Override (auto-logging)
    # ------------------------------------------------------------------

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        """Override to auto-log every non-memory tool call.

        Memory tools are excluded to avoid noise and recursion.
        Failed tools auto-store errors as knowledge for future avoidance.
        """
        # Skip logging for memory tools
        if tool_name in _MEMORY_TOOLS:
            return super()._execute_tool(tool_name, tool_args)

        # Log non-memory tools
        start = time.time()
        error_msg = None
        result = None
        is_error = False

        try:
            result = super()._execute_tool(tool_name, tool_args)
            duration_ms = int((time.time() - start) * 1000)

            # Determine success from result dict
            is_error = isinstance(result, dict) and result.get("status") == "error"
            if is_error:
                error_msg = str(
                    result.get("error_brief") or result.get("error") or "Unknown error"
                )
        except Exception as exc:
            # Tool raised an exception — log it, then re-raise
            duration_ms = int((time.time() - start) * 1000)
            is_error = True
            error_msg = str(exc)
            result = {"status": "error", "error": error_msg}

            # Log to tool_history before re-raising
            try:
                if hasattr(self, "_memory_store"):
                    self._memory_store.log_tool_call(
                        session_id=self.memory_session_id,
                        tool_name=tool_name,
                        args=tool_args,
                        result_summary=f"EXCEPTION: {error_msg}"[:500],
                        success=False,
                        error=error_msg,
                        duration_ms=duration_ms,
                    )
                    self._auto_store_error(tool_name, error_msg)
            except Exception:
                pass
            raise

        # Truncate result summary
        result_str = str(result)
        result_summary = result_str[:500] if len(result_str) > 500 else result_str

        # Log to tool_history
        try:
            if hasattr(self, "_memory_store"):
                self._memory_store.log_tool_call(
                    session_id=self.memory_session_id,
                    tool_name=tool_name,
                    args=tool_args,
                    result_summary=result_summary,
                    success=not is_error,
                    error=error_msg,
                    duration_ms=duration_ms,
                )

                # Auto-store novel errors as knowledge
                if is_error and error_msg:
                    self._auto_store_error(tool_name, error_msg)
        except Exception as e:
            logger.debug("[MemoryMixin] tool logging failed: %s", e)

        return result

    def _auto_store_error(self, tool_name: str, error_msg: str) -> None:
        """Store a novel tool error as knowledge for future avoidance."""
        try:
            if not error_msg or not error_msg.strip():
                return
            error_content = f"{tool_name}: {error_msg}"
            kid = self._memory_store.store(
                category="error",
                content=error_content,
                source="error_auto",
                context=self._memory_context,
                confidence=0.5,
            )
            # Embed the error
            try:
                vec = self._embed_text(error_content)
                self._memory_store.store_embedding(kid, _embedding_to_blob(vec))
                self._faiss_add(kid, vec)
            except Exception:
                pass
            logger.debug("[MemoryMixin] auto-stored error: %s", error_content[:80])
        except Exception as e:
            logger.debug("[MemoryMixin] failed to auto-store error: %s", e)

    # ------------------------------------------------------------------
    # Hook 4: Post-Query Processing
    # ------------------------------------------------------------------

    def _after_process_query(self, user_input: str, assistant_response: str) -> None:
        """Store conversation turns and run Mem0-style LLM extraction.

        Called after process_query() completes (via hook in agent.py).
        Uses _original_user_input so dynamic context prefix is never persisted.
        """
        if not hasattr(self, "_memory_store"):
            return

        # Use original (pre-augmentation) user text for storage.
        clean_input = self._original_user_input or user_input

        # 1. Store conversation turns
        try:
            session_id = self.memory_session_id
            ctx = self._memory_context
            self._memory_store.store_turn(session_id, "user", clean_input, context=ctx)
            self._memory_store.store_turn(
                session_id, "assistant", assistant_response, context=ctx
            )
        except Exception as e:
            logger.warning("[MemoryMixin] failed to store conversation: %s", e)

        # 2. Mem0-style LLM extraction (for turns >= 20 words)
        if (
            self._auto_extract_enabled
            and len(clean_input.split()) >= MIN_EXTRACTION_WORDS
        ):
            try:
                # Fetch relevant existing memory for context
                existing = self._hybrid_search(
                    clean_input,
                    context=self._memory_context,
                    top_k=10,
                )

                # LLM decides operations against existing memory
                operations = self._extract_via_llm(
                    clean_input, assistant_response, existing
                )

                # Execute operations
                if operations:
                    self._execute_extraction_operations(operations, existing)
                    logger.debug(
                        "[MemoryMixin] executed %d extraction operations",
                        len(operations),
                    )
            except Exception as e:
                logger.warning("[MemoryMixin] LLM extraction failed: %s", e)

    # ------------------------------------------------------------------
    # Tool Registration
    # ------------------------------------------------------------------

    def register_memory_tools(self) -> None:
        """Register the 5 memory tools with the agent's tool registry.

        Call this from _register_tools() in your agent subclass.

        KNOWN ARCHITECTURAL LIMITATION — global _TOOL_REGISTRY:
        The @tool decorator registers into a module-level global dict in
        gaia.agents.base.tools. Each call to register_memory_tools() overwrites
        the previous closures. Avoid running two MemoryMixin agents concurrently
        in the same process.
        """
        from gaia.agents.base.tools import tool

        mixin = self  # Capture for closures

        @tool
        def remember(
            fact: str,
            category: str = "fact",
            domain: str = "",
            due_at: str = "",
            context: str = "",
            sensitive: str = "false",
            entity: str = "",
        ) -> dict:
            """Store a fact, preference, or learning in persistent memory. Categories: fact, preference, error, skill, note, reminder. Use due_at for time-sensitive items (ISO 8601). Use context to scope (work, personal). Use sensitive=true for private data. Use entity for linking (person:name, app:name)."""
            if not fact or not fact.strip():
                return {"status": "error", "message": "fact must not be empty."}

            if category not in VALID_CATEGORIES:
                return {
                    "status": "error",
                    "message": f"Invalid category. Use: {sorted(VALID_CATEGORIES)}",
                }

            if due_at:
                try:
                    datetime.fromisoformat(due_at)
                except ValueError:
                    return {
                        "status": "error",
                        "message": "Invalid due_at. Use ISO 8601 format.",
                    }

            ctx = context or mixin._memory_context
            sens = sensitive.lower() == "true" if sensitive else False

            was_truncated = len(fact) > MAX_CONTENT_LENGTH
            knowledge_id = mixin._memory_store.store(
                category=category,
                content=fact[:MAX_CONTENT_LENGTH],
                domain=domain or None,
                due_at=due_at or None,
                source="tool",
                context=ctx,
                sensitive=sens,
                entity=entity or None,
            )
            # Embed the new item
            try:
                vec = mixin._embed_text(fact[:MAX_CONTENT_LENGTH])
                mixin._memory_store.store_embedding(
                    knowledge_id, _embedding_to_blob(vec)
                )
                mixin._faiss_add(knowledge_id, vec)
            except Exception as e:
                logger.debug("[MemoryMixin] embedding on remember failed: %s", e)

            msg = f"Remembered: {fact[:80]}"
            if was_truncated:
                msg += f" (note: content was truncated to {MAX_CONTENT_LENGTH} chars)"
            return {
                "status": "stored",
                "knowledge_id": knowledge_id,
                "message": msg,
            }

        @tool
        def recall(
            query: str = "",
            category: str = "",
            context: str = "",
            entity: str = "",
            limit: int = 0,
            time_from: str = "",
            time_to: str = "",
        ) -> dict:
            """Search memory for relevant knowledge. With query: uses hybrid semantic+keyword search (vector + BM25, cross-encoder reranking). Without query: returns entries filtered by category/context/entity. At least one parameter required. time_from/time_to: ISO 8601 boundaries for temporal filtering."""
            if not any([query, category, context, entity, time_from, time_to]):
                return {
                    "status": "error",
                    "message": "Provide at least one of: query, category, context, entity, time_from, time_to",
                }

            # Adaptive top_k based on query complexity
            if limit <= 0:
                if query:
                    limit = mixin._classify_query_complexity(query)
                else:
                    limit = 5

            # Clamp limit to prevent huge result sets
            limit = max(1, min(limit, 20))

            if query:
                # Use hybrid search for query-based recall
                results = mixin._hybrid_search(
                    query=query,
                    category=category or None,
                    context=context or None,
                    entity=entity or None,
                    include_sensitive=True,  # LLM can access sensitive via explicit recall
                    top_k=limit,
                    time_from=time_from or None,
                    time_to=time_to or None,
                )
            elif entity:
                results = mixin._memory_store.get_by_entity(entity, limit=limit)
            elif category:
                results = mixin._memory_store.get_by_category(
                    category, context=context or None, limit=limit
                )
            elif time_from or time_to:
                # Time-range only: fetch from store sorted by created_at and
                # apply time filtering in Python.  Uses get_all_knowledge()
                # which includes items without embeddings (unlike
                # get_items_with_embeddings which filters embedding IS NOT NULL).
                page = mixin._memory_store.get_all_knowledge(
                    category=category or None,
                    context=context or None,
                    entity=entity or None,
                    sort_by="created_at",
                    order="desc",
                    limit=limit * 3,  # over-fetch to account for filtering
                )
                filtered = []
                for item in page.get("items", []):
                    created = item.get("created_at", "")
                    if time_from and created < time_from:
                        continue
                    if time_to and created > time_to:
                        continue
                    filtered.append(item)
                results = filtered[:limit]
            else:
                page = mixin._memory_store.get_all_knowledge(
                    context=context, limit=limit
                )
                results = page.get("items", [])

            # Sensitive items ARE accessible via explicit recall tool calls
            # (per spec). They are only excluded from the system prompt injection.

            return {
                "status": "found" if results else "empty",
                "count": len(results),
                "results": results,
            }

        @tool
        def update_memory(
            knowledge_id: str,
            content: str = "",
            category: str = "",
            domain: str = "",
            due_at: str = "",
            reminded_at: str = "",
            context: str = "",
            sensitive: str = "",
            entity: str = "",
        ) -> dict:
            """Update an existing memory entry by ID. Only non-empty fields change. Set reminded_at=now after mentioning a time-sensitive item."""
            kwargs = {}
            if content:
                if not content.strip():
                    return {
                        "status": "error",
                        "message": "content must not be empty or whitespace-only.",
                    }
                kwargs["content"] = content[:MAX_CONTENT_LENGTH]
            if category:
                if category not in VALID_CATEGORIES:
                    return {
                        "status": "error",
                        "message": f"Invalid category. Use: {sorted(VALID_CATEGORIES)}",
                    }
                kwargs["category"] = category
            if domain:
                kwargs["domain"] = domain
            if due_at:
                try:
                    datetime.fromisoformat(due_at)
                    kwargs["due_at"] = due_at
                except ValueError:
                    return {
                        "status": "error",
                        "message": "Invalid due_at. Use ISO 8601.",
                    }
            if reminded_at:
                if reminded_at.lower() == "now":
                    kwargs["reminded_at"] = datetime.now().astimezone().isoformat()
                else:
                    try:
                        datetime.fromisoformat(reminded_at)
                        kwargs["reminded_at"] = reminded_at
                    except ValueError:
                        return {
                            "status": "error",
                            "message": "Invalid reminded_at. Use ISO 8601 format or 'now'.",
                        }
            if context:
                kwargs["context"] = context
            if sensitive:
                kwargs["sensitive"] = sensitive.lower() == "true"
            if entity:
                kwargs["entity"] = entity

            if not kwargs:
                return {"status": "error", "message": "No fields to update."}

            content_truncated = content and len(content) > MAX_CONTENT_LENGTH
            success = mixin._memory_store.update(knowledge_id, **kwargs)
            if success:
                # Re-embed if content changed
                if content:
                    try:
                        vec = mixin._embed_text(kwargs["content"])
                        mixin._memory_store.store_embedding(
                            knowledge_id, _embedding_to_blob(vec)
                        )
                        # Replace in FAISS: remove old, add new
                        mixin._faiss_remove(knowledge_id)
                        mixin._faiss_add(knowledge_id, vec)
                    except Exception as e:
                        logger.debug(
                            "[MemoryMixin] re-embedding on update failed: %s", e
                        )

                result = {"status": "updated", "knowledge_id": knowledge_id}
                if content_truncated:
                    result["note"] = "content was truncated to 2000 chars"
                return result
            return {"status": "not_found", "knowledge_id": knowledge_id}

        @tool
        def forget(knowledge_id: str) -> dict:
            """Remove a specific memory entry by ID."""
            removed = mixin._memory_store.delete(knowledge_id)
            if removed:
                mixin._faiss_remove(knowledge_id)
                return {"status": "removed", "knowledge_id": knowledge_id}
            return {"status": "not_found", "knowledge_id": knowledge_id}

        @tool
        def search_past_conversations(
            query: str = "",
            days: int = 0,
            limit: int = 10,
            time_from: str = "",
            time_to: str = "",
        ) -> dict:
            """Search past conversations. Use query for keywords, days for time range, time_from/time_to for ISO 8601 boundaries, or combinations."""
            if not query and not days and not time_from and not time_to:
                return {
                    "status": "error",
                    "message": "Provide query (keywords), days (time range), or time_from/time_to.",
                }

            # Clamp params to safe bounds
            limit = max(1, min(limit, 50))
            if days:
                days = max(1, min(days, 365))

            results = []
            if query and days:
                keyword_results = mixin._memory_store.search_conversations(
                    query, limit=limit * 2
                )
                cutoff = (
                    datetime.now().astimezone() - timedelta(days=days)
                ).isoformat()
                results = [
                    r for r in keyword_results if r.get("timestamp", "") >= cutoff
                ][:limit]
            elif query and (time_from or time_to):
                keyword_results = mixin._memory_store.search_conversations(
                    query, limit=limit * 2
                )
                filtered = []
                for r in keyword_results:
                    ts = r.get("timestamp", "")
                    if time_from and ts < time_from:
                        continue
                    if time_to and ts > time_to:
                        continue
                    filtered.append(r)
                results = filtered[:limit]
            elif query:
                results = mixin._memory_store.search_conversations(query, limit=limit)
            elif days:
                results = mixin._memory_store.get_recent_conversations(
                    days=days, limit=limit
                )
            elif time_from or time_to:
                # Time-range only: get recent and filter
                max_days = 365
                all_results = mixin._memory_store.get_recent_conversations(
                    days=max_days, limit=limit * 2
                )
                filtered = []
                for r in all_results:
                    ts = r.get("timestamp", "")
                    if time_from and ts < time_from:
                        continue
                    if time_to and ts > time_to:
                        continue
                    filtered.append(r)
                results = filtered[:limit]

            return {
                "status": "found" if results else "empty",
                "count": len(results),
                "results": results,
            }

        logger.info("[MemoryMixin] registered 5 memory tools (v2)")

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    def reset_memory_session(self) -> None:
        """Start a fresh memory session.

        Generates new session ID and applies confidence decay.
        """
        if hasattr(self, "_memory_store"):
            self._memory_store.apply_confidence_decay()
            self._memory_session_id = str(uuid4())
            logger.info(
                "[MemoryMixin] session reset, new session_id=%s",
                self._memory_session_id,
            )
