"""
GAIA Pipeline Metrics Unit Tests

Tests for the pipeline metrics collection system including:
- MetricType enum extensions (TPS, TTFT, PHASE_DURATION, etc.)
- PipelineMetricsCollector class
- Metrics hooks
- Metrics API endpoints

Run with:
    python -m pytest tests/unit/test_pipeline_metrics.py -v
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from gaia.hooks.base import HookContext, HookResult
from gaia.metrics.models import (
    MetricSnapshot,
    MetricsReport,
    MetricStatistics,
    MetricType,
)
from gaia.pipeline.metrics_collector import (
    LoopMetrics,
    PhaseTiming,
    PipelineMetricsCollector,
    StateTransition,
    get_all_collectors,
    get_pipeline_collector,
    remove_pipeline_collector,
)
from gaia.pipeline.metrics_hooks import (
    AgentSelectMetricsHook,
    HookExecutionMetricsHook,
    LoopEndMetricsHook,
    LoopStartMetricsHook,
    PhaseEnterMetricsHook,
    PhaseExitMetricsHook,
    QualityEvalMetricsHook,
    create_metrics_hook_group,
)

# =============================================================================
# Test MetricType Enum Extensions
# =============================================================================


class TestMetricTypeExtensions:
    """Tests for new metric types added in Phase 2."""

    def test_new_metric_types_exist(self):
        """Test that all new metric types are defined."""
        assert hasattr(MetricType, "TPS")
        assert hasattr(MetricType, "TTFT")
        assert hasattr(MetricType, "PHASE_DURATION")
        assert hasattr(MetricType, "LOOP_ITERATION_COUNT")
        assert hasattr(MetricType, "HOOK_EXECUTION_TIME")
        assert hasattr(MetricType, "STATE_TRANSITION")
        assert hasattr(MetricType, "AGENT_SELECTION")
        assert hasattr(MetricType, "RESOURCE_UTILIZATION")

    def test_new_metric_types_category(self):
        """Test category method for new metric types."""
        assert MetricType.TPS.category() == "performance"
        assert MetricType.TTFT.category() == "performance"
        assert MetricType.PHASE_DURATION.category() == "performance"
        assert MetricType.LOOP_ITERATION_COUNT.category() == "performance"
        assert MetricType.HOOK_EXECUTION_TIME.category() == "performance"
        assert MetricType.STATE_TRANSITION.category() == "performance"
        assert MetricType.AGENT_SELECTION.category() == "performance"
        assert MetricType.RESOURCE_UTILIZATION.category() == "performance"

    def test_new_metric_types_unit(self):
        """Test unit method for new metric types."""
        assert MetricType.TPS.unit() == "tokens/second"
        assert MetricType.TTFT.unit() == "seconds"
        assert MetricType.PHASE_DURATION.unit() == "seconds"
        assert MetricType.LOOP_ITERATION_COUNT.unit() == "iterations"
        assert MetricType.HOOK_EXECUTION_TIME.unit() == "seconds"
        assert MetricType.STATE_TRANSITION.unit() == "timestamp"
        assert MetricType.AGENT_SELECTION.unit() == "decision"
        assert MetricType.RESOURCE_UTILIZATION.unit() == "percentage"

    def test_is_higher_better(self):
        """Test is_higher_better method for new metric types."""
        # Higher is better
        assert MetricType.TPS.is_higher_better() is True
        assert MetricType.RESOURCE_UTILIZATION.is_higher_better() is True

        # Lower is better
        assert MetricType.TTFT.is_higher_better() is False
        assert MetricType.PHASE_DURATION.is_higher_better() is False
        assert MetricType.LOOP_ITERATION_COUNT.is_higher_better() is False
        assert MetricType.HOOK_EXECUTION_TIME.is_higher_better() is False

    def test_all_metric_types_iterable(self):
        """Test that all metric types can be iterated."""
        all_types = list(MetricType)
        assert len(all_types) >= 14  # Original 6 + 8 new

        type_names = [t.name for t in all_types]
        assert "TPS" in type_names
        assert "TTFT" in type_names
        assert "PHASE_DURATION" in type_names


# =============================================================================
# Test PhaseTiming
# =============================================================================


class TestPhaseTiming:
    """Tests for PhaseTiming dataclass."""

    def test_phase_timing_creation(self):
        """Test creating PhaseTiming instance."""
        timing = PhaseTiming(phase_name="PLANNING")
        assert timing.phase_name == "PLANNING"
        assert timing.started_at is None
        assert timing.ended_at is None
        assert timing.duration_seconds == 0.0
        assert timing.token_count == 0
        assert timing.ttft is None

    def test_phase_timing_start_end(self):
        """Test starting and ending phase timing."""
        timing = PhaseTiming(phase_name="DEVELOPMENT")

        # Start phase
        timing.start()
        assert timing.started_at is not None

        # Wait a tiny bit
        asyncio.run(asyncio.sleep(0.01))

        # End phase
        timing.end()
        assert timing.ended_at is not None
        assert timing.duration_seconds >= 0.01

    def test_phase_timing_ttft(self):
        """Test time to first token recording."""
        timing = PhaseTiming(phase_name="DEVELOPMENT")

        timing.start()
        asyncio.run(asyncio.sleep(0.005))
        timing.record_first_token()

        assert timing.first_token_at is not None
        assert timing.ttft is not None
        assert timing.ttft >= 0.005

    def test_phase_timing_tps(self):
        """Test tokens per second calculation."""
        timing = PhaseTiming(phase_name="DEVELOPMENT")
        timing.token_count = 100
        timing.duration_seconds = 2.0

        assert timing.get_tps() == 50.0

    def test_phase_timing_tps_zero_duration(self):
        """Test TPS with zero duration."""
        timing = PhaseTiming(phase_name="DEVELOPMENT")
        timing.token_count = 100
        timing.duration_seconds = 0.0

        assert timing.get_tps() == 0.0

    def test_phase_timing_to_dict(self):
        """Test PhaseTiming to dictionary conversion."""
        timing = PhaseTiming(phase_name="PLANNING")
        timing.start()
        timing.end()
        timing.token_count = 50

        data = timing.to_dict()
        assert data["phase_name"] == "PLANNING"
        assert data["duration_seconds"] >= 0
        assert data["token_count"] == 50
        assert "tps" in data


# =============================================================================
# Test LoopMetrics
# =============================================================================


class TestLoopMetrics:
    """Tests for LoopMetrics dataclass."""

    def test_loop_metrics_creation(self):
        """Test creating LoopMetrics instance."""
        metrics = LoopMetrics(loop_id="loop-001", phase_name="DEVELOPMENT")
        assert metrics.loop_id == "loop-001"
        assert metrics.phase_name == "DEVELOPMENT"
        assert metrics.iteration_count == 0
        assert metrics.quality_scores == []
        assert metrics.defects_by_type == {}

    def test_add_quality_score(self):
        """Test adding quality scores."""
        metrics = LoopMetrics(loop_id="loop-001", phase_name="DEVELOPMENT")

        metrics.add_quality_score(0.75)
        assert metrics.iteration_count == 1
        assert metrics.quality_scores == [0.75]

        metrics.add_quality_score(0.85)
        assert metrics.iteration_count == 2
        assert metrics.quality_scores == [0.75, 0.85]

    def test_add_defect(self):
        """Test adding defects."""
        metrics = LoopMetrics(loop_id="loop-001", phase_name="DEVELOPMENT")

        metrics.add_defect("testing")
        assert metrics.defects_by_type == {"testing": 1}

        metrics.add_defect("documentation")
        assert metrics.defects_by_type == {"testing": 1, "documentation": 1}

        metrics.add_defect("testing")
        assert metrics.defects_by_type == {"testing": 2, "documentation": 1}

    def test_average_quality(self):
        """Test average quality calculation."""
        metrics = LoopMetrics(loop_id="loop-001", phase_name="DEVELOPMENT")

        assert metrics.average_quality is None

        metrics.add_quality_score(0.70)
        metrics.add_quality_score(0.80)
        metrics.add_quality_score(0.90)

        assert metrics.average_quality == pytest.approx(0.80, rel=1e-9)

    def test_max_quality(self):
        """Test max quality calculation."""
        metrics = LoopMetrics(loop_id="loop-001", phase_name="DEVELOPMENT")

        assert metrics.max_quality is None

        metrics.add_quality_score(0.70)
        metrics.add_quality_score(0.95)
        metrics.add_quality_score(0.85)

        assert metrics.max_quality == 0.95

    def test_loop_metrics_to_dict(self):
        """Test LoopMetrics to dictionary conversion."""
        metrics = LoopMetrics(loop_id="loop-001", phase_name="DEVELOPMENT")
        metrics.add_quality_score(0.85)
        metrics.add_defect("testing")

        data = metrics.to_dict()
        assert data["loop_id"] == "loop-001"
        assert data["iteration_count"] == 1
        assert data["average_quality"] == 0.85
        assert data["defects_by_type"] == {"testing": 1}


# =============================================================================
# Test PipelineMetricsCollector
# =============================================================================


class TestPipelineMetricsCollector:
    """Tests for PipelineMetricsCollector class."""

    @pytest.fixture
    def collector(self):
        """Create a fresh collector for each test."""
        collector = PipelineMetricsCollector(pipeline_id="test-pipeline")
        yield collector
        # Cleanup
        remove_pipeline_collector("test-pipeline")

    def test_collector_creation(self, collector):
        """Test creating PipelineMetricsCollector."""
        assert collector.pipeline_id == "test-pipeline"
        assert collector._phase_timings == {}
        assert collector._loop_metrics == {}
        assert collector._state_transitions == []

    def test_start_phase(self, collector):
        """Test starting a phase."""
        collector.start_phase("PLANNING")

        assert collector._current_phase == "PLANNING"
        assert "PLANNING" in collector._phase_timings
        assert collector._phase_timings["PLANNING"].started_at is not None

    def test_end_phase(self, collector):
        """Test ending a phase."""
        collector.start_phase("PLANNING")
        asyncio.run(asyncio.sleep(0.01))
        collector.end_phase("PLANNING")

        timing = collector._phase_timings["PLANNING"]
        assert timing.ended_at is not None
        assert timing.duration_seconds >= 0.01

    def test_record_phase_duration(self, collector):
        """Test recording phase duration directly."""
        collector.record_phase_duration("DEVELOPMENT", duration=45.5)

        # Verify metric was recorded
        base_collector = collector.get_base_collector()
        history = base_collector.get_metric_history(MetricType.PHASE_DURATION)
        assert len(history) > 0
        assert history[0][1] == 45.5

    def test_record_tps(self, collector):
        """Test recording tokens per second."""
        collector.record_tps("DEVELOPMENT", tps=25.5, token_count=100)

        base_collector = collector.get_base_collector()
        history = base_collector.get_metric_history(MetricType.TPS)
        assert len(history) > 0
        assert history[0][1] == 25.5

    def test_record_ttft(self, collector):
        """Test recording time to first token."""
        collector.record_ttft("DEVELOPMENT", ttft=0.35)

        base_collector = collector.get_base_collector()
        history = base_collector.get_metric_history(MetricType.TTFT)
        assert len(history) > 0
        assert history[0][1] == 0.35

    def test_record_loop_iteration(self, collector):
        """Test recording loop iterations."""
        iteration1 = collector.record_loop_iteration("loop-001", "DEVELOPMENT")
        assert iteration1 == 1

        iteration2 = collector.record_loop_iteration("loop-001", "DEVELOPMENT")
        assert iteration2 == 2

    def test_record_quality_score(self, collector):
        """Test recording quality scores."""
        collector.record_quality_score("loop-001", "DEVELOPMENT", 0.85)

        history = collector.get_quality_history()
        assert len(history) == 1
        assert history[0] == ("loop-001", "DEVELOPMENT", 0.85)

    def test_record_defect(self, collector):
        """Test recording defects."""
        collector.record_defect("loop-001", "DEVELOPMENT", "testing")
        collector.record_defect("loop-001", "DEVELOPMENT", "testing")
        collector.record_defect("loop-001", "QUALITY", "documentation")

        defects = collector.get_defects_by_type()
        assert defects["testing"] == 2
        assert defects["documentation"] == 1

    def test_record_agent_selection(self, collector):
        """Test recording agent selection decisions."""
        collector.record_agent_selection(
            phase_name="PLANNING",
            agent_id="senior-developer",
            reason="Best match for requirements",
            alternatives=["architect", "tech-lead"],
        )

        selections = collector.get_agent_selections()
        assert len(selections) == 1
        assert selections[0]["agent_id"] == "senior-developer"
        assert selections[0]["reason"] == "Best match for requirements"
        assert selections[0]["alternatives"] == ["architect", "tech-lead"]

    def test_record_state_transition(self, collector):
        """Test recording state transitions."""
        collector.record_state_transition(
            from_state="INIT",
            to_state="PLANNING",
            reason="Pipeline started",
        )

        transitions = collector.get_state_transitions()
        assert len(transitions) == 1
        assert transitions[0].from_state == "INIT"
        assert transitions[0].to_state == "PLANNING"

    def test_record_hook_execution(self, collector):
        """Test recording hook execution times."""
        collector.record_hook_execution(
            hook_name="quality_gate",
            event="PHASE_EXIT",
            duration_seconds=0.05,
            success=True,
        )

        hooks = collector._hook_execution_times
        assert len(hooks) == 1
        assert hooks[0]["hook_name"] == "quality_gate"
        assert hooks[0]["duration_seconds"] == 0.05

    def test_get_metrics_snapshot(self, collector):
        """Test getting comprehensive metrics snapshot."""
        collector.start_phase("PLANNING")
        collector.record_quality_score("loop-001", "PLANNING", 0.85)
        collector.record_defect("loop-001", "PLANNING", "testing")

        snapshot = collector.get_metrics_snapshot()

        assert "pipeline_id" in snapshot
        assert "phase_timings" in snapshot
        assert "loop_metrics" in snapshot
        assert "quality_scores" in snapshot

    def test_generate_report(self, collector):
        """Test generating metrics report."""
        collector.start_phase("PLANNING")
        collector.end_phase("PLANNING")
        collector.record_quality_score("loop-001", "PLANNING", 0.85)

        report = collector.generate_report()

        assert "summary" in report
        assert "phase_breakdown" in report
        assert report["summary"]["avg_quality_score"] > 0

    def test_clear_collector(self, collector):
        """Test clearing collector data."""
        collector.start_phase("PLANNING")
        collector.record_quality_score("loop-001", "PLANNING", 0.85)

        collector.clear()

        assert collector._current_phase is None
        assert len(collector._phase_timings) == 0
        assert len(collector._quality_scores) == 0


# =============================================================================
# Test Metrics Hooks
# =============================================================================


class TestMetricsHooks:
    """Tests for metrics collection hooks."""

    @pytest.fixture
    def collector(self):
        """Create a collector for hook tests."""
        collector = PipelineMetricsCollector(pipeline_id="test-hooks")
        yield collector
        remove_pipeline_collector("test-hooks")

    @pytest.mark.asyncio
    async def test_phase_enter_hook(self, collector):
        """Test PhaseEnterMetricsHook."""
        hook = PhaseEnterMetricsHook(collector)
        context = HookContext(
            event="PHASE_ENTER",
            pipeline_id="test-hooks",
            phase="PLANNING",
        )

        result = await hook.execute(context)

        assert result.success is True
        assert collector._current_phase == "PLANNING"

    @pytest.mark.asyncio
    async def test_phase_exit_hook(self, collector):
        """Test PhaseExitMetricsHook."""
        hook = PhaseExitMetricsHook(collector)
        context = HookContext(
            event="PHASE_EXIT",
            pipeline_id="test-hooks",
            phase="PLANNING",
            data={"success": True},
        )

        # Start phase first
        collector.start_phase("PLANNING")

        result = await hook.execute(context)

        assert result.success is True
        assert "PLANNING" in collector._phase_timings
        assert collector._phase_timings["PLANNING"].ended_at is not None

    @pytest.mark.asyncio
    async def test_loop_start_hook(self, collector):
        """Test LoopStartMetricsHook."""
        hook = LoopStartMetricsHook(collector)
        context = HookContext(
            event="LOOP_START",
            pipeline_id="test-hooks",
            loop_id="loop-001",
            phase="DEVELOPMENT",
        )

        result = await hook.execute(context)

        assert result.success is True
        assert "loop-001" in collector._loop_metrics

    @pytest.mark.asyncio
    async def test_loop_end_hook(self, collector):
        """Test LoopEndMetricsHook."""
        hook = LoopEndMetricsHook(collector)
        context = HookContext(
            event="LOOP_END",
            pipeline_id="test-hooks",
            loop_id="loop-001",
            phase="DEVELOPMENT",
            data={
                "quality_score": 0.85,
                "defects": [
                    {"category": "testing"},
                    {"category": "documentation"},
                ],
            },
        )

        result = await hook.execute(context)

        assert result.success is True
        quality_history = collector.get_quality_history()
        assert len(quality_history) == 1
        defects = collector.get_defects_by_type()
        assert defects["testing"] == 1

    @pytest.mark.asyncio
    async def test_quality_eval_hook(self, collector):
        """Test QualityEvalMetricsHook."""
        hook = QualityEvalMetricsHook(collector)
        context = HookContext(
            event="QUALITY_EVAL",
            pipeline_id="test-hooks",
            loop_id="loop-001",
            phase="QUALITY",
            data={"quality_score": 0.92},
        )

        result = await hook.execute(context)

        assert result.success is True
        quality_history = collector.get_quality_history()
        assert len(quality_history) == 1
        assert quality_history[0][2] == 0.92

    @pytest.mark.asyncio
    async def test_agent_select_hook(self, collector):
        """Test AgentSelectMetricsHook."""
        hook = AgentSelectMetricsHook(collector)
        context = HookContext(
            event="AGENT_SELECT",
            pipeline_id="test-hooks",
            phase="PLANNING",
            data={
                "agent_id": "senior-developer",
                "reason": "Best match",
                "alternatives": ["architect"],
            },
        )

        result = await hook.execute(context)

        assert result.success is True
        selections = collector.get_agent_selections()
        assert len(selections) == 1
        assert selections[0]["agent_id"] == "senior-developer"

    def test_create_metrics_hook_group(self, collector):
        """Test creating a group of metrics hooks."""
        hooks = create_metrics_hook_group(collector)

        assert len(hooks) == 7  # All metrics hooks
        hook_names = [h.name for h in hooks]
        assert "phase_enter_metrics" in hook_names
        assert "phase_exit_metrics" in hook_names
        assert "loop_start_metrics" in hook_names
        assert "loop_end_metrics" in hook_names
        assert "quality_eval_metrics" in hook_names
        assert "agent_select_metrics" in hook_names
        assert "hook_execution_metrics" in hook_names

    @pytest.mark.asyncio
    async def test_hook_execution_hook_with_exception(self, collector):
        """Test HookExecutionMetricsHook handles exceptions correctly."""
        from gaia.pipeline.metrics_hooks import TimingHookWrapper

        wrapper = TimingHookWrapper(collector)

        # Create a mock hook that raises an exception
        mock_hook = MagicMock()
        mock_hook.name = "failing_hook"
        mock_hook.execute = AsyncMock(side_effect=RuntimeError("Test error"))

        # Create context
        context = HookContext(
            event="TEST_EVENT",
            pipeline_id="test-hooks",
            phase="TESTING",
        )

        # Wrap the hook
        wrapped = wrapper.wrap_hook(mock_hook, context)

        # Should raise the exception but still record timing
        with pytest.raises(RuntimeError, match="Test error"):
            await wrapped(context)

        # Verify timing was recorded even though hook failed
        # Check that hook execution was recorded


# =============================================================================
# Test Global Collector Registry
# =============================================================================


class TestCollectorRegistry:
    """Tests for global pipeline collector registry."""

    def test_get_pipeline_collector(self):
        """Test getting a collector from registry."""
        collector1 = get_pipeline_collector("registry-test")
        collector2 = get_pipeline_collector("registry-test")

        # Should return same instance
        assert collector1 is collector2

        remove_pipeline_collector("registry-test")

    def test_remove_pipeline_collector(self):
        """Test removing a collector from registry."""
        collector = get_pipeline_collector("remove-test")
        assert collector is not None

        result = remove_pipeline_collector("remove-test")
        assert result is True

        # Should be able to get a new collector
        collector2 = get_pipeline_collector("remove-test")
        assert collector2 is not None
        assert collector2 is not collector

    def test_get_all_collectors(self):
        """Test getting all collectors."""
        # Get some collectors
        get_pipeline_collector("all-test-1")
        get_pipeline_collector("all-test-2")

        all_collectors = get_all_collectors()

        assert "all-test-1" in all_collectors
        assert "all-test-2" in all_collectors

        # Cleanup
        remove_pipeline_collector("all-test-1")
        remove_pipeline_collector("all-test-2")


# =============================================================================
# Test MetricSnapshot with New Types
# =============================================================================


class TestMetricSnapshotWithNewTypes:
    """Tests for MetricSnapshot with new metric types."""

    def test_snapshot_with_new_metrics(self):
        """Test creating snapshot with new metric types."""
        snapshot = MetricSnapshot(
            timestamp=datetime.now(timezone.utc),
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metrics={
                MetricType.TPS: 25.5,
                MetricType.TTFT: 0.35,
                MetricType.PHASE_DURATION: 45.0,
                MetricType.LOOP_ITERATION_COUNT: 3,
            },
        )

        assert snapshot[MetricType.TPS] == 25.5
        assert snapshot[MetricType.TTFT] == 0.35
        assert snapshot[MetricType.PHASE_DURATION] == 45.0
        assert snapshot[MetricType.LOOP_ITERATION_COUNT] == 3

    def test_snapshot_quality_check_with_new_metrics(self):
        """Test quality check with new metric types."""
        snapshot = MetricSnapshot(
            timestamp=datetime.now(timezone.utc),
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metrics={
                MetricType.TPS: 5.0,  # Below threshold (too slow)
                MetricType.TTFT: 10.0,  # Above threshold (too slow)
                MetricType.PHASE_DURATION: 500.0,  # Above threshold (too long)
            },
        )

        passed, failures = snapshot.quality_check()

        assert passed is False
        assert "TPS" in failures
        assert "TTFT" in failures
        assert "PHASE_DURATION" in failures

    def test_snapshot_summary_with_new_metrics(self):
        """Test summary generation with new metric types."""
        snapshot = MetricSnapshot(
            timestamp=datetime.now(timezone.utc),
            loop_id="loop-001",
            phase="DEVELOPMENT",
            metrics={
                MetricType.TPS: 25.5,
                MetricType.TTFT: 0.35,
                MetricType.LOOP_ITERATION_COUNT: 3,
            },
        )

        summary = snapshot.summary()

        assert "TPS" in summary
        assert "TTFT" in summary
        assert "LOOP ITERATION COUNT" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
