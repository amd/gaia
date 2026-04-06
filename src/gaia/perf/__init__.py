# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Performance utilities for GAIA.

This module provides performance optimization utilities including:
- Async caching, rate limiting, retry, and timeout decorators
- Connection pooling for LLM clients
- Bounded concurrency executors

Example:
    >>> from gaia.perf import async_cached, AsyncRateLimiter, ConnectionPool
    >>> @async_cached(ttl_seconds=300)
    ... async def cached_operation():
    ...     return await expensive_call()
"""

from gaia.perf.async_utils import (
    async_cached,
    async_retry,
    async_timeout,
    async_debounce,
    async_throttle,
    AsyncRateLimiter,
    AsyncBoundedExecutor,
    async_gather_with_concurrency,
    create_key,
)
from gaia.perf.connection_pool import (
    ConnectionPool,
    PoolManager,
    PoolStatistics,
    PooledConnection,
    ConnectionPoolError,
    PoolExhaustedError,
    PoolClosedError,
)

__all__ = [
    # Async utilities
    "async_cached",
    "async_retry",
    "async_timeout",
    "async_debounce",
    "async_throttle",
    "AsyncRateLimiter",
    "AsyncBoundedExecutor",
    "async_gather_with_concurrency",
    "create_key",
    # Connection pooling
    "ConnectionPool",
    "PoolManager",
    "PoolStatistics",
    "PooledConnection",
    "ConnectionPoolError",
    "PoolExhaustedError",
    "PoolClosedError",
]

# Module version
__version__ = "1.0.0"


def get_version() -> str:
    """Return the module version."""
    return __version__
