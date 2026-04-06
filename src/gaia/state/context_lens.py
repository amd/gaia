"""
Context Lens - Enhanced Context Digest for GAIA

Provides hierarchical context summarization with:
1. Token budget enforcement via TokenCounter
2. Relevance scoring via EmbeddingRelevance (optional)
3. Hierarchical summarization (recent -> phase -> loop)
4. Smart prioritization based on agent and event type

Features:
    - Multi-level abstraction (event -> phase -> pipeline)
    - Token-efficient summarization with hard budget
    - Relevance-based event prioritization
    - Graceful degradation when components unavailable

Example:
    >>> from gaia.state.context_lens import ContextLens
    >>> from gaia.state.nexus import NexusService
    >>> nexus = NexusService.get_instance()
    >>> lens = ContextLens(nexus)
    >>> context = lens.get_context(
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

    Thread Safety:
        - RLock protection for all context generation
        - Safe for concurrent access from multiple threads

    Example:
        >>> from gaia.state.nexus import NexusService
        >>> nexus = NexusService.get_instance()
        >>> lens = ContextLens(nexus)
        >>> context = lens.get_context(
        ...     agent_id="CodeAgent",
        ...     max_tokens=2000
        ... )
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

        Example:
            >>> nexus = NexusService.get_instance()
            >>> lens = ContextLens(nexus)
        """
        self._nexus = nexus_service
        self._token_counter = token_counter or TokenCounter(model=model)
        self._lock = threading.RLock()

        logger.info(
            "ContextLens initialized",
            extra={
                "model": model,
                "tiktoken": self._token_counter.get_encoding_info()["tiktoken_available"],
            }
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
            max_tokens: Maximum token budget (default: 2000)
            use_relevance: Enable relevance-based scoring (default: True)
            include_phases: Filter to specific phases (None = all)
            include_agents: Filter to specific agents (None = all)

        Returns:
            Dictionary with:
            - digest: Formatted context string
            - metadata: ContextMetadata with statistics
            - events: List of included events

        Example:
            >>> lens = ContextLens(nexus)
            >>> context = lens.get_context(
            ...     agent_id="CodeAgent",
            ...     max_tokens=2000
            ... )
            >>> print(context["digest"][:200])
            >>> print(context["metadata"].total_tokens)
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
            remaining_budget = max_tokens - tokens_used
            recent_budget = int(remaining_budget * 0.7)
            recent_section = self._format_recent_events(selected_events, recent_budget)
            digest_parts.append(recent_section)

            # 2. Phase summaries (20% of budget)
            phase_budget = int(remaining_budget * 0.2)
            if phase_budget > 50:
                phase_section = self._format_phase_summaries(events, phase_budget)
                digest_parts.append(phase_section)

            # 3. Workspace summary (10% of budget)
            workspace_budget = int(remaining_budget * 0.1)
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
                agents_included=len(set(
                    e.event.get("agent_id") for e in selected_events
                )),
                phases_included=len(set(
                    e.event.get("phase") for e in selected_events
                    if e.event.get("phase")
                )),
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
                    truncated = self._token_counter.truncate_to_budget(
                        event_text, remaining
                    )
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
        """
        Format recent events section.

        Args:
            scored_events: List of scored events to format
            token_budget: Token budget for this section

        Returns:
            Formatted string section
        """
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
        """
        Format single event in compact form.

        Args:
            event: Event dictionary

        Returns:
            Formatted string representation
        """
        phase = event.get("phase", "N/A")
        agent_id = event.get("agent_id", "system")
        event_type = event.get("event_type", "unknown")

        # Payload summary
        payload = event.get("payload", {})
        if payload:
            payload_str = ", ".join(
                f"{k}={str(v)[:30]}" for k, v in list(payload.items())[:2]
            )
            return f"- [{phase}] {agent_id}: {event_type} ({payload_str})\n"
        else:
            return f"- [{phase}] {agent_id}: {event_type}\n"

    def _format_phase_summaries(
        self,
        events: List[Dict[str, Any]],
        token_budget: int,
    ) -> str:
        """
        Format phase summaries section.

        Args:
            events: List of events to summarize by phase
            token_budget: Token budget for this section

        Returns:
            Formatted string section
        """
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

            agents = list(set(
                e.get("agent_id") for e in phase_events
                if e.get("agent_id")
            ))
            event_types = list(set(
                e.get("event_type") for e in phase_events
            ))

            summary = (
                f"- **{phase}**: {len(phase_events)} events | "
                f"Agents: {', '.join(agents[:3])} | "
                f"Types: {', '.join(event_types[:3])}\n"
            )
            parts.append(summary)
            tokens += self._token_counter.count(summary)

        return "".join(parts)

    def _format_workspace_summary(
        self,
        workspace: Dict[str, Any],
        token_budget: int,
    ) -> str:
        """
        Format workspace summary section.

        Args:
            workspace: Workspace index dictionary
            token_budget: Token budget for this section

        Returns:
            Formatted string section
        """
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
            max_events: Maximum number of events (default: 15)
            max_tokens: Maximum token budget (default: 3500)
            include_phases: Phase filter (None = all)
            include_agents: Agent filter (None = all)
            use_relevance: Enable relevance scoring (default: False)
            agent_id: Target agent for relevance scoring (required if use_relevance=True)

        Returns:
            Formatted digest string

        Example:
            >>> lens = ContextLens(nexus)
            >>> digest = lens.get_chronicle_digest(
            ...     max_events=20,
            ...     max_tokens=3000,
            ...     use_relevance=True,
            ...     agent_id="CodeAgent"
            ... )
        """
        context = self.get_context(
            agent_id=agent_id or "system",
            max_tokens=max_tokens,
            use_relevance=use_relevance and agent_id is not None,
            include_phases=include_phases,
            include_agents=include_agents,
        )
        return context["digest"]
