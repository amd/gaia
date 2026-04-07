"""
Unit tests for MetricsCollector.

Covers:
- Counter metrics
- Gauge metrics
- Histogram metrics
- Prometheus export format
- Thread safety
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from gaia.observability.metrics import (
    MetricsCollector,
    Counter,
    Gauge,
    Histogram,
    MetricPoint,
    get_metrics_collector,
)


class TestCounter:
    """Test Counter metric."""

    def test_counter_init(self):
        """Should initialize counter correctly."""
        counter = Counter("test_counter", description="Test counter")

        assert counter.name == "test_counter"
        assert counter.description == "Test counter"
        assert counter.get() == 0

    def test_counter_inc(self):
        """Should increment counter."""
        counter = Counter("test_counter")

        counter.inc()
        assert counter.get() == 1

        counter.inc(5)
        assert counter.get() == 6

    def test_counter_inc_with_labels(self):
        """Should increment counter with labels."""
        counter = Counter("test_counter", label_names=["method", "status"])

        counter.inc(labels={"method": "GET", "status": "200"})
        counter.inc(2, labels={"method": "POST", "status": "201"})

        assert counter.get(labels={"method": "GET", "status": "200"}) == 1
        assert counter.get(labels={"method": "POST", "status": "201"}) == 2

    def test_counter_inc_negative_raises(self):
        """Should raise on negative increment."""
        counter = Counter("test_counter")

        with pytest.raises(ValueError, match="non-negative"):
            counter.inc(-1)

    def test_counter_get_all(self):
        """Should return all counter values."""
        counter = Counter("test_counter", label_names=["method"])

        counter.inc(labels={"method": "GET"})
        counter.inc(labels={"method": "POST"})
        counter.inc(labels={"method": "GET"})

        all_values = counter.get_all()

        assert len(all_values) == 2

    def test_counter_reset(self):
        """Should reset counter values."""
        counter = Counter("test_counter")
        counter.inc(5)
        counter.reset()

        assert counter.get() == 0


class TestGauge:
    """Test Gauge metric."""

    def test_gauge_init(self):
        """Should initialize gauge correctly."""
        gauge = Gauge("test_gauge", description="Test gauge")

        assert gauge.name == "test_gauge"
        assert gauge.description == "Test gauge"

    def test_gauge_set(self):
        """Should set gauge value."""
        gauge = Gauge("test_gauge")

        gauge.set(42)
        assert gauge.get() == 42

        gauge.set(100.5)
        assert gauge.get() == 100.5

    def test_gauge_set_with_labels(self):
        """Should set gauge value with labels."""
        gauge = Gauge("test_gauge", label_names=["sensor"])

        gauge.set(21.5, labels={"sensor": "room1"})
        gauge.set(23.0, labels={"sensor": "room2"})

        assert gauge.get(labels={"sensor": "room1"}) == 21.5
        assert gauge.get(labels={"sensor": "room2"}) == 23.0

    def test_gauge_inc(self):
        """Should increment gauge."""
        gauge = Gauge("test_gauge")

        gauge.set(10)
        gauge.inc()
        assert gauge.get() == 11

        gauge.inc(5)
        assert gauge.get() == 16

    def test_gauge_dec(self):
        """Should decrement gauge."""
        gauge = Gauge("test_gauge")

        gauge.set(10)
        gauge.dec()
        assert gauge.get() == 9

        gauge.dec(5)
        assert gauge.get() == 4

    def test_gauge_get_all(self):
        """Should return all gauge values."""
        gauge = Gauge("test_gauge", label_names=["sensor"])

        gauge.set(21.5, labels={"sensor": "room1"})
        gauge.set(23.0, labels={"sensor": "room2"})

        all_values = gauge.get_all()
        assert len(all_values) == 2

    def test_gauge_reset(self):
        """Should reset gauge values."""
        gauge = Gauge("test_gauge")
        gauge.set(42)
        gauge.reset()

        assert gauge.get() == 0


class TestHistogram:
    """Test Histogram metric."""

    def test_histogram_init(self):
        """Should initialize histogram correctly."""
        histogram = Histogram("test_histogram", description="Test histogram")

        assert histogram.name == "test_histogram"
        assert histogram.description == "Test histogram"
        assert histogram.buckets == Histogram.DEFAULT_BUCKETS

    def test_histogram_custom_buckets(self):
        """Should use custom buckets."""
        custom_buckets = [0.1, 0.5, 1.0, 5.0]
        histogram = Histogram("test_histogram", buckets=custom_buckets)

        assert histogram.buckets == custom_buckets

    def test_histogram_observe(self):
        """Should record observations."""
        histogram = Histogram("test_histogram", buckets=[0.1, 0.5, 1.0])

        histogram.observe(0.05)
        histogram.observe(0.25)
        histogram.observe(0.75)
        histogram.observe(1.5)

        summary = histogram.get_summary()

        assert summary["count"] == 4
        assert summary["sum"] == 2.55

    def test_histogram_buckets(self):
        """Should track bucket counts correctly."""
        histogram = Histogram("test_histogram", buckets=[0.1, 0.5, 1.0])

        histogram.observe(0.05)  # In 0.1 bucket
        histogram.observe(0.25)  # In 0.5 bucket
        histogram.observe(0.75)  # In 1.0 bucket
        histogram.observe(1.5)   # In +Inf bucket

        summary = histogram.get_summary()
        buckets = summary["buckets"]

        assert buckets["0.1"] == 1
        assert buckets["0.5"] == 2
        assert buckets["1.0"] == 3

    def test_histogram_with_labels(self):
        """Should handle labels."""
        histogram = Histogram("test_histogram", label_names=["endpoint"])

        histogram.observe(0.1, labels={"endpoint": "/api/users"})
        histogram.observe(0.2, labels={"endpoint": "/api/users"})
        histogram.observe(0.3, labels={"endpoint": "/api/posts"})

        users_summary = histogram.get_summary(labels={"endpoint": "/api/users"})
        posts_summary = histogram.get_summary(labels={"endpoint": "/api/posts"})

        assert users_summary["count"] == 2
        assert posts_summary["count"] == 1

    def test_histogram_get_all_summaries(self):
        """Should return all summaries."""
        histogram = Histogram("test_histogram", label_names=["endpoint"])

        histogram.observe(0.1, labels={"endpoint": "/api/users"})
        histogram.observe(0.2, labels={"endpoint": "/api/posts"})

        all_summaries = histogram.get_all_summaries()

        assert len(all_summaries) == 2

    def test_histogram_reset(self):
        """Should reset histogram data."""
        histogram = Histogram("test_histogram", buckets=[0.1, 0.5])

        histogram.observe(0.05)
        histogram.observe(0.25)
        histogram.reset()

        summary = histogram.get_summary()
        assert summary["count"] == 0


class TestMetricsCollector:
    """Test MetricsCollector."""

    def test_collector_init(self):
        """Should initialize collector correctly."""
        metrics = MetricsCollector(prefix="test")

        assert metrics.prefix == "test"

    def test_collector_counter(self):
        """Should create counter metrics."""
        metrics = MetricsCollector(prefix="test")

        counter = metrics.counter("requests", description="Total requests")

        assert counter.name == "test_requests"
        assert counter.description == "Total requests"

    def test_collector_counter_cached(self):
        """Should return cached counter."""
        metrics = MetricsCollector(prefix="test")

        counter1 = metrics.counter("requests")
        counter2 = metrics.counter("requests")

        assert counter1 is counter2

    def test_collector_gauge(self):
        """Should create gauge metrics."""
        metrics = MetricsCollector(prefix="test")

        gauge = metrics.gauge("temperature", description="Current temperature")

        assert gauge.name == "test_temperature"

    def test_collector_histogram(self):
        """Should create histogram metrics."""
        metrics = MetricsCollector(prefix="test")

        histogram = metrics.histogram("latency", buckets=[0.1, 0.5, 1.0])

        assert histogram.name == "test_latency"
        assert histogram.buckets == [0.1, 0.5, 1.0]

    def test_collector_to_prometheus(self):
        """Should export metrics in Prometheus format."""
        metrics = MetricsCollector(prefix="test")

        counter = metrics.counter("requests_total", description="Total requests")
        counter.inc()
        counter.inc(labels={"method": "GET"})

        gauge = metrics.gauge("queue_size", description="Queue size")
        gauge.set(42)

        output = metrics.to_prometheus()

        assert "# HELP test_requests_total Total requests" in output
        assert "# TYPE test_requests_total counter" in output
        assert "# HELP test_queue_size Queue size" in output
        assert "# TYPE test_queue_size gauge" in output
        assert 'test_queue_size 42.0' in output

    def test_collector_prometheus_histogram(self):
        """Should export histogram in Prometheus format."""
        metrics = MetricsCollector(prefix="test")

        histogram = metrics.histogram("latency", buckets=[0.1, 0.5, 1.0])
        histogram.observe(0.05)
        histogram.observe(0.25)
        histogram.observe(0.75)

        output = metrics.to_prometheus()

        assert "# HELP test_latency" in output
        assert "# TYPE test_latency histogram" in output
        assert 'test_latency_bucket{le="0.1"} 1' in output
        assert 'test_latency_bucket{le="0.5"} 2' in output
        assert 'test_latency_bucket{le="1.0"} 3' in output
        assert 'test_latency_bucket{le="+Inf"} 3' in output
        assert "test_latency_sum" in output
        assert "test_latency_count" in output

    def test_collector_get_summary(self):
        """Should return summary dictionary."""
        metrics = MetricsCollector(prefix="test")

        metrics.counter("requests").inc(5)
        metrics.gauge("temperature").set(21.5)

        summary = metrics.get_summary()

        assert "counters" in summary
        assert "gauges" in summary
        assert "histograms" in summary

    def test_collector_clear(self):
        """Should clear all metrics."""
        metrics = MetricsCollector(prefix="test")

        metrics.counter("requests").inc(5)
        metrics.gauge("temperature").set(42)
        metrics.histogram("latency").observe(0.1)

        metrics.clear()

        summary = metrics.get_summary()
        assert len(summary["counters"]) == 0 or all(
            v == 0 for values in summary["counters"].values() for v in values.values()
        )

    def test_collector_integrate_cache_stats(self):
        """Should integrate cache statistics."""
        metrics = MetricsCollector(prefix="gaia")

        # Create mock cache stats object
        class CacheStats:
            hits = 80
            misses = 20
            memory_size = 1024
            disk_size = 512

        cache_stats = CacheStats()
        metrics.integrate_cache_stats(cache_stats)

        output = metrics.to_prometheus()

        assert "gaia_cache_hits" in output
        assert "gaia_cache_misses" in output
        assert "gaia_cache_hit_rate" in output


class TestMetricsThreadSafety:
    """Test thread safety of metrics."""

    def test_counter_thread_safety(self):
        """Counter should be thread-safe."""
        metrics = MetricsCollector(prefix="test")
        counter = metrics.counter("thread_counter")

        def increment(n: int):
            for _ in range(n):
                counter.inc()

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(increment, 10) for _ in range(10)]
            for f in futures:
                f.result()

        assert counter.get() == 100

    def test_gauge_thread_safety(self):
        """Gauge should be thread-safe."""
        metrics = MetricsCollector(prefix="test")
        gauge = metrics.gauge("thread_gauge")

        def set_value(val: float):
            for _ in range(10):
                gauge.set(val)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(set_value, float(i)) for i in range(10)]
            for f in futures:
                f.result()

        # Should have some value set
        assert gauge.get() >= 0

    def test_histogram_thread_safety(self):
        """Histogram should be thread-safe."""
        metrics = MetricsCollector(prefix="test")
        histogram = metrics.histogram("thread_histogram")

        def observe(n: int):
            for i in range(n):
                histogram.observe(float(i) / 10)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(observe, 10) for _ in range(10)]
            for f in futures:
                f.result()

        summary = histogram.get_summary()
        assert summary["count"] == 100

    def test_concurrent_metrics_updates(self):
        """Metrics should handle 100+ concurrent updates safely."""
        metrics = MetricsCollector(prefix="test")
        counter = metrics.counter("concurrent_requests")

        def increment():
            counter.inc()

        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(increment) for _ in range(100)]
            for f in futures:
                f.result()

        assert counter.get() == 100


class TestMetricPoint:
    """Test MetricPoint dataclass."""

    def test_metric_point_creation(self):
        """Should create metric point correctly."""
        point = MetricPoint(
            name="test_metric",
            value=42.5,
            labels={"key": "value"},
        )

        assert point.name == "test_metric"
        assert point.value == 42.5
        assert point.labels == {"key": "value"}
        assert point.timestamp > 0

    def test_metric_point_default_timestamp(self):
        """Should have current timestamp by default."""
        before = time.time()
        point = MetricPoint(name="test", value=1)
        after = time.time()

        assert before <= point.timestamp <= after


class TestGetMetricsCollector:
    """Test get_metrics_collector function."""

    def test_get_metrics_collector_singleton(self):
        """Should return singleton instance."""
        metrics1 = get_metrics_collector(prefix="test")
        metrics2 = get_metrics_collector(prefix="test")

        assert metrics1 is metrics2


class TestMetricsExportAccuracy:
    """Test metrics export accuracy."""

    def test_counter_prometheus_format(self):
        """Counter should export correctly in Prometheus format."""
        metrics = MetricsCollector(prefix="test")
        counter = metrics.counter("requests_total", description="Total requests")
        counter.inc()
        counter.inc(labels={"method": "GET", "status": "200"})
        counter.inc(5, labels={"method": "POST", "status": "201"})

        output = metrics.to_prometheus()

        assert "# HELP test_requests_total Total requests" in output
        assert "# TYPE test_requests_total counter" in output
        assert 'test_requests_total{method="GET",status="200"} 1' in output
        assert 'test_requests_total{method="POST",status="201"} 5' in output

    def test_gauge_prometheus_format(self):
        """Gauge should export correctly in Prometheus format."""
        metrics = MetricsCollector(prefix="test")
        gauge = metrics.gauge("queue_size", description="Current queue size")
        gauge.set(42)

        output = metrics.to_prometheus()

        assert "# HELP test_queue_size Current queue size" in output
        assert "# TYPE test_queue_size gauge" in output
        assert "test_queue_size 42.0" in output

    def test_histogram_prometheus_format(self):
        """Histogram should export correctly in Prometheus format."""
        metrics = MetricsCollector(prefix="test")
        histogram = metrics.histogram(
            "latency_seconds",
            description="Request latency",
            buckets=[0.1, 0.5, 1.0],
        )
        histogram.observe(0.05)
        histogram.observe(0.25)
        histogram.observe(0.75)
        histogram.observe(1.5)

        output = metrics.to_prometheus()

        assert "# HELP test_latency_seconds Request latency" in output
        assert "# TYPE test_latency_seconds histogram" in output
        assert 'test_latency_seconds_bucket{le="0.1"} 1' in output
        assert 'test_latency_seconds_bucket{le="0.5"} 2' in output
        assert 'test_latency_seconds_bucket{le="1.0"} 3' in output
        assert 'test_latency_seconds_bucket{le="+Inf"} 4' in output
        assert "test_latency_seconds_sum" in output
        assert "test_latency_seconds_count 4" in output
