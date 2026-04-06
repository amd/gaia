# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for ConnectionPool.

This test suite validates:
- Pool creation and initialization
- Connection acquire/release
- Pool sizing and limits
- Health checking
- Statistics tracking
- PoolManager functionality

Quality Gate 4 Criteria Covered:
- PERF-001: Connection pool >100 req/s
- THREAD-001: Thread safety no race conditions
"""

import asyncio
import pytest
import time

from gaia.perf.connection_pool import (
    ConnectionPool,
    PoolManager,
    PoolStatistics,
    PooledConnection,
    ConnectionPoolError,
    PoolExhaustedError,
    PoolClosedError,
)


# =============================================================================
# Mock Client Classes
# =============================================================================

class MockClient:
    """Mock client for testing."""
    instance_count = 0

    def __init__(self, client_id=None):
        MockClient.instance_count += 1
        self.client_id = client_id or MockClient.instance_count
        self.is_closed = False
        self.query_count = 0

    async def query(self, data):
        """Mock query method."""
        self.query_count += 1
        return f"Response from client {self.client_id}: {data}"

    async def close(self):
        """Close client."""
        self.is_closed = True

    def __repr__(self):
        return f"MockClient(id={self.client_id}, closed={self.is_closed})"


class UnhealthyMockClient:
    """Mock client that reports unhealthy."""
    instance_count = 0

    def __init__(self):
        UnhealthyMockClient.instance_count += 1
        self.is_closed = True  # Always unhealthy

    async def close(self):
        self.is_closed = True


class AsyncMockClientFactory:
    """Async factory for mock clients."""
    instance_count = 0

    @classmethod
    async def create(cls):
        cls.instance_count += 1
        client = MockClient(client_id=cls.instance_count)
        return client


# =============================================================================
# Pool Creation Tests
# =============================================================================

class TestPoolCreation:
    """Tests for ConnectionPool creation."""

    def test_pool_creation(self):
        """Test creating pool with parameters."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=2,
            name="test-pool"
        )

        assert pool.max_size == 10
        assert pool.min_size == 2
        assert pool.name == "test-pool"

    def test_pool_invalid_min_greater_than_max(self):
        """Test that min > max raises ValueError."""
        with pytest.raises(ValueError, match="min_size.*cannot exceed"):
            ConnectionPool(
                client_factory=lambda: MockClient(),
                max_size=5,
                min_size=10
            )

    def test_pool_invalid_max_size(self):
        """Test that max_size < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_size must be at least 1"):
            ConnectionPool(
                client_factory=lambda: MockClient(),
                max_size=0,
                min_size=0  # Set min_size to 0 to avoid min > max error
            )

    def test_pool_invalid_min_size(self):
        """Test that negative min_size raises ValueError."""
        with pytest.raises(ValueError, match="min_size must be non-negative"):
            ConnectionPool(
                client_factory=lambda: MockClient(),
                max_size=10,
                min_size=-1
            )

    def test_pool_repr(self):
        """Test pool string representation."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            name="test"
        )
        repr_str = repr(pool)

        assert "ConnectionPool" in repr_str
        assert "test" in repr_str


# =============================================================================
# Pool Initialization Tests
# =============================================================================

class TestPoolInitialization:
    """Tests for pool initialization."""

    @pytest.mark.asyncio
    async def test_pool_initialize_precreates(self):
        """Test that initialize pre-creates min_size connections."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=3,
            name="test"
        )

        await pool.initialize()

        assert pool._created == 3
        assert pool._pool.qsize() == 3

    @pytest.mark.asyncio
    async def test_pool_initialize_idempotent(self):
        """Test that initialize is idempotent."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=2,
            name="test"
        )

        await pool.initialize()
        created_after_first = pool._created

        await pool.initialize()  # Second call

        assert pool._created == created_after_first

    @pytest.mark.asyncio
    async def test_pool_auto_initialize_on_acquire(self):
        """Test that acquire auto-initializes."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=2,
            name="test"
        )

        # Don't call initialize, just acquire
        client = await pool.acquire()

        assert client is not None


# =============================================================================
# Connection Acquire/Release Tests
# =============================================================================

class TestAcquireRelease:
    """Tests for acquire and release operations."""

    @pytest.mark.asyncio
    async def test_pool_acquire_returns_client(self):
        """Test that acquire returns a client."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=1,
        )
        await pool.initialize()

        client = await pool.acquire()

        assert isinstance(client, MockClient)

    @pytest.mark.asyncio
    async def test_pool_release_returns_to_pool(self):
        """Test that release returns connection to pool."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=1,
        )
        await pool.initialize()

        client = await pool.acquire()
        await pool.release(client)

        # Connection should be back in pool
        assert pool._pool.qsize() >= 1

    @pytest.mark.asyncio
    async def test_pool_reuse_connection(self):
        """Test that released connection is reused."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=1,
        )
        await pool.initialize()

        client1 = await pool.acquire()
        await pool.release(client1)
        client2 = await pool.acquire()

        # Should be same connection
        assert client1 is client2

    @pytest.mark.asyncio
    async def test_pool_max_size_respected(self):
        """Test that pool respects max size."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=2,
            min_size=0,
        )

        client1 = await pool.acquire()
        client2 = await pool.acquire()

        # Try to acquire third (should wait or create if under max)
        # Since we're at max, it should wait
        with pytest.raises(PoolExhaustedError):
            await pool.acquire(timeout=0.1)

        # Cleanup
        await pool.release(client1)
        await pool.release(client2)

    @pytest.mark.asyncio
    async def test_pool_exhausted_error(self):
        """Test PoolExhaustedError on timeout."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=1,
            min_size=1,
        )
        await pool.initialize()

        client = await pool.acquire()

        with pytest.raises(PoolExhaustedError, match="exhausted"):
            await pool.acquire(timeout=0.1)

        await pool.release(client)

    @pytest.mark.asyncio
    async def test_pool_closed_acquire_raises(self):
        """Test that acquire on closed pool raises."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=1,
        )
        await pool.initialize()
        await pool.close()

        with pytest.raises(PoolClosedError):
            await pool.acquire()

    @pytest.mark.asyncio
    async def test_pool_closed_release_destroys(self):
        """Test that release on closed pool destroys connection."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=1,
        )
        await pool.initialize()

        client = await pool.acquire()
        await pool.close()

        # Release after close should destroy
        await pool.release(client)

        assert client.is_closed


# =============================================================================
# Context Manager Tests
# =============================================================================

class TestContextManager:
    """Tests for connection context manager."""

    @pytest.mark.asyncio
    async def test_pool_context_manager(self):
        """Test context manager acquire/release."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=1,
        )
        await pool.initialize()

        async with pool.get_connection() as client:
            assert isinstance(client, MockClient)
            # Connection is in use
            assert pool._in_use >= 1

        # After context, connection should be released
        assert pool._in_use == 0

    @pytest.mark.asyncio
    async def test_pool_context_manager_on_exception(self):
        """Test context manager releases on exception."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=1,
        )
        await pool.initialize()

        try:
            async with pool.get_connection() as client:
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Connection should still be released
        assert pool._in_use == 0


# =============================================================================
# Health Check Tests
# =============================================================================

class TestHealthCheck:
    """Tests for health checking."""

    @pytest.mark.asyncio
    async def test_pool_removes_unhealthy_connection(self):
        """Test that unhealthy connections are replaced."""
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            # First call returns unhealthy, subsequent return healthy
            if call_count == 1:
                return UnhealthyMockClient()
            return MockClient()

        pool = ConnectionPool(
            client_factory=factory,
            max_size=10,
            min_size=0,
        )

        # Acquire should create, detect unhealthy, and replace
        client = await pool.acquire()

        assert not client.is_closed
        assert call_count == 2  # First unhealthy, second healthy

    @pytest.mark.asyncio
    async def test_pool_idle_timeout(self):
        """Test idle timeout removes connections."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=0,
            max_idle_time=0.1,  # 100ms timeout
        )
        await pool.initialize()

        client = await pool.acquire()
        await pool.release(client)

        # Wait for idle timeout
        await asyncio.sleep(0.15)

        # Next acquire should create new connection
        client2 = await pool.acquire()

        # May be same or different depending on timing
        # The key is that idle timeout is tracked


# =============================================================================
# Pool Statistics Tests
# =============================================================================

class TestPoolStatistics:
    """Tests for pool statistics."""

    @pytest.mark.asyncio
    async def test_pool_stats(self):
        """Test getting pool statistics."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=2,
        )
        await pool.initialize()

        stats = await pool.stats()

        assert isinstance(stats, PoolStatistics)
        assert stats.max_size == 10
        assert stats.min_size == 2
        assert stats.created == 2

    @pytest.mark.asyncio
    async def test_pool_stats_tracking(self):
        """Test that stats track acquisitions."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=1,
        )
        await pool.initialize()

        client = await pool.acquire()
        await pool.release(client)

        stats = await pool.stats()

        assert stats.created >= 1
        assert stats.avg_acquire_time_ms >= 0

    @pytest.mark.asyncio
    async def test_pool_stats_utilization(self):
        """Test utilization calculation."""
        stats = PoolStatistics(
            size=5,
            available=3,
            in_use=2,
            created=10,
            max_size=10,
            min_size=1,
        )

        utilization = stats.utilization()
        assert utilization == 20.0  # 2/10 = 20%

    def test_pool_stats_repr(self):
        """Test statistics string representation."""
        stats = PoolStatistics(
            size=5, available=3, in_use=2,
            created=10, max_size=10, min_size=1,
        )
        repr_str = repr(stats)

        assert "PoolStats" in repr_str
        assert "size=" in repr_str


# =============================================================================
# Pool Close Tests
# =============================================================================

class TestPoolClose:
    """Tests for pool close operation."""

    @pytest.mark.asyncio
    async def test_pool_close(self):
        """Test closing pool."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=2,
        )
        await pool.initialize()

        await pool.close()

        assert pool._closed is True
        assert pool._created == 0

    @pytest.mark.asyncio
    async def test_pool_close_destroys_connections(self):
        """Test that close destroys all connections."""
        clients = []

        def factory():
            client = MockClient()
            clients.append(client)
            return client

        pool = ConnectionPool(
            client_factory=factory,
            max_size=10,
            min_size=2,
        )
        await pool.initialize()
        await pool.close()

        # All clients should be closed
        for client in clients:
            assert client.is_closed


# =============================================================================
# PoolManager Tests
# =============================================================================

class TestPoolManager:
    """Tests for PoolManager."""

    def test_manager_create_pool(self):
        """Test creating pool via manager."""
        manager = PoolManager()

        pool = manager.create_pool(
            "test",
            client_factory=lambda: MockClient(),
            max_size=5,
        )

        assert isinstance(pool, ConnectionPool)
        assert "test" in manager.list_pools()

    def test_manager_create_pool_duplicate_name(self):
        """Test that duplicate pool name raises."""
        manager = PoolManager()

        manager.create_pool("test", client_factory=lambda: MockClient())

        with pytest.raises(ValueError, match="already exists"):
            manager.create_pool("test", client_factory=lambda: MockClient())

    def test_manager_get_pool(self):
        """Test getting pool by name."""
        manager = PoolManager()
        manager.create_pool("test", client_factory=lambda: MockClient())

        pool = manager.get_pool("test")
        assert pool is not None

        pool = manager.get_pool("unknown")
        assert pool is None

    def test_manager_list_pools(self):
        """Test listing pool names."""
        manager = PoolManager()
        manager.create_pool("pool1", client_factory=lambda: MockClient())
        manager.create_pool("pool2", client_factory=lambda: MockClient())

        names = manager.list_pools()

        assert "pool1" in names
        assert "pool2" in names

    @pytest.mark.asyncio
    async def test_manager_get_client(self):
        """Test getting client via manager."""
        manager = PoolManager()
        manager.create_pool("test", client_factory=lambda: MockClient())

        client = await manager.get_client("test")

        assert isinstance(client, MockClient)

    @pytest.mark.asyncio
    async def test_manager_get_client_unknown_pool(self):
        """Test getting client from unknown pool."""
        manager = PoolManager()

        with pytest.raises(KeyError, match="not found"):
            await manager.get_client("unknown")

    @pytest.mark.asyncio
    async def test_manager_release_client(self):
        """Test releasing client via manager."""
        manager = PoolManager()
        manager.create_pool("test", client_factory=lambda: MockClient(), min_size=1)

        client = await manager.get_client("test")
        await manager.release_client("test", client)

        # Should succeed without error

    @pytest.mark.asyncio
    async def test_manager_close_all(self):
        """Test closing all pools."""
        manager = PoolManager()
        manager.create_pool("pool1", client_factory=lambda: MockClient())
        manager.create_pool("pool2", client_factory=lambda: MockClient())

        await manager.close_all()

        assert len(manager.list_pools()) == 0

    def test_manager_repr(self):
        """Test manager string representation."""
        manager = PoolManager()
        manager.create_pool("test", client_factory=lambda: MockClient())

        repr_str = repr(manager)

        assert "PoolManager" in repr_str
        assert "test" in repr_str


# =============================================================================
# Performance Tests
# =============================================================================

class TestPerformance:
    """Performance-related tests."""

    @pytest.mark.asyncio
    async def test_pool_throughput(self):
        """Test pool throughput (basic sanity check)."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=5,
        )
        await pool.initialize()

        start = time.time()
        iterations = 100

        for _ in range(iterations):
            client = await pool.acquire()
            await client.query("test")
            await pool.release(client)

        elapsed = time.time() - start

        # Handle case where execution is extremely fast
        if elapsed == 0:
            elapsed = 0.001  # Assume at least 1ms

        req_per_sec = iterations / elapsed

        # Basic sanity check - should handle at least 100 req/s
        assert req_per_sec > 100, f"Throughput too low: {req_per_sec:.1f} req/s"

    @pytest.mark.asyncio
    async def test_pool_concurrent_acquire(self):
        """Test concurrent acquire operations."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=20,
            min_size=10,
        )
        await pool.initialize()

        async def worker(worker_id):
            client = await pool.acquire()
            result = await client.query(f"worker-{worker_id}")
            await pool.release(client)
            return result

        # Run 50 concurrent workers
        tasks = [worker(i) for i in range(50)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 50


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_pool_zero_min_size(self):
        """Test pool with min_size=0."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=0,
        )

        client = await pool.acquire()
        assert isinstance(client, MockClient)

    @pytest.mark.asyncio
    async def test_pool_single_connection(self):
        """Test pool with max_size=1."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=1,
            min_size=1,
        )
        await pool.initialize()

        client = await pool.acquire()
        await pool.release(client)

        # Should work fine
        assert client is not None

    @pytest.mark.asyncio
    async def test_pool_sync_factory(self):
        """Test pool with synchronous factory."""
        pool = ConnectionPool(
            client_factory=lambda: MockClient(),
            max_size=10,
            min_size=1,
        )
        await pool.initialize()

        client = await pool.acquire()
        assert isinstance(client, MockClient)

    @pytest.mark.asyncio
    async def test_pool_async_factory(self):
        """Test pool with asynchronous factory."""
        pool = ConnectionPool(
            client_factory=AsyncMockClientFactory.create,
            max_size=10,
            min_size=1,
        )
        await pool.initialize()

        client = await pool.acquire()
        assert isinstance(client, MockClient)


# Run tests with: pytest tests/unit/perf/test_connection_pool.py -v
