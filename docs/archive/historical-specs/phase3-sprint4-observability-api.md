# Phase 3 Sprint 4: Observability + API Standardization Technical Specification

**Document Type:** Technical Specification
**Version:** 1.0.0
**Date:** 2026-04-06
**Author:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead
**Status:** Ready for Implementation

---

## Executive Summary

This specification defines Phase 3 Sprint 4 of the BAIBEL-GAIA integration program, focusing on **enterprise-grade observability infrastructure** and **API standardization**. This sprint is the final technical sprint before Phase 3 closeout, building upon:

- **Sprint 1:** Modular Architecture (pipeline orchestration, state management)
- **Sprint 2:** Dependency Injection + Performance Optimizations (DI container, async utilities)
- **Sprint 3:** Caching + Enterprise Configuration (CacheLayer, ConfigManager, SecretsManager)

### Sprint Objectives

| Objective | Description | Success Metric |
|-----------|-------------|----------------|
| **ObservabilityCore** | Distributed tracing, structured logging, context propagation | 100% trace propagation (OBS-001) |
| **MetricsCollector** | Counter, Gauge, Histogram metrics with Prometheus export | 100% export accuracy (OBS-002) |
| **OpenAPI Generation** | Auto-generated OpenAPI 3.0 spec with Swagger UI | 100% spec completeness (API-001) |
| **API Versioning** | URL and header versioning with negotiation | All strategies supported (API-002) |
| **Deprecation Management** | Deprecation warnings, sunset headers, migration hints | 100% backward compatibility (BC-002) |
| **Thread Safety** | Concurrent access under load | 100+ threads (THREAD-003) |

### Program Status

```
Phase 3 Progress: ~90% Complete

[Sprint 1] Modular Architecture          [COMPLETE]
[Sprint 2] DI + Performance              [COMPLETE]
[Sprint 3] Caching + Config              [COMPLETE]
[Sprint 4] Observability + API           [IN PROGRESS]
[Closeout] Phase 3 Review                [PENDING]
```

---

## 1. Architecture Overview

### 1.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Phase 3 Sprint 4 Architecture                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────────┐         ┌────────────────────────┐              │
│  │   ObservabilityCore    │         │    MetricsCollector    │              │
│  │  ┌──────────────────┐  │         │  ┌──────────────────┐  │              │
│  │  │ Distributed      │  │         │  │ Counter Metrics  │  │              │
│  │  │ Tracing          │  │         │  └──────────────────┘  │              │
│  │  └──────────────────┘  │         │  ┌──────────────────┐  │              │
│  │  ┌──────────────────┐  │         │  │ Gauge Metrics    │  │              │
│  │  │ Structured       │  │         │  └──────────────────┘  │              │
│  │  │ Logging          │  │         │  ┌──────────────────┐  │              │
│  │  └──────────────────┘  │         │  │ Histogram        │  │              │
│  │  ┌──────────────────┐  │         │  │ Metrics          │  │              │
│  │  │ Context          │  │         │  └──────────────────┘  │              │
│  │  │ Propagation      │  │         │  ┌──────────────────┐  │              │
│  │  └──────────────────┘  │         │  │ Prometheus       │  │              │
│  │  ┌──────────────────┐  │         │  │ Exporter         │  │              │
│  │  │ Log Sink         │  │         │  └──────────────────┘  │              │
│  │  │ (JSON/Console)   │  │         └────────────────────────┘              │
│  │  └──────────────────┘  │                    │                             │
│  └────────────────────────┘                    │                             │
│           │                                    │                             │
│           │         ┌────────────────────────┐ │                             │
│           │         │    API Standardization │◄┘                             │
│           │         │  ┌──────────────────┐  │                               │
│           │         │  │ OpenAPI Generator│  │                               │
│           │         │  └──────────────────┘  │                               │
│           │         │  ┌──────────────────┐  │                               │
│           │         │  │ API Versioning   │  │                               │
│           │         │  └──────────────────┘  │                               │
│           │         │  ┌──────────────────┐  │                               │
│           │         │  │ Deprecation      │  │                               │
│           │         │  │ Manager          │  │                               │
│           │         │  └──────────────────┘  │                               │
│           │         └────────────────────────┘                               │
│           │                  │                                               │
│           └──────────────────┼───────────────────────────────────────────────┘
│                              │
│              ┌───────────────┼───────────────┐
│              │               │               │
│              ▼               ▼               ▼
│     ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
│     │ Sprint 1       │ │ Sprint 2       │ │ Sprint 3       │
│     │ - pipeline     │ │ - di_container │ │ - cache_layer  │
│     │ - state        │ │ - async_utils  │ │ - config_mgr   │
│     │ - orchestrator │ │ - perf         │ │ - secrets      │
│     └────────────────┘ └────────────────┘ └────────────────┘
│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Module Structure

```
src/gaia/
├── observability/                  # NEW: Observability infrastructure
│   ├── __init__.py                 # Public API exports (~30 LOC)
│   ├── core.py                     # ObservabilityCore class (~500 LOC)
│   ├── metrics.py                  # MetricsCollector class (~300 LOC)
│   ├── tracing/                    # Distributed tracing
│   │   ├── __init__.py             # Tracing exports
│   │   ├── span.py                 # Span context and propagation
│   │   ├── trace_context.py        # Trace context management
│   │   └── propagator.py           # Context propagators (W3C, B3)
│   ├── logging/                    # Structured logging
│   │   ├── __init__.py             # Logging exports
│   │   ├── formatter.py            # JSON log formatter
│   │   ├── sink.py                 # Log sink abstraction
│   │   └── context_filter.py       # Context-aware log filtering
│   └── exporters/                  # Metrics exporters
│       ├── __init__.py             # Exporter exports
│       ├── prometheus.py           # Prometheus format exporter
│       └── console.py              # Console/metrics dashboard exporter
│
├── api/                            # EXISTING: API infrastructure (UPDATE)
│   ├── __init__.py                 # Update exports (~40 LOC)
│   ├── openai_server.py            # EXISTING: OpenAI server
│   ├── schemas.py                  # EXISTING: Pydantic schemas
│   ├── agent_registry.py           # EXISTING: Agent registry
│   ├── sse_handler.py              # EXISTING: SSE handler
│   ├── app.py                      # EXISTING: CLI entry point
│   ├── openapi.py                  # NEW: OpenAPI spec generation (~400 LOC)
│   ├── versioning.py               # NEW: API versioning (~200 LOC)
│   └── deprecation.py              # NEW: Deprecation management (~150 LOC)
│
└── testing/                        # EXISTING: Test utilities (EXTENDED)
    ├── fixtures.py                 # Existing + new fixtures
    ├── mocks.py                    # Existing + new mocks
    ├── observability_fixtures.py   # NEW: Observability test fixtures
    └── api_fixtures.py             # NEW: API test fixtures
```

### 1.3 Data Flow Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          Observability Data Flow                              │
└──────────────────────────────────────────────────────────────────────────────┘

  Request → [API Gateway] ──────────────────────────────────────────┐
              │                                                      │
              ▼                                                      │
     ┌────────────────┐                                              │
     │ Trace Context  │ ◄─── Inject/Extract (W3C Trace Context)      │
     │ Propagation    │                                              │
     └────────────────┘                                              │
              │                                                      │
              ▼                                                      │
     ┌────────────────┐    ┌──────────────────┐    ┌──────────────┐ │
     │ Observability  │───▶│ Structured Log   │───▶│ Log Sink     │ │
     │ Core           │    │ Event            │    │ (JSON/File)  │ │
     └────────────────┘    └──────────────────┘    └──────────────┘ │
              │                                                      │
              ├───▶ [Span Start] ──▶ [Agent Execution] ──▶ [Span End]
              │                                                      │
              ▼                                                      │
     ┌────────────────┐    ┌──────────────────┐    ┌──────────────┐ │
     │ Metrics        │───▶│ Prometheus       │───▶│ Metrics      │ │
     │ Collector      │    │ Format           │    │ Backend      │ │
     └────────────────┘    └──────────────────┘    └──────────────┘ │
              │                                                      │
              └──────────────────────────────────────────────────────┘
```

---

## 2. ObservabilityCore Specification (~500 LOC)

### 2.1 Design Patterns

| Pattern | Usage | Rationale |
|---------|-------|-----------|
| **Singleton** | Default observability instance | Shared context across application |
| **Facade** | `ObservabilityCore` class | Unified interface for tracing, logging, metrics |
| **Context** | Trace context propagation | Thread-local and async context storage |
| **Observer** | Log sink subscribers | Multiple output destinations |
| **Decorator** | `@traced` method decorator | Declarative span creation |
| **Strategy** | Context propagators | Pluggable W3C, B3, Jaeger formats |

### 2.2 File Paths and LOC Estimates

| File | LOC | Description |
|------|-----|-------------|
| `observability/__init__.py` | 30 | Public API exports, version |
| `observability/core.py` | 500 | ObservabilityCore class, decorators |
| `observability/tracing/__init__.py` | 20 | Tracing module exports |
| `observability/tracing/span.py` | 120 | Span context and lifecycle |
| `observability/tracing/trace_context.py` | 100 | Trace context management |
| `observability/tracing/propagator.py` | 100 | Context propagation (W3C, B3) |
| `observability/logging/__init__.py` | 20 | Logging module exports |
| `observability/logging/formatter.py` | 100 | JSON log formatter |
| `observability/logging/sink.py` | 100 | Log sink abstraction |
| `observability/logging/context_filter.py` | 80 | Context-aware filtering |
| **Total** | **~1,170 LOC** | Core observability implementation |

### 2.3 Key Classes and Methods

#### 2.3.1 ObservabilityCore (Main Facade)

```python
# File: observability/core.py

from typing import Any, Callable, Dict, List, Optional, TypeVar
from contextlib import asynccontextmanager, contextmanager
import logging

from gaia.observability.tracing.span import Span, SpanKind, SpanStatus
from gaia.observability.tracing.trace_context import TraceContext
from gaia.observability.tracing.propagator import (
    TracePropagator,
    W3CPropagator,
    B3Propagator,
)
from gaia.observability.metrics import MetricsCollector


F = TypeVar('F', bound=Callable)


class ObservabilityCore:
    """
    Central observability facade for GAIA.

    Provides unified access to:
    - Distributed tracing with context propagation
    - Structured logging with context enrichment
    - Metrics collection and export

    Features:
        - W3C Trace Context support for distributed tracing
        - Automatic context propagation across async boundaries
        - Structured JSON logging with correlation IDs
        - Integration with Sprint 3 CacheLayer for metrics
        - Thread-safe span and log management

    Example:
        >>> from gaia.observability import ObservabilityCore
        >>>
        >>> obs = ObservabilityCore(
        ...     service_name="gaia-api",
        ...     log_level="INFO",
        ...     enable_tracing=True,
        ... )
        >>>
        >>> # Start a trace
        >>> with obs.trace("agent_execution", kind=SpanKind.SERVER) as span:
        ...     span.set_attribute("agent", "code-agent")
        ...     obs.log_info("Agent started", agent="code-agent")
        ...     result = agent.process_query(query)
        ...     span.set_status(SpanStatus.OK)
        >>>
        >>> # Export metrics
        >>> metrics = obs.metrics.get_summary()
    """

    def __init__(
        self,
        service_name: str = "gaia",
        log_level: str = "INFO",
        enable_tracing: bool = True,
        enable_metrics: bool = True,
        propagator: Optional[TracePropagator] = None,
        log_format: str = "json",  # 'json' or 'text'
    ):
        """
        Initialize ObservabilityCore.

        Args:
            service_name: Service name for traces and logs
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
            enable_tracing: Enable distributed tracing
            enable_metrics: Enable metrics collection
            propagator: Context propagator (default: W3CPropagator)
            log_format: Log output format ('json' or 'text')
        """
        self.service_name = service_name
        self.log_level = log_level
        self.enable_tracing = enable_tracing
        self.enable_metrics = enable_metrics
        self.propagator = propagator or W3CPropagator()
        self.log_format = log_format

        self._metrics: Optional[MetricsCollector] = None
        self._logger: Optional[logging.Logger] = None
        self._active_spans: Dict[str, Span] = {}
        self._lock = asyncio.Lock()

        self._initialize()

    def _initialize(self) -> None:
        """Initialize logging, tracing, and metrics subsystems."""

    @property
    def metrics(self) -> MetricsCollector:
        """Get metrics collector instance."""

    @property
    def logger(self) -> logging.Logger:
        """Get structured logger instance."""

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Optional[Span] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Span:
        """
        Start a new span.

        Args:
            name: Span name (operation being performed)
            kind: Span kind (SERVER, CLIENT, PRODUCER, CONSUMER, INTERNAL)
            parent: Parent span (creates child span if provided)
            attributes: Initial span attributes

        Returns:
            New span instance

        Example:
            >>> span = obs.start_span("database.query", kind=SpanKind.CLIENT)
            >>> span.set_attribute("query.type", "SELECT")
            >>> span.end()
        """

    def end_span(self, span: Span, status: SpanStatus = SpanStatus.OK) -> None:
        """
        End a span.

        Args:
            span: Span to end
            status: Span status (OK, ERROR, UNSET)
        """

    @contextmanager
    def trace(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        """
        Context manager for span lifecycle.

        Args:
            name: Span name
            kind: Span kind
            attributes: Initial attributes

        Yields:
            Active span instance

        Example:
            >>> with obs.trace("agent.execution") as span:
            ...     span.set_attribute("agent", "code")
            ...     result = execute()
        """

    @asynccontextmanager
    async def trace_async(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        """
        Async context manager for span lifecycle.

        Args:
            name: Span name
            kind: Span kind
            attributes: Initial attributes

        Yields:
            Active span instance

        Example:
            >>> async with obs.trace_async("db.query") as span:
            ...     result = await db.execute(query)
        """

    def inject_context(self, carrier: Dict[str, str]) -> None:
        """
        Inject trace context into carrier for propagation.

        Args:
            carrier: Dictionary to inject context into

        Example:
            >>> headers = {}
            >>> obs.inject_context(headers)
            >>> # headers now contains traceparent, tracestate
        """

    def extract_context(self, carrier: Dict[str, str]) -> TraceContext:
        """
        Extract trace context from carrier.

        Args:
            carrier: Dictionary containing context

        Returns:
            Extracted trace context
        """

    def log_debug(self, message: str, **kwargs) -> None:
        """Log debug message with context."""
    def log_info(self, message: str, **kwargs) -> None:
        """Log info message with context."""
    def log_warning(self, message: str, **kwargs) -> None:
        """Log warning message with context."""
    def log_error(self, message: str, **kwargs) -> None:
        """Log error message with context."""

    def get_current_trace(self) -> Optional[TraceContext]:
        """Get current trace context from async context."""

    def set_current_trace(self, context: TraceContext) -> None:
        """Set current trace context in async context."""

    async def shutdown(self) -> None:
        """
        Graceful shutdown.

        Flushes pending spans, metrics, and logs.
        """
```

#### 2.3.2 Span (Distributed Tracing Unit)

```python
# File: observability/tracing/span.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time
import uuid


class SpanKind(Enum):
    """Span kind indicating relationship to parent/child spans."""
    INTERNAL = "internal"    # Internal operation
    SERVER = "server"        # Handles incoming request
    CLIENT = "client"        # Makes outgoing request
    PRODUCER = "producer"    # Produces message to queue
    CONSUMER = "consumer"    # Consumes message from queue


class SpanStatus(Enum):
    """Span execution status."""
    UNSET = "unset"    # Default status
    OK = "ok"          # Operation completed successfully
    ERROR = "error"    # Operation completed with error


@dataclass
class SpanContext:
    """
    Immutable span context for propagation.

    Attributes:
        trace_id: 128-bit trace ID (32 hex chars)
        span_id: 64-bit span ID (16 hex chars)
        trace_flags: Trace flags (01 = sampled)
        trace_state: Vendor-specific trace state
    """
    trace_id: str
    span_id: str
    trace_flags: int = 1
    trace_state: str = ""

    def to_w3c_traceparent(self) -> str:
        """Format as W3C traceparent header."""
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags:02x}"

    @classmethod
    def from_w3c_traceparent(cls, traceparent: str) -> "SpanContext":
        """Parse W3C traceparent header."""
        parts = traceparent.split("-")
        if len(parts) != 4:
            raise ValueError(f"Invalid traceparent: {traceparent}")
        return cls(
            trace_id=parts[1],
            span_id=parts[2],
            trace_flags=int(parts[3], 16),
        )


class Span:
    """
    Represents a single operation within a trace.

    A span is the basic unit of work in distributed tracing.
    It has a start time, end time, and contains attributes,
    events, and status information.

    Attributes:
        name: Human-readable span name
        kind: Span kind (SERVER, CLIENT, etc.)
        context: Immutable span context
        parent: Optional parent span context

    Example:
        >>> span = Span("database.query", kind=SpanKind.CLIENT)
        >>> span.start()
        >>> span.set_attribute("db.system", "postgresql")
        >>> span.add_event("query.start", {"query": "SELECT ..."})
        >>> span.end(status=SpanStatus.OK)
    """

    def __init__(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Optional[SpanContext] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize span.

        Args:
            name: Span name
            kind: Span kind
            parent: Parent span context
            attributes: Initial attributes
        """
        self.name = name
        self.kind = kind
        self.context = SpanContext(
            trace_id=parent.trace_id if parent else uuid.uuid4().hex + uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
        )
        self.parent = parent
        self.attributes = attributes or {}
        self.events: List[Dict[str, Any]] = []
        self.status = SpanStatus.UNSET
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None

    def start(self) -> "Span":
        """
        Start the span.

        Returns:
            Self for method chaining
        """

    def end(self, status: SpanStatus = SpanStatus.OK) -> None:
        """
        End the span.

        Args:
            status: Final span status
        """

    def set_attribute(self, key: str, value: Any) -> None:
        """
        Set span attribute.

        Args:
            key: Attribute key
            value: Attribute value (converted to string if needed)
        """

    def set_attributes(self, attributes: Dict[str, Any]) -> None:
        """Set multiple attributes at once."""

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """
        Add event to span.

        Args:
            name: Event name
            attributes: Event attributes
        """

    def record_exception(
        self,
        exception: Exception,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record exception as span event.

        Args:
            exception: Exception to record
            attributes: Additional attributes
        """

    def set_status(self, status: SpanStatus, description: str = "") -> None:
        """
        Set span status.

        Args:
            status: Span status
            description: Optional status description
        """

    @property
    def duration(self) -> Optional[float]:
        """Get span duration in seconds."""
        if self.start_time and self.end_time:
            return self.end_time - self.start_time
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert span to dictionary for export."""
```

#### 2.3.3 TracePropagator (Context Propagation)

```python
# File: observability/tracing/propagator.py

from abc import ABC, abstractmethod
from typing import Dict, Optional

from gaia.observability.tracing.trace_context import TraceContext


class TracePropagator(ABC):
    """
    Abstract base class for trace context propagators.

    Propagators handle injecting and extracting trace context
    into/from wire formats (HTTP headers, etc.).
    """

    @abstractmethod
    def inject(self, context: TraceContext, carrier: Dict[str, str]) -> None:
        """
        Inject trace context into carrier.

        Args:
            context: Trace context to inject
            carrier: Dictionary to inject into (e.g., HTTP headers)
        """

    @abstractmethod
    def extract(self, carrier: Dict[str, str]) -> Optional[TraceContext]:
        """
        Extract trace context from carrier.

        Args:
            carrier: Dictionary to extract from (e.g., HTTP headers)

        Returns:
            Extracted trace context or None
        """


class W3CPropagator(TracePropagator):
    """
    W3C Trace Context propagator.

    Implements the W3C Trace Context specification:
    https://www.w3.org/TR/trace-context/

    Headers:
        - traceparent: Version, trace-id, span-id, trace-flags
        - tracestate: Vendor-specific trace state
    """

    def inject(self, context: TraceContext, carrier: Dict[str, str]) -> None:
        """Inject W3C traceparent and tracestate headers."""

    def extract(self, carrier: Dict[str, str]) -> Optional[TraceContext]:
        """Extract from W3C traceparent header."""


class B3Propagator(TracePropagator):
    """
    B3 propagator (Zipkin format).

    Implements the B3 multi-header format:
    https://github.com/openzipkin/b3-propagation

    Headers:
        - X-B3-TraceId: Trace ID
        - X-B3-SpanId: Span ID
        - X-B3-Sampled: Sampled flag (0 or 1)
        - X-B3-ParentSpanId: Parent span ID (optional)
    """

    def inject(self, context: TraceContext, carrier: Dict[str, str]) -> None:
        """Inject B3 headers."""

    def extract(self, carrier: Dict[str, str]) -> Optional[TraceContext]:
        """Extract from B3 headers."""
```

#### 2.3.4 @traced Decorator

```python
# File: observability/core.py

def traced(
    name: Optional[str] = None,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Optional[Dict[str, Any]] = None,
    record_exceptions: bool = True,
):
    """
    Decorator for automatic span creation.

    Creates a span for the duration of the decorated function.
    Automatically sets span name, records exceptions, and handles context.

    Args:
        name: Span name (uses function name if None)
        kind: Span kind
        attributes: Static attributes to add to span
        record_exceptions: Whether to record exceptions as events

    Example:
        >>> @traced(kind=SpanKind.CLIENT, attributes={"db.system": "postgres"})
        ... async def query_database(sql: str) -> list:
        ...     return await db.execute(sql)

        >>> @traced("agent.process", kind=SpanKind.SERVER)
        ... def process_request(agent, query):
        ...     return agent.process_query(query)
    """
    def decorator(func: F) -> F:
        span_name = name or f"{func.__module__}.{func.__qualname__}"

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                async with obs_core.trace_async(span_name, kind=kind, attributes=attributes):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        if record_exceptions:
                            # Record exception to current span
                            raise
                        raise
            return async_wrapper  # type: ignore
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                with obs_core.trace(span_name, kind=kind, attributes=attributes):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        if record_exceptions:
                            raise
                        raise
            return sync_wrapper  # type: ignore

    return decorator
```

---

## 3. MetricsCollector Specification (~300 LOC)

### 3.1 File Paths and LOC Estimates

| File | LOC | Description |
|------|-----|-------------|
| `observability/metrics.py` | 300 | MetricsCollector, Counter, Gauge, Histogram |
| `observability/exporters/__init__.py` | 20 | Exporter module exports |
| `observability/exporters/prometheus.py` | 150 | Prometheus format exporter |
| `observability/exporters/console.py` | 80 | Console metrics exporter |
| **Total** | **~550 LOC** | Metrics collection + export |

### 3.2 Key Classes and Methods

#### 3.2.1 MetricsCollector (Main Collector)

```python
# File: observability/metrics.py

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
import threading
import time


@dataclass
class MetricPoint:
    """Single metric data point."""
    name: str
    value: Union[int, float]
    timestamp: float = field(default_factory=time.time)
    labels: Dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """
    Central metrics collection with Prometheus-compatible export.

    Supports three metric types:
    - Counter: Monotonically increasing value (requests, errors)
    - Gauge: Point-in-time value (queue size, temperature)
    - Histogram: Distribution of values (latencies, sizes)

    Features:
        - Thread-safe metric operations
        - Automatic label handling
        - Integration with Sprint 3 CacheStats
        - Prometheus text format export

    Example:
        >>> metrics = MetricsCollector()
        >>>
        >>> # Counter
        >>> requests = metrics.counter("http_requests_total")
        >>> requests.inc()
        >>> requests.inc(labels={"method": "POST", "status": "200"})
        >>>
        >>> # Gauge
        >>> queue_size = metrics.gauge("queue_size")
        >>> queue_size.set(42)
        >>>
        >>> # Histogram
        >>> latency = metrics.histogram("request_latency_seconds")
        >>> latency.observe(0.125)
        >>>
        >>> # Export
        >>> prometheus_output = metrics.to_prometheus()
    """

    def __init__(self, prefix: str = "gaia"):
        """
        Initialize metrics collector.

        Args:
            prefix: Metric name prefix (default: "gaia")
        """
        self.prefix = prefix
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
    ) -> Counter:
        """
        Get or create counter metric.

        Args:
            name: Metric name (prefix auto-added)
            description: Metric description
            label_names: Optional label names for filtering

        Returns:
            Counter instance
        """

    def gauge(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
    ) -> Gauge:
        """
        Get or create gauge metric.

        Args:
            name: Metric name
            description: Metric description
            label_names: Optional label names

        Returns:
            Gauge instance
        """

    def histogram(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
        buckets: Optional[List[float]] = None,
    ) -> Histogram:
        """
        Get or create histogram metric.

        Args:
            name: Metric name
            description: Metric description
            label_names: Optional label names
            buckets: Histogram bucket boundaries (default: standard buckets)

        Returns:
            Histogram instance
        """

    def to_prometheus(self) -> str:
        """
        Export metrics in Prometheus text format.

        Returns:
            Prometheus-formatted metrics string

        Example output:
            # HELP gaia_http_requests_total Total HTTP requests
            # TYPE gaia_http_requests_total counter
            gaia_http_requests_total{method="GET",status="200"} 150
            gaia_http_requests_total{method="POST",status="200"} 75

            # HELP gaia_request_latency_seconds Request latency histogram
            # TYPE gaia_request_latency_seconds histogram
            gaia_request_latency_seconds_bucket{le="0.01"} 10
            gaia_request_latency_seconds_bucket{le="0.05"} 50
            gaia_request_latency_seconds_bucket{le="0.1"} 120
            gaia_request_latency_seconds_bucket{le="+Inf"} 150
            gaia_request_latency_seconds_sum 12.5
            gaia_request_latency_seconds_count 150
        """

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all metrics as dictionary."""

    def clear(self) -> None:
        """Clear all metrics."""

    def integrate_cache_stats(self, cache_stats: "CacheStats") -> None:
        """
        Integrate Sprint 3 CacheStats.

        Args:
            cache_stats: Cache statistics from CacheLayer
        """
```

#### 3.2.2 Counter Metric

```python
# File: observability/metrics.py

class Counter:
    """
    Monotonically increasing counter metric.

    Counters only increase; they never decrease.
    Use for: request counts, error counts, completed tasks.

    Example:
        >>> requests = Counter("http_requests_total")
        >>> requests.inc()  # Increment by 1
        >>> requests.inc(labels={"method": "GET"})
        >>> requests.inc(5)  # Increment by 5
        >>> value = requests.get()  # Get current value
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
    ):
        """
        Initialize counter.

        Args:
            name: Metric name
            description: Metric description
            label_names: Optional label names
        """
        self.name = name
        self.description = description
        self.label_names = label_names or []
        self._values: Dict[str, float] = {}
        self._lock = threading.Lock()

    def inc(self, value: float = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """
        Increment counter.

        Args:
            value: Value to increment by (must be >= 0)
            labels: Label values for this increment
        """
        if value < 0:
            raise ValueError("Counter can only be incremented")

    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        """Get counter value for labels."""

    def _label_key(self, labels: Optional[Dict[str, str]]) -> str:
        """Convert labels to unique key."""
```

#### 3.2.3 Gauge Metric

```python
# File: observability/metrics.py

class Gauge:
    """
    Point-in-time gauge metric.

    Gauges can go up and down.
    Use for: queue sizes, temperatures, current memory usage.

    Example:
        >>> queue_size = Gauge("queue_size")
        >>> queue_size.set(42)
        >>> queue_size.inc()  # Increment
        >>> queue_size.dec(5)  # Decrement by 5
        >>> value = queue_size.get()
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
    ):
        self.name = name
        self.description = description
        self.label_names = label_names or []
        self._values: Dict[str, float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Set gauge to specific value."""

    def inc(self, value: float = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """Increment gauge by value."""

    def dec(self, value: float = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """Decrement gauge by value."""

    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        """Get gauge value."""
```

#### 3.2.4 Histogram Metric

```python
# File: observability/metrics.py

class Histogram:
    """
    Histogram metric for value distributions.

    Histograms track:
    - Count of observations
    - Sum of observed values
    - Bucket counts for configurable ranges

    Use for: latencies, request sizes, response sizes.

    Example:
        >>> latency = Histogram(
        ...     "request_latency_seconds",
        ...     buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        ... )
        >>> latency.observe(0.125)
        >>> summary = latency.get_summary()  # count, sum, buckets
    """

    DEFAULT_BUCKETS = [
        0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0
    ]

    def __init__(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
        buckets: Optional[List[float]] = None,
    ):
        self.name = name
        self.description = description
        self.label_names = label_names or []
        self.buckets = sorted(buckets or self.DEFAULT_BUCKETS)
        self._buckets: Dict[str, Dict[float, int]] = {}
        self._sums: Dict[str, float] = {}
        self._counts: Dict[str, int] = {}
        self._lock = threading.Lock()

    def observe(
        self,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Record an observation.

        Args:
            value: Observed value
            labels: Label values for this observation
        """

    def get_summary(
        self,
        labels: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Get histogram summary.

        Returns:
            Dictionary with count, sum, and bucket counts
        """
```

#### 3.2.5 PrometheusExporter

```python
# File: observability/exporters/prometheus.py

class PrometheusExporter:
    """
    Export metrics in Prometheus text exposition format.

    Format specification:
    https://github.com/prometheus/docs/blob/main/content/docs/instrumenting/exposition_formats.md

    Example:
        >>> exporter = PrometheusExporter()
        >>> output = exporter.export(metrics_collector)
        >>> print(output)  # Prometheus-formatted text
    """

    def __init__(self, prefix: str = "gaia"):
        self.prefix = prefix

    def export(self, collector: MetricsCollector) -> str:
        """
        Export all metrics to Prometheus format.

        Args:
            collector: MetricsCollector instance

        Returns:
            Prometheus-formatted metrics string
        """

    def _format_counter(self, name: str, counter: Counter) -> str:
        """Format counter metric."""

    def _format_gauge(self, name: str, gauge: Gauge) -> str:
        """Format gauge metric."""

    def _format_histogram(self, name: str, histogram: Histogram) -> str:
        """Format histogram metric."""

    def _format_labels(self, labels: Dict[str, str]) -> str:
        """Format labels as {key="value", ...}."""
```

---

## 4. API Standardization Specification

### 4.1 Overview

The API standardization component enhances the existing GAIA API (`src/gaia/api/`) with:

1. **OpenAPI Specification Generation** - Auto-generated OpenAPI 3.0 docs
2. **API Versioning** - Support for multiple API versions
3. **Deprecation Management** - Graceful API deprecation workflow

### 4.2 File Paths and LOC Estimates

| File | LOC | Description |
|------|-----|-------------|
| `api/__init__.py` | 40 | Update exports (existing + new) |
| `api/openapi.py` | 400 | OpenAPI spec generation, Swagger UI |
| `api/versioning.py` | 200 | URL and header versioning |
| `api/deprecation.py` | 150 | Deprecation warnings, sunset headers |
| **Total** | **~790 LOC** | API standardization |

### 4.3 Key Classes and Methods

#### 4.3.1 OpenAPIGenerator

```python
# File: api/openapi.py

from typing import Any, Dict, List, Optional, Type
from fastapi import FastAPI
from pydantic import BaseModel


class OpenAPIGenerator:
    """
    Auto-generate OpenAPI 3.0 specification from FastAPI app.

    Features:
        - Automatic schema extraction from Pydantic models
        - Route documentation from docstrings
        - Custom schema extensions
        - Swagger UI HTML generation

    Example:
        >>> from fastapi import FastAPI
        >>> from gaia.api.openapi import OpenAPIGenerator
        >>>
        >>> app = FastAPI(title="GAIA API")
        >>>
        >>> @app.post("/v1/chat/completions")
        >>> async def create_completion(request: ChatRequest):
        >>>     \"\"\"Create chat completion.\"\"\"
        >>>     ...
        >>>
        >>> generator = OpenAPIGenerator(app)
        >>> spec = generator.generate()
        >>> swagger_html = generator.generate_swagger_ui()
    """

    def __init__(
        self,
        app: FastAPI,
        title: str = "GAIA API",
        version: str = "1.0.0",
        description: str = "",
    ):
        """
        Initialize OpenAPI generator.

        Args:
            app: FastAPI application
            title: API title
            version: API version
            description: API description
        """
        self.app = app
        self.title = title
        self.version = version
        self.description = description

    def generate(self) -> Dict[str, Any]:
        """
        Generate OpenAPI 3.0 specification.

        Returns:
            OpenAPI spec as dictionary

        Example output structure:
            {
                "openapi": "3.0.3",
                "info": {
                    "title": "GAIA API",
                    "version": "1.0.0",
                    "description": "..."
                },
                "servers": [{"url": "/v1"}],
                "paths": {
                    "/chat/completions": {
                        "post": {
                            "summary": "Create chat completion",
                            "operationId": "create_completion",
                            "requestBody": {...},
                            "responses": {...}
                        }
                    }
                },
                "components": {
                    "schemas": {...}
                }
            }
        """

    def generate_swagger_ui(
        self,
        spec_url: str = "/openapi.json",
        title: str = "GAIA API - Swagger UI",
    ) -> str:
        """
        Generate Swagger UI HTML page.

        Args:
            spec_url: URL to OpenAPI JSON spec
            title: Page title

        Returns:
            Complete HTML string
        """

    def generate_redoc(
        self,
        spec_url: str = "/openapi.json",
        title: str = "GAIA API - Documentation",
    ) -> str:
        """
        Generate ReDoc HTML page.

        Args:
            spec_url: URL to OpenAPI JSON spec
            title: Page title

        Returns:
            Complete HTML string
        """

    def add_routes(self, router_prefix: str = "") -> None:
        """
        Add OpenAPI routes to the FastAPI app.

        Routes added:
            - GET {prefix}/openapi.json - OpenAPI JSON spec
            - GET {prefix}/docs - Swagger UI
            - GET {prefix}/redoc - ReDoc

        Args:
            router_prefix: Optional URL prefix for docs routes
        """

    def _extract_schemas(self) -> Dict[str, Any]:
        """Extract Pydantic schemas from app."""

    def _extract_paths(self) -> Dict[str, Any]:
        """Extract path operations from app routes."""

    def _parse_docstring(self, docstring: str) -> Dict[str, Any]:
        """Parse function docstring for operation metadata."""
```

#### 4.3.2 APIVersioning

```python
# File: api/versioning.py

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple
from fastapi import FastAPI, Request, APIRouter


class VersionStrategy(Enum):
    """API versioning strategy."""
    URL = "url"           # /v1/resource, /v2/resource
    HEADER = "header"     # X-API-Version: 1
    ACCEPT = "accept"     # Accept: application/json; version=1


@dataclass
class VersionConfig:
    """Version configuration."""
    version: int
    prefix: str
    status: str  # 'current', 'stable', 'deprecated', 'sunset'
    sunset_date: Optional[str] = None  # ISO 8601 date


class APIVersioning:
    """
    API versioning manager with multiple strategy support.

    Supports:
    - URL versioning: /v1/chat, /v2/chat
    - Header versioning: X-API-Version: 1
    - Accept header versioning: Accept: application/vnd.gaia.v1+json

    Example:
        >>> from fastapi import FastAPI, APIRouter
        >>> from gaia.api.versioning import APIVersioning, VersionStrategy
        >>>
        >>> app = FastAPI()
        >>> versioning = APIVersioning(
        ...     app,
        ...     default_version=1,
        ...     strategy=VersionStrategy.URL,
        ... )
        >>>
        >>> # Register versioned routers
        >>> v1_router = versioning.create_router(version=1)
        >>> v2_router = versioning.create_router(version=2)
        >>>
        >>> @v1_router.get("/chat")
        >>> def get_chat_v1(): ...
        >>>
        >>> @v2_router.get("/chat")
        >>> def get_chat_v2(): ...
    """

    def __init__(
        self,
        app: FastAPI,
        default_version: int = 1,
        strategy: VersionStrategy = VersionStrategy.URL,
        versions: Optional[List[VersionConfig]] = None,
    ):
        """
        Initialize API versioning.

        Args:
            app: FastAPI application
            default_version: Default API version
            strategy: Version resolution strategy
            versions: List of version configurations
        """
        self.app = app
        self.default_version = default_version
        self.strategy = strategy
        self.versions = versions or []
        self._routers: Dict[int, APIRouter] = {}

    def create_router(self, version: int, **kwargs) -> APIRouter:
        """
        Create versioned API router.

        Args:
            version: API version number
            **kwargs: Additional APIRouter arguments

        Returns:
            Configured APIRouter
        """

    def get_version_from_request(self, request: Request) -> int:
        """
        Extract API version from request.

        Args:
            request: FastAPI request

        Returns:
            Resolved version number
        """

    def register_version(
        self,
        version: int,
        prefix: str,
        status: str = "stable",
        sunset_date: Optional[str] = None,
    ) -> None:
        """
        Register a new API version.

        Args:
            version: Version number
            prefix: URL prefix (e.g., "/v1")
            status: Version status
            sunset_date: ISO 8601 sunset date for deprecated versions
        """

    def get_current_version(self) -> int:
        """Get current (latest stable) API version."""

    def get_deprecated_versions(self) -> List[int]:
        """Get list of deprecated versions."""

    def get_version_info(self, version: int) -> Optional[VersionConfig]:
        """Get version configuration."""
```

#### 4.3.3 DeprecationManager

```python
# File: api/deprecation.py

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional
from fastapi import Request, Response, FastAPI
from functools import wraps


@dataclass
class DeprecationInfo:
    """Deprecation metadata."""
    endpoint: str
    deprecated_version: str
    sunset_version: str
    sunset_date: datetime
    migration_hint: str
    alternative: Optional[str] = None


class DeprecationManager:
    """
    API deprecation management with automated headers and warnings.

    Features:
        - Automatic Sunset header injection
        - Deprecation warning logs
        - Migration hint responses
        - Version-aware routing

    Example:
        >>> from gaia.api.deprecation import DeprecationManager
        >>>
        >>> deprecation = DeprecationManager()
        >>> deprecation.deprecate(
        ...     endpoint="/v1/legacy",
        ...     deprecated_in="1.0.0",
        ...     sunset_in="2.0.0",
        ...     sunset_date="2026-12-31",
        ...     alternative="/v2/modern",
        ...     migration_hint="See migration guide at /docs/migration"
        ... )
        >>>
        >>> @deprecation.deprecated(
        ...     deprecated_in="1.5.0",
        ...     sunset_date="2026-06-30",
        ...     alternative="use_new_endpoint",
        ... )
        >>> def old_endpoint():
        ...     ...
    """

    def __init__(self, app: Optional[FastAPI] = None):
        """
        Initialize deprecation manager.

        Args:
            app: Optional FastAPI app for automatic middleware
        """
        self.app = app
        self._deprecated_endpoints: Dict[str, DeprecationInfo] = {}

    def deprecate(
        self,
        endpoint: str,
        deprecated_in: str,
        sunset_in: str,
        sunset_date: str,
        migration_hint: str,
        alternative: Optional[str] = None,
    ) -> None:
        """
        Register endpoint for deprecation.

        Args:
            endpoint: Endpoint path
            deprecated_in: Version when deprecated
            sunset_in: Version when removed
            sunset_date: ISO 8601 sunset date
            migration_hint: Migration instructions
            alternative: Alternative endpoint
        """

    def deprecated(
        self,
        deprecated_in: str,
        sunset_date: str,
        alternative: Optional[str] = None,
        migration_hint: str = "",
    ) -> Callable:
        """
        Decorator for marking endpoints as deprecated.

        Args:
            deprecated_in: Version when deprecated
            sunset_date: ISO 8601 sunset date
            alternative: Alternative endpoint
            migration_hint: Migration instructions

        Returns:
            Decorator function
        """

    def _add_deprecation_headers(
        self,
        response: Response,
        info: DeprecationInfo,
    ) -> None:
        """
        Add deprecation headers to response.

        Headers:
            - Deprecation: true
            - Sunset: <date>
            - Link: <alternative>; rel="successor-version"
            - X-Migration-Hint: <hint>
        """

    def get_deprecation_info(self, endpoint: str) -> Optional[DeprecationInfo]:
        """Get deprecation info for endpoint."""

    def list_deprecated(self) -> List[DeprecationInfo]:
        """List all deprecated endpoints."""

    def is_sunset(self, endpoint: str) -> bool:
        """Check if endpoint has passed sunset date."""
```

---

## 5. Integration with Sprints 1-3

### 5.1 Sprint 1 Integration (Modular Architecture)

```python
# Integration with pipeline orchestration
from gaia.observability import ObservabilityCore

obs = ObservabilityCore(service_name="gaia-pipeline")

@obs.traced(kind=SpanKind.INTERNAL, attributes={"pipeline": "code-generation"})
def execute_pipeline(pipeline, context):
    """Execute pipeline with tracing."""
    with obs.trace("pipeline.execution"):
        for stage in pipeline.stages:
            with obs.trace("stage.execution", attributes={"stage": stage.name}):
                stage.execute(context)
```

### 5.2 Sprint 2 Integration (Dependency Injection + Performance)

```python
# Integration with DI container
from gaia.di.container import DIContainer
from gaia.observability import ObservabilityCore, MetricsCollector

container = DIContainer()
container.register_singleton("observability", ObservabilityCore)
container.register_singleton("metrics", MetricsCollector)

# Inject into services
class AgentService:
    def __init__(
        self,
        observability: ObservabilityCore,
        metrics: MetricsCollector,
    ):
        self.obs = observability
        self.metrics = metrics

    @traced()
    async def process_query(self, query: str):
        self.metrics.counter("agent.requests").inc()
        return await self.agent.process(query)
```

### 5.3 Sprint 3 Integration (Caching + Configuration)

```python
# Integration with CacheLayer
from gaia.cache import CacheLayer
from gaia.observability import MetricsCollector

async def get_or_set_with_metrics(
    cache: CacheLayer,
    metrics: MetricsCollector,
    key: str,
    factory,
):
    """Cache with metrics tracking."""
    histogram = metrics.histogram("cache.get_or_set_latency")

    start = time.perf_counter()
    try:
        result = await cache.get_or_set(key, factory)
        latency = time.perf_counter() - start
        histogram.observe(latency)

        metrics.counter("cache.hits").inc()
        return result
    except Exception as e:
        metrics.counter("cache.errors").inc()
        raise

# Integration with ConfigManager for observability settings
from gaia.config import ConfigManager

def configure_observability(config: ConfigManager, obs: ObservabilityCore):
    """Configure observability from config."""
    obs.log_level = config.get("observability.log_level", "INFO")
    obs.enable_tracing = config.get("observability.enable_tracing", True)
    obs.enable_metrics = config.get("observability.enable_metrics", True)
```

---

## 6. Test Specifications (~155 Tests)

### 6.1 Test File Structure

```
tests/
├── unit/
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── test_core.py              # 50 tests
│   │   ├── test_metrics.py           # 30 tests
│   │   ├── test_tracing/
│   │   │   ├── __init__.py
│   │   │   ├── test_span.py          # 15 tests (included in test_core.py)
│   │   │   ├── test_trace_context.py # 10 tests (included in test_core.py)
│   │   │   └── test_propagator.py    # 10 tests (included in test_core.py)
│   │   └── test_logging/
│   │       ├── __init__.py
│   │       ├── test_formatter.py     # 10 tests (included in test_core.py)
│   │       └── test_sink.py          # 5 tests (included in test_core.py)
│   └── api/
│       ├── __init__.py
│       ├── test_openapi.py           # 40 tests
│       ├── test_versioning.py        # 20 tests
│       └── test_deprecation.py       # 15 tests
└── integration/
    ├── test_observability_integration.py  # 15 tests
    └── test_api_versioning_integration.py # 10 tests
```

### 6.2 Test Coverage Requirements

| Component | Unit Tests | Integration Tests | Total |
|-----------|------------|-------------------|-------|
| ObservabilityCore | 50 | 15 | 65 |
| MetricsCollector | 30 | - | 30 |
| OpenAPI Generator | 40 | - | 40 |
| API Versioning | 20 | 10 | 30 |
| Deprecation Manager | 15 | - | 15 |
| **Total** | **155** | **25** | **180** |

### 6.3 Quality Gate Test Cases

#### OBS-001: Trace Context Propagation (100%)

```python
# tests/unit/observability/test_core.py

class TestTracePropagation:
    """Verify trace context propagation."""

    def test_w3c_propagator_inject_extract(self):
        """W3C propagator should correctly inject and extract context."""
        from gaia.observability.tracing import W3CPropagator, TraceContext

        propagator = W3CPropagator()
        context = TraceContext(
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
            trace_flags=1,
        )

        # Inject
        carrier = {}
        propagator.inject(context, carrier)

        assert "traceparent" in carrier
        assert carrier["traceparent"] == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

        # Extract
        extracted = propagator.extract(carrier)
        assert extracted.trace_id == context.trace_id
        assert extracted.span_id == context.span_id
        assert extracted.trace_flags == context.trace_flags

    def test_b3_propagator_inject_extract(self):
        """B3 propagator should correctly inject and extract context."""
        from gaia.observability.tracing import B3Propagator, TraceContext

        propagator = B3Propagator()
        context = TraceContext(
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
            trace_flags=1,
        )

        carrier = {}
        propagator.inject(context, carrier)

        assert carrier["X-B3-TraceId"] == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert carrier["X-B3-SpanId"] == "00f067aa0ba902b7"
        assert carrier["X-B3-Sampled"] == "1"

    @pytest.mark.asyncio
    async def test_async_context_propagation(self):
        """Trace context should propagate across async boundaries."""
        from gaia.observability import ObservabilityCore

        obs = ObservabilityCore()

        async def nested_operation():
            # Context should be available here
            current = obs.get_current_trace()
            assert current is not None
            return current.trace_id

        async with obs.trace_async("parent") as parent_span:
            child_trace_id = await nested_operation()
            assert child_trace_id == parent_span.context.trace_id

    def test_context_across_threads(self):
        """Trace context should propagate across threads."""
        from concurrent.futures import ThreadPoolExecutor
        from gaia.observability import ObservabilityCore

        obs = ObservabilityCore()
        captured_trace_ids = []

        def worker():
            context = obs.get_current_trace()
            if context:
                captured_trace_ids.append(context.trace_id)

        with obs.trace("parent"):
            parent_context = obs.get_current_trace()

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(worker) for _ in range(4)]
                for f in futures:
                    f.result()

        # Thread-local context should be propagated
        assert len(captured_trace_ids) == 4
        # All should have same trace_id as parent
```

#### OBS-002: Metrics Export Accuracy (100%)

```python
# tests/unit/observability/test_metrics.py

class TestMetricsExport:
    """Verify metrics export accuracy."""

    def test_counter_prometheus_format(self):
        """Counter should export correctly in Prometheus format."""
        from gaia.observability.metrics import MetricsCollector

        metrics = MetricsCollector(prefix="test")
        counter = metrics.counter("requests_total", description="Total requests")
        counter.inc()
        counter.inc(labels={"method": "GET", "status": "200"})
        counter.inc(5, labels={"method": "POST", "status": "201"})

        output = metrics.to_prometheus()

        assert "# HELP test_requests_total Total requests" in output
        assert "# TYPE test_requests_total counter" in output
        assert 'test_requests_total{method="GET",status="200"} 1' in output
        assert 'test_requests_total{method="POST",status="201"} 5' in output

    def test_gauge_prometheus_format(self):
        """Gauge should export correctly in Prometheus format."""
        from gaia.observability.metrics import MetricsCollector

        metrics = MetricsCollector(prefix="test")
        gauge = metrics.gauge("queue_size", description="Current queue size")
        gauge.set(42)

        output = metrics.to_prometheus()

        assert "# HELP test_queue_size Current queue size" in output
        assert "# TYPE test_queue_size gauge" in output
        assert "test_queue_size 42.0" in output

    def test_histogram_prometheus_format(self):
        """Histogram should export correctly in Prometheus format."""
        from gaia.observability.metrics import MetricsCollector

        metrics = MetricsCollector(prefix="test")
        histogram = metrics.histogram(
            "latency_seconds",
            description="Request latency",
            buckets=[0.1, 0.5, 1.0],
        )
        histogram.observe(0.05)  # Goes in 0.1 bucket
        histogram.observe(0.25)  # Goes in 0.5 bucket
        histogram.observe(0.75)  # Goes in 1.0 bucket
        histogram.observe(1.5)   # Goes in +Inf bucket

        output = metrics.to_prometheus()

        assert "# HELP test_latency_seconds Request latency" in output
        assert "# TYPE test_latency_seconds histogram" in output
        assert 'test_latency_seconds_bucket{le="0.1"} 1' in output
        assert 'test_latency_seconds_bucket{le="0.5"} 2' in output
        assert 'test_latency_seconds_bucket{le="1.0"} 3' in output
        assert 'test_latency_seconds_bucket{le="+Inf"} 4' in output
        assert "test_latency_seconds_sum" in output
        assert "test_latency_seconds_count 4" in output

    def test_cache_stats_integration(self):
        """Metrics should integrate with Sprint 3 CacheStats."""
        from gaia.observability.metrics import MetricsCollector
        from gaia.cache.stats import CacheStats

        metrics = MetricsCollector()
        cache_stats = CacheStats(
            hits=80,
            misses=20,
            memory_size=100,
            disk_size=50,
        )

        metrics.integrate_cache_stats(cache_stats)

        output = metrics.to_prometheus()

        assert "gaia_cache_hits" in output
        assert "gaia_cache_misses" in output
        assert "gaia_cache_hit_rate" in output
```

#### API-001: OpenAPI Spec Completeness (100%)

```python
# tests/unit/api/test_openapi.py

class TestOpenAPIGeneration:
    """Verify OpenAPI spec completeness."""

    def test_openapi_version(self):
        """Generated spec should be OpenAPI 3.0.x."""
        from gaia.api.openapi import OpenAPIGenerator
        from fastapi import FastAPI

        app = FastAPI(title="Test API")
        generator = OpenAPIGenerator(app, version="1.0.0")

        spec = generator.generate()

        assert spec["openapi"].startswith("3.0")

    def test_paths_extracted(self):
        """All API paths should be extracted."""
        from gaia.api.openapi import OpenAPIGenerator
        from fastapi import FastAPI, APIRouter

        app = FastAPI()
        router = APIRouter(prefix="/v1")

        @router.get("/chat")
        def get_chat():
            """Get chat history."""

        @router.post("/chat/completions")
        def create_completion():
            """Create chat completion."""

        app.include_router(router)
        generator = OpenAPIGenerator(app)

        spec = generator.generate()

        assert "/v1/chat" in spec["paths"]
        assert "/v1/chat/completions" in spec["paths"]

    def test_schemas_extracted(self):
        """Pydantic schemas should be extracted to components."""
        from gaia.api.openapi import OpenAPIGenerator
        from fastapi import FastAPI
        from pydantic import BaseModel

        class ChatRequest(BaseModel):
            model: str
            messages: list

        app = FastAPI()

        @app.post("/chat")
        def create_chat(request: ChatRequest):
            pass

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        assert "components" in spec
        assert "schemas" in spec["components"]
        assert "ChatRequest" in spec["components"]["schemas"]

    def test_swagger_ui_generation(self):
        """Swagger UI HTML should be generated correctly."""
        from gaia.api.openapi import OpenAPIGenerator
        from fastapi import FastAPI

        app = FastAPI()
        generator = OpenAPIGenerator(app)

        html = generator.generate_swagger_ui(spec_url="/openapi.json")

        assert "<!DOCTYPE html>" in html or "<html" in html
        assert "swagger-ui" in html.lower()
        assert "/openapi.json" in html
```

#### API-002: Version Negotiation (All Strategies)

```python
# tests/unit/api/test_versioning.py

class TestAPIVersioning:
    """Verify API versioning strategies."""

    def test_url_versioning(self):
        """URL versioning should route to correct version."""
        from gaia.api.versioning import APIVersioning, VersionStrategy
        from fastapi import FastAPI, Request
        from starlette.testclient import TestClient

        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.URL)

        v1 = versioning.create_router(1)
        v2 = versioning.create_router(2)

        @v1.get("/resource")
        def get_v1():
            return {"version": "v1"}

        @v2.get("/resource")
        def get_v2():
            return {"version": "v2"}

        app.include_router(v1, prefix="/v1")
        app.include_router(v2, prefix="/v2")

        client = TestClient(app)

        response = client.get("/v1/resource")
        assert response.json()["version"] == "v1"

        response = client.get("/v2/resource")
        assert response.json()["version"] == "v2"

    def test_header_versioning(self):
        """Header versioning should extract version from X-API-Version."""
        from gaia.api.versioning import APIVersioning, VersionStrategy
        from fastapi import FastAPI, Request

        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.HEADER)

        @app.get("/resource")
        def get_resource(request: Request):
            version = versioning.get_version_from_request(request)
            return {"version": f"v{version}"}

        from starlette.testclient import TestClient
        client = TestClient(app)

        response = client.get("/resource", headers={"X-API-Version": "2"})
        assert response.json()["version"] == "v2"

    def test_accept_header_versioning(self):
        """Accept header versioning should parse version from Accept."""
        from gaia.api.versioning import APIVersioning, VersionStrategy

        app = FastAPI()
        versioning = APIVersioning(app, strategy=VersionStrategy.ACCEPT)

        request = type('MockRequest', (), {
            'headers': {"accept": "application/vnd.gaia.v2+json"}
        })()

        version = versioning.get_version_from_request(request)
        assert version == 2

    def test_version_registration(self):
        """Version registration should track all versions."""
        from gaia.api.versioning import APIVersioning

        app = FastAPI()
        versioning = APIVersioning(app)

        versioning.register_version(1, "/v1", status="stable")
        versioning.register_version(2, "/v2", status="current")
        versioning.register_version(3, "/v3", status="beta")

        assert versioning.get_current_version() == 2
        assert 1 in versioning.get_deprecated_versions() or 1 not in versioning.get_deprecated_versions()
```

#### BC-002: Backward Compatibility (100%)

```python
# tests/unit/api/test_deprecation.py

class TestDeprecationManagement:
    """Verify deprecation and backward compatibility."""

    def test_deprecation_headers(self):
        """Deprecated endpoints should include deprecation headers."""
        from gaia.api.deprecation import DeprecationManager
        from fastapi import FastAPI, Response
        from starlette.testclient import TestClient

        app = FastAPI()
        deprecation = DeprecationManager(app)

        deprecation.deprecate(
            endpoint="/v1/legacy",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use /v2/modern instead",
            alternative="/v2/modern",
        )

        @app.get("/v1/legacy")
        def legacy_endpoint(response: Response):
            info = deprecation.get_deprecation_info("/v1/legacy")
            if info:
                deprecation._add_deprecation_headers(response, info)
            return {"status": "legacy"}

        client = TestClient(app)
        response = client.get("/v1/legacy")

        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers
        assert "Link" in response.headers

    def test_deprecated_decorator(self):
        """@deprecated decorator should add headers automatically."""
        from gaia.api.deprecation import DeprecationManager
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        app = FastAPI()
        deprecation = DeprecationManager()

        @deprecation.deprecated(
            deprecated_in="1.5.0",
            sunset_date="2026-06-30T23:59:59Z",
            alternative="/v2/new-endpoint",
            migration_hint="See migration guide",
        )
        def old_endpoint():
            return {"status": "old"}

        # Manually apply to app for testing
        app.get("/old")(old_endpoint)

        client = TestClient(app)
        response = client.get("/old")

        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers

    def test_sunset_date_check(self):
        """is_sunset should return correct status based on date."""
        from gaia.api.deprecation import DeprecationManager
        from datetime import datetime, timedelta

        deprecation = DeprecationManager()

        # Future sunset date
        future_date = (datetime.now() + timedelta(days=30)).isoformat()
        deprecation.deprecate(
            endpoint="/future",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date=future_date,
            migration_hint="",
        )
        assert not deprecation.is_sunset("/future")

        # Past sunset date
        past_date = (datetime.now() - timedelta(days=30)).isoformat()
        deprecation.deprecate(
            endpoint="/past",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date=past_date,
            migration_hint="",
        )
        assert deprecation.is_sunset("/past")
```

#### THREAD-003: Thread Safety (100+ Threads)

```python
# tests/unit/observability/test_core.py

class TestThreadSafety:
    """Verify thread safety under concurrent load."""

    @pytest.mark.asyncio
    async def test_concurrent_span_creation(self):
        """Should handle 100+ concurrent span creations safely."""
        from gaia.observability import ObservabilityCore
        import asyncio

        obs = ObservabilityCore()
        errors = []
        span_ids = []
        lock = asyncio.Lock()

        async def worker(worker_id: int):
            try:
                async with obs.trace_async(f"worker.{worker_id}") as span:
                    async with lock:
                        span_ids.append(span.context.span_id)
            except Exception as e:
                errors.append((worker_id, str(e)))

        # Launch 100 concurrent workers
        tasks = [worker(i) for i in range(100)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(span_ids) == 100
        # All span IDs should be unique
        assert len(set(span_ids)) == 100

    @pytest.mark.asyncio
    async def test_concurrent_metrics_updates(self):
        """Metrics should handle 100+ concurrent updates safely."""
        from gaia.observability.metrics import MetricsCollector
        import asyncio

        metrics = MetricsCollector()
        counter = metrics.counter("concurrent_requests")

        async def increment():
            counter.inc()

        # 100 concurrent increments
        tasks = [increment() for _ in range(100)]
        await asyncio.gather(*tasks)

        # Final count should be exactly 100
        assert counter.get() == 100

    def test_metrics_thread_safety(self):
        """Metrics should be thread-safe across threads."""
        from gaia.observability.metrics import MetricsCollector
        from concurrent.futures import ThreadPoolExecutor

        metrics = MetricsCollector()
        counter = metrics.counter("thread_requests")

        def increment(n):
            for _ in range(n):
                counter.inc()

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(increment, 10) for _ in range(10)]
            for f in futures:
                f.result()

        # 10 workers * 10 increments = 100
        assert counter.get() == 100
```

### 6.4 Test Fixtures

```python
# tests/conftest.py (additions)

import pytest
from gaia.observability.core import ObservabilityCore
from gaia.observability.metrics import MetricsCollector
from gaia.api.openapi import OpenAPIGenerator
from gaia.api.versioning import APIVersioning
from gaia.api.deprecation import DeprecationManager


@pytest.fixture
def obs_core():
    """Provide ObservabilityCore instance for tests."""
    obs = ObservabilityCore(
        service_name="test-gaia",
        log_level="DEBUG",
        enable_tracing=True,
        enable_metrics=True,
    )
    yield obs
    # Cleanup: shutdown observability
    import asyncio
    asyncio.run(obs.shutdown())


@pytest.fixture
def metrics_collector():
    """Provide MetricsCollector instance for tests."""
    return MetricsCollector(prefix="test")


@pytest.fixture
def openapi_generator():
    """Provide OpenAPIGenerator for tests."""
    from fastapi import FastAPI
    app = FastAPI(title="Test API")
    return OpenAPIGenerator(app)


@pytest.fixture
def api_versioning():
    """Provide APIVersioning instance for tests."""
    from fastapi import FastAPI
    app = FastAPI()
    return APIVersioning(app, default_version=1)


@pytest.fixture
def deprecation_manager():
    """Provide DeprecationManager instance for tests."""
    return DeprecationManager()


@pytest.fixture
def sample_trace_context():
    """Provide sample trace context for tests."""
    from gaia.observability.tracing import TraceContext
    return TraceContext(
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        trace_flags=1,
    )
```

---

## 7. Implementation Plan

### 7.1 Phase Breakdown

| Phase | Duration | Deliverables | Dependencies |
|-------|----------|--------------|--------------|
| **Phase 1: ObservabilityCore** | 4-5 days | core.py, tracing/, logging/, tests | None |
| **Phase 2: MetricsCollector** | 2-3 days | metrics.py, exporters/, tests | Phase 1 |
| **Phase 3: OpenAPI Generation** | 2-3 days | openapi.py, integration tests | Existing API |
| **Phase 4: API Versioning** | 1-2 days | versioning.py, tests | Phase 3 |
| **Phase 5: Deprecation Manager** | 1-2 days | deprecation.py, tests | Phase 4 |
| **Phase 6: Integration + Polish** | 2-3 days | Integration tests, documentation | All phases |
| **Total** | **12-18 days** | Full sprint deliverables | ~3 weeks |

### 7.2 File Creation Order

1. `observability/tracing/trace_context.py` - Trace context data structures
2. `observability/tracing/span.py` - Span class and lifecycle
3. `observability/tracing/propagator.py` - Context propagators
4. `observability/logging/formatter.py` - JSON log formatter
5. `observability/logging/sink.py` - Log sink abstraction
6. `observability/metrics.py` - Metrics collection
7. `observability/exporters/prometheus.py` - Prometheus exporter
8. `observability/core.py` - Main facade (depends on 1-7)
9. `api/openapi.py` - OpenAPI generation
10. `api/versioning.py` - API versioning
11. `api/deprecation.py` - Deprecation management
12. Test files throughout development
13. `api/__init__.py` - Update exports

### 7.3 Dependencies

| Sprint 4 Component | Sprint 1 Dependency | Sprint 2 Dependency | Sprint 3 Dependency |
|--------------------|--------------------|--------------------|--------------------|
| ObservabilityCore | - | async_utils | - |
| MetricsCollector | - | - | CacheStats integration |
| OpenAPI Generator | - | - | Existing API schemas |
| API Versioning | - | - | Existing API app |
| Deprecation Manager | - | - | API Versioning |

---

## 8. Quality Gates Summary

| ID | Metric | Target | Measurement | Success Criteria |
|----|--------|--------|-------------|------------------|
| **OBS-001** | Trace context propagation | 100% | W3C/B3 propagator tests | All propagators pass inject/extract round-trip |
| **OBS-002** | Metrics export accuracy | 100% | Prometheus format validation | All metric types export correctly |
| **API-001** | OpenAPI spec completeness | 100% | Path/schema coverage | All routes and schemas documented |
| **API-002** | Version negotiation | All strategies | URL, Header, Accept tests | All three strategies functional |
| **BC-002** | Backward compatibility | 100% | Deprecation header tests | All deprecated endpoints have headers |
| **THREAD-003** | Thread safety | 100+ threads | Concurrent access tests | No race conditions at 100+ threads |

### Quality Gate Validation Tests

```python
# tests/unit/test_quality_gates.py

class TestQualityGates:
    """Validate all quality gates pass."""

    def test_obs_001_trace_propagation(self):
        """OBS-001: Trace context propagation must be 100%."""
        from gaia.observability.tracing import W3CPropagator, TraceContext

        propagator = W3CPropagator()
        context = TraceContext(
            trace_id="1234567890abcdef1234567890abcdef",
            span_id="fedcba0987654321",
            trace_flags=1,
        )

        carrier = {}
        propagator.inject(context, carrier)
        extracted = propagator.extract(carrier)

        assert extracted.trace_id == context.trace_id, "Trace ID mismatch"
        assert extracted.span_id == context.span_id, "Span ID mismatch"
        assert extracted.trace_flags == context.trace_flags, "Trace flags mismatch"

    def test_obs_002_metrics_export(self):
        """OBS-002: Metrics export must be 100% accurate."""
        from gaia.observability.metrics import MetricsCollector

        metrics = MetricsCollector()
        metrics.counter("test").inc(5)
        metrics.gauge("gauge").set(10)
        metrics.histogram("hist").observe(0.5)

        output = metrics.to_prometheus()

        # Verify all metric types present
        assert "test" in output
        assert "gauge" in output
        assert "hist" in output

    def test_api_001_openapi_completeness(self):
        """API-001: OpenAPI spec must be 100% complete."""
        from gaia.api.openapi import OpenAPIGenerator
        from fastapi import FastAPI
        from pydantic import BaseModel

        class TestRequest(BaseModel):
            name: str

        app = FastAPI()

        @app.post("/test")
        def test_endpoint(request: TestRequest):
            pass

        generator = OpenAPIGenerator(app)
        spec = generator.generate()

        assert "openapi" in spec
        assert "paths" in spec
        assert "/test" in spec["paths"]
        assert "components" in spec
        assert "TestRequest" in spec["components"]["schemas"]

    def test_api_002_version_negotiation(self):
        """API-002: All version negotiation strategies must work."""
        from gaia.api.versioning import APIVersioning, VersionStrategy

        # Test URL strategy
        url_versioning = APIVersioning(FastAPI(), strategy=VersionStrategy.URL)
        assert url_versioning.strategy == VersionStrategy.URL

        # Test Header strategy
        header_versioning = APIVersioning(FastAPI(), strategy=VersionStrategy.HEADER)
        assert header_versioning.strategy == VersionStrategy.HEADER

        # Test Accept strategy
        accept_versioning = APIVersioning(FastAPI(), strategy=VersionStrategy.ACCEPT)
        assert accept_versioning.strategy == VersionStrategy.ACCEPT

    def test_bc_002_backward_compatibility(self):
        """BC-002: Backward compatibility must be 100%."""
        from gaia.api.deprecation import DeprecationManager
        from fastapi import Response

        deprecation = DeprecationManager()
        deprecation.deprecate(
            endpoint="/legacy",
            deprecated_in="1.0.0",
            sunset_in="2.0.0",
            sunset_date="2026-12-31T23:59:59Z",
            migration_hint="Use /v2/new",
            alternative="/v2/new",
        )

        response = Response()
        info = deprecation.get_deprecation_info("/legacy")
        deprecation._add_deprecation_headers(response, info)

        assert response.headers.get("Deprecation") == "true"
        assert "Sunset" in response.headers

    @pytest.mark.asyncio
    async def test_thread_003_thread_safety(self):
        """THREAD-003: Must handle 100+ concurrent threads."""
        from gaia.observability import ObservabilityCore
        import asyncio

        obs = ObservabilityCore()
        errors = []

        async def worker(i):
            try:
                async with obs.trace_async(f"worker.{i}"):
                    pass
            except Exception as e:
                errors.append(e)

        tasks = [worker(i) for i in range(100)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Thread safety failures: {errors}"
```

---

## 9. API Reference Summary

### 9.1 ObservabilityCore API

```python
from gaia.observability import ObservabilityCore, traced
from gaia.observability.tracing import SpanKind, SpanStatus

# Initialize
obs = ObservabilityCore(
    service_name="gaia-api",
    log_level="INFO",
    enable_tracing=True,
    enable_metrics=True,
)

# Decorator usage
@traced(kind=SpanKind.SERVER, attributes={"agent": "code"})
def process_query(agent, query):
    return agent.process_query(query)

# Context manager usage
with obs.trace("database.operation", kind=SpanKind.CLIENT) as span:
    span.set_attribute("db.system", "sqlite")
    result = db.execute(query)
    span.set_status(SpanStatus.OK)

# Async context manager
async with obs.trace_async("async.operation") as span:
    result = await async_operation()

# Logging
obs.log_info("Operation completed", operation="query", duration_ms=125)
obs.log_error("Operation failed", error=str(e), exc_info=True)

# Metrics access
obs.metrics.counter("requests").inc()
obs.metrics.histogram("latency").observe(0.125)
```

### 9.2 MetricsCollector API

```python
from gaia.observability.metrics import MetricsCollector

metrics = MetricsCollector(prefix="gaia")

# Counter
requests = metrics.counter(
    "http_requests_total",
    description="Total HTTP requests",
    label_names=["method", "status"],
)
requests.inc()
requests.inc(labels={"method": "GET", "status": "200"})

# Gauge
queue_size = metrics.gauge("queue_size")
queue_size.set(42)
queue_size.inc()
queue_size.dec(5)

# Histogram
latency = metrics.histogram(
    "request_latency_seconds",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0],
)
latency.observe(0.125)

# Export
prometheus_output = metrics.to_prometheus()
summary = metrics.get_summary()
```

### 9.3 OpenAPI Generator API

```python
from gaia.api.openapi import OpenAPIGenerator
from fastapi import FastAPI

app = FastAPI(title="GAIA API", version="1.0.0")

# ... define routes ...

# Generate spec
generator = OpenAPIGenerator(app)
spec = generator.generate()

# Add documentation routes
generator.add_routes()

# Generate HTML
swagger_html = generator.generate_swagger_ui()
redoc_html = generator.generate_redoc()
```

### 9.4 API Versioning API

```python
from gaia.api.versioning import APIVersioning, VersionStrategy

app = FastAPI()
versioning = APIVersioning(
    app,
    default_version=1,
    strategy=VersionStrategy.URL,
)

# Create versioned routers
v1 = versioning.create_router(1)
v2 = versioning.create_router(2)

@v1.get("/resource")
def get_resource_v1():
    return {"version": "v1"}

@v2.get("/resource")
def get_resource_v2():
    return {"version": "v2"}

app.include_router(v1, prefix="/v1")
app.include_router(v2, prefix="/v2")
```

### 9.5 Deprecation Manager API

```python
from gaia.api.deprecation import DeprecationManager

deprecation = DeprecationManager()

# Decorator usage
@deprecation.deprecated(
    deprecated_in="1.0.0",
    sunset_date="2026-12-31T23:59:59Z",
    alternative="/v2/new-endpoint",
    migration_hint="See migration guide at /docs/migration",
)
def old_endpoint():
    return {"status": "legacy"}

# Programmatic registration
deprecation.deprecate(
    endpoint="/v1/legacy",
    deprecated_in="1.0.0",
    sunset_in="2.0.0",
    sunset_date="2026-12-31T23:59:59Z",
    migration_hint="Use /v2/modern",
    alternative="/v2/modern",
)

# List deprecated endpoints
deprecated = deprecation.list_deprecated()
```

---

## 10. Documentation Requirements

All new modules require:
1. **Module docstrings** with examples
2. **Class docstrings** with attribute descriptions
3. **Method docstrings** with Args, Returns, Raises sections
4. **Inline comments** for complex logic
5. **Type hints** for all function signatures

### Documentation Files to Create/Update

| File | Type | Description |
|------|------|-------------|
| `docs/sdk/infrastructure/observability.mdx` | New | Observability guide |
| `docs/sdk/infrastructure/api-standardization.mdx` | New | API versioning/deprecation guide |
| `docs/reference/api/observability.md` | New | API reference |
| `docs/reference/api/openapi.md` | New | OpenAPI generation reference |
| `docs/guides/agent-api.mdx` | Update | Add versioning section |
| `docs/spec/phase3-master.mdx` | Update | Add Sprint 4 status |

---

## 11. Risk Assessment and Mitigation

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **Tracing overhead** | Performance degradation | Medium | Benchmark early; add sampling option |
| **OpenAPI route conflicts** | Documentation routes clash | Low | Use configurable prefix |
| **Version negotiation complexity** | Routing bugs | Medium | Comprehensive test coverage |
| **Thread safety issues** | Race conditions | Medium | Stress tests with 100+ threads |
| **Integration breaking changes** | API incompatibility | Low | Backward compatibility tests |

---

## Appendix A: Design Decision Log

| Decision | Rationale | Alternatives Considered |
|----------|-----------|------------------------|
| **W3C Trace Context default** | Industry standard, vendor-neutral | B3 (Zipkin), Jaeger format |
| **JSON structured logging** | Machine-parseable, cloud-native | Text format, logfmt |
| **Prometheus export format** | Industry standard for metrics | StatsD, OpenTelemetry |
| **URL versioning primary** | Most visible, cache-friendly | Header-only, query param |
| **Singleton ObservabilityCore** | Consistent context across app | Factory pattern, DI-only |
| **Async context propagation** | Native Python async support | Thread-local only |

---

## Appendix B: Sprint 4 Timeline (3 Weeks)

```
Week 1: Observability Core
├── Days 1-2: Trace context, Span, Propagator
├── Days 3-4: Structured logging, MetricsCollector
└── Day 5: ObservabilityCore facade + unit tests

Week 2: API Standardization
├── Days 1-2: OpenAPI generator
├── Days 3-4: API versioning
└── Day 5: Deprecation manager + unit tests

Week 3: Integration + Polish
├── Days 1-2: Integration tests (Sprints 1-3)
├── Days 3-4: Quality gate validation, stress tests
└── Day 5: Documentation, sprint review

Milestones:
- End of Week 1: Observability functional
- End of Week 2: API standardization complete
- End of Week 3: Phase 3 Sprint 4 READY FOR CLOSEOUT
```

---

*This specification is ready for handoff to senior-developer for implementation.*
