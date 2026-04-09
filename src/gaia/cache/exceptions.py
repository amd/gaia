# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Custom exceptions for the GAIA caching system.

Provides specific exception classes for cache-related errors,
enabling precise error handling and debugging.

Example:
    from gaia.cache import CacheError, CacheKeyError, CacheSerializationError

    try:
        await cache.get("missing_key")
    except CacheKeyError as e:
        print(f"Key not found: {e}")

    try:
        await cache.set("key", unserializable_object)
    except CacheSerializationError as e:
        print(f"Serialization failed: {e}")
"""

from typing import Any, Optional


class CacheError(Exception):
    """
    Base exception for all cache-related errors.

    All cache exceptions inherit from this base class,
    allowing unified exception handling for cache operations.

    Attributes:
        message: Error message describing the exception
        key: Optional cache key related to the error
        original_error: Optional original exception that caused this error
    """

    def __init__(
        self,
        message: str,
        key: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ):
        self.message = message
        self.key = key
        self.original_error = original_error
        super().__init__(self.message)

    def __str__(self) -> str:
        if self.key:
            return f"{self.__class__.__name__} (key={self.key}): {self.message}"
        return f"{self.__class__.__name__}: {self.message}"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, key={self.key!r})"


class CacheKeyError(CacheError):
    """
    Exception raised when a cache key is not found.

    Raised when attempting to retrieve a key that doesn't exist
    in the cache (memory or disk).

    Example:
        >>> try:
        ...     value = await cache.get("nonexistent")
        ...     if value is None:
        ...         raise CacheKeyError("Key not found", key="nonexistent")
        ... except CacheKeyError as e:
        ...     print(f"Cache miss: {e.key}")
    """

    pass


class CacheSerializationError(CacheError):
    """
    Exception raised when serialization/deserialization fails.

    Occurs when attempting to cache objects that cannot be
    serialized (e.g., functions, classes, circular references).

    Example:
        >>> try:
        ...     await cache.set("key", lambda x: x)  # Functions can't be serialized
        ... except CacheSerializationError as e:
        ...     print(f"Cannot cache: {e.original_error}")
    """

    pass


class CacheConnectionError(CacheError):
    """
    Exception raised when cache backend connection fails.

    Specific to disk cache (SQLite) connection issues,
    such as database lock, file permission, or corruption.

    Example:
        >>> try:
        ...     cache = DiskCache("/locked/database.db")
        ... except CacheConnectionError as e:
        ...     print(f"Database unavailable: {e}")
    """

    pass


class CacheConfigurationError(CacheError):
    """
    Exception raised for invalid cache configuration.

    Raised when cache is initialized with invalid parameters,
    such as negative max_size, invalid paths, or conflicting options.

    Example:
        >>> try:
        ...     cache = CacheLayer(memory_max_size=-100)  # Invalid
        ... except CacheConfigurationError as e:
        ...     print(f"Invalid config: {e}")
    """

    pass


class CacheTTLExpiredError(CacheError):
    """
    Exception raised when accessing an expired cache entry.

    Can be raised in strict mode when TTL expiration is detected
    during cache access (normally returns None/default instead).

    Example:
        >>> try:
        ...     value = await cache.get("expired_key", strict=True)
        ... except CacheTTLExpiredError as e:
        ...     print(f"Entry expired: {e.key}")
    """

    pass


class CacheEvictionError(CacheError):
    """
    Exception raised when cache eviction fails.

    Occurs during LRU/LFU eviction process if removal fails
    due to locks, concurrent modification, or internal errors.

    Example:
        >>> try:
        ...     await cache.set("new_key", value)  # Triggers eviction
        ... except CacheEvictionError as e:
        ...     print(f"Eviction failed: {e}")
    """

    pass


class CacheStatsError(CacheError):
    """
    Exception raised for cache statistics errors.

    Raised when computing or accessing cache statistics fails,
    such as division by zero in hit rate calculations.

    Example:
        >>> try:
        ...     stats = cache.stats()
        ... except CacheStatsError as e:
        ...     print(f"Stats unavailable: {e}")
    """

    pass


class CacheDecoratorError(CacheError):
    """
    Exception raised when @cached decorator fails.

    Occurs when the cached decorator encounters errors during
    key generation, cache access, or result storage.

    Example:
        >>> @cached(cache=my_cache)
        ... async def fetch_data(key: str) -> dict:
        ...     ...
        >>> try:
        ...     await fetch_data("data")
        ... except CacheDecoratorError as e:
        ...     print(f"Decorator error: {e}")
    """

    pass


# Exception aliases for backwards compatibility
CacheMissError = CacheKeyError
CacheTimeoutError = CacheTTLExpiredError
