# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the orchestrator REST/SSE API router.

Tests cover:
- Phase 1: REST endpoints (state, health, objectives, history)
- Phase 2: SSE streaming endpoint connectivity
- Phase 3: Control endpoints (run, pause, resume)
- Error handling (503, 404, 409)
- Pagination and filtering
- Idempotent pause/resume
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gaia.orchestration.engine import OrchestratorConfig, OrchestratorState
from gaia.orchestration.models import (
    Objective,
    ObjectiveStatus,
    ProjectObjectives,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_objectives():
    """Create a set of mock objectives for testing."""
    objectives = []
    for i, (phase, status) in enumerate([
        ("phase-1", ObjectiveStatus.COMPLETED),
        ("phase-1", ObjectiveStatus.COMPLETED),
        ("phase-2", ObjectiveStatus.IN_PROGRESS),
        ("phase-2", ObjectiveStatus.QUEUED),
        ("phase-3", ObjectiveStatus.BLOCKED),
    ], start=1):
        obj = Objective(
            objective_id=f"obj-{i}",
            title=f"Objective {i}",
            description=f"Description for objective {i}",
            phase=phase,
            status=status,
            priority=i,
        )
        objectives.append(obj)
    return objectives


@pytest.fixture
def mock_project(mock_objectives):
    """Create a mocked ProjectObjectives."""
    project = MagicMock(spec=ProjectObjectives)
    project.project_id = "test-project"
    project.objectives = mock_objectives

    def get_objective(oid):
        for o in mock_objectives:
            if o.objective_id == oid:
                return o
        return None

    project.get_objective = get_objective
    return project


@pytest.fixture
def mock_orchestrator(mock_project):
    """Create a mocked ProjectOrchestrator with state and project."""
    orchestrator = MagicMock()
    orchestrator.state = OrchestratorState(
        paused=False,
        cycle_count=3,
        objectives_processed=2,
        objectives_failed=1,
        execution_history=[
            {"cycle": 1, "objective_id": "obj-1", "success": True},
            {"cycle": 2, "objective_id": "obj-2", "success": True},
            {"cycle": 3, "objective_id": "obj-3", "success": False},
        ],
        objective_branches={
            "obj-1": "obj/1-objective-1",
            "obj-2": "obj/2-objective-2",
        },
    )
    orchestrator.project = mock_project
    orchestrator.supervisor = None
    orchestrator.hook_registry = MagicMock()
    orchestrator.hook_registry.register = MagicMock()
    orchestrator.pause = MagicMock()
    orchestrator.resume = MagicMock()
    orchestrator.run = AsyncMock()
    orchestrator.config = OrchestratorConfig()
    return orchestrator


@pytest.fixture
def app_state(mock_orchestrator):
    """Create a mock app.state with orchestrator."""
    state = MagicMock()
    state.orchestrator = mock_orchestrator
    state._orchestrator_running = False
    return state


@pytest.fixture
def mock_request(app_state):
    """Create a mock FastAPI Request with app.state."""
    request = MagicMock()
    request.app.state = app_state
    return request


@pytest.fixture
def mock_request_no_orchestrator():
    """Create a mock request without orchestrator."""
    state = MagicMock()
    state.orchestrator = None
    request = MagicMock()
    request.app.state = request.app = MagicMock()
    request.app.state = state
    return request


# =============================================================================
# Phase 1: REST Endpoints
# =============================================================================


class TestGetOrchestratorState:
    """Tests for GET /api/v1/orchestrator/state."""

    @pytest.mark.asyncio
    async def test_returns_state_with_project_summary(self, mock_request, mock_orchestrator):
        """State endpoint returns orchestrator state and project summary."""
        from gaia.ui.routers.orchestrator import get_orchestrator_state

        response = await get_orchestrator_state(mock_request)

        assert "orchestrator_state" in response
        assert "project_summary" in response

        state = response["orchestrator_state"]
        assert state["paused"] is False
        assert state["cycle_count"] == 3
        assert state["objectives_processed"] == 2
        assert state["objectives_failed"] == 1

        summary = response["project_summary"]
        assert summary["project_id"] == "test-project"
        assert summary["total_objectives"] == 5
        assert summary["completed"] == 2

    @pytest.mark.asyncio
    async def test_returns_503_without_orchestrator(self, mock_request_no_orchestrator):
        """Returns 503 when orchestrator is not initialized."""
        from gaia.ui.routers.orchestrator import get_orchestrator_state
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_orchestrator_state(mock_request_no_orchestrator)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_includes_supervisor_state_when_enabled(self, mock_request):
        """Includes supervisor state when supervisor is enabled."""
        from gaia.ui.routers.orchestrator import get_orchestrator_state

        mock_request.app.state.orchestrator.supervisor = MagicMock()
        mock_request.app.state.orchestrator.supervisor.state = MagicMock()
        mock_request.app.state.orchestrator.supervisor.state.aborted_reason = None
        mock_request.app.state.orchestrator.supervisor.state.paused_reason = None
        mock_request.app.state.orchestrator.supervisor.state.quality_trend = "stable"

        response = await get_orchestrator_state(mock_request)

        assert "supervisor_state" in response
        assert response["supervisor_state"]["quality_trend"] == "stable"


class TestGetOrchestratorHealth:
    """Tests for GET /api/v1/orchestrator/health."""

    @pytest.mark.asyncio
    async def test_returns_health_composite(self, mock_request):
        """Health endpoint returns composite score and status."""
        from gaia.ui.routers.orchestrator import get_orchestrator_health

        response = await get_orchestrator_health(mock_request)

        assert "status" in response
        assert "health_score" in response
        assert "components" in response
        assert response["components"]["orchestrator_alive"] is True
        assert response["components"]["paused"] is False

    @pytest.mark.asyncio
    async def test_health_reflects_error_rate(self, mock_request):
        """Health score reflects objective failure ratio."""
        from gaia.ui.routers.orchestrator import get_orchestrator_health

        mock_request.app.state.orchestrator.state.objectives_processed = 1
        mock_request.app.state.orchestrator.state.objectives_failed = 1

        response = await get_orchestrator_health(mock_request)

        assert response["components"]["error_rate"] == 0.5


class TestListObjectives:
    """Tests for GET /api/v1/orchestrator/objectives."""

    @pytest.mark.asyncio
    async def test_returns_all_objectives(self, mock_request):
        """Returns all objectives without filters."""
        from gaia.ui.routers.orchestrator import list_objectives

        response = await list_objectives(
            mock_request, phase=None, status=None, limit=50, offset=0
        )

        assert response["total"] == 5
        assert len(response["objectives"]) == 5

    @pytest.mark.asyncio
    async def test_filters_by_phase(self, mock_request):
        """Filters objectives by phase name."""
        from gaia.ui.routers.orchestrator import list_objectives

        response = await list_objectives(
            mock_request, phase="phase-1", status=None, limit=50, offset=0
        )

        assert response["total"] == 2
        assert all(o["phase"] == "phase-1" for o in response["objectives"])

    @pytest.mark.asyncio
    async def test_filters_by_status(self, mock_request):
        """Filters objectives by status."""
        from gaia.ui.routers.orchestrator import list_objectives

        response = await list_objectives(
            mock_request, phase=None, status="queued", limit=50, offset=0
        )

        assert response["total"] == 1
        assert response["objectives"][0]["objective_id"] == "obj-4"

    @pytest.mark.asyncio
    async def test_returns_400_for_invalid_status(self, mock_request):
        """Returns 400 for invalid status filter value."""
        from gaia.ui.routers.orchestrator import list_objectives
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await list_objectives(
                mock_request, phase=None, status="invalid_status_xyz", limit=50, offset=0
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_pagination_limit(self, mock_request):
        """Respects limit parameter."""
        from gaia.ui.routers.orchestrator import list_objectives

        response = await list_objectives(
            mock_request, phase=None, status=None, limit=2, offset=0
        )

        assert len(response["objectives"]) == 2
        assert response["limit"] == 2

    @pytest.mark.asyncio
    async def test_pagination_offset(self, mock_request):
        """Respects offset parameter."""
        from gaia.ui.routers.orchestrator import list_objectives

        response = await list_objectives(
            mock_request, phase=None, status=None, limit=10, offset=3
        )

        assert len(response["objectives"]) == 2
        assert response["offset"] == 3


class TestGetObjective:
    """Tests for GET /api/v1/orchestrator/objectives/{objective_id}."""

    @pytest.mark.asyncio
    async def test_returns_objective_detail(self, mock_request):
        """Returns detailed info for a single objective."""
        from gaia.ui.routers.orchestrator import get_objective

        response = await get_objective(mock_request, "obj-1")

        assert response["objective_id"] == "obj-1"
        assert response["title"] == "Objective 1"
        assert response["phase"] == "phase-1"
        assert response["status"] == "completed"

    @pytest.mark.asyncio
    async def test_returns_branch_mapping(self, mock_request):
        """Includes worktree branch when mapped."""
        from gaia.ui.routers.orchestrator import get_objective

        response = await get_objective(mock_request, "obj-1")

        assert response["branch"] == "obj/1-objective-1"

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_objective(self, mock_request):
        """Returns 404 when objective does not exist."""
        from gaia.ui.routers.orchestrator import get_objective
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_objective(mock_request, "obj-999")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_404_when_no_project(self, mock_request):
        """Returns 404 when no project is loaded."""
        from gaia.ui.routers.orchestrator import get_objective
        from fastapi import HTTPException

        mock_request.app.state.orchestrator.project = None

        with pytest.raises(HTTPException) as exc_info:
            await get_objective(mock_request, "obj-1")
        assert exc_info.value.status_code == 404


class TestGetExecutionHistory:
    """Tests for GET /api/v1/orchestrator/history."""

    @pytest.mark.asyncio
    async def test_returns_history_reversed(self, mock_request):
        """Returns execution history in reverse chronological order."""
        from gaia.ui.routers.orchestrator import get_execution_history

        response = await get_execution_history(mock_request, limit=20, offset=0)

        assert response["total"] == 3
        # Newest first: cycle 3 should be first
        assert response["history"][0]["cycle"] == 3

    @pytest.mark.asyncio
    async def test_pagination(self, mock_request):
        """Respects limit and offset for history."""
        from gaia.ui.routers.orchestrator import get_execution_history

        response = await get_execution_history(mock_request, limit=1, offset=1)

        assert len(response["history"]) == 1
        assert response["limit"] == 1
        assert response["offset"] == 1


# =============================================================================
# Phase 2: SSE Streaming
# =============================================================================


class TestSSEStream:
    """Tests for GET /api/v1/orchestrator/stream."""

    @pytest.mark.asyncio
    async def test_sse_endpoint_connects(self):
        """SSE stream endpoint is callable and returns StreamingResponse."""
        from gaia.ui.routers.orchestrator import stream_events

        request = MagicMock()
        response = await stream_events(request)

        # Should return a StreamingResponse with text/event-stream
        assert response.media_type == "text/event-stream"
        assert "Cache-Control" in response.headers
        assert response.headers["Cache-Control"] == "no-cache"

    @pytest.mark.asyncio
    async def test_sse_bridge_broadcast(self):
        """SSE bridge broadcasts to subscribers."""
        from gaia.ui.routers.orchestrator import _sse_bridge

        q = await _sse_bridge.subscribe()
        try:
            await _sse_bridge.broadcast({"type": "test_event", "data": "hello"})
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            assert event["type"] == "test_event"
        finally:
            await _sse_bridge.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_sse_bridge_unsubscribe(self):
        """SSE bridge removes subscriber on unsubscribe."""
        from gaia.ui.routers.orchestrator import _sse_bridge

        q = await _sse_bridge.subscribe()
        assert _sse_bridge.client_count >= 1
        await _sse_bridge.unsubscribe(q)
        # Client count should decrease

    @pytest.mark.asyncio
    async def test_sse_generator_handles_cancelled_error(self):
        """SSE generator properly propagates CancelledError on disconnect."""
        from gaia.ui.routers.orchestrator import _stream_orchestrator_events, _sse_bridge

        q = await _sse_bridge.subscribe()
        try:
            gen = _stream_orchestrator_events()
            # Get the first yield (subscribe call)
            await gen.__anext__()
            # Now simulate cancellation by cancelling the task
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await _sse_bridge.unsubscribe(q)


# =============================================================================
# Phase 3: Control Endpoints
# =============================================================================


class TestRunOrchestrator:
    """Tests for POST /api/v1/orchestrator/run."""

    @pytest.mark.asyncio
    async def test_starts_orchestrator(self, mock_request):
        """Run endpoint starts orchestrator in background and returns 202."""
        from gaia.ui.routers.orchestrator import run_orchestrator
        from gaia.ui.routers.orchestrator import RunRequest

        response = await run_orchestrator(mock_request, RunRequest())

        # The endpoint returns 202 immediately; the background task runs
        # asynchronously. Verify the response payload is correct.
        assert response["status"] == "accepted"
        assert response["message"] == "Orchestrator started in background"

    @pytest.mark.asyncio
    async def test_returns_409_when_already_running(self, mock_request):
        """Returns 409 Conflict if orchestrator is already running."""
        from gaia.ui.routers.orchestrator import run_orchestrator
        from gaia.ui.routers.orchestrator import RunRequest
        from fastapi import HTTPException

        mock_request.app.state._orchestrator_running = True

        with pytest.raises(HTTPException) as exc_info:
            await run_orchestrator(mock_request, RunRequest())
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_returns_503_without_orchestrator(self, mock_request_no_orchestrator):
        """Returns 503 when orchestrator is not initialized."""
        from gaia.ui.routers.orchestrator import run_orchestrator
        from gaia.ui.routers.orchestrator import RunRequest
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await run_orchestrator(mock_request_no_orchestrator, RunRequest())
        assert exc_info.value.status_code == 503


class TestPauseOrchestrator:
    """Tests for POST /api/v1/orchestrator/pause."""

    @pytest.mark.asyncio
    async def test_pauses_orchestrator(self, mock_request):
        """Pause endpoint calls orchestrator.pause with reason."""
        from gaia.ui.routers.orchestrator import pause_orchestrator
        from gaia.ui.routers.orchestrator import PauseRequest

        await pause_orchestrator(mock_request, PauseRequest(reason="maintenance"))

        mock_request.app.state.orchestrator.pause.assert_called_once_with(
            reason="maintenance"
        )

    @pytest.mark.asyncio
    async def test_idempotent_pause(self, mock_request):
        """Pause is idempotent — returns success even if already paused."""
        from gaia.ui.routers.orchestrator import pause_orchestrator
        from gaia.ui.routers.orchestrator import PauseRequest

        mock_request.app.state.orchestrator.state.paused = True

        response = await pause_orchestrator(
            mock_request, PauseRequest(reason="already paused")
        )

        assert response["status"] == "paused"
        assert response["was_already_paused"] is True

    @pytest.mark.asyncio
    async def test_default_reason(self, mock_request):
        """Uses default reason when none provided."""
        from gaia.ui.routers.orchestrator import pause_orchestrator
        from gaia.ui.routers.orchestrator import PauseRequest

        await pause_orchestrator(mock_request, PauseRequest())

        mock_request.app.state.orchestrator.pause.assert_called_once()
        call_kwargs = mock_request.app.state.orchestrator.pause.call_args
        assert call_kwargs[1]["reason"] == "User requested pause"


class TestResumeOrchestrator:
    """Tests for POST /api/v1/orchestrator/resume."""

    @pytest.mark.asyncio
    async def test_resumes_orchestrator(self, mock_request):
        """Resume endpoint calls orchestrator.resume."""
        from gaia.ui.routers.orchestrator import resume_orchestrator

        mock_request.app.state.orchestrator.state.paused = True

        response = await resume_orchestrator(mock_request)

        mock_request.app.state.orchestrator.resume.assert_called_once()
        assert response["status"] == "resumed"

    @pytest.mark.asyncio
    async def test_idempotent_resume(self, mock_request):
        """Resume is idempotent — returns success even if already running."""
        from gaia.ui.routers.orchestrator import resume_orchestrator

        mock_request.app.state.orchestrator.state.paused = False

        response = await resume_orchestrator(mock_request)

        assert response["was_already_running"] is True


# =============================================================================
# OrchestratorState.to_dict() tests
# =============================================================================


class TestOrchestratorStateToDict:
    """Tests for OrchestratorState.to_dict() method."""

    def test_to_dict_returns_all_fields(self):
        """to_dict returns all state fields as a serializable dict."""
        state = OrchestratorState(
            paused=True,
            cycle_count=5,
            objectives_processed=3,
            objectives_failed=2,
            execution_history=[{"cycle": 1, "objective_id": "obj-1", "success": True}],
            objective_branches={"obj-1": "obj/1-test"},
        )

        result = state.to_dict()

        assert result["paused"] is True
        assert result["cycle_count"] == 5
        assert result["objectives_processed"] == 3
        assert result["objectives_failed"] == 2
        assert len(result["execution_history"]) == 1
        assert result["objective_branches"] == {"obj-1": "obj/1-test"}

    def test_to_dict_is_json_serializable(self):
        """to_dict output can be passed to json.dumps without errors."""
        state = OrchestratorState(
            paused=False,
            cycle_count=0,
            objectives_processed=0,
            objectives_failed=0,
        )

        result = state.to_dict()
        # Should not raise TypeError
        serialized = json.dumps(result)
        assert isinstance(serialized, str)

    def test_to_dict_returns_copies(self):
        """to_dict returns copies, not references to internal state."""
        state = OrchestratorState()
        result = state.to_dict()

        # Mutating result should not affect state
        result["execution_history"].append({"cycle": 99})
        result["objective_branches"]["new"] = "branch"

        assert len(state.execution_history) == 0
        assert len(state.objective_branches) == 0
