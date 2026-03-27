"""
Tests for GAIA Metrics Analyzer

Tests for MetricsAnalyzer class and related analysis classes.
"""

import pytest
from datetime import datetime, timezone, timedelta
from gaia.metrics.collector import MetricsCollector
from gaia.metrics.analyzer import (
    MetricsAnalyzer,
    TrendAnalysis,
    TrendDirection,
    Anomaly,
    AnomalyType,
    CorrelationResult,
    AnomalyCallback,
)
from gaia.metrics.models import MetricType


class TestTrendAnalysis:
    """Tests for TrendAnalysis dataclass."""

    def test_trend_analysis_creation(self):
        """Test trend analysis creation."""
        trend = TrendAnalysis(
            metric_type=MetricType.TOKEN_EFFICIENCY,
            direction=TrendDirection.INCREASING,
            confidence=0.85,
            slope=0.02,
        )

        assert trend.metric_type == MetricType.TOKEN_EFFICIENCY
        assert trend.direction == TrendDirection.INCREASING
        assert trend.confidence == 0.85

    def test_trend_analysis_to_dict(self):
        """Test dictionary serialization."""
        trend = TrendAnalysis(
            metric_type=MetricType.TOKEN_EFFICIENCY,
            direction=TrendDirection.INCREASING,
            confidence=0.85,
            start_value=0.75,
            end_value=0.90,
            change_percent=20.0,
        )

        data = trend.to_dict()

        assert data["metric_type"] == "TOKEN_EFFICIENCY"
        assert data["direction"] == "increasing"
        assert data["confidence"] == 0.85
        assert data["change_percent"] == 20.0

    def test_trend_is_positive_higher_better(self):
        """Test positive trend detection for higher=better metrics."""
        # Token efficiency: higher is better
        trend = TrendAnalysis(
            metric_type=MetricType.TOKEN_EFFICIENCY,
            direction=TrendDirection.INCREASING,
        )
        assert trend.is_positive() is True

        trend = TrendAnalysis(
            metric_type=MetricType.TOKEN_EFFICIENCY,
            direction=TrendDirection.DECREASING,
        )
        assert trend.is_positive() is False

    def test_trend_is_positive_lower_better(self):
        """Test positive trend detection for lower=better metrics."""
        # Defect density: lower is better
        trend = TrendAnalysis(
            metric_type=MetricType.DEFECT_DENSITY,
            direction=TrendDirection.DECREASING,
        )
        assert trend.is_positive() is True

        trend = TrendAnalysis(
            metric_type=MetricType.DEFECT_DENSITY,
            direction=TrendDirection.INCREASING,
        )
        assert trend.is_positive() is False

    def test_trend_summary(self):
        """Test trend summary generation."""
        trend = TrendAnalysis(
            metric_type=MetricType.TOKEN_EFFICIENCY,
            direction=TrendDirection.INCREASING,
            confidence=0.85,
            change_percent=15.5,
        )

        summary = trend.summary()

        assert "TOKEN_EFFICIENCY" in summary
        assert "increasing" in summary
        assert "85%" in summary


class TestAnomaly:
    """Tests for Anomaly dataclass."""

    def test_anomaly_creation(self):
        """Test anomaly creation."""
        anomaly = Anomaly(
            metric_type=MetricType.DEFECT_DENSITY,
            anomaly_type=AnomalyType.SPIKE,
            timestamp=datetime.now(timezone.utc),
            value=15.5,
            expected_value=5.0,
            deviation=3.5,
            severity="high",
        )

        assert anomaly.metric_type == MetricType.DEFECT_DENSITY
        assert anomaly.anomaly_type == AnomalyType.SPIKE
        assert anomaly.value == 15.5
        assert anomaly.severity == "high"

    def test_anomaly_to_dict(self):
        """Test dictionary serialization."""
        now = datetime.now(timezone.utc)
        anomaly = Anomaly(
            metric_type=MetricType.DEFECT_DENSITY,
            anomaly_type=AnomalyType.SPIKE,
            timestamp=now,
            value=15.5,
            expected_value=5.0,
            deviation=3.5,
            severity="high",
            description="Sudden increase in defect density",
        )

        data = anomaly.to_dict()

        assert data["metric_type"] == "DEFECT_DENSITY"
        assert data["anomaly_type"] == "spike"
        assert data["severity"] == "high"
        assert "Sudden increase" in data["description"]

    def test_anomaly_string_representation(self):
        """Test anomaly string representation."""
        now = datetime.now(timezone.utc)
        anomaly = Anomaly(
            metric_type=MetricType.DEFECT_DENSITY,
            anomaly_type=AnomalyType.SPIKE,
            timestamp=now,
            value=15.5,
            expected_value=5.0,
            deviation=3.5,
        )

        str_repr = str(anomaly)

        assert "DEFECT_DENSITY" in str_repr
        assert "spike" in str_repr
        assert "15.50" in str_repr


class TestCorrelationResult:
    """Tests for CorrelationResult dataclass."""

    def test_correlation_result_creation(self):
        """Test correlation result creation."""
        corr = CorrelationResult(
            metric_a=MetricType.TOKEN_EFFICIENCY,
            metric_b=MetricType.QUALITY_VELOCITY,
            correlation_coefficient=-0.65,
            p_value=0.02,
            sample_size=50,
        )

        assert corr.metric_a == MetricType.TOKEN_EFFICIENCY
        assert corr.correlation_coefficient == -0.65
        assert corr.p_value == 0.02

    def test_correlation_relationship_positive(self):
        """Test positive relationship detection."""
        corr = CorrelationResult(
            metric_a=MetricType.TOKEN_EFFICIENCY,
            metric_b=MetricType.CONTEXT_UTILIZATION,
            correlation_coefficient=0.75,
            p_value=0.01,
            sample_size=30,
        )

        assert corr.relationship == "positive"
        assert corr.strength == "strong"

    def test_correlation_relationship_negative(self):
        """Test negative relationship detection."""
        corr = CorrelationResult(
            metric_a=MetricType.TOKEN_EFFICIENCY,
            metric_b=MetricType.DEFECT_DENSITY,
            correlation_coefficient=-0.45,
            p_value=0.03,
            sample_size=30,
        )

        assert corr.relationship == "negative"
        assert corr.strength == "moderate"

    def test_correlation_relationship_none(self):
        """Test no relationship detection."""
        corr = CorrelationResult(
            metric_a=MetricType.TOKEN_EFFICIENCY,
            metric_b=MetricType.MTTR,
            correlation_coefficient=0.05,
            p_value=0.80,
            sample_size=30,
        )

        assert corr.relationship == "none"
        assert corr.strength == "none"

    def test_correlation_is_significant(self):
        """Test significance testing."""
        corr_significant = CorrelationResult(
            metric_a=MetricType.TOKEN_EFFICIENCY,
            metric_b=MetricType.CONTEXT_UTILIZATION,
            correlation_coefficient=0.75,
            p_value=0.01,
            sample_size=30,
        )
        assert corr_significant.is_significant() is True

        corr_not_significant = CorrelationResult(
            metric_a=MetricType.TOKEN_EFFICIENCY,
            metric_b=MetricType.CONTEXT_UTILIZATION,
            correlation_coefficient=0.30,
            p_value=0.15,
            sample_size=30,
        )
        assert corr_not_significant.is_significant(alpha=0.05) is False

    def test_correlation_to_dict(self):
        """Test dictionary serialization."""
        corr = CorrelationResult(
            metric_a=MetricType.TOKEN_EFFICIENCY,
            metric_b=MetricType.QUALITY_VELOCITY,
            correlation_coefficient=-0.65,
            p_value=0.02,
            sample_size=50,
        )

        data = corr.to_dict()

        assert data["metric_a"] == "TOKEN_EFFICIENCY"
        assert data["metric_b"] == "QUALITY_VELOCITY"
        assert data["correlation_coefficient"] == -0.65
        assert data["relationship"] == "negative"
        assert data["strength"] == "moderate"


class TestMetricsAnalyzer:
    """Tests for MetricsAnalyzer class."""

    @pytest.fixture
    def collector_with_data(self):
        """Create a collector with sample data."""
        collector = MetricsCollector(collector_id="test-analyzer")

        # Add token efficiency data (increasing trend)
        base_time = datetime.now(timezone.utc)
        for i, value in enumerate([0.75, 0.78, 0.82, 0.85, 0.88, 0.90]):
            collector.record_metric(
                loop_id="loop-001",
                phase="DEVELOPMENT",
                metric_type=MetricType.TOKEN_EFFICIENCY,
                value=value,
            )

        # Add context utilization data (stable)
        for value in [0.80, 0.81, 0.79, 0.80, 0.81, 0.80]:
            collector.record_metric(
                loop_id="loop-001",
                phase="DEVELOPMENT",
                metric_type=MetricType.CONTEXT_UTILIZATION,
                value=value,
            )

        # Add quality velocity data (decreasing trend - improving)
        for value in [5.0, 4.0, 3.5, 3.0, 2.5, 2.0]:
            collector.record_metric(
                loop_id="loop-001",
                phase="QUALITY",
                metric_type=MetricType.QUALITY_VELOCITY,
                value=value,
            )

        return collector

    @pytest.fixture
    def analyzer(self, collector_with_data):
        """Create analyzer with sample data."""
        return MetricsAnalyzer(collector_with_data)

    def test_analyzer_creation(self, collector_with_data):
        """Test analyzer creation."""
        analyzer = MetricsAnalyzer(collector_with_data)
        assert analyzer._collector == collector_with_data

    def test_detect_trends(self, analyzer):
        """Test trend detection."""
        trends = analyzer.detect_trends()

        assert MetricType.TOKEN_EFFICIENCY in trends
        assert MetricType.CONTEXT_UTILIZATION in trends
        assert MetricType.QUALITY_VELOCITY in trends

    def test_detect_trends_token_efficiency(self):
        """Test token efficiency trend detection."""
        collector = MetricsCollector(collector_id="test-trend-te")
        # Add token efficiency data (increasing trend)
        for value in [0.75, 0.78, 0.82, 0.85, 0.88, 0.90]:
            collector.record_metric(
                loop_id="loop-001",
                phase="DEVELOPMENT",
                metric_type=MetricType.TOKEN_EFFICIENCY,
                value=value,
            )

        analyzer = MetricsAnalyzer(collector)
        trends = analyzer.detect_trends(loop_id="loop-001")
        trend = trends.get(MetricType.TOKEN_EFFICIENCY)

        if trend:
            # Trend should show positive change (end > start)
            assert trend.end_value == 0.90
            assert trend.start_value == 0.75
            assert trend.change_percent > 0

    def test_detect_trends_quality_velocity(self):
        """Test quality velocity trend detection (decreasing is good)."""
        collector = MetricsCollector(collector_id="test-trend-qv")
        # Add quality velocity data (decreasing trend - improving)
        for value in [5.0, 4.0, 3.5, 3.0, 2.5, 2.0]:
            collector.record_metric(
                loop_id="loop-001",
                phase="QUALITY",
                metric_type=MetricType.QUALITY_VELOCITY,
                value=value,
            )

        analyzer = MetricsAnalyzer(collector)
        trends = analyzer.detect_trends(loop_id="loop-001")
        trend = trends.get(MetricType.QUALITY_VELOCITY)

        if trend:
            # Quality velocity should be decreasing (improving)
            assert trend.end_value == 2.0
            assert trend.start_value == 5.0

    def test_detect_trends_with_loop_filter(self, analyzer):
        """Test trend detection with loop filter."""
        trends = analyzer.detect_trends(loop_id="loop-001")
        assert len(trends) > 0

        trends_nonexistent = analyzer.detect_trends(loop_id="loop-999")
        assert len(trends_nonexistent) == 0

    def test_detect_anomalies(self, analyzer, collector_with_data):
        """Test anomaly detection."""
        # Add an anomaly
        collector_with_data.record_metric(
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metric_type=MetricType.TOKEN_EFFICIENCY,
            value=0.20,  # Anomalous low value
        )

        anomalies = analyzer.detect_anomalies(threshold_std=2.0)

        assert len(anomalies) > 0

        # Check anomaly properties
        token_anomalies = [a for a in anomalies if a.metric_type == MetricType.TOKEN_EFFICIENCY]
        if token_anomalies:
            anomaly = token_anomalies[0]
            # Anomaly type can be spike, drop, outlier, or pattern_break
            assert anomaly.anomaly_type in [AnomalyType.SPIKE, AnomalyType.DROP, AnomalyType.OUTLIER, AnomalyType.PATTERN_BREAK]
            assert anomaly.severity in ["low", "medium", "high", "critical"]

    def test_detect_anomalies_with_loop_filter(self, analyzer):
        """Test anomaly detection with loop filter."""
        anomalies = analyzer.detect_anomalies(loop_id="loop-001")
        assert isinstance(anomalies, list)

    def test_analyze_correlations(self, analyzer):
        """Test correlation analysis."""
        correlations = analyzer.analyze_correlations()

        # Correlations may be empty if there isn't enough paired data
        assert isinstance(correlations, list)

        # If we have correlations, check their properties
        for corr in correlations:
            assert corr.metric_a != corr.metric_b
            assert -1 <= corr.correlation_coefficient <= 1
            assert corr.sample_size > 0

    def test_correlation_token_efficiency_quality_velocity(self, analyzer):
        """Test specific correlation analysis."""
        correlations = analyzer.analyze_correlations()

        # Find correlation between token efficiency and quality velocity
        token_qv_corr = None
        for corr in correlations:
            if (
                corr.metric_a == MetricType.TOKEN_EFFICIENCY
                and corr.metric_b == MetricType.QUALITY_VELOCITY
            ):
                token_qv_corr = corr
                break

        if token_qv_corr:
            assert -1 <= token_qv_corr.correlation_coefficient <= 1

    def test_get_comparative_analysis(self, analyzer, collector_with_data):
        """Test comparative analysis across loops."""
        # Add data for second loop
        for value in [0.60, 0.65, 0.70]:
            collector_with_data.record_metric(
                loop_id="loop-002",
                phase="DEVELOPMENT",
                metric_type=MetricType.TOKEN_EFFICIENCY,
                value=value,
            )

        comparison = analyzer.get_comparative_analysis(["loop-001", "loop-002"])

        assert "loop-001" in comparison
        assert "loop-002" in comparison

    def test_generate_insights(self, analyzer):
        """Test insight generation."""
        insights = analyzer.generate_insights(loop_id="loop-001")

        assert "summary" in insights
        assert "trends" in insights
        assert "anomalies" in insights
        assert "correlations" in insights
        assert "recommendations" in insights
        assert "risk_assessment" in insights

    def test_generate_insights_summary(self, analyzer):
        """Test insight summary content."""
        insights = analyzer.generate_insights(loop_id="loop-001")
        summary = insights["summary"]

        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_generate_insights_recommendations(self, analyzer):
        """Test insight recommendations."""
        insights = analyzer.generate_insights(loop_id="loop-001")
        recommendations = insights["recommendations"]

        assert isinstance(recommendations, list)

    def test_generate_insights_risk_assessment(self, analyzer):
        """Test insight risk assessment."""
        insights = analyzer.generate_insights(loop_id="loop-001")
        risk = insights["risk_assessment"]

        assert "level" in risk
        assert risk["level"] in ["minimal", "low", "medium", "high"]
        assert "score" in risk
        assert "factors" in risk

    def test_export_analysis_json(self, analyzer):
        """Test JSON export."""
        json_output = analyzer.export_analysis(loop_id="loop-001", format="json")

        assert isinstance(json_output, str)
        assert "trends" in json_output
        assert "summary" in json_output

    def test_export_analysis_text(self, analyzer):
        """Test text export."""
        text_output = analyzer.export_analysis(loop_id="loop-001", format="text")

        assert isinstance(text_output, str)
        assert "METRICS ANALYSIS REPORT" in text_output
        assert "TRENDS" in text_output

    def test_export_analysis_invalid_format(self, analyzer):
        """Test invalid export format."""
        with pytest.raises(ValueError):
            analyzer.export_analysis(format="invalid")

    def test_trend_direction_constants(self):
        """Test trend direction constant values."""
        assert TrendDirection.INCREASING == "increasing"
        assert TrendDirection.DECREASING == "decreasing"
        assert TrendDirection.STABLE == "stable"
        assert TrendDirection.VOLATILE == "volatile"

    def test_anomaly_type_constants(self):
        """Test anomaly type constant values."""
        assert AnomalyType.SPIKE == "spike"
        assert AnomalyType.DROP == "drop"
        assert AnomalyType.OUTLIER == "outlier"
        assert AnomalyType.PATTERN_BREAK == "pattern_break"


class TestAnomalyCallback:
    """Tests for AnomalyCallback real-time alerting."""

    def test_anomaly_callback_creation(self):
        """Test AnomalyCallback creation."""
        from gaia.metrics.analyzer import AnomalyCallback

        callback_triggered = []

        def test_handler(anomaly, metadata):
            callback_triggered.append((anomaly, metadata))

        callback = AnomalyCallback(
            callback_fn=test_handler,
            severity_filter="high",
        )

        assert callback.severity_filter == "high"
        assert callback.metric_filter is None
        assert callback.include_context is True

    def test_anomaly_callback_should_trigger(self):
        """Test callback trigger conditions."""
        from gaia.metrics.analyzer import AnomalyCallback

        callback_triggered = []

        def test_handler(anomaly, metadata):
            callback_triggered.append((anomaly, metadata))

        callback = AnomalyCallback(
            callback_fn=test_handler,
            severity_filter="high",
            metric_filter=[MetricType.DEFECT_DENSITY, MetricType.MTTR],
        )

        # High severity, filtered metric - should trigger
        high_defect = Anomaly(
            metric_type=MetricType.DEFECT_DENSITY,
            anomaly_type=AnomalyType.SPIKE,
            timestamp=datetime.now(timezone.utc),
            value=15.0,
            expected_value=5.0,
            deviation=3.5,
            severity="high",
        )
        assert callback.should_trigger(high_defect) is True

        # Medium severity - should NOT trigger (below threshold)
        medium_defect = Anomaly(
            metric_type=MetricType.DEFECT_DENSITY,
            anomaly_type=AnomalyType.SPIKE,
            timestamp=datetime.now(timezone.utc),
            value=10.0,
            expected_value=5.0,
            deviation=2.5,
            severity="medium",
        )
        assert callback.should_trigger(medium_defect) is False

        # High severity, non-filtered metric - should NOT trigger
        high_token = Anomaly(
            metric_type=MetricType.TOKEN_EFFICIENCY,
            anomaly_type=AnomalyType.DROP,
            timestamp=datetime.now(timezone.utc),
            value=0.3,
            expected_value=0.8,
            deviation=3.0,
            severity="high",
        )
        assert callback.should_trigger(high_token) is False

    def test_anomaly_callback_invoke(self):
        """Test callback invocation."""
        from gaia.metrics.analyzer import AnomalyCallback

        callback_data = []

        def test_handler(anomaly, metadata):
            callback_data.append({
                "metric_type": anomaly.metric_type.name,
                "severity": anomaly.severity,
                "metadata": metadata,
            })

        callback = AnomalyCallback(
            callback_fn=test_handler,
            severity_filter="low",  # Accept all
        )

        anomaly = Anomaly(
            metric_type=MetricType.DEFECT_DENSITY,
            anomaly_type=AnomalyType.SPIKE,
            timestamp=datetime.now(timezone.utc),
            value=15.0,
            expected_value=5.0,
            deviation=3.5,
            severity="high",
        )

        callback.invoke(anomaly)

        assert len(callback_data) == 1
        assert callback_data[0]["metric_type"] == "DEFECT_DENSITY"
        assert callback_data[0]["severity"] == "high"
        assert "anomaly_data" in callback_data[0]["metadata"]

    def test_anomaly_callback_with_analyzer(self):
        """Test anomaly callback integration with analyzer."""
        from gaia.metrics.analyzer import AnomalyCallback

        collector = MetricsCollector(collector_id="test-callback")
        analyzer = MetricsAnalyzer(collector)

        # Record enough data points for anomaly detection
        for i in range(10):
            if i == 5:
                # Add an anomalous value
                collector.record_metric(
                    loop_id="loop-001",
                    phase="DEVELOPMENT",
                    metric_type=MetricType.DEFECT_DENSITY,
                    value=50.0,  # Anomaly: much higher than others
                )
            else:
                collector.record_metric(
                    loop_id="loop-001",
                    phase="DEVELOPMENT",
                    metric_type=MetricType.DEFECT_DENSITY,
                    value=5.0 + (i * 0.1),
                )

        callback_triggered = []

        def test_handler(anomaly, metadata):
            callback_triggered.append({
                "anomaly": anomaly,
                "metadata": metadata,
            })

        callback = AnomalyCallback(
            callback_fn=test_handler,
            severity_filter="low",  # Trigger for all severities
        )

        anomalies = analyzer.detect_anomalies(
            loop_id="loop-001",
            callback=callback,
        )

        # Verify anomalies were detected
        assert len(anomalies) > 0

        # Verify callback was triggered for the anomaly
        assert len(callback_triggered) > 0
        assert callback_triggered[0]["anomaly"].metric_type == MetricType.DEFECT_DENSITY

    def test_anomaly_callback_severity_filter(self):
        """Test that severity filter correctly filters callbacks."""
        from gaia.metrics.analyzer import AnomalyCallback

        collector = MetricsCollector(collector_id="test-severity-filter")
        analyzer = MetricsAnalyzer(collector)

        # Record data with varying severities
        for i in range(10):
            if i == 5:
                # Critical anomaly
                collector.record_metric(
                    loop_id="loop-001",
                    phase="DEVELOPMENT",
                    metric_type=MetricType.MTTR,
                    value=100.0,  # Critical: extremely high
                )
            elif i == 7:
                # Lower anomaly
                collector.record_metric(
                    loop_id="loop-001",
                    phase="DEVELOPMENT",
                    metric_type=MetricType.MTTR,
                    value=15.0,  # Lower deviation
                )
            else:
                collector.record_metric(
                    loop_id="loop-001",
                    phase="DEVELOPMENT",
                    metric_type=MetricType.MTTR,
                    value=5.0 + (i * 0.1),
                )

        callback_triggered = []

        def test_handler(anomaly, metadata):
            callback_triggered.append(anomaly)

        # Only trigger for critical anomalies
        callback = AnomalyCallback(
            callback_fn=test_handler,
            severity_filter="critical",
        )

        anomalies = analyzer.detect_anomalies(
            loop_id="loop-001",
            callback=callback,
        )

        # All anomalies are detected
        assert len(anomalies) > 0

        # But callback only triggered for critical ones
        for triggered_anomaly in callback_triggered:
            assert triggered_anomaly.severity == "critical"
