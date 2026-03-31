"""
GAIA Metrics Data Models

Data models for runtime metrics tracking in the GAIA pipeline system.

This module defines the core data structures for capturing, storing, and
analyzing pipeline execution metrics. All models are designed for immutability
and thread-safety.

Example:
    >>> from gaia.metrics.models import MetricSnapshot, MetricType
    >>> from datetime import datetime, timezone
    >>> snapshot = MetricSnapshot(
    ...     timestamp=datetime.now(timezone.utc),
    ...     loop_id="loop-001",
    ...     phase="DEVELOPMENT",
    ...     metrics={
    ...         MetricType.TOKEN_EFFICIENCY: 0.85,
    ...         MetricType.CONTEXT_UTILIZATION: 0.72
    ...     }
    ... )
    >>> print(snapshot.metrics[MetricType.TOKEN_EFFICIENCY])
    0.85
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple


class MetricType(Enum):
    """
    Enumeration of all tracked metric types.

    Each metric type represents a specific aspect of pipeline performance
    or quality. Metrics are categorized into:

    Efficiency Metrics:
        - TOKEN_EFFICIENCY: Tokens used per feature delivered
        - CONTEXT_UTILIZATION: Percentage of context window used effectively
        - TPS: Tokens per second (LLM generation speed)
        - TTFT: Time to first token (latency from request to first token)

    Quality Metrics:
        - QUALITY_VELOCITY: Iterations to reach quality threshold
        - DEFECT_DENSITY: Defects per KLOC (thousand lines of code)

    Reliability Metrics:
        - MTTR: Mean time to remediate defects (in hours)
        - AUDIT_COMPLETENESS: Percentage of actions logged

    Performance Metrics:
        - PHASE_DURATION: Time spent in a pipeline phase
        - LOOP_ITERATION_COUNT: Number of iterations per loop
        - HOOK_EXECUTION_TIME: Duration per hook execution

    Example:
        >>> MetricType.TOKEN_EFFICIENCY.category()
        'efficiency'
        >>> MetricType.QUALITY_VELOCITY.category()
        'quality'
        >>> MetricType.TPS.category()
        'performance'
    """

    # Efficiency Metrics
    TOKEN_EFFICIENCY = auto()
    CONTEXT_UTILIZATION = auto()

    # Quality Metrics
    QUALITY_VELOCITY = auto()
    DEFECT_DENSITY = auto()

    # Reliability Metrics
    MTTR = auto()
    AUDIT_COMPLETENESS = auto()

    # Performance Metrics (Phase 2 additions)
    TPS = auto()  # Tokens per second
    TTFT = auto()  # Time to first token (seconds)
    PHASE_DURATION = auto()  # Time spent in pipeline phase (seconds)
    LOOP_ITERATION_COUNT = auto()  # Number of iterations per loop
    HOOK_EXECUTION_TIME = auto()  # Hook execution duration (seconds)
    STATE_TRANSITION = auto()  # State transition timestamp
    AGENT_SELECTION = auto()  # Agent selection decision tracking
    RESOURCE_UTILIZATION = auto()  # CPU/memory utilization (percentage)

    def category(self) -> str:
        """
        Get the category of this metric type.

        Returns:
            Category string: 'efficiency', 'quality', 'reliability', or 'performance'

        Example:
            >>> MetricType.DEFECT_DENSITY.category()
            'quality'
            >>> MetricType.TPS.category()
            'performance'
        """
        name = self.name
        if name in {"TOKEN_EFFICIENCY", "CONTEXT_UTILIZATION"}:
            return "efficiency"
        elif name in {"QUALITY_VELOCITY", "DEFECT_DENSITY"}:
            return "quality"
        elif name in {"MTTR", "AUDIT_COMPLETENESS"}:
            return "reliability"
        elif name in {
            "TPS",
            "TTFT",
            "PHASE_DURATION",
            "LOOP_ITERATION_COUNT",
            "HOOK_EXECUTION_TIME",
            "STATE_TRANSITION",
            "AGENT_SELECTION",
            "RESOURCE_UTILIZATION",
        }:
            return "performance"
        return "unknown"

    def unit(self) -> str:
        """
        Get the unit of measurement for this metric.

        Returns:
            Unit string for display purposes

        Example:
            >>> MetricType.MTTR.unit()
            'hours'
            >>> MetricType.AUDIT_COMPLETENESS.unit()
            'percentage'
            >>> MetricType.TPS.unit()
            'tokens/second'
        """
        units = {
            "TOKEN_EFFICIENCY": "tokens/feature",
            "CONTEXT_UTILIZATION": "percentage",
            "QUALITY_VELOCITY": "iterations",
            "DEFECT_DENSITY": "defects/KLOC",
            "MTTR": "hours",
            "AUDIT_COMPLETENESS": "percentage",
            # Phase 2 additions
            "TPS": "tokens/second",
            "TTFT": "seconds",
            "PHASE_DURATION": "seconds",
            "LOOP_ITERATION_COUNT": "iterations",
            "HOOK_EXECUTION_TIME": "seconds",
            "STATE_TRANSITION": "timestamp",
            "AGENT_SELECTION": "decision",
            "RESOURCE_UTILIZATION": "percentage",
        }
        return units.get(self.name, "unknown")

    def is_higher_better(self) -> bool:
        """
        Check if higher values are better for this metric.

        Returns:
            True if higher is better, False if lower is better

        Example:
            >>> MetricType.TOKEN_EFFICIENCY.is_higher_better()
            True
            >>> MetricType.DEFECT_DENSITY.is_higher_better()
            False
            >>> MetricType.TPS.is_higher_better()
            True
            >>> MetricType.TTFT.is_higher_better()
            False
        """
        # Higher is better for efficiency, audit completeness, and throughput (TPS)
        return self.name in {
            "TOKEN_EFFICIENCY",
            "CONTEXT_UTILIZATION",
            "AUDIT_COMPLETENESS",
            "TPS",
            "RESOURCE_UTILIZATION",
        }


@dataclass(frozen=True)
class MetricSnapshot:
    """
    Immutable snapshot of metrics at a point in time.

    A MetricSnapshot captures the complete state of all tracked metrics
    for a specific pipeline execution context (loop_id, phase) at a
    specific timestamp.

    The frozen=True ensures snapshots cannot be modified after creation,
    providing immutability for thread-safe operations and historical accuracy.

    Attributes:
        timestamp: When the snapshot was taken (UTC timezone)
        loop_id: Unique identifier for the loop iteration
        phase: Pipeline phase name (e.g., "PLANNING", "DEVELOPMENT")
        metrics: Dictionary mapping MetricType to metric values
        metadata: Additional contextual information

    Example:
        >>> snapshot = MetricSnapshot(
        ...     timestamp=datetime.now(timezone.utc),
        ...     loop_id="loop-001",
        ...     phase="DEVELOPMENT",
        ...     metrics={
        ...         MetricType.TOKEN_EFFICIENCY: 0.85,
        ...         MetricType.CONTEXT_UTILIZATION: 0.72,
        ...         MetricType.QUALITY_VELOCITY: 3,
        ...         MetricType.DEFECT_DENSITY: 2.5,
        ...         MetricType.MTTR: 1.5,
        ...         MetricType.AUDIT_COMPLETENESS: 1.0
        ...     },
        ...     metadata={"agent": "senior-developer"}
        ... )
        >>> print(snapshot[MetricType.TOKEN_EFFICIENCY])
        0.85
    """

    timestamp: datetime
    loop_id: str
    phase: str
    metrics: Dict[MetricType, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: MetricType) -> Optional[float]:
        """
        Get metric value by type using subscript notation.

        Args:
            key: MetricType to retrieve

        Returns:
            Metric value or None if not present

        Example:
            >>> snapshot = MetricSnapshot(
            ...     timestamp=datetime.now(timezone.utc),
            ...     loop_id="loop-001",
            ...     phase="DEVELOPMENT",
            ...     metrics={MetricType.TOKEN_EFFICIENCY: 0.85}
            ... )
            >>> snapshot[MetricType.TOKEN_EFFICIENCY]
            0.85
        """
        return self.metrics.get(key)

    def get(self, metric_type: MetricType, default: float = 0.0) -> float:
        """
        Get metric value with default fallback.

        Args:
            metric_type: MetricType to retrieve
            default: Default value if metric not present

        Returns:
            Metric value or default

        Example:
            >>> snapshot.get(MetricType.TOKEN_EFFICIENCY, 0.0)
            0.85
        """
        return self.metrics.get(metric_type, default)

    def with_metric(self, metric_type: MetricType, value: float) -> "MetricSnapshot":
        """
        Create a new snapshot with updated metric value.

        Since MetricSnapshot is immutable (frozen), this creates a copy
        with the specified metric updated.

        Args:
            metric_type: MetricType to update
            value: New metric value

        Returns:
            New MetricSnapshot with updated value

        Example:
            >>> new_snapshot = snapshot.with_metric(MetricType.TOKEN_EFFICIENCY, 0.90)
        """
        new_metrics = {**self.metrics, metric_type: value}
        return MetricSnapshot(
            timestamp=self.timestamp,
            loop_id=self.loop_id,
            phase=self.phase,
            metrics=new_metrics,
            metadata=self.metadata,
        )

    def with_metadata(self, **kwargs: Any) -> "MetricSnapshot":
        """
        Create a new snapshot with updated metadata.

        Args:
            **kwargs: Metadata fields to update

        Returns:
            New MetricSnapshot with updated metadata

        Example:
            >>> new_snapshot = snapshot.with_metadata(agent="qa-specialist")
        """
        new_metadata = {**self.metadata, **kwargs}
        return MetricSnapshot(
            timestamp=self.timestamp,
            loop_id=self.loop_id,
            phase=self.phase,
            metrics=self.metrics,
            metadata=new_metadata,
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert snapshot to dictionary for serialization.

        Returns:
            Dictionary representation with ISO format timestamp

        Example:
            >>> data = snapshot.to_dict()
            >>> assert "timestamp" in data
            >>> assert "loop_id" in data
            >>> assert "metrics" in data
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "loop_id": self.loop_id,
            "phase": self.phase,
            "metrics": {k.name: v for k, v in self.metrics.items()},
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MetricSnapshot":
        """
        Create snapshot from dictionary.

        Args:
            data: Dictionary with snapshot data

        Returns:
            MetricSnapshot instance

        Example:
            >>> data = {
            ...     "timestamp": "2024-01-01T00:00:00+00:00",
            ...     "loop_id": "loop-001",
            ...     "phase": "DEVELOPMENT",
            ...     "metrics": {"TOKEN_EFFICIENCY": 0.85}
            ... }
            >>> snapshot = MetricSnapshot.from_dict(data)
        """
        metrics = {MetricType[k]: v for k, v in data.get("metrics", {}).items()}
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            loop_id=data["loop_id"],
            phase=data["phase"],
            metrics=metrics,
            metadata=data.get("metadata", {}),
        )

    def quality_check(self, threshold: float = 0.90) -> Tuple[bool, List[str]]:
        """
        Check if metrics meet quality threshold.

        Evaluates all metrics against the threshold and returns
        pass/fail status with list of failing metrics.

        Args:
            threshold: Quality threshold (0-1) for percentage-based metrics

        Returns:
            Tuple of (passed, list of failing metric names)

        Example:
            >>> passed, failures = snapshot.quality_check(0.80)
            >>> if not passed:
            ...     print(f"Failing metrics: {failures}")
        """
        failures = []

        for metric_type, value in self.metrics.items():
            if metric_type in {
                MetricType.CONTEXT_UTILIZATION,
                MetricType.AUDIT_COMPLETENESS,
                MetricType.RESOURCE_UTILIZATION,
            }:
                # Percentage metrics - higher is better, check against threshold
                if value < threshold:
                    failures.append(metric_type.name)
            elif metric_type == MetricType.TOKEN_EFFICIENCY:
                # Token efficiency - higher is better (normalize to 0-1)
                if value < threshold:
                    failures.append(metric_type.name)
            elif metric_type == MetricType.QUALITY_VELOCITY:
                # Iterations - lower is better (assume 5 iterations max is acceptable)
                if value > 5:
                    failures.append(metric_type.name)
            elif metric_type == MetricType.DEFECT_DENSITY:
                # Defects per KLOC - lower is better (assume <5 is acceptable)
                if value > 5:
                    failures.append(metric_type.name)
            elif metric_type == MetricType.MTTR:
                # Mean time to resolve - lower is better (assume <4 hours is acceptable)
                if value > 4:
                    failures.append(metric_type.name)
            # Phase 2 performance metrics
            elif metric_type == MetricType.TPS:
                # Tokens per second - higher is better (assume <10 is too slow)
                if value < 10:
                    failures.append(metric_type.name)
            elif metric_type == MetricType.TTFT:
                # Time to first token - lower is better (assume >5 seconds is too slow)
                if value > 5:
                    failures.append(metric_type.name)
            elif metric_type == MetricType.PHASE_DURATION:
                # Phase duration - lower is better (assume >300 seconds is too long)
                if value > 300:
                    failures.append(metric_type.name)
            elif metric_type == MetricType.LOOP_ITERATION_COUNT:
                # Loop iterations - lower is better (assume >10 is too many)
                if value > 10:
                    failures.append(metric_type.name)
            elif metric_type == MetricType.HOOK_EXECUTION_TIME:
                # Hook execution time - lower is better (assume >1 second is too slow)
                if value > 1:
                    failures.append(metric_type.name)

        return len(failures) == 0, failures

    def summary(self) -> str:
        """
        Generate human-readable summary of metrics.

        Returns:
            Formatted summary string

        Example:
            >>> print(snapshot.summary())
            Metrics for loop-001 (DEVELOPMENT):
              Token Efficiency: 0.85 tokens/feature
              Context Utilization: 72.0%
              ...
        """
        lines = [
            f"Metrics for {self.loop_id} ({self.phase}) @ {self.timestamp.isoformat()}"
        ]

        for metric_type, value in sorted(self.metrics.items(), key=lambda x: x[0].name):
            unit = metric_type.unit()
            if "percentage" in unit:
                formatted_value = f"{value * 100:.1f}%"
            elif metric_type in {
                MetricType.QUALITY_VELOCITY,
                MetricType.LOOP_ITERATION_COUNT,
            }:
                formatted_value = f"{int(value)} iterations"
            elif metric_type == MetricType.TPS:
                formatted_value = f"{value:.1f} {unit}"
            elif metric_type in {
                MetricType.TTFT,
                MetricType.PHASE_DURATION,
                MetricType.HOOK_EXECUTION_TIME,
            }:
                formatted_value = f"{value:.3f} {unit}"
            elif metric_type == MetricType.STATE_TRANSITION:
                formatted_value = (
                    f"{datetime.fromtimestamp(value).isoformat()}"
                    if isinstance(value, (int, float))
                    else str(value)
                )
            elif metric_type == MetricType.AGENT_SELECTION:
                formatted_value = str(value)
            else:
                formatted_value = f"{value:.2f} {unit}"

            lines.append(f"  {metric_type.name.replace('_', ' ')}: {formatted_value}")

        return "\n".join(lines)


@dataclass
class MetricStatistics:
    """
    Statistical summary for a metric across multiple snapshots.

    Provides comprehensive statistical analysis including mean, median,
    standard deviation, min/max values, and trend analysis.

    Attributes:
        metric_type: The metric being analyzed
        count: Number of data points
        mean: Arithmetic mean
        median: Middle value
        std_dev: Standard deviation
        min_value: Minimum observed value
        max_value: Maximum observed value
        trend: Trend direction ('increasing', 'decreasing', 'stable')
        percentiles: Dictionary of percentile values (25th, 75th, 90th)

    Example:
        >>> stats = MetricStatistics(
        ...     metric_type=MetricType.TOKEN_EFFICIENCY,
        ...     count=10,
        ...     mean=0.85,
        ...     median=0.87,
        ...     std_dev=0.05,
        ...     min_value=0.75,
        ...     max_value=0.95,
        ...     trend='increasing'
        ... )
    """

    metric_type: MetricType
    count: int
    mean: float
    median: float
    std_dev: float
    min_value: float
    max_value: float
    trend: str = "stable"
    percentiles: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert statistics to dictionary for serialization.

        Returns:
            Dictionary representation of statistics

        Example:
            >>> data = stats.to_dict()
            >>> assert data["metric_type"] == "TOKEN_EFFICIENCY"
            >>> assert data["count"] == 10
        """
        return {
            "metric_type": self.metric_type.name,
            "count": self.count,
            "mean": self.mean,
            "median": self.median,
            "std_dev": self.std_dev,
            "min": self.min_value,
            "max": self.max_value,
            "trend": self.trend,
            "percentiles": self.percentiles,
        }

    @classmethod
    def from_values(
        cls, metric_type: MetricType, values: List[float]
    ) -> "MetricStatistics":
        """
        Create statistics from raw values.

        Computes all statistical measures from a list of metric values.

        Args:
            metric_type: The metric being analyzed
            values: List of metric values

        Returns:
            MetricStatistics instance

        Raises:
            ValueError: If values list is empty

        Example:
            >>> values = [0.80, 0.85, 0.87, 0.90, 0.92]
            >>> stats = MetricStatistics.from_values(MetricType.TOKEN_EFFICIENCY, values)
            >>> print(f"Mean: {stats.mean:.3f}")
        """
        if not values:
            raise ValueError("Cannot compute statistics from empty values list")

        sorted_values = sorted(values)
        n = len(values)

        # Basic statistics
        mean_val = statistics.mean(values)
        median_val = statistics.median(values)
        std_dev_val = statistics.stdev(values) if n > 1 else 0.0

        # Percentiles
        percentiles = {
            "p25": sorted_values[int(n * 0.25)] if n >= 4 else sorted_values[0],
            "p75": sorted_values[int(n * 0.75)] if n >= 4 else sorted_values[-1],
            "p90": sorted_values[int(n * 0.90)] if n >= 10 else sorted_values[-1],
        }

        # Trend analysis (simple linear regression slope)
        trend = cls._compute_trend(values)

        return cls(
            metric_type=metric_type,
            count=n,
            mean=mean_val,
            median=median_val,
            std_dev=std_dev_val,
            min_value=min(values),
            max_value=max(values),
            trend=trend,
            percentiles=percentiles,
        )

    @staticmethod
    def _compute_trend(values: List[float], threshold: float = 0.05) -> str:
        """
        Compute trend direction from values.

        Uses simple linear regression to determine if values are
        increasing, decreasing, or stable.

        Args:
            values: List of metric values in chronological order
            threshold: Slope threshold for 'stable' classification

        Returns:
            Trend string: 'increasing', 'decreasing', or 'stable'
        """
        if len(values) < 2:
            return "stable"

        # Simple linear regression slope
        n = len(values)
        x_mean = (n - 1) / 2
        y_mean = statistics.mean(values)

        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return "stable"

        slope = numerator / denominator

        # Normalize slope by mean value for relative change
        relative_slope = slope / y_mean if y_mean != 0 else 0

        if relative_slope > threshold:
            return "increasing"
        elif relative_slope < -threshold:
            return "decreasing"
        return "stable"


@dataclass
class MetricsReport:
    """
    Comprehensive metrics analysis report.

    Aggregates statistical analysis across all metric types and
    provides overall assessment and recommendations.

    Attributes:
        generated_at: When the report was generated
        loop_id: Loop iteration being reported on (optional)
        phase: Pipeline phase being reported on (optional)
        snapshot_count: Number of snapshots analyzed
        metric_statistics: Statistics for each metric type
        overall_health: Overall health score (0-1)
        recommendations: List of improvement recommendations

    Example:
        >>> report = MetricsReport(
        ...     generated_at=datetime.now(timezone.utc),
        ...     loop_id="loop-001",
        ...     phase="DEVELOPMENT",
        ...     snapshot_count=10,
        ...     metric_statistics={
        ...         MetricType.TOKEN_EFFICIENCY: stats
        ...     },
        ...     overall_health=0.85
        ... )
    """

    generated_at: datetime
    loop_id: Optional[str] = None
    phase: Optional[str] = None
    snapshot_count: int = 0
    metric_statistics: Dict[MetricType, MetricStatistics] = field(default_factory=dict)
    overall_health: float = 0.0
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert report to dictionary for serialization.

        Returns:
            Dictionary representation of report

        Example:
            >>> data = report.to_dict()
            >>> assert "overall_health" in data
            >>> assert "recommendations" in data
        """
        return {
            "generated_at": self.generated_at.isoformat(),
            "loop_id": self.loop_id,
            "phase": self.phase,
            "snapshot_count": self.snapshot_count,
            "metric_statistics": {
                k.name: v.to_dict() for k, v in self.metric_statistics.items()
            },
            "overall_health": self.overall_health,
            "recommendations": self.recommendations,
        }

    def get_health_status(self) -> str:
        """
        Get health status string based on overall health score.

        Returns:
            Status string: 'excellent', 'good', 'acceptable', 'needs_improvement', or 'critical'

        Example:
            >>> report.overall_health = 0.92
            >>> report.get_health_status()
            'excellent'
        """
        if self.overall_health >= 0.95:
            return "excellent"
        elif self.overall_health >= 0.85:
            return "good"
        elif self.overall_health >= 0.70:
            return "acceptable"
        elif self.overall_health >= 0.50:
            return "needs_improvement"
        return "critical"

    def summary(self) -> str:
        """
        Generate human-readable report summary.

        Returns:
            Formatted summary string

        Example:
            >>> print(report.summary())
            Metrics Report for loop-001 (DEVELOPMENT)
            Generated: 2024-01-01T00:00:00+00:00
            Overall Health: 85.0% (good)
            ...
        """
        lines = [
            f"Metrics Report for {self.loop_id or 'all loops'} ({self.phase or 'all phases'})",
            f"Generated: {self.generated_at.isoformat()}",
            f"Overall Health: {self.overall_health * 100:.1f}% ({self.get_health_status()})",
            f"Snapshots Analyzed: {self.snapshot_count}",
            "",
            "Metric Statistics:",
        ]

        for metric_type, stats in sorted(
            self.metric_statistics.items(), key=lambda x: x[0].name
        ):
            lines.append(f"  {metric_type.name}:")
            lines.append(f"    Mean: {stats.mean:.3f}, Median: {stats.median:.3f}")
            lines.append(f"    Range: [{stats.min_value:.3f}, {stats.max_value:.3f}]")
            lines.append(f"    Trend: {stats.trend}")

        if self.recommendations:
            lines.extend(["", "Recommendations:"])
            for i, rec in enumerate(self.recommendations, 1):
                lines.append(f"  {i}. {rec}")

        return "\n".join(lines)
