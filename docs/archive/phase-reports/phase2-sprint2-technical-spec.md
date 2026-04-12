# Phase 2 Sprint 2: Context Lens Optimization - Technical Specification

**Document Version:** 1.0
**Date:** 2026-04-06
**Status:** READY FOR IMPLEMENTATION
**Duration:** 4 weeks (Weeks 3-6)
**Owner:** senior-developer
**Sprint Goal:** Enhance Context Lens pattern with token-efficient summarization, embedding-based relevance scoring, and smart budget enforcement.

---

## Executive Summary

Phase 2 Sprint 2 builds upon the Phase 1 foundation (NexusService, ChronicleDigest) and Phase 2 Sprint 1 completion (SupervisorAgent) to implement advanced context optimization capabilities. This sprint addresses action items AI-001 (benchmarking), AI-002 (tiktoken integration), and AI-003 (performance monitoring) from Phase 1.

### Sprint 2 Objectives

| Objective | Metric | Target | Priority |
|-----------|--------|--------|----------|
| **ContextLens Extension** | Enhanced digest with relevance scoring | Complete | P0 |
| **Tiktoken Integration** | Accurate token counting (>95% accuracy) | Complete | P0 |
| **Embedding-Based Relevance** | Semantic similarity for event prioritization | Complete | P1 |
| **Performance Benchmarks** | Digest latency <50ms (95th percentile) | Complete | P0 |
| **Memory Optimization** | Context state <1MB footprint | Complete | P1 |

### Sprint 2 Deliverables

| Component | File | LOC Estimate | Tests | Sprint Week |
|-----------|------|--------------|-------|-------------|
| **TokenCounter** | `src/gaia/state/token_counter.py` | ~150 | 15 | Week 3 |
| **ContextLens Extension** | `src/gaia/state/context_lens.py` | ~300 | 25 | Week 3-4 |
| **EmbeddingRelevance** | `src/gaia/state/relevance.py` | ~200 | 15 | Week 4 |
| **NexusService Extension** | `src/gaia/state/nexus.py` | +100 | +10 | Week 3-4 |
| **Performance Tests** | `tests/unit/state/test_context_lens.py` | N/A | 65 | Week 4 |
| **Integration Tests** | `tests/unit/state/test_context_integration.py` | N/A | 20 | Week 5 |

---

## 1. Technical Architecture

### 1.1 System Overview

```
                                    Phase 1 Foundation
┌─────────────────────────────────────────────────────────────────────────────┐
│  NexusService (singleton)                                                   │
│  ├── AuditLogger wrapper (Chronicle)                                        │
│  ├── WorkspaceIndex (metadata)                                              │
│  └── get_digest() / get_chronicle_digest()                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Phase 2 Sprint 2: Context Lens                           │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  TokenCounter   │  │  ContextLens    │  │  Embedding      │             │
│  │  (tiktoken)     │──│  (digest v2)    │──│  Relevance      │             │
│  │                 │  │                 │  │                 │             │
│  │  - count()      │  │  - optimize()   │  │  - score()      │             │
│  │  - truncate()   │  │  - summarize()  │  │  - rank()       │             │
│  │  - estimate()   │  │  - prioritize() │  │  - embed()      │             │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘             │
│         │                    │                    │                        │
│         └────────────────────┼────────────────────┘                        │
│                              ▼                                            │
│                    ┌─────────────────┐                                    │
│                    │  NexusService   │                                    │
│                    │  (extended)     │                                    │
│                    │                 │                                    │
│                    │  - get_context()│                                    │
│                    │  - with_lens()  │                                    │
│                    └─────────────────┘                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                                    Phase 2 Sprint 1 (Complete)
┌─────────────────────────────────────────────────────────────────────────────┐
│  SupervisorAgent                                                            │
│  └── Uses: get_chronicle_digest(max_tokens=2000)                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Component Dependencies

```
Phase 1 Complete (NexusService, AuditLogger, ChronicleDigest)
       │
       ▼
┌─────────────────┐
│  TokenCounter   │◄───────┐
│  (tiktoken)     │        │
└────────┬────────┘        │
         │                 │
         ▼                 │
┌─────────────────┐        │
│  ContextLens    │◄───────┤
│  (digest v2)    │        │
└────────┬────────┘        │
         │                 │
         ▼                 │
┌─────────────────┐        │
│  Embedding      │        │
│  Relevance      │────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  NexusService   │
│  (extended)     │
└────────┬────────┘
         │
    ┌────┴────┬────────────┐
    │         │            │
    ▼         ▼            ▼
┌────────┐ ┌────────┐ ┌──────────┐
│Super-  │ │Pipeline│ │CodeAgent │
│visor   │ │Engine  │ │(future)  │
└────────┘ └────────┘ └──────────┘
```

---

## 2. Implementation Details

### 2.1 TokenCounter Component

**File:** `src/gaia/state/token_counter.py`

**Purpose:** Accurate token counting using tiktoken for GPT-style models, with fallback estimation for local models.

**Dependencies:**
- `tiktoken>=0.5.0` (optional, graceful fallback if not installed)
- Thread-safe for concurrent access

**Implementation:**

```python
# src/gaia/state/token_counter.py
"""
Token Counter - Accurate Token Counting for GAIA

Provides accurate token counting using tiktoken for GPT-style models,
with fallback estimation for local models (GGUF, etc.).

Features:
    - tiktoken integration for OpenAI/Claude models
    - Fallback estimation (~4 chars/token) for local models
    - Thread-safe counting operations
    - Budget enforcement with truncation

Example:
    >>> counter = TokenCounter(model="gpt-4")
    >>> tokens = counter.count("Hello, world!")
    >>> counter.truncate_to_budget(text, max_tokens=100)
"""

import threading
from typing import Optional, List, Tuple
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

    Example:
        >>> counter = TokenCounter(model="gpt-4")
        >>> counter.count("Hello")
        1
        >>> counter.count_tokens(["Hello", "World"])
        2
    """

    _encoding_cache = {}
    _cache_lock = threading.Lock()

    def __init__(self, model: str = "cl100k_base"):
        """
        Initialize token counter with model-specific encoding.

        Args:
            model: Model name or encoding (default: cl100k_base)
                   Supports: gpt-4, gpt-3.5-turbo, cl100k_base,
                            p50k_base, r50k_base
        """
        self.model = model
        self._encoding = self._get_encoding(model)
        self._lock = threading.RLock()

    def _get_encoding(self, model: str):
        """Get or create encoding for model."""
        with self._cache_lock:
            if model in self._encoding_cache:
                return self._encoding_cache[model]

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
                logger.debug(f"Loaded tiktoken encoding for {model}")
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
                return len(text) // 4

    def count_many(self, texts: List[str]) -> List[int]:
        """
        Count tokens for multiple texts efficiently.

        Args:
            texts: List of texts to count

        Returns:
            List of token counts

        Example:
            >>> counter.count_many(["Hello", "World"])
            [1, 1]
        """
        with self._lock:
            if self._encoding is not None:
                # Batch encoding is more efficient
                all_tokens = self._encoding.encode_many(texts)
                return [len(tokens) for tokens in all_tokens]
            else:
                return [len(text) // 4 for text in texts]

    def truncate_to_budget(
        self,
        text: str,
        max_tokens: int,
        preserve_sentences: bool = True,
    ) -> str:
        """
        Truncate text to fit within token budget.

        Args:
            text: Text to truncate
            max_tokens: Maximum token budget
            preserve_sentences: If True, preserve sentence boundaries

        Returns:
            Truncated text within budget

        Example:
            >>> counter.truncate_to_budget("Long text...", max_tokens=100)
            'Truncated text...'
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

        Greedy selection: adds texts until budget is exceeded.

        Args:
            texts: List of texts to select from
            max_tokens: Maximum total token budget

        Returns:
            Tuple of (selected_texts, total_tokens)

        Example:
            >>> texts = ["Short", "Medium text", "Very long text..."]
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

    def get_encoding_info(self) -> dict:
        """Get information about current encoding."""
        with self._lock:
            return {
                "model": self.model,
                "tiktoken_available": self._encoding is not None,
                "fallback_mode": self._encoding is None,
            }
```

**Test File:** `tests/unit/state/test_token_counter.py` (15 tests)

| Test Function | Purpose | Category |
|---------------|---------|----------|
| `test_initialization_default` | Verify default model initialization | Initialization |
| `test_initialization_custom_model` | Verify custom model initialization | Initialization |
| `test_count_simple_text` | Basic token counting | Counting |
| `test_count_empty_text` | Empty string handling | Counting |
| `test_count_many_batch` | Batch counting efficiency | Counting |
| `test_truncate_within_budget` | No truncation when under budget | Truncation |
| `test_truncate_exceeds_budget` | Truncation when over budget | Truncation |
| `test_truncate_preserve_sentences` | Sentence boundary preservation | Truncation |
| `test_truncate_no_sentence_boundary` | Truncation without clear boundary | Truncation |
| `test_estimate_budget_fits_all` | Budget estimation when all fit | Budget |
| `test_estimate_budget_partial` | Budget estimation with partial fit | Budget |
| `test_fallback_mode_without_tiktoken` | Graceful fallback without tiktoken | Fallback |
| `test_thread_safety_concurrent_count` | Concurrent counting operations | Thread Safety |
| `test_encoding_cache_shared` | Encoding cache is shared | Caching |
| `test_get_encoding_info` | Encoding info reporting | Metadata |

---

### 2.2 ContextLens Extension

**File:** `src/gaia/state/context_lens.py`

**Purpose:** Enhanced context digest with hierarchical summarization, relevance scoring, and budget enforcement.

**Dependencies:**
- `TokenCounter` for accurate token counting
- `NexusService` for event access
- `EmbeddingRelevance` (optional) for semantic scoring

**Implementation:**

```python
# src/gaia/state/context_lens.py
"""
Context Lens - Enhanced Context Digest for GAIA

Provides hierarchical context summarization with:
1. Token budget enforcement via TokenCounter
2. Relevance scoring via EmbeddingRelevance
3. Hierarchical summarization (recent -> phase -> loop)
4. Smart prioritization based on agent and event type

Features:
    - Multi-level abstraction (event -> phase -> pipeline)
    - Token-efficient summarization with hard budget
    - Relevance-based event prioritization
    - Graceful degradation when components unavailable

Example:
    >>> lens = ContextLens(nexus_service)
    >>> context = lens.optimize_context(
    ...     agent_id="CodeAgent",
    ...     max_tokens=2000,
    ...     use_relevance=True
    ... )
"""

import copy
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from gaia.state.nexus import NexusService
from gaia.state.token_counter import TokenCounter
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ContextMetadata:
    """
    Metadata about generated context.

    Attributes:
        total_tokens: Actual token count of context
        token_budget: Requested maximum tokens
        events_included: Number of events in context
        agents_included: Unique agents in context
        phases_included: Unique phases in context
        generation_time_ms: Time to generate context
        relevance_used: Whether relevance scoring was applied
        compression_ratio: Original events / included events
    """
    total_tokens: int
    token_budget: int
    events_included: int
    agents_included: int
    phases_included: int
    generation_time_ms: float
    relevance_used: bool = False
    compression_ratio: float = 1.0


@dataclass
class ScoredEvent:
    """
    Event with relevance score.

    Attributes:
        event: Original event dictionary
        score: Relevance score (0.0 - 1.0)
        recency_factor: Time-decay factor
        agent_relevance: Agent proximity score
        type_weight: Event type importance
    """
    event: Dict[str, Any]
    score: float
    recency_factor: float = 1.0
    agent_relevance: float = 0.0
    type_weight: float = 1.0


class ContextLens:
    """
    Enhanced context digest with smart prioritization.

    The ContextLens provides:
    1. Token-accurate context generation using tiktoken
    2. Relevance-based event scoring and selection
    3. Hierarchical summarization (recent -> phase -> loop)
    4. Budget enforcement with graceful degradation

    Context Generation Strategy:
        1. Score all events by relevance to target agent
        2. Sort by score (descending)
        3. Add events until token budget reached
        4. Fill remaining budget with phase summaries
        5. Add workspace summary if budget allows

    Relevance Scoring Factors:
        - Recency: Exponential decay (1/(1+age_hours))
        - Agent proximity: +2 for same agent, +1 for related
        - Event type: Quality/Decision events weighted higher
        - Phase relevance: Current phase events prioritized

    Example:
        >>> lens = ContextLens(nexus)
        >>> context = lens.get_context(
        ...     agent_id="CodeAgent",
        ...     max_tokens=2000
        ... )
        >>> print(context["digest"])
        >>> print(context["metadata"].total_tokens)
    """

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

    # Agent relationship mapping (for future expansion)
    AGENT_RELATIONSHIPS = {
        "CodeAgent": ["PipelineEngine", "QualityScorer"],
        "ChatAgent": ["RAGService", "KnowledgeBase"],
        "SupervisorAgent": ["QualityScorer", "PipelineEngine"],
    }

    def __init__(
        self,
        nexus_service: NexusService,
        token_counter: Optional[TokenCounter] = None,
        model: str = "cl100k_base",
    ):
        """
        Initialize ContextLens.

        Args:
            nexus_service: NexusService instance for event access
            token_counter: Optional TokenCounter (created if not provided)
            model: Token encoding model (default: cl100k_base)
        """
        self._nexus = nexus_service
        self._token_counter = token_counter or TokenCounter(model=model)
        self._lock = threading.RLock()

        logger.info(
            "ContextLens initialized",
            extra={
                "model": model,
                "tiktoken": self._token_counter.get_encoding_info()["tiktoken_available"],
            },
        )

    def get_context(
        self,
        agent_id: str,
        max_tokens: int = 2000,
        use_relevance: bool = True,
        include_phases: Optional[List[str]] = None,
        include_agents: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Generate optimized context for agent.

        Args:
            agent_id: Target agent identifier
            max_tokens: Maximum token budget
            use_relevance: Enable relevance-based scoring
            include_phases: Filter to specific phases
            include_agents: Filter to specific agents

        Returns:
            Dictionary with:
            - digest: Formatted context string
            - metadata: ContextMetadata with statistics
            - events: List of included events
        """
        start_time = time.time()

        with self._lock:
            # Get events from Nexus
            snapshot = self._nexus.get_snapshot()
            events = snapshot.get("chronicle", [])

            # Apply filters
            if include_phases:
                events = [e for e in events if e.get("phase") in include_phases]
            if include_agents:
                events = [e for e in events if e.get("agent_id") in include_agents]

            # Score events if relevance enabled
            if use_relevance:
                scored_events = self._score_events(events, agent_id)
                # Sort by score descending
                scored_events.sort(key=lambda x: x.score, reverse=True)
            else:
                # No scoring: use reversed chronological order
                scored_events = [
                    ScoredEvent(event=e, score=1.0)
                    for e in reversed(events)
                ]

            # Select events within token budget
            selected_events, tokens_used = self._select_events_within_budget(
                scored_events,
                max_tokens,
                reserved_for_summary=500,  # Reserve 500 tokens for phase/workspace summary
            )

            # Build digest
            digest_parts = []

            # 1. Recent events (70% of remaining budget)
            recent_budget = int((max_tokens - tokens_used) * 0.7)
            recent_section = self._format_recent_events(selected_events, recent_budget)
            digest_parts.append(recent_section)

            # 2. Phase summaries (20% of budget)
            phase_budget = int((max_tokens - tokens_used) * 0.2)
            if phase_budget > 50:
                phase_section = self._format_phase_summaries(events, phase_budget)
                digest_parts.append(phase_section)

            # 3. Workspace summary (10% of budget)
            workspace_budget = int((max_tokens - tokens_used) * 0.1)
            if workspace_budget > 30:
                workspace = snapshot.get("workspace", {})
                workspace_section = self._format_workspace_summary(workspace, workspace_budget)
                digest_parts.append(workspace_section)

            digest = "\n\n".join(digest_parts)

            # Calculate metadata
            generation_time = (time.time() - start_time) * 1000
            actual_tokens = self._token_counter.count(digest)

            metadata = ContextMetadata(
                total_tokens=actual_tokens,
                token_budget=max_tokens,
                events_included=len(selected_events),
                agents_included=len(set(e.event.get("agent_id") for e in selected_events)),
                phases_included=len(set(e.event.get("phase") for e in selected_events if e.event.get("phase"))),
                generation_time_ms=generation_time,
                relevance_used=use_relevance,
                compression_ratio=len(events) / len(selected_events) if selected_events else 1.0,
            )

            return {
                "digest": digest,
                "metadata": metadata,
                "events": [e.event for e in selected_events],
            }

    def _score_events(
        self,
        events: List[Dict[str, Any]],
        agent_id: str,
    ) -> List[ScoredEvent]:
        """
        Score events by relevance to agent.

        Scoring formula:
            score = recency_factor * (1 + agent_relevance + type_weight)

        Args:
            events: List of events to score
            agent_id: Target agent identifier

        Returns:
            List of ScoredEvent objects sorted by score
        """
        scored = []
        current_time = time.time()

        for event in events:
            # Recency factor: exponential decay
            event_time = event.get("timestamp", current_time)
            age_hours = (current_time - event_time) / 3600
            recency_factor = 1.0 / (1.0 + age_hours)

            # Agent relevance
            event_agent = event.get("agent_id", "")
            if event_agent == agent_id:
                agent_relevance = 2.0
            elif event_agent in self.AGENT_RELATIONSHIPS.get(agent_id, []):
                agent_relevance = 1.0
            else:
                agent_relevance = 0.0

            # Event type weight
            event_type = event.get("event_type", "")
            type_weight = self.EVENT_TYPE_WEIGHTS.get(event_type, 1.0)

            # Combined score
            score = recency_factor * (1.0 + agent_relevance + type_weight)

            scored.append(ScoredEvent(
                event=event,
                score=score,
                recency_factor=recency_factor,
                agent_relevance=agent_relevance,
                type_weight=type_weight,
            ))

        return scored

    def _select_events_within_budget(
        self,
        scored_events: List[ScoredEvent],
        max_tokens: int,
        reserved_for_summary: int = 500,
    ) -> Tuple[List[ScoredEvent], int]:
        """
        Select events that fit within token budget.

        Args:
            scored_events: Scored events to select from
            max_tokens: Maximum token budget
            reserved_for_summary: Tokens reserved for phase/workspace summary

        Returns:
            Tuple of (selected_events, tokens_used)
        """
        available_budget = max_tokens - reserved_for_summary
        selected = []
        tokens_used = 0

        for scored_event in scored_events:
            # Estimate event tokens (compact format ~50 chars/event)
            event_text = self._format_event_compact(scored_event.event)
            event_tokens = self._token_counter.count(event_text)

            if tokens_used + event_tokens <= available_budget:
                selected.append(scored_event)
                tokens_used += event_tokens
            else:
                # Try to fit truncated event
                remaining = available_budget - tokens_used
                if remaining > 20:  # Minimum viable event
                    truncated = self._token_counter.truncate_to_budget(event_text, remaining)
                    if truncated != event_text:
                        selected.append(scored_event)
                        tokens_used += self._token_counter.count(truncated)
                break

        return selected, tokens_used

    def _format_recent_events(
        self,
        scored_events: List[ScoredEvent],
        token_budget: int,
    ) -> str:
        """Format recent events section."""
        parts = ["## Recent Events\n"]
        tokens = self._token_counter.count(parts[0])

        for scored in scored_events:
            if tokens >= token_budget:
                remaining = len(scored_events) - len(parts) + 1
                parts.append(f"\n... and {remaining} more events")
                break

            event_str = self._format_event_compact(scored.event)
            parts.append(event_str)
            tokens += self._token_counter.count(event_str)

        return "".join(parts)

    def _format_event_compact(self, event: Dict[str, Any]) -> str:
        """Format single event in compact form."""
        phase = event.get("phase", "N/A")
        agent_id = event.get("agent_id", "system")
        event_type = event.get("event_type", "unknown")

        # Payload summary
        payload = event.get("payload", {})
        if payload:
            payload_str = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(payload.items())[:2])
            return f"- [{phase}] {agent_id}: {event_type} ({payload_str})\n"
        else:
            return f"- [{phase}] {agent_id}: {event_type}\n"

    def _format_phase_summaries(
        self,
        events: List[Dict[str, Any]],
        token_budget: int,
    ) -> str:
        """Format phase summaries section."""
        # Group by phase
        phase_groups: Dict[str, List[Dict]] = {}
        for event in events:
            phase = event.get("phase", "N/A")
            if phase not in phase_groups:
                phase_groups[phase] = []
            phase_groups[phase].append(event)

        parts = ["\n## Phase Summaries\n"]
        tokens = self._token_counter.count(parts[0])

        for phase, phase_events in phase_groups.items():
            if tokens >= token_budget:
                break

            agents = list(set(e.get("agent_id") for e in phase_events if e.get("agent_id")))
            event_types = list(set(e.get("event_type") for e in phase_events))

            summary = f"- **{phase}**: {len(phase_events)} events | Agents: {', '.join(agents[:3])} | Types: {', '.join(event_types[:3])}\n"
            parts.append(summary)
            tokens += self._token_counter.count(summary)

        return "".join(parts)

    def _format_workspace_summary(
        self,
        workspace: Dict[str, Any],
        token_budget: int,
    ) -> str:
        """Format workspace summary section."""
        files = workspace.get("files", {})
        file_count = len(files)

        parts = ["\n## Workspace\n"]
        tokens = self._token_counter.count(parts[0])

        parts.append(f"Files tracked: {file_count}\n")
        tokens += self._token_counter.count(parts[-1])

        # Recent files (last 5)
        if files and tokens < token_budget:
            parts.append("Recent changes:\n")
            recent = list(files.items())[-5:]
            for path, meta in recent:
                if tokens >= token_budget:
                    break
                line_info = f"  - {path}"
                if isinstance(meta, dict) and "lines" in meta:
                    line_info += f" ({meta['lines']} lines)"
                parts.append(line_info + "\n")
                tokens += self._token_counter.count(line_info)

        return "".join(parts)

    def get_chronicle_digest(
        self,
        max_events: int = 15,
        max_tokens: int = 3500,
        include_phases: Optional[List[str]] = None,
        include_agents: Optional[List[str]] = None,
        use_relevance: bool = False,
        agent_id: Optional[str] = None,
    ) -> str:
        """
        Generate enhanced Chronicle digest with relevance scoring.

        This method extends NexusService.get_chronicle_digest() with:
        - Accurate token counting via tiktoken
        - Optional relevance-based event prioritization
        - Better budget enforcement

        Args:
            max_events: Maximum number of events
            max_tokens: Maximum token budget
            include_phases: Phase filter
            include_agents: Agent filter
            use_relevance: Enable relevance scoring
            agent_id: Target agent for relevance scoring

        Returns:
            Formatted digest string
        """
        context = self.get_context(
            agent_id=agent_id or "system",
            max_tokens=max_tokens,
            use_relevance=use_relevance and agent_id is not None,
            include_phases=include_phases,
            include_agents=include_agents,
        )
        return context["digest"]
```

**Test File:** `tests/unit/state/test_context_lens.py` (25 tests)

| Test Function | Purpose | Category |
|---------------|---------|----------|
| `test_initialization` | Verify ContextLens initialization | Initialization |
| `test_get_context_basic` | Basic context generation | Context Generation |
| `test_get_context_with_filters` | Context with phase/agent filters | Context Generation |
| `test_get_context_relevance_enabled` | Context with relevance scoring | Relevance |
| `test_get_context_relevance_disabled` | Context without relevance | Relevance |
| `test_score_events_recency` | Recency factor in scoring | Relevance Scoring |
| `test_score_events_agent_proximity` | Agent proximity scoring | Relevance Scoring |
| `test_score_events_type_weight` | Event type weighting | Relevance Scoring |
| `test_select_events_within_budget` | Budget-constrained selection | Budget Enforcement |
| `test_select_events_truncation` | Event truncation when needed | Budget Enforcement |
| `test_format_recent_events` | Recent events formatting | Formatting |
| `test_format_phase_summaries` | Phase summary formatting | Formatting |
| `test_format_workspace_summary` | Workspace summary formatting | Formatting |
| `test_format_event_compact` | Compact event formatting | Formatting |
| `test_token_budget_enforcement` | Hard token budget enforcement | Budget Enforcement |
| `test_metadata_generation` | Context metadata accuracy | Metadata |
| `test_compression_ratio` | Event compression calculation | Metadata |
| `test_generation_time_tracking` | Performance tracking | Performance |
| `test_chronicle_digest_enhanced` | Enhanced Chronicle digest | Integration |
| `test_empty_events_handling` | Graceful handling of empty event log | Edge Cases |
| `test_single_event_handling` | Single event in context | Edge Cases |
| `test_budget_too_small` | Handling very small budgets | Edge Cases |
| `test_thread_safety_concurrent` | Concurrent context generation | Thread Safety |
| `test_nexus_unavailable_degradation` | Graceful degradation | Degradation |
| `test_full_context_pipeline` | End-to-end context generation | Integration |

---

### 2.3 EmbeddingRelevance Component

**File:** `src/gaia/state/relevance.py`

**Purpose:** Embedding-based semantic similarity scoring for event relevance.

**Dependencies:**
- `sentence-transformers>=2.2.0` (optional, graceful fallback)
- `numpy>=1.24.0` for vector operations
- Thread-safe for concurrent access

**Implementation:**

```python
# src/gaia/state/relevance.py
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
    >>> relevance = EmbeddingRelevance()
    >>> score = relevance.score_event(
    ...     event_text="CodeAgent fixed bug in parser",
    ...     query="CodeAgent code changes"
    ... )
    >>> scores = relevance.rank_events(events, query="debugging")
"""

import threading
import numpy as np
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
        keyword-based relevance scoring.

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
            use_gpu: Enable GPU acceleration (default: False)
        """
        self.model_name = model
        self.use_gpu = use_gpu
        self._model = None
        self._available = False
        self._lock = threading.RLock()

        self._load_model()

    def _load_model(self):
        """Load sentence transformer model."""
        try:
            from sentence_transformers import SentenceTransformer

            device = "cuda" if self.use_gpu else "cpu"
            self._model = SentenceTransformer(self.model_name, device=device)
            self._available = True

            logger.info(
                f"EmbeddingRelevance loaded with {self.model_name} on {device}",
                extra={"gpu": self.use_gpu},
            )

        except ImportError:
            logger.warning(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers. "
                "Falling back to keyword-based scoring."
            )
            self._available = False
            self._model = None

    def is_available(self) -> bool:
        """Check if embedding model is available."""
        return self._available

    def embed(self, text: str) -> np.ndarray:
        """
        Generate embedding for text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector (numpy array)

        Raises:
            RuntimeError: If model not available
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

    def embed_many(self, texts: List[str]) -> np.ndarray:
        """
        Generate embeddings for multiple texts efficiently.

        Args:
            texts: List of texts to embed

        Returns:
            Embedding matrix (n_texts x embedding_dim)
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
        embedding1: np.ndarray,
        embedding2: np.ndarray,
    ) -> float:
        """
        Compute cosine similarity between two embeddings.

        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector

        Returns:
            Similarity score (0.0 - 1.0 for normalized embeddings)
        """
        # For normalized embeddings, dot product = cosine similarity
        similarity = np.dot(embedding1, embedding2)
        # Clamp to [0, 1] range
        return float(np.clip(similarity, 0.0, 1.0))

    def score_event(
        self,
        event_text: str,
        query: str,
        event_embedding: Optional[np.ndarray] = None,
    ) -> float:
        """
        Score event relevance against query.

        Args:
            event_text: Event text to score
            query: Query/relevance target
            event_embedding: Optional pre-computed event embedding

        Returns:
            Relevance score (0.0 - 1.0)
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

        Args:
            event_texts: List of event texts to score
            query: Query/relevance target

        Returns:
            List of relevance scores
        """
        with self._lock:
            if not self._available:
                return [self._keyword_score(text, query) for text in event_texts]

            try:
                # Batch embed events
                event_embs = self.embed_many(event_texts)
                query_emb = self.embed(query)

                # Compute similarities
                similarities = event_embs @ query_emb.T
                scores = np.clip(similarities.flatten(), 0.0, 1.0)
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
        """Convert event dict to text for embedding."""
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

        Computes Jaccard similarity between word sets.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score (0.0 - 1.0)
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
        """Get model information."""
        return {
            "model_name": self.model_name,
            "available": self._available,
            "gpu_enabled": self.use_gpu,
            "fallback_mode": not self._available,
        }
```

**Test File:** `tests/unit/state/test_relevance.py` (15 tests)

| Test Function | Purpose | Category |
|---------------|---------|----------|
| `test_initialization_default` | Default model initialization | Initialization |
| `test_initialization_custom_model` | Custom model initialization | Initialization |
| `test_is_available_with_model` | Availability check (model installed) | Availability |
| `test_is_available_without_model` | Availability check (fallback) | Availability |
| `test_embed_single_text` | Single text embedding | Embedding |
| `test_embed_many_batch` | Batch embedding efficiency | Embedding |
| `test_cosine_similarity_identical` | Similarity for identical texts | Similarity |
| `test_cosine_similarity_different` | Similarity for different texts | Similarity |
| `test_score_event_embedding` | Event scoring with embeddings | Scoring |
| `test_score_event_fallback` | Event scoring with fallback | Fallback |
| `test_score_events_batch` | Batch event scoring | Scoring |
| `test_rank_events_relevance` | Event ranking by relevance | Ranking |
| `test_rank_events_top_k` | Top-K event selection | Ranking |
| `test_keyword_score_jaccard` | Keyword-based Jaccard scoring | Fallback |
| `test_event_to_text_conversion` | Event-to-text conversion | Utility |

---

### 2.4 NexusService Extension

**File:** `src/gaia/state/nexus.py` (Extension: +100 LOC)

**Purpose:** Extend NexusService with ContextLens integration.

**Changes:**
- Add `get_optimized_context()` method
- Add `get_context_lens()` accessor
- Lazy initialization of ContextLens

**Implementation (diff):**

```python
# Add to src/gaia/state/nexus.py

# Add imports at top
from typing import Optional, Dict, Any, List


# Add after NexusService.__init__()
def _get_context_lens(self):
    """
    Get or create ContextLens instance (lazy initialization).

    Returns:
        ContextLens instance for optimized context generation
    """
    if not hasattr(self, '_context_lens') or self._context_lens is None:
        from gaia.state.context_lens import ContextLens
        self._context_lens = ContextLens(self)
    return self._context_lens


def get_optimized_context(
    self,
    agent_id: str,
    max_tokens: int = 2000,
    use_relevance: bool = True,
    include_phases: Optional[List[str]] = None,
    include_agents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate optimized context with smart prioritization.

    Uses ContextLens for:
    1. Token-accurate context generation (tiktoken)
    2. Relevance-based event scoring
    3. Hierarchical summarization
    4. Budget enforcement

    Args:
        agent_id: Target agent identifier
        max_tokens: Maximum token budget
        use_relevance: Enable relevance scoring
        include_phases: Filter to specific phases
        include_agents: Filter to specific agents

    Returns:
        Dictionary with:
        - digest: Formatted context string
        - metadata: Context metadata (tokens, events, timing)
        - events: List of included events

    Example:
        >>> context = nexus.get_optimized_context(
        ...     agent_id="CodeAgent",
        ...     max_tokens=2000,
        ...     use_relevance=True
        ... )
        >>> print(context["metadata"].total_tokens)
    """
    context_lens = self._get_context_lens()
    return context_lens.get_context(
        agent_id=agent_id,
        max_tokens=max_tokens,
        use_relevance=use_relevance,
        include_phases=include_phases,
        include_agents=include_agents,
    )


def get_enhanced_chronicle_digest(
    self,
    max_events: int = 15,
    max_tokens: int = 3500,
    include_phases: Optional[List[str]] = None,
    include_agents: Optional[List[str]] = None,
    use_relevance: bool = False,
    agent_id: Optional[str] = None,
) -> str:
    """
    Generate enhanced Chronicle digest with relevance scoring.

    Extension of get_chronicle_digest() with:
    - Accurate token counting (tiktoken)
    - Optional relevance-based prioritization

    Args:
        max_events: Maximum number of events
        max_tokens: Maximum token budget
        include_phases: Phase filter
        include_agents: Agent filter
        use_relevance: Enable relevance scoring
        agent_id: Target agent for relevance

    Returns:
        Formatted digest string

    Example:
        >>> digest = nexus.get_enhanced_chronicle_digest(
        ...     max_events=20,
        ...     max_tokens=3000,
        ...     use_relevance=True,
        ...     agent_id="CodeAgent"
        ... )
    """
    context_lens = self._get_context_lens()
    return context_lens.get_chronicle_digest(
        max_events=max_events,
        max_tokens=max_tokens,
        include_phases=include_phases,
        include_agents=include_agents,
        use_relevance=use_relevance,
        agent_id=agent_id,
    )
```

**Test Additions:** `tests/unit/state/test_nexus_service.py` (+10 tests)

| Test Function | Purpose | Category |
|---------------|---------|----------|
| `test_get_optimized_context_basic` | Basic optimized context | Context Lens |
| `test_get_optimized_context_relevance` | Context with relevance | Context Lens |
| `test_get_enhanced_chronicle_digest` | Enhanced Chronicle digest | Context Lens |
| `test_context_lens_lazy_init` | Lazy ContextLens initialization | Initialization |
| `test_context_metadata_accuracy` | Metadata tracking accuracy | Metadata |
| `test_context_token_accuracy` | Token counting accuracy | Token Counting |
| `test_context_thread_safety` | Concurrent context generation | Thread Safety |
| `test_context_backward_compatibility` | Existing digest() still works | Backward Compatibility |
| `test_context_with_embedding_relevance` | Embedding-based relevance | Integration |
| `test_context_performance_benchmark` | Generation latency <50ms | Performance |

---

## 3. Quality Gate Criteria

### 3.1 Exit Criteria

| ID | Criteria | Test | Target | Priority |
|----|----------|------|--------|----------|
| **LENS-001** | Token counting accuracy | Compare with tiktoken ground truth | >95% accuracy | CRITICAL |
| **LENS-002** | Relevance scoring accuracy | Validate against ground truth rankings | >80% correlation | HIGH |
| **PERF-002** | Digest generation latency | 95th percentile benchmark | <50ms | CRITICAL |
| **PERF-004** | Memory footprint | Context state size | <1MB | HIGH |
| **BC-002** | Backward compatibility | Existing digest() calls | 100% pass | CRITICAL |
| **THREAD-002** | Thread safety | Concurrent context generation | 100 threads | CRITICAL |

### 3.2 Test Coverage Requirements

| Component | Min Coverage | Target Coverage |
|-----------|--------------|-----------------|
| TokenCounter | 90% | 95% |
| ContextLens | 90% | 95% |
| EmbeddingRelevance | 85% | 90% |
| NexusService Extension | 95% | 100% |
| **Overall** | **90%** | **95%** |

---

## 4. Test Strategy

### 4.1 Test Matrix

| Test File | Functions | Coverage Focus | Priority |
|-----------|-----------|----------------|----------|
| `test_token_counter.py` | 15 | Token counting accuracy, tiktoken integration | CRITICAL |
| `test_context_lens.py` | 25 | Context generation, relevance scoring | CRITICAL |
| `test_relevance.py` | 15 | Embedding scoring, fallback behavior | HIGH |
| `test_nexus_service.py` | +10 | Nexus extension, integration | CRITICAL |
| `test_context_integration.py` | 20 | End-to-end integration | HIGH |
| `test_performance_benchmarks.py` | 10 | Latency, memory benchmarks | MEDIUM |
| **Total** | **95** | **Full coverage** | |

### 4.2 Performance Benchmarks

**Benchmark Harness:** `tests/unit/state/test_performance_benchmarks.py`

| Benchmark | Target | Measurement |
|-----------|--------|-------------|
| Token counting (100 events) | <10ms | Average latency |
| Context generation (50 events) | <50ms | 95th percentile |
| Relevance scoring (100 events) | <100ms | With embeddings |
| Relevance scoring fallback | <20ms | Keyword-based |
| Memory per context | <1MB | Peak allocation |
| Concurrent generation (50 threads) | <100ms | Average latency |

### 4.3 Thread Safety Verification

| Test | Threads | Operations | Target |
|------|---------|------------|--------|
| Concurrent token counting | 100 | 1000 counts | 100% pass |
| Concurrent context generation | 50 | 100 contexts | 100% pass |
| Concurrent relevance scoring | 100 | 500 scores | 100% pass |
| Mixed operations stress | 150 | 1000 ops | 100% pass |

---

## 5. Risk Analysis

### 5.1 Active Risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R2.5 | Embedding model availability | MEDIUM | LOW | Graceful fallback to keyword scoring | senior-developer |
| R2.6 | Token counting variance | MEDIUM | LOW | tiktoken integration, fallback estimation | senior-developer |
| R2.7 | Performance regression | MEDIUM | MEDIUM | Early benchmarking, caching | senior-developer |
| R2.8 | Memory bloat | LOW | MEDIUM | Lazy initialization, cleanup | senior-developer |
| R2.9 | Backward compatibility break | LOW | HIGH | Extensive BC testing, deprecation warnings | senior-developer |

### 5.2 Risk Triggers

| Risk | Trigger | Action |
|------|---------|--------|
| R2.5 | sentence-transformers import fails | Log warning, enable fallback mode |
| R2.6 | Token variance >20% from tiktoken | Increase tiktoken usage, adjust estimation |
| R2.7 | Digest latency >100ms | Profile and optimize hot paths |
| R2.8 | Context memory >2MB | Add caching, reduce event caching |
| R2.9 | Any BC test failure | Immediate fix, add deprecation period |

---

## 6. Integration Points

### 6.1 Phase 1 Integration

| Phase 1 Component | Sprint 2 Extension | Integration Method |
|-------------------|-------------------|-------------------|
| `NexusService.get_digest()` | `get_optimized_context()` | Method overload (new method) |
| `NexusService.get_chronicle_digest()` | `get_enhanced_chronicle_digest()` | Method overload (new method) |
| `AuditLogger.get_digest()` | ContextLens formatting | Delegation pattern |
| `SupervisorAgent.review()` | Uses enhanced digest | Direct usage |

### 6.2 Phase 2 Sprint 1 Integration

| Sprint 1 Component | Sprint 2 Integration | Usage Pattern |
|-------------------|---------------------|---------------|
| `SupervisorAgent.review()` | ContextLens for context | `get_optimized_context(agent_id="SupervisorAgent")` |
| `ReviewOps.get_chronicle_digest()` | Enhanced digest | `get_enhanced_chronicle_digest()` |

### 6.3 Backward Compatibility

Sprint 2 maintains backward compatibility:

- Existing `get_digest()` calls continue to work unchanged
- Existing `get_chronicle_digest()` calls continue to work unchanged
- New methods are additive, not replacements
- tiktoken is optional (graceful fallback)
- sentence-transformers is optional (graceful fallback)

---

## 7. Success Metrics

### 7.1 Technical Metrics

| Metric | Baseline (Phase 1) | Target | Measurement |
|--------|-------------------|--------|-------------|
| Token counting variance | ~20% (estimation) | <5% (tiktoken) | Comparison with tiktoken |
| Digest latency | Not benchmarked | <50ms (95th %ile) | Performance benchmarks |
| Relevance accuracy | N/A (rule-based) | >80% (embedding) | Ground truth correlation |
| Memory per context | Not measured | <1MB | Memory profiling |

### 7.2 Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Test coverage | 95% | `pytest --cov` |
| Test pass rate | 100% | All tests pass |
| Thread safety | 100 threads | Concurrent tests |
| Performance | All benchmarks green | perf tests |
| Backward compatibility | 100% | BC test suite |

---

## 8. Effort Estimates

### 8.1 Developer Effort

| Week | Focus | Tasks | Deliverable |
|------|-------|-------|-------------|
| **Week 3** | TokenCounter + ContextLens | Implement TokenCounter, ContextLens core | token_counter.py, context_lens.py |
| **Week 4** | EmbeddingRelevance + Tests | Implement relevance, unit tests | relevance.py, test files |
| **Week 5** | Integration + Performance | Nexus extension, benchmarks | nexus.py extension, perf tests |
| **Week 6** | Quality Gate 2 Prep | Full test suite, documentation | QG2 assessment |

**Total Effort:** 4 weeks senior-developer, 1 week testing-quality-specialist

### 8.2 Testing Effort

| Sprint | Testing FTE | Focus |
|--------|-------------|-------|
| Week 3 | 0.25 | TokenCounter tests |
| Week 4 | 0.5 | ContextLens + Relevance tests |
| Week 5 | 0.5 | Integration + Performance tests |
| Week 6 | 0.25 | QG2 validation |

**Total Testing Effort:** 1.5 weeks

---

## 9. File Modification Summary

| File | Change Type | LOC Estimate | Tests | Sprint Week |
|------|-------------|--------------|-------|-------------|
| `src/gaia/state/token_counter.py` | **NEW** | ~150 | 15 | Week 3 |
| `src/gaia/state/context_lens.py` | **NEW** | ~300 | 25 | Week 3-4 |
| `src/gaia/state/relevance.py` | **NEW** | ~200 | 15 | Week 4 |
| `src/gaia/state/nexus.py` | MODIFY | +100 | +10 | Week 3-5 |
| `tests/unit/state/test_token_counter.py` | **NEW** | N/A | 15 | Week 3 |
| `tests/unit/state/test_context_lens.py` | **NEW** | N/A | 25 | Week 4 |
| `tests/unit/state/test_relevance.py` | **NEW** | N/A | 15 | Week 4 |
| `tests/unit/state/test_context_integration.py` | **NEW** | N/A | 20 | Week 5 |
| `tests/unit/state/test_performance_benchmarks.py` | **NEW** | N/A | 10 | Week 5 |

---

## 10. Dependencies

### 10.1 Internal Dependencies

```
Phase 1 Complete (NexusService, AuditLogger)
       │
       ▼
┌─────────────────┐
│  TokenCounter   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  ContextLens    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Embedding      │
│  Relevance      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  NexusService   │
│  (extended)     │
└─────────────────┘
```

### 10.2 External Dependencies

| Dependency | Purpose | Version | Installation | Optional |
|------------|---------|---------|--------------|----------|
| `tiktoken` | Token counting | >=0.5.0 | `pip install tiktoken` | Yes (fallback available) |
| `sentence-transformers` | Embedding model | >=2.2.0 | `pip install sentence-transformers` | Yes (fallback available) |
| `numpy` | Vector operations | >=1.24.0 | `pip install numpy` | Yes (bundled with sentence-transformers) |

---

## 11. Handoff Notes

### 11.1 For senior-developer

**Implementation Notes:**
1. Start with TokenCounter (Week 3) - foundational for accurate token counting
2. Make tiktoken optional with graceful fallback
3. Implement ContextLens with rule-based scoring first
4. Add EmbeddingRelevance as optional enhancement
5. Benchmark digest latency early (target: <50ms)

**Key Design Decisions:**
- TokenCounter: tiktoken primary, fallback estimation
- ContextLens: relevance scoring additive, not breaking
- EmbeddingRelevance: sentence-transformers optional, keyword fallback
- NexusService: new methods additive, existing methods unchanged

### 11.2 For testing-quality-specialist

**Test Priorities:**
1. Token counting accuracy (>95% vs tiktoken)
2. Performance benchmarks (<50ms digest latency)
3. Thread safety (100+ concurrent operations)
4. Backward compatibility (existing digest() calls)

**Test Infrastructure:**
- pytest 8.4.2+
- pytest-benchmark for performance
- pytest-asyncio for async tests
- pytest-cov for coverage
- tiktoken for validation
- sentence-transformers (optional) for embedding tests

---

## 12. Approval & Sign-Off

**Prepared By:** Dr. Sarah Kim, planning-analysis-strategist
**Date:** 2026-04-06
**Next Action:** senior-developer begins Sprint 2 Week 3

### Sign-Off Checklist

- [x] Technical feasibility confirmed
- [x] Resource allocation confirmed
- [x] Risk assessment acceptable
- [x] Test strategy comprehensive
- [x] Quality criteria defined
- [ ] **Team approval to begin Sprint 2**

---

**END OF SPECIFICATION**

**Distribution:** GAIA Development Team
**Review Cadence:** Weekly status reviews
**Version History:**
- v1.0: Initial Sprint 2 specification (2026-04-06)
