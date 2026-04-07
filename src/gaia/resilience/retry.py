"""
Retry with Exponential Backoff Implementation.

This module provides a retry decorator and execution mechanism with
configurable exponential backoff and jitter to prevent thundering herd
problems during failure recovery.

The retry pattern helps handle transient failures by automatically
retrying failed operations with increasing delays between attempts.
"""

from __future__ import annotations

import asyncio
import functools
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple, TypeVar, Union

T = TypeVar("T")


class RetryError(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(
        self,
        message: str = "All retry attempts exhausted",
        last_exception: Optional[Exception] = None,
        attempts: int = 0,
    ):
        super().__init__(message)
        self.last_exception = last_exception
        self.attempts = attempts


@dataclass(frozen=True)
class RetryConfig:
    """
    Configuration for Retry with exponential backoff.

    Attributes:
        max_retries: Maximum number of retry attempts (not including initial).
                    Default: 3
        base_delay: Base delay in seconds for exponential backoff.
                   Default: 1.0
        max_delay: Maximum delay cap in seconds to prevent excessive waits.
                  Default: 60.0
        jitter: Whether to add random jitter to delays.
               Default: True
        jitter_factor: Factor for jitter calculation (0.0 to 1.0).
                      Jitter will be +/- jitter_factor * delay.
                      Default: 0.1 (10%)
        retryable_exceptions: Exception types that trigger retry.
                            Default: (Exception,) - all exceptions
        on_retry: Optional callback called on each retry attempt.
                 Signature: (attempt: int, exception: Exception, delay: float) -> None
                 Default: None
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True
    jitter_factor: float = 0.1
    retryable_exceptions: tuple = field(default_factory=lambda: (Exception,))
    on_retry: Optional[Callable[[int, Exception, float], None]] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.base_delay <= 0:
            raise ValueError("base_delay must be > 0")
        if self.max_delay <= 0:
            raise ValueError("max_delay must be > 0")
        if self.base_delay > self.max_delay:
            raise ValueError("base_delay must be <= max_delay")
        if not 0 <= self.jitter_factor <= 1:
            raise ValueError("jitter_factor must be between 0 and 1")

    def calculate_delay(self, attempt: int) -> float:
        """
        Calculate delay for a given attempt with exponential backoff.

        Args:
            attempt: Current attempt number (1-based).

        Returns:
            Delay in seconds with optional jitter applied.
        """
        # Exponential backoff: base_delay * 2^(attempt-1)
        delay = self.base_delay * (2 ** (attempt - 1))

        # Apply max delay cap
        delay = min(delay, self.max_delay)

        # Apply jitter if enabled
        if self.jitter:
            jitter_range = delay * self.jitter_factor
            delay = delay + random.uniform(-jitter_range, jitter_range)
            # Ensure delay is never negative after jitter
            delay = max(0, delay)

        return delay


def retry(
    config: Optional[RetryConfig] = None,
    *,
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: Optional[float] = None,
    jitter: Optional[bool] = None,
    jitter_factor: Optional[float] = None,
    retryable_exceptions: Optional[Tuple[type, ...]] = None,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for adding retry with exponential backoff to functions.

    Can be used with a RetryConfig or by passing individual parameters.

    Args:
        config: Retry configuration object.
        max_retries: Maximum retry attempts (overrides config if provided).
        base_delay: Base delay in seconds (overrides config if provided).
        max_delay: Maximum delay cap (overrides config if provided).
        jitter: Enable jitter (overrides config if provided).
        jitter_factor: Jitter factor (overrides config if provided).
        retryable_exceptions: Exceptions that trigger retry (overrides config).
        on_retry: Callback on retry (overrides config).

    Returns:
        Decorated function with retry behavior.

    Example usage:
        >>> @retry(max_retries=3, base_delay=1.0)
        >>> def flaky_api_call():
        ...     return requests.get(url)
        >>>
        >>> # With custom exception handling
        >>> @retry(
        ...     max_retries=5,
        ...     retryable_exceptions=(ConnectionError, TimeoutError)
        ... )
        >>> def network_operation():
        ...     ...
        >>>
        >>> # Async functions
        >>> @retry(max_retries=3)
        >>> async def async_operation():
        ...     ...
    """
    # Build configuration
    if config is not None:
        # Use provided config, but allow parameter overrides
        kwargs = {}
        if max_retries is not None:
            kwargs["max_retries"] = max_retries
        if base_delay is not None:
            kwargs["base_delay"] = base_delay
        if max_delay is not None:
            kwargs["max_delay"] = max_delay
        if jitter is not None:
            kwargs["jitter"] = jitter
        if jitter_factor is not None:
            kwargs["jitter_factor"] = jitter_factor
        if retryable_exceptions is not None:
            kwargs["retryable_exceptions"] = retryable_exceptions
        if on_retry is not None:
            kwargs["on_retry"] = on_retry

        if kwargs:
            config = RetryConfig(**{**config.__dict__, **kwargs})
    else:
        # Create new config from parameters
        # Note: Use explicit None checks to allow 0 values for max_retries
        config = RetryConfig(
            max_retries=3 if max_retries is None else max_retries,
            base_delay=1.0 if base_delay is None else base_delay,
            max_delay=60.0 if max_delay is None else max_delay,
            jitter=jitter if jitter is not None else True,
            jitter_factor=jitter_factor if jitter_factor is not None else 0.1,
            retryable_exceptions=retryable_exceptions if retryable_exceptions is not None else (Exception,),
            on_retry=on_retry,
        )

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await _retry_async(func, config, *args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> T:
                return _retry_sync(func, config, *args, **kwargs)
            return sync_wrapper

    return decorator


def _retry_sync(
    func: Callable[..., T],
    config: RetryConfig,
    *args: Any,
    **kwargs: Any,
) -> T:
    """
    Execute function with retry logic (synchronous).

    Args:
        func: Function to execute.
        config: Retry configuration.
        *args: Positional arguments for function.
        **kwargs: Keyword arguments for function.

    Returns:
        Result of function execution.

    Raises:
        RetryError: When all retry attempts are exhausted.
    """
    last_exception: Optional[Exception] = None

    for attempt in range(config.max_retries + 1):  # +1 for initial attempt
        try:
            return func(*args, **kwargs)
        except config.retryable_exceptions as e:
            last_exception = e

            if attempt >= config.max_retries:
                # No more retries
                break

            # Calculate delay
            delay = config.calculate_delay(attempt + 1)

            # Call on_retry callback if provided
            if config.on_retry:
                config.on_retry(attempt + 1, e, delay)

            # Wait before retry
            time.sleep(delay)

    raise RetryError(
        message=f"All {config.max_retries + 1} attempts failed",
        last_exception=last_exception,
        attempts=config.max_retries + 1,
    )


async def _retry_async(
    func: Callable[..., Any],
    config: RetryConfig,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """
    Execute async function with retry logic.

    Args:
        func: Async function to execute.
        config: Retry configuration.
        *args: Positional arguments for function.
        **kwargs: Keyword arguments for function.

    Returns:
        Result of async function execution.

    Raises:
        RetryError: When all retry attempts are exhausted.
    """
    last_exception: Optional[Exception] = None

    for attempt in range(config.max_retries + 1):  # +1 for initial attempt
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)
        except config.retryable_exceptions as e:
            last_exception = e

            if attempt >= config.max_retries:
                # No more retries
                break

            # Calculate delay
            delay = config.calculate_delay(attempt + 1)

            # Call on_retry callback if provided
            if config.on_retry:
                config.on_retry(attempt + 1, e, delay)

            # Wait before retry
            await asyncio.sleep(delay)

    raise RetryError(
        message=f"All {config.max_retries + 1} attempts failed",
        last_exception=last_exception,
        attempts=config.max_retries + 1,
    )


class RetryExecutor:
    """
    Alternative class-based executor for retry operations.

    Provides more flexibility for dynamic configuration and
    programmatic retry control.

    Example usage:
        >>> executor = RetryExecutor(max_retries=3, base_delay=1.0)
        >>> result = executor.execute(risky_operation)
        >>> result = await executor.aexecute(async_risky_operation)
    """

    def __init__(self, config: Optional[RetryConfig] = None):
        """
        Initialize RetryExecutor.

        Args:
            config: Retry configuration. Uses defaults if None.
        """
        self._config = config or RetryConfig()

    @property
    def config(self) -> RetryConfig:
        """Get retry configuration."""
        return self._config

    def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute function with retry (synchronous).

        Args:
            func: Function to execute.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Result of function execution.

        Raises:
            RetryError: When all retries exhausted.
        """
        return _retry_sync(func, self._config, *args, **kwargs)

    async def aexecute(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Execute async function with retry.

        Args:
            func: Async function to execute.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Result of async function execution.

        Raises:
            RetryError: When all retries exhausted.
        """
        return await _retry_async(func, self._config, *args, **kwargs)
