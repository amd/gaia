"""
Observability core module for GAIA.

This module provides the central ObservabilityCore class which serves
as a unified facade for:
- Distributed tracing with context propagation
- Structured logging with context enrichment
- Metrics collection and export

Example:
    >>> from gaia.observability.core import ObservabilityCore, traced
    >>> from gaia.observability.tracing import SpanKind, SpanStatus
    >>>
    >>> obs = ObservabilityCore(
    ...     service_name="gaia-api",
    ...     log_level="INFO",
    ...     enable_tracing=True,
    ... )
    >>>
    >>> # Use context manager for spans
    >>> with obs.trace("agent_execution", kind=SpanKind.SERVER) as span:
    ...     span.set_attribute("agent", "code-agent")
    ...     obs.log_info("Agent started")
    ...     result = agent.process_query(query)
    >>>
    >>> # Use decorator for automatic tracing
    >>> @traced(kind=SpanKind.CLIENT)
    ... def database_query(sql: str):
    ...     return db.execute(sql)
"""

from contextlib import asynccontextmanager, contextmanager
from typing import Any, Callable, Dict, List, Optional, TypeVar
import asyncio
import functools
import logging
import threading

from .tracing import (
    Span,
    SpanKind,
    SpanStatus,
    TraceContext,
    TraceContextManager,
    W3CPropagator,
    B3Propagator,
    TracePropagator,
    get_default_propagator,
    get_current_trace_context,
    set_current_trace_context,
)
from .metrics import MetricsCollector, get_metrics_collector
from .logging import JSONFormatter, ConsoleSink, setup_logging, ContextFilter


F = TypeVar("F", bound=Callable)


# Global observability instance
_default_observability: Optional["ObservabilityCore"] = None
_observability_lock = threading.Lock()


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
        - Integration with CacheLayer for metrics
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
        log_format: str = "json",
    ) -> None:
        """
        Initialize ObservabilityCore.

        Args:
            service_name: Service name for traces and logs
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
            enable_tracing: Enable distributed tracing
            enable_metrics: Enable metrics collection
            propagator: Context propagator (default: W3CPropagator)
            log_format: Log output format ('json' or 'text')

        Example:
            >>> obs = ObservabilityCore(
            ...     service_name="my-service",
            ...     log_level="DEBUG",
            ...     enable_tracing=True,
            ...     enable_metrics=True,
            ...     log_format="json"
            ... )
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
        self._context_manager = TraceContextManager()
        self._lock = threading.RLock()  # Use RLock for thread safety

        self._initialize()

    def _initialize(self) -> None:
        """Initialize logging, tracing, and metrics subsystems."""
        # Initialize logging
        self._logger = setup_logging(
            service_name=self.service_name,
            level=self.log_level,
            log_format=self.log_format,
            output="console",
        )
        self._logger.addFilter(ContextFilter())

        # Initialize metrics
        if self.enable_metrics:
            self._metrics = MetricsCollector(prefix=self.service_name.replace("-", "_"))

        self._log_debug("ObservabilityCore initialized", service=self.service_name)

    @property
    def metrics(self) -> MetricsCollector:
        """
        Get metrics collector instance.

        Returns:
            MetricsCollector instance

        Example:
            >>> obs = ObservabilityCore(enable_metrics=True)
            >>> obs.metrics.counter("requests").inc()
        """
        if self._metrics is None:
            raise RuntimeError("Metrics collection is disabled")
        return self._metrics

    @property
    def logger(self) -> logging.Logger:
        """
        Get structured logger instance.

        Returns:
            Configured logging.Logger instance

        Example:
            >>> obs = ObservabilityCore()
            >>> obs.logger.info("Message", extra={"key": "value"})
        """
        if self._logger is None:
            raise RuntimeError("Logger not initialized")
        return self._logger

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
            >>> obs = ObservabilityCore()
            >>> span = obs.start_span("database.query", kind=SpanKind.CLIENT)
            >>> span.set_attribute("query.type", "SELECT")
            >>> span.end()
        """
        parent_context = parent.context if parent else get_current_trace_context()

        span = Span(
            name=name,
            kind=kind,
            parent=parent_context,
            attributes=attributes,
        )
        span.start()

        # Set as current context
        set_current_trace_context(span.context)

        # Store active span
        span_id = span.context.span_id
        self._active_spans[span_id] = span

        return span

    def end_span(self, span: Span, status: SpanStatus = SpanStatus.OK) -> None:
        """
        End a span.

        Args:
            span: Span to end
            status: Span status (OK, ERROR, UNSET)

        Example:
            >>> obs = ObservabilityCore()
            >>> span = obs.start_span("operation")
            >>> obs.end_span(span, SpanStatus.OK)
        """
        span.end(status)

        # Remove from active spans
        span_id = span.context.span_id
        self._active_spans.pop(span_id, None)

        # Restore parent context
        if span.parent:
            set_current_trace_context(span.parent)
        else:
            set_current_trace_context(None)

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
            >>> obs = ObservabilityCore()
            >>> with obs.trace("agent.execution") as span:
            ...     span.set_attribute("agent", "code")
            ...     result = execute()
        """
        span = self.start_span(name, kind=kind, attributes=attributes)
        try:
            yield span
            span.end(SpanStatus.OK)
        except Exception as e:
            span.record_exception(e)
            span.end(SpanStatus.ERROR)
            raise
        finally:
            # Clean up active spans
            self._active_spans.pop(span.context.span_id, None)

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
            >>> obs = ObservabilityCore()
            >>> async with obs.trace_async("db.query") as span:
            ...     result = await db.execute(query)
        """
        span = self.start_span(name, kind=kind, attributes=attributes)
        try:
            yield span
            span.end(SpanStatus.OK)
        except Exception as e:
            span.record_exception(e)
            span.end(SpanStatus.ERROR)
            raise
        finally:
            self._active_spans.pop(span.context.span_id, None)

    def inject_context(self, carrier: Dict[str, str]) -> None:
        """
        Inject trace context into carrier for propagation.

        Args:
            carrier: Dictionary to inject context into

        Example:
            >>> obs = ObservabilityCore()
            >>> headers = {}
            >>> with obs.trace("operation"):
            ...     obs.inject_context(headers)
            ...     # headers now contains traceparent
        """
        context = get_current_trace_context()
        if context and self.enable_tracing:
            self.propagator.inject(context, carrier)

    def extract_context(self, carrier: Dict[str, str]) -> Optional[TraceContext]:
        """
        Extract trace context from carrier.

        Args:
            carrier: Dictionary containing context

        Returns:
            Extracted trace context or None

        Example:
            >>> obs = ObservabilityCore()
            >>> headers = {"traceparent": "00-...-...-01"}
            >>> context = obs.extract_context(headers)
            >>> obs._context_manager.set_current(context)
        """
        if not self.enable_tracing:
            return None
        return self.propagator.extract(carrier)

    def _log_debug(self, message: str, **kwargs) -> None:
        """Log debug message with context."""
        if self._logger:
            self._logger.debug(message, extra=kwargs)

    def log_debug(self, message: str, **kwargs) -> None:
        """Log debug message with context."""
        if self._logger:
            self._logger.debug(message, extra=kwargs)

    def log_info(self, message: str, **kwargs) -> None:
        """Log info message with context."""
        if self._logger:
            self._logger.info(message, extra=kwargs)

    def log_warning(self, message: str, **kwargs) -> None:
        """Log warning message with context."""
        if self._logger:
            self._logger.warning(message, extra=kwargs)

    def log_error(self, message: str, **kwargs) -> None:
        """Log error message with context."""
        if self._logger:
            self._logger.error(message, extra=kwargs)

    def get_current_trace(self) -> Optional[TraceContext]:
        """
        Get current trace context.

        Returns:
            Current trace context or None

        Example:
            >>> obs = ObservabilityCore()
            >>> with obs.trace("operation"):
            ...     context = obs.get_current_trace()
            ...     print(context.trace_id)
        """
        return get_current_trace_context()

    def set_current_trace(self, context: TraceContext) -> None:
        """
        Set current trace context.

        Args:
            context: Trace context to set

        Example:
            >>> obs = ObservabilityCore()
            >>> context = TraceContext.generate()
            >>> obs.set_current_trace(context)
        """
        set_current_trace_context(context)

    async def shutdown(self) -> None:
        """
        Graceful shutdown.

        Flushes pending spans, metrics, and logs.

        Example:
            >>> obs = ObservabilityCore()
            >>> await obs.shutdown()
        """
        self.log_info("Shutting down observability")

        # End any active spans
        for span in list(self._active_spans.values()):
            if span.is_recording():
                span.end(SpanStatus.OK)
        self._active_spans.clear()

        # Clear context
        set_current_trace_context(None)

        self._log_debug("Observability shutdown complete")


def get_observability() -> ObservabilityCore:
    """
    Get the global observability instance.

    Returns:
        Global ObservabilityCore instance

    Example:
        >>> obs = get_observability()
        >>> obs.log_info("Message")
    """
    global _default_observability
    with _observability_lock:
        if _default_observability is None:
            _default_observability = ObservabilityCore()
        return _default_observability


def traced(
    name: Optional[str] = None,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Optional[Dict[str, Any]] = None,
    record_exceptions: bool = True,
) -> Callable[[F], F]:
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
                obs = get_observability()
                async with obs.trace_async(span_name, kind=kind, attributes=attributes) as span:
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        if record_exceptions:
                            span.record_exception(e)
                        raise

            return async_wrapper  # type: ignore
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                obs = get_observability()
                with obs.trace(span_name, kind=kind, attributes=attributes) as span:
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        if record_exceptions:
                            span.record_exception(e)
                        raise

            return sync_wrapper  # type: ignore

    return decorator
