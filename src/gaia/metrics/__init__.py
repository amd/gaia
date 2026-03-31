"""
GAIA Metrics Module

Runtime metrics tracking for GAIA pipeline execution.

This module provides comprehensive metrics collection, analysis, and reporting
for the GAIA pipeline system. It tracks key performance indicators including:

Efficiency Metrics:
    - TokenEfficiency: Tokens used per feature delivered
    - ContextUtilization: Percentage of context window used effectively

Quality Metrics:
    - QualityVelocity: Iterations to reach quality threshold
    - DefectDensity: Defects per KLOC (thousand lines of code)

Reliability Metrics:
    - MTTR: Mean time to remediate defects (in hours)
    - AuditCompleteness: Percentage of actions logged

Module Structure:
    - models.py: Data models (MetricSnapshot, MetricType, MetricStatistics)
    - collector.py: Thread-safe MetricsCollector class
    - analyzer.py: MetricsAnalyzer for statistical analysis

Example:
    >>> from gaia.metrics import MetricsCollector, MetricsAnalyzer, MetricType
    >>> collector = MetricsCollector(collector_id="pipeline-001")
    >>> collector.record_metric(
    ...     loop_id="loop-001",
    ...     phase="DEVELOPMENT",
    ...     metric_type=MetricType.TOKEN_EFFICIENCY,
    ...     value=0.85
    ... )
    >>> analyzer = MetricsAnalyzer(collector)
    >>> report = analyzer.generate_insights(loop_id="loop-001")
"""

from gaia.metrics.analyzer import (
    Anomaly,
    AnomalyCallback,
    AnomalyType,
    CorrelationResult,
    MetricsAnalyzer,
    TrendAnalysis,
    TrendDirection,
)
from gaia.metrics.benchmarks import (
    BenchmarkResult,
    BenchmarkStatistics,
    BenchmarkType,
    Bottleneck,
    PipelineBenchmarker,
    run_benchmarks_and_generate_report,
)
from gaia.metrics.collector import (
    ContextTracking,
    MetricsCollector,
    QualityIteration,
    TokenTracking,
)
from gaia.metrics.models import (
    MetricSnapshot,
    MetricsReport,
    MetricStatistics,
    MetricType,
)
from gaia.metrics.production_monitor import ProductionMetrics, ProductionMonitor

__all__ = [
    # Models
    "MetricType",
    "MetricSnapshot",
    "MetricStatistics",
    "MetricsReport",
    # Collector
    "MetricsCollector",
    "TokenTracking",
    "ContextTracking",
    "QualityIteration",
    # Analyzer
    "MetricsAnalyzer",
    "TrendAnalysis",
    "TrendDirection",
    "Anomaly",
    "AnomalyType",
    "CorrelationResult",
    "AnomalyCallback",
    # Benchmarks
    "PipelineBenchmarker",
    "BenchmarkType",
    "BenchmarkResult",
    "BenchmarkStatistics",
    "Bottleneck",
    "run_benchmarks_and_generate_report",
    # P4 additions - production monitoring
    "ProductionMonitor",
    "ProductionMetrics",
]

__version__ = "1.2.0"  # Updated with benchmarking module
