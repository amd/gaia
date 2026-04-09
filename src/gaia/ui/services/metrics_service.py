# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pipeline metrics service for GAIA Agent UI.

Provides business logic for querying and aggregating pipeline metrics:
- Real-time metrics snapshots
- Historical metrics data
- Aggregate statistics across pipelines
"""

import asyncio
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from gaia.metrics import MetricsCollector, MetricSnapshot, MetricType
from gaia.pipeline.metrics_collector import (
    PipelineMetricsCollector,
    get_all_collectors,
    get_pipeline_collector,
)
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class MetricsService:
    """
    Service for pipeline metrics queries and aggregation.

    This service provides:
    - Real-time metrics snapshots for individual pipelines
    - Historical metrics data with filtering and pagination
    - Aggregate statistics across multiple pipelines
    - Health scoring and recommendations
    """

    def __init__(self):
        """Initialize metrics service."""
        self._collectors_cache: Dict[str, PipelineMetricsCollector] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(seconds=5)  # 5 second cache

    def _get_collector(self, pipeline_id: str) -> Optional[PipelineMetricsCollector]:
        """
        Get metrics collector for a pipeline.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            PipelineMetricsCollector or None if not found
        """
        # Try cache first
        if pipeline_id in self._collectors_cache:
            return self._collectors_cache[pipeline_id]

        # Get from global registry
        try:
            collector = get_pipeline_collector(pipeline_id)
            self._collectors_cache[pipeline_id] = collector
            return collector
        except Exception:
            return None

    def _refresh_collectors(self) -> None:
        """Refresh collectors cache from global registry."""
        now = datetime.now(timezone.utc)
        if self._cache_timestamp and (now - self._cache_timestamp) < self._cache_ttl:
            return  # Cache still valid

        self._collectors_cache = get_all_collectors()
        self._cache_timestamp = now

    def get_metrics_snapshot(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        """
        Get real-time metrics snapshot for a pipeline.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            Dictionary containing metrics snapshot or None if not found
        """
        collector = self._get_collector(pipeline_id)
        if not collector:
            return None

        return collector.get_metrics_snapshot()

    def get_metrics_summary(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        """
        Get metrics summary for a pipeline.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            Dictionary containing metrics summary or None if not found
        """
        collector = self._get_collector(pipeline_id)
        if not collector:
            return None

        return collector.generate_report()

    def get_phase_timings(self, pipeline_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get phase timing information for a pipeline.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            Dictionary mapping phase names to timing data
        """
        collector = self._get_collector(pipeline_id)
        if not collector:
            return {}

        snapshot = collector.get_metrics_snapshot()
        return snapshot.get("phase_timings", {})

    def get_loop_metrics(
        self, pipeline_id: str, loop_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get loop metrics for a pipeline.

        Args:
            pipeline_id: Pipeline identifier
            loop_id: Optional specific loop ID

        Returns:
            Dictionary containing loop metrics
        """
        collector = self._get_collector(pipeline_id)
        if not collector:
            return {}

        snapshot = collector.get_metrics_snapshot()
        loop_metrics = snapshot.get("loop_metrics", {})

        if loop_id:
            return loop_metrics.get(loop_id, {})
        return loop_metrics

    def get_state_transitions(self, pipeline_id: str) -> List[Dict[str, Any]]:
        """
        Get state transition history for a pipeline.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            List of state transition records
        """
        collector = self._get_collector(pipeline_id)
        if not collector:
            return []

        return [t.to_dict() for t in collector.get_state_transitions()]

    def get_agent_selections(self, pipeline_id: str) -> List[Dict[str, Any]]:
        """
        Get agent selection decisions for a pipeline.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            List of agent selection records
        """
        collector = self._get_collector(pipeline_id)
        if not collector:
            return []

        return collector.get_agent_selections()

    def get_quality_history(self, pipeline_id: str) -> List[Tuple[str, str, float]]:
        """
        Get quality score history for a pipeline.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            List of (loop_id, phase, score) tuples
        """
        collector = self._get_collector(pipeline_id)
        if not collector:
            return []

        return collector.get_quality_history()

    def get_defects_by_type(self, pipeline_id: str) -> Dict[str, int]:
        """
        Get defect counts by type for a pipeline.

        Args:
            pipeline_id: Pipeline identifier

        Returns:
            Dictionary mapping defect types to counts
        """
        collector = self._get_collector(pipeline_id)
        if not collector:
            return {}

        return collector.get_defects_by_type()

    def get_metrics_history(
        self,
        pipeline_id: str,
        metric_type: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get historical metrics data for a pipeline.

        Args:
            pipeline_id: Pipeline identifier
            metric_type: Optional metric type filter
            start_time: Optional start of time range
            end_time: Optional end of time range
            limit: Maximum number of records to return

        Returns:
            List of metric history records
        """
        collector = self._get_collector(pipeline_id)
        if not collector:
            return []

        base_collector = collector.get_base_collector()
        history = []

        # Get all snapshots
        snapshots = base_collector.get_all_snapshots()

        # Filter by time range
        if start_time:
            snapshots = [s for s in snapshots if s.timestamp >= start_time]
        if end_time:
            snapshots = [s for s in snapshots if s.timestamp <= end_time]

        # Convert to history format
        for snapshot in snapshots[-limit:]:  # Get most recent
            for m_type, value in snapshot.metrics.items():
                if metric_type and m_type.name != metric_type:
                    continue

                history.append(
                    {
                        "timestamp": snapshot.timestamp.isoformat(),
                        "loop_id": snapshot.loop_id,
                        "phase": snapshot.phase,
                        "metric_type": m_type.name,
                        "value": value,
                        "metadata": snapshot.metadata,
                    }
                )

        return sorted(history, key=lambda x: x["timestamp"])

    def get_aggregate_metrics(
        self,
        pipeline_ids: Optional[List[str]] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Get aggregate metrics across multiple pipelines.

        Args:
            pipeline_ids: Optional list of pipeline IDs to aggregate
            start_time: Optional start of time range
            end_time: Optional end of time range

        Returns:
            Dictionary containing aggregate statistics
        """
        self._refresh_collectors()

        if pipeline_ids is None:
            pipeline_ids = list(self._collectors_cache.keys())

        if not pipeline_ids:
            return {
                "total_pipelines": 0,
                "metric_statistics": {},
                "overall_health": 0.0,
                "recommendations": [],
            }

        # Aggregate data across pipelines
        all_metrics: Dict[str, List[float]] = defaultdict(list)
        total_duration = 0.0
        total_tokens = 0
        total_defects = 0
        all_quality_scores = []

        for pid in pipeline_ids:
            collector = self._collectors_cache.get(pid)
            if not collector:
                continue

            snapshot = collector.get_metrics_snapshot()

            # Aggregate phase timings
            for phase_timing in snapshot.get("phase_timings", {}).values():
                if phase_timing.get("duration_seconds"):
                    total_duration += phase_timing["duration_seconds"]
                if phase_timing.get("token_count"):
                    total_tokens += phase_timing["token_count"]

            # Aggregate defects
            for count in snapshot.get("defects_by_type", {}).values():
                total_defects += count

            # Aggregate quality scores
            all_quality_scores.extend(
                [score for _, _, score in collector.get_quality_history()]
            )

            # Collect metrics for statistics
            base_collector = collector.get_base_collector()
            for m_type in MetricType:
                values = base_collector.get_metric_history(m_type)
                all_metrics[m_type.name].extend([v for _, v in values])

        # Calculate statistics for each metric type
        metric_statistics = {}
        for metric_type, values in all_metrics.items():
            if not values:
                continue

            sorted_values = sorted(values)
            n = len(values)

            metric_statistics[metric_type] = {
                "metric_type": metric_type,
                "count": n,
                "mean": statistics.mean(values) if values else 0.0,
                "median": statistics.median(values) if values else 0.0,
                "std_dev": statistics.stdev(values) if n > 1 else 0.0,
                "min_value": min(values) if values else 0.0,
                "max_value": max(values) if values else 0.0,
                "trend": self._compute_trend(values),
                "percentiles": {
                    "p25": (
                        sorted_values[int(n * 0.25)]
                        if n >= 4
                        else sorted_values[0] if values else 0.0
                    ),
                    "p75": (
                        sorted_values[int(n * 0.75)]
                        if n >= 4
                        else sorted_values[-1] if values else 0.0
                    ),
                    "p90": (
                        sorted_values[int(n * 0.90)]
                        if n >= 10
                        else sorted_values[-1] if values else 0.0
                    ),
                },
            }

        # Calculate overall health score
        health_scores = []
        for metric_type, stats in metric_statistics.items():
            if metric_type in [
                "TPS",
                "TOKEN_EFFICIENCY",
                "CONTEXT_UTILIZATION",
                "AUDIT_COMPLETENESS",
                "RESOURCE_UTILIZATION",
            ]:
                health_scores.append(max(0, min(1, stats["mean"])))
            elif metric_type in ["TTFT", "PHASE_DURATION", "DEFECT_DENSITY", "MTTR"]:
                # Lower is better - invert
                if metric_type == "TTFT":
                    health_scores.append(max(0, min(1, (5 - stats["mean"]) / 5)))
                elif metric_type == "PHASE_DURATION":
                    health_scores.append(max(0, min(1, (300 - stats["mean"]) / 300)))
                elif metric_type == "DEFECT_DENSITY":
                    health_scores.append(max(0, min(1, (10 - stats["mean"]) / 10)))
                elif metric_type == "MTTR":
                    health_scores.append(max(0, min(1, (8 - stats["mean"]) / 8)))

        overall_health = statistics.mean(health_scores) if health_scores else 0.0

        # Generate recommendations
        recommendations = self._generate_recommendations(metric_statistics)

        return {
            "total_pipelines": len(pipeline_ids),
            "time_range": {
                "start": start_time.isoformat() if start_time else None,
                "end": end_time.isoformat() if end_time else None,
            },
            "summary": {
                "total_duration_seconds": total_duration,
                "total_tokens": total_tokens,
                "total_defects": total_defects,
                "avg_quality_score": (
                    statistics.mean(all_quality_scores) if all_quality_scores else 0.0
                ),
            },
            "metric_statistics": metric_statistics,
            "overall_health": overall_health,
            "recommendations": recommendations,
        }

    def _compute_trend(self, values: List[float], threshold: float = 0.05) -> str:
        """
        Compute trend direction from values.

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

    def _generate_recommendations(
        self, metric_statistics: Dict[str, Dict[str, Any]]
    ) -> List[str]:
        """
        Generate improvement recommendations based on metrics.

        Args:
            metric_statistics: Dictionary of metric statistics

        Returns:
            List of recommendation strings
        """
        recommendations = []

        for metric_type, stats in metric_statistics.items():
            mean = stats.get("mean", 0.0)

            if metric_type == "TPS" and mean < 10:
                recommendations.append(
                    "Consider optimizing LLM configuration for faster token generation"
                )

            elif metric_type == "TTFT" and mean > 2:
                recommendations.append(
                    "Time to first token is high - review model loading and prompt preparation"
                )

            elif metric_type == "PHASE_DURATION" and mean > 180:
                recommendations.append(
                    "Phase durations are long - consider breaking tasks into smaller units"
                )

            elif metric_type == "LOOP_ITERATION_COUNT" and mean > 5:
                recommendations.append(
                    "High iteration count - review initial requirements clarity"
                )

            elif metric_type == "HOOK_EXECUTION_TIME" and mean > 0.5:
                recommendations.append(
                    "Hook execution times are high - optimize hook implementations"
                )

            elif metric_type == "TOKEN_EFFICIENCY" and mean < 0.7:
                recommendations.append(
                    "Consider optimizing prompts to reduce token consumption"
                )

            elif metric_type == "CONTEXT_UTILIZATION" and mean < 0.5:
                recommendations.append(
                    "Context window underutilized - consider batching related tasks"
                )

        return recommendations

    def list_available_metrics(self) -> Dict[str, Any]:
        """
        List available metric types and categories.

        Returns:
            Dictionary with metric types grouped by category
        """
        categories = defaultdict(list)

        for metric_type in MetricType:
            categories[metric_type.category()].append(metric_type.name)

        return {
            "metric_types": [m.name for m in MetricType],
            "categories": dict(categories),
        }
