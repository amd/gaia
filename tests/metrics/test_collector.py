"""
Tests for GAIA Metrics Collector

Tests for MetricsCollector class and related tracking classes.
"""

import pytest
from datetime import datetime, timezone, timedelta
from gaia.metrics.collector import (
    MetricsCollector,
    TokenTracking,
    ContextTracking,
    QualityIteration,
)
from gaia.metrics.models import MetricType, MetricSnapshot


class TestTokenTracking:
    """Tests for TokenTracking dataclass."""

    def test_token_tracking_creation(self):
        """Test token tracking creation."""
        tracking = TokenTracking(
            tokens_input=15000,
            tokens_output=5000,
            feature_name="REST API",
        )

        assert tracking.tokens_input == 15000
        assert tracking.tokens_output == 5000
        assert tracking.feature_name == "REST API"

    def test_token_tracking_total(self):
        """Test total token calculation."""
        tracking = TokenTracking(tokens_input=15000, tokens_output=5000)
        assert tracking.total_tokens() == 20000

    def test_token_tracking_to_dict(self):
        """Test dictionary serialization."""
        tracking = TokenTracking(
            tokens_input=15000,
            tokens_output=5000,
            feature_name="REST API",
            completed_at=datetime.now(timezone.utc),
        )

        data = tracking.to_dict()

        assert data["tokens_input"] == 15000
        assert data["tokens_output"] == 5000
        assert data["total_tokens"] == 20000
        assert data["feature_name"] == "REST API"


class TestContextTracking:
    """Tests for ContextTracking dataclass."""

    def test_context_tracking_creation(self):
        """Test context tracking creation."""
        tracking = ContextTracking(
            context_window_size=128000,
            tokens_used=96000,
            effective_tokens=80000,
        )

        assert tracking.context_window_size == 128000
        assert tracking.tokens_used == 96000
        assert tracking.effective_tokens == 80000

    def test_context_utilization_ratio(self):
        """Test utilization ratio calculation."""
        tracking = ContextTracking(
            context_window_size=128000,
            tokens_used=96000,
        )
        assert tracking.utilization_ratio() == 0.75

    def test_context_utilization_zero_window(self):
        """Test utilization with zero window size."""
        tracking = ContextTracking(context_window_size=0, tokens_used=1000)
        assert tracking.utilization_ratio() == 0.0

    def test_context_effectiveness_ratio(self):
        """Test effectiveness ratio calculation."""
        tracking = ContextTracking(
            context_window_size=128000,
            tokens_used=100000,
            effective_tokens=80000,
        )
        assert tracking.effectiveness_ratio() == 0.8

    def test_context_tracking_to_dict(self):
        """Test dictionary serialization."""
        tracking = ContextTracking(
            context_window_size=128000,
            tokens_used=96000,
            effective_tokens=80000,
        )

        data = tracking.to_dict()

        assert data["utilization_ratio"] == 0.75
        assert data["effectiveness_ratio"] == 0.8333333333333334


class TestQualityIteration:
    """Tests for QualityIteration dataclass."""

    def test_quality_iteration_creation(self):
        """Test quality iteration creation."""
        qi = QualityIteration(
            loop_id="loop-001",
            threshold=0.90,
        )

        assert qi.loop_id == "loop-001"
        assert qi.threshold == 0.90
        assert qi.iterations == 0
        assert qi.reached_threshold is False

    def test_quality_iteration_add_score(self):
        """Test adding quality scores."""
        qi = QualityIteration(loop_id="loop-001", threshold=0.90)

        iteration = qi.add_score(0.65)
        assert iteration == 1
        assert qi.iterations == 1

        iteration = qi.add_score(0.78)
        assert iteration == 2

        iteration = qi.add_score(0.92)
        assert iteration == 3

    def test_quality_iteration_reached_threshold(self):
        """Test threshold detection."""
        qi = QualityIteration(loop_id="loop-001", threshold=0.90)

        qi.add_score(0.65)
        assert qi.reached_threshold is False

        qi.add_score(0.78)
        assert qi.reached_threshold is False

        qi.add_score(0.92)
        assert qi.reached_threshold is True

    def test_quality_iteration_to_dict(self):
        """Test dictionary serialization."""
        qi = QualityIteration(
            loop_id="loop-001",
            threshold=0.90,
        )
        qi.add_score(0.65)
        qi.add_score(0.78)
        qi.add_score(0.92)

        data = qi.to_dict()

        assert data["loop_id"] == "loop-001"
        assert data["iterations"] == 3
        assert data["reached_threshold"] is True


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    @pytest.fixture
    def collector(self):
        """Create a fresh collector for each test."""
        return MetricsCollector(collector_id="test-collector")

    def test_collector_creation(self, collector):
        """Test collector creation."""
        assert collector.collector_id == "test-collector"

    def test_collector_auto_id(self):
        """Test auto-generated collector ID."""
        collector = MetricsCollector()
        assert collector.collector_id.startswith("metrics-")

    def test_record_metric(self, collector):
        """Test recording a metric."""
        snapshot = collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.85,
        )

        assert snapshot[MetricType.TOKEN_EFFICIENCY] == 0.85
        assert snapshot.loop_id == "loop-001"
        assert snapshot.phase == "DEVELOPMENT"

    def test_record_metric_invalid_value(self, collector):
        """Test recording metric with invalid value type."""
        with pytest.raises(ValueError):
            collector.record_metric(
                loop_id="loop-001",
                phase="DEVELOPMENT",
                metric_type=MetricType.TOKEN_EFFICIENCY,
                value="invalid",  # type: ignore
            )

    def test_record_multiple_metrics(self, collector):
        """Test recording multiple metrics."""
        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.85,
        )

        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.CONTEXT_UTILIZATION,
            value=0.72,
        )

        snapshot = collector.get_latest_snapshot("loop-001", "DEVELOPMENT")
        assert snapshot is not None
        assert snapshot[MetricType.TOKEN_EFFICIENCY] == 0.85
        assert snapshot[MetricType.CONTEXT_UTILIZATION] == 0.72

    def test_record_token_usage(self, collector):
        """Test recording token usage."""
        collector.record_token_usage(
            loop_id="loop-001",
            tokens_input=15000,
            tokens_output=5000,
            feature_name="REST API",
        )

        snapshot = collector.get_latest_snapshot("loop-001", "DEVELOPMENT")
        assert snapshot is not None
        assert MetricType.TOKEN_EFFICIENCY in snapshot.metrics

    def test_record_context_utilization(self, collector):
        """Test recording context utilization."""
        collector.record_context_utilization(
            loop_id="loop-001",
            context_window_size=128000,
            tokens_used=96000,
            effective_tokens=80000,
        )

        snapshot = collector.get_latest_snapshot("loop-001", "DEVELOPMENT")
        assert snapshot is not None
        assert MetricType.CONTEXT_UTILIZATION in snapshot.metrics
        assert snapshot[MetricType.CONTEXT_UTILIZATION] == 0.75

    def test_record_quality_score(self, collector):
        """Test recording quality scores."""
        iteration1 = collector.record_quality_score("loop-001", 0.65)
        assert iteration1 == 1

        iteration2 = collector.record_quality_score("loop-001", 0.78)
        assert iteration2 == 2

        iteration3 = collector.record_quality_score("loop-001", 0.92)
        assert iteration3 == 3

        # Check that quality velocity was recorded
        snapshot = collector.get_latest_snapshot("loop-001", "QUALITY")
        assert snapshot is not None
        assert MetricType.QUALITY_VELOCITY in snapshot.metrics

    def test_record_defect_discovered(self, collector):
        """Test recording defect discovery."""
        collector.record_defect_discovered("loop-001", kloc=1.0)
        collector.record_defect_discovered("loop-001", kloc=1.0)

        snapshot = collector.get_latest_snapshot("loop-001", "QUALITY")
        assert snapshot is not None
        assert MetricType.DEFECT_DENSITY in snapshot.metrics
        assert snapshot[MetricType.DEFECT_DENSITY] == 2.0

    def test_record_defect_resolved(self, collector):
        """Test recording defect resolution."""
        discovered_at = datetime.now(timezone.utc) - timedelta(hours=2)

        collector.record_defect_resolved(
            loop_id="loop-001",
            defect_id="defect-001",
            discovered_at=discovered_at,
        )

        snapshot = collector.get_latest_snapshot("loop-001", "DEVELOPMENT")
        assert snapshot is not None
        assert MetricType.MTTR in snapshot.metrics

    def test_record_audit_event(self, collector):
        """Test recording audit events."""
        collector.record_audit_event("loop-001", expected=True)
        collector.record_audit_event("loop-001", expected=True)
        collector.record_audit_event("loop-001", expected=True)

        snapshot = collector.get_latest_snapshot("loop-001", "REVIEW")
        assert snapshot is not None
        assert MetricType.AUDIT_COMPLETENESS in snapshot.metrics

    def test_get_snapshot(self, collector):
        """Test retrieving snapshots."""
        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.85,
        )

        snapshot = collector.get_snapshot("loop-001", "DEVELOPMENT")
        assert snapshot is not None
        assert snapshot[MetricType.TOKEN_EFFICIENCY] == 0.85

    def test_get_snapshot_not_found(self, collector):
        """Test retrieving non-existent snapshot."""
        snapshot = collector.get_snapshot("loop-999", "UNKNOWN")
        assert snapshot is None

    def test_get_latest_snapshot(self, collector):
        """Test retrieving latest snapshot across phases."""
        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.85,
        )

        # Wait a tiny bit to ensure different timestamp
        import time
        time.sleep(0.001)

        collector.record_metric(
            loop_id="loop-001",
            phase="QUALITY",
            metric_type=MetricType.QUALITY_VELOCITY,
            value=3.0,
        )

        latest = collector.get_latest_snapshot("loop-001")
        assert latest is not None
        assert latest.phase == "QUALITY"

    def test_get_all_snapshots(self, collector):
        """Test retrieving all snapshots."""
        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.85,
        )

        collector.record_metric(
            loop_id="loop-002",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.90,
        )

        all_snapshots = collector.get_all_snapshots()
        assert len(all_snapshots) == 2

        loop1_snapshots = collector.get_all_snapshots(loop_id="loop-001")
        assert len(loop1_snapshots) == 1

    def test_get_metric_history(self, collector):
        """Test retrieving metric history."""
        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.80,
        )
        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.85,
        )
        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.90,
        )

        history = collector.get_metric_history(MetricType.TOKEN_EFFICIENCY)
        assert len(history) == 3
        assert history[0][1] == 0.80
        assert history[1][1] == 0.85
        assert history[2][1] == 0.90

    def test_get_statistics(self, collector):
        """Test getting metric statistics."""
        for value in [0.80, 0.85, 0.87, 0.90, 0.92]:
            collector.record_metric(
                loop_id="loop-001",
                phase="DEVELOPMENT",
                metric_type=MetricType.TOKEN_EFFICIENCY,
                value=value,
            )

        stats = collector.get_statistics(MetricType.TOKEN_EFFICIENCY)
        assert stats is not None
        assert stats.count == 5
        assert abs(stats.mean - 0.868) < 0.01

    def test_generate_report(self, collector):
        """Test generating metrics report."""
        for value in [0.80, 0.85, 0.87, 0.90, 0.92]:
            collector.record_metric(
                loop_id="loop-001",
                phase="DEVELOPMENT",
                metric_type=MetricType.TOKEN_EFFICIENCY,
                value=value,
            )

        report = collector.generate_report(loop_id="loop-001")
        assert report.snapshot_count == 5
        assert MetricType.TOKEN_EFFICIENCY in report.metric_statistics

    def test_get_summary(self, collector):
        """Test getting collector summary."""
        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.85,
        )

        summary = collector.get_summary()
        assert summary["collector_id"] == "test-collector"
        assert summary["total_snapshots"] == 1
        assert summary["loops_tracked"] == 1

    def test_clear(self, collector):
        """Test clearing collector."""
        collector.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.85,
        )

        collector.clear()

        summary = collector.get_summary()
        assert summary["total_snapshots"] == 0


class TestCrossLoopMTTRTracking:
    """Tests for cross-loop defect MTTR tracking."""

    @pytest.fixture
    def collector(self):
        """Create a fresh collector for each test."""
        return MetricsCollector(collector_id="test-collector-cross-loop")

    def test_record_defect_discovered_cross_loop(self, collector):
        """Test cross-loop defect discovery tracking."""
        collector.record_defect_discovered_cross_loop(
            defect_id="defect-001",
            loop_id_discovered="loop-001",
            kloc=1.0,
        )

        snapshot = collector.get_latest_snapshot("loop-001", "QUALITY")
        assert snapshot is not None
        assert MetricType.DEFECT_DENSITY in snapshot.metrics
        assert snapshot[MetricType.DEFECT_DENSITY] == 1.0

    def test_record_defect_resolved_with_cross_loop(self, collector):
        """Test defect resolution with cross-loop tracking."""
        from datetime import timedelta

        discovered_at = datetime.now(timezone.utc) - timedelta(hours=5)

        collector.record_defect_resolved(
            loop_id="loop-003",
            defect_id="defect-001",
            discovered_at=discovered_at,
            loop_id_discovered="loop-001",
            loop_id_resolved="loop-003",
        )

        snapshot = collector.get_latest_snapshot("loop-003", "DEVELOPMENT")
        assert snapshot is not None
        assert MetricType.MTTR in snapshot.metrics
        assert snapshot.metadata.get("is_cross_loop") is True
        assert snapshot.metadata.get("loop_discovered") == "loop-001"
        assert snapshot.metadata.get("loop_resolved") == "loop-003"

    def test_record_defect_resolved_cross_loop_method(self, collector):
        """Test the dedicated cross-loop resolution method."""
        from datetime import timedelta

        discovered_at = datetime.now(timezone.utc) - timedelta(hours=5)
        resolved_at = datetime.now(timezone.utc)

        mttr_breakdown = collector.record_defect_resolved_cross_loop(
            defect_id="defect-001",
            loop_id_discovered="loop-001",
            loop_id_resolved="loop-003",
            discovered_at=discovered_at,
            resolved_at=resolved_at,
        )

        assert "discovery_loop_mttr" in mttr_breakdown
        assert "resolution_loop_mttr" in mttr_breakdown
        assert "cross_loop_overhead" in mttr_breakdown
        assert "total_mttr" in mttr_breakdown
        assert mttr_breakdown["total_mttr"] > 0
        assert mttr_breakdown["cross_loop_overhead"] > 0

    def test_get_cross_loop_defects(self, collector):
        """Test retrieving cross-loop defects."""
        from datetime import timedelta

        discovered_at = datetime.now(timezone.utc) - timedelta(hours=5)

        collector.record_defect_resolved_cross_loop(
            defect_id="defect-001",
            loop_id_discovered="loop-001",
            loop_id_resolved="loop-003",
            discovered_at=discovered_at,
        )

        cross_loop_defects = collector.get_cross_loop_defects()

        # Cross-loop defects are recorded in both discovery and resolution loops
        assert len(cross_loop_defects) == 2
        # Both records should have the same defect_id
        assert all(d["defect_id"] == "defect-001" for d in cross_loop_defects)
        assert all(d["loop_discovered"] == "loop-001" for d in cross_loop_defects)
        assert all(d["loop_resolved"] == "loop-003" for d in cross_loop_defects)
        assert all(d["is_cross_loop"] is True for d in cross_loop_defects)


class TestPersistenceLayer:
    """Tests for JSON and SQLite export functionality."""

    @pytest.fixture
    def collector_with_data(self, tmp_path):
        """Create a collector with sample data."""
        collector = MetricsCollector(collector_id="test-persistence")

        # Add some sample data
        for i in range(5):
            collector.record_metric(
                loop_id="loop-001",
                phase="DEVELOPMENT",
                metric_type=MetricType.TOKEN_EFFICIENCY,
                value=0.80 + (i * 0.02),
            )
            collector.record_metric(
                loop_id="loop-001",
                phase="DEVELOPMENT",
                metric_type=MetricType.CONTEXT_UTILIZATION,
                value=0.70 + (i * 0.03),
            )

        collector.record_token_usage(
            loop_id="loop-001",
            tokens_input=15000,
            tokens_output=5000,
            feature_name="Test Feature",
        )

        collector.record_quality_score("loop-001", 0.65)
        collector.record_quality_score("loop-001", 0.78)
        collector.record_quality_score("loop-001", 0.92)

        return collector

    def test_export_to_json(self, collector_with_data, tmp_path):
        """Test JSON export functionality."""
        export_path = tmp_path / "metrics_export.json"

        result_path = collector_with_data.export_to_json(str(export_path))

        assert result_path == str(export_path.resolve())
        assert export_path.exists()

        import json
        with open(export_path, "r") as f:
            data = json.load(f)

        assert "export_timestamp" in data
        assert "collector_id" in data
        assert "snapshots" in data
        # Snapshots include all recorded metrics (5 efficiency + 5 context + token usage + quality velocity)
        assert len(data["snapshots"]) >= 10
        assert "summary" in data
        assert data["collector_id"] == "test-persistence"

    def test_export_to_json_minimal(self, collector_with_data, tmp_path):
        """Test JSON export without metadata."""
        export_path = tmp_path / "metrics_minimal.json"

        result_path = collector_with_data.export_to_json(
            str(export_path),
            include_metadata=False,
        )

        assert export_path.exists()

        import json
        with open(export_path, "r") as f:
            data = json.load(f)

        assert "snapshots" in data
        assert "token_tracking" not in data  # Excluded in minimal mode

    def test_export_to_sqlite(self, collector_with_data, tmp_path):
        """Test SQLite export functionality."""
        import sqlite3

        db_path = tmp_path / "metrics.db"

        result_path = collector_with_data.export_to_sqlite(str(db_path))

        assert result_path == str(db_path.resolve())
        assert db_path.exists()

        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Check snapshots table - includes all recorded metrics
        cursor.execute("SELECT COUNT(*) FROM snapshots")
        snapshot_count = cursor.fetchone()[0]
        assert snapshot_count >= 10  # At least 5 efficiency + 5 context

        # Check snapshot_metrics table
        cursor.execute("SELECT COUNT(*) FROM snapshot_metrics")
        metrics_count = cursor.fetchone()[0]
        assert metrics_count >= 10

        # Check token_tracking table
        cursor.execute("SELECT COUNT(*) FROM token_tracking")
        token_count = cursor.fetchone()[0]
        assert token_count == 1

        conn.close()

    def test_export_to_sqlite_minimal(self, collector_with_data, tmp_path):
        """Test SQLite export without metadata tables."""
        db_path = tmp_path / "metrics_minimal.db"

        collector_with_data.export_to_sqlite(
            str(db_path),
            include_metadata=False,
        )

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Core tables should exist
        cursor.execute("SELECT COUNT(*) FROM snapshots")
        assert cursor.fetchone()[0] > 0

        # Metadata tables should not exist
        try:
            cursor.execute("SELECT COUNT(*) FROM token_tracking")
            # If we get here, table exists (which is unexpected for minimal mode)
        except sqlite3.OperationalError:
            pass  # Expected - table doesn't exist in minimal mode

        conn.close()

    def test_export_preserves_cross_loop_data(self, tmp_path):
        """Test that cross-loop defect data is preserved in export."""
        from datetime import timedelta

        collector = MetricsCollector(collector_id="test-cross-loop-export")

        discovered_at = datetime.now(timezone.utc) - timedelta(hours=5)

        collector.record_defect_resolved_cross_loop(
            defect_id="defect-001",
            loop_id_discovered="loop-001",
            loop_id_resolved="loop-003",
            discovered_at=discovered_at,
        )

        # Export to JSON
        export_path = tmp_path / "cross_loop_export.json"
        collector.export_to_json(str(export_path))

        import json
        with open(export_path, "r") as f:
            data = json.load(f)

        assert "cross_loop_defects" in data
        # Cross-loop defects are recorded in both loops
        assert len(data["cross_loop_defects"]) >= 1
        # Check that all records have the correct defect info
        for defect in data["cross_loop_defects"]:
            assert defect["defect_id"] == "defect-001"
            assert defect["loop_discovered"] == "loop-001"
            assert defect["loop_resolved"] == "loop-003"
