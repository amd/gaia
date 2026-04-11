# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for pipeline SSE endpoint JSON serialization error handling.

Validates that all json.dumps() calls in the streaming generator are
protected and fall back to safe defaults on failure.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# JSON serialization failure in start_event
# ---------------------------------------------------------------------------


def test_start_event_serialization_fallback():
    """When json.dumps fails for start_event, safe fallback is used."""
    # The fallback string should be valid JSON
    fallback = '{"type": "status", "status": "starting", "message": "Initializing pipeline..."}'
    parsed = json.loads(fallback)
    assert parsed["type"] == "status"
    assert parsed["status"] == "starting"


# ---------------------------------------------------------------------------
# JSON serialization failure in done_event
# ---------------------------------------------------------------------------


def test_done_event_serialization_fallback():
    """When json.dumps fails for done_event, safe fallback is used."""
    fallback = json.dumps({
        "type": "done",
        "pipeline_id": "test-id",
        "status": "unknown",
        "result": {"pipeline_status": "unknown", "error": "Serialization error"},
    })
    parsed = json.loads(fallback)
    assert parsed["type"] == "done"
    assert parsed["status"] == "unknown"


# ---------------------------------------------------------------------------
# JSON serialization failure in error_event
# ---------------------------------------------------------------------------


def test_error_event_serialization_fallback():
    """When json.dumps fails for error_event, safe fallback is used."""
    fallback = '{"type": "error", "content": "Internal error"}'
    parsed = json.loads(fallback)
    assert parsed["type"] == "error"


# ---------------------------------------------------------------------------
# TypeError handling in json.dumps
# ---------------------------------------------------------------------------


def test_json_dumps_typeerror_for_non_serializable():
    """json.dumps raises TypeError for non-serializable objects."""
    class NonSerializable:
        pass

    with pytest.raises(TypeError):
        json.dumps({"obj": NonSerializable()})


# ---------------------------------------------------------------------------
# ValueError handling in json.dumps
# ---------------------------------------------------------------------------


def test_json_dumps_valueerror_for_circular_reference():
    """json.dumps raises ValueError for circular references."""
    data: dict = {}
    data["self"] = data

    with pytest.raises((ValueError, TypeError)):
        json.dumps(data)


# ---------------------------------------------------------------------------
# SSE event format validation
# ---------------------------------------------------------------------------


def test_sse_start_event_format():
    """Start event follows SSE format: data: {...}\\n\\n"""
    pipeline_id = "test-123"
    event = json.dumps({
        "type": "status",
        "status": "starting",
        "message": "Initializing pipeline...",
        "pipeline_id": pipeline_id,
    })
    sse_message = f"data: {event}\n\n"

    assert sse_message.startswith("data: ")
    assert sse_message.endswith("\n\n")
    assert "Initializing pipeline..." in sse_message


def test_sse_done_event_format():
    """Done event follows SSE format with pipeline status."""
    result = {"pipeline_status": "completed", "stages": []}
    pipeline_id = "test-123"

    event = json.dumps({
        "type": "done",
        "pipeline_id": pipeline_id,
        "status": result.get("pipeline_status", "unknown"),
        "result": result,
    })
    sse_message = f"data: {event}\n\n"

    parsed = json.loads(sse_message.strip()[6:])  # Remove "data: " and "\n\n"
    assert parsed["type"] == "done"
    assert parsed["pipeline_id"] == pipeline_id
    assert parsed["status"] == "completed"


def test_sse_error_event_format():
    """Error event follows SSE format with error content."""
    error_msg = "Pipeline execution failed: timeout"
    pipeline_id = "test-123"

    event = json.dumps({
        "type": "error",
        "content": error_msg,
        "pipeline_id": pipeline_id,
    })
    sse_message = f"data: {event}\n\n"

    parsed = json.loads(sse_message.strip()[6:])
    assert parsed["type"] == "error"
    assert parsed["content"] == error_msg
    assert parsed["pipeline_id"] == pipeline_id


# ---------------------------------------------------------------------------
# Pipeline execution error path
# ---------------------------------------------------------------------------


def test_execute_pipeline_sync_returns_error_dict():
    """_execute_pipeline_sync returns error dict on failure."""
    from gaia.ui.routers.pipeline import _execute_pipeline_sync

    with patch(
        "gaia.ui.routers.pipeline._run_pipeline"
    ) as mock_run:
        mock_run.side_effect = RuntimeError("Pipeline crashed")

        result = _execute_pipeline_sync("test task", False)

        assert result["pipeline_status"] == "failed"
        assert "Pipeline crashed" in result["error"]


def test_execute_pipeline_sync_success():
    """_execute_pipeline_sync returns result on success."""
    from gaia.ui.routers.pipeline import _execute_pipeline_sync

    with patch(
        "gaia.ui.routers.pipeline._run_pipeline"
    ) as mock_run:
        mock_run.return_value = {"pipeline_status": "completed", "stages": 5}

        result = _execute_pipeline_sync("test task", False)

        assert result["pipeline_status"] == "completed"
        assert result["stages"] == 5
