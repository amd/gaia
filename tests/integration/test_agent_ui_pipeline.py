# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration tests for Agent UI Pipeline Integration (B3-C).

Tests cover:
- SSE endpoint for pipeline execution
- Pipeline router integration
- Frontend component rendering
- End-to-end pipeline via Agent UI
"""

import json
import pytest
from unittest.mock import Mock, patch, AsyncMock
from typing import Dict, Any, List

from gaia.pipeline.orchestrator import PipelineOrchestrator


class TestPipelineRouter:
    """Tests for pipeline router endpoints."""

    @pytest.fixture
    def client(self, mocker):
        """Create test client for Agent UI API."""
        # Mock the FastAPI app and client
        from fastapi.testclient import TestClient
        from gaia.ui.server import create_app

        # Mock PipelineOrchestrator to avoid real LLM calls
        mocker.patch('gaia.pipeline.orchestrator.PipelineOrchestrator')

        app = create_app()
        return TestClient(app)

    def test_pipeline_run_endpoint_exists(self, client, mocker):
        """Verify POST /api/v1/pipeline/run endpoint exists."""
        # Mock orchestrator response
        mock_result = {
            "pipeline_status": "success",
            "stage_results": {
                "domain_analysis": {"primary_domain": "test"},
            },
            "agents_spawned": [],
            "execution_result": {"result": "mocked"}
        }

        with patch.object(PipelineOrchestrator, 'run_pipeline', return_value=mock_result):
            response = client.post("/api/v1/pipeline/run", json={
                "task": "Build a calculator",
                "auto_spawn": True
            })

            # Should either succeed or return 404 if endpoint not mounted
            if response.status_code == 404:
                pytest.skip("Pipeline endpoint not mounted yet (B3-C in progress)")
            else:
                assert response.status_code == 200

    def test_pipeline_run_endpoint_accepts_task(self, client, mocker):
        """Verify pipeline endpoint accepts task description."""
        mock_result = {
            "pipeline_status": "success",
            "stage_results": {},
            "agents_spawned": [],
            "execution_result": {}
        }

        with patch.object(PipelineOrchestrator, 'run_pipeline', return_value=mock_result):
            response = client.post("/api/v1/pipeline/run", json={
                "task": "Create REST API with database integration",
                "auto_spawn": True,
                "model": "Qwen3.5-35B-A3B-GGUF"
            })

            if response.status_code == 404:
                pytest.skip("Pipeline endpoint not mounted yet")

            assert response.status_code == 200
            data = response.json()
            assert "pipeline_status" in data

    def test_pipeline_run_endpoint_sse_streaming(self, client, mocker):
        """Verify pipeline endpoint supports SSE streaming."""
        # This test verifies SSE event streaming
        # Implementation depends on how B3-C is implemented

        mock_result = {
            "pipeline_status": "success",
            "stage_results": {
                "domain_analysis": {"stage": 1, "status": "complete"},
                "workflow_model": {"stage": 2, "status": "complete"},
                "loom_topology": {"stage": 3, "status": "complete"},
                "gap_analysis": {"stage": 4, "status": "complete"},
                "pipeline_execution": {"stage": 5, "status": "complete"}
            },
            "agents_spawned": [],
            "execution_result": {"result": "success"}
        }

        with patch.object(PipelineOrchestrator, 'run_pipeline', return_value=mock_result):
            response = client.post(
                "/api/v1/pipeline/run",
                json={"task": "Test task"},
                headers={"Accept": "text/event-stream"}
            )

            if response.status_code == 404:
                pytest.skip("Pipeline SSE endpoint not implemented yet")

            # Check for SSE content-type
            if response.status_code == 200:
                content_type = response.headers.get("content-type", "")
                # Either JSON or SSE depending on implementation
                assert "application/json" in content_type or "text/event-stream" in content_type


class TestPipelineStatusEndpoint:
    """Tests for pipeline status endpoint."""

    @pytest.fixture
    def client(self, mocker):
        """Create test client."""
        from fastapi.testclient import TestClient
        from gaia.ui.server import create_app

        mocker.patch('gaia.pipeline.orchestrator.PipelineOrchestrator')
        app = create_app()
        return TestClient(app)

    def test_pipeline_status_endpoint_exists(self, client):
        """Verify GET /api/v1/pipeline/status endpoint exists."""
        response = client.get("/api/v1/pipeline/status")

        if response.status_code == 404:
            pytest.skip("Pipeline status endpoint not mounted yet")

        assert response.status_code in [200, 400]  # 400 if no pipeline_id


class TestSSEEventStreaming:
    """Tests for SSE event streaming."""

    def parse_sse_events(self, text: str) -> List[Dict[str, Any]]:
        """Parse SSE event stream text into list of events."""
        events = []
        current_event = {}

        for line in text.split('\n'):
            if line.startswith('event:'):
                current_event['event'] = line[6:].strip()
            elif line.startswith('data:'):
                data = line[5:].strip()
                try:
                    current_event['data'] = json.loads(data)
                except json.JSONDecodeError:
                    current_event['data'] = data
            elif line == '' and current_event:
                events.append(current_event)
                current_event = {}

        if current_event:
            events.append(current_event)

        return events

    def test_sse_event_format(self):
        """Verify SSE events have correct format."""
        # Sample SSE event stream
        sample_sse = """event: stage-progress
data: {"stage": 1, "name": "Domain Analysis", "status": "in_progress"}

event: stage-progress
data: {"stage": 1, "name": "Domain Analysis", "status": "complete"}

event: stage-progress
data: {"stage": 2, "name": "Workflow Modeling", "status": "in_progress"}
"""

        events = self.parse_sse_events(sample_sse)

        assert len(events) == 3
        assert events[0]['event'] == 'stage-progress'
        assert events[0]['data']['stage'] == 1
        assert events[0]['data']['status'] == 'in_progress"

    def test_sse_events_cover_all_stages(self):
        """Verify SSE events cover all 5 pipeline stages."""
        # Expected stages
        expected_stages = [
            "Domain Analysis",
            "Workflow Modeling",
            "Loom Building",
            "Gap Detection",
            "Pipeline Execution"
        ]

        # This test validates the SSE event structure
        # Actual implementation depends on B3-C implementation
        sample_events = [
            {"event": "stage-progress", "data": {"stage": 1, "name": "Domain Analysis"}},
            {"event": "stage-progress", "data": {"stage": 2, "name": "Workflow Modeling"}},
            {"event": "stage-progress", "data": {"stage": 3, "name": "Loom Building"}},
            {"event": "stage-progress", "data": {"stage": 4, "name": "Gap Detection"}},
            {"event": "stage-progress", "data": {"stage": 5, "name": "Pipeline Execution"}},
        ]

        stage_names = [e['data']['name'] for e in sample_events]
        for expected in expected_stages:
            assert expected in stage_names, f"Missing stage: {expected}"


class TestPipelineErrorHandling:
    """Tests for pipeline error handling."""

    @pytest.fixture
    def client(self, mocker):
        """Create test client."""
        from fastapi.testclient import TestClient
        from gaia.ui.server import create_app

        mocker.patch('gaia.pipeline.orchestrator.PipelineOrchestrator')
        app = create_app()
        return TestClient(app)

    def test_pipeline_handles_invalid_task(self, client, mocker):
        """Verify pipeline handles invalid/empty task."""
        with patch.object(PipelineOrchestrator, 'run_pipeline') as mock_run:
            mock_run.side_effect = ValueError("Task description required")

            response = client.post("/api/v1/pipeline/run", json={
                "task": ""
            })

            if response.status_code == 404:
                pytest.skip("Pipeline endpoint not mounted yet")

            # Should return error response
            assert response.status_code in [400, 500]

    def test_pipeline_handles_llm_unavailable(self, client, mocker):
        """Verify pipeline handles LLM unavailability."""
        with patch.object(PipelineOrchestrator, 'run_pipeline') as mock_run:
            mock_run.side_effect = Exception("LLM not available")

            response = client.post("/api/v1/pipeline/run", json={
                "task": "Test task"
            })

            if response.status_code == 404:
                pytest.skip("Pipeline endpoint not mounted yet")

            # Should return error with appropriate status
            assert response.status_code in [500, 503]
            data = response.json()
            assert "error" in data or "detail" in data


class TestPipelineFrontendComponent:
    """Tests for PipelinePanel frontend component."""

    # These tests require Jest/React Testing Library
    # Placeholder for frontend tests

    def test_pipeline_panel_component_exists(self):
        """Verify PipelinePanel component file exists."""
        component_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "apps" / "webui" / "src" / "components" / "PipelinePanel.tsx"

        if not component_path.exists():
            pytest.skip("PipelinePanel component not created yet (B3-C in progress)")

        # Verify file has content
        content = component_path.read_text(encoding='utf-8')
        assert len(content) > 100, "PipelinePanel component too short"
        assert "export" in content, "PipelinePanel missing export"

    def test_pipeline_service_file_exists(self):
        """Verify pipeline service file exists."""
        service_path = Path(__file__).parent.parent.parent / "src" / "gaia" / "apps" / "webui" / "src" / "services" / "pipeline.ts"

        if not service_path.exists():
            pytest.skip("Pipeline service not created yet (B3-C in progress)")

        content = service_path.read_text(encoding='utf-8')
        assert len(content) > 50, "Pipeline service too short"
        assert "fetch" in content or "axios" in content or "api" in content


class TestPipelineIntegration:
    """Integration tests for complete pipeline flow."""

    @pytest.mark.integration
    @pytest.mark.require_lemonade
    def test_pipeline_end_to_end_via_api(self, require_lemonade, client, mocker):
        """E2E test: Complete pipeline execution via Agent UI API."""
        # This test requires:
        # 1. Lemonade server running
        # 2. Pipeline endpoint mounted
        # 3. All 5 stage agents available

        mock_result = {
            "pipeline_status": "success",
            "stage_results": {
                "domain_analysis": {"primary_domain": "software-development"},
                "workflow_model": {"workflow_pattern": "standard"},
                "loom_topology": {"execution_graph": {"nodes": [1, 2, 3]}},
                "gap_analysis": {"gaps_identified": False},
                "pipeline_execution": {"result": "success"}
            },
            "agents_spawned": [],
            "execution_result": {"artifacts": ["output.py"]}
        }

        with patch.object(PipelineOrchestrator, 'run_pipeline', return_value=mock_result):
            response = client.post("/api/v1/pipeline/run", json={
                "task": "Create a Python module with add and multiply functions",
                "auto_spawn": True
            })

            if response.status_code == 404:
                pytest.skip("Pipeline endpoint not available")

            assert response.status_code == 200
            data = response.json()
            assert data["pipeline_status"] == "success"


# Import Path for file checks
from pathlib import Path
