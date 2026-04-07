"""
Bulkhead Pattern Implementation.

The Bulkhead pattern isolates resources to prevent failures from cascading
across system boundaries. Inspired by ship bulkheads that prevent water
from flooding the entire vessel.

This implementation uses semaphores to limit concurrent operations,
preventing resource exhaustion from cascading failures.
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


class BulkheadFullError(Exception):
    """Raised when bulkhead is at capacity and rejects new requests."""

    def __init__(self, message: str = "Bulkhead is at maximum capacity", max_concurrency: int = 0):
        super().__init__(message)
        self.max_concurrency = max_concurrency


@dataclass(frozen=True)
class BulkheadConfig:
    """
    Configuration for Bulkhead.

    Attributes:
        max_concurrency: Maximum concurrent operations allowed.
                        Default: 10
        acquire_timeout: Timeout in seconds for acquiring a permit.
                        None means wait indefinitely.
                        Default: 30.0
    """

    max_concurrency: int = 10
    acquire_timeout: Optional[float] = 30.0

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if self.acquire_timeout is not None and self.acquire_timeout <= 0:
            raise ValueError("acquire_timeout must be > 0 or None")


class Bulkhead:
    """
    Bulkhead implementation for resource isolation.

    Uses semaphores to limit concurrent operations and prevent resource
    exhaustion. Thread-safe for concurrent access.

    Example usage:
        >>> bulkhead = Bulkhead(max_concurrency=10)
        >>>
        >>> # Synchronous usage
        >>> result = bulkhead.execute(risky_operation, arg1, arg2)
        >>>
        >>> # As decorator
        >>> @bulkhead
        >>> def resource_intensive_operation():
        ...     ...
        >>>
        >>> # Async usage
        >>> result = await bulkhead.aexecute(async_operation)
        >>>
        >>> # With timeout
        >>> bulkhead_timeout = Bulkhead(max_concurrency=5, acquire_timeout=5.0)
    """

    def __init__(self, config: Optional[BulkheadConfig] = None):
        """
        Initialize Bulkhead.

        Args:
            config: Bulkhead configuration. Uses defaults if None.
        """
        self._config = config or BulkheadConfig()
        self._semaphore = threading.Semaphore(self._config.max_concurrency)
        self._async_semaphore = asyncio.Semaphore(self._config.max_concurrency)
        self._active_count = 0
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        self._total_acquired = 0
        self._total_rejected = 0

    @property
    def config(self) -> BulkheadConfig:
        """Get bulkhead configuration (read-only)."""
        return self._config

    @property
    def available_permits(self) -> int:
        """Get number of available permits."""
        # Estimate based on semaphore internal counter
        # This is approximate since threading.Semaphore doesn't expose this directly
        with self._lock:
            return max(0, self._config.max_concurrency - self._active_count)

    @property
    def active_count(self) -> int:
        """Get number of currently active operations."""
        with self._lock:
            return self._active_count

    @property
    def max_concurrency(self) -> int:
        """Get maximum concurrency setting."""
        return self._config.max_concurrency

    @property
    def total_acquired(self) -> int:
        """Get total number of successful permit acquisitions."""
        with self._lock:
            return self._total_acquired

    @property
    def total_rejected(self) -> int:
        """Get total number of rejected requests."""
        with self._lock:
            return self._total_rejected

    @property
    def utilization(self) -> float:
        """Get current utilization as percentage (0.0 to 1.0)."""
        with self._lock:
            if self._config.max_concurrency == 0:
                return 0.0
            return self._active_count / self._config.max_concurrency

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute function through bulkhead (synchronous).

        Acquires a permit before executing the function. If the bulkhead
        is at capacity and acquire_timeout is set, may raise BulkheadFullError.

        Args:
            func: Function to execute.
            *args: Positional arguments for function.
            **kwargs: Keyword arguments for function.

        Returns:
            Result of function execution.

        Raises:
            BulkheadFullError: If bulkhead is at capacity and timeout expires.
        """
        acquired = self._semaphore.acquire(timeout=self._config.acquire_timeout)

        if not acquired:
            with self._lock:
                self._total_rejected += 1
            raise BulkheadFullError(
                f"Bulkhead at capacity ({self._config.max_concurrency}), "
                f"timeout ({self._config.acquire_timeout}s) exceeded",
                max_concurrency=self._config.max_concurrency,
            )

        try:
            with self._lock:
                self._active_count += 1
                self._total_acquired += 1

            return func(*args, **kwargs)
        finally:
            with self._lock:
                self._active_count -= 1
            self._semaphore.release()

    async def aexecute(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Execute async function through bulkhead.

        Acquires a permit before executing the async function.

        Args:
            func: Async function to execute.
            *args: Positional arguments for function.
            **kwargs: Keyword arguments for function.

        Returns:
            Result of async function execution.

        Raises:
            BulkheadFullError: If bulkhead is at capacity and timeout expires.
        """
        try:
            if self._config.acquire_timeout is not None:
                # Use asyncio.wait_for for timeout
                await asyncio.wait_for(
                    self._async_semaphore.acquire(),
                    timeout=self._config.acquire_timeout,
                )
            else:
                await self._async_semaphore.acquire()
        except asyncio.TimeoutError:
            async with self._async_lock:
                self._total_rejected += 1
            raise BulkheadFullError(
                f"Bulkhead at capacity ({self._config.max_concurrency}), "
                f"timeout ({self._config.acquire_timeout}s) exceeded",
                max_concurrency=self._config.max_concurrency,
            )

        try:
            async with self._async_lock:
                self._active_count += 1
                self._total_acquired += 1

            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        finally:
            async with self._async_lock:
                self._active_count -= 1
            self._async_semaphore.release()

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator for wrapping functions with bulkhead protection.

        Args:
            func: Function to wrap.

        Returns:
            Wrapped function with bulkhead protection.
        """
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await self.aexecute(func, *args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> T:
                return self.execute(func, *args, **kwargs)
            return sync_wrapper

    def try_acquire(self) -> bool:
        """
        Try to acquire a permit without blocking.

        Returns:
            True if permit acquired, False if bulkhead is at capacity.
        """
        acquired = self._semaphore.acquire(blocking=False)
        if acquired:
            with self._lock:
                self._active_count += 1
                self._total_acquired += 1
        return acquired

    def release(self) -> None:
        """
        Release a permit manually.

        Should only be called if try_acquire() returned True and you're
        managing the permit lifecycle manually.
        """
        with self._lock:
            self._active_count -= 1
        self._semaphore.release()

    def __repr__(self) -> str:
        """Get string representation of bulkhead."""
        return (
            f"Bulkhead("
            f"max={self._config.max_concurrency}, "
            f"active={self._active_count}, "
            f"available={self.available_permits}"
            f")"
        )
