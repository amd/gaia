"""
GAIA Decision Engine

Determines pipeline progression based on quality scores and defects.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Dict, List, Any, Optional

from gaia.utils.logging import get_logger
from gaia.exceptions import QualityGateFailedError


logger = get_logger(__name__)


class DecisionType(Enum):
    """
    Decision types for pipeline progression.

    Decision types determine what happens next in the pipeline:
    - CONTINUE: Proceed to next phase
    - LOOP_BACK: Return to previous phase with defects
    - PAUSE: Wait for user input
    - COMPLETE: Pipeline finished successfully
    - FAIL: Pipeline failed
    """

    CONTINUE = auto()      # Continue to next phase
    LOOP_BACK = auto()     # Return to planning with defects
    PAUSE = auto()         # Wait for user input
    COMPLETE = auto()      # Pipeline complete
    FAIL = auto()          # Pipeline failed

    def is_terminal(self) -> bool:
        """Check if decision is terminal (ends pipeline)."""
        return self in {DecisionType.COMPLETE, DecisionType.FAIL}

    def requires_action(self) -> bool:
        """Check if decision requires external action."""
        return self in {DecisionType.PAUSE, DecisionType.FAIL}


@dataclass
class Decision:
    """
    Decision output from the engine.

    Attributes:
        decision_type: Type of decision
        reason: Human-readable reason
        target_phase: Target phase for LOOP_BACK decisions
        defects: Defects influencing the decision
        metadata: Additional decision metadata
        made_at: When decision was made
    """

    decision_type: DecisionType
    reason: str
    target_phase: Optional[str] = None
    defects: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    made_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "decision_type": self.decision_type.name,
            "reason": self.reason,
            "target_phase": self.target_phase,
            "defects_count": len(self.defects),
            "defects": self.defects,
            "metadata": self.metadata,
            "made_at": self.made_at.isoformat(),
        }

    @classmethod
    def continue_decision(
        cls,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Decision":
        """Create a CONTINUE decision."""
        return cls(
            decision_type=DecisionType.CONTINUE,
            reason=reason,
            metadata=metadata or {},
        )

    @classmethod
    def loop_back_decision(
        cls,
        reason: str,
        target_phase: str,
        defects: List[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Decision":
        """Create a LOOP_BACK decision."""
        return cls(
            decision_type=DecisionType.LOOP_BACK,
            reason=reason,
            target_phase=target_phase,
            defects=defects,
            metadata=metadata or {},
        )

    @classmethod
    def pause_decision(
        cls,
        reason: str,
        defects: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Decision":
        """Create a PAUSE decision."""
        return cls(
            decision_type=DecisionType.PAUSE,
            reason=reason,
            defects=defects or [],
            metadata=metadata or {},
        )

    @classmethod
    def complete_decision(
        cls,
        reason: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Decision":
        """Create a COMPLETE decision."""
        return cls(
            decision_type=DecisionType.COMPLETE,
            reason=reason,
            metadata=metadata or {},
        )

    @classmethod
    def fail_decision(
        cls,
        reason: str,
        defects: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Decision":
        """Create a FAIL decision."""
        return cls(
            decision_type=DecisionType.FAIL,
            reason=reason,
            defects=defects or [],
            metadata=metadata or {},
        )


class DecisionEngine:
    """
    Determines pipeline progression based on quality scores and defects.

    The DecisionEngine implements the core decision logic:
    1. If quality >= threshold -> Continue to next phase (or Complete if final)
    2. If quality < threshold AND iterations < max -> Loop back with defects
    3. If quality < threshold AND iterations >= max -> Fail
    4. If critical defect found -> Pause for user input

    Example:
        >>> engine = DecisionEngine({"critical_patterns": ["security"]})
        >>> decision = engine.evaluate(
        ...     phase_name="DEVELOPMENT",
        ...     quality_score=0.85,
        ...     quality_threshold=0.90,
        ...     defects=[{"description": "Minor issue"}],
        ...     iteration=1,
        ...     max_iterations=3,
        ...     is_final_phase=False
        ... )
        >>> print(decision.decision_type)
        DecisionType.LOOP_BACK
    """

    # Default critical patterns that trigger pause
    DEFAULT_CRITICAL_PATTERNS = [
        "security vulnerability",
        "data loss",
        "breaking change",
        "compliance violation",
        "security",
        "vulnerability",
        "exploit",
        "injection",
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize decision engine.

        Args:
            config: Configuration dictionary with:
                - critical_patterns: List of critical defect patterns
        """
        self.config = config or {}
        self._critical_patterns = self.config.get(
            "critical_patterns",
            self.DEFAULT_CRITICAL_PATTERNS,
        )

        logger.info(
            "DecisionEngine initialized",
            extra={"critical_patterns_count": len(self._critical_patterns)},
        )

    def evaluate(
        self,
        phase_name: str,
        quality_score: float,
        quality_threshold: float,
        defects: List[Dict[str, Any]],
        iteration: int,
        max_iterations: int,
        is_final_phase: bool,
    ) -> Decision:
        """
        Evaluate current state and determine next action.

        This is the main decision method. It evaluates:
        1. Critical defects (pause for review)
        2. Quality threshold (continue or loop back)
        3. Max iterations (fail if exceeded)

        Args:
            phase_name: Current phase name
            quality_score: Overall quality score (0-1)
            quality_threshold: Required threshold (0-1)
            defects: List of identified defects
            iteration: Current iteration count
            max_iterations: Maximum allowed iterations
            is_final_phase: Whether this is the final phase

        Returns:
            Decision object with progression instruction
        """
        logger.info(
            f"Evaluating decision for phase {phase_name}",
            extra={
                "phase": phase_name,
                "quality_score": quality_score,
                "threshold": quality_threshold,
                "iteration": iteration,
                "defects_count": len(defects),
            },
        )

        # Check for critical defects first
        critical_defects = self._find_critical_defects(defects)
        if critical_defects:
            decision = Decision.pause_decision(
                reason=f"Critical defects require user review: {[d['description'] for d in critical_defects]}",
                defects=critical_defects,
                metadata={
                    "critical": True,
                    "critical_count": len(critical_defects),
                },
            )
            logger.warning(
                f"Decision: PAUSE due to critical defects",
                extra={"critical_count": len(critical_defects)},
            )
            return decision

        # Check if quality threshold met
        if quality_score >= quality_threshold:
            if is_final_phase:
                decision = Decision.complete_decision(
                    reason=f"Quality threshold ({quality_threshold:.2f}) met in final phase with score {quality_score:.2f}",
                    metadata={
                        "final_score": quality_score,
                        "threshold": quality_threshold,
                    },
                )
                logger.info(f"Decision: COMPLETE - quality threshold met")
            else:
                decision = Decision.continue_decision(
                    reason=f"Quality threshold ({quality_threshold:.2f}) met with score {quality_score:.2f}, proceeding to next phase",
                    metadata={
                        "score": quality_score,
                        "threshold": quality_threshold,
                    },
                )
                logger.info(f"Decision: CONTINUE to next phase")
            return decision

        # Quality below threshold
        if max_iterations > 0 and iteration >= max_iterations:
            decision = Decision.fail_decision(
                reason=f"Max iterations ({max_iterations}) reached - failed to meet quality threshold {quality_threshold:.2f} (final score: {quality_score:.2f})",
                defects=defects,
                metadata={
                    "final_score": quality_score,
                    "threshold": quality_threshold,
                    "iterations": iteration,
                },
            )
            logger.warning(
                f"Decision: FAIL - max iterations exceeded",
                extra={"iterations": iteration, "score": quality_score},
            )
            return decision

        # Loop back with defects for another iteration
        decision = Decision.loop_back_decision(
            reason=f"Quality score ({quality_score:.2f}) below threshold ({quality_threshold:.2f}), looping back with {len(defects)} defects",
            target_phase="PLANNING",
            defects=defects,
            metadata={
                "score": quality_score,
                "threshold": quality_threshold,
                "iteration": iteration,
                "defect_count": len(defects),
            },
        )
        logger.info(
            f"Decision: LOOP_BACK to PLANNING",
            extra={"defect_count": len(defects)},
        )
        return decision

    def _find_critical_defects(
        self,
        defects: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Identify critical defects requiring user review.

        Checks defect descriptions and categories against
        critical patterns.

        Args:
            defects: List of defects to check

        Returns:
            List of critical defects
        """
        critical = []

        for defect in defects:
            description = defect.get("description", "").lower()
            category = defect.get("category", "").lower()
            severity = defect.get("severity", "").lower()

            # Check severity first
            if severity == "critical":
                critical.append(defect)
                continue

            # Check patterns
            is_critical = False
            for pattern in self._critical_patterns:
                if pattern in description or pattern in category:
                    is_critical = True
                    break

            if is_critical:
                critical.append(defect)

        return critical

    def evaluate_simple(
        self,
        quality_score: float,
        quality_threshold: float,
        has_critical_defects: bool = False,
    ) -> DecisionType:
        """
        Simple evaluation returning just decision type.

        Useful for quick checks without full context.

        Args:
            quality_score: Quality score (0-1)
            quality_threshold: Required threshold (0-1)
            has_critical_defects: Whether critical defects exist

        Returns:
            DecisionType
        """
        if has_critical_defects:
            return DecisionType.PAUSE

        if quality_score >= quality_threshold:
            return DecisionType.CONTINUE

        return DecisionType.LOOP_BACK

    def should_loop_back(
        self,
        quality_score: float,
        quality_threshold: float,
        iteration: int,
        max_iterations: int,
    ) -> tuple[bool, str]:
        """
        Determine if pipeline should loop back.

        Args:
            quality_score: Quality score (0-1)
            quality_threshold: Required threshold (0-1)
            iteration: Current iteration
            max_iterations: Maximum iterations

        Returns:
            Tuple of (should_loop_back, reason)
        """
        if quality_score >= quality_threshold:
            return False, "Quality threshold met"

        if max_iterations > 0 and iteration >= max_iterations:
            return False, f"Max iterations ({max_iterations}) exceeded"

        return True, f"Quality {quality_score:.2f} below threshold {quality_threshold:.2f}"

    def get_statistics(self) -> Dict[str, Any]:
        """Get engine configuration statistics."""
        return {
            "critical_patterns": self._critical_patterns,
            "critical_patterns_count": len(self._critical_patterns),
        }
