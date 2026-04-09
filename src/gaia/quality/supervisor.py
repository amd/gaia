# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Quality Supervisor Agent for GAIA Pipeline.

This module implements the SupervisorAgent responsible for quality review
orchestration, consensus aggregation, and pipeline LOOP_BACK decisions.

The supervisor:
1. Retrieves chronicle digest from previous iteration
2. Analyzes quality scores and validator feedback
3. Makes LOOP_FORWARD/LOOP_BACK decisions with rationale
4. Commits decision to Chronicle via NexusService

Features:
    - Thread-safe concurrent access (RLock)
    - Deep copy of defects and consensus_data for mutation safety
    - Integration with PipelineEngine decision flow
    - Error handling and graceful degradation
    - Comprehensive logging and audit trail

Example:
    >>> from gaia.quality.supervisor import SupervisorAgent
    >>> agent = SupervisorAgent()
    >>> decision = await agent.make_quality_decision(
    ...     quality_score=0.85,
    ...     quality_threshold=0.90,
    ...     defects=[{"description": "Minor issue"}],
    ...     iteration=1,
    ...     max_iterations=5
    ... )
    >>> print(decision["decision_type"])
    LOOP_BACK
"""

import asyncio
import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import ToolRegistry, tool as register_tool
from gaia.state.nexus import NexusService
from gaia.tools.review_ops import (
    clear_review_history,
    get_chronicle_digest,
    get_review_history,
    get_review_history_count,
    review_consensus,
)
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Decision Type Enum
# =============================================================================

class SupervisorDecisionType(Enum):
    """Decision types for supervisor quality decisions.

    Values:
        LOOP_FORWARD: Continue to next phase (quality acceptable)
        LOOP_BACK: Return to previous phase with defects (quality insufficient)
        PAUSE: Wait for user input (critical issues detected)
        FAIL: Pipeline failed (max iterations exceeded)
    """
    LOOP_FORWARD = auto()
    LOOP_BACK = auto()
    PAUSE = auto()
    FAIL = auto()

    def is_terminal(self) -> bool:
        """Check if decision is terminal (ends pipeline)."""
        return self in {SupervisorDecisionType.PAUSE, SupervisorDecisionType.FAIL}

    def requires_loop_back(self) -> bool:
        """Check if decision requires looping back."""
        return self == SupervisorDecisionType.LOOP_BACK


# =============================================================================
# Decision Dataclass
# =============================================================================

@dataclass
class SupervisorDecision:
    """Quality decision output from supervisor.

    Attributes:
        decision_type: Type of decision (LOOP_FORWARD, LOOP_BACK, etc.)
        reason: Human-readable explanation
        quality_score: Current quality score (0-100)
        threshold: Required threshold (0-100)
        defects: List of defects influencing decision
        consensus_data: Consensus analysis results
        chronicle_digest: Chronicle context snapshot
        rationale: Detailed decision rationale
        metadata: Additional decision metadata
        timestamp: When decision was made

    Example:
        >>> decision = SupervisorDecision(
        ...     decision_type=SupervisorDecisionType.LOOP_BACK,
        ...     reason="Quality score 85% below threshold 90%",
        ...     quality_score=85.0,
        ...     threshold=90.0,
        ...     defects=[{"description": "Missing tests"}]
        ... )
        >>> decision.to_dict()["decision_type"]
        'LOOP_BACK'
    """
    decision_type: SupervisorDecisionType
    reason: str
    quality_score: float
    threshold: float
    defects: List[Dict[str, Any]] = field(default_factory=list)
    consensus_data: Optional[Dict[str, Any]] = None
    chronicle_digest: Optional[str] = None
    rationale: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        """Convert decision to dictionary for serialization.

        Returns:
            Dictionary representation of decision
        """
        return {
            "decision_type": self.decision_type.name,
            "reason": self.reason,
            "quality_score": self.quality_score,
            "threshold": self.threshold,
            "defects_count": len(self.defects),
            "defects": self.defects,
            "consensus_data": self.consensus_data,
            "chronicle_digest": self.chronicle_digest,
            "rationale": self.rationale,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_pipeline_decision(self) -> Dict[str, Any]:
        """Convert to PipelineEngine decision format.

        Returns:
            Dictionary compatible with PipelineEngine decision handling
        """
        # Map supervisor decision type to pipeline decision type
        type_mapping = {
            "LOOP_FORWARD": "CONTINUE",
            "LOOP_BACK": "LOOP_BACK",
            "PAUSE": "PAUSE",
            "FAIL": "FAIL",
        }

        return {
            "decision_type": type_mapping.get(self.decision_type.name, "LOOP_BACK"),
            "reason": self.reason,
            "quality_score": self.quality_score,
            "threshold": self.threshold,
            "defects": self.defects,
            "target_phase": "PLANNING" if self.decision_type == SupervisorDecisionType.LOOP_BACK else None,
            "metadata": {
                **self.metadata,
                "supervisor_decision": True,
                "consensus_reached": self.consensus_data.get("consensus_reached", False) if self.consensus_data else False,
            },
        }


# =============================================================================
# SupervisorAgent Class
# =============================================================================

class SupervisorAgent(Agent):
    """
    Quality Supervisor Agent for GAIA Pipeline.

    The SupervisorAgent is responsible for:
    1. Retrieving chronicle digest from previous iteration
    2. Analyzing quality scores and validator feedback
    3. Making LOOP_FORWARD/LOOP_BACK decisions with rationale
    4. Committing decision to Chronicle via NexusService

    Thread Safety:
        - Uses RLock for all state mutations
        - Deep copy of defects and consensus_data to prevent external mutation
        - Thread-safe tool execution via ToolRegistry

    Integration Points:
        - NexusService for Chronicle access
        - PipelineEngine for decision injection
        - QualityScorer for score retrieval
        - AgentRegistry for agent coordination

    Example:
        >>> agent = SupervisorAgent()
        >>> decision = await agent.make_quality_decision(
        ...     quality_score=0.85,
        ...     quality_threshold=0.90,
        ...     defects=[{"description": "Missing tests"}],
        ...     iteration=1,
        ...     max_iterations=5
        ... )
        >>> print(decision.decision_type.name)
        LOOP_BACK
    """

    # Quality thresholds
    DEFAULT_MIN_ACCEPTABLE_SCORE = 0.85
    DEFAULT_TARGET_SCORE = 0.90
    DEFAULT_CRITICAL_DEFECT_THRESHOLD = 1
    DEFAULT_MAX_DEFECTS_ALLOWED = 5
    DEFAULT_MAX_REVIEW_ITERATIONS = 3
    DEFAULT_MIN_CONSENSUS_THRESHOLD = 0.75

    def __init__(
        self,
        min_acceptable_score: Optional[float] = None,
        target_score: Optional[float] = None,
        critical_defect_threshold: Optional[int] = None,
        max_defects_allowed: Optional[int] = None,
        max_review_iterations: Optional[int] = None,
        min_consensus_threshold: Optional[float] = None,
        model_id: str = "Qwen3.5-35B-A3B-GGUF",
        debug: bool = False,
        silent_mode: bool = False,
        skip_lemonade: bool = True,  # Supervisor doesn't need LLM by default
        allowed_tools: Optional[List[str]] = None,
        **kwargs,
    ):
        """
        Initialize the Quality Supervisor Agent.

        Args:
            min_acceptable_score: Minimum acceptable quality score (0-1)
            target_score: Target quality score (0-1)
            critical_defect_threshold: Number of critical defects that trigger pause
            max_defects_allowed: Maximum defects before forcing loop back
            max_review_iterations: Maximum review iterations before fail
            min_consensus_threshold: Minimum consensus ratio for agreement
            model_id: LLM model to use (default: Qwen3.5-35B-A3B-GGUF)
            debug: Enable debug logging
            silent_mode: Suppress console output
            skip_lemonade: Skip Lemonade server initialization (default: True)
            allowed_tools: List of allowed tool names
            **kwargs: Additional agent initialization parameters
        """
        # Initialize base agent
        super().__init__(
            model_id=model_id,
            debug=debug,
            silent_mode=silent_mode,
            skip_lemonade=skip_lemonade,
            allowed_tools=allowed_tools,
            **kwargs,
        )

        # Quality thresholds with defaults
        self._min_acceptable_score = min_acceptable_score or self.DEFAULT_MIN_ACCEPTABLE_SCORE
        self._target_score = target_score or self.DEFAULT_TARGET_SCORE
        self._critical_defect_threshold = critical_defect_threshold or self.DEFAULT_CRITICAL_DEFECT_THRESHOLD
        self._max_defects_allowed = max_defects_allowed or self.DEFAULT_MAX_DEFECTS_ALLOWED
        self._max_review_iterations = max_review_iterations or self.DEFAULT_MAX_REVIEW_ITERATIONS
        self._min_consensus_threshold = min_consensus_threshold or self.DEFAULT_MIN_CONSENSUS_THRESHOLD

        # Thread safety
        self._state_lock = threading.RLock()

        # Decision history
        self._decision_history: List[SupervisorDecision] = []
        self._review_iterations = 0

        # NexusService integration
        self._nexus: Optional[NexusService] = None

        # Register tools
        self._register_tools()

        logger.info(
            "SupervisorAgent initialized",
            extra={
                "target_score": self._target_score,
                "min_acceptable_score": self._min_acceptable_score,
                "skip_lemonade": skip_lemonade,
            }
        )

    def _register_tools(self) -> None:
        """Register supervisor tools with ToolRegistry."""
        try:
            registry = ToolRegistry.get_instance()

            # Register review_consensus tool
            if not registry.has_tool("review_consensus"):
                registry.register(
                    name="review_consensus",
                    func=review_consensus,
                    description="Aggregate multiple quality reviews into consensus decision"
                )

            # Register get_chronicle_digest tool
            if not registry.has_tool("get_chronicle_digest"):
                registry.register(
                    name="get_chronicle_digest",
                    func=get_chronicle_digest,
                    description="Retrieve Chronicle digest from NexusService"
                )

            # Register get_review_history tool
            if not registry.has_tool("get_review_history"):
                registry.register(
                    name="get_review_history",
                    func=get_review_history,
                    description="Retrieve past quality decisions and reviews"
                )

            # Register workspace_validate tool
            if not registry.has_tool("workspace_validate"):
                registry.register(
                    name="workspace_validate",
                    func=workspace_validate,
                    description="Validate current workspace state"
                )

            logger.debug("SupervisorAgent tools registered")

        except Exception as exc:
            logger.exception(f"Failed to register supervisor tools: {exc}")

    async def make_quality_decision(
        self,
        quality_score: float,
        quality_threshold: float,
        defects: List[Dict[str, Any]],
        iteration: int,
        max_iterations: int,
        reviews: Optional[List[Dict[str, Any]]] = None,
        include_chronicle: bool = True,
    ) -> SupervisorDecision:
        """
        Make quality decision based on scores, defects, and consensus.

        This is the main decision method. It:
        1. Retrieves chronicle digest if requested
        2. Aggregates reviews for consensus (if provided)
        3. Analyzes quality score against threshold
        4. Evaluates defects for critical issues
        5. Makes LOOP_FORWARD/LOOP_BACK/PAUSE/FAIL decision

        Args:
            quality_score: Current quality score (0-100)
            quality_threshold: Required quality threshold (0-100)
            defects: List of defects from quality evaluation
            iteration: Current iteration count
            max_iterations: Maximum allowed iterations
            reviews: Optional list of review dictionaries for consensus
            include_chronicle: Whether to include chronicle context

        Returns:
            SupervisorDecision with full rationale

        Example:
            >>> decision = await agent.make_quality_decision(
            ...     quality_score=85.0,
            ...     quality_threshold=90.0,
            ...     defects=[{"description": "Minor issue", "severity": "low"}],
            ...     iteration=1,
            ...     max_iterations=5
            ... )
            >>> decision.decision_type
            <SupervisorDecisionType.LOOP_BACK: 2>
        """
        with self._state_lock:
            self._review_iterations = iteration

            logger.info(
                f"Making quality decision for iteration {iteration}",
                extra={
                    "quality_score": quality_score,
                    "quality_threshold": quality_threshold,
                    "defects_count": len(defects),
                }
            )

            # Retrieve chronicle digest if requested
            chronicle_digest = None
            if include_chronicle:
                chronicle_result = get_chronicle_digest(
                    max_events=15,
                    max_tokens=3500,
                )
                if chronicle_result.get("status") == "success":
                    chronicle_digest = chronicle_result.get("digest")

            # Aggregate reviews for consensus if provided
            consensus_data = None
            if reviews:
                consensus_result = review_consensus(
                    reviews=reviews,
                    min_consensus=self._min_consensus_threshold,
                )
                consensus_data = consensus_result

            # Analyze and make decision
            decision = self._analyze_and_decide(
                quality_score=quality_score,
                quality_threshold=quality_threshold,
                defects=defects,
                iteration=iteration,
                max_iterations=max_iterations,
                consensus_data=consensus_data,
                chronicle_digest=chronicle_digest,
            )

            # Record decision to history
            self._decision_history.append(decision)

            # Commit to Chronicle
            self._commit_decision_to_chronicle(decision)

            return decision

    def _analyze_and_decide(
        self,
        quality_score: float,
        quality_threshold: float,
        defects: List[Dict[str, Any]],
        iteration: int,
        max_iterations: int,
        consensus_data: Optional[Dict[str, Any]] = None,
        chronicle_digest: Optional[str] = None,
    ) -> SupervisorDecision:
        """
        Analyze quality metrics and make decision.

        Decision logic:
        1. Check for critical defects -> PAUSE
        2. Check max iterations exceeded -> FAIL
        3. Check quality >= threshold -> LOOP_FORWARD
        4. Otherwise -> LOOP_BACK

        Args:
            quality_score: Current quality score
            quality_threshold: Required threshold
            defects: List of defects
            iteration: Current iteration
            max_iterations: Maximum iterations
            consensus_data: Consensus analysis results
            chronicle_digest: Chronicle context

        Returns:
            SupervisorDecision with rationale
        """
        # Deep copy defects and consensus_data to prevent external mutation
        defects_copy = copy.deepcopy(defects)
        consensus_data_copy = copy.deepcopy(consensus_data) if consensus_data else None

        # Handle both 0-1 scale (0.90) and 0-100 scale (90.0) for threshold
        # If threshold > 1, assume it's 0-100 scale; otherwise it's 0-1 scale
        if quality_threshold > 1:
            # Threshold is in 0-100 scale, compare directly
            effective_threshold = quality_threshold
            normalized_score = quality_score
            threshold_display = quality_threshold
        else:
            # Threshold is in 0-1 scale, convert score
            effective_threshold = quality_threshold * 100
            normalized_score = quality_score
            threshold_display = quality_threshold * 100

        # Check for critical defects
        critical_defects = self._find_critical_defects(defects)
        if critical_defects:
            return SupervisorDecision(
                decision_type=SupervisorDecisionType.PAUSE,
                reason=f"Critical defects require review: {[d['description'] for d in critical_defects]}",
                quality_score=quality_score,
                threshold=threshold_display,
                defects=defects_copy,
                consensus_data=consensus_data_copy,
                chronicle_digest=chronicle_digest,
                rationale=self._build_rationale(
                    quality_score=quality_score,
                    quality_threshold=threshold_display,
                    defects=defects_copy,
                    critical_defects=critical_defects,
                    iteration=iteration,
                    decision_type=SupervisorDecisionType.PAUSE,
                    consensus_data=consensus_data_copy,
                ),
                metadata={
                    "critical_defect_count": len(critical_defects),
                    "iteration": iteration,
                },
            )

        # Check max iterations
        if max_iterations > 0 and iteration >= max_iterations:
            return SupervisorDecision(
                decision_type=SupervisorDecisionType.FAIL,
                reason=f"Max iterations ({max_iterations}) exceeded - quality {quality_score:.1f}% below threshold {threshold_display:.1f}%",
                quality_score=quality_score,
                threshold=threshold_display,
                defects=defects_copy,
                consensus_data=consensus_data_copy,
                chronicle_digest=chronicle_digest,
                rationale=self._build_rationale(
                    quality_score=quality_score,
                    quality_threshold=threshold_display,
                    defects=defects_copy,
                    iteration=iteration,
                    max_iterations=max_iterations,
                    decision_type=SupervisorDecisionType.FAIL,
                    consensus_data=consensus_data_copy,
                ),
                metadata={
                    "iteration": iteration,
                    "max_iterations": max_iterations,
                },
            )

        # Check quality threshold
        if normalized_score >= effective_threshold:
            # Check if consensus data suggests improvements needed
            if consensus_data and not consensus_data.get("consensus_reached", True):
                # Consensus not reached but score is good - still loop forward with notes
                return SupervisorDecision(
                    decision_type=SupervisorDecisionType.LOOP_FORWARD,
                    reason=f"Quality threshold met ({quality_score:.1f}% >= {threshold_display:.1f}%), proceeding despite consensus gaps",
                    quality_score=quality_score,
                    threshold=threshold_display,
                    defects=defects_copy,
                    consensus_data=consensus_data_copy,
                    chronicle_digest=chronicle_digest,
                    rationale=self._build_rationale(
                        quality_score=quality_score,
                        quality_threshold=threshold_display,
                        defects=defects_copy,
                        iteration=iteration,
                        decision_type=SupervisorDecisionType.LOOP_FORWARD,
                        consensus_data=consensus_data_copy,
                        note="Proceeding despite insufficient consensus",
                    ),
                    metadata={
                        "iteration": iteration,
                        "consensus_gap_noted": True,
                    },
                )

            return SupervisorDecision(
                decision_type=SupervisorDecisionType.LOOP_FORWARD,
                reason=f"Quality threshold met ({quality_score:.1f}% >= {threshold_display:.1f}%), proceeding to next phase",
                quality_score=quality_score,
                threshold=threshold_display,
                defects=defects_copy,
                consensus_data=consensus_data_copy,
                chronicle_digest=chronicle_digest,
                rationale=self._build_rationale(
                    quality_score=quality_score,
                    quality_threshold=threshold_display,
                    defects=defects_copy,
                    iteration=iteration,
                    decision_type=SupervisorDecisionType.LOOP_FORWARD,
                    consensus_data=consensus_data_copy,
                ),
                metadata={
                    "iteration": iteration,
                    "consensus_reached": consensus_data.get("consensus_reached", True) if consensus_data else True,
                },
            )

        # Quality below threshold - loop back
        return SupervisorDecision(
            decision_type=SupervisorDecisionType.LOOP_BACK,
            reason=f"Quality score ({quality_score:.1f}%) below threshold ({threshold_display:.1f}%), looping back with {len(defects)} defects",
            quality_score=quality_score,
            threshold=threshold_display,
            defects=defects_copy,
            consensus_data=consensus_data_copy,
            chronicle_digest=chronicle_digest,
            rationale=self._build_rationale(
                quality_score=quality_score,
                quality_threshold=threshold_display,
                defects=defects_copy,
                iteration=iteration,
                decision_type=SupervisorDecisionType.LOOP_BACK,
                consensus_data=consensus_data_copy,
            ),
            metadata={
                "iteration": iteration,
                "score_gap": round(effective_threshold - normalized_score, 2),
            },
        )

    def _find_critical_defects(
        self,
        defects: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Identify critical defects requiring immediate attention.

        Critical patterns include:
        - Security vulnerabilities
        - Data loss risks
        - Breaking changes
        - Compliance violations
        - Severity marked as "critical"

        Args:
            defects: List of defects to analyze

        Returns:
            List of critical defects
        """
        critical_patterns = [
            "security",
            "vulnerability",
            "data loss",
            "breaking change",
            "compliance violation",
            "exploit",
            "injection",
            "critical",
        ]

        critical_defects = []

        for defect in defects:
            # Handle non-dict defects gracefully
            if not isinstance(defect, dict):
                continue

            description = defect.get("description", "").lower()
            category = defect.get("category", "").lower()
            severity = defect.get("severity", "").lower()

            # Check severity
            if severity == "critical":
                critical_defects.append(defect)
                continue

            # Check patterns
            for pattern in critical_patterns:
                if pattern in description or pattern in category:
                    critical_defects.append(defect)
                    break

        return critical_defects

    def _build_rationale(
        self,
        quality_score: float,
        quality_threshold: float,
        defects: List[Dict[str, Any]],
        iteration: int,
        decision_type: SupervisorDecisionType,
        consensus_data: Optional[Dict[str, Any]] = None,
        max_iterations: Optional[int] = None,
        critical_defects: Optional[List[Dict[str, Any]]] = None,
        note: Optional[str] = None,
    ) -> str:
        """
        Build detailed decision rationale.

        Args:
            quality_score: Current quality score
            quality_threshold: Required threshold
            defects: List of defects
            iteration: Current iteration
            decision_type: Decision being made
            consensus_data: Consensus analysis results
            max_iterations: Maximum iterations
            critical_defects: Critical defects found
            note: Optional additional note

        Returns:
            Formatted rationale string
        """
        parts = [
            f"Decision: {decision_type.name}",
            f"Iteration: {iteration}" + (f"/{max_iterations}" if max_iterations else ""),
            f"Quality Score: {quality_score:.1f}%" + (f" (Threshold: {quality_threshold*100:.1f}%)" if decision_type == SupervisorDecisionType.LOOP_BACK else ""),
        ]

        if defects:
            parts.append(f"Defects Found: {len(defects)}")
            # Summarize top defects
            for i, defect in enumerate(defects[:3]):
                parts.append(f"  - {defect.get('description', 'Unknown issue')}")

        if consensus_data:
            parts.append(
                f"Consensus: {'Reached' if consensus_data.get('consensus_reached') else 'Not Reached'} "
                f"(Ratio: {consensus_data.get('agreement_ratio', 0):.2f})"
            )

        if critical_defects:
            parts.append(f"Critical Defects: {len(critical_defects)}")

        if note:
            parts.append(f"Note: {note}")

        return " | ".join(parts)

    def _commit_decision_to_chronicle(self, decision: SupervisorDecision) -> Optional[str]:
        """
        Commit decision to Chronicle via NexusService.

        Args:
            decision: Decision to commit

        Returns:
            Event ID if committed, None otherwise
        """
        try:
            if self._nexus is None:
                self._nexus = NexusService.get_instance()

            event_id = self._nexus.commit(
                agent_id="SupervisorAgent",
                event_type="decision_made",
                payload={
                    "decision_type": decision.decision_type.name,
                    "reason": decision.reason,
                    "quality_score": decision.quality_score,
                    "threshold": decision.threshold,
                    "defects_count": len(decision.defects),
                    "iteration": self._review_iterations,
                },
                phase="DECISION",
                loop_id=None,  # Decision is phase-level
            )

            # Also commit LOOP_BACK event if applicable
            if decision.decision_type == SupervisorDecisionType.LOOP_BACK:
                self._nexus.commit(
                    agent_id="SupervisorAgent",
                    event_type="loop_back",
                    payload={
                        "reason": decision.reason,
                        "target_phase": "PLANNING",
                        "defects": decision.defects,
                    },
                    phase="DECISION",
                    loop_id=None,
                )

            logger.debug(
                f"Committed supervisor decision to Chronicle",
                extra={"event_id": event_id, "decision_type": decision.decision_type.name},
            )

            return event_id

        except Exception as exc:
            logger.exception(f"Failed to commit decision to Chronicle: {exc}")
            return None

    def get_decision_history(
        self,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Get decision history.

        Args:
            limit: Maximum number of decisions to return

        Returns:
            List of decision dictionaries (most recent first)
        """
        with self._state_lock:
            decisions = list(reversed(self._decision_history[-limit:]))
            return [d.to_dict() for d in decisions]

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get supervisor agent statistics.

        Returns:
            Dictionary with statistics
        """
        with self._state_lock:
            total_decisions = len(self._decision_history)

            if total_decisions == 0:
                return {
                    "total_decisions": 0,
                    "decisions_by_type": {},
                    "average_quality_score": 0,
                    "total_defects_reviewed": 0,
                }

            # Count by type
            type_counts = {}
            for decision in self._decision_history:
                type_name = decision.decision_type.name
                type_counts[type_name] = type_counts.get(type_name, 0) + 1

            # Calculate averages
            avg_score = sum(d.quality_score for d in self._decision_history) / total_decisions
            total_defects = sum(len(d.defects) for d in self._decision_history)

            return {
                "total_decisions": total_decisions,
                "decisions_by_type": type_counts,
                "average_quality_score": round(avg_score, 2),
                "total_defects_reviewed": total_defects,
                "current_iteration": self._review_iterations,
            }

    def reset(self) -> None:
        """Reset agent state (for testing).

        Thread-safe state reset.
        """
        with self._state_lock:
            self._decision_history.clear()
            self._review_iterations = 0
            self._nexus = None
            logger.info("SupervisorAgent reset")

    def shutdown(self) -> None:
        """Shutdown agent and cleanup resources."""
        self.reset()
        logger.info("SupervisorAgent shutdown complete")


# Import workspace_validate from review_ops if not already imported
try:
    from gaia.tools.review_ops import workspace_validate
except ImportError:
    # Fallback implementation if review_ops not available
    def workspace_validate() -> Dict[str, Any]:
        """Fallback workspace validation."""
        return {
            "status": "success",
            "workspace": {"files": {}, "version": 0},
            "validation": {"valid": True, "issues": []},
            "file_count": 0,
        }
