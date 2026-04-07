"""
Circuit Breaker Pattern Implementation.

The Circuit Breaker pattern prevents cascading failures in distributed systems
by failing fast when a service is unavailable, and automatically recovering
when the service becomes healthy again.

States:
- CLOSED: Normal operation, requests flow through
- OPEN: Failure threshold exceeded, requests fail fast
- HALF_OPEN: Testing if service has recovered
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional, TypeVar, Union

T = TypeVar("T")


class CircuitBreakerState(Enum):
    """Circuit breaker states."""

    CLOSED = auto()  # Normal operation
    OPEN = auto()  # Failing fast
    HALF_OPEN = auto()  # Testing recovery


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and requests are rejected."""

    def __init__(self, message: str = "Circuit breaker is open", time_until_retry: Optional[float] = None):
        super().__init__(message)
        self.time_until_retry = time_until_retry


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """
    Configuration for Circuit Breaker.

    Attributes:
        failure_threshold: Number of consecutive failures before opening circuit.
                          Default: 5
        recovery_timeout: Seconds to wait before testing recovery.
                         Default: 30.0
        success_threshold: Successes needed in half-open to close circuit.
                          Default: 2
        expected_exceptions: Exception types that count as failures.
                            Default: (Exception,) - all exceptions
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    success_threshold: int = 2
    expected_exceptions: tuple = field(default_factory=lambda: (Exception,))

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")
        if self.success_threshold < 1:
            raise ValueError("success_threshold must be >= 1")


class CircuitBreaker:
    """
    Circuit Breaker implementation with automatic state transitions.

    Thread-safe implementation using RLock for concurrent access protection.

    Example usage:
        >>> breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        >>>
        >>> # Synchronous usage
        >>> result = breaker.call(risky_operation, arg1, arg2)
        >>>
        >>> # As decorator
        >>> @breaker
        >>> def risky_operation():
        ...     ...
        >>>
        >>> # Async usage
        >>> result = await breaker.acall(async_risky_operation)
    """

    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        """
        Initialize Circuit Breaker.

        Args:
            config: Circuit breaker configuration. Uses defaults if None.
        """
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()

    @property
    def config(self) -> CircuitBreakerConfig:
        """Get circuit breaker configuration (read-only)."""
        return self._config

    @property
    def state(self) -> CircuitBreakerState:
        """Get current circuit state."""
        with self._lock:
            return self._get_state()

    @property
    def failure_count(self) -> int:
        """Get current failure count."""
        with self._lock:
            return self._failure_count

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self.state == CircuitBreakerState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (failing fast)."""
        return self.state == CircuitBreakerState.OPEN

    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self.state == CircuitBreakerState.HALF_OPEN

    def _get_state(self) -> CircuitBreakerState:
        """
        Get current state, checking for automatic transitions.

        Thread-safe state retrieval with timeout checking.

        Returns:
            Current circuit breaker state.
        """
        # Check if we should transition from OPEN to HALF_OPEN
        if self._state == CircuitBreakerState.OPEN and self._last_failure_time is not None:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self._config.recovery_timeout:
                self._state = CircuitBreakerState.HALF_OPEN
                self._success_count = 0

        return self._state

    def _record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    # Circuit recovered, close it
                    self._state = CircuitBreakerState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            elif self._state == CircuitBreakerState.CLOSED:
                # Reset failure count on success in closed state
                self._failure_count = 0

    def _record_failure(self) -> None:
        """Record a failed operation."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitBreakerState.HALF_OPEN:
                # Failed during recovery test, reopen circuit
                self._state = CircuitBreakerState.OPEN
            elif self._state == CircuitBreakerState.CLOSED:
                if self._failure_count >= self._config.failure_threshold:
                    # Threshold exceeded, open circuit
                    self._state = CircuitBreakerState.OPEN

    def _can_execute(self) -> tuple[bool, Optional[float]]:
        """
        Check if operation can execute in current state.

        Returns:
            Tuple of (can_execute, time_until_retry).
            time_until_retry is None if operation can proceed.
        """
        current_state = self._get_state()

        if current_state == CircuitBreakerState.CLOSED:
            return True, None
        elif current_state == CircuitBreakerState.OPEN:
            if self._last_failure_time is not None:
                elapsed = time.time() - self._last_failure_time
                if elapsed < self._config.recovery_timeout:
                    time_remaining = self._config.recovery_timeout - elapsed
                    return False, time_remaining
            # Recovery timeout passed, will transition to HALF_OPEN
            return True, None
        else:  # HALF_OPEN
            return True, None

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute function through circuit breaker (synchronous).

        Args:
            func: Function to execute.
            *args: Positional arguments for function.
            **kwargs: Keyword arguments for function.

        Returns:
            Result of function execution.

        Raises:
            CircuitOpenError: If circuit is open and request is rejected.
            Exception: Any exception from the wrapped function that is not
                      in expected_exceptions will be re-raised immediately.
        """
        with self._lock:
            can_execute, time_until_retry = self._can_execute()

            if not can_execute:
                raise CircuitOpenError(
                    f"Circuit breaker is open, failing fast",
                    time_until_retry=time_until_retry,
                )

        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except self._config.expected_exceptions as e:
            self._record_failure()
            raise

    async def acall(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Execute async function through circuit breaker.

        Args:
            func: Async function to execute.
            *args: Positional arguments for function.
            **kwargs: Keyword arguments for function.

        Returns:
            Result of async function execution.

        Raises:
            CircuitOpenError: If circuit is open and request is rejected.
        """
        async with self._async_lock:
            can_execute, time_until_retry = self._can_execute()

            if not can_execute:
                raise CircuitOpenError(
                    f"Circuit breaker is open, failing fast",
                    time_until_retry=time_until_retry,
                )

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            self._record_success()
            return result
        except self._config.expected_exceptions as e:
            self._record_failure()
            raise

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator for wrapping functions with circuit breaker.

        Args:
            func: Function to wrap.

        Returns:
            Wrapped function with circuit breaker protection.
        """
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await self.acall(func, *args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> T:
                return self.call(func, *args, **kwargs)
            return sync_wrapper

    def reset(self) -> None:
        """
        Manually reset circuit breaker to closed state.

        Clears failure count and resets state to CLOSED.
        Use this for manual recovery scenarios.
        """
        with self._lock:
            self._state = CircuitBreakerState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None

    def trip(self) -> None:
        """
        Manually trip (open) the circuit breaker.

        Forces circuit to OPEN state immediately.
        Use this for forced failure scenarios or testing.
        """
        with self._lock:
            self._state = CircuitBreakerState.OPEN
            self._last_failure_time = time.time()

    def half_open(self) -> None:
        """
        Manually set circuit breaker to HALF_OPEN state.

        Forces circuit to HALF_OPEN state for testing recovery.
        Use this for manual recovery testing scenarios.
        """
        with self._lock:
            self._state = CircuitBreakerState.HALF_OPEN
            self._success_count = 0

    def __repr__(self) -> str:
        """Get string representation of circuit breaker."""
        return (
            f"CircuitBreaker("
            f"state={self._state.name}, "
            f"failure_count={self._failure_count}, "
            f"threshold={self._config.failure_threshold}"
            f")"
        )
