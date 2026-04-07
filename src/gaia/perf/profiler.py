# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Performance Profiler - Timing Utilities and Bottleneck Detection.

This module provides performance profiling utilities for GAIA including:
- Function timing decorator
- Context manager for timing blocks
- Cumulative timing for repeated operations
- Statistics: min, max, avg, p95, p99
- Bottleneck detection with configurable thresholds

Example:
    >>> from gaia.perf import timed, Profiler
    >>> @timed
    ... def my_function():
    ...     time.sleep(0.1)
    >>> result = my_function()  # Prints timing info
"""

import asyncio
import contextlib
import functools
import logging
import statistics
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, Union

logger = logging.getLogger(__name__)

# Type variables
F = TypeVar('F', bound=Callable[..., Any])
T = TypeVar('T')


# ==================== Constants ====================

# Default threshold for slow operation detection (in seconds)
DEFAULT_SLOW_THRESHOLD = 1.0

# Percentile calculations
P95_INDEX = 0.95
P99_INDEX = 0.99


# ==================== Data Classes ====================

@dataclass
class TimingStats:
    """
    Statistical summary of timing measurements.

    Attributes:
        count: Number of measurements
        total: Total time across all measurements
        min: Minimum time
        max: Maximum time
        avg: Average (mean) time
        median: Median time
        p95: 95th percentile time
        p99: 99th percentile time
        std_dev: Standard deviation
    """
    count: int
    total: float
    min: float
    max: float
    avg: float
    median: float
    p95: float
    p99: float
    std_dev: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'count': self.count,
            'total': self.total,
            'min': self.min,
            'max': self.max,
            'avg': self.avg,
            'median': self.median,
            'p95': self.p95,
            'p99': self.p99,
            'std_dev': self.std_dev,
        }

    def __str__(self) -> str:
        """Return formatted string representation."""
        return (
            f"TimingStats(count={self.count}, avg={self.avg*1000:.2f}ms, "
            f"min={self.min*1000:.2f}ms, max={self.max*1000:.2f}ms, "
            f"p95={self.p95*1000:.2f}ms, p99={self.p99*1000:.2f}ms)"
        )


@dataclass
class BottleneckReport:
    """
    Report of detected performance bottlenecks.

    Attributes:
        operation: Name of the slow operation
        stats: Timing statistics
        threshold: Threshold that was exceeded
        call_count: Number of times operation was called
        total_time: Total time spent in operation
        severity: Severity level (low, medium, high, critical)
        recommendation: Suggested optimization
    """
    operation: str
    stats: TimingStats
    threshold: float
    call_count: int
    total_time: float
    severity: str
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'operation': self.operation,
            'stats': self.stats.to_dict(),
            'threshold': self.threshold,
            'call_count': self.call_count,
            'total_time': self.total_time,
            'severity': self.severity,
            'recommendation': self.recommendation,
        }


# ==================== Timing Decorator ====================

def timed(
    func: Optional[F] = None,
    *,
    name: Optional[str] = None,
    log_level: int = logging.DEBUG,
    logger_func: Optional[Callable[[str], None]] = None,
) -> F:
    """
    Decorator to measure function execution time.

    This decorator wraps a function to measure and log its execution time.
    Can be used with or without parentheses.

    Args:
        func: The function to decorate (when used without parentheses)
        name: Optional custom name for the operation
        log_level: Logging level for timing output
        logger_func: Optional custom logger function

    Returns:
        Decorated function with timing

    Example:
        >>> @timed
        ... def slow_function():
        ...     time.sleep(0.1)
        >>> slow_function()  # Logs: slow_function took 0.1002s

        >>> @timed(name="custom_name", log_level=logging.INFO)
        ... def another_function():
        ...     time.sleep(0.2)
    """
    def decorator(f: F) -> F:
        op_name = name or f.__qualname__
        log_fn = logger_func or (lambda msg: logger.log(log_level, msg))

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return f(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                log_fn(f"{op_name} took {elapsed:.4f}s")

        @functools.wraps(f)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await f(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                log_fn(f"{op_name} took {elapsed:.4f}s")

        if asyncio.iscoroutinefunction(f):
            return async_wrapper  # type: ignore
        return wrapper  # type: ignore

    if func is not None:
        return decorator(func)
    return decorator  # type: ignore


# ==================== Timing Context Manager ====================

class Timer:
    """
    Context manager for timing code blocks.

    Provides precise timing for code blocks with automatic logging
    and statistics collection.

    Attributes:
        name: Name of the timed operation
        elapsed: Elapsed time after context exit

    Example:
        >>> with Timer("database query") as timer:
        ...     result = db.query(sql)
        >>> print(f"Query took {timer.elapsed*1000:.2f}ms")
    """

    def __init__(
        self,
        name: str = "operation",
        log_level: int = logging.DEBUG,
        logger_func: Optional[Callable[[str], None]] = None,
    ):
        """
        Initialize timer.

        Args:
            name: Name of the timed operation
            log_level: Logging level for timing output
            logger_func: Optional custom logger function
        """
        self.name = name
        self.log_level = log_level
        self.logger_func = logger_func or (lambda msg: logger.log(self.log_level, msg))
        self.elapsed: float = 0.0
        self._start_time: float = 0.0
        self._end_time: Optional[float] = None

    def __enter__(self) -> 'Timer':
        """Start timing on context entry."""
        self._start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Stop timing and log result on context exit."""
        self._end_time = time.perf_counter()
        self.elapsed = self._end_time - self._start_time
        self.logger_func(f"{self.name} took {self.elapsed:.4f}s")

    @property
    def elapsed_ms(self) -> float:
        """Return elapsed time in milliseconds."""
        return self.elapsed * 1000


@contextlib.contextmanager
def timer_block(name: str = "block"):
    """
    Simple context manager for timing a code block.

    Yields:
        Timer object with elapsed time

    Example:
        >>> with timer_block("operation"):
        ...     do_something()
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        logger.debug(f"{name} took {elapsed:.4f}s")


# ==================== Cumulative Timer ====================

class CumulativeTimer:
    """
    Cumulative timer for repeated operations.

    Tracks timing statistics across multiple calls to the same
    operation, providing min, max, avg, and percentile statistics.

    Thread-safe for concurrent operations.

    Example:
        >>> timer = CumulativeTimer("api_call")
        >>> for i in range(100):
        ...     with timer:
        ...         make_api_call()
        >>> stats = timer.get_stats()
        >>> print(f"Avg: {stats.avg*1000:.2f}ms, P95: {stats.p95*1000:.2f}ms")
    """

    def __init__(self, name: str):
        """
        Initialize cumulative timer.

        Args:
            name: Name of the operation being timed
        """
        self.name = name
        self._times: List[float] = []
        self._lock = threading.RLock()
        self._total = 0.0

    def __enter__(self) -> 'CumulativeTimer':
        """Start timing on context entry."""
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Record timing on context exit."""
        elapsed = time.perf_counter() - self._start
        with self._lock:
            self._times.append(elapsed)
            self._total += elapsed

    def record(self, elapsed: float) -> None:
        """
        Manually record an elapsed time.

        Args:
            elapsed: Elapsed time in seconds
        """
        with self._lock:
            self._times.append(elapsed)
            self._total += elapsed

    def time(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator to time a function.

        Args:
            func: Function to time

        Returns:
            Wrapped function with timing
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with self:
                return func(*args, **kwargs)
        return wrapper

    def get_stats(self) -> Optional[TimingStats]:
        """
        Get timing statistics.

        Returns:
            TimingStats object or None if no measurements

        Example:
            >>> timer = CumulativeTimer("op")
            >>> # ... record some times ...
            >>> stats = timer.get_stats()
            >>> print(stats.avg)
        """
        with self._lock:
            if not self._times:
                return None
            return calculate_stats(self._times)

    def get_total(self) -> float:
        """Return total elapsed time across all measurements."""
        with self._lock:
            return self._total

    def get_count(self) -> int:
        """Return number of measurements."""
        with self._lock:
            return len(self._times)

    def reset(self) -> None:
        """Reset all measurements."""
        with self._lock:
            self._times.clear()
            self._total = 0.0


# ==================== Statistics Functions ====================

def calculate_stats(times: List[float]) -> TimingStats:
    """
    Calculate timing statistics from a list of measurements.

    Args:
        times: List of elapsed times in seconds

    Returns:
        TimingStats with all calculated statistics

    Raises:
        ValueError: If times list is empty

    Example:
        >>> times = [0.1, 0.15, 0.2, 0.25, 0.3]
        >>> stats = calculate_stats(times)
        >>> print(f"P95: {stats.p95*1000:.2f}ms")
    """
    if not times:
        raise ValueError("Cannot calculate stats on empty list")

    sorted_times = sorted(times)
    count = len(sorted_times)
    total = sum(sorted_times)
    min_time = sorted_times[0]
    max_time = sorted_times[-1]
    avg = total / count
    median = sorted_times[count // 2] if count % 2 == 1 else (
        sorted_times[count // 2 - 1] + sorted_times[count // 2]
    ) / 2

    # Calculate percentiles
    p95_idx = int(count * P95_INDEX)
    p99_idx = int(count * P99_INDEX)
    p95 = sorted_times[min(p95_idx, count - 1)]
    p99 = sorted_times[min(p99_idx, count - 1)]

    # Standard deviation
    if count > 1:
        std_dev = statistics.stdev(times)
    else:
        std_dev = 0.0

    return TimingStats(
        count=count,
        total=total,
        min=min_time,
        max=max_time,
        avg=avg,
        median=median,
        p95=p95,
        p99=p99,
        std_dev=std_dev,
    )


def percentile(times: List[float], p: float) -> float:
    """
    Calculate the p-th percentile of a list of times.

    Args:
        times: List of elapsed times
        p: Percentile (0.0 to 1.0)

    Returns:
        Value at the p-th percentile

    Example:
        >>> times = [0.1, 0.2, 0.3, 0.4, 0.5]
        >>> percentile(times, 0.9)  # 90th percentile
        0.5
    """
    if not times:
        return 0.0
    sorted_times = sorted(times)
    idx = int(len(sorted_times) * p)
    return sorted_times[min(idx, len(sorted_times) - 1)]


# ==================== Profiler ====================

class Profiler:
    """
    Performance profiler for bottleneck detection.

    Tracks timing for multiple operations and identifies bottlenecks
    based on configurable thresholds.

    Features:
    - Track multiple operations by name
    - Automatic slow operation detection
    - Call count tracking
    - Time distribution analysis
    - Thread-safe for concurrent profiling

    Example:
        >>> profiler = Profiler(slow_threshold=0.5)
        >>> with profiler.track("expensive_operation"):
        ...     do_expensive_thing()
        >>> report = profiler.get_bottlenecks()
    """

    def __init__(
        self,
        slow_threshold: float = DEFAULT_SLOW_THRESHOLD,
        name: str = "profiler",
    ):
        """
        Initialize profiler.

        Args:
            slow_threshold: Threshold in seconds for slow operation detection
            name: Optional name for this profiler instance
        """
        self.slow_threshold = slow_threshold
        self.name = name
        self._timers: Dict[str, CumulativeTimer] = {}
        self._lock = threading.RLock()
        self._slow_operations: List[Tuple[str, float]] = []
        self._enabled = True

        logger.debug(f"Profiler initialized with threshold={slow_threshold}s")

    @contextlib.contextmanager
    def track(self, name: str) -> 'ProfilerTrackContext':
        """
        Track an operation by name.

        Args:
            name: Name of the operation

        Yields:
            Context manager for tracking

        Example:
            >>> with profiler.track("database_query"):
            ...     result = db.query(sql)
        """
        if not self._enabled:
            yield
            return

        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self._record(name, elapsed)

    def _record(self, name: str, elapsed: float) -> None:
        """
        Record an elapsed time for an operation.

        Args:
            name: Operation name
            elapsed: Elapsed time in seconds
        """
        with self._lock:
            if name not in self._timers:
                self._timers[name] = CumulativeTimer(name)

            self._timers[name].record(elapsed)

            # Check for slow operation
            if elapsed > self.slow_threshold:
                self._slow_operations.append((name, elapsed))
                logger.warning(
                    f"Slow operation detected: {name} took {elapsed:.4f}s "
                    f"(threshold: {self.slow_threshold}s)"
                )

    def time(self, name: str) -> Callable[[F], F]:
        """
        Decorator factory to time a function.

        Args:
            name: Name for the operation

        Returns:
            Decorator function

        Example:
            >>> @profiler.time("expensive_calculation")
            ... def calculate():
            ...     return sum(range(1000000))
        """
        def decorator(func: F) -> F:
            op_name = name or func.__qualname__

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                with self.track(op_name):
                    return func(*args, **kwargs)

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                with self.track(op_name):
                    return await func(*args, **kwargs)

            if asyncio.iscoroutinefunction(func):
                return async_wrapper  # type: ignore
            return wrapper  # type: ignore

        return decorator  # type: ignore

    def get_stats(self, name: str) -> Optional[TimingStats]:
        """
        Get timing statistics for a specific operation.

        Args:
            name: Operation name

        Returns:
            TimingStats or None if operation not tracked

        Example:
            >>> stats = profiler.get_stats("api_call")
            >>> print(f"Avg: {stats.avg*1000:.2f}ms")
        """
        with self._lock:
            if name not in self._timers:
                return None
            return self._timers[name].get_stats()

    def get_all_stats(self) -> Dict[str, TimingStats]:
        """
        Get timing statistics for all tracked operations.

        Returns:
            Dictionary mapping operation names to stats

        Example:
            >>> all_stats = profiler.get_all_stats()
            >>> for name, stats in all_stats.items():
            ...     print(f"{name}: {stats.avg*1000:.2f}ms avg")
        """
        with self._lock:
            return {
                name: timer.get_stats()
                for name, timer in self._timers.items()
                if timer.get_stats() is not None
            }

    def get_bottlenecks(
        self,
        limit: int = 10,
        min_calls: int = 1,
    ) -> List[BottleneckReport]:
        """
        Identify performance bottlenecks.

        Args:
            limit: Maximum number of bottlenecks to return
            min_calls: Minimum call count to consider

        Returns:
            List of BottleneckReport sorted by severity

        Example:
            >>> bottlenecks = profiler.get_bottlenecks(limit=5)
            >>> for bn in bottlenecks:
            ...     print(f"{bn.operation}: {bn.severity}")
        """
        with self._lock:
            bottlenecks: List[BottleneckReport] = []

            for name, timer in self._timers.items():
                stats = timer.get_stats()
                if stats is None or stats.count < min_calls:
                    continue

                # Determine severity
                severity = self._calculate_severity(stats, timer.get_total())
                if severity is None:
                    continue

                # Generate recommendation
                recommendation = self._generate_recommendation(name, stats)

                bottleneck = BottleneckReport(
                    operation=name,
                    stats=stats,
                    threshold=self.slow_threshold,
                    call_count=stats.count,
                    total_time=timer.get_total(),
                    severity=severity,
                    recommendation=recommendation,
                )
                bottlenecks.append(bottleneck)

            # Sort by total time (descending)
            bottlenecks.sort(key=lambda b: b.total_time, reverse=True)
            return bottlenecks[:limit]

    def _calculate_severity(
        self,
        stats: TimingStats,
        total_time: float,
    ) -> Optional[str]:
        """
        Calculate severity level for a potential bottleneck.

        Args:
            stats: Timing statistics
            total_time: Total time for operation

        Returns:
            Severity string or None if not a bottleneck
        """
        # Critical: Single call over 10 seconds or total over 60 seconds
        if stats.max > 10.0 or total_time > 60.0:
            return 'critical'

        # High: Single call over 5 seconds or total over 30 seconds
        if stats.max > 5.0 or total_time > 30.0:
            return 'high'

        # Medium: Single call over threshold or total over 10 seconds
        if stats.max > self.slow_threshold or total_time > 10.0:
            return 'medium'

        # Low: P95 over threshold
        if stats.p95 > self.slow_threshold:
            return 'low'

        return None

    def _generate_recommendation(
        self,
        name: str,
        stats: TimingStats,
    ) -> str:
        """
        Generate optimization recommendation based on stats.

        Args:
            name: Operation name
            stats: Timing statistics

        Returns:
            Recommendation string
        """
        recommendations = []

        # High variance suggests inconsistent performance
        if stats.std_dev > stats.avg * 0.5:
            recommendations.append(
                "High variance detected - consider investigating inconsistent performance"
            )

        # High max with low avg suggests occasional spikes
        if stats.max > stats.avg * 5:
            recommendations.append(
                "Occasional spikes detected - check for resource contention or GC pauses"
            )

        # High call count with moderate time suggests optimization opportunity
        if stats.count > 100 and stats.avg > 0.01:
            recommendations.append(
                "Frequent calls with non-trivial cost - consider caching or batching"
            )

        # P99 much higher than P95 suggests tail latency issues
        if stats.p99 > stats.p95 * 2:
            recommendations.append(
                "High tail latency - consider async processing or timeouts"
            )

        if not recommendations:
            if stats.avg > 1.0:
                recommendations.append("Consider algorithmic optimization or parallelization")
            else:
                recommendations.append("Monitor for changes under load")

        return "; ".join(recommendations)

    def get_slow_operations(self) -> List[Tuple[str, float]]:
        """
        Get list of operations that exceeded the slow threshold.

        Returns:
            List of (name, elapsed) tuples

        Example:
            >>> slow = profiler.get_slow_operations()
            >>> for name, elapsed in slow:
            ...     print(f"{name}: {elapsed*1000:.2f}ms")
        """
        with self._lock:
            return list(self._slow_operations)

    def get_summary(self) -> str:
        """
        Get human-readable summary of all tracked operations.

        Returns:
            Formatted summary string

        Example:
            >>> print(profiler.get_summary())
            Profiler Summary:
              operation_a: 10 calls, avg=15.23ms, p95=25.00ms
              operation_b: 5 calls, avg=45.67ms, p95=50.00ms
        """
        with self._lock:
            lines = [f"Profiler Summary ({self.name}):"]

            for name, timer in sorted(self._timers.items()):
                stats = timer.get_stats()
                if stats:
                    lines.append(
                        f"  {name}: {stats.count} calls, "
                        f"avg={stats.avg*1000:.2f}ms, "
                        f"p95={stats.p95*1000:.2f}ms, "
                        f"total={stats.total*1000:.2f}ms"
                    )

            return "\n".join(lines)

    def reset(self) -> None:
        """Reset all tracking data."""
        with self._lock:
            self._timers.clear()
            self._slow_operations.clear()

    def enable(self) -> None:
        """Enable profiling."""
        self._enabled = True

    def disable(self) -> None:
        """Disable profiling."""
        self._enabled = False


class ProfilerTrackContext:
    """Context manager for profiler tracking (returned by track())."""

    def __init__(self, profiler: Profiler, name: str):
        self.profiler = profiler
        self.name = name
        self._start: float = 0.0

    def __enter__(self) -> 'ProfilerTrackContext':
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        elapsed = time.perf_counter() - self._start
        self.profiler._record(self.name, elapsed)


# ==================== Overhead Measurement ====================

def measure_overhead(
    func: Callable[[], Any],
    iterations: int = 1000,
) -> Dict[str, float]:
    """
    Measure the overhead of a function call.

    This is useful for validating that profiling/timing overhead
    is within acceptable limits (e.g., <5%).

    Args:
        func: Function to measure (should be fast)
        iterations: Number of iterations

    Returns:
        Dictionary with overhead measurements

    Example:
        >>> def fast_op(): return 1 + 1
        >>> overhead = measure_overhead(fast_op, iterations=10000)
        >>> print(f"Overhead: {overhead['overhead_percent']:.2f}%")
    """
    # Measure without timing
    start = time.perf_counter()
    for _ in range(iterations):
        func()
    baseline = time.perf_counter() - start

    # Measure with timing
    start = time.perf_counter()
    for _ in range(iterations):
        with timer_block("overhead_test"):
            func()
    with_timing = time.perf_counter() - start

    overhead = with_timing - baseline
    overhead_percent = (overhead / baseline * 100) if baseline > 0 else 0

    return {
        'baseline_total': baseline,
        'with_timing_total': with_timing,
        'overhead_total': overhead,
        'overhead_percent': overhead_percent,
        'iterations': iterations,
        'avg_overhead_per_call': overhead / iterations,
    }


# Module exports
__all__ = [
    'timed',
    'Timer',
    'timer_block',
    'CumulativeTimer',
    'Profiler',
    'TimingStats',
    'BottleneckReport',
    'calculate_stats',
    'percentile',
    'measure_overhead',
    'DEFAULT_SLOW_THRESHOLD',
]
