"""
Observability module for GAIA.

This module provides comprehensive observability infrastructure including:
- Distributed tracing with W3C and B3 context propagation
- Structured JSON logging with context enrichment
- Metrics collection with Prometheus-compatible export
- Thread-safe operations for concurrent environments

Example:
    >>> from gaia.observability import (
    ...     ObservabilityCore,
    ...     traced,
    ...     SpanKind,
    ...     SpanStatus,
    ... )
    >>>
    >>> # Initialize observability
    >>> obs = ObservabilityCore(
    ...     service_name="gaia-api",
    ...     log_level="INFO",
    ...     enable_tracing=True,
    ...     enable_metrics=True,
    ... )
    >>>
    >>> # Use context manager for spans
    >>> with obs.trace("operation", kind=SpanKind.SERVER) as span:
    ...     span.set_attribute("key", "value")
    ...     obs.log_info("Operation completed")
    >>>
    >>> # Use decorator for automatic tracing
    >>> @traced(kind=SpanKind.CLIENT)
    ... def external_call():
    ...     return api.request()
"""

from gaia.observability.core import (
    ObservabilityCore,
    traced,
    get_observability,
)
from gaia.observability.metrics import (
    MetricsCollector,
    Counter,
    Gauge,
    Histogram,
    get_metrics_collector,
)
from gaia.observability.tracing import (
    Span,
    SpanKind,
    SpanStatus,
    TraceContext,
    TraceContextManager,
    W3CPropagator,
    B3Propagator,
    TracePropagator,
    get_current_trace_context,
    set_current_trace_context,
)
from gaia.observability.logging import (
    JSONFormatter,
    ConsoleSink,
    FileSink,
    MultiSink,
    ContextFilter,
    setup_logging,
)
from gaia.observability.exporters import (
    PrometheusExporter,
    ConsoleExporter,
    get_exporter,
)

__version__ = "1.0.0"

__all__ = [
    # Core
    "ObservabilityCore",
    "traced",
    "get_observability",
    # Metrics
    "MetricsCollector",
    "Counter",
    "Gauge",
    "Histogram",
    "get_metrics_collector",
    # Tracing
    "Span",
    "SpanKind",
    "SpanStatus",
    "TraceContext",
    "TraceContextManager",
    "TracePropagator",
    "W3CPropagator",
    "B3Propagator",
    "get_current_trace_context",
    "set_current_trace_context",
    # Logging
    "JSONFormatter",
    "ConsoleSink",
    "FileSink",
    "MultiSink",
    "ContextFilter",
    "setup_logging",
    # Exporters
    "PrometheusExporter",
    "ConsoleExporter",
    "get_exporter",
    # Version
    "__version__",
]
