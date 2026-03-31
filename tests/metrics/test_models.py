"""
Tests for GAIA Metrics Models

Tests for MetricType, MetricSnapshot, MetricStatistics, and MetricsReport.
"""

from datetime import datetime, timedelta, timezone

import pytest

from gaia.metrics.models import (
    MetricSnapshot,
    MetricsReport,
    MetricStatistics,
    MetricType,
)


class TestMetricType:
    """Tests for MetricType enumeration."""

    def test_metric_type_categories(self):
        """Test metric type category classification."""
        assert MetricType.TOKEN_EFFICIENCY.category() == "efficiency"
        assert MetricType.CONTEXT_UTILIZATION.category() == "efficiency"
        assert MetricType.QUALITY_VELOCITY.category() == "quality"
        assert MetricType.DEFECT_DENSITY.category() == "quality"
        assert MetricType.MTTR.category() == "reliability"
        assert MetricType.AUDIT_COMPLETENESS.category() == "reliability"

    def test_metric_type_units(self):
        """Test metric type unit strings."""
        assert MetricType.TOKEN_EFFICIENCY.unit() == "tokens/feature"
        assert MetricType.CONTEXT_UTILIZATION.unit() == "percentage"
        assert MetricType.QUALITY_VELOCITY.unit() == "iterations"
        assert MetricType.DEFECT_DENSITY.unit() == "defects/KLOC"
        assert MetricType.MTTR.unit() == "hours"
        assert MetricType.AUDIT_COMPLETENESS.unit() == "percentage"

    def test_higher_better_classification(self):
        """Test which metrics are better when higher."""
        # Higher is better
        assert MetricType.TOKEN_EFFICIENCY.is_higher_better() is True
        assert MetricType.CONTEXT_UTILIZATION.is_higher_better() is True
        assert MetricType.AUDIT_COMPLETENESS.is_higher_better() is True

        # Lower is better
        assert MetricType.QUALITY_VELOCITY.is_higher_better() is False
        assert MetricType.DEFECT_DENSITY.is_higher_better() is False
        assert MetricType.MTTR.is_higher_better() is False


class TestMetricSnapshot:
    """Tests for MetricSnapshot dataclass."""

    @pytest.fixture
    def sample_snapshot(self):
        """Create a sample snapshot for testing."""
        return MetricSnapshot(
            timestamp=datetime.now(timezone.utc),
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metrics={
                MetricType.TOKEN_EFFICIENCY: 0.85,
                MetricType.CONTEXT_UTILIZATION: 0.72,
            },
            metadata={"agent": "senior-developer"},
        )

    def test_snapshot_creation(self, sample_snapshot):
        """Test snapshot creation with metrics."""
        assert sample_snapshot.loop_id == "loop-001"
        assert sample_snapshot.phase == "DEVELOPMENT"
        assert len(sample_snapshot.metrics) == 2

    def test_snapshot_subscript_access(self, sample_snapshot):
        """Test subscript notation for metric access."""
        assert sample_snapshot[MetricType.TOKEN_EFFICIENCY] == 0.85
        assert sample_snapshot[MetricType.CONTEXT_UTILIZATION] == 0.72
        assert sample_snapshot[MetricType.MTTR] is None

    def test_snapshot_get_with_default(self, sample_snapshot):
        """Test get method with default value."""
        assert sample_snapshot.get(MetricType.TOKEN_EFFICIENCY) == 0.85
        assert sample_snapshot.get(MetricType.MTTR, 0.0) == 0.0
        assert sample_snapshot.get(MetricType.MTTR, 1.0) == 1.0

    def test_snapshot_with_metric(self, sample_snapshot):
        """Test creating updated snapshot (immutable)."""
        new_snapshot = sample_snapshot.with_metric(MetricType.TOKEN_EFFICIENCY, 0.90)

        # Original unchanged
        assert sample_snapshot[MetricType.TOKEN_EFFICIENCY] == 0.85
        # New snapshot has updated value
        assert new_snapshot[MetricType.TOKEN_EFFICIENCY] == 0.90
        # Other metrics preserved
        assert new_snapshot[MetricType.CONTEXT_UTILIZATION] == 0.72

    def test_snapshot_with_metadata(self, sample_snapshot):
        """Test creating snapshot with updated metadata."""
        new_snapshot = sample_snapshot.with_metadata(agent="qa-specialist", score=0.95)

        # Original unchanged
        assert sample_snapshot.metadata["agent"] == "senior-developer"
        # New snapshot has updated metadata
        assert new_snapshot.metadata["agent"] == "qa-specialist"
        assert new_snapshot.metadata["score"] == 0.95

    def test_snapshot_to_dict(self, sample_snapshot):
        """Test dictionary serialization."""
        data = sample_snapshot.to_dict()

        assert data["loop_id"] == "loop-001"
        assert data["phase"] == "DEVELOPMENT"
        assert "TOKEN_EFFICIENCY" in data["metrics"]
        assert data["metrics"]["TOKEN_EFFICIENCY"] == 0.85
        assert data["metadata"]["agent"] == "senior-developer"

    def test_snapshot_from_dict(self, sample_snapshot):
        """Test dictionary deserialization."""
        data = sample_snapshot.to_dict()
        restored = MetricSnapshot.from_dict(data)

        assert restored.loop_id == sample_snapshot.loop_id
        assert restored.phase == sample_snapshot.phase
        assert (
            restored[MetricType.TOKEN_EFFICIENCY]
            == sample_snapshot[MetricType.TOKEN_EFFICIENCY]
        )

    def test_snapshot_quality_check_pass(self):
        """Test quality check with passing metrics."""
        snapshot = MetricSnapshot(
            timestamp=datetime.now(timezone.utc),
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metrics={
                MetricType.TOKEN_EFFICIENCY: 0.95,
                MetricType.CONTEXT_UTILIZATION: 0.92,
                MetricType.QUALITY_VELOCITY: 2.0,
                MetricType.DEFECT_DENSITY: 1.5,
                MetricType.MTTR: 1.0,
                MetricType.AUDIT_COMPLETENESS: 1.0,
            },
        )

        passed, failures = snapshot.quality_check(threshold=0.90)
        assert passed is True
        assert len(failures) == 0

    def test_snapshot_quality_check_fail(self):
        """Test quality check with failing metrics."""
        snapshot = MetricSnapshot(
            timestamp=datetime.now(timezone.utc),
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metrics={
                MetricType.CONTEXT_UTILIZATION: 0.50,  # Below threshold
                MetricType.QUALITY_VELOCITY: 8.0,  # Too many iterations
                MetricType.DEFECT_DENSITY: 10.0,  # High defect density
            },
        )

        passed, failures = snapshot.quality_check(threshold=0.90)
        assert passed is False
        assert "CONTEXT_UTILIZATION" in failures
        assert "QUALITY_VELOCITY" in failures
        assert "DEFECT_DENSITY" in failures

    def test_snapshot_summary(self, sample_snapshot):
        """Test human-readable summary generation."""
        summary = sample_snapshot.summary()

        assert "loop-001" in summary
        assert "DEVELOPMENT" in summary
        assert "TOKEN EFFICIENCY" in summary
        assert "CONTEXT UTILIZATION" in summary


class TestMetricStatistics:
    """Tests for MetricStatistics dataclass."""

    def test_statistics_from_values(self):
        """Test computing statistics from raw values."""
        values = [0.80, 0.85, 0.87, 0.90, 0.92]

        stats = MetricStatistics.from_values(MetricType.TOKEN_EFFICIENCY, values)

        assert stats.metric_type == MetricType.TOKEN_EFFICIENCY
        assert stats.count == 5
        assert abs(stats.mean - 0.868) < 0.01
        assert stats.median == 0.87
        assert stats.min_value == 0.80
        assert stats.max_value == 0.92
        assert stats.std_dev > 0

    def test_statistics_from_empty_values(self):
        """Test that empty values raises ValueError."""
        with pytest.raises(ValueError):
            MetricStatistics.from_values(MetricType.TOKEN_EFFICIENCY, [])

    def test_statistics_from_single_value(self):
        """Test statistics with single value."""
        stats = MetricStatistics.from_values(MetricType.TOKEN_EFFICIENCY, [0.85])

        assert stats.count == 1
        assert stats.mean == 0.85
        assert stats.std_dev == 0.0

    def test_statistics_trend_increasing(self):
        """Test trend detection for increasing values."""
        values = [0.70, 0.75, 0.80, 0.85, 0.90]

        stats = MetricStatistics.from_values(MetricType.TOKEN_EFFICIENCY, values)

        assert stats.trend == "increasing"

    def test_statistics_trend_decreasing(self):
        """Test trend detection for decreasing values."""
        values = [0.90, 0.85, 0.80, 0.75, 0.70]

        stats = MetricStatistics.from_values(MetricType.TOKEN_EFFICIENCY, values)

        assert stats.trend == "decreasing"

    def test_statistics_trend_stable(self):
        """Test trend detection for stable values."""
        values = [0.85, 0.85, 0.85, 0.85, 0.85]

        stats = MetricStatistics.from_values(MetricType.TOKEN_EFFICIENCY, values)

        assert stats.trend == "stable"

    def test_statistics_to_dict(self):
        """Test dictionary serialization."""
        values = [0.80, 0.85, 0.87, 0.90, 0.92]
        stats = MetricStatistics.from_values(MetricType.TOKEN_EFFICIENCY, values)

        data = stats.to_dict()

        assert data["metric_type"] == "TOKEN_EFFICIENCY"
        assert data["count"] == 5
        assert "mean" in data
        assert "trend" in data


class TestMetricsReport:
    """Tests for MetricsReport dataclass."""

    @pytest.fixture
    def sample_report(self):
        """Create a sample report for testing."""
        stats = MetricStatistics(
            metric_type=MetricType.TOKEN_EFFICIENCY,
            count=10,
            mean=0.85,
            median=0.87,
            std_dev=0.05,
            min_value=0.75,
            max_value=0.95,
            trend="increasing",
        )

        return MetricsReport(
            generated_at=datetime.now(timezone.utc),
            loop_id="loop-001",
            phase="DEVELOPMENT",
            snapshot_count=10,
            metric_statistics={MetricType.TOKEN_EFFICIENCY: stats},
            overall_health=0.85,
            recommendations=["Optimize prompts to reduce token consumption"],
        )

    def test_report_creation(self, sample_report):
        """Test report creation."""
        assert sample_report.loop_id == "loop-001"
        assert sample_report.phase == "DEVELOPMENT"
        assert sample_report.snapshot_count == 10
        assert len(sample_report.metric_statistics) == 1

    def test_report_to_dict(self, sample_report):
        """Test dictionary serialization."""
        data = sample_report.to_dict()

        assert data["loop_id"] == "loop-001"
        assert data["snapshot_count"] == 10
        assert data["overall_health"] == 0.85
        assert "TOKEN_EFFICIENCY" in data["metric_statistics"]

    def test_report_health_status_excellent(self):
        """Test health status classification - excellent."""
        report = MetricsReport(
            generated_at=datetime.now(timezone.utc),
            overall_health=0.96,
        )
        assert report.get_health_status() == "excellent"

    def test_report_health_status_good(self):
        """Test health status classification - good."""
        report = MetricsReport(
            generated_at=datetime.now(timezone.utc),
            overall_health=0.88,
        )
        assert report.get_health_status() == "good"

    def test_report_health_status_acceptable(self):
        """Test health status classification - acceptable."""
        report = MetricsReport(
            generated_at=datetime.now(timezone.utc),
            overall_health=0.75,
        )
        assert report.get_health_status() == "acceptable"

    def test_report_health_status_needs_improvement(self):
        """Test health status classification - needs improvement."""
        report = MetricsReport(
            generated_at=datetime.now(timezone.utc),
            overall_health=0.60,
        )
        assert report.get_health_status() == "needs_improvement"

    def test_report_health_status_critical(self):
        """Test health status classification - critical."""
        report = MetricsReport(
            generated_at=datetime.now(timezone.utc),
            overall_health=0.40,
        )
        assert report.get_health_status() == "critical"

    def test_report_summary(self, sample_report):
        """Test human-readable summary generation."""
        summary = sample_report.summary()

        assert "loop-001" in summary
        assert "DEVELOPMENT" in summary
        assert "85.0%" in summary
        assert "TOKEN_EFFICIENCY" in summary
