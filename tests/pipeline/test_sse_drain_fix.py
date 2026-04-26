# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for the drain() bug fix in _PipelineSSEHandler.

The bug: drain() returns a generator, so calling it without iterating
is a no-op. The fix wraps it in a for-loop to actually consume events.

Tests verify:
- drain() yields events (not just creating a generator)
- Events are yielded in FIFO order
- Empty drain returns no events
- SSE format (starts with "data: ", ends with "\n\n")
- The async generator properly iterates drain()
"""

import json
import queue
import asyncio
import pytest

from gaia.ui.routers.pipeline import _PipelineSSEHandler


class TestDrainYieldsEvents:
    """Verify drain() actually emits events when consumed."""

    def test_drain_yields_events_not_generator_object(self):
        """drain() must yield strings, not generator objects."""
        handler = _PipelineSSEHandler()
        handler.emit("status", {"message": "hello"})
        handler.emit("done", {"status": "complete"})

        # drain() returns a generator; we must iterate it to get values
        gen = handler.drain(handler.event_queue)
        results = list(gen)

        assert len(results) == 2
        assert all(isinstance(r, str) for r in results)

    def test_drain_yields_multiple_events(self):
        """drain() should yield all events in the queue."""
        handler = _PipelineSSEHandler()
        for i in range(5):
            handler.emit("event", {"index": i})

        results = list(handler.drain(handler.event_queue))
        assert len(results) == 5


class TestDrainFifoOrder:
    """Events should be yielded in FIFO order."""

    def test_events_yielded_in_fifo_order(self):
        """Events should come out in the same order they were put in."""
        handler = _PipelineSSEHandler()
        handler.emit("first", {"seq": 1})
        handler.emit("second", {"seq": 2})
        handler.emit("third", {"seq": 3})

        results = list(handler.drain(handler.event_queue))
        payloads = [json.loads(r.replace("data: ", "").replace("\n\n", "")) for r in results]

        assert payloads[0]["type"] == "first"
        assert payloads[1]["type"] == "second"
        assert payloads[2]["type"] == "third"
        assert payloads[0]["seq"] == 1
        assert payloads[1]["seq"] == 2
        assert payloads[2]["seq"] == 3

    def test_large_fifo_order(self):
        """FIFO order should hold for many events."""
        handler = _PipelineSSEHandler()
        n = 100
        for i in range(n):
            handler.emit("seq", {"index": i})

        results = list(handler.drain(handler.event_queue))
        assert len(results) == n
        for i, r in enumerate(results):
            json_str = r[len("data: "):-2]
            payload = json.loads(json_str)
            assert payload["index"] == i


class TestDrainEmptyQueue:
    """Empty queue should return no events."""

    def test_empty_drain_returns_no_events(self):
        """drain() on empty queue should yield nothing."""
        handler = _PipelineSSEHandler()
        results = list(handler.drain(handler.event_queue))
        assert results == []

    def test_drain_after_all_events_consumed(self):
        """Second drain() call after first consumed everything should be empty."""
        handler = _PipelineSSEHandler()
        handler.emit("status", {"message": "test"})

        results1 = list(handler.drain(handler.event_queue))
        assert len(results1) == 1

        results2 = list(handler.drain(handler.event_queue))
        assert results2 == []


class TestSseFormat:
    """Verify SSE formatting is correct."""

    def test_sse_format_starts_with_data_prefix(self):
        """Each event string should start with 'data: '."""
        handler = _PipelineSSEHandler()
        handler.emit("status", {"message": "test"})

        results = list(handler.drain(handler.event_queue))
        assert len(results) == 1
        assert results[0].startswith("data: ")

    def test_sse_format_ends_with_double_newline(self):
        """Each event string should end with '\\n\\n'."""
        handler = _PipelineSSEHandler()
        handler.emit("status", {"message": "test"})

        results = list(handler.drain(handler.event_queue))
        assert results[0].endswith("\n\n")

    def test_sse_format_complete_structure(self):
        """Full SSE format: 'data: {json}\\n\\n'."""
        handler = _PipelineSSEHandler()
        handler.emit("test_event", {"key": "value"})

        results = list(handler.drain(handler.event_queue))
        raw = results[0]

        # Strip SSE framing
        assert raw.startswith("data: ")
        assert raw.endswith("\n\n")
        json_str = raw[len("data: "):-2]  # Remove "data: " prefix and "\n\n" suffix

        # Verify valid JSON
        payload = json.loads(json_str)
        assert payload["type"] == "test_event"
        assert payload["key"] == "value"

    def test_sse_format_with_complex_payload(self):
        """SSE format should handle nested JSON payloads."""
        handler = _PipelineSSEHandler()
        handler.emit("quality_score", {
            "quality_score": 0.95,
            "message": "Quality score: 0.95",
            "details": {"phase": "QUALITY", "scorer": "default"},
        })

        results = list(handler.drain(handler.event_queue))
        json_str = results[0][len("data: "):-2]
        payload = json.loads(json_str)

        assert payload["type"] == "quality_score"
        assert payload["quality_score"] == 0.95
        assert payload["details"]["phase"] == "QUALITY"


class TestAsyncGeneratorIteration:
    """Verify the async generator properly iterates drain()."""

    @pytest.mark.asyncio
    async def test_async_generator_iterates_drain(self):
        """The async generator used in SSE streaming should consume drain()."""
        handler = _PipelineSSEHandler()
        handler.emit("status", {"message": "test1"})
        handler.emit("status", {"message": "test2"})

        # Simulate what _stream_pipeline_events does
        collected = []
        for event_str in handler.drain(handler.event_queue):
            collected.append(event_str)

        assert len(collected) == 2
        assert collected[0].startswith("data: ")
        assert collected[1].startswith("data: ")

    @pytest.mark.asyncio
    async def test_async_generator_with_empty_queue(self):
        """Async iteration over empty drain should yield nothing."""
        handler = _PipelineSSEHandler()

        collected = []
        for event_str in handler.drain(handler.event_queue):
            collected.append(event_str)

        assert collected == []

    @pytest.mark.asyncio
    async def test_emit_then_drain_in_async_context(self):
        """Emit events, then drain in an async context simulates real usage."""
        handler = _PipelineSSEHandler()

        # Emit events (could happen from background threads via hooks)
        handler.emit("phase_enter", {"current_phase": "PLANNING"})
        handler.emit("quality_score", {"quality_score": 0.87})
        handler.emit("decision", {"decision_type": "PROCEED"})

        # Drain as the async generator would
        collected = []
        async def stream():
            for event_str in handler.drain(handler.event_queue):
                collected.append(event_str)
                yield event_str

        results = [r async for r in stream()]
        assert len(results) == 3

        # Verify parseable payloads
        for r in results:
            json_str = r[len("data: "):-2]
            payload = json.loads(json_str)
            assert "type" in payload


class TestEmitMethod:
    """Verify emit() puts events into the queue correctly."""

    def test_emit_adds_event_to_queue(self):
        """emit() should add an event dict to the queue."""
        handler = _PipelineSSEHandler()
        handler.emit("test", {"key": "val"})

        assert not handler.event_queue.empty()
        event = handler.event_queue.get_nowait()
        assert event["type"] == "test"
        assert event["key"] == "val"

    def test_emit_merges_type_into_data(self):
        """emit() should merge event_type as 'type' field in the event dict."""
        handler = _PipelineSSEHandler()
        handler.emit("status", {"running": True, "message": "ok"})

        event = handler.event_queue.get_nowait()
        assert event == {"type": "status", "running": True, "message": "ok"}

    def test_emit_does_not_crash_on_large_payload(self):
        """emit() should handle large payloads without errors."""
        handler = _PipelineSSEHandler()
        large_data = {"content": "x" * 10000}
        handler.emit("large", large_data)

        event = handler.event_queue.get_nowait()
        assert event["type"] == "large"
        assert len(event["content"]) == 10000
