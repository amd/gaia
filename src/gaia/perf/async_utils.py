# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Async utilities for standardized async/await patterns.

This module provides:
- Async caching decorators
- Rate limiting utilities
- Retry logic with backoff
- Timeout wrappers
- Async bounded executors
- Debounce and throttle decorators

Example:
    >>> @async_cached(ttl_seconds=300)
    ... async def get_llm_response(prompt):
    ...     return await client.chat(prompt)
"""

import asyncio
import functools
import logging
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

from gaia.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar('T')
R = TypeVar('R')


# ==================== Caching Decorators ====================

def async_cached(ttl_seconds: int = 300, key_func: Optional[Callable] = None):
    """
    Decorator for async function caching.

    Caches the result of async function calls for a specified TTL.
    Cache keys are generated from function name and arguments, or
    from a custom key function if provided.

    Args:
        ttl_seconds: Cache time-to-live in seconds (default: 300)
        key_func: Optional function to generate cache key from arguments

    Returns:
        Decorated async function with caching

    Example:
        >>> @async_cached(ttl_seconds=600)
        ... async def get_response(prompt: str) -> str:
        ...     return await llm.chat(prompt)
        >>> # First call - cache miss
        >>> result1 = await get_response("Hello")
        >>> # Second call - cache hit (within 600 seconds)
        >>> result2 = await get_response("Hello")
        >>> assert result1 == result2

    Notes:
        - Cache is stored in memory and not persisted
        - Each decorated function has its own cache
        - Use cache_clear() and cache_info() methods on wrapped function
    """
    cache: Dict[str, tuple] = {}
    _lock = asyncio.Lock()

    def make_key(func: Callable, args: tuple, kwargs: dict) -> str:
        """Generate cache key from function and arguments."""
        if key_func:
            return key_func(*args, **kwargs)
        key_parts = [func.__qualname__]
        key_parts.extend(str(a) for a in args)
        key_parts.extend(f"{k}={v!r}" for k, v in sorted(kwargs.items()))
        return ":".join(key_parts)

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            key = make_key(func, args, kwargs)

            async with _lock:
                if key in cache:
                    result, timestamp = cache[key]
                    elapsed = time.time() - timestamp
                    if elapsed < ttl_seconds:
                        logger.debug(f"Cache hit for {key} (age={elapsed:.1f}s)")
                        return result
                    else:
                        logger.debug(f"Cache expired for {key} (age={elapsed:.1f}s)")
                        del cache[key]

            result = await func(*args, **kwargs)

            async with _lock:
                cache[key] = (result, time.time())
                logger.debug(f"Cached result for {key}")

            return result

        def cache_clear() -> None:
            """Clear all cached entries."""
            cache.clear()

        def cache_info() -> Dict[str, Any]:
            """Get cache statistics."""
            now = time.time()
            stats = {
                "size": len(cache),
                "entries": {},
            }
            for key, (_, timestamp) in cache.items():
                age = now - timestamp
                stats["entries"][key] = {
                    "age_seconds": round(age, 2),
                    "expired": age > ttl_seconds,
                }
            return stats

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        wrapper.cache_info = cache_info  # type: ignore[attr-defined]

        return wrapper

    return decorator


# ==================== Rate Limiting ====================

class AsyncRateLimiter:
    """
    Async rate limiter using token bucket algorithm.

    This class implements a token bucket rate limiter that can be used
    to control the rate of async operations. Tokens are added at a
    constant rate up to a maximum capacity.

    Attributes:
        rate: Tokens added per second
        capacity: Maximum token capacity

    Example:
        >>> limiter = AsyncRateLimiter(rate=10, capacity=20)
        >>> async with limiter:
        ...     await make_api_call()
        >>> # Or use acquire directly
        >>> await limiter.acquire()
        >>> await make_api_call()

    Notes:
        - Thread-safe for concurrent async operations
        - Automatically refills tokens based on elapsed time
        - Blocks until tokens are available
    """

    def __init__(self, rate: float, capacity: int):
        """
        Initialize rate limiter.

        Args:
            rate: Tokens per second (refill rate)
            capacity: Maximum token capacity (bucket size)

        Raises:
            ValueError: If rate or capacity is invalid

        Example:
            >>> limiter = AsyncRateLimiter(rate=10.0, capacity=100)
            >>> print(limiter.rate, limiter.capacity)
            10.0 100
        """
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")

        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self._last_update = time.time()
        self._lock = asyncio.Lock()
        self._waiters: List[asyncio.Future] = []

    async def acquire(self, tokens: int = 1) -> None:
        """
        Acquire tokens (wait if necessary).

        This method attempts to acquire the specified number of tokens.
        If insufficient tokens are available, it waits until enough
        tokens have been refilled.

        Args:
            tokens: Number of tokens to acquire (default: 1)

        Raises:
            ValueError: If tokens is not positive

        Example:
            >>> limiter = AsyncRateLimiter(rate=10, capacity=20)
            >>> await limiter.acquire(5)  # Acquire 5 tokens
        """
        if tokens <= 0:
            raise ValueError("tokens must be positive")

        async with self._lock:
            now = time.time()
            elapsed = now - self._last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self._last_update = now

            if self.tokens < tokens:
                wait_time = (tokens - self.tokens) / self.rate
                logger.debug(f"Rate limit: waiting {wait_time:.2f}s for {tokens} tokens")
                # Release lock during wait
                self._lock.release()
                try:
                    await asyncio.sleep(wait_time)
                finally:
                    await self._lock.acquire()
                # Update tokens after wait
                now = time.time()
                elapsed = now - self._last_update
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self._last_update = now
            else:
                self.tokens -= tokens

    async def __aenter__(self) -> "AsyncRateLimiter":
        """Acquire token on context manager entry."""
        await self.acquire()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """No cleanup needed on exit."""
        pass

    @property
    def available_tokens(self) -> float:
        """
        Get current available tokens.

        Returns:
            Current token count (may be fractional)

        Example:
            >>> limiter = AsyncRateLimiter(rate=10, capacity=100)
            >>> print(f"Available: {limiter.available_tokens:.1f}")
        """
        now = time.time()
        elapsed = now - self._last_update
        return min(self.capacity, self.tokens + elapsed * self.rate)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"AsyncRateLimiter(rate={self.rate}, capacity={self.capacity}, tokens={self.available_tokens:.1f})"


# ==================== Retry Logic ====================

def async_retry(
    retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    logger_func: Optional[Callable] = None,
):
    """
    Decorator for async function retry with exponential backoff.

    Retries the decorated function on specified exceptions with
    exponential backoff between attempts.

    Args:
        retries: Maximum retry attempts (default: 3)
        delay: Initial delay in seconds (default: 1.0)
        backoff: Backoff multiplier (default: 2.0)
        exceptions: Exception types to catch (default: Exception)
        logger_func: Optional logger function for retry messages

    Returns:
        Decorated async function with retry logic

    Example:
        >>> @async_retry(retries=3, delay=1.0, backoff=2.0)
        ... async def flaky_api_call():
        ...     return await api.request()
        >>> # Will retry up to 3 times with delays: 1s, 2s, 4s

    Notes:
        - Delay between retries: delay * (backoff ** attempt)
        - All specified exception types are caught
        - Uses provided logger_func or falls back to module logger
    """
    log_func = logger_func or logger.warning

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            current_delay = delay

            for attempt in range(retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < retries:
                        log_func(
                            f"Retry {attempt + 1}/{retries} for {func.__name__} after: {e}"
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(f"All {retries} retries exhausted for {func.__name__}")

            if last_exception is not None:
                raise last_exception
            raise RuntimeError("Unexpected state in async_retry")

        return wrapper
    return decorator


# ==================== Timeout Wrapper ====================

def async_timeout(seconds: float):
    """
    Decorator for async function timeout.

    Wraps an async function to enforce a maximum execution time.
    Raises asyncio.TimeoutError if the function exceeds the timeout.

    Args:
        seconds: Timeout in seconds

    Returns:
        Decorated async function with timeout

    Raises:
        asyncio.TimeoutError: If function execution exceeds timeout

    Example:
        >>> @async_timeout(30.0)
        ... async def slow_operation():
        ...     await asyncio.sleep(100)
        >>> try:
        ...     await slow_operation()
        ... except asyncio.TimeoutError:
        ...     print("Operation timed out")

    Notes:
        - Uses asyncio.wait_for internally
        - Timeout applies to total execution time
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=seconds,
                )
            except asyncio.TimeoutError:
                logger.error(f"Operation {func.__name__} timed out after {seconds}s")
                raise

        return wrapper
    return decorator


# ==================== Semaphore Bounded Concurrency ====================

class AsyncBoundedExecutor:
    """
    Async executor with bounded concurrency.

    This class provides controlled concurrent execution using a
    semaphore to limit the number of simultaneous operations.

    Attributes:
        max_concurrent: Maximum concurrent operations

    Example:
        >>> executor = AsyncBoundedExecutor(max_concurrent=5)
        >>> results = await executor.map(process_item, items)
        >>> # Or submit individual tasks
        >>> task = await executor.submit(process_item, item)
        >>> result = await task

    Notes:
        - Prevents resource exhaustion from too many concurrent operations
        - Tasks are scheduled immediately but may wait for semaphore
        - Use wait_all() to wait for all submitted tasks
    """

    def __init__(self, max_concurrent: int = 10):
        """
        Initialize bounded executor.

        Args:
            max_concurrent: Maximum concurrent operations (default: 10)

        Raises:
            ValueError: If max_concurrent is not positive

        Example:
            >>> executor = AsyncBoundedExecutor(max_concurrent=5)
        """
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")

        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: List[asyncio.Task] = []

    async def submit(self, coro: Any) -> asyncio.Task:
        """
        Submit coroutine for execution.

        Args:
            coro: Coroutine to execute

        Returns:
            asyncio.Task for the submitted coroutine

        Example:
            >>> executor = AsyncBoundedExecutor(max_concurrent=5)
            >>> task = await executor.submit(process_item(item))
            >>> result = await task
        """
        async def wrapped():
            async with self._semaphore:
                return await coro

        task = asyncio.create_task(wrapped())
        self._tasks.append(task)
        return task

    async def map(
        self,
        func: Callable[..., T],
        items: List[Any],
        *args,
        **kwargs,
    ) -> List[T]:
        """
        Map function over items with bounded concurrency.

        Applies the async function to all items concurrently, limited
        by the semaphore.

        Args:
            func: Async function to apply
            items: Items to process
            *args: Additional positional arguments for func
            **kwargs: Additional keyword arguments for func

        Returns:
            List of results in same order as items

        Example:
            >>> executor = AsyncBoundedExecutor(max_concurrent=5)
            >>> results = await executor.map(process, [1, 2, 3, 4, 5])
        """
        tasks = [await self.submit(func(item, *args, **kwargs)) for item in items]
        return await asyncio.gather(*tasks)

    async def wait_all(self, return_exceptions: bool = False) -> List[Any]:
        """
        Wait for all submitted tasks.

        Args:
            return_exceptions: If True, exceptions are returned in results

        Returns:
            List of results (or exceptions if return_exceptions=True)

        Example:
            >>> executor = AsyncBoundedExecutor()
            >>> await executor.submit(task1())
            >>> await executor.submit(task2())
            >>> results = await executor.wait_all()
        """
        results = await asyncio.gather(*self._tasks, return_exceptions=return_exceptions)
        self._tasks.clear()
        return results

    def get_pending_count(self) -> int:
        """
        Get number of pending tasks.

        Returns:
            Number of tasks not yet completed

        Example:
            >>> executor = AsyncBoundedExecutor()
            >>> await executor.submit(long_task())
            >>> print(f"Pending: {executor.get_pending_count()}")
        """
        return sum(1 for task in self._tasks if not task.done())

    def __repr__(self) -> str:
        """Return string representation."""
        pending = self.get_pending_count()
        return f"AsyncBoundedExecutor(pending={pending})"


# ==================== Debounce/Throttle ====================

def async_debounce(wait: float = 0.5, leading: bool = False):
    """
    Decorator for debouncing async function calls.

    Debouncing ensures that a function is only executed after a
    specified wait time has passed since the last call. Useful for
    rate-limiting user input or rapid events.

    Args:
        wait: Wait time in seconds before executing (default: 0.5)
        leading: If True, execute on leading edge instead of trailing

    Returns:
        Decorated async function with debouncing

    Example:
        >>> @async_debounce(wait=0.5)
        ... async def save_document(content):
        ...     await db.save(content)
        >>> # Rapid calls will only execute once after 0.5s pause
        >>> await save_document("text1")  # Cancelled by next call
        >>> await save_document("text2")  # Cancelled by next call
        >>> await save_document("text3")  # Will execute after 0.5s

    Notes:
        - Cancels previous pending call on new invocation
        - trailing=True (default): executes after wait period
        - leading=True: executes immediately, then ignores during wait
    """
    def decorator(func: Callable) -> Callable:
        _task: Optional[asyncio.Task] = None
        _leading_cooldown: Optional[float] = None

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal _task, _leading_cooldown

            if leading:
                # Execute immediately if not in cooldown
                now = time.time()
                if _leading_cooldown is None or (now - _leading_cooldown) >= wait:
                    _leading_cooldown = now
                    return await func(*args, **kwargs)
                # In cooldown period, ignore call
                return None

            # Trailing debounce (default)
            if _task and not _task.done():
                _task.cancel()
                try:
                    await _task
                except asyncio.CancelledError:
                    pass

            async def delayed_call():
                await asyncio.sleep(wait)
                return await func(*args, **kwargs)

            _task = asyncio.create_task(delayed_call())
            return await _task

        async def cancel() -> None:
            """Cancel any pending execution."""
            nonlocal _task
            if _task and not _task.done():
                _task.cancel()
                try:
                    await _task
                except asyncio.CancelledError:
                    pass

        wrapper.cancel = cancel  # type: ignore[attr-defined]

        return wrapper

    return decorator


def async_throttle(period: float):
    """
    Decorator for throttling async function calls.

    Throttling ensures that a function is executed at most once
    per specified time period. Calls within the period are delayed.

    Args:
        period: Minimum time between calls in seconds

    Returns:
        Decorated async function with throttling

    Example:
        >>> @async_throttle(period=1.0)
        ... async def api_call():
        ...     return await api.request()
        >>> # Calls are spaced at least 1 second apart
        >>> await api_call()  # Executes immediately
        >>> await api_call()  # Waits until 1s has passed

    Notes:
        - Uses locking to ensure thread safety
        - First call executes immediately
        - Subsequent calls wait if within throttle period
    """
    def decorator(func: Callable) -> Callable:
        _last_call = 0.0
        _lock = asyncio.Lock()

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal _last_call
            async with _lock:
                now = time.time()
                elapsed = now - _last_call
                if elapsed < period:
                    wait_time = period - elapsed
                    logger.debug(f"Throttle: waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
                _last_call = time.time()
            return await func(*args, **kwargs)

        return wrapper

    return decorator


# ==================== Utility Functions ====================

async def async_gather_with_concurrency(
    max_concurrent: int,
    *coros,
    return_exceptions: bool = False,
) -> List[Any]:
    """
    Gather coroutines with limited concurrency.

    Similar to asyncio.gather but limits concurrent execution.

    Args:
        max_concurrent: Maximum concurrent coroutines
        *coros: Coroutines to execute
        return_exceptions: If True, exceptions are returned in results

    Returns:
        List of results in same order as coros

    Example:
        >>> results = await async_gather_with_concurrency(
        ...     5, task1(), task2(), task3(), task4(), task5()
        ... )

    Notes:
        - Results maintain original order regardless of completion order
        - More efficient than creating AsyncBoundedExecutor for one-off use
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def wrapped(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*[wrapped(c) for c in coros], return_exceptions=return_exceptions)


def create_key(*args, **kwargs) -> str:
    """
    Create a cache key from arguments.

    Utility function for generating consistent cache keys.

    Args:
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        String cache key

    Example:
        >>> key = create_key("user", 123, sort="name")
        >>> print(key)
        user:123:sort=name
    """
    parts = [str(a) for a in args]
    parts.extend(f"{k}={v!r}" for k, v in sorted(kwargs.items()))
    return ":".join(parts)


# Module version
__version__ = "1.0.0"


def get_version() -> str:
    """Return the module version."""
    return __version__
