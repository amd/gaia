"""
Trace context management for distributed tracing.

This module provides the TraceContext dataclass for managing trace context
information that propagates across service boundaries.

Example:
    >>> from gaia.observability.tracing.trace_context import TraceContext
    >>>
    >>> context = TraceContext(
    ...     trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
    ...     span_id="00f067aa0ba902b7",
    ...     trace_flags=1,
    ... )
    >>> print(context.trace_id)
    4bf92f3577b34da6a3ce929d0e0e4736
"""

from dataclasses import dataclass, field
from typing import Optional
import threading


@dataclass
class SpanContext:
    """
    Immutable span context for propagation.

    This is a lightweight version of TraceContext specifically for
    span-level context propagation.

    Attributes:
        trace_id: 128-bit trace ID (32 hex chars)
        span_id: 64-bit span ID (16 hex chars)
        trace_flags: Trace flags (01 = sampled)
        trace_state: Vendor-specific trace state

    Example:
        >>> ctx = SpanContext(
        ...     trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        ...     span_id="00f067aa0ba902b7"
        ... )
        >>> ctx.to_w3c_traceparent()
        '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01'
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


@dataclass
class TraceContext:
    """
    Immutable trace context for distributed tracing.

    The trace context contains the minimum information needed to
    propagate trace context across service boundaries. It is designed
    to be lightweight and thread-safe.

    Attributes:
        trace_id: 128-bit trace ID as 32 lowercase hex characters
        span_id: 64-bit span ID as 16 lowercase hex characters
        trace_flags: Trace flags (01 = sampled, 00 = not sampled)
        trace_state: Vendor-specific trace state (optional)
        parent_span_id: Optional parent span ID for context hierarchy

    Example:
        >>> context = TraceContext(
        ...     trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        ...     span_id="00f067aa0ba902b7",
        ...     trace_flags=1,
        ...     trace_state="vendor=value"
        ... )
        >>> context.is_sampled
        True
    """

    trace_id: str
    span_id: str
    trace_flags: int = 1
    trace_state: str = ""
    parent_span_id: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate trace context fields."""
        if len(self.trace_id) != 32:
            raise ValueError(f"trace_id must be 32 hex chars, got {len(self.trace_id)}")
        if len(self.span_id) != 16:
            raise ValueError(f"span_id must be 16 hex chars, got {len(self.span_id)}")
        if self.trace_flags not in (0, 1):
            raise ValueError(f"trace_flags must be 0 or 1, got {self.trace_flags}")

    @property
    def is_sampled(self) -> bool:
        """Check if trace is sampled."""
        return bool(self.trace_flags & 1)

    @classmethod
    def generate(cls) -> "TraceContext":
        """
        Generate a new trace context with random IDs.

        Returns:
            New trace context with generated IDs

        Example:
            >>> context = TraceContext.generate()
            >>> len(context.trace_id)
            32
        """
        import uuid

        return cls(
            trace_id=uuid.uuid4().hex,
            span_id=uuid.uuid4().hex[:16],
            trace_flags=1,
        )

    def with_span_id(self, span_id: str) -> "TraceContext":
        """
        Create a new context with a different span ID.

        Args:
            span_id: New span ID

        Returns:
            New TraceContext with updated span_id

        Example:
            >>> context = TraceContext.generate()
            >>> new_context = context.with_span_id("newspanid1234567")
        """
        return TraceContext(
            trace_id=self.trace_id,
            span_id=span_id,
            trace_flags=self.trace_flags,
            trace_state=self.trace_state,
            parent_span_id=self.span_id,
        )

    def to_dict(self) -> dict:
        """
        Convert trace context to dictionary.

        Returns:
            Dictionary representation of trace context

        Example:
            >>> context = TraceContext.generate()
            >>> d = context.to_dict()
            >>> "trace_id" in d
            True
        """
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "trace_flags": self.trace_flags,
            "trace_state": self.trace_state,
            "parent_span_id": self.parent_span_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TraceContext":
        """
        Create trace context from dictionary.

        Args:
            data: Dictionary with trace context fields

        Returns:
            New TraceContext instance

        Example:
            >>> context = TraceContext.from_dict({
            ...     "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
            ...     "span_id": "00f067aa0ba902b7",
            ... })
        """
        return cls(
            trace_id=data.get("trace_id", ""),
            span_id=data.get("span_id", ""),
            trace_flags=data.get("trace_flags", 1),
            trace_state=data.get("trace_state", ""),
            parent_span_id=data.get("parent_span_id"),
        )


class TraceContextManager:
    """
    Thread-safe manager for trace context propagation.

    This class manages the current trace context using thread-local
    storage for synchronous code and context variables for async code.

    Example:
        >>> manager = TraceContextManager()
        >>> context = TraceContext.generate()
        >>> manager.set_current(context)
        >>> current = manager.get_current()
        >>> current.trace_id == context.trace_id
        True
    """

    def __init__(self) -> None:
        """Initialize trace context manager."""
        self._local = threading.local()
        self._lock = threading.RLock()

    def get_current(self) -> Optional[TraceContext]:
        """
        Get the current trace context from thread-local storage.

        Returns:
            Current trace context or None if not set

        Example:
            >>> manager = TraceContextManager()
            >>> manager.get_current() is None
            True
        """
        return getattr(self._local, "trace_context", None)

    def set_current(self, context: Optional[TraceContext]) -> None:
        """
        Set the current trace context in thread-local storage.

        Args:
            context: Trace context to set (None to clear)

        Example:
            >>> manager = TraceContextManager()
            >>> context = TraceContext.generate()
            >>> manager.set_current(context)
        """
        with self._lock:
            self._local.trace_context = context

    def clear(self) -> None:
        """
        Clear the current trace context.

        Example:
            >>> manager = TraceContextManager()
            >>> manager.set_current(TraceContext.generate())
            >>> manager.clear()
            >>> manager.get_current() is None
            True
        """
        with self._lock:
            if hasattr(self._local, "trace_context"):
                delattr(self._local, "trace_context")

    def context_manager(self, context: TraceContext):
        """
        Create a context manager for trace context scope.

        Args:
            context: Trace context to set for the scope

        Yields:
            The trace context

        Example:
            >>> manager = TraceContextManager()
            >>> context = TraceContext.generate()
            >>> with manager.context_manager(context):
            ...     assert manager.get_current() == context
        """
        old_context = self.get_current()
        self.set_current(context)
        try:
            yield context
        finally:
            self.set_current(old_context)


# Global context manager instance for default usage
_default_context_manager: Optional[TraceContextManager] = None
_context_manager_lock = threading.Lock()


def get_context_manager() -> TraceContextManager:
    """
    Get the global trace context manager instance.

    Returns:
        Global TraceContextManager singleton

    Example:
        >>> manager = get_context_manager()
        >>> context = TraceContext.generate()
        >>> manager.set_current(context)
    """
    global _default_context_manager
    with _context_manager_lock:
        if _default_context_manager is None:
            _default_context_manager = TraceContextManager()
        return _default_context_manager


def get_current_trace_context() -> Optional[TraceContext]:
    """
    Get the current trace context from global manager.

    Returns:
        Current trace context or None

    Example:
        >>> context = TraceContext.generate()
        >>> get_context_manager().set_current(context)
        >>> current = get_current_trace_context()
    """
    return get_context_manager().get_current()


def set_current_trace_context(context: Optional[TraceContext]) -> None:
    """
    Set the current trace context in global manager.

    Args:
        context: Trace context to set

    Example:
        >>> context = TraceContext.generate()
        >>> set_current_trace_context(context)
    """
    get_context_manager().set_current(context)
