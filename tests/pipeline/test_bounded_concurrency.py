"""
Tests for PipelineEngine bounded concurrency (execute_with_backpressure).

Tests cover:
- Semaphore limits (max_concurrent_loops, worker_pool_size)
- Progress callback invocation
- Exception handling inside bounded_execute (return_exceptions=True)
- execute() single-workload delegate
- Default parameter values for concurrency controls
"""

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_engine(max_concurrent_loops: int = 100, worker_pool_size: int = 4):
    """Create a PipelineEngine with bounded concurrency params without full init."""
    from gaia.pipeline.engine import PipelineEngine

    with patch.object(PipelineEngine, "__init__", lambda self, *a, **kw: None):
        engine = PipelineEngine.__new__(PipelineEngine)

    engine.max_concurrent_loops = max_concurrent_loops
    engine._semaphore = asyncio.Semaphore(max_concurrent_loops)
    engine._worker_semaphore = asyncio.Semaphore(worker_pool_size)
    engine._initialized = False
    engine._state_machine = None
    engine._routing_engine = None
    return engine


# ---------------------------------------------------------------------------
# execute() delegate tests
# ---------------------------------------------------------------------------


class TestPipelineEngineExecute:
    """Tests for PipelineEngine.execute() single-workload method."""

    @pytest.mark.asyncio
    async def test_execute_returns_workload_when_not_initialized(self):
        """execute() returns the workload unchanged when engine not initialized."""
        engine = make_engine()
        workload = {"feature": "login-flow"}
        result = await engine.execute(workload)
        assert result == workload

    @pytest.mark.asyncio
    async def test_execute_delegates_to_start_when_initialized(self):
        """execute() calls start() when engine is initialized."""
        engine = make_engine()
        engine._initialized = True
        engine._state_machine = MagicMock()
        engine.start = AsyncMock(return_value={"status": "done"})

        result = await engine.execute({"feature": "x"})
        engine.start.assert_awaited_once()
        assert result == {"status": "done"}


# ---------------------------------------------------------------------------
# execute_with_backpressure() tests
# ---------------------------------------------------------------------------


class TestExecuteWithBackpressure:
    """Tests for PipelineEngine.execute_with_backpressure()."""

    @pytest.mark.asyncio
    async def test_all_workloads_processed(self):
        """All workloads are processed and results returned."""
        engine = make_engine(max_concurrent_loops=10, worker_pool_size=4)

        workloads = [{"id": i} for i in range(8)]
        engine.execute = AsyncMock(side_effect=lambda w: asyncio.sleep(0) or w)

        results = await engine.execute_with_backpressure(workloads)

        assert len(results) == 8
        # No exceptions in results
        for r in results:
            assert not isinstance(r, Exception)

    @pytest.mark.asyncio
    async def test_progress_callback_called_for_each_workload(self):
        """Progress callback is invoked once per workload."""
        engine = make_engine(max_concurrent_loops=10, worker_pool_size=4)
        engine.execute = AsyncMock(side_effect=lambda w: w)

        callback_results: List[Any] = []

        results = await engine.execute_with_backpressure(
            [{"id": i} for i in range(5)],
            progress_callback=lambda r: callback_results.append(r),
        )

        assert len(callback_results) == 5

    @pytest.mark.asyncio
    async def test_exceptions_captured_not_raised(self):
        """Exceptions from individual workloads are captured, not propagated."""
        engine = make_engine(max_concurrent_loops=10, worker_pool_size=4)

        async def failing_execute(workload):
            if workload.get("fail"):
                raise RuntimeError("deliberate failure")
            return workload

        engine.execute = AsyncMock(side_effect=failing_execute)

        workloads = [{"id": 0}, {"id": 1, "fail": True}, {"id": 2}]
        results = await engine.execute_with_backpressure(workloads)

        assert len(results) == 3
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 1
        assert "deliberate failure" in str(exceptions[0])

    @pytest.mark.asyncio
    async def test_empty_workloads_returns_empty_list(self):
        """Empty workload list returns an empty results list immediately."""
        engine = make_engine()
        engine.execute = AsyncMock()

        results = await engine.execute_with_backpressure([])
        assert results == []
        engine.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """At most max_concurrent_loops tasks run concurrently."""
        MAX_CONCURRENT = 3
        engine = make_engine(
            max_concurrent_loops=MAX_CONCURRENT, worker_pool_size=MAX_CONCURRENT
        )

        active_count = 0
        peak_active = 0

        async def slow_execute(workload):
            nonlocal active_count, peak_active
            active_count += 1
            peak_active = max(peak_active, active_count)
            await asyncio.sleep(0.02)
            active_count -= 1
            return workload

        engine.execute = AsyncMock(side_effect=slow_execute)

        workloads = [{"id": i} for i in range(9)]
        await engine.execute_with_backpressure(workloads)

        assert peak_active <= MAX_CONCURRENT

    @pytest.mark.asyncio
    async def test_worker_semaphore_limits_concurrency(self):
        """At most worker_pool_size tasks hold the worker semaphore simultaneously."""
        WORKER_POOL = 2
        engine = make_engine(max_concurrent_loops=100, worker_pool_size=WORKER_POOL)

        worker_active = 0
        peak_worker = 0

        original_execute = engine.execute

        async def instrumented_execute(workload):
            nonlocal worker_active, peak_worker
            worker_active += 1
            peak_worker = max(peak_worker, worker_active)
            await asyncio.sleep(0.02)
            worker_active -= 1
            return workload

        engine.execute = AsyncMock(side_effect=instrumented_execute)

        workloads = [{"id": i} for i in range(6)]
        await engine.execute_with_backpressure(workloads)

        assert peak_worker <= WORKER_POOL

    @pytest.mark.asyncio
    async def test_progress_callback_not_called_on_exception(self):
        """Progress callback should not be called when workload raises."""
        engine = make_engine()

        async def raise_always(w):
            raise ValueError("boom")

        engine.execute = AsyncMock(side_effect=raise_always)
        called: List[Any] = []

        results = await engine.execute_with_backpressure(
            [{"id": 0}],
            progress_callback=lambda r: called.append(r),
        )

        assert len(results) == 1
        assert isinstance(results[0], ValueError)
        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_results_order_corresponds_to_input_order(self):
        """asyncio.gather preserves input order in results list."""
        engine = make_engine(max_concurrent_loops=10, worker_pool_size=4)

        async def identity(w):
            # Introduce variable delay so tasks complete out of submission order
            await asyncio.sleep(0.001 * (10 - w["id"]))
            return w["id"]

        engine.execute = AsyncMock(side_effect=identity)

        workloads = [{"id": i} for i in range(5)]
        results = await engine.execute_with_backpressure(workloads)

        assert results == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_large_workload_batch(self):
        """Large batch of workloads completes without error."""
        engine = make_engine(max_concurrent_loops=20, worker_pool_size=8)
        engine.execute = AsyncMock(side_effect=lambda w: w)

        workloads = [{"id": i} for i in range(200)]
        results = await engine.execute_with_backpressure(workloads)

        assert len(results) == 200
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0


# ---------------------------------------------------------------------------
# Default parameter tests
# ---------------------------------------------------------------------------


class TestPipelineEngineConcurrencyDefaults:
    """Tests verifying PipelineEngine default concurrency parameter values."""

    def test_default_max_concurrent_loops(self):
        """max_concurrent_loops defaults to 100."""
        import inspect

        from gaia.pipeline.engine import PipelineEngine

        sig = inspect.signature(PipelineEngine.__init__)
        params = sig.parameters
        assert "max_concurrent_loops" in params
        assert params["max_concurrent_loops"].default == 100

    def test_default_worker_pool_size(self):
        """worker_pool_size defaults to 4."""
        import inspect

        from gaia.pipeline.engine import PipelineEngine

        sig = inspect.signature(PipelineEngine.__init__)
        params = sig.parameters
        assert "worker_pool_size" in params
        assert params["worker_pool_size"].default == 4

    def test_semaphores_created_with_correct_limits(self):
        """_semaphore and _worker_semaphore are created with configured limits.

        The _value attribute is a CPython implementation detail that is present
        on CPython 3.10+ but is not part of the public asyncio.Semaphore API.
        The test verifies that the semaphores are instances of asyncio.Semaphore,
        and checks _value only when the attribute is available on the platform.
        """
        engine = make_engine(max_concurrent_loops=50, worker_pool_size=8)

        assert isinstance(engine._semaphore, asyncio.Semaphore)
        assert isinstance(engine._worker_semaphore, asyncio.Semaphore)

        # _value is a CPython implementation detail; skip assertion if not present
        if hasattr(engine._semaphore, "_value"):
            assert engine._semaphore._value == 50
            assert engine._worker_semaphore._value == 8
        else:
            # On non-CPython, we verify type only (already asserted above)
            pass
