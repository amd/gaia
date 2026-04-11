# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for PipelineOrchestrator.

Tests cover:
- Full pipeline execution flow
- Gap detection and agent spawning
- Clear Thought MCP integration
- Error handling and recovery
- Stage-by-stage validation
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock, call
from typing import Dict, Any

from gaia.pipeline.orchestrator import PipelineOrchestrator, run_pipeline


class TestPipelineOrchestratorInit:
    """Tests for PipelineOrchestrator initialization."""

    def test_init_default_configuration(self):
        """Verify default configuration on initialization."""
        orchestrator = PipelineOrchestrator()

        assert orchestrator.model_id == "Qwen3.5-35B-A3B-GGUF"
        assert orchestrator.max_steps == 50
        assert orchestrator.debug is False
        assert orchestrator._domain_analyzer is None
        assert orchestrator._workflow_modeler is None
        assert orchestrator._loom_builder is None
        assert orchestrator._gap_detector is None
        assert orchestrator._pipeline_executor is None

    def test_init_custom_configuration(self):
        """Verify custom configuration is applied."""
        orchestrator = PipelineOrchestrator(
            model_id="test-model",
            max_steps=100,
            debug=True
        )

        assert orchestrator.model_id == "test-model"
        assert orchestrator.max_steps == 100
        assert orchestrator.debug is True


class TestPipelineOrchestratorTools:
    """Tests for PipelineOrchestrator tool methods."""

    @pytest.fixture
    def orchestrator(self, mocker):
        """Create orchestrator with mocked chat."""
        orch = PipelineOrchestrator(model_id="test-model")
        orch.chat = mocker.Mock()
        return orch

    def test_execute_full_pipeline_success(self, orchestrator, mocker):
        """Verify complete pipeline execution flow with all stages."""
        # Arrange: Mock all Clear Thought methods
        mocker.patch.object(
            orchestrator, '_clear_thought_domain_analysis',
            return_value={"summary": "Domain analysis complete", "primary_domain": "api"}
        )
        mocker.patch.object(
            orchestrator, '_clear_thought_workflow_planning',
            return_value={"summary": "Workflow planned", "pattern": "standard"}
        )
        mocker.patch.object(
            orchestrator, '_clear_thought_topology_design',
            return_value={"summary": "Topology designed", "nodes": 5}
        )

        # Mock stage instances
        mock_domain = mocker.Mock()
        mock_domain.analyze.return_value = {"primary_domain": "api-development"}
        orchestrator._domain_analyzer = mock_domain

        mock_workflow = mocker.Mock()
        mock_workflow.model_workflow.return_value = {"workflow_pattern": "standard"}
        orchestrator._workflow_modeler = mock_workflow

        mock_loom = mocker.Mock()
        mock_loom.build_loom.return_value = {"execution_graph": {"nodes": [1, 2, 3]}}
        orchestrator._loom_builder = mock_loom

        mock_gap = mocker.Mock()
        mock_gap.detect_gaps.return_value = {
            "gap_result": {"gaps_identified": False, "missing_agents": []}
        }
        orchestrator._gap_detector = mock_gap

        mock_executor = mocker.Mock()
        mock_executor.execute_pipeline.return_value = {"result": "success"}
        orchestrator._pipeline_executor = mock_executor

        # Act
        result = orchestrator._execute_tool(
            "execute_full_pipeline",
            {
                "task_description": "Build a REST API endpoint",
                "auto_spawn": True,
                "use_clear_thought": True
            }
        )

        # Assert
        assert result["pipeline_status"] == "success"
        assert "stage_results" in result
        assert "clear_thought_analyses" in result
        assert len(result["clear_thought_analyses"]) == 3  # Domain, Workflow, Loom

        # Verify all stages were called
        assert mock_domain.analyze.called
        assert mock_workflow.model_workflow.called
        assert mock_loom.build_loom.called
        assert mock_gap.detect_gaps.called
        assert mock_executor.execute_pipeline.called

    def test_execute_pipeline_gap_detection_triggers_spawn(self, orchestrator, mocker):
        """Verify gap detection triggers agent generation when auto_spawn=True."""
        # Arrange: Mock stages with no gaps until GapDetector
        mock_gap = mocker.Mock()
        mock_gap.detect_gaps.return_value = {
            "gap_result": {
                "gaps_identified": True,
                "missing_agents": ["api-specialist", "database-expert"]
            }
        }
        orchestrator._gap_detector = mock_gap

        # Mock agent spawn
        mock_spawn_result = {
            "generation_status": "success",
            "agents_spawned": ["api-specialist", "database-expert"],
            "clear_thought_analysis": {"plan": "Generate 2 agents"}
        }
        mocker.patch.object(orchestrator, '_trigger_agent_spawn', return_value=mock_spawn_result)

        mock_executor = mocker.Mock()
        mock_executor.execute_pipeline.return_value = {"result": "success"}
        orchestrator._pipeline_executor = mock_executor

        # Act
        result = orchestrator._execute_tool(
            "execute_full_pipeline",
            {"task_description": "Build full-stack app", "auto_spawn": True, "use_clear_thought": True}
        )

        # Assert
        assert result["pipeline_status"] == "success"
        assert len(result["agents_spawned"]) == 2
        assert "api-specialist" in result["agents_spawned"]
        assert "database-expert" in result["agents_spawned"]
        assert "agent_generation" in result["clear_thought_analyses"]
        orchestrator._trigger_agent_spawn.assert_called_once()

    def test_execute_pipeline_auto_spawn_disabled_blocks(self, orchestrator, mocker):
        """Verify pipeline blocks when auto_spawn=False and gaps exist."""
        # Arrange
        mock_gap = mocker.Mock()
        mock_gap.detect_gaps.return_value = {
            "gap_result": {
                "gaps_identified": True,
                "missing_agents": ["missing-agent"]
            }
        }
        orchestrator._gap_detector = mock_gap

        # Act
        result = orchestrator._execute_tool(
            "execute_full_pipeline",
            {"task_description": "Test task", "auto_spawn": False, "use_clear_thought": True}
        )

        # Assert
        assert result["pipeline_status"] == "blocked"
        assert result["block_reason"] == "missing_agents_require_generation"
        assert len(result["agents_spawned"]) == 0
        assert result["execution_result"] == {}

    def test_execute_pipeline_error_handling(self, orchestrator, mocker):
        """Verify error handling during pipeline execution."""
        # Arrange: Mock domain analyzer to fail
        mock_domain = mocker.Mock()
        mock_domain.analyze.side_effect = Exception("Domain analysis failed")
        orchestrator._domain_analyzer = mock_domain

        # Act
        result = orchestrator._execute_tool(
            "execute_full_pipeline",
            {"task_description": "Test task", "auto_spawn": True, "use_clear_thought": True}
        )

        # Assert
        assert result["pipeline_status"] == "failed"
        assert "error" in result
        assert "Domain analysis failed" in result["error"]

    def test_get_pipeline_status_tool(self, orchestrator):
        """Verify get_pipeline_status returns current state."""
        # Arrange
        orchestrator._domain_blueprint = {"primary_domain": "test"}
        orchestrator._workflow_model = {"pattern": "standard"}

        # Act
        result = orchestrator._execute_tool("get_pipeline_status", {})

        # Assert
        assert result["domain_blueprint"] == {"primary_domain": "test"}
        assert result["workflow_model"] == {"pattern": "standard"}
        assert result["pipeline_status"] == "workflow_modeling_complete"


class TestClearThoughtIntegration:
    """Tests for Clear Thought MCP integration."""

    @pytest.fixture
    def orchestrator(self, mocker):
        """Create orchestrator with mocked chat."""
        orch = PipelineOrchestrator(model_id="test-model")
        orch.chat = mocker.Mock()
        return orch

    def test_clear_thought_domain_analysis_json_response(self, orchestrator, mocker):
        """Verify Clear Thought domain analysis parses JSON response."""
        # Arrange
        mock_response = mocker.Mock()
        mock_response.text = json.dumps({
            "primary_domain": "api-development",
            "secondary_domains": ["database", "frontend"],
            "complexity": "high"
        })
        orchestrator.chat.send_messages.return_value = mock_response

        # Act
        result = orchestrator._clear_thought_domain_analysis("Build REST API")

        # Assert
        assert "summary" in result
        assert "analysis" in result
        assert result["analysis"]["primary_domain"] == "api-development"

    def test_clear_thought_domain_analysis_prose_response(self, orchestrator, mocker):
        """Verify Clear Thought handles prose (non-JSON) response."""
        # Arrange
        mock_response = mocker.Mock()
        mock_response.text = "The primary domain is API development with high complexity."
        orchestrator.chat.send_messages.return_value = mock_response

        # Act
        result = orchestrator._clear_thought_domain_analysis("Build REST API")

        # Assert
        assert "raw_response" in result["analysis"]
        assert "API development" in result["analysis"]["raw_response"]

    def test_clear_thought_workflow_planning(self, orchestrator, mocker):
        """Verify Clear Thought workflow planning."""
        mock_response = mocker.Mock()
        mock_response.text = json.dumps({
            "workflow_pattern": "standard",
            "phases": ["planning", "development", "testing"],
            "recommended_agents": ["senior-developer", "quality-reviewer"]
        })
        orchestrator.chat.send_messages.return_value = mock_response

        domain_blueprint = {"primary_domain": "api", "secondary_domains": []}
        result = orchestrator._clear_thought_workflow_planning(
            domain_blueprint, "Build API"
        )

        assert "plan" in result or "summary" in result

    def test_clear_thought_topology_design(self, orchestrator, mocker):
        """Verify Clear Thought topology design."""
        mock_response = mocker.Mock()
        mock_response.text = json.dumps({
            "execution_order": ["agent1", "agent2", "agent3"],
            "dependencies": {"agent2": ["agent1"], "agent3": ["agent2"]},
            "tools_required": ["file_read", "file_write", "shell"]
        })
        orchestrator.chat.send_messages.return_value = mock_response

        workflow_model = {"workflow_pattern": "standard", "phases": []}
        domain_blueprint = {"primary_domain": "api"}
        result = orchestrator._clear_thought_topology_design(
            workflow_model, domain_blueprint
        )

        assert "design" in result or "summary" in result


class TestTriggerAgentSpawn:
    """Tests for _trigger_agent_spawn tool."""

    @pytest.fixture
    def orchestrator(self, mocker):
        """Create orchestrator with mocked components."""
        orch = PipelineOrchestrator(model_id="test-model")
        orch.chat = mocker.Mock()
        orch._gap_analysis = {
            "missing_agents": ["api-specialist", "test-engineer"]
        }
        return orch

    def test_trigger_agent_spawn_with_gaps(self, orchestrator, mocker):
        """Verify agent spawn triggered when gaps exist."""
        # Arrange
        mock_response = mocker.Mock()
        mock_response.text = json.dumps({"plan": "Generate 2 agents in order"})
        orchestrator.chat.send_messages.return_value = mock_response

        # Act
        result = orchestrator._trigger_agent_spawn("Build API with tests")

        # Assert
        assert result["generation_status"] == "success"
        assert len(result["agents_spawned"]) == 2
        assert "mcp_tool_call" in result
        assert "CALL: mcp__master-ecosystem-creator__spawn_agents" in result["mcp_tool_call"]

    def test_trigger_agent_spawn_no_gaps(self, orchestrator, mocker):
        """Verify spawn skipped when no gaps."""
        orchestrator._gap_analysis = {"missing_agents": []}

        result = orchestrator._trigger_agent_spawn("Simple task")

        assert result["generation_status"] == "skipped"
        assert result["reason"] == "no_missing_agents"
        assert len(result["agents_spawned"]) == 0


class TestPipelineStatus:
    """Tests for pipeline status tracking."""

    def test_get_pipeline_status_stages(self):
        """Verify pipeline status reflects stage completion."""
        orchestrator = PipelineOrchestrator()

        # Initial state
        assert orchestrator._get_pipeline_status() == "not_started"

        # After domain analysis
        orchestrator._domain_blueprint = {"primary_domain": "test"}
        assert orchestrator._get_pipeline_status() == "domain_analysis_complete"

        # After workflow modeling
        orchestrator._workflow_model = {"pattern": "standard"}
        assert orchestrator._get_pipeline_status() == "workflow_modeling_complete"

        # After loom building
        orchestrator._loom_topology = {"nodes": [1, 2, 3]}
        assert orchestrator._get_pipeline_status() == "loom_building_complete"

        # After gap detection (no gaps)
        orchestrator._gap_analysis = {"gaps_identified": False}
        assert orchestrator._get_pipeline_status() == "gap_detection_complete_pending_execution"

        # After gap detection (gaps exist)
        orchestrator._gap_analysis = {"gaps_identified": True}
        assert orchestrator._get_pipeline_status() == "gap_detection_complete_pending_spawn"

        # After execution
        orchestrator._execution_result = {"result": "success"}
        assert orchestrator._get_pipeline_status() == "complete"


class TestRunPipelineConvenience:
    """Tests for run_pipeline convenience function."""

    def test_run_pipeline_function(self, mocker):
        """Verify run_pipeline function creates orchestrator and runs."""
        mock_instance = mocker.Mock()
        mock_instance.run_pipeline.return_value = {"status": "success"}

        mocker.patch(
            'gaia.pipeline.orchestrator.PipelineOrchestrator',
            return_value=mock_instance
        )

        result = run_pipeline(
            task_description="Test task",
            auto_spawn=True,
            model_id="test-model",
            debug=False
        )

        assert result == {"status": "success"}
        mock_instance.run_pipeline.assert_called_once_with(
            task_description="Test task",
            auto_spawn=True
        )


class TestAnalyzeWithLLM:
    """Tests for _analyze_with_llm helper method."""

    @pytest.fixture
    def orchestrator(self, mocker):
        """Create orchestrator."""
        orch = PipelineOrchestrator(model_id="test-model")
        orch.chat = mocker.Mock()
        return orch

    def test_analyze_with_llm_json_response(self, orchestrator, mocker):
        """Verify JSON response is parsed correctly."""
        mock_response = mocker.Mock()
        mock_response.text = '{"key": "value", "number": 42}'
        orchestrator.chat.send_messages.return_value = mock_response

        result = orchestrator._analyze_with_llm("Test query")

        assert result["key"] == "value"
        assert result["number"] == 42

    def test_analyze_with_llm_prose_response(self, orchestrator, mocker):
        """Verify prose response is handled."""
        mock_response = mocker.Mock()
        mock_response.text = "This is a prose response without JSON."
        orchestrator.chat.send_messages.return_value = mock_response

        result = orchestrator._analyze_with_llm("Test query")

        assert "raw_response" in result

    def test_analyze_with_llm_dict_response(self, orchestrator, mocker):
        """Verify dict response is returned as-is."""
        mock_response = {"direct": "dict", "response": True}
        orchestrator.chat.send_messages.return_value = mock_response

        result = orchestrator._analyze_with_llm("Test query")

        assert result == {"direct": "dict", "response": True}

    def test_analyze_with_llm_json_decode_error(self, orchestrator, mocker, caplog):
        """Verify JSON decode error is handled."""
        mock_response = mocker.Mock()
        mock_response.text = "{invalid json"
        orchestrator.chat.send_messages.return_value = mock_response

        result = orchestrator._analyze_with_llm("Test query")

        assert "error" in result
        assert "JSON parse error" in result["error"]

    def test_analyze_with_llm_exception(self, orchestrator, mocker):
        """Verify exception during LLM call is handled."""
        orchestrator.chat.send_messages.side_effect = Exception("LLM failed")

        result = orchestrator._analyze_with_llm("Test query")

        assert "error" in result
        assert "LLM failed" in result["error"]
