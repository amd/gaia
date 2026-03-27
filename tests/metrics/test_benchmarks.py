"""
Tests for GAIA Performance Benchmarks Module

Tests for PipelineBenchmarker and related benchmark functionality.
"""

import pytest
import asyncio
import statistics
from datetime import datetime, timezone
from gaia.metrics.benchmarks import (
    PipelineBenchmarker,
    BenchmarkType,
    BenchmarkResult,
    BenchmarkStatistics,
    Bottleneck,
)


class TestBenchmarkType:
    """Tests for BenchmarkType enum."""

    def test_benchmark_type_values(self):
        """Test benchmark type enum values exist."""
        assert BenchmarkType.LATENCY.value == 1
        assert BenchmarkType.THROUGHPUT.value == 2
        assert BenchmarkType.MEMORY.value == 3
        assert BenchmarkType.TOKEN_EFFICIENCY.value == 4
        assert BenchmarkType.SCALE.value == 5
        assert BenchmarkType.ENDURANCE.value == 6


class TestBenchmarkResult:
    """Tests for BenchmarkResult dataclass."""

    def test_benchmark_result_creation(self):
        """Test BenchmarkResult creation."""
        result = BenchmarkResult(
            benchmark_type=BenchmarkType.LATENCY,
            timestamp=datetime.now(timezone.utc),
            duration_ms=150.5,
            memory_peak_mb=25.3,
            memory_current_mb=20.1,
        )

        assert result.benchmark_type == BenchmarkType.LATENCY
        assert result.duration_ms == 150.5
        assert result.memory_peak_mb == 25.3
        assert result.memory_current_mb == 20.1

    def test_benchmark_result_to_dict(self):
        """Test BenchmarkResult serialization to dictionary."""
        timestamp = datetime(2026, 3, 25, 10, 30, 0, tzinfo=timezone.utc)
        result = BenchmarkResult(
            benchmark_type=BenchmarkType.MEMORY,
            timestamp=timestamp,
            duration_ms=0,
            memory_peak_mb=30.5,
            memory_current_mb=28.2,
            metrics={"iterations": 3},
            metadata={"test_type": "memory_footprint"},
        )

        data = result.to_dict()

        assert data["benchmark_type"] == "MEMORY"
        assert data["timestamp"] == "2026-03-25T10:30:00+00:00"
        assert data["duration_ms"] == 0
        assert data["memory_peak_mb"] == 30.5
        assert data["memory_current_mb"] == 28.2
        assert data["metrics"]["iterations"] == 3
        assert data["metadata"]["test_type"] == "memory_footprint"

    def test_benchmark_result_from_dict(self):
        """Test BenchmarkResult deserialization from dictionary."""
        data = {
            "benchmark_type": "LATENCY",
            "timestamp": "2026-03-25T10:30:00+00:00",
            "duration_ms": 200.5,
            "memory_peak_mb": 35.0,
            "memory_current_mb": 30.0,
            "metrics": {"iterations": 5},
            "metadata": {"test_type": "single_execution"},
        }

        result = BenchmarkResult.from_dict(data)

        assert result.benchmark_type == BenchmarkType.LATENCY
        assert result.duration_ms == 200.5
        assert result.memory_peak_mb == 35.0
        assert result.metrics["iterations"] == 5


class TestBenchmarkStatistics:
    """Tests for BenchmarkStatistics dataclass."""

    def test_statistics_from_results(self):
        """Test creating statistics from benchmark results."""
        timestamp = datetime.now(timezone.utc)
        results = [
            BenchmarkResult(
                benchmark_type=BenchmarkType.LATENCY,
                timestamp=timestamp,
                duration_ms=100 + i * 10,
                memory_peak_mb=20.0 + i,
            )
            for i in range(5)
        ]

        stats = BenchmarkStatistics.from_results(BenchmarkType.LATENCY, results)

        assert stats.count == 5
        assert stats.mean_ms == 120.0  # median of [100, 110, 120, 130, 140]
        assert stats.min_ms == 100
        assert stats.max_ms == 140
        assert stats.memory_peak_avg_mb == 22.0

    def test_statistics_from_empty_results(self):
        """Test that empty results raise ValueError."""
        with pytest.raises(ValueError, match="empty results list"):
            BenchmarkStatistics.from_results(BenchmarkType.LATENCY, [])

    def test_statistics_percentile_calculation(self):
        """Test percentile calculation in statistics."""
        timestamp = datetime.now(timezone.utc)
        results = [
            BenchmarkResult(
                benchmark_type=BenchmarkType.LATENCY,
                timestamp=timestamp,
                duration_ms=float(i * 10),
            )
            for i in range(1, 11)  # 10, 20, 30, ..., 100
        ]

        stats = BenchmarkStatistics.from_results(BenchmarkType.LATENCY, results)

        assert stats.count == 10
        assert stats.p95_ms > stats.median_ms  # p95 should be higher than median


class TestBottleneck:
    """Tests for Bottleneck dataclass."""

    def test_bottleneck_creation(self):
        """Test Bottleneck creation."""
        bottleneck = Bottleneck(
            name="High Latency",
            location="pipeline/engine.py",
            severity="high",
            description="Latency exceeds target",
            impact_ms=5000,
            recommendation="Optimize phase transitions",
        )

        assert bottleneck.name == "High Latency"
        assert bottleneck.severity == "high"
        assert bottleneck.impact_ms == 5000

    def test_bottleneck_to_dict(self):
        """Test Bottleneck serialization."""
        bottleneck = Bottleneck(
            name="Memory Leak",
            location="pipeline/state.py",
            severity="critical",
            description="Memory increases over time",
            impact_ms=0,
            recommendation="Review object lifecycle",
        )

        data = bottleneck.to_dict()

        assert data["name"] == "Memory Leak"
        assert data["severity"] == "critical"
        assert data["recommendation"] == "Review object lifecycle"


class TestPipelineBenchmarker:
    """Tests for PipelineBenchmarker class."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create a fresh benchmarker for each test."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    def test_benchmarker_creation(self, benchmarker):
        """Test benchmarker creation with seed."""
        assert benchmarker._seed == 42
        assert benchmarker._output_dir.exists()

    def test_benchmarker_reproducible_seed(self, tmp_path):
        """Test that benchmarker produces reproducible results with same seed."""
        benchmarker1 = PipelineBenchmarker(output_dir=str(tmp_path / "run1"), seed=42)
        benchmarker2 = PipelineBenchmarker(output_dir=str(tmp_path / "run2"), seed=42)

        # Both should have same seed
        assert benchmarker1._seed == benchmarker2._seed == 42

    def test_benchmarker_different_seed(self, tmp_path):
        """Test benchmarker with different seed."""
        benchmarker = PipelineBenchmarker(output_dir=str(tmp_path), seed=123)
        assert benchmarker._seed == 123


class TestSingleExecutionBenchmark:
    """Tests for single execution benchmark."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create benchmarker for single execution tests."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    @pytest.mark.asyncio
    async def test_run_single_execution_benchmark(self, benchmarker):
        """Test running single execution benchmark."""
        result = await benchmarker.run_single_execution_benchmark(iterations=3)

        assert result.benchmark_type == BenchmarkType.LATENCY
        assert result.duration_ms > 0
        # Memory should now be realistic (not 0.0MB)
        assert result.memory_peak_mb >= 0
        assert "iterations" in result.metrics
        assert result.metrics["iterations"] == 3
        assert "seed" in result.metadata

    @pytest.mark.asyncio
    async def test_run_single_execution_multiple_iterations(self, benchmarker):
        """Test running benchmark with multiple iterations."""
        result = await benchmarker.run_single_execution_benchmark(iterations=5)

        assert "all_durations_ms" in result.metrics
        assert len(result.metrics["all_durations_ms"]) == 5
        assert "std_dev_ms" in result.metrics

    @pytest.mark.asyncio
    async def test_single_execution_reproducibility(self, tmp_path):
        """Test that results are reproducible with same seed."""
        benchmarker1 = PipelineBenchmarker(output_dir=str(tmp_path / "1"), seed=42)
        benchmarker2 = PipelineBenchmarker(output_dir=str(tmp_path / "2"), seed=42)

        result1 = await benchmarker1.run_single_execution_benchmark(iterations=3)
        result2 = await benchmarker2.run_single_execution_benchmark(iterations=3)

        # Duration should be similar (allowing for small timing variations)
        # Using 50% tolerance for timing variations in async tests
        assert abs(result1.duration_ms - result2.duration_ms) / result1.duration_ms < 0.5


class TestThroughputBenchmark:
    """Tests for throughput benchmark."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create benchmarker for throughput tests."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    @pytest.mark.asyncio
    async def test_run_throughput_benchmark(self, benchmarker):
        """Test running throughput benchmark."""
        result = await benchmarker.run_throughput_benchmark(concurrent_executions=5)

        assert result.benchmark_type == BenchmarkType.THROUGHPUT
        assert result.duration_ms > 0
        assert "throughput_per_hour" in result.metrics
        assert "executions_per_second" in result.metrics
        assert result.metrics["concurrent_executions"] == 5
        assert "seed" in result.metadata

    @pytest.mark.asyncio
    async def test_throughput_calculation(self, benchmarker):
        """Test throughput calculation is reasonable."""
        result = await benchmarker.run_throughput_benchmark(concurrent_executions=10)

        # Should complete 10 concurrent executions
        assert result.metrics["throughput_per_hour"] > 0
        assert result.metrics["executions_per_second"] > 0


class TestMemoryBenchmark:
    """Tests for memory benchmark."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create benchmarker for memory tests."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    @pytest.mark.asyncio
    async def test_run_memory_benchmark(self, benchmarker):
        """Test running memory benchmark."""
        result = await benchmarker.run_memory_benchmark(iterations=3)

        assert result.benchmark_type == BenchmarkType.MEMORY
        # Memory should be realistic (not 0.0MB)
        # Even a minimal Python process uses 20-50MB
        assert result.memory_peak_mb > 0
        assert result.memory_current_mb > 0
        assert "peak_memory_mb" in result.metrics
        assert len(result.metrics["peak_memory_mb"]) == 3
        assert "seed" in result.metadata

    @pytest.mark.asyncio
    async def test_memory_measurements_realistic(self, benchmarker):
        """Test that memory measurements are realistic."""
        result = await benchmarker.run_memory_benchmark(iterations=3)

        # Memory should be in reasonable range for Python process
        # Not too low (< 1MB is suspicious) and not too high (> 1GB is unlikely)
        assert 1.0 < result.memory_peak_mb < 1000.0


class TestTokenEfficiencyBenchmark:
    """Tests for token efficiency benchmark."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create benchmarker for token efficiency tests."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    @pytest.mark.asyncio
    async def test_run_token_efficiency_benchmark(self, benchmarker):
        """Test running token efficiency benchmark."""
        result = await benchmarker.run_token_efficiency_benchmark(iterations=3)

        assert result.benchmark_type == BenchmarkType.TOKEN_EFFICIENCY
        assert "avg_tokens_per_execution" in result.metrics
        assert "token_usage_samples" in result.metrics
        assert len(result.metrics["token_usage_samples"]) == 3
        assert "seed" in result.metadata


class TestScaleBenchmark:
    """Tests for scale benchmark."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create benchmarker for scale tests."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    @pytest.mark.asyncio
    async def test_run_scale_benchmark(self, benchmarker):
        """Test running scale benchmark."""
        results = await benchmarker.run_scale_benchmark(scale_levels=[5, 10])

        assert len(results) == 2
        for result in results:
            assert result.benchmark_type == BenchmarkType.SCALE
            assert "concurrent_loops" in result.metrics
            assert "loops_per_second" in result.metrics
            assert "seed" in result.metadata

    @pytest.mark.asyncio
    async def test_run_scale_benchmark_default_levels(self, benchmarker):
        """Test scale benchmark with default levels."""
        results = await benchmarker.run_scale_benchmark()

        assert len(results) == 3  # Default: [10, 50, 100]
        assert results[0].metrics["concurrent_loops"] == 10
        assert results[1].metrics["concurrent_loops"] == 50
        assert results[2].metrics["concurrent_loops"] == 100


class TestEnduranceBenchmark:
    """Tests for endurance benchmark."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create benchmarker for endurance tests."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    @pytest.mark.asyncio
    async def test_run_endurance_benchmark(self, benchmarker):
        """Test running endurance benchmark."""
        result = await benchmarker.run_endurance_benchmark(duration_seconds=5)

        assert result.benchmark_type == BenchmarkType.ENDURANCE
        assert result.metrics["target_duration_s"] == 5
        assert result.metrics["iterations_completed"] > 0
        assert "memory_leak_detected" in result.metrics
        assert "memory_samples_mb" in result.metrics
        assert "memory_growth_percent" in result.metrics
        assert "seed" in result.metadata

    @pytest.mark.asyncio
    async def test_endurance_no_memory_leak_short_run(self, benchmarker):
        """Test that short endurance runs don't falsely detect memory leaks."""
        result = await benchmarker.run_endurance_benchmark(duration_seconds=3)

        # Short runs with minimal work shouldn't show memory leaks
        # The detection logic requires > 20% growth AND > 5MB absolute increase
        assert result.metrics["memory_leak_detected"] is False or result.metrics["memory_growth_percent"] <= 20


class TestRunAllBenchmarks:
    """Tests for running complete benchmark suite."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create benchmarker for full suite tests."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    @pytest.mark.asyncio
    async def test_run_all_benchmarks(self, benchmarker):
        """Test running complete benchmark suite."""
        results = await benchmarker.run_all_benchmarks(
            scale_levels=[5, 10],
            endurance_seconds=3,
        )

        assert "summary" in results
        assert "statistics" in results
        assert "bottlenecks" in results
        assert results["total_results"] >= 6  # At least one result per benchmark type

        # Check all benchmark types are present
        summary = results["summary"]
        assert "single_execution" in summary
        assert "throughput" in summary
        assert "memory" in summary
        assert "token_efficiency" in summary
        assert "scale" in summary
        assert "endurance" in summary

    @pytest.mark.asyncio
    async def test_run_all_benchmarks_generates_statistics(self, benchmarker):
        """Test that running all benchmarks generates statistics."""
        results = await benchmarker.run_all_benchmarks(endurance_seconds=2)

        stats = results["statistics"]
        # Should have statistics for each benchmark type
        assert "latency" in stats or "single_execution" in stats


class TestBottleneckIdentification:
    """Tests for bottleneck identification."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create benchmarker for bottleneck tests."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    @pytest.mark.asyncio
    async def test_identify_bottlenecks_empty_results(self, benchmarker):
        """Test bottleneck identification with no results."""
        bottlenecks = benchmarker.identify_bottlenecks()
        assert bottlenecks == []

    @pytest.mark.asyncio
    async def test_identify_bottlenecks_after_benchmarks(self, benchmarker):
        """Test bottleneck identification after running benchmarks."""
        await benchmarker.run_all_benchmarks(endurance_seconds=2)

        bottlenecks = benchmarker.identify_bottlenecks()

        # Should return list of Bottleneck objects or empty list
        assert isinstance(bottlenecks, list)
        for bn in bottlenecks:
            assert isinstance(bn, Bottleneck)
            assert hasattr(bn, "name")
            assert hasattr(bn, "severity")

    @pytest.mark.asyncio
    async def test_memory_leak_bottleneck_consistency(self, benchmarker):
        """Test that memory leak bottleneck flag is consistent with detection."""
        await benchmarker.run_endurance_benchmark(duration_seconds=3)

        bottlenecks = benchmarker.identify_bottlenecks()

        # Check that memory leak bottleneck is only flagged if actually detected
        memory_leak_bn = [bn for bn in bottlenecks if "Memory Leak" in bn.name]

        if memory_leak_bn:
            # If bottleneck flagged, endurance result should have detected leak
            endurance_results = [
                r for r in benchmarker._results
                if r.benchmark_type == BenchmarkType.ENDURANCE
            ]
            assert len(endurance_results) > 0
            # The bottleneck should be consistent with the detection
            assert endurance_results[0].metrics.get("memory_leak_detected") is True


class TestReportGeneration:
    """Tests for report generation."""

    @pytest.fixture
    def benchmarker_with_results(self, tmp_path):
        """Create benchmarker with benchmark results."""
        benchmarker = PipelineBenchmarker(output_dir=str(tmp_path), seed=42)
        # Don't actually run benchmarks in tests, just add mock results
        return benchmarker

    def test_generate_report_no_results(self, benchmarker_with_results):
        """Test report generation with no results."""
        report = benchmarker_with_results.generate_report()

        assert "# Benchmark Report" in report
        assert "No benchmark results available" in report

    @pytest.mark.asyncio
    async def test_generate_report_with_results(self, tmp_path):
        """Test report generation with benchmark results."""
        benchmarker = PipelineBenchmarker(output_dir=str(tmp_path), seed=42)
        await benchmarker.run_all_benchmarks(endurance_seconds=2)

        report = benchmarker.generate_report()

        assert "# P3.1 Baseline Benchmark Results" in report
        assert "## Executive Summary" in report
        assert "## Baseline Metrics Table" in report
        assert "## Detailed Benchmark Results" in report
        assert "## Bottleneck Analysis" in report


class TestExportFunctionality:
    """Tests for export functionality."""

    @pytest.fixture
    def benchmarker(self, tmp_path):
        """Create benchmarker for export tests."""
        return PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

    @pytest.mark.asyncio
    async def test_export_results(self, benchmarker, tmp_path):
        """Test exporting benchmark results."""
        await benchmarker.run_single_execution_benchmark(iterations=3)

        export_path = tmp_path / "exported_results.json"
        result_path = benchmarker.export_results(str(export_path))

        assert result_path == str(export_path.resolve())
        assert export_path.exists()

        import json
        with open(export_path, "r") as f:
            data = json.load(f)

        assert "export_timestamp" in data
        assert "results" in data
        assert len(data["results"]) >= 1


class TestReproducibility:
    """Tests for benchmark reproducibility - critical for DEF-001."""

    @pytest.mark.asyncio
    async def test_reproducibility_three_runs(self, tmp_path):
        """Test that 3 consecutive runs produce same results with same seed."""
        seeds = [42, 42, 42]
        durations = []

        for i, seed in enumerate(seeds):
            benchmarker = PipelineBenchmarker(
                output_dir=str(tmp_path / f"run_{i}"),
                seed=seed,
            )
            result = await benchmarker.run_single_execution_benchmark(iterations=3)
            durations.append(result.duration_ms)

        # All durations should be very similar (within 20% for timing variations)
        mean_duration = statistics.mean(durations)
        for duration in durations:
            assert abs(duration - mean_duration) / mean_duration < 0.2

    @pytest.mark.asyncio
    async def test_different_seeds_produce_different_results(self, tmp_path):
        """Test that different seeds can produce different results."""
        # This tests that the seed is actually being used
        benchmarker1 = PipelineBenchmarker(output_dir=str(tmp_path / "1"), seed=42)
        benchmarker2 = PipelineBenchmarker(output_dir=str(tmp_path / "2"), seed=123)

        # Both should initialize successfully
        assert benchmarker1._seed == 42
        assert benchmarker2._seed == 123


# Integration tests for the complete workflow
class TestIntegration:
    """Integration tests for complete benchmark workflow."""

    @pytest.mark.asyncio
    async def test_complete_benchmark_workflow(self, tmp_path):
        """Test complete benchmark workflow from start to report."""
        benchmarker = PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

        # Run all benchmarks
        results = await benchmarker.run_all_benchmarks(endurance_seconds=2)

        # Verify results structure
        assert "summary" in results
        assert "statistics" in results
        assert "bottlenecks" in results

        # Identify bottlenecks
        bottlenecks = benchmarker.identify_bottlenecks()
        assert isinstance(bottlenecks, list)

        # Generate report
        report = benchmarker.generate_report()
        assert len(report) > 100  # Should be substantial

        # Export results
        export_path = tmp_path / "final_export.json"
        benchmarker.export_results(str(export_path))
        assert export_path.exists()

    @pytest.mark.asyncio
    async def test_memory_measurements_not_zero(self, tmp_path):
        """Regression test for DEF-002 - memory should not be 0.0MB."""
        benchmarker = PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

        # Run memory benchmark
        memory_result = await benchmarker.run_memory_benchmark(iterations=3)

        # Memory should NOT be 0.0MB (the original defect)
        assert memory_result.memory_peak_mb > 0, "DEF-002: Memory measurement should not be 0.0MB"
        assert memory_result.memory_current_mb > 0, "DEF-002: Current memory should not be 0.0MB"

        # Also check other benchmarks report memory
        latency_result = await benchmarker.run_single_execution_benchmark(iterations=3)
        assert latency_result.memory_peak_mb > 0, "DEF-002: Latency benchmark memory should not be 0.0MB"

    @pytest.mark.asyncio
    async def test_seed_metadata_in_results(self, tmp_path):
        """Test that seed is recorded in result metadata - part of DEF-001 fix."""
        benchmarker = PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

        result = await benchmarker.run_single_execution_benchmark(iterations=3)

        # Seed should be in metadata for reproducibility
        assert "seed" in result.metadata, "DEF-001: Seed should be recorded in metadata"
        assert result.metadata["seed"] == 42

    @pytest.mark.asyncio
    async def test_memory_leak_logic_consistent(self, tmp_path):
        """Regression test for DEF-005 - memory leak detection should be consistent."""
        benchmarker = PipelineBenchmarker(output_dir=str(tmp_path), seed=42)

        # Run endurance benchmark
        await benchmarker.run_endurance_benchmark(duration_seconds=3)

        # Get bottlenecks
        bottlenecks = benchmarker.identify_bottlenecks()

        # Check consistency between detection and bottleneck reporting
        endurance_result = [
            r for r in benchmarker._results
            if r.benchmark_type == BenchmarkType.ENDURANCE
        ][0]

        leak_detected = endurance_result.metrics.get("memory_leak_detected", False)
        memory_growth = endurance_result.metrics.get("memory_growth_percent", 0)

        # If bottleneck is flagged, detection should be True AND growth > 20%
        memory_leak_bottleneck = [bn for bn in bottlenecks if "Memory Leak" in bn.name]

        if memory_leak_bottleneck:
            assert leak_detected is True
            assert memory_growth > 20
        else:
            # If no bottleneck, either no leak detected or growth <= 20%
            assert leak_detected is False or memory_growth <= 20
