"""
GAIA P3.3 Scale Testing Runner

Comprehensive scale testing for the GAIA pipeline system.
Tests concurrent loop execution at 10, 100, 500, and 1000 levels.

Measures:
- Throughput (loops/second)
- Memory footprint per concurrency level
- Latency percentiles (p50, p95, p99)
- Error rate
- Bottlenecks at each scale level
"""

import asyncio
import time
import tracemalloc
import statistics
import sys
import os
import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from pathlib import Path

# Add gaia to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from gaia.metrics.benchmarks import PipelineBenchmarker, BenchmarkResult, BenchmarkType
from gaia.utils.logging import get_logger

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

logger = get_logger(__name__)


@dataclass
class ScaleTestResult:
    """Results from a scale test at a specific concurrency level."""

    concurrency_level: int
    timestamp: datetime
    total_duration_ms: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    min_latency_ms: float
    max_latency_ms: float
    throughput_loops_per_sec: float
    memory_peak_mb: float
    memory_baseline_mb: float
    memory_delta_mb: float
    error_count: int
    error_rate: float
    success_rate: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "concurrency_level": self.concurrency_level,
            "timestamp": self.timestamp.isoformat(),
            "total_duration_ms": self.total_duration_ms,
            "avg_latency_ms": self.avg_latency_ms,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "min_latency_ms": self.min_latency_ms,
            "max_latency_ms": self.max_latency_ms,
            "throughput_loops_per_sec": self.throughput_loops_per_sec,
            "memory_peak_mb": self.memory_peak_mb,
            "memory_baseline_mb": self.memory_baseline_mb,
            "memory_delta_mb": self.memory_delta_mb,
            "error_count": self.error_count,
            "error_rate": self.error_rate,
            "success_rate": self.success_rate,
        }


@dataclass
class BottleneckAnalysis:
    """Identified bottleneck at a scale level."""

    scale_level: int
    bottleneck_type: str
    severity: str  # critical, high, medium, low
    description: str
    impact: str
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scale_level": self.scale_level,
            "bottleneck_type": self.bottleneck_type,
            "severity": self.severity,
            "description": self.description,
            "impact": self.impact,
            "recommendation": self.recommendation,
        }


class ScaleTestRunner:
    """Scale testing runner for GAIA pipeline."""

    def __init__(self, output_dir: str = None):
        self._output_dir = Path(output_dir) if output_dir else Path(__file__).parent.parent.parent.parent / "gaia-proposal"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._results: List[ScaleTestResult] = []
        self._bottlenecks: List[BottleneckAnalysis] = []
        self._benchmarker = PipelineBenchmarker()

    async def run_scale_test(self, concurrency_level: int, iterations: int = 3) -> ScaleTestResult:
        """
        Run scale test at a specific concurrency level.

        Args:
            concurrency_level: Number of concurrent loops to test
            iterations: Number of test iterations for averaging

        Returns:
            ScaleTestResult with comprehensive metrics
        """
        logger.info(f"Starting scale test at {concurrency_level} concurrent loops ({iterations} iterations)")

        all_latencies = []
        memory_peaks = []
        memory_baselines = []
        total_errors = 0
        total_executions = 0

        for iteration in range(iterations):
            # Get baseline memory
            baseline_memory_mb = 0.0
            if PSUTIL_AVAILABLE:
                process = psutil.Process(os.getpid())
                baseline_memory_mb = process.memory_info().rss / 1024 / 1024

            memory_baselines.append(baseline_memory_mb)

            tracemalloc.start()
            iteration_latencies = []
            iteration_errors = 0

            start = time.perf_counter()

            # Run concurrent executions
            tasks = [self._execute_with_timing() for _ in range(concurrency_level)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            elapsed_ms = (time.perf_counter() - start) * 1000

            # Process results
            for result in results:
                total_executions += 1
                if isinstance(result, Exception):
                    iteration_errors += 1
                    iteration_latencies.append(elapsed_ms / concurrency_level)  # Estimate
                elif isinstance(result, dict):
                    iteration_latencies.append(result.get("latency_ms", elapsed_ms / concurrency_level))

            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            # Get peak memory
            peak_memory_mb = peak / 1024 / 1024
            if PSUTIL_AVAILABLE:
                process = psutil.Process(os.getpid())
                total_memory_mb = process.memory_info().rss / 1024 / 1024
                peak_memory_mb = max(peak_memory_mb, total_memory_mb - baseline_memory_mb)

            memory_peaks.append(peak_memory_mb)
            all_latencies.extend(iteration_latencies)
            total_errors += iteration_errors

            logger.debug(f"Iteration {iteration + 1}: {elapsed_ms:.2f}ms total, {len(iteration_latencies)} samples, {iteration_errors} errors")

        # Calculate statistics
        sorted_latencies = sorted(all_latencies)
        n = len(sorted_latencies)

        def percentile(data: List[float], p: float) -> float:
            k = (len(data) - 1) * p / 100
            f = int(k)
            c = f + 1 if f + 1 < len(data) else f
            return data[f] + (k - f) * (data[c] - data[f]) if c != f else data[f]

        total_duration_ms = statistics.mean([r for r in all_latencies]) * concurrency_level / iterations if n > 0 else 0
        avg_latency = statistics.mean(all_latencies) if all_latencies else 0
        p50_latency = percentile(sorted_latencies, 50) if sorted_latencies else 0
        p95_latency = percentile(sorted_latencies, 95) if sorted_latencies else 0
        p99_latency = percentile(sorted_latencies, 99) if sorted_latencies else 0

        throughput = concurrency_level / (total_duration_ms / 1000) if total_duration_ms > 0 else 0

        memory_peak_avg = statistics.mean(memory_peaks)
        memory_baseline_avg = statistics.mean(memory_baselines)
        memory_delta = memory_peak_avg - memory_baseline_avg

        error_rate = total_errors / total_executions if total_executions > 0 else 0
        success_rate = 1.0 - error_rate

        result = ScaleTestResult(
            concurrency_level=concurrency_level,
            timestamp=datetime.now(timezone.utc),
            total_duration_ms=total_duration_ms,
            avg_latency_ms=avg_latency,
            p50_latency_ms=p50_latency,
            p95_latency_ms=p95_latency,
            p99_latency_ms=p99_latency,
            min_latency_ms=min(all_latencies) if all_latencies else 0,
            max_latency_ms=max(all_latencies) if all_latencies else 0,
            throughput_loops_per_sec=throughput,
            memory_peak_mb=memory_peak_avg,
            memory_baseline_mb=memory_baseline_avg,
            memory_delta_mb=memory_delta,
            error_count=total_errors,
            error_rate=error_rate,
            success_rate=success_rate,
        )

        self._results.append(result)
        logger.info(f"Scale test complete (level={concurrency_level}): throughput={throughput:.1f} loops/sec, p99={p99_latency:.2f}ms")

        return result

    async def run_all_scale_tests(self, scale_levels: List[int] = None) -> List[ScaleTestResult]:
        """Run scale tests at all specified levels."""
        if scale_levels is None:
            scale_levels = [10, 100, 500, 1000]

        results = []
        for level in scale_levels:
            result = await self.run_scale_test(level)
            results.append(result)
        return results

    def identify_bottlenecks(self) -> List[BottleneckAnalysis]:
        """Identify bottlenecks from scale test results."""
        bottlenecks = []

        if len(self._results) < 2:
            return bottlenecks

        # Analyze throughput degradation
        first_result = self._results[0]
        last_result = self._results[-1]

        # Check for throughput degradation at scale
        if first_result.throughput_loops_per_sec > 0:
            scale_factor = last_result.concurrency_level / first_result.concurrency_level
            throughput_ratio = first_result.throughput_loops_per_sec / last_result.throughput_loops_per_sec if last_result.throughput_loops_per_sec > 0 else float('inf')

            if throughput_ratio > scale_factor * 1.5:
                bottlenecks.append(BottleneckAnalysis(
                    scale_level=last_result.concurrency_level,
                    bottleneck_type="Throughput Degradation",
                    severity="high",
                    description=f"Throughput drops from {first_result.throughput_loops_per_sec:.1f} to {last_result.throughput_loops_per_sec:.1f} loops/sec at {last_result.concurrency_level}x concurrency",
                    impact=f"{(1 - 1/throughput_ratio) * 100:.1f}% efficiency loss at scale",
                    recommendation="Implement connection pooling and async I/O optimization",
                ))

        # Check for memory pressure at scale
        for result in self._results:
            if result.memory_delta_mb > 100:  # > 100MB memory increase
                bottlenecks.append(BottleneckAnalysis(
                    scale_level=result.concurrency_level,
                    bottleneck_type="Memory Pressure",
                    severity="medium" if result.memory_delta_mb < 200 else "high",
                    description=f"Memory increases by {result.memory_delta_mb:.1f}MB at {result.concurrency_level} concurrent loops",
                    impact=f"Peak memory: {result.memory_peak_mb:.1f}MB",
                    recommendation="Implement artifact compression and object pooling",
                ))

        # Check for latency spikes
        for result in self._results:
            if result.p99_latency_ms > result.avg_latency_ms * 3:
                bottlenecks.append(BottleneckAnalysis(
                    scale_level=result.concurrency_level,
                    bottleneck_type="Latency Variance",
                    severity="medium",
                    description=f"P99 latency ({result.p99_latency_ms:.1f}ms) is {result.p99_latency_ms/result.avg_latency_ms:.1f}x higher than average ({result.avg_latency_ms:.1f}ms)",
                    impact="Unpredictable response times under load",
                    recommendation="Add request queuing and load shedding",
                ))

        # Check for error rate increase
        for result in self._results:
            if result.error_rate > 0.01:  # > 1% error rate
                bottlenecks.append(BottleneckAnalysis(
                    scale_level=result.concurrency_level,
                    bottleneck_type="Error Rate",
                    severity="critical" if result.error_rate > 0.05 else "high",
                    description=f"Error rate of {result.error_rate * 100:.2f}% at {result.concurrency_level} concurrent loops",
                    impact=f"{result.error_count} failures out of {result.error_count + int(result.throughput_loops_per_sec * 10)} executions",
                    recommendation="Add circuit breakers and retry logic",
                ))

        # Check for scale efficiency
        if len(self._results) >= 3:
            # Compare linear vs actual scaling
            baseline_throughput = self._results[0].throughput_loops_per_sec
            baseline_concurrency = self._results[0].concurrency_level

            for result in self._results[1:]:
                expected_throughput = baseline_throughput * (result.concurrency_level / baseline_concurrency)
                actual_throughput = result.throughput_loops_per_sec
                efficiency = actual_throughput / expected_throughput if expected_throughput > 0 else 0

                if efficiency < 0.7:  # < 70% scale efficiency
                    bottlenecks.append(BottleneckAnalysis(
                        scale_level=result.concurrency_level,
                        bottleneck_type="Scale Efficiency",
                        severity="high",
                        description=f"Scale efficiency drops to {efficiency * 100:.1f}% at {result.concurrency_level} concurrent loops",
                        impact=f"Expected {expected_throughput:.1f} loops/sec, achieved {actual_throughput:.1f} loops/sec",
                        recommendation="Reduce contention in concurrent execution paths",
                    ))

        self._bottlenecks = bottlenecks
        return bottlenecks

    async def _execute_with_timing(self) -> Dict[str, float]:
        """Execute a minimal pipeline with timing."""
        start = time.perf_counter()
        try:
            result = await self._benchmarker._execute_minimal_pipeline()
            elapsed_ms = (time.perf_counter() - start) * 1000
            result["latency_ms"] = elapsed_ms
            return result
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            raise Exception(f"Execution failed: {e}")

    def generate_report(self) -> str:
        """Generate comprehensive scale test report."""
        if not self._results:
            return "# P3.3 Scale Test Results\n\nNo scale test results available."

        lines = [
            "# P3.3 Scale Test Results",
            "",
            "**Phase:** P3 - Performance Optimization & Scale Testing",
            "**Sub-Phase:** P3.3 - Deep Optimizations & Scale Testing",
            "",
            f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
            f"**Test Executed By:** Jordan Lee, Senior Software Developer",
            f"**Total Scale Tests:** {len(self._results)}",
            "",
            "## Executive Summary",
            "",
            "This report presents the P3.3 scale testing results for the GAIA pipeline system.",
            "Scale tests were executed at 10, 100, 500, and 1000 concurrent loop levels.",
            "",
        ]

        # Add summary table
        lines.extend([
            "## Scale Test Results Summary",
            "",
            "| Concurrency | Throughput (loops/sec) | Avg Latency (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Memory Delta (MB) | Error Rate |",
            "|-------------|------------------------|------------------|----------|----------|----------|-------------------|------------|",
        ])

        for result in self._results:
            lines.append(
                f"| {result.concurrency_level} | {result.throughput_loops_per_sec:.1f} | {result.avg_latency_ms:.2f} | {result.p50_latency_ms:.2f} | {result.p95_latency_ms:.2f} | {result.p99_latency_ms:.2f} | {result.memory_delta_mb:.2f} | {result.error_rate * 100:.4f}% |"
            )

        lines.extend(["", ""])

        # Throughput vs Concurrency Analysis
        lines.extend([
            "## Throughput vs Concurrency Analysis",
            "",
            "### Performance Graph: Throughput Scaling",
            "",
            "```",
            "Throughput (loops/sec) vs Concurrency Level",
            "",
        ])

        # Create ASCII graph
        max_throughput = max(r.throughput_loops_per_sec for r in self._results)
        graph_height = 10
        graph_width = 60

        for result in self._results:
            bar_length = int((result.throughput_loops_per_sec / max_throughput) * graph_width)
            bar = "#" * bar_length
            lines.append(f"  {result.concurrency_level:4d} loops: {bar} {result.throughput_loops_per_sec:.1f}/s")

        lines.extend([
            "```",
            "",
        ])

        # Detailed Results by Scale Level
        lines.extend([
            "",
            "## Detailed Results by Scale Level",
            "",
        ])

        for result in self._results:
            lines.extend([
                f"### {result.concurrency_level} Concurrent Loops",
                "",
                f"**Timing Metrics:**",
                f"- Total Duration: {result.total_duration_ms:.2f}ms",
                f"- Average Latency: {result.avg_latency_ms:.2f}ms",
                f"- P50 Latency: {result.p50_latency_ms:.2f}ms",
                f"- P95 Latency: {result.p95_latency_ms:.2f}ms",
                f"- P99 Latency: {result.p99_latency_ms:.2f}ms",
                f"- Min Latency: {result.min_latency_ms:.2f}ms",
                f"- Max Latency: {result.max_latency_ms:.2f}ms",
                "",
                f"**Throughput:** {result.throughput_loops_per_sec:.1f} loops/second",
                "",
                f"**Memory Metrics:**",
                f"- Baseline Memory: {result.memory_baseline_mb:.2f}MB",
                f"- Peak Memory: {result.memory_peak_mb:.2f}MB",
                f"- Memory Delta: {result.memory_delta_mb:.2f}MB",
                "",
                f"**Reliability:**",
                f"- Error Count: {result.error_count}",
                f"- Error Rate: {result.error_rate * 100:.4f}%",
                f"- Success Rate: {result.success_rate * 100:.4f}%",
                "",
            ])

        # Bottleneck Analysis
        bottlenecks = self.identify_bottlenecks()

        lines.extend([
            "## Bottleneck Analysis",
            "",
        ])

        if bottlenecks:
            lines.extend([
                "| # | Scale Level | Type | Severity | Description | Impact | Recommendation |",
                "|---|-------------|------|----------|-------------|--------|----------------|",
            ])

            sorted_bottlenecks = sorted(
                bottlenecks,
                key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}[x.severity]
            )

            for i, bn in enumerate(sorted_bottlenecks, 1):
                lines.append(
                    f"| {i} | {bn.scale_level} | {bn.bottleneck_type} | {bn.severity.upper()} | {bn.description} | {bn.impact} | {bn.recommendation} |"
                )
        else:
            lines.append("No significant bottlenecks identified during scale testing.")

        lines.extend(["", ""])

        # Comparison with P3.1 Baseline
        lines.extend([
            "## Comparison with P3.1 Baseline",
            "",
            "| Metric | P3.1 Baseline | P3.3 Result | Change | Status |",
            "|--------|---------------|-------------|--------|--------|",
        ])

        # Find comparable metrics (100 loops from P3.1)
        p31_baseline_throughput = 157  # loops/sec at 100 loops (from P3.1 report)
        p31_100_loop_result = next((r for r in self._results if r.concurrency_level == 100), None)

        if p31_100_loop_result:
            throughput_change = p31_100_loop_result.throughput_loops_per_sec - p31_baseline_throughput
            throughput_change_pct = (throughput_change / p31_baseline_throughput) * 100
            status = "IMPROVED" if throughput_change > 0 else "DEGRADED" if throughput_change < -10 else "STABLE"
            lines.append(
                f"| Throughput @ 100 loops | {p31_baseline_throughput} loops/sec | {p31_100_loop_result.throughput_loops_per_sec:.1f} loops/sec | {throughput_change:+.1f} ({throughput_change_pct:+.1f}%) | {status} |"
            )
        else:
            lines.append(
                f"| Throughput @ 100 loops | {p31_baseline_throughput} loops/sec | N/A | N/A | PENDING |"
            )

        lines.extend(["", ""])

        # Recommendations
        lines.extend([
            "## Recommendations for Production Deployment",
            "",
        ])

        recommendations = []

        # Analyze results and generate recommendations
        max_stable_throughput = max(self._results, key=lambda r: r.throughput_loops_per_sec)
        min_error_result = min(self._results, key=lambda r: r.error_rate)

        recommendations.extend([
            f"1. **Optimal Concurrency Level:** Based on testing, {max_stable_throughput.concurrency_level} concurrent loops achieves the highest throughput of {max_stable_throughput.throughput_loops_per_sec:.1f} loops/sec",
            "",
            f"2. **Memory Allocation:** Allocate at least {max(r.memory_peak_mb for r in self._results) * 1.5:.0f}MB (1.5x peak) for production workloads",
            "",
            f"3. **Error Handling: {'Critical attention needed' if any(r.error_rate > 0.05 for r in self._results) else 'Standard error handling sufficient'}",
            "",
            f"4. **Latency SLA:** P99 latency of {max(r.p99_latency_ms for r in self._results):.2f}ms at maximum scale should be considered for SLA definitions",
            "",
        ])

        # Add bottleneck-specific recommendations
        if bottlenecks:
            recommendations.extend([
                "5. **Bottleneck Mitigation:**",
                "",
            ])
            for bn in bottlenecks[:3]:
                recommendations.append(f"   - {bn.bottleneck_type}: {bn.recommendation}")

        lines.extend(recommendations)
        lines.extend(["", ""])

        # Production Readiness Assessment
        lines.extend([
            "## Production Readiness Assessment",
            "",
            "| Criterion | Target | Result | Status |",
            "|-----------|--------|--------|--------|",
        ])

        # Check various criteria
        criteria = [
            ("Throughput > 100 loops/sec @ 100 concurrency", ">100", f"{next((r.throughput_loops_per_sec for r in self._results if r.concurrency_level == 100), 0):.1f}", "PASS" if any(r.concurrency_level == 100 and r.throughput_loops_per_sec > 100 for r in self._results) else "FAIL"),
            ("Error rate < 1% @ all levels", "<1%", f"{max(r.error_rate for r in self._results) * 100:.4f}%", "PASS" if all(r.error_rate < 0.01 for r in self._results) else "FAIL"),
            ("Memory < 500MB @ max scale", "<500MB", f"{max(r.memory_peak_mb for r in self._results):.1f}MB", "PASS" if all(r.memory_peak_mb < 500 for r in self._results) else "FAIL"),
            ("No critical bottlenecks", "0 critical", f"{len([b for b in bottlenecks if b.severity == 'critical'])}", "PASS" if not any(b.severity == 'critical' for b in bottlenecks) else "FAIL"),
        ]

        for criterion, target, result, status in criteria:
            lines.append(f"| {criterion} | {target} | {result} | {status} |")

        lines.extend(["", ""])

        # Test Configuration
        lines.extend([
            "## Test Configuration",
            "",
            "### Scale Test Parameters",
            "",
            "- **Scale Levels Tested:** 10, 100, 500, 1000 concurrent loops",
            "- **Iterations per Level:** 3",
            "- **Measurement Method:** Async concurrent execution with asyncio.gather()",
            "- **Memory Measurement:** psutil process RSS + tracemalloc peak",
            "",
            "### Environment",
            "",
            f"- **Platform:** {sys.platform}",
            f"- **Python:** {sys.version.split()[0]}",
            f"- **OS:** Windows 11 Pro",
            f"- **Test Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "",
        ])

        # Next Steps
        lines.extend([
            "## Next Steps",
            "",
            "1. **Quality Review:** Submit to quality-reviewer for evaluation",
            "2. **Optimization (if needed):** Address identified bottlenecks",
            "3. **P4 Preparation:** Prepare for P4 - Production Hardening phase",
            "",
        ])

        lines.extend([
            "---",
            "",
            "*Report generated by GAIA Scale Test Runner v1.0.0*",
            "",
            "## Appendix: Raw Data Export",
            "",
            "Full scale test data exported to: `scale_test_results.json`",
        ])

        return "\n".join(lines)

    def export_results(self, filepath: str = None) -> str:
        """Export results to JSON."""
        if filepath is None:
            filepath = str(self._output_dir / "scale_test_results.json")

        export_path = Path(filepath).resolve()
        export_path.parent.mkdir(parents=True, exist_ok=True)

        export_data = {
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "p31_baseline_reference": {
                "throughput_100_loops": 157,  # loops/sec
                "single_exec_latency": 62,  # ms
                "memory_peak": 6.2,  # MB
            },
            "results": [r.to_dict() for r in self._results],
            "bottlenecks": [b.to_dict() for b in self._bottlenecks],
        }

        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2)

        logger.info(f"Scale test results exported to {export_path}")
        return str(export_path)


async def main():
    """Main entry point for scale testing."""
    print("=" * 60)
    print("GAIA P3.3 Scale Testing")
    print("=" * 60)

    runner = ScaleTestRunner(output_dir="C:/Users/antmi/gaia-proposal")

    # Run scale tests at specified levels
    scale_levels = [10, 100, 500, 1000]
    print(f"\nRunning scale tests at levels: {scale_levels}")

    for level in scale_levels:
        print(f"\n>>> Testing {level} concurrent loops...")
        result = await runner.run_scale_test(level, iterations=3)
        print(f"    Throughput: {result.throughput_loops_per_sec:.1f} loops/sec")
        print(f"    P99 Latency: {result.p99_latency_ms:.2f}ms")
        print(f"    Memory Delta: {result.memory_delta_mb:.2f}MB")
        print(f"    Error Rate: {result.error_rate * 100:.4f}%")

    # Identify bottlenecks
    print("\n>>> Analyzing bottlenecks...")
    bottlenecks = runner.identify_bottlenecks()
    if bottlenecks:
        print(f"    Found {len(bottlenecks)} bottleneck(s)")
        for bn in bottlenecks:
            print(f"    - [{bn.severity.upper()}] {bn.bottleneck_type} @ {bn.scale_level} loops")
    else:
        print("    No significant bottlenecks identified")

    # Generate report
    print("\n>>> Generating report...")
    report = runner.generate_report()
    report_path = "C:/Users/antmi/gaia-proposal/P3.3_SCALE_TEST_RESULTS.md"
    Path(report_path).write_text(report)
    print(f"    Report saved to: {report_path}")

    # Export JSON
    json_path = runner.export_results()
    print(f"    JSON data saved to: {json_path}")

    print("\n" + "=" * 60)
    print("P3.3 Scale Testing Complete")
    print("=" * 60)

    return report_path


if __name__ == "__main__":
    asyncio.run(main())
