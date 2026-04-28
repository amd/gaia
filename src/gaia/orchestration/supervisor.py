"""
ProjectSupervisor — Health monitoring and governance for ProjectOrchestrator.

Evaluates each dispatch cycle and produces verdicts:
    CONTINUE  - Normal execution
    PAUSE     - Temporary halt (failure threshold or cascade blocking)
    REMEDIATE - Quality trend declining, needs attention
    ABORT     - Critical failure (max failures exceeded)

Quality review fixes incorporated:
    D-1 (CRITICAL): ALL supervisor calls wrapped in try/except
    D-2 (HIGH): Per-objective failure tracking via _objective_failures Dict
    D-3 (HIGH): max_remediation_depth limit on SupervisorConfig
    D-4 (MEDIUM): min_trend_slope as configurable threshold
    D-6 (MEDIUM): reset() method called at run() start
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from gaia.orchestration.models import (
    DependencyGraph,
    Objective,
    ObjectiveStatus,
    ProjectObjectives,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Enums and Data Types
# ============================================================================


class Verdict(Enum):
    """Supervision verdict after evaluating a dispatch cycle."""

    CONTINUE = "continue"
    PAUSE = "pause"
    REMEDIATE = "remediate"
    ABORT = "abort"


@dataclass
class SupervisorConfig:
    """
    Configuration for ProjectSupervisor.

    Attributes:
        consecutive_failure_threshold: Pause after this many consecutive failures
        max_consecutive_failures: Abort after this many consecutive failures
        quality_window: Rolling window size for quality trend calculation
        quality_decline_threshold: Slope below which REMEDIATE triggers
        health_success_rate_weight: Weight for success rate in health score
        health_quality_weight: Weight for quality trend in health score
        health_dependency_weight: Weight for dependency health in health score
        health_minimum_score: Minimum acceptable composite health score
        max_remediation_depth: Maximum consecutive remediation cycles [FIX D-3]
        min_trend_slope: Minimum absolute slope to trigger REMEDIATE [FIX D-4]
    """

    consecutive_failure_threshold: int = 3
    max_consecutive_failures: int = 5
    quality_window: int = 5
    quality_decline_threshold: float = 0.10
    health_success_rate_weight: float = 0.40
    health_quality_weight: float = 0.30
    health_dependency_weight: float = 0.30
    health_minimum_score: float = 0.50
    max_remediation_depth: int = 2       # [FIX D-3]
    min_trend_slope: float = 0.05        # [FIX D-4]

    def __post_init__(self) -> None:
        """Validate configuration constraints."""
        if self.max_consecutive_failures <= self.consecutive_failure_threshold:
            raise ValueError(
                f"max_consecutive_failures ({self.max_consecutive_failures}) "
                f"must exceed consecutive_failure_threshold "
                f"({self.consecutive_failure_threshold})"
            )
        weight_sum = (
            self.health_success_rate_weight
            + self.health_quality_weight
            + self.health_dependency_weight
        )
        if abs(weight_sum - 1.0) > 1e-9:
            raise ValueError(
                f"Health weights must sum to 1.0, got {weight_sum}"
            )
        if self.max_remediation_depth < 1:
            raise ValueError(
                f"max_remediation_depth must be >= 1, got {self.max_remediation_depth}"
            )
        if self.min_trend_slope < 0:
            raise ValueError(
                f"min_trend_slope must be >= 0, got {self.min_trend_slope}"
            )


@dataclass
class ObjectiveOutcome:
    """
    Record of a single objective execution outcome.

    Attributes:
        objective_id: The objective that was executed
        success: Whether execution succeeded
        quality_score: Optional quality score from the pipeline
        phase: Pipeline phase of the objective
        timestamp: ISO timestamp of the outcome
        error_message: Error description if failed
    """

    objective_id: str
    success: bool
    quality_score: Optional[float] = None
    phase: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    error_message: Optional[str] = None


@dataclass
class HealthScore:
    """
    Composite health score for the project.

    Attributes:
        success_rate: Ratio of successful objectives (0.0 - 1.0)
        quality_trend: Normalized quality trend (-1..1 -> 0..1)
        dependency_health: Ratio of unblocked objectives (0.0 - 1.0)
        composite: Weighted composite score (0.0 - 1.0)
    """

    success_rate: float
    quality_trend: float
    dependency_health: float
    composite: float


@dataclass
class SupervisorState:
    """
    Runtime state of the ProjectSupervisor.

    Attributes:
        outcomes: List of all objective outcomes recorded
        consecutive_failures: Current streak of consecutive failures
        objective_failures: Per-objective failure counts [FIX D-2]
        current_verdict: Most recent verdict
        paused_reason: Reason for pause, if paused
        aborted_reason: Reason for abort, if aborted
        total_cycles: Total evaluation cycles processed
        remediation_depth: Current consecutive remediation count [FIX D-3]
    """

    outcomes: List[ObjectiveOutcome] = field(default_factory=list)
    consecutive_failures: int = 0
    objective_failures: Dict[str, int] = field(default_factory=dict)  # [FIX D-2]
    current_verdict: Verdict = Verdict.CONTINUE
    paused_reason: Optional[str] = None
    aborted_reason: Optional[str] = None
    total_cycles: int = 0
    remediation_depth: int = 0  # [FIX D-3]

    @property
    def total_objectives(self) -> int:
        """Total number of outcomes recorded."""
        return len(self.outcomes)

    @property
    def successful_objectives(self) -> int:
        """Number of successful outcomes."""
        return sum(1 for o in self.outcomes if o.success)

    @property
    def failed_objectives(self) -> int:
        """Number of failed outcomes."""
        return sum(1 for o in self.outcomes if not o.success)


# ============================================================================
# ProjectSupervisor
# ============================================================================


class ProjectSupervisor:
    """
    Evaluates dispatch cycles and produces governance verdicts.

    Operates on ObjectiveOutcome records and ProjectObjectives state
    to determine whether execution should continue, pause, remediate,
    or abort.

    Quality review fixes:
        D-1: All public methods are self-contained; callers wrap in try/except
        D-2: Per-objective failure tracking via objective_failures Dict[str, int]
        D-3: max_remediation_depth limits consecutive remediation cycles
        D-4: min_trend_slope is configurable
        D-6: reset() clears all state between run() invocations
    """

    def __init__(self, config: Optional[SupervisorConfig] = None) -> None:
        """
        Initialize the supervisor.

        Args:
            config: Supervisor configuration (defaults used if None)
        """
        self._config = config or SupervisorConfig()
        self._state = SupervisorState()

    @property
    def config(self) -> SupervisorConfig:
        """Get supervisor configuration."""
        return self._config

    @property
    def state(self) -> SupervisorState:
        """Get current supervisor state."""
        return self._state

    # -----------------------------------------------------------------------
    # Core evaluation
    # -----------------------------------------------------------------------

    def evaluate_cycle(
        self,
        outcome: ObjectiveOutcome,
        project: ProjectObjectives,
        dep_graph: DependencyGraph,
    ) -> Verdict:
        """
        Evaluate a single dispatch cycle.

        Checks are performed in priority order:
        1. Already aborted -> ABORT
        2. Record outcome, update consecutive and per-objective counters
        3. Max consecutive failures (project or per-objective) -> ABORT [FIX D-2]
        4. Consecutive failure threshold -> PAUSE
        5. Dependency cascade blocking -> PAUSE
        6. Quality trend declining -> REMEDIATE
        7. Default -> CONTINUE

        Args:
            outcome: The latest objective outcome
            project: Current project state
            dep_graph: Dependency graph for cascade analysis

        Returns:
            Verdict indicating the action to take
        """
        # 1. Already aborted -> always ABORT
        if self._state.current_verdict == Verdict.ABORT:
            return Verdict.ABORT

        # 2. Record outcome
        self._state.outcomes.append(outcome)
        self._state.total_cycles += 1

        if outcome.success:
            self._state.consecutive_failures = 0
            # Reset remediation depth on success
            if self._state.current_verdict == Verdict.REMEDIATE:
                self._state.remediation_depth = 0
        else:
            self._state.consecutive_failures += 1
            # [FIX D-2] Per-objective failure tracking
            obj_id = outcome.objective_id
            self._state.objective_failures[obj_id] = (
                self._state.objective_failures.get(obj_id, 0) + 1
            )

        # 3. Max consecutive failures (project-level) -> ABORT
        if self._state.consecutive_failures >= self._config.max_consecutive_failures:
            self._record_verdict(
                Verdict.ABORT,
                f"Project-level consecutive failures "
                f"({self._state.consecutive_failures}) >= "
                f"max ({self._config.max_consecutive_failures})",
            )
            return Verdict.ABORT

        # [FIX D-2] Per-objective failure tracking -> ABORT
        obj_failures = self._state.objective_failures.get(
            outcome.objective_id, 0
        )
        if obj_failures >= self._config.max_consecutive_failures:
            self._record_verdict(
                Verdict.ABORT,
                f"Objective '{outcome.objective_id}' has "
                f"{obj_failures} failures >= "
                f"max ({self._config.max_consecutive_failures})",
            )
            return Verdict.ABORT

        # 4. Consecutive failure threshold -> PAUSE
        if self._state.consecutive_failures >= self._config.consecutive_failure_threshold:
            self._record_verdict(
                Verdict.PAUSE,
                f"Consecutive failures "
                f"({self._state.consecutive_failures}) >= "
                f"threshold ({self._config.consecutive_failure_threshold})",
            )
            return Verdict.PAUSE

        # 5. Dependency cascade blocking -> PAUSE
        if self._is_cascade_blocked(outcome, project, dep_graph):
            self._record_verdict(
                Verdict.PAUSE,
                f"Dependency cascade blocked after objective "
                f"'{outcome.objective_id}'",
            )
            return Verdict.PAUSE

        # 6. Quality trend declining -> REMEDIATE
        if not outcome.success or (
            outcome.quality_score is not None
            and self._compute_quality_trend() < -self._config.quality_decline_threshold
        ):
            # Only trigger REMEDIATE if slope exceeds configured minimum [FIX D-4]
            trend = self._compute_quality_trend()
            if (
                outcome.quality_score is not None
                and trend < -self._config.min_trend_slope
            ):
                # [FIX D-3] Check remediation depth limit
                if self._state.remediation_depth >= self._config.max_remediation_depth:
                    self._record_verdict(
                        Verdict.ABORT,
                        f"Remediation depth ({self._state.remediation_depth}) "
                        f"exceeded max ({self._config.max_remediation_depth})",
                    )
                    return Verdict.ABORT

                self._state.remediation_depth += 1
                self._record_verdict(
                    Verdict.REMEDIATE,
                    f"Quality trend declining: slope={trend:.4f} "
                    f"(threshold={self._config.quality_decline_threshold})",
                )
                return Verdict.REMEDIATE

        # 7. Default -> CONTINUE
        self._record_verdict(Verdict.CONTINUE, "All checks passed")
        return Verdict.CONTINUE

    # -----------------------------------------------------------------------
    # Health scoring
    # -----------------------------------------------------------------------

    def compute_health_score(
        self,
        project: ProjectObjectives,
        dep_graph: DependencyGraph,
    ) -> HealthScore:
        """
        Compute a composite health score for the project.

        Components:
        - success_rate: successful / total outcomes
        - quality_trend: normalized linear regression slope of quality scores
          (-1..1 mapped to 0..1, where 1.0 = perfectly stable)
        - dependency_health: 1 - (blocked / total objectives)
        - composite: weighted sum of the above

        Args:
            project: Current project state
            dep_graph: Dependency graph

        Returns:
            HealthScore with all components
        """
        # Success rate
        total = self._state.total_objectives
        if total > 0:
            success_rate = self._state.successful_objectives / total
        else:
            success_rate = 1.0

        # Quality trend: normalize slope to 0..1 range
        raw_slope = self._compute_quality_trend()
        # slope range is roughly -1..1 (per step), clamp and normalize
        normalized_trend = max(0.0, min(1.0, (1.0 - abs(raw_slope)) / 2.0 + 0.5))

        # Dependency health
        all_ids = dep_graph.nodes
        total_deps = len(all_ids) if all_ids else 1
        blocked_count = sum(
            1
            for o in project.objectives
            if o.status == ObjectiveStatus.BLOCKED
        )
        dependency_health = 1.0 - (blocked_count / total_deps)

        # Weighted composite
        composite = (
            success_rate * self._config.health_success_rate_weight
            + normalized_trend * self._config.health_quality_weight
            + dependency_health * self._config.health_dependency_weight
        )

        return HealthScore(
            success_rate=round(success_rate, 4),
            quality_trend=round(normalized_trend, 4),
            dependency_health=round(dependency_health, 4),
            composite=round(composite, 4),
        )

    # -----------------------------------------------------------------------
    # Phase completion
    # -----------------------------------------------------------------------

    def check_phase_completion(
        self, project: ProjectObjectives, phase: str
    ) -> bool:
        """
        Check if all objectives in a given phase are terminal.

        An objective is terminal if it is COMPLETED or CANCELLED.

        Args:
            project: Current project state
            phase: Phase name to check

        Returns:
            True if all objectives in the phase are terminal
        """
        phase_objectives = [
            o for o in project.objectives if o.phase == phase
        ]
        if not phase_objectives:
            return True

        return all(
            o.status in (ObjectiveStatus.COMPLETED, ObjectiveStatus.CANCELLED)
            for o in phase_objectives
        )

    # -----------------------------------------------------------------------
    # Reset (FIX D-6)
    # -----------------------------------------------------------------------

    def reset(self) -> None:
        """
        Reset all supervisor state between run() invocations.

        [FIX D-6] Ensures no stale state carries over between runs.
        """
        self._state = SupervisorState()
        logger.info("ProjectSupervisor state reset")

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _compute_quality_trend(self) -> float:
        """
        Compute linear regression slope over the last quality_window scores.

        Uses ordinary least squares on (index, quality_score) pairs.
        Returns the slope (negative = declining quality).
        Returns 0.0 if insufficient data points.
        """
        scores = [
            o.quality_score
            for o in self._state.outcomes[-self._config.quality_window:]
            if o.quality_score is not None
        ]
        n = len(scores)
        if n < 2:
            return 0.0

        # OLS: slope = (n*sum(xy) - sum(x)*sum(y)) / (n*sum(x^2) - (sum(x))^2)
        indices = list(range(n))
        sum_x = sum(indices)
        sum_y = sum(scores)
        sum_xy = sum(x * y for x, y in zip(indices, scores))
        sum_x2 = sum(x * x for x in indices)

        denominator = n * sum_x2 - sum_x * sum_x
        if denominator == 0:
            return 0.0

        slope = (n * sum_xy - sum_x * sum_y) / denominator
        return slope

    def _is_cascade_blocked(
        self,
        outcome: ObjectiveOutcome,
        project: ProjectObjectives,
        dep_graph: DependencyGraph,
    ) -> bool:
        """
        Check if a failure causes cascade blocking.

        If the failed objective has dependents that are now BLOCKED,
        return True to trigger a PAUSE.

        Args:
            outcome: The latest outcome
            project: Current project state
            dep_graph: Dependency graph

        Returns:
            True if cascade blocking is detected
        """
        if outcome.success:
            return False

        # Get all objectives affected by this failure
        affected = dep_graph.compute_cascade(outcome.objective_id)
        if not affected:
            return False

        # Check if any affected objectives are BLOCKED
        blocked_ids = {
            o.objective_id
            for o in project.objectives
            if o.status == ObjectiveStatus.BLOCKED
        }
        return bool(affected & blocked_ids)

    def _record_verdict(self, verdict: Verdict, reason: str) -> None:
        """
        Record a verdict and log it.

        Args:
            verdict: The verdict to record
            reason: Human-readable reason
        """
        self._state.current_verdict = verdict

        if verdict == Verdict.PAUSE:
            self._state.paused_reason = reason
            logger.warning(f"Supervisor PAUSE: {reason}")
        elif verdict == Verdict.ABORT:
            self._state.aborted_reason = reason
            logger.error(f"Supervisor ABORT: {reason}")
        elif verdict == Verdict.REMEDIATE:
            logger.warning(f"Supervisor REMEDIATE: {reason}")
        else:
            logger.debug(f"Supervisor CONTINUE: {reason}")

    # -----------------------------------------------------------------------
    # Level evaluation (parallel execution support)
    # -----------------------------------------------------------------------

    def evaluate_level(
        self,
        outcomes: list,
        project,
        dep_graph,
        conflicts: list | None = None,
    ) -> str:
        """
        Evaluate an entire level of parallel executions.

        Logic:
        1. If any conflict detected -> REMEDIATE
        2. If all objectives failed -> ABORT
        3. Count failures, check against max_consecutive_failures
        4. Otherwise -> CONTINUE

        Args:
            outcomes: List of ObjectiveOutcome for this level
            project: Current project state
            dep_graph: Dependency graph
            conflicts: Optional list of ConflictReport

        Returns:
            Verdict enum value as string
        """
        # 1. If any conflict detected -> REMEDIATE
        if conflicts:
            self._record_verdict(
                Verdict.REMEDIATE,
                f"{len(conflicts)} file conflict(s) detected in level",
            )
            return Verdict.REMEDIATE.value

        if not outcomes:
            return Verdict.CONTINUE.value

        # 2. Check failure counts
        failed = [o for o in outcomes if not o.success]
        succeeded = [o for o in outcomes if o.success]

        # All objectives failed -> ABORT
        if len(failed) == len(outcomes):
            self._record_verdict(
                Verdict.ABORT,
                f"All {len(outcomes)} objectives failed in level",
            )
            return Verdict.ABORT.value

        # 3. Record outcomes and check consecutive failures
        for outcome in outcomes:
            self._state.outcomes.append(outcome)
            self._state.total_cycles += 1

            if outcome.success:
                self._state.consecutive_failures = 0
            else:
                self._state.consecutive_failures += 1
                obj_id = outcome.objective_id
                self._state.objective_failures[obj_id] = (
                    self._state.objective_failures.get(obj_id, 0) + 1
                )

                # Per-objective failure tracking -> ABORT
                obj_failures = self._state.objective_failures.get(obj_id, 0)
                if obj_failures >= self._config.max_consecutive_failures:
                    self._record_verdict(
                        Verdict.ABORT,
                        f"Objective '{obj_id}' has {obj_failures} failures >= "
                        f"max ({self._config.max_consecutive_failures})",
                    )
                    return Verdict.ABORT.value

        # Project-level consecutive failures -> ABORT
        if self._state.consecutive_failures >= self._config.max_consecutive_failures:
            self._record_verdict(
                Verdict.ABORT,
                f"Project-level consecutive failures "
                f"({self._state.consecutive_failures}) >= "
                f"max ({self._config.max_consecutive_failures})",
            )
            return Verdict.ABORT.value

        # 4. Default -> CONTINUE
        self._record_verdict(
            Verdict.CONTINUE,
            f"Level: {len(succeeded)}/{len(outcomes)} succeeded",
        )
        return Verdict.CONTINUE.value
