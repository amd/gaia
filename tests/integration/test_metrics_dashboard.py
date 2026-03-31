# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for pipeline metrics API endpoints.

Tests cover:
- End-to-end metrics fetching
- Metrics history queries
- Aggregate metrics computation
- Error handling for missing pipelines
"""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app
from gaia.ui.services.metrics_service import MetricsService


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        yield Path(tmp.name)


@pytest.fixture
def mock_metrics_service():
    """Create a mock metrics service with test data."""
    service = MagicMock(spec=MetricsService)

    # Sample metrics summary
    service.get_metrics_summary.return_value = {
        "summary": {
            "pipeline_id": "test-pipeline-001",
            "total_duration_seconds": 120.5,
            "total_tokens": 5000,
            "avg_tps": 41.5,
            "avg_ttft": 0.35,
            "total_loops": 4,
            "total_iterations": 12,
            "total_defects": 5,
            "avg_quality_score": 0.88,
            "max_quality_score": 0.95,
            "min_quality_score": 0.72,
        },
        "phase_breakdown": {
            "PLANNING": {
                "phase_name": "PLANNING",
                "started_at": datetime.now(timezone.utc) - timedelta(minutes=2),
                "ended_at": datetime.now(timezone.utc) - timedelta(minutes=1, seconds=35),
                "duration_seconds": 25.3,
                "token_count": 1500,
                "ttft": 0.4,
                "tps": 35.2,
            },
            "DEVELOPMENT": {
                "phase_name": "DEVELOPMENT",
                "started_at": datetime.now(timezone.utc) - timedelta(minutes=1, seconds=35),
                "ended_at": datetime.now(timezone.utc),
                "duration_seconds": 65.0,
                "token_count": 2500,
                "ttft": 0.3,
                "tps": 45.0,
            },
        },
        "loop_metrics": {
            "loop-001": {
                "loop_id": "loop-001",
                "phase_name": "DEVELOPMENT",
                "iteration_count": 3,
                "quality_scores": [0.65, 0.78, 0.92],
                "average_quality": 0.78,
                "max_quality": 0.92,
                "defects_by_type": {"testing": 2, "documentation": 1},
                "started_at": datetime.now(timezone.utc) - timedelta(minutes=1, seconds=35),
                "ended_at": datetime.now(timezone.utc),
            },
        },
        "state_transitions": [
            {
                "from_state": "INIT",
                "to_state": "PLANNING",
                "timestamp": datetime.now(timezone.utc) - timedelta(minutes=2),
                "reason": "Phase transition",
                "metadata": {},
            },
            {
                "from_state": "PLANNING",
                "to_state": "DEVELOPMENT",
                "timestamp": datetime.now(timezone.utc) - timedelta(minutes=1, seconds=35),
                "reason": "Phase exit",
                "metadata": {},
            },
        ],
        "defects_by_type": {"testing": 3, "documentation": 2},
        "agent_selections": [
            {
                "phase": "PLANNING",
                "agent_id": "senior-developer",
                "reason": "Best match for requirements analysis",
                "alternatives": ["architect", "tech-lead"],
                "timestamp": datetime.now(timezone.utc) - timedelta(minutes=2),
            },
        ],
    }

    # Sample metrics history
    service.get_metrics_history.return_value = [
        {
            "timestamp": datetime.now(timezone.utc) - timedelta(seconds=i * 10),
            "loop_id": "loop-001",
            "phase": "DEVELOPMENT",
            "metric_type": "TPS",
            "value": 40.0 + i,
            "metadata": {"iteration": i},
        }
        for i in range(10)
    ]

    # Sample aggregate metrics
    service.get_aggregate_metrics.return_value = {
        "total_pipelines": 5,
        "time_range": {
            "start": datetime.now(timezone.utc) - timedelta(days=1),
            "end": datetime.now(timezone.utc),
        },
        "metric_statistics": {
            "TPS": {
                "metric_type": "TPS",
                "count": 100,
                "mean": 40.5,
                "median": 42.0,
                "std_dev": 5.2,
                "min_value": 25.0,
                "max_value": 55.0,
                "trend": "stable",
                "percentiles": {"p50": 42.0, "p90": 50.0, "p99": 54.0},
            },
            "TTFT": {
                "metric_type": "TTFT",
                "count": 100,
                "mean": 0.35,
                "median": 0.32,
                "std_dev": 0.1,
                "min_value": 0.15,
                "max_value": 0.65,
                "trend": "improving",
                "percentiles": {"p50": 0.32, "p90": 0.50, "p99": 0.62},
            },
        },
        "overall_health": 0.85,
        "recommendations": [
            "Consider optimizing phase transitions",
            "Improve test coverage in development phase",
        ],
    }

    # Phase timings
    service.get_phase_timings.return_value = {
        "PLANNING": {
            "phase_name": "PLANNING",
            "started_at": datetime.now(timezone.utc) - timedelta(minutes=2),
            "ended_at": datetime.now(timezone.utc) - timedelta(minutes=1, seconds=35),
            "duration_seconds": 25.3,
            "token_count": 1500,
            "ttft": 0.4,
            "tps": 35.2,
        },
    }

    # Loop metrics
    service.get_loop_metrics.return_value = {
        "loop-001": {
            "loop_id": "loop-001",
            "phase_name": "DEVELOPMENT",
            "iteration_count": 3,
            "quality_scores": [0.65, 0.78, 0.92],
            "average_quality": 0.78,
            "defects_by_type": {"testing": 2},
        },
    }

    # Quality history
    service.get_quality_history.return_value = [
        ("loop-001", "DEVELOPMENT", 0.65),
        ("loop-001", "DEVELOPMENT", 0.78),
        ("loop-001", "DEVELOPMENT", 0.92),
    ]

    # Defects by type
    service.get_defects_by_type.return_value = {"testing": 3, "documentation": 2}

    # State transitions
    service.get_state_transitions.return_value = [
        {
            "from_state": "INIT",
            "to_state": "PLANNING",
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=2),
            "reason": "Phase transition",
        },
    ]

    # Agent selections
    service.get_agent_selections.return_value = [
        {
            "phase": "PLANNING",
            "agent_id": "senior-developer",
            "reason": "Best match",
            "alternatives": ["architect"],
            "timestamp": datetime.now(timezone.utc) - timedelta(minutes=2),
        },
    ]

    # Available metrics
    service.list_available_metrics.return_value = {
        "metric_types": [
            "TOKEN_EFFICIENCY",
            "CONTEXT_UTILIZATION",
            "QUALITY_VELOCITY",
            "DEFECT_DENSITY",
            "TPS",
            "TTFT",
        ],
        "categories": {
            "efficiency": ["TOKEN_EFFICIENCY", "CONTEXT_UTILIZATION"],
            "quality": ["QUALITY_VELOCITY", "DEFECT_DENSITY"],
            "performance": ["TPS", "TTFT"],
        },
    }

    return service


@pytest.fixture
def client_with_metrics(mock_metrics_service):
    """Create test client with mocked metrics service."""
    app = create_app(db_path=":memory:")

    # Import router module
    from gaia.ui.routers import pipeline_metrics as metrics_router

    # Override dependency
    def override_get_service():
        return mock_metrics_service

    app.dependency_overrides[metrics_router.get_metrics_service] = override_get_service

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


class TestMetricsAPIPaths:
    """Test that metrics API paths are correct (no duplication bug)."""

    def test_get_metrics_path(self, client_with_metrics):
        """Test that /api/v1/pipeline/{id}/metrics returns 200, not 404."""
        response = client_with_metrics.get("/api/v1/pipeline/test-pipeline-001/metrics")
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_metrics_history_path(self, client_with_metrics):
        """Test that metrics history path is correct."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/history"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_aggregate_metrics_path(self, client_with_metrics):
        """Test that aggregate metrics path is correct."""
        response = client_with_metrics.get("/api/v1/pipeline/metrics/aggregate")
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_metrics_types_path(self, client_with_metrics):
        """Test that metrics types list path is correct."""
        response = client_with_metrics.get("/api/v1/pipeline/metrics/types")
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_phase_metrics_path(self, client_with_metrics):
        """Test that phase metrics path is correct."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/phases"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_loop_metrics_path(self, client_with_metrics):
        """Test that loop metrics path is correct."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/loops"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_quality_history_path(self, client_with_metrics):
        """Test that quality history path is correct."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/quality"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_defect_metrics_path(self, client_with_metrics):
        """Test that defect metrics path is correct."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/defects"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_state_transitions_path(self, client_with_metrics):
        """Test that state transitions path is correct."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/transitions"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200

    def test_agent_selections_path(self, client_with_metrics):
        """Test that agent selections path is correct."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/agents"
        )
        assert response.status_code != 404, "Path duplication bug detected!"
        assert response.status_code == 200


class TestPipelineMetricsEndpoint:
    """Test /api/v1/pipeline/{id}/metrics endpoint."""

    def test_get_metrics_success(self, client_with_metrics):
        """Test successful metrics fetch."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics"
        )
        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["success"] is True
        assert data["pipeline_id"] == "test-pipeline-001"
        assert "summary" in data
        assert "phase_breakdown" in data
        assert "loop_metrics" in data
        assert "state_transitions" in data
        assert "defects_by_type" in data
        assert "agent_selections" in data

    def test_get_metrics_summary_values(self, client_with_metrics):
        """Test that summary values are correct."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics"
        )
        data = response.json()

        summary = data["summary"]
        assert summary["total_duration_seconds"] == 120.5
        assert summary["total_tokens"] == 5000
        assert summary["avg_tps"] == 41.5
        assert summary["avg_ttft"] == 0.35
        assert summary["total_loops"] == 4
        assert summary["total_iterations"] == 12
        assert summary["avg_quality_score"] == 0.88

    def test_get_metrics_not_found(self, client_with_metrics, mock_metrics_service):
        """Test metrics for non-existent pipeline."""
        # Mock service to return None for unknown pipeline
        def mock_get_summary(pipeline_id):
            if pipeline_id == "unknown":
                return None
            return {
                "summary": {"pipeline_id": pipeline_id},
                "phase_breakdown": {},
                "loop_metrics": {},
                "state_transitions": [],
                "defects_by_type": {},
                "agent_selections": [],
            }

        mock_metrics_service.get_metrics_summary = mock_get_summary

        response = client_with_metrics.get("/api/v1/pipeline/unknown/metrics")
        assert response.status_code == 404

    def test_get_metrics_phase_breakdown(self, client_with_metrics):
        """Test phase breakdown in metrics response."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics"
        )
        data = response.json()

        assert "PLANNING" in data["phase_breakdown"]
        assert "DEVELOPMENT" in data["phase_breakdown"]

        # Verify phase data
        planning = data["phase_breakdown"]["PLANNING"]
        assert planning["duration_seconds"] == 25.3
        assert planning["tps"] == 35.2
        assert planning["ttft"] == 0.4


class TestMetricsHistoryEndpoint:
    """Test /api/v1/pipeline/{id}/metrics/history endpoint."""

    def test_get_history_success(self, client_with_metrics):
        """Test successful history fetch."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/history"
        )
        assert response.status_code == 200
        data = response.json()

        assert "pipeline_id" in data
        assert "total_points" in data
        assert "history" in data
        assert data["total_points"] == 10

    def test_get_history_with_metric_type(self, client_with_metrics):
        """Test history fetch with metric type filter."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/history?metric_type=TPS"
        )
        assert response.status_code == 200
        data = response.json()

        assert data["metric_type"] == "TPS"

    def test_get_history_with_limit(self, client_with_metrics):
        """Test history fetch with limit."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/history?limit=5"
        )
        assert response.status_code == 200
        data = response.json()

        # Service returns 10, but client can limit
        assert data["total_points"] >= 0


class TestAggregateMetricsEndpoint:
    """Test /api/v1/pipeline/metrics/aggregate endpoint."""

    def test_get_aggregate_success(self, client_with_metrics):
        """Test successful aggregate metrics fetch."""
        response = client_with_metrics.get("/api/v1/pipeline/metrics/aggregate")
        assert response.status_code == 200
        data = response.json()

        assert data["success"] is True
        assert data["total_pipelines"] == 5
        assert "metric_statistics" in data
        assert "overall_health" in data
        assert "recommendations" in data

    def test_get_aggregate_with_pipeline_ids(self, client_with_metrics):
        """Test aggregate metrics with specific pipeline IDs."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/metrics/aggregate?pipeline_ids=pipeline-1,pipeline-2"
        )
        assert response.status_code == 200
        data = response.json()

        assert data["success"] is True

    def test_get_aggregate_health_score(self, client_with_metrics):
        """Test that health score is in valid range."""
        response = client_with_metrics.get("/api/v1/pipeline/metrics/aggregate")
        data = response.json()

        health = data["overall_health"]
        assert 0.0 <= health <= 1.0


class TestMetricsTypesEndpoint:
    """Test /api/v1/pipeline/metrics/types endpoint."""

    def test_list_metrics_types_success(self, client_with_metrics):
        """Test successful metrics types list."""
        response = client_with_metrics.get("/api/v1/pipeline/metrics/types")
        assert response.status_code == 200
        data = response.json()

        assert "metric_types" in data
        assert "categories" in data
        assert isinstance(data["metric_types"], list)
        assert isinstance(data["categories"], dict)

    def test_metrics_categories(self, client_with_metrics):
        """Test that categories are properly organized."""
        response = client_with_metrics.get("/api/v1/pipeline/metrics/types")
        data = response.json()

        assert "efficiency" in data["categories"]
        assert "quality" in data["categories"]
        assert "performance" in data["categories"]


class TestPhaseMetricsEndpoint:
    """Test /api/v1/pipeline/{id}/metrics/phases endpoint."""

    def test_get_phase_metrics_success(self, client_with_metrics):
        """Test successful phase metrics fetch."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/phases"
        )
        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, dict)
        assert "PLANNING" in data

    def test_get_phase_metrics_values(self, client_with_metrics):
        """Test phase metrics values."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/phases"
        )
        data = response.json()

        planning = data["PLANNING"]
        assert planning["phase_name"] == "PLANNING"
        assert planning["duration_seconds"] == 25.3
        assert planning["tps"] == 35.2


class TestLoopMetricsEndpoint:
    """Test /api/v1/pipeline/{id}/metrics/loops endpoint."""

    def test_get_loop_metrics_success(self, client_with_metrics):
        """Test successful loop metrics fetch."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/loops"
        )
        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, dict)
        assert "loop-001" in data

    def test_get_loop_metrics_with_id(self, client_with_metrics):
        """Test loop metrics with specific loop ID."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/loops?loop_id=loop-001"
        )
        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, dict)


class TestQualityHistoryEndpoint:
    """Test /api/v1/pipeline/{id}/metrics/quality endpoint."""

    def test_get_quality_history_success(self, client_with_metrics):
        """Test successful quality history fetch."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/quality"
        )
        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, list)
        assert len(data) == 3

    def test_get_quality_history_format(self, client_with_metrics):
        """Test quality history response format."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/quality"
        )
        data = response.json()

        for item in data:
            assert "loop_id" in item
            assert "phase" in item
            assert "score" in item
            assert 0 <= item["score"] <= 1


class TestDefectMetricsEndpoint:
    """Test /api/v1/pipeline/{id}/metrics/defects endpoint."""

    def test_get_defect_metrics_success(self, client_with_metrics):
        """Test successful defect metrics fetch."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/defects"
        )
        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, dict)
        assert "testing" in data
        assert "documentation" in data

    def test_get_defect_counts(self, client_with_metrics):
        """Test defect counts."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/defects"
        )
        data = response.json()

        assert data["testing"] == 3
        assert data["documentation"] == 2


class TestStateTransitionsEndpoint:
    """Test /api/v1/pipeline/{id}/metrics/transitions endpoint."""

    def test_get_transitions_success(self, client_with_metrics):
        """Test successful state transitions fetch."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/transitions"
        )
        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_transitions_format(self, client_with_metrics):
        """Test state transitions response format."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/transitions"
        )
        data = response.json()

        for transition in data:
            assert "from_state" in transition
            assert "to_state" in transition
            assert "timestamp" in transition
            assert "reason" in transition


class TestAgentSelectionsEndpoint:
    """Test /api/v1/pipeline/{id}/metrics/agents endpoint."""

    def test_get_selections_success(self, client_with_metrics):
        """Test successful agent selections fetch."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/agents"
        )
        assert response.status_code == 200
        data = response.json()

        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_selections_format(self, client_with_metrics):
        """Test agent selections response format."""
        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics/agents"
        )
        data = response.json()

        for selection in data:
            assert "phase" in selection
            assert "agent_id" in selection
            assert "reason" in selection
            assert "alternatives" in selection
            assert "timestamp" in selection


class TestMetricsErrorHandling:
    """Test error handling in metrics API."""

    def test_metrics_service_error(self, client_with_metrics, mock_metrics_service):
        """Test handling of service errors."""
        def mock_error(*args, **kwargs):
            raise Exception("Service error")

        mock_metrics_service.get_metrics_summary = mock_error

        response = client_with_metrics.get(
            "/api/v1/pipeline/test-pipeline-001/metrics"
        )
        assert response.status_code == 500


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
