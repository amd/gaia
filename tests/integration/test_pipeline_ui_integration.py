# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Pipeline UI Integration Tests

Tests the end-to-end flow from recursive PipelineEngine through SSE streaming
to the frontend event types: loop_back, quality_score, phase_jump,
iteration_start, iteration_end, defect_found.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRecursiveSSEEventEmission:
    """Verify recursive pipeline events are emitted through SSE handler."""

    def test_loop_back_event_emission(self):
        """LOOP_BACK decision emits loop_back SSE event."""
        from gaia.pipeline.orchestrator import _emit_sse

        handler = MagicMock()
        handler.event_queue = MagicMock()

        _emit_sse(handler, "loop_back", {
            "message": "Looping back to PLANNING phase",
            "target_phase": "PLANNING",
            "iteration": 2,
        })

        handler.event_queue.put.assert_called_once()
        event = handler.event_queue.put.call_args[0][0]
        assert event["type"] == "loop_back"
        assert event["target_phase"] == "PLANNING"
        assert event["iteration"] == 2

    def test_quality_score_event_emission(self):
        """QUALITY phase emits quality_score event."""
        from gaia.pipeline.orchestrator import _emit_sse

        handler = MagicMock()
        handler.event_queue = MagicMock()

        _emit_sse(handler, "quality_score", {
            "message": "Quality evaluation complete",
            "quality_score": 0.85,
            "iteration": 1,
        })

        event = handler.event_queue.put.call_args[0][0]
        assert event["type"] == "quality_score"
        assert event["quality_score"] == 0.85

    def test_phase_jump_event_emission(self):
        """Phase transition emits phase_jump event."""
        from gaia.pipeline.orchestrator import _emit_sse

        handler = MagicMock()
        handler.event_queue = MagicMock()

        _emit_sse(handler, "phase_jump", {
            "message": "Jumping to DEVELOPMENT phase",
            "target_phase": "DEVELOPMENT",
        })

        event = handler.event_queue.put.call_args[0][0]
        assert event["type"] == "phase_jump"
        assert event["target_phase"] == "DEVELOPMENT"

    def test_iteration_boundary_events(self):
        """Iteration start/end events are emitted."""
        from gaia.pipeline.orchestrator import _emit_sse

        handler = MagicMock()
        handler.event_queue = MagicMock()

        _emit_sse(handler, "iteration_start", {
            "message": "Pipeline starting (max 10 iterations)",
            "iteration": 1,
        })
        _emit_sse(handler, "iteration_end", {
            "message": "Iteration 1 complete",
            "iteration": 1,
        })

        assert handler.event_queue.put.call_count == 2
        events = [c[0][0] for c in handler.event_queue.put.call_args_list]
        assert events[0]["type"] == "iteration_start"
        assert events[1]["type"] == "iteration_end"
        assert events[0]["iteration"] == 1
        assert events[1]["iteration"] == 1

    def test_defect_found_event_emission(self):
        """Defect detection emits defect_found event."""
        from gaia.pipeline.orchestrator import _emit_sse

        handler = MagicMock()
        handler.event_queue = MagicMock()

        _emit_sse(handler, "defect_found", {
            "message": "Critical defect found in PLANNING",
            "defects": [
                {"type": "missing_requirements", "severity": "critical", "description": "No user story"}
            ],
        })

        event = handler.event_queue.put.call_args[0][0]
        assert event["type"] == "defect_found"
        assert len(event["defects"]) == 1
        assert event["defects"][0]["severity"] == "critical"


class TestRecursivePipelineExecution:
    """Test the recursive pipeline execution path through the router."""

    def test_execute_recursive_pipeline_returns_result(self):
        """_execute_recursive_pipeline returns structured result with metadata."""
        from gaia.pipeline.orchestrator import _execute_recursive_pipeline

        # Mock PipelineEngine to avoid real LLM calls
        mock_engine = AsyncMock()
        mock_result = MagicMock()
        mock_result.state = "COMPLETED"
        mock_engine.start = AsyncMock(return_value=mock_result)
        mock_engine._state_machine = MagicMock()
        mock_engine._state_machine.artifacts = {"quality_score": 0.92}
        mock_engine._state_machine.decisions = []
        mock_engine._state_machine.state = MagicMock()
        mock_engine._state_machine.state.iteration = 1

        with patch("gaia.pipeline.orchestrator.uuid4") as mock_uuid:
            mock_uuid.return_value = MagicMock(hex="abc123")
            with patch("gaia.pipeline.engine.PipelineEngine", return_value=mock_engine) as MockEngine:
                # Mock initialize and start
                MockEngine.return_value.initialize = AsyncMock()
                MockEngine.return_value.start = AsyncMock(return_value=mock_result)
                MockEngine.return_value._state_machine = mock_engine._state_machine

                result = _execute_recursive_pipeline(
                    task_description="Test task",
                    template_name="generic",
                    max_iterations=3,
                )

                assert result["pipeline_status"] == "success"
                assert result["pipeline_id"].startswith("recursive-")

    def test_execute_recursive_pipeline_error_handling(self):
        """_execute_recursive_pipeline handles errors gracefully."""
        from gaia.pipeline.orchestrator import _execute_recursive_pipeline

        with patch("gaia.pipeline.engine.PipelineEngine") as MockEngine:
            MockEngine.side_effect = RuntimeError("Engine unavailable")

            result = _execute_recursive_pipeline(
                task_description="Test task",
            )

            assert result["pipeline_status"] == "failed"
            assert "Engine unavailable" in result["error"]


class TestRouterIntegration:
    """Test the router's SSE event integration."""

    def test_pipeline_sse_handler_emit(self):
        """_PipelineSSEHandler.emit() queues events correctly."""
        from gaia.ui.routers.pipeline import _PipelineSSEHandler

        handler = _PipelineSSEHandler()
        handler.emit("loop_back", {
            "message": "Looping back",
            "target_phase": "PLANNING",
        })

        assert not handler.event_queue.empty()
        event = handler.event_queue.get_nowait()
        assert event["type"] == "loop_back"
        assert event["target_phase"] == "PLANNING"

    def test_pipeline_sse_handler_drain(self):
        """_PipelineSSEHandler.drain() yields all queued events."""
        from gaia.ui.routers.pipeline import _PipelineSSEHandler

        handler = _PipelineSSEHandler()
        handler.emit("iteration_start", {"iteration": 1})
        handler.emit("quality_score", {"quality_score": 0.9})
        handler.emit("iteration_end", {"iteration": 1})

        drained = list(handler.drain(handler.event_queue))
        assert len(drained) == 3
        assert all(d.startswith("data: ") for d in drained)
        assert '"iteration_start"' in drained[0]
        assert '"quality_score"' in drained[1]
        assert '"iteration_end"' in drained[2]

    def test_execute_pipeline_sync_recursive_mode(self):
        """_execute_pipeline_sync with recursive=True delegates to PipelineEngine."""
        from gaia.ui.routers.pipeline import _execute_pipeline_sync

        mock_handler = MagicMock()

        with patch(
            "gaia.pipeline.orchestrator._execute_recursive_pipeline"
        ) as mock_recursive:
            mock_recursive.return_value = {
                "pipeline_status": "success",
                "loop_count": 2,
                "quality_scores": [0.85, 0.92],
            }

            result = _execute_pipeline_sync(
                task_description="Test",
                auto_spawn=True,
                template_name="generic",
                recursive=True,
                sse_handler=mock_handler,
            )

            mock_recursive.assert_called_once()
            assert result["pipeline_status"] == "success"
            assert result["loop_count"] == 2
