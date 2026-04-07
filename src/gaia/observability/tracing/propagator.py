"""
Trace context propagators for distributed tracing.

This module provides propagators for injecting and extracting trace
context across service boundaries using standard formats like W3C
Trace Context and B3 (Zipkin).

Example:
    >>> from gaia.observability.tracing.propagator import W3CPropagator
    >>> from gaia.observability.tracing.trace_context import TraceContext
    >>>
    >>> propagator = W3CPropagator()
    >>> context = TraceContext(
    ...     trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
    ...     span_id="00f067aa0ba902b7",
    ... )
    >>> carrier = {}
    >>> propagator.inject(context, carrier)
    >>> carrier["traceparent"]
    '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01'
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional, List

from .trace_context import TraceContext


class TracePropagator(ABC):
    """
    Abstract base class for trace context propagators.

    Propagators handle injecting and extracting trace context
    into/from wire formats (HTTP headers, message metadata, etc.).

    Example:
        >>> class CustomPropagator(TracePropagator):
        ...     def inject(self, context, carrier):
        ...         carrier["x-trace-id"] = context.trace_id
        ...
        ...     def extract(self, carrier):
        ...         trace_id = carrier.get("x-trace-id")
        ...         if trace_id:
        ...             return TraceContext(trace_id=trace_id, span_id="0" * 16)
        ...         return None
    """

    @abstractmethod
    def inject(self, context: TraceContext, carrier: Dict[str, str]) -> None:
        """
        Inject trace context into carrier.

        Args:
            context: Trace context to inject
            carrier: Dictionary to inject into (e.g., HTTP headers)

        Example:
            >>> propagator = W3CPropagator()
            >>> context = TraceContext.generate()
            >>> carrier = {}
            >>> propagator.inject(context, carrier)
        """
        pass

    @abstractmethod
    def extract(self, carrier: Dict[str, str]) -> Optional[TraceContext]:
        """
        Extract trace context from carrier.

        Args:
            carrier: Dictionary to extract from (e.g., HTTP headers)

        Returns:
            Extracted trace context or None if not found

        Example:
            >>> propagator = W3CPropagator()
            >>> carrier = {"traceparent": "00-...-...-01"}
            >>> context = propagator.extract(carrier)
        """
        pass


class W3CPropagator(TracePropagator):
    """
    W3C Trace Context propagator.

    Implements the W3C Trace Context specification:
    https://www.w3.org/TR/trace-context/

    Headers:
        - traceparent: Version, trace-id, span-id, trace-flags
        - tracestate: Vendor-specific trace state (optional)

    Format:
        traceparent: {version}-{trace_id}-{span_id}-{trace_flags}
        Example: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01

    Example:
        >>> propagator = W3CPropagator()
        >>> context = TraceContext(
        ...     trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        ...     span_id="00f067aa0ba902b7",
        ...     trace_flags=1,
        ... )
        >>> carrier = {}
        >>> propagator.inject(context, carrier)
        >>> print(carrier["traceparent"])
        00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
    """

    TRACEPARENT_HEADER = "traceparent"
    TRACESTATE_HEADER = "tracestate"
    VERSION = "00"

    def inject(self, context: TraceContext, carrier: Dict[str, str]) -> None:
        """
        Inject W3C traceparent and tracestate headers.

        Args:
            context: Trace context to inject
            carrier: Dictionary to inject headers into

        Example:
            >>> propagator = W3CPropagator()
            >>> context = TraceContext.generate()
            >>> carrier = {}
            >>> propagator.inject(context, carrier)
            >>> "traceparent" in carrier
            True
        """
        traceparent = f"{self.VERSION}-{context.trace_id}-{context.span_id}-{context.trace_flags:02x}"
        carrier[self.TRACEPARENT_HEADER] = traceparent

        if context.trace_state:
            carrier[self.TRACESTATE_HEADER] = context.trace_state

    def extract(self, carrier: Dict[str, str]) -> Optional[TraceContext]:
        """
        Extract from W3C traceparent header.

        Args:
            carrier: Dictionary to extract from

        Returns:
            Extracted trace context or None if invalid/missing

        Example:
            >>> propagator = W3CPropagator()
            >>> carrier = {
            ...     "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
            ... }
            >>> context = propagator.extract(carrier)
            >>> context.trace_id
            '4bf92f3577b34da6a3ce929d0e0e4736'
        """
        traceparent = carrier.get(self.TRACEPARENT_HEADER)
        if not traceparent:
            return None

        parts = traceparent.split("-")
        if len(parts) != 4:
            return None

        try:
            version, trace_id, span_id, trace_flags = parts
            if version not in ("00", "ff"):
                return None

            return TraceContext(
                trace_id=trace_id.lower(),
                span_id=span_id.lower(),
                trace_flags=int(trace_flags, 16),
                trace_state=carrier.get(self.TRACESTATE_HEADER, ""),
            )
        except (ValueError, IndexError):
            return None

    @staticmethod
    def is_valid_traceparent(traceparent: str) -> bool:
        """
        Validate W3C traceparent format.

        Args:
            traceparent: Traceparent header value

        Returns:
            True if valid format

        Example:
            >>> W3CPropagator.is_valid_traceparent(
            ...     "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
            ... )
            True
        """
        parts = traceparent.split("-")
        if len(parts) != 4:
            return False

        version, trace_id, span_id, trace_flags = parts
        if version not in ("00", "ff"):
            return False
        if len(trace_id) != 32 or not all(c in "0123456789abcdef" for c in trace_id.lower()):
            return False
        if len(span_id) != 16 or not all(c in "0123456789abcdef" for c in span_id.lower()):
            return False
        if len(trace_flags) != 2 or not all(c in "0123456789abcdef" for c in trace_flags.lower()):
            return False

        return True


class B3Propagator(TracePropagator):
    """
    B3 propagator (Zipkin format).

    Implements the B3 multi-header format:
    https://github.com/openzipkin/b3-propagation

    Headers:
        - X-B3-TraceId: Trace ID (32 or 16 hex chars)
        - X-B3-SpanId: Span ID (16 hex chars)
        - X-B3-Sampled: Sampled flag ("1" or "0")
        - X-B3-ParentSpanId: Parent span ID (optional)

    Example:
        >>> propagator = B3Propagator()
        >>> context = TraceContext(
        ...     trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        ...     span_id="00f067aa0ba902b7",
        ...     trace_flags=1,
        ... )
        >>> carrier = {}
        >>> propagator.inject(context, carrier)
        >>> carrier["X-B3-TraceId"]
        '4bf92f3577b34da6a3ce929d0e0e4736'
    """

    TRACE_ID_HEADER = "X-B3-TraceId"
    SPAN_ID_HEADER = "X-B3-SpanId"
    SAMPLED_HEADER = "X-B3-Sampled"
    PARENT_ID_HEADER = "X-B3-ParentSpanId"

    def inject(self, context: TraceContext, carrier: Dict[str, str]) -> None:
        """
        Inject B3 headers.

        Args:
            context: Trace context to inject
            carrier: Dictionary to inject headers into

        Example:
            >>> propagator = B3Propagator()
            >>> context = TraceContext.generate()
            >>> carrier = {}
            >>> propagator.inject(context, carrier)
            >>> "X-B3-TraceId" in carrier
            True
        """
        carrier[self.TRACE_ID_HEADER] = context.trace_id
        carrier[self.SPAN_ID_HEADER] = context.span_id
        carrier[self.SAMPLED_HEADER] = "1" if context.is_sampled else "0"

        if context.parent_span_id:
            carrier[self.PARENT_ID_HEADER] = context.parent_span_id

    def extract(self, carrier: Dict[str, str]) -> Optional[TraceContext]:
        """
        Extract from B3 headers.

        Args:
            carrier: Dictionary to extract from

        Returns:
            Extracted trace context or None if invalid/missing

        Example:
            >>> propagator = B3Propagator()
            >>> carrier = {
            ...     "X-B3-TraceId": "4bf92f3577b34da6a3ce929d0e0e4736",
            ...     "X-B3-SpanId": "00f067aa0ba902b7",
            ...     "X-B3-Sampled": "1"
            ... }
            >>> context = propagator.extract(carrier)
            >>> context.trace_id
            '4bf92f3577b34da6a3ce929d0e0e4736'
        """
        trace_id = carrier.get(self.TRACE_ID_HEADER)
        span_id = carrier.get(self.SPAN_ID_HEADER)

        if not trace_id or not span_id:
            return None

        try:
            sampled = carrier.get(self.SAMPLED_HEADER, "0")
            trace_flags = 1 if sampled in ("1", "true", "d") else 0

            return TraceContext(
                trace_id=trace_id.lower().zfill(32),
                span_id=span_id.lower().zfill(16),
                trace_flags=trace_flags,
                parent_span_id=carrier.get(self.PARENT_ID_HEADER),
            )
        except (ValueError, AttributeError):
            return None


class CompositePropagator(TracePropagator):
    """
    Composite propagator that tries multiple propagators in sequence.

    This is useful when you want to support multiple trace context
    formats and extract from whichever one is present.

    Example:
        >>> propagators = [W3CPropagator(), B3Propagator()]
        >>> composite = CompositePropagator(propagators)
        >>> carrier = {"traceparent": "00-...-...-01"}
        >>> context = composite.extract(carrier)  # Uses W3C
    """

    def __init__(self, propagators: List[TracePropagator]) -> None:
        """
        Initialize composite propagator.

        Args:
            propagators: List of propagators to try in order

        Example:
            >>> composite = CompositePropagator([
            ...     W3CPropagator(),
            ...     B3Propagator()
            ... ])
        """
        self.propagators = propagators

    def inject(self, context: TraceContext, carrier: Dict[str, str]) -> None:
        """
        Inject using all propagators.

        Args:
            context: Trace context to inject
            carrier: Dictionary to inject into
        """
        for propagator in self.propagators:
            propagator.inject(context, carrier)

    def extract(self, carrier: Dict[str, str]) -> Optional[TraceContext]:
        """
        Extract using first propagator that succeeds.

        Args:
            carrier: Dictionary to extract from

        Returns:
            First successfully extracted context or None
        """
        for propagator in self.propagators:
            context = propagator.extract(carrier)
            if context is not None:
                return context
        return None


def get_default_propagator() -> TracePropagator:
    """
    Get the default propagator (W3C Trace Context).

    Returns:
        Default W3CPropagator instance

    Example:
        >>> propagator = get_default_propagator()
        >>> isinstance(propagator, W3CPropagator)
        True
    """
    return W3CPropagator()
