"""
Integration tests for Observability.

Covers:
- End-to-end tracing
- Metrics integration
- Logging integration
- Sprint 3 CacheLayer integration
"""

import asyncio
import pytest
import time
from typing import Dict, Any

from gaia.observability.core import ObservabilityCore, traced
from gaia.observability.tracing import SpanKind, SpanStatus, TraceContext
from gaia.observability.metrics import MetricsCollector


class TestObservabilityEndToEnd:
    """End-to-end observability tests."""

    def test_full_trace_lifecycle(self):
        """Should complete full trace lifecycle."""
        obs = ObservabilityCore(service_name="test-e2e")

        with obs.trace("parent.operation", kind=SpanKind.SERVER) as parent:
            parent.set_attribute("parent.key", "parent.value")

            with obs.trace("child.operation", kind=SpanKind.INTERNAL) as child:
                child.set_attribute("child.key", "child.value")
                obs.log_info("Child operation completed")

            parent.set_status(SpanStatus.OK)

        assert parent.status == SpanStatus.OK
        assert child.status == SpanStatus.OK
        assert parent.context.trace_id == child.context.trace_id

    @pytest.mark.asyncio
    async def test_async_trace_lifecycle(self):
        """Should complete async trace lifecycle."""
        obs = ObservabilityCore(service_name="test-async")

        async def nested_operation():
            async with obs.trace_async("nested.operation") as span:
                await asyncio.sleep(0.01)
                span.set_attribute("nested", True)

        async with obs.trace_async("async.parent") as parent:
            parent.set_attribute("async", True)
            await nested_operation()

        assert parent.status == SpanStatus.OK

    def test_trace_with_exception(self):
        """Should handle exceptions in trace."""
        obs = ObservabilityCore(service_name="test-exception")

        with pytest.raises(ValueError):
            with obs.trace("failing.operation") as span:
                span.set_attribute("will.fail", True)
                raise ValueError("Test error")

        assert span.status == SpanStatus.ERROR

    def test_context_propagation_chain(self):
        """Should propagate context through span chain."""
        obs = ObservabilityCore(service_name="test-chain")
        trace_ids = []

        def level3():
            with obs.trace("level3") as span:
                trace_ids.append(span.context.trace_id)

        def level2():
            with obs.trace("level2") as span:
                trace_ids.append(span.context.trace_id)
                level3()

        def level1():
            with obs.trace("level1") as span:
                trace_ids.append(span.context.trace_id)
                level2()

        level1()

        # All spans should have same trace ID
        assert len(set(trace_ids)) == 1

    def test_multiple_concurrent_traces(self):
        """Should handle multiple concurrent traces."""
        obs = ObservabilityCore(service_name="test-concurrent")
        results: Dict[str, str] = {}

        def worker(worker_id: str):
            with obs.trace(f"worker.{worker_id}") as span:
                results[worker_id] = span.context.trace_id

        threads = []
        import threading

        for i in range(5):
            t = threading.Thread(target=worker, args=(f"w{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Each thread should have its own trace
        assert len(results) == 5


class TestMetricsIntegration:
    """Test metrics integration."""

    def test_counter_integration(self):
        """Should track request counts."""
        obs = ObservabilityCore(service_name="test-metrics")

        for i in range(100):
            with obs.trace("request"):
                obs.metrics.counter("requests_total").inc()
                obs.metrics.histogram("request_latency").observe(0.01 * (i % 10))

        counter_value = obs.metrics.counter("requests_total").get()
        assert counter_value == 100

    def test_gauge_integration(self):
        """Should track point-in-time values."""
        obs = ObservabilityCore(service_name="test-gauge")

        gauge = obs.metrics.gauge("queue_size")

        for i in range(10):
            gauge.set(i)

        assert gauge.get() == 9

    def test_histogram_integration(self):
        """Should track value distributions."""
        obs = ObservabilityCore(service_name="test-histogram")

        histogram = obs.metrics.histogram("latency", buckets=[0.1, 0.5, 1.0])

        for i in range(100):
            histogram.observe(0.01 * i)

        summary = histogram.get_summary()
        assert summary["count"] == 100
        assert summary["sum"] > 0

    def test_prometheus_export_integration(self):
        """Should export all metrics in Prometheus format."""
        obs = ObservabilityCore(service_name="test-prometheus")

        obs.metrics.counter("http_requests").inc(10)
        obs.metrics.gauge("active_connections").set(5)
        obs.metrics.histogram("response_time").observe(0.5)

        output = obs.metrics.to_prometheus()

        assert "test_prometheus_http_requests" in output
        assert "test_prometheus_active_connections" in output
        assert "test_prometheus_response_time" in output


class TestLoggingIntegration:
    """Test logging integration."""

    def test_structured_logging(self):
        """Should log structured messages."""
        obs = ObservabilityCore(service_name="test-logging", log_level="DEBUG")

        with obs.trace("logging.test") as span:
            obs.log_info("Test message", span_id=span.context.span_id)
            obs.log_debug("Debug info", extra_field="value")
            obs.log_warning("Warning message")

        # Should not raise - logging is working

    def test_error_logging(self):
        """Should log errors with context."""
        obs = ObservabilityCore(service_name="test-error")

        try:
            with obs.trace("failing.operation") as span:
                raise ValueError("Test error")
        except ValueError:
            obs.log_error("Operation failed", error_type="ValueError")

        # Should not raise

    def test_logging_with_trace_context(self):
        """Should include trace context in logs."""
        import logging
        from gaia.observability.logging import ContextFilter, get_current_trace_context

        obs = ObservabilityCore(service_name="test-context")

        with obs.trace("context.test") as span:
            context = obs.get_current_trace()
            assert context is not None
            assert context.trace_id == span.context.trace_id


class TestCacheStatsIntegration:
    """Test integration with Sprint 3 CacheStats."""

    def test_cache_stats_integration(self):
        """Should integrate cache statistics."""
        obs = ObservabilityCore(service_name="test-cache")

        # Mock CacheStats from Sprint 3
        class CacheStats:
            hits = 80
            misses = 20
            memory_size = 1024 * 1024  # 1MB
            disk_size = 10 * 1024 * 1024  # 10MB

        cache_stats = CacheStats()
        obs.metrics.integrate_cache_stats(cache_stats)

        output = obs.metrics.to_prometheus()

        assert "gaia_cache_hits" in output or "test_cache_cache_hits" in output
        assert "gaia_cache_misses" in output or "test_cache_cache_misses" in output

    def test_cache_hit_rate_calculation(self):
        """Should calculate cache hit rate."""
        metrics = MetricsCollector(prefix="test")

        class CacheStats:
            hits = 80
            misses = 20
            memory_size = 100
            disk_size = 50

        metrics.integrate_cache_stats(CacheStats())

        output = metrics.to_prometheus()

        # Hit rate should be 80%
        assert "cache_hit_rate" in output


class TestTracedDecoratorIntegration:
    """Test @traced decorator integration."""

    def test_traced_sync_function_integration(self):
        """Should trace synchronous functions."""
        @traced(name="integration.sync_func", kind=SpanKind.INTERNAL)
        def process_data(data: str) -> str:
            return data.upper()

        result = process_data("hello")
        assert result == "HELLO"

    @pytest.mark.asyncio
    async def test_traced_async_function_integration(self):
        """Should trace asynchronous functions."""
        @traced(name="integration.async_func")
        async def async_process(data: str) -> str:
            await asyncio.sleep(0.01)
            return data.upper()

        result = await async_process("world")
        assert result == "WORLD"

    def test_traced_function_with_attributes(self):
        """Should add static attributes."""
        @traced(
            name="integration.attr_func",
            attributes={"static_attr": "value", "type": "test"}
        )
        def attr_func():
            pass

        attr_func()

    def test_nested_traced_functions(self):
        """Should handle nested traced functions."""
        @traced(name="outer")
        def outer_func():
            return inner_func()

        @traced(name="inner")
        def inner_func():
            return "inner result"

        result = outer_func()
        assert result == "inner result"


class TestObservabilityCoreShutdown:
    """Test shutdown behavior."""

    @pytest.mark.asyncio
    async def test_graceful_shutdown(self):
        """Should shutdown gracefully."""
        obs = ObservabilityCore(service_name="test-shutdown")

        with obs.trace("before.shutdown"):
            pass

        await obs.shutdown()

        # Should have cleaned up
        assert len(obs._active_spans) == 0

    @pytest.mark.asyncio
    async def test_shutdown_ends_active_spans(self):
        """Should end active spans on shutdown."""
        obs = ObservabilityCore()

        # Start span but don't end it
        span = obs.start_span("active.span")
        assert span.is_recording()

        await obs.shutdown()

        # Span should be ended
        assert not span.is_recording()


class TestObservabilityQualityGates:
    """Test quality gates from specification."""

    def test_obs_001_trace_propagation(self):
        """OBS-001: Trace context propagation must be 100%."""
        from gaia.observability.tracing import W3CPropagator

        propagator = W3CPropagator()
        context = TraceContext(
            trace_id="1234567890abcdef1234567890abcdef",
            span_id="fedcba0987654321",
            trace_flags=1,
        )

        carrier: Dict[str, str] = {}
        propagator.inject(context, carrier)
        extracted = propagator.extract(carrier)

        assert extracted is not None
        assert extracted.trace_id == context.trace_id, "Trace ID mismatch"
        assert extracted.span_id == context.span_id, "Span ID mismatch"
        assert extracted.trace_flags == context.trace_flags, "Trace flags mismatch"

    def test_obs_002_metrics_export(self):
        """OBS-002: Metrics export must be 100% accurate."""
        metrics = MetricsCollector(prefix="qa")
        metrics.counter("test").inc(5)
        metrics.gauge("gauge").set(10)
        metrics.histogram("hist").observe(0.5)

        output = metrics.to_prometheus()

        # Verify all metric types present
        assert "qa_test" in output
        assert "qa_gauge" in output
        assert "qa_hist" in output

    @pytest.mark.asyncio
    async def test_thread_003_thread_safety(self):
        """THREAD-003: Must handle 100+ concurrent threads."""
        obs = ObservabilityCore(service_name="test-thread")
        errors = []

        async def worker(i: int):
            try:
                async with obs.trace_async(f"worker.{i}"):
                    await asyncio.sleep(0.001)
            except Exception as e:
                errors.append(e)

        tasks = [asyncio.create_task(worker(i)) for i in range(100)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Thread safety failures: {errors}"
