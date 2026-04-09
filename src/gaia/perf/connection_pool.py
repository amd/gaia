# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Connection pooling for LLM clients.

This module provides async connection pooling to reduce LLM client
creation overhead and improve throughput.

Features:
    - Configurable pool size (min/max)
    - Idle timeout for connection cleanup
    - Health checking before returning connections
    - Statistics tracking
    - Graceful shutdown

Example:
    >>> pool = ConnectionPool(
    ...     client_factory=lambda: LemonadeClient(model_id="Qwen3.5-35B"),
    ...     max_size=10,
    ...     min_size=2,
    ... )
    >>> async with pool.get_connection() as client:
    ...     response = await client.chat("Hello")
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TypeVar

from gaia.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar('T')


@dataclass
class PooledConnection:
    """
    Wrapper for pooled connections.

    This dataclass tracks metadata about each pooled connection
    including creation time, last usage, and use count.

    Attributes:
        client: The actual client instance
        created_at: Connection creation timestamp
        last_used_at: Last usage timestamp
        use_count: Number of times connection was used

    Example:
        >>> pooled = PooledConnection(client=my_client)
        >>> print(f"Use count: {pooled.use_count}")
    """
    client: Any
    created_at: float = field(default_factory=lambda: time.time())
    last_used_at: float = field(default_factory=lambda: time.time())
    use_count: int = 0


@dataclass
class PoolStatistics:
    """
    Connection pool statistics.

    This dataclass provides insights into pool performance and
    utilization for monitoring and debugging.

    Attributes:
        size: Current pool size (available connections)
        available: Number of available connections
        in_use: Connections currently in use
        created: Total connections created (lifetime)
        max_size: Maximum pool size
        min_size: Minimum pool size
        avg_acquire_time_ms: Average acquisition time in milliseconds

    Example:
        >>> stats = await pool.stats()
        >>> print(f"In use: {stats.in_use}, Available: {stats.available}")
    """
    size: int
    available: int
    in_use: int
    created: int
    max_size: int
    min_size: int
    avg_acquire_time_ms: float = 0.0

    def utilization(self) -> float:
        """
        Calculate pool utilization percentage.

        Returns:
            Utilization as percentage (0.0 to 100.0)

        Example:
            >>> stats = PoolStatistics(size=5, available=2, in_use=3,
            ...                        created=10, max_size=10, min_size=2)
            >>> print(f"Utilization: {stats.utilization():.1f}%")
        """
        if self.max_size == 0:
            return 0.0
        return (self.in_use / self.max_size) * 100.0

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"PoolStats(size={self.size}, available={self.available}, "
            f"in_use={self.in_use}, created={self.created}, "
            f"utilization={self.utilization():.1f}%)"
        )


class ConnectionPoolError(Exception):
    """Base exception for connection pool errors."""
    pass


class PoolExhaustedError(ConnectionPoolError):
    """Raised when pool is exhausted and cannot create new connections."""
    pass


class PoolClosedError(ConnectionPoolError):
    """Raised when attempting to use a closed pool."""
    pass


class ConnectionPool:
    """
    Async connection pool for LLM clients.

    Manages a pool of reusable connections with configurable sizing
    and idle timeouts. Optimized for high-throughput LLM client usage.

    Features:
        - Configurable min/max pool size
        - Idle timeout for connection cleanup
        - Health checking before returning connections
        - Statistics tracking
        - Graceful shutdown with timeout
        - Background health checker

    Attributes:
        max_size: Maximum pool size
        min_size: Minimum pool size (pre-created)
        max_idle_time: Max idle time before connection closed
        health_check_interval: How often to run health checks
        name: Pool name for logging

    Example:
        >>> pool = ConnectionPool(
        ...     client_factory=lambda: LemonadeClient(),
        ...     max_size=10,
        ...     min_size=2,
        ... )
        >>> await pool.initialize()
        >>> client = await pool.acquire()
        >>> try:
        ...     response = await client.chat("Hello")
        ... finally:
        ...     await pool.release(client)
        >>> await pool.close()
    """

    def __init__(
        self,
        client_factory: Callable[[], Any],
        max_size: int = 10,
        min_size: int = 2,
        max_idle_time: float = 300.0,
        health_check_interval: float = 60.0,
        name: str = "default",
    ):
        """
        Initialize connection pool.

        Args:
            client_factory: Factory function to create new clients
            max_size: Maximum pool size (default: 10)
            min_size: Minimum pool size - pre-created (default: 2)
            max_idle_time: Max idle time before connection closed in seconds (default: 300)
            health_check_interval: How often to run health checks in seconds (default: 60)
            name: Pool name for logging (default: "default")

        Raises:
            ValueError: If min_size > max_size or invalid parameters

        Example:
            >>> pool = ConnectionPool(
            ...     client_factory=lambda: LemonadeClient(model_id="Qwen3.5-35B"),
            ...     max_size=20,
            ...     min_size=5,
            ...     name="llm-pool"
            ... )
        """
        if min_size > max_size:
            raise ValueError(f"min_size ({min_size}) cannot exceed max_size ({max_size})")
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        if min_size < 0:
            raise ValueError("min_size must be non-negative")

        self.client_factory = client_factory
        self.max_size = max_size
        self.min_size = min_size
        self.max_idle_time = max_idle_time
        self.health_check_interval = health_check_interval
        self.name = name

        self._pool: asyncio.Queue[PooledConnection] = asyncio.Queue(maxsize=max_size)
        self._created = 0
        self._in_use = 0
        self._closed = False
        self._lock = asyncio.Lock()
        self._initialized = False
        self._health_check_task: Optional[asyncio.Task] = None

        # Statistics
        self._acquire_times: List[float] = []
        self._total_acquires = 0

        logger.info(f"ConnectionPool '{name}' created: min={min_size}, max={max_size}")

    async def initialize(self) -> None:
        """
        Initialize pool with minimum connections.

        Pre-creates min_size connections for immediate availability.
        This method should be called before first use, though acquire()
        will auto-initialize if needed.

        Example:
            >>> pool = ConnectionPool(...)
            >>> await pool.initialize()
            >>> # Pool now has min_size connections ready
        """
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            logger.info(f"Initializing connection pool '{self.name}'")

            for i in range(self.min_size):
                try:
                    client = await self._create_client()
                    pooled = PooledConnection(client=client)
                    await self._pool.put(pooled)
                    logger.debug(f"Pre-created connection {i + 1}/{self.min_size}")
                except Exception as e:
                    logger.error(f"Failed to pre-create connection: {e}")

            self._initialized = True
            logger.info(f"Connection pool '{self.name}' initialized")

    async def _create_client(self) -> Any:
        """
        Create new client using factory.

        Returns:
            New client instance

        Raises:
            Exception: If client factory fails
        """
        client = self.client_factory()

        # Handle async factory - check if result is a coroutine
        if asyncio.iscoroutine(client):
            client = await client

        self._created += 1
        logger.debug(f"Created new client (total: {self._created})")
        return client

    async def _destroy_client(self, client: Any) -> None:
        """
        Destroy client and cleanup resources.

        Args:
            client: Client instance to destroy
        """
        try:
            if hasattr(client, 'close'):
                if asyncio.iscoroutinefunction(client.close):
                    await client.close()
                else:
                    client.close()
            logger.debug("Destroyed client")
        except Exception as e:
            logger.warning(f"Error destroying client: {e}")

    async def _is_healthy(self, client: Any) -> bool:
        """
        Check if client is healthy.

        Override this method in subclasses for custom health checks.
        Default implementation checks for is_closed attribute.

        Args:
            client: Client instance to check

        Returns:
            True if client is healthy, False otherwise
        """
        # Default: assume healthy if has no is_closed attribute
        if hasattr(client, 'is_closed'):
            return not client.is_closed
        return True

    async def acquire(self, timeout: Optional[float] = None) -> Any:
        """
        Acquire connection from pool.

        This method attempts to get a connection from the pool. If the
        pool has available connections, it returns one after health
        checking. If the pool is empty but under max capacity, it
        creates a new connection. If at max capacity, it waits for
        a connection to be released.

        Args:
            timeout: Optional timeout in seconds (default: None - wait indefinitely)

        Returns:
            Client instance

        Raises:
            PoolExhaustedError: If pool exhausted and at max capacity
            PoolClosedError: If pool is closed
            ConnectionPoolError: If connection creation fails

        Example:
            >>> client = await pool.acquire()
            >>> try:
            ...     response = await client.chat("Hello")
            ... finally:
            ...     await pool.release(client)
        """
        if self._closed:
            raise PoolClosedError("Connection pool is closed")

        if not self._initialized:
            await self.initialize()

        start_time = time.time()

        # Try to get existing connection
        try:
            pooled = self._pool.get_nowait()

            # Check health and idle timeout
            now = time.time()
            is_healthy = await self._is_healthy(pooled.client)
            is_idle_expired = (now - pooled.last_used_at) > self.max_idle_time

            if is_healthy and not is_idle_expired:
                pooled.use_count += 1
                pooled.last_used_at = now
                async with self._lock:
                    self._in_use += 1
                    self._record_acquire_time(start_time)
                logger.debug(f"Acquired existing connection (use_count={pooled.use_count})")
                return pooled.client
            else:
                # Connection unhealthy or idle expired - destroy and create new
                logger.debug("Connection unhealthy/idle-expired, replacing")
                await self._destroy_client(pooled.client)
                self._created -= 1

        except asyncio.QueueEmpty:
            pass  # Pool empty, will create new if under limit

        # Create new connection if under max
        async with self._lock:
            if self._created < self.max_size:
                try:
                    client = await self._create_client()
                    # Health check for newly created client
                    if not await self._is_healthy(client):
                        logger.debug("Newly created connection unhealthy, destroying")
                        await self._destroy_client(client)
                        self._created -= 1
                        # Try again to create a healthy client
                        client = await self._create_client()
                    self._in_use += 1
                    self._record_acquire_time(start_time)
                    return client
                except Exception as e:
                    raise ConnectionPoolError(f"Failed to create connection: {e}") from e

            # At max capacity - wait for available connection
            logger.debug("Pool at capacity, waiting for available connection")

        try:
            pooled = await asyncio.wait_for(self._pool.get(), timeout=timeout)

            # Health check
            if not await self._is_healthy(pooled.client):
                logger.debug("Acquired unhealthy connection, replacing")
                await self._destroy_client(pooled.client)
                self._created -= 1
                client = await self._create_client()
                pooled.client = client

            pooled.use_count += 1
            pooled.last_used_at = time.time()
            async with self._lock:
                self._in_use += 1
                self._record_acquire_time(start_time)

            return pooled.client

        except asyncio.TimeoutError:
            raise PoolExhaustedError(
                f"Connection pool exhausted (max_size={self.max_size})"
            )

    async def release(self, client: Any) -> None:
        """
        Release connection back to pool.

        This method returns a connection to the pool for reuse.
        If the pool is at capacity, the connection is destroyed.

        Args:
            client: Client instance to release

        Example:
            >>> client = await pool.acquire()
            >>> try:
            ...     # Use client
            ...     pass
            ... finally:
            ...     await pool.release(client)
        """
        if self._closed:
            await self._destroy_client(client)
            return

        async with self._lock:
            self._in_use = max(0, self._in_use - 1)

        # Return to pool if not at capacity
        try:
            pooled = PooledConnection(client=client)
            self._pool.put_nowait(pooled)
            logger.debug("Released connection back to pool")
        except asyncio.QueueFull:
            logger.debug("Pool full, destroying excess connection")
            await self._destroy_client(client)
            self._created -= 1

    def get_connection(self):
        """
        Context manager for acquiring connection.

        Returns:
            _ConnectionContextManager instance

        Example:
            >>> async with pool.get_connection() as client:
            ...     response = await client.chat("Hello")
            >>> # Connection automatically released
        """
        return _ConnectionContextManager(self)

    def _record_acquire_time(self, start_time: float) -> None:
        """Record acquisition time for statistics."""
        elapsed_ms = (time.time() - start_time) * 1000
        self._acquire_times.append(elapsed_ms)
        self._total_acquires += 1

        # Keep last 100 measurements
        if len(self._acquire_times) > 100:
            self._acquire_times = self._acquire_times[-100:]

    async def stats(self) -> PoolStatistics:
        """
        Get pool statistics.

        Returns:
            PoolStatistics instance with current pool metrics

        Example:
            >>> stats = await pool.stats()
            >>> print(f"Utilization: {stats.utilization():.1f}%")
            >>> print(f"Avg acquire time: {stats.avg_acquire_time_ms:.2f}ms")
        """
        avg_acquire_time = (
            sum(self._acquire_times) / len(self._acquire_times)
            if self._acquire_times else 0.0
        )

        return PoolStatistics(
            size=self._pool.qsize(),
            available=self._pool.qsize(),
            in_use=self._in_use,
            created=self._created,
            max_size=self.max_size,
            min_size=self.min_size,
            avg_acquire_time_ms=round(avg_acquire_time, 2),
        )

    async def close(self) -> None:
        """
        Close pool and all connections.

        Waits for in-use connections to be returned before closing
        (with 30 second timeout). All connections are destroyed.

        Example:
            >>> await pool.close()
            >>> # Pool is now closed, cannot acquire/release
        """
        logger.info(f"Closing connection pool '{self.name}'")

        self._closed = True

        # Cancel health check task
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # Wait for in-use connections (with timeout)
        wait_start = time.time()
        while self._in_use > 0 and (time.time() - wait_start) < 30:
            await asyncio.sleep(0.5)

        # Destroy all pooled connections
        while not self._pool.empty():
            try:
                pooled = self._pool.get_nowait()
                await self._destroy_client(pooled.client)
            except asyncio.QueueEmpty:
                break

        self._created = 0
        self._in_use = 0
        logger.info(f"Connection pool '{self.name}' closed")

    async def start_health_checker(self) -> None:
        """
        Start background health check task.

        This spawns a background task that periodically checks
        idle connections and removes unhealthy or expired ones.

        Example:
            >>> await pool.start_health_checker()
            >>> # Health checker runs in background
        """
        async def health_check_loop():
            while not self._closed:
                try:
                    await asyncio.sleep(self.health_check_interval)

                    # Check idle connections
                    connections_to_check = []
                    while not self._pool.empty():
                        pooled = self._pool.get_nowait()
                        connections_to_check.append(pooled)

                    for pooled in connections_to_check:
                        is_healthy = await self._is_healthy(pooled.client)
                        is_idle_expired = (
                            time.time() - pooled.last_used_at
                        ) > self.max_idle_time

                        if is_healthy and not is_idle_expired:
                            await self._pool.put(pooled)
                        else:
                            logger.debug("Health check: removing unhealthy/idle connection")
                            await self._destroy_client(pooled.client)
                            self._created -= 1

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Health check error: {e}")

        self._health_check_task = asyncio.create_task(health_check_loop())
        logger.debug("Health checker started")

    def __repr__(self) -> str:
        """Return string representation."""
        status = "closed" if self._closed else "open"
        return f"ConnectionPool(name='{self.name}', {status}, created={self._created}, in_use={self._in_use})"


class _ConnectionContextManager:
    """
    Context manager for connection acquisition.

    This internal class provides async context manager support
    for automatic connection acquire/release.

    Example:
        >>> async with pool.get_connection() as client:
        ...     response = await client.chat("Hello")
    """

    def __init__(self, pool: ConnectionPool):
        """Initialize with pool reference."""
        self.pool = pool
        self.client = None

    async def __aenter__(self) -> Any:
        """Acquire connection on context entry."""
        self.client = await self.pool.acquire()
        return self.client

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Release connection on context exit."""
        if self.client:
            await self.pool.release(self.client)


# ==================== Default Pool Instances ====================

class PoolManager:
    """
    Manager for multiple connection pools.

    This class provides centralized management of multiple named
    connection pools, useful when working with different client types
    or configurations.

    Attributes:
        _pools: Dictionary of managed pools
        _lock: Async lock for thread safety

    Example:
        >>> manager = PoolManager()
        >>> manager.create_pool("lemonade", lambda: LemonadeClient())
        >>> manager.create_pool("claude", lambda: ClaudeClient())
        >>> client = await manager.get_client("lemonade")
        >>> await manager.release_client("lemonade", client)
    """

    def __init__(self):
        """Initialize pool manager."""
        self._pools: Dict[str, ConnectionPool] = {}
        self._lock = asyncio.Lock()

    def create_pool(
        self,
        name: str,
        client_factory: Callable,
        **kwargs,
    ) -> ConnectionPool:
        """
        Create a new connection pool.

        Args:
            name: Pool name (must be unique)
            client_factory: Factory function for creating clients
            **kwargs: Additional pool configuration options

        Returns:
            Created ConnectionPool instance

        Raises:
            ValueError: If pool name already exists

        Example:
            >>> pool = manager.create_pool(
            ...     "lemonade",
            ...     lambda: LemonadeClient(model_id="Qwen3.5-35B"),
            ...     max_size=20,
            ...     min_size=5,
            ... )
        """
        if name in self._pools:
            raise ValueError(f"Pool '{name}' already exists")

        pool = ConnectionPool(client_factory=client_factory, name=name, **kwargs)
        self._pools[name] = pool
        logger.info(f"Created pool '{name}'")
        return pool

    def get_pool(self, name: str) -> Optional[ConnectionPool]:
        """
        Get pool by name.

        Args:
            name: Pool name

        Returns:
            ConnectionPool instance or None if not found

        Example:
            >>> pool = manager.get_pool("lemonade")
        """
        return self._pools.get(name)

    def list_pools(self) -> List[str]:
        """
        List all registered pool names.

        Returns:
            List of pool names

        Example:
            >>> names = manager.list_pools()
            >>> print(names)
            ['lemonade', 'claude']
        """
        return list(self._pools.keys())

    async def get_client(self, name: str) -> Any:
        """
        Get client from named pool.

        Args:
            name: Pool name

        Returns:
            Client instance

        Raises:
            KeyError: If pool not found
            PoolExhaustedError: If pool exhausted

        Example:
            >>> client = await manager.get_client("lemonade")
        """
        pool = self._pools.get(name)
        if not pool:
            raise KeyError(f"Pool '{name}' not found")
        return await pool.acquire()

    async def release_client(self, name: str, client: Any) -> None:
        """
        Release client back to named pool.

        Args:
            name: Pool name
            client: Client instance to release

        Example:
            >>> client = await manager.get_client("lemonade")
            >>> try:
            ...     # Use client
            ...     pass
            ... finally:
            ...     await manager.release_client("lemonade", client)
        """
        pool = self._pools.get(name)
        if pool:
            await pool.release(client)

    async def close_all(self) -> None:
        """
        Close all pools.

        This method gracefully shuts down all managed pools.

        Example:
            >>> await manager.close_all()
        """
        for name, pool in list(self._pools.items()):
            logger.info(f"Closing pool '{name}'")
            await pool.close()
        self._pools.clear()

    def __repr__(self) -> str:
        """Return string representation."""
        pools = list(self._pools.keys())
        return f"PoolManager(pools={pools})"


# Module version
__version__ = "1.0.0"


def get_version() -> str:
    """Return the module version."""
    return __version__
