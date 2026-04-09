"""
Unit tests for ObservabilityCore.

Covers:
- Span creation and lifecycle
- Trace context propagation
- Structured logging
- Async context management
- Thread safety
"""

import asyncio
import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any

from gaia.observability.core import ObservabilityCore, traced, get_observability
from gaia.observability.tracing import (
    Span,
    SpanKind,
    SpanStatus,
    TraceContext,
    W3CPropagator,
    B3Propagator,
    get_current_trace_context,
    set_current_trace_context,
)
from gaia.observability.logging import JSONFormatter, ContextFilter
from gaia.observability.metrics import MetricsCollector


class TestObservabilityCoreInit:
    """Test ObservabilityCore initialization."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        obs = ObservabilityCore()

        assert obs.service_name == "gaia"
        assert obs.log_level == "INFO"
        assert obs.enable_tracing is True
        assert obs.enable_metrics is True
        assert obs.log_format == "json"

    def test_init_custom_values(self):
        """Should initialize with custom values."""
        obs = ObservabilityCore(
            service_name="test-service",
            log_level="DEBUG",
            enable_tracing=False,
            enable_metrics=False,
            log_format="text",
        )

        assert obs.service_name == "test-service"
        assert obs.log_level == "DEBUG"
        assert obs.enable_tracing is False
        assert obs.enable_metrics is False
        assert obs.log_format == "text"

    def test_init_with_custom_propagator(self):
        """Should use custom propagator."""
        propagator = B3Propagator()
        obs = ObservabilityCore(propagator=propagator)

        assert obs.propagator is propagator

    def test_metrics_property(self):
        """Should return metrics collector."""
        obs = ObservabilityCore(enable_metrics=True)
        metrics = obs.metrics

        assert isinstance(metrics, MetricsCollector)

    def test_metrics_property_when_disabled(self):
        """Should raise when metrics disabled."""
        obs = ObservabilityCore(enable_metrics=False)

        with pytest.raises(RuntimeError, match="Metrics collection is disabled"):
            _ = obs.metrics

    def test_logger_property(self):
        """Should return logger."""
        obs = ObservabilityCore()
        logger = obs.logger

        assert logger is not None


class TestSpanManagement:
    """Test span creation and lifecycle."""

    def test_start_span_basic(self):
        """Should create basic span."""
        obs = ObservabilityCore()
        span = obs.start_span("test.operation")

        assert span.name == "test.operation"
        assert span.kind == SpanKind.INTERNAL
        assert span.is_recording() is True
        assert span.start_time is not None

    def test_start_span_with_kind(self):
        """Should create span with specified kind."""
        obs = ObservabilityCore()
        span = obs.start_span("http.request", kind=SpanKind.SERVER)

        assert span.kind == SpanKind.SERVER

    def test_start_span_with_attributes(self):
        """Should create span with initial attributes."""
        obs = ObservabilityCore()
        span = obs.start_span("db.query", attributes={"db.system": "postgresql"})

        assert span.attributes.get("db.system") == "postgresql"

    def test_end_span(self):
        """Should end span correctly."""
        obs = ObservabilityCore()
        span = obs.start_span("test.operation")

        obs.end_span(span, SpanStatus.OK)

        assert span.is_recording() is False
        assert span.status == SpanStatus.OK
        assert span.end_time is not None

    def test_span_duration(self):
        """Should calculate span duration."""
        obs = ObservabilityCore()
        span = obs.start_span("test.operation")

        time.sleep(0.05)

        obs.end_span(span)

        assert span.duration >= 0.05

    def test_trace_context_manager(self):
        """Should manage span lifecycle via context manager."""
        obs = ObservabilityCore()

        with obs.trace("test.operation") as span:
            assert span.is_recording() is True
            span.set_attribute("key", "value")

        assert span.is_recording() is False
        assert span.status == SpanStatus.OK
        assert span.attributes.get("key") == "value"

    def test_trace_context_manager_with_exception(self):
        """Should handle exceptions in context manager."""
        obs = ObservabilityCore()

        with pytest.raises(ValueError):
            with obs.trace("test.operation") as span:
                raise ValueError("Test error")

        assert span.status == SpanStatus.ERROR

    @pytest.mark.asyncio
    async def test_trace_async_context_manager(self):
        """Should manage span lifecycle via async context manager."""
        obs = ObservabilityCore()

        async with obs.trace_async("test.async_operation") as span:
            assert span.is_recording() is True
            await asyncio.sleep(0.01)

        assert span.is_recording() is False

    @pytest.mark.asyncio
    async def test_trace_async_with_exception(self):
        """Should handle exceptions in async context manager."""
        obs = ObservabilityCore()

        with pytest.raises(ValueError):
            async with obs.trace_async("test.async_operation") as span:
                raise ValueError("Test error")

        assert span.status == SpanStatus.ERROR


class TestTracePropagation:
    """Test trace context propagation."""

    def test_w3c_propagator_inject(self):
        """W3C propagator should inject context correctly."""
        propagator = W3CPropagator()
        context = TraceContext(
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
            trace_flags=1,
        )

        carrier: Dict[str, str] = {}
        propagator.inject(context, carrier)

        assert carrier["traceparent"] == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    def test_w3c_propagator_extract(self):
        """W3C propagator should extract context correctly."""
        propagator = W3CPropagator()
        carrier = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        }

        context = propagator.extract(carrier)

        assert context is not None
        assert context.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert context.span_id == "00f067aa0ba902b7"
        assert context.trace_flags == 1

    def test_w3c_propagator_roundtrip(self):
        """W3C propagator should support inject/extract roundtrip."""
        propagator = W3CPropagator()
        original = TraceContext(
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
            trace_flags=1,
            trace_state="vendor=value",
        )

        carrier: Dict[str, str] = {}
        propagator.inject(original, carrier)
        extracted = propagator.extract(carrier)

        assert extracted is not None
        assert extracted.trace_id == original.trace_id
        assert extracted.span_id == original.span_id
        assert extracted.trace_flags == original.trace_flags

    def test_b3_propagator_inject(self):
        """B3 propagator should inject context correctly."""
        propagator = B3Propagator()
        context = TraceContext(
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
            trace_flags=1,
        )

        carrier: Dict[str, str] = {}
        propagator.inject(context, carrier)

        assert carrier["X-B3-TraceId"] == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert carrier["X-B3-SpanId"] == "00f067aa0ba902b7"
        assert carrier["X-B3-Sampled"] == "1"

    def test_b3_propagator_extract(self):
        """B3 propagator should extract context correctly."""
        propagator = B3Propagator()
        carrier = {
            "X-B3-TraceId": "4bf92f3577b34da6a3ce929d0e0e4736",
            "X-B3-SpanId": "00f067aa0ba902b7",
            "X-B3-Sampled": "1",
        }

        context = propagator.extract(carrier)

        assert context is not None
        assert context.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert context.span_id == "00f067aa0ba902b7"
        assert context.is_sampled is True

    def test_obs_inject_context(self):
        """ObservabilityCore should inject context into carrier."""
        obs = ObservabilityCore()

        with obs.trace("test.operation") as span:
            carrier: Dict[str, str] = {}
            obs.inject_context(carrier)

            assert "traceparent" in carrier

    def test_obs_extract_context(self):
        """ObservabilityCore should extract context from carrier."""
        obs = ObservabilityCore()
        carrier = {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        }

        context = obs.extract_context(carrier)

        assert context is not None
        assert context.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_trace_context_propagation_across_spans(self):
        """Trace ID should be consistent across child spans."""
        obs = ObservabilityCore()

        with obs.trace("parent.operation") as parent:
            with obs.trace("child.operation") as child:
                assert parent.context.trace_id == child.context.trace_id
                assert child.context.parent_span_id == parent.context.span_id


class TestAsyncContextPropagation:
    """Test async context propagation."""

    @pytest.mark.asyncio
    async def test_async_context_propagation(self):
        """Trace context should propagate across async boundaries."""
        obs = ObservabilityCore()

        async def nested_operation():
            current = obs.get_current_trace()
            assert current is not None
            return current.trace_id

        async with obs.trace_async("parent") as parent_span:
            child_trace_id = await nested_operation()
            assert child_trace_id == parent_span.context.trace_id

    @pytest.mark.asyncio
    async def test_async_context_in_tasks(self):
        """Trace context should propagate in async tasks."""
        obs = ObservabilityCore()
        trace_ids = []

        async def worker(worker_id: int):
            current = obs.get_current_trace()
            if current:
                trace_ids.append((worker_id, current.trace_id))

        async with obs.trace_async("parent"):
            parent_context = obs.get_current_trace()
            tasks = [asyncio.create_task(worker(i)) for i in range(5)]
            await asyncio.gather(*tasks)

        # All tasks should see the same trace ID
        assert len(trace_ids) == 5
        trace_id_set = set(tid for _, tid in trace_ids)
        assert len(trace_id_set) == 1

    @pytest.mark.asyncio
    async def test_concurrent_span_creation(self):
        """Should handle 100+ concurrent span creations safely."""
        obs = ObservabilityCore()
        errors = []
        span_ids = []
        lock = asyncio.Lock()

        async def worker(worker_id: int):
            try:
                async with obs.trace_async(f"worker.{worker_id}") as span:
                    async with lock:
                        span_ids.append(span.context.span_id)
            except Exception as e:
                errors.append((worker_id, str(e)))

        tasks = [asyncio.create_task(worker(i)) for i in range(100)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(span_ids) == 100
        assert len(set(span_ids)) == 100  # All unique


class TestThreadSafety:
    """Test thread safety."""

    def test_context_across_threads(self):
        """Trace context should propagate across threads."""
        obs = ObservabilityCore()
        captured_trace_ids = []
        lock = threading.Lock()

        def worker():
            with obs.trace("worker.operation") as span:
                with lock:
                    captured_trace_ids.append(span.context.trace_id)

        with obs.trace("parent"):
            parent_context = obs.get_current_trace()

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(worker) for _ in range(4)]
                for f in futures:
                    f.result()

        # Each thread should have its own context
        assert len(captured_trace_ids) == 4

    def test_metrics_thread_safety(self):
        """Metrics should be thread-safe across threads."""
        obs = ObservabilityCore()
        counter = obs.metrics.counter("thread_requests")

        def increment(n: int):
            for _ in range(n):
                counter.inc()

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(increment, 10) for _ in range(10)]
            for f in futures:
                f.result()

        # 10 workers * 10 increments = 100
        assert counter.get() == 100


class TestLogging:
    """Test structured logging."""

    def test_log_info(self):
        """Should log info messages."""
        obs = ObservabilityCore()

        # Should not raise
        obs.log_info("Test message", key="value")

    def test_log_error(self):
        """Should log error messages."""
        obs = ObservabilityCore()

        # Should not raise
        obs.log_error("Test error", error="test")

    def test_json_formatter(self):
        """JSON formatter should produce valid JSON."""
        import json
        import logging

        formatter = JSONFormatter(service_name="test")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        # Manually set extra fields (they don't get merged automatically)
        record.custom_field = "value"

        output = formatter.format(record)
        data = json.loads(output)

        assert data["service"] == "test"
        assert data["level"] == "INFO"
        assert data["message"] == "Test message"
        assert data["extra"]["custom_field"] == "value"

    def test_context_filter(self):
        """Context filter should add trace context to logs."""
        filter = ContextFilter()
        context = TraceContext(
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
        )
        set_current_trace_context(context)

        import logging
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test",
            args=(),
            exc_info=None,
        )

        result = filter.filter(record)

        assert result is True
        assert record.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"


class TestTracedDecorator:
    """Test @traced decorator."""

    def test_traced_sync_function(self):
        """Should trace synchronous functions."""
        @traced(name="test.function", kind=SpanKind.INTERNAL)
        def my_function(x: int) -> int:
            return x * 2

        result = my_function(5)
        assert result == 10

    def test_traced_async_function(self):
        """Should trace asynchronous functions."""
        @traced(name="test.async_function")
        async def my_async_function(x: int) -> int:
            await asyncio.sleep(0.01)
            return x * 3

        result = asyncio.run(my_async_function(5))
        assert result == 15

    def test_traced_function_with_exception(self):
        """Should record exceptions in traced functions."""
        @traced(name="test.error_function", record_exceptions=True)
        def error_function():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            error_function()

    def test_traced_function_attributes(self):
        """Should add static attributes to span."""
        @traced(name="test.attr_function", attributes={"static": "value"})
        def attr_function():
            pass

        attr_function()


class TestShutdown:
    """Test shutdown behavior."""

    @pytest.mark.asyncio
    async def test_shutdown(self):
        """Should shutdown gracefully."""
        obs = ObservabilityCore()

        with obs.trace("test.operation"):
            pass

        await obs.shutdown()

        # Active spans should be cleared
        assert len(obs._active_spans) == 0

    @pytest.mark.asyncio
    async def test_shutdown_ends_active_spans(self):
        """Should end active spans on shutdown."""
        obs = ObservabilityCore()

        span = obs.start_span("test.operation")
        assert span.is_recording() is True

        await obs.shutdown()

        assert span.is_recording() is False


class TestGetCurrentTrace:
    """Test get_current_trace and set_current_trace."""

    def test_get_current_trace_none_initially(self):
        """Should return None when no trace active."""
        # Clear any existing context
        set_current_trace_context(None)

        obs = ObservabilityCore()
        context = obs.get_current_trace()

        assert context is None

    def test_get_current_trace_during_trace(self):
        """Should return context during trace."""
        obs = ObservabilityCore()

        with obs.trace("test.operation") as span:
            context = obs.get_current_trace()
            assert context is not None
            assert context.span_id == span.context.span_id

    def test_set_current_trace(self):
        """Should set current trace context."""
        obs = ObservabilityCore()
        context = TraceContext.generate()

        obs.set_current_trace(context)

        retrieved = obs.get_current_trace()
        assert retrieved is not None
        assert retrieved.trace_id == context.trace_id
