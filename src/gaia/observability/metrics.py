"""
Metrics collection for GAIA observability.

This module provides metrics collection capabilities including:
- Counter metrics for monotonically increasing values
- Gauge metrics for point-in-time values
- Histogram metrics for value distributions
- Prometheus-compatible export format

Example:
    >>> from gaia.observability.metrics import MetricsCollector
    >>>
    >>> metrics = MetricsCollector(prefix="gaia")
    >>>
    >>> # Counter
    >>> requests = metrics.counter("http_requests_total")
    >>> requests.inc()
    >>> requests.inc(labels={"method": "GET", "status": "200"})
    >>>
    >>> # Gauge
    >>> queue_size = metrics.gauge("queue_size")
    >>> queue_size.set(42)
    >>>
    >>> # Histogram
    >>> latency = metrics.histogram("request_latency_seconds")
    >>> latency.observe(0.125)
    >>>
    >>> # Export
    >>> prometheus_output = metrics.to_prometheus()
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
import threading
import time


@dataclass
class MetricPoint:
    """
    Single metric data point.

    Attributes:
        name: Metric name
        value: Metric value
        timestamp: Unix timestamp when recorded
        labels: Metric labels for filtering/grouping

    Example:
        >>> point = MetricPoint(
        ...     name="request_latency",
        ...     value=0.125,
        ...     labels={"endpoint": "/api/users"}
        ... )
    """

    name: str
    value: Union[int, float]
    timestamp: float = field(default_factory=time.time)
    labels: Dict[str, str] = field(default_factory=dict)


class Counter:
    """
    Monotonically increasing counter metric.

    Counters only increase; they never decrease.
    Use for: request counts, error counts, completed tasks.

    Example:
        >>> counter = Counter("http_requests_total")
        >>> counter.inc()  # Increment by 1
        >>> counter.inc(labels={"method": "GET"})
        >>> counter.inc(5)  # Increment by 5
        >>> value = counter.get()
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
    ) -> None:
        """
        Initialize counter.

        Args:
            name: Metric name
            description: Metric description
            label_names: Optional label names for filtering

        Example:
            >>> counter = Counter(
            ...     "http_requests_total",
            ...     description="Total HTTP requests",
            ...     label_names=["method", "status"]
            ... )
        """
        self.name = name
        self.description = description
        self.label_names = sorted(label_names or [])
        self._values: Dict[str, float] = {}
        self._lock = threading.RLock()

    def inc(self, value: float = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """
        Increment counter.

        Args:
            value: Value to increment by (must be >= 0)
            labels: Label values for this increment

        Raises:
            ValueError: If value is negative

        Example:
            >>> counter = Counter("requests")
            >>> counter.inc()
            >>> counter.inc(5)
            >>> counter.inc(labels={"method": "GET"})
        """
        if value < 0:
            raise ValueError("Counter can only be incremented with non-negative values")

        key = self._label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + value

    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        """
        Get counter value for labels.

        Args:
            labels: Label values to filter by

        Returns:
            Current counter value

        Example:
            >>> counter = Counter("requests")
            >>> counter.inc(5)
            >>> counter.get()
            5.0
        """
        key = self._label_key(labels)
        with self._lock:
            return self._values.get(key, 0)

    def get_all(self) -> Dict[str, float]:
        """
        Get all counter values.

        Returns:
            Dictionary of label keys to values

        Example:
            >>> counter = Counter("requests", label_names=["method"])
            >>> counter.inc(labels={"method": "GET"})
            >>> counter.inc(labels={"method": "POST"})
            >>> counter.get_all()
            {'method="GET"': 1.0, 'method="POST"': 1.0}
        """
        with self._lock:
            return dict(self._values)

    def _label_key(self, labels: Optional[Dict[str, str]]) -> str:
        """Convert labels to unique key."""
        if not labels:
            return ""

        # Sort labels for consistent ordering
        sorted_labels = sorted(labels.items())
        return ",".join(f'{k}="{v}"' for k, v in sorted_labels if k in self.label_names or not self.label_names)

    def reset(self) -> None:
        """Reset all counter values."""
        with self._lock:
            self._values.clear()


class Gauge:
    """
    Point-in-time gauge metric.

    Gauges can go up and down.
    Use for: queue sizes, temperatures, current memory usage.

    Example:
        >>> gauge = Gauge("queue_size")
        >>> gauge.set(42)
        >>> gauge.inc()
        >>> gauge.dec(5)
        >>> value = gauge.get()
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
    ) -> None:
        """
        Initialize gauge.

        Args:
            name: Metric name
            description: Metric description
            label_names: Optional label names

        Example:
            >>> gauge = Gauge(
            ...     "queue_size",
            ...     description="Current queue size",
            ...     label_names=["queue"]
            ... )
        """
        self.name = name
        self.description = description
        self.label_names = sorted(label_names or [])
        self._values: Dict[str, float] = {}
        self._lock = threading.RLock()

    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """
        Set gauge to specific value.

        Args:
            value: Value to set
            labels: Label values for this measurement

        Example:
            >>> gauge = Gauge("temperature")
            >>> gauge.set(21.5)
            >>> gauge.set(22.0, labels={"sensor": "room1"})
        """
        key = self._label_key(labels)
        with self._lock:
            self._values[key] = value

    def inc(self, value: float = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """
        Increment gauge by value.

        Args:
            value: Value to increment by
            labels: Label values

        Example:
            >>> gauge = Gauge("counter")
            >>> gauge.inc()
            >>> gauge.inc(5)
        """
        key = self._label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + value

    def dec(self, value: float = 1, labels: Optional[Dict[str, str]] = None) -> None:
        """
        Decrement gauge by value.

        Args:
            value: Value to decrement by
            labels: Label values

        Example:
            >>> gauge = Gauge("queue_size")
            >>> gauge.dec()
            >>> gauge.dec(5)
        """
        key = self._label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) - value

    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        """
        Get gauge value.

        Args:
            labels: Label values to filter by

        Returns:
            Current gauge value

        Example:
            >>> gauge = Gauge("temperature")
            >>> gauge.set(21.5)
            >>> gauge.get()
            21.5
        """
        key = self._label_key(labels)
        with self._lock:
            return self._values.get(key, 0)

    def get_all(self) -> Dict[str, float]:
        """Get all gauge values."""
        with self._lock:
            return dict(self._values)

    def _label_key(self, labels: Optional[Dict[str, str]]) -> str:
        """Convert labels to unique key."""
        if not labels:
            return ""
        sorted_labels = sorted(labels.items())
        return ",".join(f'{k}="{v}"' for k, v in sorted_labels if k in self.label_names or not self.label_names)

    def reset(self) -> None:
        """Reset all gauge values."""
        with self._lock:
            self._values.clear()


class Histogram:
    """
    Histogram metric for value distributions.

    Histograms track:
    - Count of observations
    - Sum of observed values
    - Bucket counts for configurable ranges

    Use for: latencies, request sizes, response sizes.

    Example:
        >>> histogram = Histogram(
        ...     "request_latency_seconds",
        ...     buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0]
        ... )
        >>> histogram.observe(0.125)
        >>> summary = histogram.get_summary()
    """

    DEFAULT_BUCKETS = [
        0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0
    ]

    def __init__(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
        buckets: Optional[List[float]] = None,
    ) -> None:
        """
        Initialize histogram.

        Args:
            name: Metric name
            description: Metric description
            label_names: Optional label names
            buckets: Histogram bucket boundaries (default: standard buckets)

        Example:
            >>> histogram = Histogram(
            ...     "request_latency_seconds",
            ...     buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
            ... )
        """
        self.name = name
        self.description = description
        self.label_names = sorted(label_names or [])
        self.buckets = sorted(buckets or self.DEFAULT_BUCKETS)
        self._buckets: Dict[str, Dict[float, int]] = {}
        self._sums: Dict[str, float] = {}
        self._counts: Dict[str, int] = {}
        self._lock = threading.RLock()

    def observe(
        self,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Record an observation.

        Args:
            value: Observed value
            labels: Label values for this observation

        Example:
            >>> histogram = Histogram("latency", buckets=[0.1, 0.5, 1.0])
            >>> histogram.observe(0.125)
            >>> histogram.observe(0.75, labels={"endpoint": "/api"})
        """
        key = self._label_key(labels)

        with self._lock:
            # Initialize if needed
            if key not in self._buckets:
                self._buckets[key] = {bucket: 0 for bucket in self.buckets}
                self._sums[key] = 0.0
                self._counts[key] = 0

            # Update count
            self._counts[key] += 1

            # Update sum
            self._sums[key] += value

            # Update buckets
            for bucket in self.buckets:
                if value <= bucket:
                    self._buckets[key][bucket] += 1

    def get_summary(
        self,
        labels: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Get histogram summary.

        Args:
            labels: Label values to filter by

        Returns:
            Dictionary with count, sum, and bucket counts

        Example:
            >>> histogram = Histogram("latency", buckets=[0.1, 0.5, 1.0])
            >>> histogram.observe(0.05)
            >>> histogram.observe(0.25)
            >>> histogram.observe(0.75)
            >>> summary = histogram.get_summary()
            >>> summary["count"]
            3
            >>> summary["buckets"]["0.1"]
            1
        """
        key = self._label_key(labels)

        with self._lock:
            return {
                "count": self._counts.get(key, 0),
                "sum": self._sums.get(key, 0),
                "buckets": {
                    str(bucket): self._buckets.get(key, {}).get(bucket, 0)
                    for bucket in self.buckets
                },
            }

    def get_all_summaries(self) -> Dict[str, Dict[str, Any]]:
        """Get summaries for all label combinations."""
        with self._lock:
            return {
                key: {
                    "count": self._counts.get(key, 0),
                    "sum": self._sums.get(key, 0),
                    "buckets": {
                        str(bucket): self._buckets.get(key, {}).get(bucket, 0)
                        for bucket in self.buckets
                    },
                }
                for key in set(self._buckets.keys()) | set(self._sums.keys()) | set(self._counts.keys())
            }

    def _label_key(self, labels: Optional[Dict[str, str]]) -> str:
        """Convert labels to unique key."""
        if not labels:
            return ""
        sorted_labels = sorted(labels.items())
        return ",".join(f'{k}="{v}"' for k, v in sorted_labels if k in self.label_names or not self.label_names)

    def reset(self) -> None:
        """Reset all histogram data."""
        with self._lock:
            self._buckets.clear()
            self._sums.clear()
            self._counts.clear()


class MetricsCollector:
    """
    Central metrics collection with Prometheus-compatible export.

    Supports three metric types:
    - Counter: Monotonically increasing value (requests, errors)
    - Gauge: Point-in-time value (queue size, temperature)
    - Histogram: Distribution of values (latencies, sizes)

    Features:
        - Thread-safe metric operations
        - Automatic label handling
        - Prometheus text format export
        - Integration with cache stats

    Example:
        >>> metrics = MetricsCollector(prefix="gaia")
        >>>
        >>> # Counter
        >>> requests = metrics.counter("http_requests_total")
        >>> requests.inc()
        >>> requests.inc(labels={"method": "POST", "status": "200"})
        >>>
        >>> # Gauge
        >>> queue_size = metrics.gauge("queue_size")
        >>> queue_size.set(42)
        >>>
        >>> # Histogram
        >>> latency = metrics.histogram("request_latency_seconds")
        >>> latency.observe(0.125)
        >>>
        >>> # Export
        >>> prometheus_output = metrics.to_prometheus()
    """

    def __init__(self, prefix: str = "gaia") -> None:
        """
        Initialize metrics collector.

        Args:
            prefix: Metric name prefix (default: "gaia")

        Example:
            >>> metrics = MetricsCollector(prefix="myapp")
        """
        self.prefix = prefix
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._lock = threading.RLock()

    def counter(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
    ) -> Counter:
        """
        Get or create counter metric.

        Args:
            name: Metric name (prefix auto-added)
            description: Metric description
            label_names: Optional label names for filtering

        Returns:
            Counter instance

        Example:
            >>> metrics = MetricsCollector()
            >>> counter = metrics.counter(
            ...     "http_requests_total",
            ...     description="Total HTTP requests",
            ...     label_names=["method", "status"]
            ... )
        """
        full_name = f"{self.prefix}_{name}" if self.prefix else name

        with self._lock:
            if full_name not in self._counters:
                self._counters[full_name] = Counter(
                    name=full_name,
                    description=description,
                    label_names=label_names,
                )
            return self._counters[full_name]

    def gauge(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
    ) -> Gauge:
        """
        Get or create gauge metric.

        Args:
            name: Metric name
            description: Metric description
            label_names: Optional label names

        Returns:
            Gauge instance

        Example:
            >>> metrics = MetricsCollector()
            >>> gauge = metrics.gauge(
            ...     "queue_size",
            ...     description="Current queue size"
            ... )
        """
        full_name = f"{self.prefix}_{name}" if self.prefix else name

        with self._lock:
            if full_name not in self._gauges:
                self._gauges[full_name] = Gauge(
                    name=full_name,
                    description=description,
                    label_names=label_names,
                )
            return self._gauges[full_name]

    def histogram(
        self,
        name: str,
        description: str = "",
        label_names: Optional[List[str]] = None,
        buckets: Optional[List[float]] = None,
    ) -> Histogram:
        """
        Get or create histogram metric.

        Args:
            name: Metric name
            description: Metric description
            label_names: Optional label names
            buckets: Histogram bucket boundaries

        Returns:
            Histogram instance

        Example:
            >>> metrics = MetricsCollector()
            >>> histogram = metrics.histogram(
            ...     "request_latency_seconds",
            ...     buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0]
            ... )
        """
        full_name = f"{self.prefix}_{name}" if self.prefix else name

        with self._lock:
            if full_name not in self._histograms:
                self._histograms[full_name] = Histogram(
                    name=full_name,
                    description=description,
                    label_names=label_names,
                    buckets=buckets,
                )
            return self._histograms[full_name]

    def to_prometheus(self) -> str:
        """
        Export metrics in Prometheus text format.

        Returns:
            Prometheus-formatted metrics string

        Example output:
            # HELP gaia_http_requests_total Total HTTP requests
            # TYPE gaia_http_requests_total counter
            gaia_http_requests_total{method="GET",status="200"} 150
            gaia_http_requests_total{method="POST",status="200"} 75

            # HELP gaia_request_latency_seconds Request latency histogram
            # TYPE gaia_request_latency_seconds histogram
            gaia_request_latency_seconds_bucket{le="0.01"} 10
            gaia_request_latency_seconds_bucket{le="+Inf"} 150
            gaia_request_latency_seconds_sum 12.5
            gaia_request_latency_seconds_count 150
        """
        lines = []

        with self._lock:
            # Export counters
            for name, counter in self._counters.items():
                lines.append(f"# HELP {name} {counter.description}")
                lines.append(f"# TYPE {name} counter")
                for key, value in counter.get_all().items():
                    if key:
                        lines.append(f'{name}{{{key}}} {value}')
                    else:
                        lines.append(f"{name} {value}")
                lines.append("")

            # Export gauges
            for name, gauge in self._gauges.items():
                lines.append(f"# HELP {name} {gauge.description}")
                lines.append(f"# TYPE {name} gauge")
                for key, value in gauge.get_all().items():
                    if key:
                        lines.append(f'{name}{{{key}}} {float(value)}')
                    else:
                        lines.append(f"{name} {float(value)}")
                lines.append("")

            # Export histograms
            for name, histogram in self._histograms.items():
                lines.append(f"# HELP {name} {histogram.description}")
                lines.append(f"# TYPE {name} histogram")

                for key, summary in histogram.get_all_summaries().items():
                    # Output bucket counts
                    for bucket, count in sorted(
                        summary["buckets"].items(),
                        key=lambda x: float(x[0]) if x[0] != "+Inf" else float("inf")
                    ):
                        label_part = f'{{{key},le="{bucket}"}}' if key else f'{{le="{bucket}"}}'
                        lines.append(f"{name}_bucket{label_part} {count}")

                    # Output +Inf bucket (total count)
                    label_part = f'{{{key},le="+Inf"}}' if key else f'{{le="+Inf"}}'
                    lines.append(f"{name}_bucket{label_part} {summary['count']}")

                    # Output sum and count
                    if key:
                        lines.append(f'{name}_sum{{{key}}} {summary["sum"]}')
                        lines.append(f'{name}_count{{{key}}} {summary["count"]}')
                    else:
                        lines.append(f"{name}_sum {summary['sum']}")
                        lines.append(f"{name}_count {summary['count']}")

                lines.append("")

        return "\n".join(lines)

    def get_summary(self) -> Dict[str, Any]:
        """
        Get summary of all metrics as dictionary.

        Returns:
            Dictionary with counters, gauges, and histograms data

        Example:
            >>> metrics = MetricsCollector()
            >>> metrics.counter("requests").inc(5)
            >>> summary = metrics.get_summary()
            >>> "counters" in summary
            True
        """
        with self._lock:
            return {
                "counters": {
                    name: counter.get_all()
                    for name, counter in self._counters.items()
                },
                "gauges": {
                    name: gauge.get_all()
                    for name, gauge in self._gauges.items()
                },
                "histograms": {
                    name: histogram.get_all_summaries()
                    for name, histogram in self._histograms.items()
                },
            }

    def clear(self) -> None:
        """Clear all metrics."""
        with self._lock:
            for counter in self._counters.values():
                counter.reset()
            for gauge in self._gauges.values():
                gauge.reset()
            for histogram in self._histograms.values():
                histogram.reset()

    def integrate_cache_stats(self, cache_stats: Any) -> None:
        """
        Integrate cache statistics (from Sprint 3 CacheStats).

        Args:
            cache_stats: Cache statistics object with hits, misses, etc.

        Example:
            >>> from gaia.cache import CacheStats
            >>> metrics = MetricsCollector()
            >>> cache_stats = CacheStats(hits=80, misses=20)
            >>> metrics.integrate_cache_stats(cache_stats)
        """
        # Import here to avoid circular dependency
        try:
            hits = getattr(cache_stats, "hits", 0)
            misses = getattr(cache_stats, "misses", 0)
            memory_size = getattr(cache_stats, "memory_size", 0)
            disk_size = getattr(cache_stats, "disk_size", 0)

            # Update cache metrics
            cache_hits = self.counter(
                "cache_hits",
                description="Total cache hits"
            )
            cache_hits.inc(hits)

            cache_misses = self.counter(
                "cache_misses",
                description="Total cache misses"
            )
            cache_misses.inc(misses)

            # Calculate and set hit rate
            total = hits + misses
            if total > 0:
                hit_rate = (hits / total) * 100
                cache_hit_rate = self.gauge(
                    "cache_hit_rate",
                    description="Cache hit rate percentage"
                )
                cache_hit_rate.set(hit_rate)

            # Set size gauges
            cache_memory = self.gauge(
                "cache_memory_bytes",
                description="Cache memory size in bytes"
            )
            cache_memory.set(memory_size)

            cache_disk = self.gauge(
                "cache_disk_bytes",
                description="Cache disk size in bytes"
            )
            cache_disk.set(disk_size)

        except Exception:
            # Silently ignore if cache_stats is incompatible
            pass


# Global metrics collector instance
_default_metrics: Optional[MetricsCollector] = None
_metrics_lock = threading.Lock()


def get_metrics_collector(prefix: str = "gaia") -> MetricsCollector:
    """
    Get the global metrics collector instance.

    Args:
        prefix: Metric name prefix

    Returns:
        Global MetricsCollector instance

    Example:
        >>> metrics = get_metrics_collector()
        >>> metrics.counter("requests").inc()
    """
    global _default_metrics
    with _metrics_lock:
        if _default_metrics is None:
            _default_metrics = MetricsCollector(prefix=prefix)
        return _default_metrics
