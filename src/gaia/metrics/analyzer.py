"""
GAIA Metrics Analyzer

Statistical analysis and reporting for pipeline metrics.

This module provides the MetricsAnalyzer class for advanced statistical
analysis of collected metrics, including trend detection, anomaly detection,
correlation analysis, and predictive insights.

Example:
    >>> from gaia.metrics.analyzer import MetricsAnalyzer
    >>> from gaia.metrics.collector import MetricsCollector
    >>> collector = MetricsCollector()
    >>> analyzer = MetricsAnalyzer(collector)
    >>> trends = analyzer.detect_trends()
    >>> anomalies = analyzer.detect_anomalies()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Tuple, Callable
import threading
import statistics
import math
import json

from gaia.metrics.models import (
    MetricSnapshot,
    MetricType,
    MetricStatistics,
    MetricsReport,
)
from gaia.metrics.collector import MetricsCollector
from gaia.utils.logging import get_logger


logger = get_logger(__name__)


class TrendDirection:
    """Constants for trend direction classification."""

    INCREASING = "increasing"
    DECREASING = "decreasing"
    STABLE = "stable"
    VOLATILE = "volatile"


class AnomalyType:
    """Constants for anomaly classification."""

    SPIKE = "spike"  # Sudden increase
    DROP = "drop"  # Sudden decrease
    OUTLIER = "outlier"  # Statistical outlier
    PATTERN_BREAK = "pattern_break"  # Break from established pattern


@dataclass
class TrendAnalysis:
    """
    Results of trend analysis for a metric.

    Attributes:
        metric_type: The metric being analyzed
        direction: Trend direction (increasing, decreasing, stable, volatile)
        confidence: Confidence level (0-1) in the trend assessment
        slope: Rate of change per time unit
        start_value: Value at start of analysis period
        end_value: Value at end of analysis period
        change_percent: Percentage change over period
        data_points: Number of data points analyzed
        period_start: Start of analysis period
        period_end: End of analysis period

    Example:
        >>> trend = TrendAnalysis(
        ...     metric_type=MetricType.TOKEN_EFFICIENCY,
        ...     direction=TrendDirection.INCREASING,
        ...     confidence=0.85,
        ...     slope=0.02,
        ...     start_value=0.75,
        ...     end_value=0.85,
        ...     change_percent=13.3
        ... )
    """

    metric_type: MetricType
    direction: str = TrendDirection.STABLE
    confidence: float = 0.0
    slope: float = 0.0
    start_value: float = 0.0
    end_value: float = 0.0
    change_percent: float = 0.0
    data_points: int = 0
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "metric_type": self.metric_type.name,
            "direction": self.direction,
            "confidence": self.confidence,
            "slope": self.slope,
            "start_value": self.start_value,
            "end_value": self.end_value,
            "change_percent": self.change_percent,
            "data_points": self.data_points,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
        }

    def is_positive(self) -> bool:
        """
        Check if trend is positive (improving).

        Returns:
            True if trend indicates improvement

        Example:
            >>> trend.direction = TrendDirection.INCREASING
            >>> trend.is_positive()  # Depends on metric type
        """
        if self.metric_type.is_higher_better():
            return self.direction == TrendDirection.INCREASING
        return self.direction == TrendDirection.DECREASING

    def summary(self) -> str:
        """Generate human-readable summary."""
        return (
            f"{self.metric_type.name}: {self.direction} "
            f"(confidence: {self.confidence:.0%}, "
            f"change: {self.change_percent:+.1f}%)"
        )


@dataclass
class AnomalyCallback:
    """
    Callback configuration for real-time anomaly alerting.

    This dataclass defines a callback that will be invoked when an anomaly
    is detected, enabling real-time alerting integrations such as webhooks,
    email notifications, or logging systems.

    Attributes:
        callback_fn: The callback function to invoke
        severity_filter: Minimum severity level to trigger callback
        metric_filter: Optional set of metric types to monitor
        include_context: Whether to include full anomaly context

    Example:
        >>> def alert_handler(anomaly: Anomaly, metadata: dict):
        ...     print(f"ALERT: {anomaly.metric_type.name} - {anomaly.severity}")
        ...     # Send to monitoring system
        >>>
        >>> callback = AnomalyCallback(
        ...     callback_fn=alert_handler,
        ...     severity_filter="high",  # Only high and critical
        ...     metric_filter={MetricType.DEFECT_DENSITY, MetricType.MTTR}
        ... )
    """

    callback_fn: Callable[["Anomaly", Dict[str, Any]], None]
    severity_filter: str = "medium"  # low, medium, high, critical
    metric_filter: Optional[List[MetricType]] = None
    include_context: bool = True

    def _severity_meets_threshold(self, severity: str) -> bool:
        """Check if severity meets the callback threshold."""
        severity_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        threshold = severity_order.get(self.severity_filter, 1)
        actual = severity_order.get(severity, 0)
        return actual >= threshold

    def should_trigger(self, anomaly: Anomaly) -> bool:
        """
        Check if callback should trigger for this anomaly.

        Args:
            anomaly: The detected anomaly

        Returns:
            True if callback should be invoked
        """
        # Check severity threshold
        if not self._severity_meets_threshold(anomaly.severity):
            return False

        # Check metric filter
        if self.metric_filter and anomaly.metric_type not in self.metric_filter:
            return False

        return True

    def invoke(self, anomaly: Anomaly, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Invoke the callback with the anomaly.

        Args:
            anomaly: The detected anomaly
            context: Optional additional context data

        Raises:
            Exception: Re-raises any exception from the callback (for debugging)
        """
        if not self.should_trigger(anomaly):
            return

        metadata = {
            "triggered_at": datetime.now(timezone.utc).isoformat(),
            "anomaly_data": anomaly.to_dict() if self.include_context else {
                "metric_type": anomaly.metric_type.name,
                "anomaly_type": anomaly.anomaly_type,
                "severity": anomaly.severity,
            },
        }
        if context:
            metadata["context"] = context

        # Invoke callback
        self.callback_fn(anomaly, metadata)


@dataclass
class Anomaly:
    """
    Detected anomaly in metric data.

    Attributes:
        metric_type: The metric with anomaly
        anomaly_type: Type of anomaly (spike, drop, outlier, pattern_break)
        timestamp: When the anomaly occurred
        value: Anomalous value
        expected_value: Expected/normal value
        deviation: Deviation from expected (in standard deviations)
        severity: Severity level (low, medium, high, critical)
        description: Human-readable description

    Example:
        >>> anomaly = Anomaly(
        ...     metric_type=MetricType.DEFECT_DENSITY,
        ...     anomaly_type=AnomalyType.SPIKE,
        ...     timestamp=datetime.now(timezone.utc),
        ...     value=15.5,
        ...     expected_value=5.0,
        ...     deviation=3.5,
        ...     severity="high"
        ... )
    """

    metric_type: MetricType
    anomaly_type: str
    timestamp: datetime
    value: float
    expected_value: float
    deviation: float
    severity: str = "medium"
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "metric_type": self.metric_type.name,
            "anomaly_type": self.anomaly_type,
            "timestamp": self.timestamp.isoformat(),
            "value": self.value,
            "expected_value": self.expected_value,
            "deviation": self.deviation,
            "severity": self.severity,
            "description": self.description,
            "metadata": self.metadata,
        }

    def __str__(self) -> str:
        """String representation."""
        return (
            f"Anomaly: {self.metric_type.name} - {self.anomaly_type} "
            f"at {self.timestamp.isoformat()} "
            f"(value={self.value:.2f}, expected={self.expected_value:.2f})"
        )


@dataclass
class CorrelationResult:
    """
    Result of correlation analysis between two metrics.

    Attributes:
        metric_a: First metric type
        metric_b: Second metric type
        correlation_coefficient: Pearson correlation coefficient (-1 to 1)
        p_value: Statistical significance (lower = more significant)
        sample_size: Number of paired observations
        relationship: Type of relationship (positive, negative, none)
        strength: Strength of correlation (weak, moderate, strong)

    Example:
        >>> corr = CorrelationResult(
        ...     metric_a=MetricType.TOKEN_EFFICIENCY,
        ...     metric_b=MetricType.QUALITY_VELOCITY,
        ...     correlation_coefficient=-0.65,
        ...     p_value=0.02,
        ...     sample_size=50
        ... )
    """

    metric_a: MetricType
    metric_b: MetricType
    correlation_coefficient: float
    p_value: float
    sample_size: int
    relationship: str = "none"
    strength: str = "none"

    def __post_init__(self):
        """Derive relationship and strength from correlation coefficient."""
        r = self.correlation_coefficient

        # Determine relationship type
        if r > 0.1:
            self.relationship = "positive"
        elif r < -0.1:
            self.relationship = "negative"
        else:
            self.relationship = "none"

        # Determine strength
        abs_r = abs(r)
        if abs_r >= 0.7:
            self.strength = "strong"
        elif abs_r >= 0.4:
            self.strength = "moderate"
        elif abs_r >= 0.1:
            self.strength = "weak"
        else:
            self.strength = "none"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "metric_a": self.metric_a.name,
            "metric_b": self.metric_b.name,
            "correlation_coefficient": self.correlation_coefficient,
            "p_value": self.p_value,
            "sample_size": self.sample_size,
            "relationship": self.relationship,
            "strength": self.strength,
        }

    def is_significant(self, alpha: float = 0.05) -> bool:
        """
        Check if correlation is statistically significant.

        Args:
            alpha: Significance level (default: 0.05)

        Returns:
            True if p-value < alpha
        """
        return self.p_value < alpha


class MetricsAnalyzer:
    """
    Advanced statistical analysis for pipeline metrics.

    The MetricsAnalyzer provides sophisticated analysis capabilities:
    - Trend detection with confidence levels
    - Anomaly detection using statistical methods
    - Correlation analysis between metrics
    - Predictive insights based on historical patterns
    - Comparative analysis across loops/phases

    Example:
        >>> analyzer = MetricsAnalyzer(collector)
        >>> trends = analyzer.detect_trends()
        >>> anomalies = analyzer.detect_anomalies()
        >>> correlations = analyzer.analyze_correlations()
    """

    def __init__(self, collector: MetricsCollector):
        """
        Initialize the analyzer with a metrics collector.

        Args:
            collector: MetricsCollector instance to analyze

        Example:
            >>> collector = MetricsCollector()
            >>> analyzer = MetricsAnalyzer(collector)
        """
        self._collector = collector
        self._lock = threading.RLock()

        logger.info(
            "MetricsAnalyzer initialized",
            extra={"collector_id": collector.collector_id},
        )

    def detect_trends(
        self,
        loop_id: Optional[str] = None,
        min_data_points: int = 3,
    ) -> Dict[MetricType, TrendAnalysis]:
        """
        Detect trends in all metrics.

        Analyzes historical data to identify increasing, decreasing,
        stable, or volatile trends for each metric type.

        Args:
            loop_id: Optional loop filter
            min_data_points: Minimum data points required for analysis

        Returns:
            Dictionary mapping MetricType to TrendAnalysis

        Example:
            >>> trends = analyzer.detect_trends()
            >>> for metric_type, trend in trends.items():
            ...     print(f"{metric_type.name}: {trend.direction}")
        """
        with self._lock:
            trends: Dict[MetricType, TrendAnalysis] = {}

            for metric_type in MetricType:
                history = self._collector.get_metric_history(metric_type, loop_id)

                if len(history) < min_data_points:
                    continue

                # Extract time series
                timestamps = [h[0] for h in history]
                values = [h[1] for h in history]

                # Compute trend
                trend = self._compute_trend(metric_type, timestamps, values)
                trends[metric_type] = trend

            return trends

    def _compute_trend(
        self,
        metric_type: MetricType,
        timestamps: List[datetime],
        values: List[float],
    ) -> TrendAnalysis:
        """
        Compute trend analysis for a time series.

        Uses linear regression with volatility analysis.
        """
        n = len(values)
        if n < 2:
            return TrendAnalysis(metric_type=metric_type)

        # Calculate time deltas in hours from start
        start_time = timestamps[0]
        time_deltas = [(t - start_time).total_seconds() / 3600 for t in timestamps]

        # Linear regression
        x_mean = statistics.mean(time_deltas)
        y_mean = statistics.mean(values)

        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(time_deltas, values))
        denominator = sum((x - x_mean) ** 2 for x in time_deltas)

        if denominator == 0:
            slope = 0
        else:
            slope = numerator / denominator

        # Calculate residuals for volatility
        predicted = [y_mean + slope * (x - x_mean) for x in time_deltas]
        residuals = [actual - pred for actual, pred in zip(values, predicted)]

        # Volatility (standard deviation of residuals)
        volatility = statistics.stdev(residuals) if n > 2 else 0

        # Determine trend direction with volatility consideration
        relative_slope = slope / y_mean if y_mean != 0 else 0

        if volatility > abs(slope):
            direction = TrendDirection.VOLATILE
            confidence = min(1.0, volatility / (abs(slope) + volatility)) if slope != 0 else 0.5
        elif relative_slope > 0.05:
            direction = TrendDirection.INCREASING
            confidence = min(1.0, abs(relative_slope) * 10)
        elif relative_slope < -0.05:
            direction = TrendDirection.DECREASING
            confidence = min(1.0, abs(relative_slope) * 10)
        else:
            direction = TrendDirection.STABLE
            confidence = 1.0 - min(1.0, abs(relative_slope) * 10)

        # Calculate percentage change
        change_percent = ((values[-1] - values[0]) / values[0] * 100) if values[0] != 0 else 0

        return TrendAnalysis(
            metric_type=metric_type,
            direction=direction,
            confidence=confidence,
            slope=slope,
            start_value=values[0],
            end_value=values[-1],
            change_percent=change_percent,
            data_points=n,
            period_start=timestamps[0],
            period_end=timestamps[-1],
        )

    def detect_anomalies(
        self,
        loop_id: Optional[str] = None,
        threshold_std: float = 2.0,
        min_data_points: int = 5,
        callback: Optional[AnomalyCallback] = None,
    ) -> List[Anomaly]:
        """
        Detect anomalies in metric data.

        Uses statistical methods (Z-score, IQR) to identify unusual
        values that deviate significantly from the norm.

        Args:
            loop_id: Optional loop filter
            threshold_std: Number of standard deviations for anomaly threshold
            min_data_points: Minimum data points required
            callback: Optional callback for real-time alerting when anomalies
                      are detected. The callback is invoked for each anomaly
                      that meets the severity and metric filters.

        Returns:
            List of detected anomalies

        Raises:
            Exception: Re-raises any exception from the callback for debugging

        Example:
            >>> anomalies = analyzer.detect_anomalies(threshold_std=2.5)
            >>> for anomaly in anomalies:
            ...     print(f"{anomaly.metric_type.name}: {anomaly.anomaly_type}")

            >>> # With real-time callback alerting
            >>> def alert_handler(anomaly, metadata):
            ...     if anomaly.severity == "critical":
            ...         send_alert(f"Critical: {anomaly.description}")
            >>>
            >>> callback = AnomalyCallback(
            ...     callback_fn=alert_handler,
            ...     severity_filter="high"
            ... )
            >>> anomalies = analyzer.detect_anomalies(callback=callback)
        """
        with self._lock:
            anomalies: List[Anomaly] = []

            for metric_type in MetricType:
                history = self._collector.get_metric_history(metric_type, loop_id)

                if len(history) < min_data_points:
                    continue

                # Extract values
                timestamps = [h[0] for h in history]
                values = [h[1] for h in history]

                # Calculate statistics
                mean_val = statistics.mean(values)
                std_val = statistics.stdev(values) if len(values) > 1 else 0

                if std_val == 0:
                    continue

                # Detect anomalies using Z-score
                for i, (ts, val) in enumerate(zip(timestamps, values)):
                    z_score = (val - mean_val) / std_val

                    if abs(z_score) >= threshold_std:
                        # Determine anomaly type
                        if z_score > 0:
                            anomaly_type = AnomalyType.SPIKE
                        else:
                            anomaly_type = AnomalyType.DROP

                        # Determine severity based on deviation
                        abs_z = abs(z_score)
                        if abs_z >= 4:
                            severity = "critical"
                        elif abs_z >= 3:
                            severity = "high"
                        elif abs_z >= 2.5:
                            severity = "medium"
                        else:
                            severity = "low"

                        anomaly = Anomaly(
                            metric_type=metric_type,
                            anomaly_type=anomaly_type,
                            timestamp=ts,
                            value=val,
                            expected_value=mean_val,
                            deviation=abs_z,
                            severity=severity,
                            description=f"{metric_type.name} {'spike' if z_score > 0 else 'drop'}: "
                            f"{val:.2f} (expected ~{mean_val:.2f})",
                            metadata={"z_score": z_score, "index": i},
                        )
                        anomalies.append(anomaly)

                        # Invoke callback if provided
                        if callback:
                            try:
                                callback.invoke(anomaly, {
                                    "loop_id": loop_id,
                                    "detection_method": "z_score",
                                    "threshold_std": threshold_std,
                                })
                            except Exception as e:
                                logger.error(
                                    f"Anomaly callback failed: {e}",
                                    extra={
                                        "metric_type": metric_type.name,
                                        "anomaly_type": anomaly_type,
                                    },
                                )
                                raise

                # Also check for pattern breaks using consecutive differences
                if len(values) >= 4:
                    diffs = [values[i] - values[i - 1] for i in range(1, len(values))]
                    if len(diffs) >= 3:
                        mean_diff = statistics.mean(diffs)
                        std_diff = statistics.stdev(diffs) if len(diffs) > 1 else 0

                        if std_diff > 0:
                            for i, diff in enumerate(diffs):
                                z_diff = abs((diff - mean_diff) / std_diff)
                                if z_diff >= threshold_std:
                                    ts = timestamps[i + 1]
                                    val = values[i + 1]
                                    anomaly = Anomaly(
                                        metric_type=metric_type,
                                        anomaly_type=AnomalyType.PATTERN_BREAK,
                                        timestamp=ts,
                                        value=val,
                                        expected_value=values[i] + mean_diff,
                                        deviation=z_diff,
                                        severity="medium",
                                        description=f"Pattern break at {ts.isoformat()}",
                                        metadata={"diff_z_score": z_diff},
                                    )
                                    # Avoid duplicates
                                    if not any(
                                        a.timestamp == ts and a.metric_type == metric_type
                                        for a in anomalies
                                    ):
                                        anomalies.append(anomaly)

                                        # Invoke callback if provided
                                        if callback:
                                            try:
                                                callback.invoke(anomaly, {
                                                    "loop_id": loop_id,
                                                    "detection_method": "pattern_break",
                                                    "threshold_std": threshold_std,
                                                })
                                            except Exception as e:
                                                logger.error(
                                                    f"Anomaly callback failed: {e}",
                                                    extra={
                                                        "metric_type": metric_type.name,
                                                        "anomaly_type": AnomalyType.PATTERN_BREAK,
                                                    },
                                                )
                                                raise

            # Sort by severity and timestamp
            severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            return sorted(
                anomalies,
                key=lambda a: (severity_order.get(a.severity, 4), a.timestamp),
            )

    def analyze_correlations(
        self,
        loop_id: Optional[str] = None,
        min_samples: int = 5,
    ) -> List[CorrelationResult]:
        """
        Analyze correlations between all metric pairs.

        Computes Pearson correlation coefficient for each pair of metrics
        that have sufficient overlapping data points.

        Args:
            loop_id: Optional loop filter
            min_samples: Minimum paired samples required

        Returns:
            List of CorrelationResult for significant correlations

        Example:
            >>> correlations = analyzer.analyze_correlations()
            >>> for corr in correlations:
            ...     if corr.is_significant():
            ...         print(f"{corr.metric_a.name} <-> {corr.metric_b.name}: "
            ...               f"r={corr.correlation_coefficient:.2f}")
        """
        with self._lock:
            correlations: List[CorrelationResult] = []
            metric_types = list(MetricType)

            for i, metric_a in enumerate(metric_types):
                for metric_b in metric_types[i + 1:]:
                    corr = self._compute_correlation(
                        metric_a, metric_b, loop_id, min_samples
                    )
                    if corr:
                        correlations.append(corr)

            # Sort by absolute correlation (strongest first)
            return sorted(
                correlations,
                key=lambda c: abs(c.correlation_coefficient),
                reverse=True,
            )

    def _compute_correlation(
        self,
        metric_a: MetricType,
        metric_b: MetricType,
        loop_id: Optional[str],
        min_samples: int,
    ) -> Optional[CorrelationResult]:
        """Compute correlation between two metrics."""
        history_a = self._collector.get_metric_history(metric_a, loop_id)
        history_b = self._collector.get_metric_history(metric_b, loop_id)

        if not history_a or not history_b:
            return None

        # Align by timestamp (match closest timestamps)
        paired_values = self._align_time_series(history_a, history_b)

        if len(paired_values) < min_samples:
            return None

        values_a = [p[0] for p in paired_values]
        values_b = [p[1] for p in paired_values]

        # Pearson correlation coefficient
        n = len(values_a)
        mean_a = statistics.mean(values_a)
        mean_b = statistics.mean(values_b)

        numerator = sum((a - mean_a) * (b - mean_b) for a, b in zip(values_a, values_b))

        std_a = math.sqrt(sum((a - mean_a) ** 2 for a in values_a))
        std_b = math.sqrt(sum((b - mean_b) ** 2 for b in values_b))

        if std_a == 0 or std_b == 0:
            return None

        r = numerator / (std_a * std_b)

        # Approximate p-value using t-distribution
        # t = r * sqrt((n-2) / (1-r^2))
        if abs(r) >= 1:
            p_value = 0.0
        else:
            t_stat = r * math.sqrt((n - 2) / (1 - r ** 2))
            # Approximate p-value (two-tailed) for large n
            p_value = 2 * (1 - self._normal_cdf(abs(t_stat)))

        return CorrelationResult(
            metric_a=metric_a,
            metric_b=metric_b,
            correlation_coefficient=r,
            p_value=p_value,
            sample_size=n,
        )

    def _align_time_series(
        self,
        series_a: List[Tuple[datetime, float]],
        series_b: List[Tuple[datetime, float]],
        tolerance: timedelta = timedelta(minutes=5),
    ) -> List[Tuple[float, float]]:
        """
        Align two time series by timestamp.

        Matches values with timestamps within tolerance.
        """
        paired = []

        for ts_a, val_a in series_a:
            for ts_b, val_b in series_b:
                if abs((ts_a - ts_b).total_seconds()) <= tolerance.total_seconds():
                    paired.append((val_a, val_b))
                    break

        return paired

    def _normal_cdf(self, x: float) -> float:
        """Approximate standard normal CDF."""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def get_comparative_analysis(
        self,
        loop_ids: List[str],
    ) -> Dict[str, Dict[MetricType, MetricStatistics]]:
        """
        Compare metrics across multiple loops.

        Args:
            loop_ids: List of loop IDs to compare

        Returns:
            Dictionary mapping loop_id to metric statistics

        Example:
            >>> comparison = analyzer.get_comparative_analysis(["loop-001", "loop-002"])
            >>> for loop_id, stats in comparison.items():
            ...     print(f"{loop_id}:")
            ...     for metric_type, stat in stats.items():
            ...         print(f"  {metric_type.name}: mean={stat.mean:.2f}")
        """
        with self._lock:
            comparison: Dict[str, Dict[MetricType, MetricStatistics]] = {}

            for loop_id in loop_ids:
                loop_stats: Dict[MetricType, MetricStatistics] = {}

                for metric_type in MetricType:
                    stats = self._collector.get_statistics(metric_type, loop_id)
                    if stats:
                        loop_stats[metric_type] = stats

                if loop_stats:
                    comparison[loop_id] = loop_stats

            return comparison

    def generate_insights(
        self,
        loop_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate predictive insights from metrics analysis.

        Combines trend analysis, anomaly detection, and correlation
        to provide actionable insights.

        Args:
            loop_id: Optional loop filter

        Returns:
            Dictionary with insights and recommendations

        Example:
            >>> insights = analyzer.generate_insights()
            >>> print(insights["summary"])
            >>> for rec in insights["recommendations"]:
            ...     print(f"- {rec}")
        """
        with self._lock:
            trends = self.detect_trends(loop_id)
            anomalies = self.detect_anomalies(loop_id)
            correlations = self.analyze_correlations(loop_id)

            # Generate insights
            insights = {
                "summary": self._generate_summary(trends, anomalies, correlations),
                "trends": {k.name: v.to_dict() for k, v in trends.items()},
                "anomalies": [a.to_dict() for a in anomalies],
                "correlations": [c.to_dict() for c in correlations if c.is_significant()],
                "recommendations": self._generate_recommendations(trends, anomalies, correlations),
                "risk_assessment": self._assess_risk(trends, anomalies),
            }

            return insights

    def _generate_summary(
        self,
        trends: Dict[MetricType, TrendAnalysis],
        anomalies: List[Anomaly],
        correlations: List[CorrelationResult],
    ) -> str:
        """Generate executive summary."""
        parts = []

        # Trend summary
        positive_trends = sum(1 for t in trends.values() if t.is_positive())
        total_trends = len(trends)

        if total_trends > 0:
            parts.append(f"Analyzed {total_trends} metrics: {positive_trends} showing improvement.")

        # Anomaly summary
        if anomalies:
            critical_count = sum(1 for a in anomalies if a.severity == "critical")
            high_count = sum(1 for a in anomalies if a.severity == "high")
            if critical_count > 0:
                parts.append(f"WARNING: {critical_count} critical anomalies detected.")
            elif high_count > 0:
                parts.append(f"Notice: {high_count} high-severity anomalies detected.")

        # Correlation summary
        significant_corrs = [c for c in correlations if c.is_significant()]
        if significant_corrs:
            strong_corrs = [c for c in significant_corrs if c.strength == "strong"]
            if strong_corrs:
                parts.append(
                    f"Found {len(strong_corrs)} strong correlations "
                    "between metrics."
                )

        return " ".join(parts) if parts else "Insufficient data for analysis."

    def _generate_recommendations(
        self,
        trends: Dict[MetricType, TrendAnalysis],
        anomalies: List[Anomaly],
        correlations: List[CorrelationResult],
    ) -> List[str]:
        """Generate actionable recommendations."""
        recommendations = []

        # Based on negative trends
        for metric_type, trend in trends.items():
            if not trend.is_positive() and trend.confidence > 0.5:
                recommendations.append(
                    f"Address declining {metric_type.name.replace('_', ' ')} "
                    f"(trend: {trend.direction}, confidence: {trend.confidence:.0%})"
                )

        # Based on anomalies
        critical_anomalies = [a for a in anomalies if a.severity in ("critical", "high")]
        for anomaly in critical_anomalies[:3]:  # Top 3
            recommendations.append(
                f"Investigate {anomaly.anomaly_type} in {anomaly.metric_type.name}: "
                f"{anomaly.description}"
            )

        # Based on correlations
        for corr in correlations:
            if corr.is_significant() and corr.strength == "strong":
                if corr.correlation_coefficient < 0:
                    recommendations.append(
                        f"Strong negative correlation between {corr.metric_a.name} "
                        f"and {corr.metric_b.name} - optimizing one may improve the other"
                    )

        return recommendations

    def _assess_risk(
        self,
        trends: Dict[MetricType, TrendAnalysis],
        anomalies: List[Anomaly],
    ) -> Dict[str, Any]:
        """Assess overall risk level."""
        risk_factors = []
        risk_score = 0

        # Risk from negative trends
        for metric_type, trend in trends.items():
            if not trend.is_positive() and trend.confidence > 0.7:
                risk_factors.append({
                    "type": "negative_trend",
                    "metric": metric_type.name,
                    "severity": "medium",
                })
                risk_score += 1

        # Risk from anomalies
        for anomaly in anomalies:
            if anomaly.severity == "critical":
                risk_factors.append({
                    "type": "anomaly",
                    "metric": anomaly.metric_type.name,
                    "severity": "critical",
                })
                risk_score += 3
            elif anomaly.severity == "high":
                risk_factors.append({
                    "type": "anomaly",
                    "metric": anomaly.metric_type.name,
                    "severity": "high",
                })
                risk_score += 2

        # Determine overall risk level
        if risk_score >= 5:
            risk_level = "high"
        elif risk_score >= 2:
            risk_level = "medium"
        elif risk_score >= 1:
            risk_level = "low"
        else:
            risk_level = "minimal"

        return {
            "level": risk_level,
            "score": risk_score,
            "factors": risk_factors,
        }

    def export_analysis(
        self,
        loop_id: Optional[str] = None,
        format: str = "json",
    ) -> str:
        """
        Export complete analysis to string.

        Args:
            loop_id: Optional loop filter
            format: Export format ("json" or "text")

        Returns:
            Formatted analysis report

        Example:
            >>> json_report = analyzer.export_analysis(format="json")
            >>> text_report = analyzer.export_analysis(format="text")
        """
        import json

        insights = self.generate_insights(loop_id)

        if format == "json":
            return json.dumps(insights, indent=2)

        elif format == "text":
            lines = [
                "=" * 60,
                "METRICS ANALYSIS REPORT",
                "=" * 60,
                f"Generated: {datetime.now(timezone.utc).isoformat()}",
                f"Loop: {loop_id or 'all'}",
                "",
                "SUMMARY",
                "-" * 40,
                insights["summary"],
                "",
                "TRENDS",
                "-" * 40,
            ]

            for metric_name, trend_data in insights["trends"].items():
                lines.append(
                    f"  {metric_name}: {trend_data['direction']} "
                    f"(confidence: {trend_data['confidence']:.0%})"
                )

            if insights["anomalies"]:
                lines.extend(["", "ANOMALIES", "-" * 40])
                for anomaly in insights["anomalies"]:
                    lines.append(
                        f"  [{anomaly['severity'].upper()}] {anomaly['metric_type']}: "
                        f"{anomaly['description']}"
                    )

            if insights["correlations"]:
                lines.extend(["", "CORRELATIONS", "-" * 40])
                for corr in insights["correlations"]:
                    lines.append(
                        f"  {corr['metric_a']} <-> {corr['metric_b']}: "
                        f"r={corr['correlation_coefficient']:.2f} "
                        f"({corr['strength']} {corr['relationship']})"
                    )

            lines.extend(["", "RECOMMENDATIONS", "-" * 40])
            for rec in insights["recommendations"]:
                lines.append(f"  - {rec}")

            lines.extend(["", "RISK ASSESSMENT", "-" * 40])
            risk = insights["risk_assessment"]
            lines.append(f"  Level: {risk['level'].upper()} (score: {risk['score']})")

            return "\n".join(lines)

        else:
            raise ValueError(f"Unsupported format: {format}")
