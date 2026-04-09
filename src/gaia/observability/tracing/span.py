"""
Span implementation for distributed tracing.

This module provides the Span class which represents a single operation
within a trace. Spans contain timing information, attributes, events,
and status for observability.

Example:
    >>> from gaia.observability.tracing.span import Span, SpanKind, SpanStatus
    >>>
    >>> span = Span("database.query", kind=SpanKind.CLIENT)
    >>> span.start()
    >>> span.set_attribute("db.system", "postgresql")
    >>> span.add_event("query.executed", {"rows": 100})
    >>> span.end(status=SpanStatus.OK)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import time
import uuid

from .trace_context import SpanContext, TraceContext


class SpanKind(Enum):
    """
    Span kind indicating relationship to parent/child spans.

    The span kind helps identify the role of the span in the trace
    and is used by observability tools to categorize and visualize spans.

    Members:
        INTERNAL: Internal operation within the service
        SERVER: Handles incoming request (e.g., HTTP server)
        CLIENT: Makes outgoing request (e.g., HTTP client, database client)
        PRODUCER: Produces message to queue/topic
        CONSUMER: Consumes message from queue/topic

    Example:
        >>> SpanKind.SERVER.value
        'server'
        >>> SpanKind.CLIENT.value
        'client'
    """

    INTERNAL = "internal"
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"


class SpanStatus(Enum):
    """
    Span execution status.

    Status indicates whether the operation completed successfully
    or encountered an error.

    Members:
        UNSET: Default status before completion
        OK: Operation completed successfully
        ERROR: Operation completed with error

    Example:
        >>> span = Span("operation")
        >>> span.status
        <SpanStatus.UNSET: 'unset'>
        >>> span.end(SpanStatus.OK)
    """

    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


@dataclass
class Event:
    """
    Event occurring during span execution.

    Events are timestamped records of significant moments during
    span execution, such as log messages or state changes.

    Attributes:
        name: Event name
        timestamp: Unix timestamp when event occurred
        attributes: Event attributes/metadata

    Example:
        >>> event = Event(
        ...     name="query.executed",
        ...     timestamp=time.time(),
        ...     attributes={"rows": 100}
        ... )
    """

    name: str
    timestamp: float = field(default_factory=time.time)
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "name": self.name,
            "timestamp": self.timestamp,
            "attributes": self.attributes,
        }


class Span:
    """
    Represents a single operation within a trace.

    A span is the basic unit of work in distributed tracing.
    It has a start time, end time, and contains attributes,
    events, and status information.

    Spans can have parent-child relationships, forming a trace tree.
    Each span has an immutable context that can be propagated across
    service boundaries.

    Attributes:
        name: Human-readable span name
        kind: Span kind (SERVER, CLIENT, etc.)
        context: Immutable span context
        parent: Optional parent span context
        status: Span execution status
        start_time: Span start time (Unix timestamp)
        end_time: Span end time (Unix timestamp)

    Example:
        >>> span = Span("database.query", kind=SpanKind.CLIENT)
        >>> span.start()
        >>> span.set_attribute("db.system", "postgresql")
        >>> span.add_event("query.start", {"query": "SELECT ..."})
        >>> span.end(status=SpanStatus.OK)
        >>> print(span.duration)
        0.0234
    """

    def __init__(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Optional[TraceContext] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Initialize span.

        Args:
            name: Span name describing the operation
            kind: Span kind indicating the relationship to parent/child
            parent: Parent span context for creating child spans
            attributes: Initial span attributes

        Example:
            >>> span = Span(
            ...     "http.request",
            ...     kind=SpanKind.SERVER,
            ...     attributes={"http.method": "GET"}
            ... )
        """
        self.name = name
        self.kind = kind
        self.context = TraceContext(
            trace_id=parent.trace_id if parent else uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            trace_flags=parent.trace_flags if parent else 1,
            trace_state=parent.trace_state if parent else "",
            parent_span_id=parent.span_id if parent else None,
        )
        self.parent = parent
        self.attributes = dict(attributes) if attributes else {}
        self.events: List[Event] = []
        self.status = SpanStatus.UNSET
        self.status_description: str = ""
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self._ended: bool = False

    def start(self) -> "Span":
        """
        Start the span.

        Records the current time as the span start time.

        Returns:
            Self for method chaining

        Example:
            >>> span = Span("operation")
            >>> span.start()
            >>> span.start_time is not None
            True
        """
        if self.start_time is None:
            self.start_time = time.perf_counter()
        return self

    def end(self, status: SpanStatus = SpanStatus.OK) -> None:
        """
        End the span.

        Records the current time as the span end time and sets the status.

        Args:
            status: Final span status (default: OK)

        Example:
            >>> span = Span("operation")
            >>> span.start()
            >>> span.end(SpanStatus.OK)
            >>> span.end_time is not None
            True
        """
        if not self._ended:
            self.end_time = time.perf_counter()
            self.status = status
            self._ended = True

    def set_attribute(self, key: str, value: Any) -> None:
        """
        Set span attribute.

        Attributes are key-value pairs that provide metadata about
        the span. Values are converted to strings if needed.

        Args:
            key: Attribute key (dot-separated for nested attributes)
            value: Attribute value (converted to string if not primitive)

        Example:
            >>> span = Span("db.query")
            >>> span.set_attribute("db.system", "postgresql")
            >>> span.set_attribute("db.statement", "SELECT * FROM users")
        """
        if not self._ended:
            self.attributes[key] = self._normalize_value(value)

    def set_attributes(self, attributes: Dict[str, Any]) -> None:
        """
        Set multiple span attributes at once.

        Args:
            attributes: Dictionary of attribute key-value pairs

        Example:
            >>> span = Span("http.request")
            >>> span.set_attributes({
            ...     "http.method": "GET",
            ...     "http.url": "/api/users",
            ...     "http.status_code": 200
            ... })
        """
        if not self._ended:
            for key, value in attributes.items():
                self.attributes[key] = self._normalize_value(value)

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """
        Add event to span.

        Events are timestamped records of significant moments during
        span execution.

        Args:
            name: Event name
            attributes: Event attributes/metadata

        Example:
            >>> span = Span("db.query")
            >>> span.add_event("query.start", {"query": "SELECT ..."})
            >>> span.add_event("query.end", {"rows": 100})
        """
        if not self._ended:
            event = Event(name=name, attributes=attributes or {})
            self.events.append(event)

    def record_exception(
        self,
        exception: Exception,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record exception as span event.

        Adds an event with exception details including type, message,
        and optionally the stacktrace.

        Args:
            exception: Exception to record
            attributes: Additional attributes to add to the event

        Example:
            >>> span = Span("operation")
            >>> span.start()
            >>> try:
            ...     risky_operation()
            ... except Exception as e:
            ...     span.record_exception(e)
            ...     span.end(SpanStatus.ERROR)
        """
        import traceback

        event_attributes = {
            "exception.type": exception.__class__.__name__,
            "exception.message": str(exception),
            "exception.stacktrace": traceback.format_exc(),
        }
        if attributes:
            event_attributes.update(attributes)

        self.add_event("exception", event_attributes)
        self.set_status(SpanStatus.ERROR, str(exception))

    def set_status(self, status: SpanStatus, description: str = "") -> None:
        """
        Set span status.

        Args:
            status: Span status (OK, ERROR, UNSET)
            description: Optional status description

        Example:
            >>> span = Span("operation")
            >>> span.start()
            >>> span.set_status(SpanStatus.OK, "Completed successfully")
        """
        if not self._ended:
            self.status = status
            self.status_description = description

    @property
    def duration(self) -> Optional[float]:
        """
        Get span duration in seconds.

        Returns:
            Duration in seconds, or None if span hasn't ended

        Example:
            >>> span = Span("operation")
            >>> span.start()
            >>> time.sleep(0.1)
            >>> span.end()
            >>> span.duration > 0.1
            True
        """
        if self.start_time is not None and self.end_time is not None:
            return self.end_time - self.start_time
        return None

    def is_recording(self) -> bool:
        """
        Check if span is still recording (not ended).

        Returns:
            True if span can still be modified

        Example:
            >>> span = Span("operation")
            >>> span.is_recording()
            True
            >>> span.end()
            >>> span.is_recording()
            False
        """
        return not self._ended

    def _normalize_value(self, value: Any) -> Any:
        """Normalize attribute value to supported types."""
        if value is None:
            return ""
        if isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (list, tuple)):
            return [self._normalize_value(v) for v in value]
        return str(value)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert span to dictionary for export.

        Returns:
            Dictionary representation of span

        Example:
            >>> span = Span("operation")
            >>> span.start()
            >>> span.end()
            >>> d = span.to_dict()
            >>> "name" in d
            True
            >>> "duration" in d
            True
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "trace_id": self.context.trace_id,
            "span_id": self.context.span_id,
            "parent_span_id": self.context.parent_span_id,
            "status": self.status.value,
            "status_description": self.status_description,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "attributes": self.attributes,
            "events": [event.to_dict() for event in self.events],
        }

    def __enter__(self) -> "Span":
        """Context manager entry - start span."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - end span."""
        if exc_type is not None:
            self.record_exception(exc_val)
            self.end(SpanStatus.ERROR)
        else:
            self.end(SpanStatus.OK)

    def __repr__(self) -> str:
        """String representation of span."""
        status = "active" if self.is_recording() else self.status.value
        return f"Span(name={self.name!r}, status={status}, duration={self.duration})"
