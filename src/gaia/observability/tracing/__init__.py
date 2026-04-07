"""
Distributed tracing module for GAIA observability.

This module provides distributed tracing capabilities including:
- Span creation and lifecycle management
- Trace context propagation (W3C, B3 formats)
- Context managers for automatic span handling

Example:
    >>> from gaia.observability.tracing import Span, SpanKind, SpanStatus
    >>> from gaia.observability.tracing import W3CPropagator, TraceContext
    >>>
    >>> # Create and manage spans
    >>> with Span("operation", kind=SpanKind.SERVER) as span:
    ...     span.set_attribute("key", "value")
    ...     result = perform_operation()
    ...     span.set_status(SpanStatus.OK)
"""

from .trace_context import (
    TraceContext,
    TraceContextManager,
    SpanContext,
    get_context_manager,
    get_current_trace_context,
    set_current_trace_context,
)
from .span import (
    Span,
    SpanKind,
    SpanStatus,
    Event,
)
from .propagator import (
    TracePropagator,
    W3CPropagator,
    B3Propagator,
    CompositePropagator,
    get_default_propagator,
)

__all__ = [
    # Trace context
    "TraceContext",
    "TraceContextManager",
    "SpanContext",
    "get_context_manager",
    "get_current_trace_context",
    "set_current_trace_context",
    # Span
    "Span",
    "SpanKind",
    "SpanStatus",
    "Event",
    # Propagators
    "TracePropagator",
    "W3CPropagator",
    "B3Propagator",
    "CompositePropagator",
    "get_default_propagator",
]
