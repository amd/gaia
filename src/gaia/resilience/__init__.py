"""
Resilience Patterns Module for GAIA Framework.

This module provides fault tolerance patterns for building reliable
distributed systems and resilient agent operations.

Patterns implemented:
- Circuit Breaker: Prevent cascading failures
- Bulkhead: Isolate resources to prevent failure spread
- Retry with Exponential Backoff: Handle transient failures

Example usage:
    >>> from gaia.resilience import CircuitBreaker, Bulkhead, retry
    >>>
    >>> # Circuit Breaker
    >>> breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
    >>> result = breaker.call(risky_operation)
    >>>
    >>> # Bulkhead
    >>> bulkhead = Bulkhead(max_concurrency=10)
    >>> result = bulkhead.execute(operation)
    >>>
    >>> # Retry with backoff
    >>> @retry(max_retries=3, base_delay=1.0)
    >>> def flaky_operation():
    ...     ...
"""

from gaia.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    CircuitBreakerConfig,
    CircuitOpenError,
)
from gaia.resilience.bulkhead import (
    Bulkhead,
    BulkheadConfig,
    BulkheadFullError,
)
from gaia.resilience.retry import (
    retry,
    RetryConfig,
    RetryError,
)

__all__ = [
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerState",
    "CircuitBreakerConfig",
    "CircuitOpenError",
    # Bulkhead
    "Bulkhead",
    "BulkheadConfig",
    "BulkheadFullError",
    # Retry
    "retry",
    "RetryConfig",
    "RetryError",
]

__version__ = "1.0.0"
