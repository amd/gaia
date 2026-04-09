"""
GAIA Pipeline Metrics Collector

Pipeline-specific metrics wrapper that extends the base MetricsCollector
with additional functionality for tracking pipeline orchestration metrics.

This module provides:
- PipelineMetricsCollector: Wraps gaia.metrics with pipeline-specific methods
- TPS/TTFT tracking for LLM token generation
- Phase duration tracking
- Loop iteration counting
- Agent selection decision tracking
- State transition timestamps
"""

import asyncio
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from gaia.metrics import MetricsCollector, MetricSnapshot, MetricType
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PhaseTiming:
    """
    Tracks timing information for a pipeline phase.

    Attributes:
        phase_name: Name of the pipeline phase
        started_at: When the phase started
        ended_at: When the phase ended (None if still running)
        duration_seconds: Total duration in seconds
        token_count: Number of tokens generated during phase
        ttft: Time to first token in seconds
    """

    phase_name: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    token_count: int = 0
    ttft: Optional[float] = None
    first_token_at: Optional[datetime] = None

    def start(self) -> None:
        """Mark phase as started."""
        self.started_at = datetime.now(timezone.utc)

    def end(self) -> None:
        """Mark phase as ended and calculate duration."""
        if self.started_at:
            self.ended_at = datetime.now(timezone.utc)
            self.duration_seconds = (self.ended_at - self.started_at).total_seconds()

    def record_first_token(self) -> None:
        """Record when the first token was generated."""
        if self.started_at and not self.first_token_at:
            self.first_token_at = datetime.now(timezone.utc)
            self.ttft = (self.first_token_at - self.started_at).total_seconds()

    def record_token(self) -> None:
        """Increment token count."""
        self.token_count += 1

    def get_tps(self) -> float:
        """
        Calculate tokens per second.

        Returns:
            Tokens per second, or 0.0 if not enough data
        """
        if self.duration_seconds > 0 and self.token_count > 0:
            return self.token_count / self.duration_seconds
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "phase_name": self.phase_name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_seconds": self.duration_seconds,
            "token_count": self.token_count,
            "ttft": self.ttft,
            "tps": self.get_tps(),
        }


@dataclass
class LoopMetrics:
    """
    Aggregated metrics for a single loop iteration.

    Attributes:
        loop_id: Unique loop identifier
        phase_name: Pipeline phase this loop belongs to
        iteration_count: Number of iterations executed
        quality_scores: History of quality scores
        defects_by_type: Defects categorized by type
        started_at: When the loop started
        ended_at: When the loop ended
    """

    loop_id: str
    phase_name: str
    iteration_count: int = 0
    quality_scores: List[float] = field(default_factory=list)
    defects_by_type: Dict[str, int] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    def add_quality_score(self, score: float) -> None:
        """Add a quality score."""
        self.quality_scores.append(score)
        self.iteration_count = len(self.quality_scores)

    def add_defect(self, defect_type: str) -> None:
        """Add a defect of the specified type."""
        self.defects_by_type[defect_type] = self.defects_by_type.get(defect_type, 0) + 1

    @property
    def average_quality(self) -> Optional[float]:
        """Get average quality score."""
        if not self.quality_scores:
            return None
        return sum(self.quality_scores) / len(self.quality_scores)

    @property
    def max_quality(self) -> Optional[float]:
        """Get maximum quality score achieved."""
        if not self.quality_scores:
            return None
        return max(self.quality_scores)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "loop_id": self.loop_id,
            "phase_name": self.phase_name,
            "iteration_count": self.iteration_count,
            "quality_scores": self.quality_scores,
            "average_quality": self.average_quality,
            "max_quality": self.max_quality,
            "defects_by_type": self.defects_by_type,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


@dataclass
class StateTransition:
    """
    Records a state transition event.

    Attributes:
        from_state: Previous state
        to_state: New state
        timestamp: When the transition occurred
        reason: Reason for the transition
        metadata: Additional context
    """

    from_state: str
    to_state: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "metadata": self.metadata,
        }


class PipelineMetricsCollector:
    """
    Pipeline-specific metrics collector wrapping gaia.metrics.

    The PipelineMetricsCollector extends the base MetricsCollector with
    additional functionality for tracking pipeline orchestration metrics:
    - Phase durations with start/end timestamps
    - TPS (tokens per second) tracking
    - TTFT (time to first token) tracking
    - Loop iteration counting
    - Agent selection decisions
    - State transition history
    - Hook execution times

    Thread Safety:
        All public methods are protected by a reentrant lock (RLock),
        making the collector safe for concurrent access.

    Example:
        >>> collector = PipelineMetricsCollector(pipeline_id="pipeline-001")
        >>> collector.start_phase("PLANNING")
        >>> collector.record_phase_duration("PLANNING", duration=45.5)
        >>> collector.record_tps("PLANNING", tps=25.5)
        >>> collector.record_ttft("PLANNING", ttft=0.5)
        >>> snapshot = collector.get_metrics_snapshot()
    """

    def __init__(
        self,
        pipeline_id: str,
        metrics_collector: Optional[MetricsCollector] = None,
    ):
        """
        Initialize pipeline metrics collector.

        Args:
            pipeline_id: Unique pipeline identifier
            metrics_collector: Optional base MetricsCollector to wrap
        """
        self.pipeline_id = pipeline_id
        self._metrics_collector = metrics_collector or MetricsCollector(
            collector_id=f"pipeline-{pipeline_id}"
        )

        # Thread safety
        self._lock = threading.RLock()

        # Phase timing tracking
        self._phase_timings: Dict[str, PhaseTiming] = {}
        self._current_phase: Optional[str] = None

        # Loop metrics tracking
        self._loop_metrics: Dict[str, LoopMetrics] = {}

        # State transition history
        self._state_transitions: List[StateTransition] = []

        # Agent selection decisions
        self._agent_selections: List[Dict[str, Any]] = []

        # Hook execution times
        self._hook_execution_times: List[Dict[str, Any]] = []

        # Quality score history
        self._quality_scores: List[Tuple[str, str, float]] = (
            []
        )  # (loop_id, phase, score)

        # Defect tracking by type
        self._defects_by_type: Dict[str, int] = defaultdict(int)

        # Resource utilization snapshots
        self._resource_snapshots: List[Dict[str, Any]] = []

        logger.info(
            "PipelineMetricsCollector initialized",
            extra={"pipeline_id": pipeline_id},
        )

    def start_phase(self, phase_name: str) -> None:
        """
        Mark the start of a pipeline phase.

        Args:
            phase_name: Name of the phase starting
        """
        with self._lock:
            # End previous phase if running
            if self._current_phase and self._current_phase in self._phase_timings:
                self._phase_timings[self._current_phase].end()

            # Start new phase
            self._current_phase = phase_name
            timing = PhaseTiming(phase_name=phase_name)
            timing.start()
            self._phase_timings[phase_name] = timing

            # Record state transition
            self._record_state_transition(
                from_state=self._current_phase,
                to_state=phase_name,
                reason="Phase transition",
                metadata={},
            )

            logger.debug(
                f"Started phase: {phase_name}",
                extra={"pipeline_id": self.pipeline_id, "phase": phase_name},
            )

    def end_phase(self, phase_name: Optional[str] = None) -> None:
        """
        Mark the end of a pipeline phase.

        Args:
            phase_name: Name of the phase ending (None for current phase)
        """
        with self._lock:
            phase = phase_name or self._current_phase
            if phase and phase in self._phase_timings:
                self._phase_timings[phase].end()

                # Record phase duration metric
                duration = self._phase_timings[phase].duration_seconds
                self._metrics_collector.record_metric(
                    loop_id=self.pipeline_id,
                    phase=phase,
                    metric_type=MetricType.PHASE_DURATION,
                    value=duration,
                    metadata={"phase_name": phase},
                )

                # Record TPS if tokens were generated
                tps = self._phase_timings[phase].get_tps()
                if tps > 0:
                    self._metrics_collector.record_metric(
                        loop_id=self.pipeline_id,
                        phase=phase,
                        metric_type=MetricType.TPS,
                        value=tps,
                        metadata={
                            "token_count": self._phase_timings[phase].token_count
                        },
                    )

                # Record TTFT if captured
                if self._phase_timings[phase].ttft is not None:
                    self._metrics_collector.record_metric(
                        loop_id=self.pipeline_id,
                        phase=phase,
                        metric_type=MetricType.TTFT,
                        value=self._phase_timings[phase].ttft,
                        metadata={"phase_name": phase},
                    )

            logger.debug(
                f"Ended phase: {phase}",
                extra={"pipeline_id": self.pipeline_id, "phase": phase},
            )

    def record_phase_duration(
        self,
        phase_name: str,
        duration: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record phase duration directly.

        Args:
            phase_name: Name of the phase
            duration: Duration in seconds
            metadata: Optional additional metadata
        """
        with self._lock:
            self._metrics_collector.record_metric(
                loop_id=self.pipeline_id,
                phase=phase_name,
                metric_type=MetricType.PHASE_DURATION,
                value=duration,
                metadata=metadata or {},
            )

            # Update phase timing if exists
            if phase_name in self._phase_timings:
                self._phase_timings[phase_name].duration_seconds = duration

    def record_tps(
        self,
        phase_name: str,
        tps: float,
        token_count: int = 0,
    ) -> None:
        """
        Record tokens per second.

        Args:
            phase_name: Phase where tokens were generated
            tps: Tokens per second
            token_count: Total token count
        """
        with self._lock:
            self._metrics_collector.record_metric(
                loop_id=self.pipeline_id,
                phase=phase_name,
                metric_type=MetricType.TPS,
                value=tps,
                metadata={"token_count": token_count},
            )

            # Update phase timing if exists
            if phase_name in self._phase_timings:
                self._phase_timings[phase_name].token_count = token_count

    def record_ttft(
        self,
        phase_name: str,
        ttft: float,
    ) -> None:
        """
        Record time to first token.

        Args:
            phase_name: Phase where token generation occurred
            ttft: Time to first token in seconds
        """
        with self._lock:
            self._metrics_collector.record_metric(
                loop_id=self.pipeline_id,
                phase=phase_name,
                metric_type=MetricType.TTFT,
                value=ttft,
                metadata={"phase_name": phase_name},
            )

            # Update phase timing if exists
            if phase_name in self._phase_timings:
                self._phase_timings[phase_name].ttft = ttft

    def record_loop_iteration(
        self,
        loop_id: str,
        phase_name: str,
        quality_score: Optional[float] = None,
    ) -> int:
        """
        Record a loop iteration.

        Args:
            loop_id: Loop identifier
            phase_name: Phase this loop belongs to
            quality_score: Optional quality score for this iteration

        Returns:
            Current iteration count
        """
        with self._lock:
            if loop_id not in self._loop_metrics:
                self._loop_metrics[loop_id] = LoopMetrics(
                    loop_id=loop_id,
                    phase_name=phase_name,
                )

            loop_metrics = self._loop_metrics[loop_id]
            loop_metrics.iteration_count += 1

            if quality_score is not None:
                loop_metrics.add_quality_score(quality_score)
                self._quality_scores.append((loop_id, phase_name, quality_score))

            # Record loop iteration count metric
            self._metrics_collector.record_metric(
                loop_id=loop_id,
                phase=phase_name,
                metric_type=MetricType.LOOP_ITERATION_COUNT,
                value=float(loop_metrics.iteration_count),
                metadata={"phase_name": phase_name},
            )

            return loop_metrics.iteration_count

    def record_quality_score(
        self,
        loop_id: str,
        phase_name: str,
        quality_score: float,
    ) -> None:
        """
        Record a quality score.

        Args:
            loop_id: Loop identifier
            phase_name: Phase this score belongs to
            quality_score: Quality score (0-1)
        """
        with self._lock:
            self._quality_scores.append((loop_id, phase_name, quality_score))

            if loop_id in self._loop_metrics:
                self._loop_metrics[loop_id].add_quality_score(quality_score)

    def record_defect(
        self,
        loop_id: str,
        phase_name: str,
        defect_type: str,
    ) -> None:
        """
        Record a defect by type.

        Args:
            loop_id: Loop identifier
            phase_name: Phase where defect was found
            defect_type: Type of defect (security, testing, documentation, etc.)
        """
        with self._lock:
            self._defects_by_type[defect_type] += 1

            if loop_id in self._loop_metrics:
                self._loop_metrics[loop_id].add_defect(defect_type)

    def record_agent_selection(
        self,
        phase_name: str,
        agent_id: str,
        reason: str,
        alternatives: Optional[List[str]] = None,
    ) -> None:
        """
        Record an agent selection decision.

        Args:
            phase_name: Phase where selection occurred
            agent_id: Selected agent ID
            reason: Reason for selection
            alternatives: List of alternative agents considered
        """
        with self._lock:
            selection = {
                "phase": phase_name,
                "agent_id": agent_id,
                "reason": reason,
                "alternatives": alternatives or [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._agent_selections.append(selection)

            # Record as metric
            self._metrics_collector.record_metric(
                loop_id=self.pipeline_id,
                phase=phase_name,
                metric_type=MetricType.AGENT_SELECTION,
                value=1.0,  # Count
                metadata=selection,
            )

    def record_state_transition(
        self,
        from_state: str,
        to_state: str,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record a state transition.

        Args:
            from_state: Previous state
            to_state: New state
            reason: Reason for transition
            metadata: Additional context
        """
        with self._lock:
            self._record_state_transition(from_state, to_state, reason, metadata or {})

    def _record_state_transition(
        self,
        from_state: str,
        to_state: str,
        reason: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Internal state transition recording (assumes lock is held)."""
        transition = StateTransition(
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            metadata=metadata,
        )
        self._state_transitions.append(transition)

        # Record timestamp as metric value
        self._metrics_collector.record_metric(
            loop_id=self.pipeline_id,
            phase=to_state,
            metric_type=MetricType.STATE_TRANSITION,
            value=transition.timestamp.timestamp(),
            metadata={
                "from_state": from_state,
                "to_state": to_state,
                "reason": reason,
            },
        )

    def record_hook_execution(
        self,
        hook_name: str,
        event: str,
        duration_seconds: float,
        success: bool = True,
    ) -> None:
        """
        Record hook execution time.

        Args:
            hook_name: Name of the hook
            event: Event that triggered the hook
            duration_seconds: Execution duration in seconds
            success: Whether hook execution succeeded
        """
        with self._lock:
            execution = {
                "hook_name": hook_name,
                "event": event,
                "duration_seconds": duration_seconds,
                "success": success,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._hook_execution_times.append(execution)

            # Record as metric
            self._metrics_collector.record_metric(
                loop_id=self.pipeline_id,
                phase=event,
                metric_type=MetricType.HOOK_EXECUTION_TIME,
                value=duration_seconds,
                metadata={
                    "hook_name": hook_name,
                    "event": event,
                    "success": success,
                },
            )

    def record_resource_utilization(
        self,
        cpu_percent: float = 0.0,
        memory_percent: float = 0.0,
        memory_mb: float = 0.0,
    ) -> None:
        """
        Record resource utilization snapshot.

        Args:
            cpu_percent: CPU utilization percentage
            memory_percent: Memory utilization percentage
            memory_mb: Memory usage in MB
        """
        with self._lock:
            snapshot = {
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
                "memory_mb": memory_mb,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._resource_snapshots.append(snapshot)

            # Record as metric (use average of cpu and memory as combined value)
            combined = (
                (cpu_percent + memory_percent) / 2
                if cpu_percent or memory_percent
                else 0.0
            )
            self._metrics_collector.record_metric(
                loop_id=self.pipeline_id,
                phase="SYSTEM",
                metric_type=MetricType.RESOURCE_UTILIZATION,
                value=combined / 100.0,  # Normalize to 0-1
                metadata=snapshot,
            )

    def get_phase_timing(self, phase_name: str) -> Optional[PhaseTiming]:
        """
        Get timing information for a phase.

        Args:
            phase_name: Name of the phase

        Returns:
            PhaseTiming or None if phase not found
        """
        with self._lock:
            return self._phase_timings.get(phase_name)

    def get_loop_metrics(self, loop_id: str) -> Optional[LoopMetrics]:
        """
        Get metrics for a loop.

        Args:
            loop_id: Loop identifier

        Returns:
            LoopMetrics or None if loop not found
        """
        with self._lock:
            return self._loop_metrics.get(loop_id)

    def get_all_loop_metrics(self) -> Dict[str, LoopMetrics]:
        """Get metrics for all loops."""
        with self._lock:
            return dict(self._loop_metrics)

    def get_state_transitions(self) -> List[StateTransition]:
        """Get all state transitions."""
        with self._lock:
            return list(self._state_transitions)

    def get_agent_selections(self) -> List[Dict[str, Any]]:
        """Get all agent selection decisions."""
        with self._lock:
            return list(self._agent_selections)

    def get_quality_history(self) -> List[Tuple[str, str, float]]:
        """Get quality score history."""
        with self._lock:
            return list(self._quality_scores)

    def get_defects_by_type(self) -> Dict[str, int]:
        """Get defect counts by type."""
        with self._lock:
            return dict(self._defects_by_type)

    def get_metrics_snapshot(self) -> Dict[str, Any]:
        """
        Get a comprehensive metrics snapshot.

        Returns:
            Dictionary containing all collected metrics
        """
        with self._lock:
            return {
                "pipeline_id": self.pipeline_id,
                "phase_timings": {
                    name: timing.to_dict()
                    for name, timing in self._phase_timings.items()
                },
                "loop_metrics": {
                    loop_id: metrics.to_dict()
                    for loop_id, metrics in self._loop_metrics.items()
                },
                "state_transitions": [t.to_dict() for t in self._state_transitions],
                "agent_selections": self._agent_selections,
                "hook_execution_times": self._hook_execution_times,
                "quality_scores": self._quality_scores,
                "defects_by_type": dict(self._defects_by_type),
                "resource_snapshots": self._resource_snapshots,
            }

    def get_base_collector(self) -> MetricsCollector:
        """Get the underlying base metrics collector."""
        return self._metrics_collector

    def generate_report(self) -> Dict[str, Any]:
        """
        Generate comprehensive metrics report.

        Returns:
            Dictionary containing metrics analysis and insights
        """
        with self._lock:
            # Calculate aggregate statistics
            total_duration = sum(
                t.duration_seconds for t in self._phase_timings.values()
            )

            total_tokens = sum(t.token_count for t in self._phase_timings.values())

            avg_tps = total_tokens / total_duration if total_duration > 0 else 0.0

            avg_ttft_values = [
                t.ttft for t in self._phase_timings.values() if t.ttft is not None
            ]
            avg_ttft = (
                sum(avg_ttft_values) / len(avg_ttft_values) if avg_ttft_values else 0.0
            )

            avg_hook_time = (
                sum(h["duration_seconds"] for h in self._hook_execution_times)
                / len(self._hook_execution_times)
                if self._hook_execution_times
                else 0.0
            )

            # Quality analysis
            if self._quality_scores:
                scores = [s[2] for s in self._quality_scores]
                avg_quality = sum(scores) / len(scores)
                max_quality = max(scores)
                min_quality = min(scores)
            else:
                avg_quality = max_quality = min_quality = 0.0

            return {
                "pipeline_id": self.pipeline_id,
                "summary": {
                    "total_duration_seconds": total_duration,
                    "total_tokens": total_tokens,
                    "avg_tps": avg_tps,
                    "avg_ttft": avg_ttft,
                    "total_loops": len(self._loop_metrics),
                    "total_iterations": sum(
                        m.iteration_count for m in self._loop_metrics.values()
                    ),
                    "total_defects": sum(self._defects_by_type.values()),
                    "avg_quality_score": avg_quality,
                    "max_quality_score": max_quality,
                    "min_quality_score": min_quality,
                    "avg_hook_execution_time": avg_hook_time,
                },
                "phase_breakdown": {
                    name: timing.to_dict()
                    for name, timing in self._phase_timings.items()
                },
                "defects_by_type": dict(self._defects_by_type),
                "agent_selections": self._agent_selections,
                "state_transitions": [t.to_dict() for t in self._state_transitions],
            }

    def clear(self) -> None:
        """Clear all collected metrics."""
        with self._lock:
            self._phase_timings.clear()
            self._loop_metrics.clear()
            self._state_transitions.clear()
            self._agent_selections.clear()
            self._hook_execution_times.clear()
            self._quality_scores.clear()
            self._defects_by_type.clear()
            self._resource_snapshots.clear()
            self._current_phase = None

            logger.info(
                "PipelineMetricsCollector cleared",
                extra={"pipeline_id": self.pipeline_id},
            )


# Global registry for pipeline metrics collectors
_pipeline_collectors: Dict[str, PipelineMetricsCollector] = {}
_registry_lock = threading.Lock()


def get_pipeline_collector(
    pipeline_id: str,
    metrics_collector: Optional[MetricsCollector] = None,
) -> PipelineMetricsCollector:
    """
    Get or create a PipelineMetricsCollector for a pipeline.

    This is a convenience function for getting a collector from the
    global registry.

    Args:
        pipeline_id: Unique pipeline identifier
        metrics_collector: Optional base MetricsCollector

    Returns:
        PipelineMetricsCollector instance
    """
    with _registry_lock:
        if pipeline_id not in _pipeline_collectors:
            _pipeline_collectors[pipeline_id] = PipelineMetricsCollector(
                pipeline_id=pipeline_id,
                metrics_collector=metrics_collector,
            )
        return _pipeline_collectors[pipeline_id]


def remove_pipeline_collector(pipeline_id: str) -> bool:
    """
    Remove a pipeline collector from the registry.

    Args:
        pipeline_id: Pipeline identifier to remove

    Returns:
        True if removed, False if not found
    """
    with _registry_lock:
        if pipeline_id in _pipeline_collectors:
            del _pipeline_collectors[pipeline_id]
            return True
        return False


def get_all_collectors() -> Dict[str, PipelineMetricsCollector]:
    """
    Get all registered pipeline collectors.

    Returns:
        Dictionary mapping pipeline IDs to collectors
    """
    with _registry_lock:
        return dict(_pipeline_collectors)
