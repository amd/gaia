# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for PipelineOrchestrator.

Tests cover:
- Pipeline initialization
- Tool registration
- Execute pipeline tool
- Auto-spawn trigger logic
- Clear Thought MCP integration (mocked)
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.pipeline.orchestrator import PipelineOrchestrator


@pytest.fixture
def orchestrator():
    """Create a PipelineOrchestrator instance for testing."""
    return PipelineOrchestrator(model_id="test-model", max_steps=10, debug=True)


@pytest.fixture
def sample_task_description():
    """Sample task description for testing."""
    return "Build a data analysis agent that processes CSV files and creates visualizations"


class TestPipelineOrchestratorInit:
    """Tests for PipelineOrchestrator initialization."""

    def test_default_initialization(self):
        """Test default initialization parameters."""
        orchestrator = PipelineOrchestrator()

        assert orchestrator.model_id == "Qwen3.5-35B-A3B-GGUF"
        assert orchestrator.max_steps == 50
        assert orchestrator.debug is False

    def test_custom_initialization(self):
        """Test custom initialization parameters."""
        orchestrator = PipelineOrchestrator(
            model_id="custom-model", max_steps=100, debug=True
        )

        assert orchestrator.model_id == "custom-model"
        assert orchestrator.max_steps == 100
        assert orchestrator.debug is True

    def test_initial_state(self, orchestrator):
        """Test initial state is properly initialized."""
        assert orchestrator._domain_blueprint == {}
        assert orchestrator._workflow_model == {}
        assert orchestrator._loom_topology == {}
        assert orchestrator._gap_analysis == {}
        assert orchestrator._execution_result == {}

        # Stage instances should be None initially
        assert orchestrator._domain_analyzer is None
        assert orchestrator._workflow_modeler is None
        assert orchestrator._loom_builder is None
        assert orchestrator._gap_detector is None
        assert orchestrator._pipeline_executor is None


class TestExecutePipelineTool:
    """Tests for execute_pipeline tool."""

    @patch("gaia.pipeline.orchestrator.DomainAnalyzer")  # noqa: F811
    @patch("gaia.pipeline.orchestrator.WorkflowModeler")  # noqa: F811
    @patch("gaia.pipeline.orchestrator.LoomBuilder")  # noqa: F811
    @patch("gaia.pipeline.orchestrator.GapDetector")  # noqa: F811
    @patch("gaia.pipeline.orchestrator.PipelineExecutor")  # noqa: F811
    def test_execute_pipeline_success(
        self,
        mock_executor_cls,
        mock_detector_cls,
        mock_builder_cls,
        mock_modeler_cls,
        mock_analyzer_cls,
        orchestrator,
        sample_task_description,
    ):
        """Test successful pipeline execution with all stages."""
        # Setup mocks
        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = {
            "primary_domain": "data-analysis",
            "secondary_domains": ["visualization"],
        }
        mock_analyzer_cls.return_value = mock_analyzer

        mock_modeler = MagicMock()
        mock_modeler.model_workflow.return_value = {
            "workflow_pattern": "sequential",
            "recommended_agents": ["data-processor", "visualizer"],
        }
        mock_modeler_cls.return_value = mock_modeler

        mock_builder = MagicMock()
        mock_builder.build_loom.return_value = {
            "execution_graph": {"nodes": [{"id": "node1"}], "edges": []}
        }
        mock_builder_cls.return_value = mock_builder

        mock_detector = MagicMock()
        mock_detector.detect_gaps.return_value = {
            "gap_result": {
                "gaps_identified": False,
                "missing_agents": [],
                "can_proceed": True,
            }
        }
        mock_detector_cls.return_value = mock_detector

        mock_executor = MagicMock()
        mock_executor.execute_pipeline.return_value = {
            "status": "success",
            "output": "pipeline completed",
        }
        mock_executor_cls.return_value = mock_executor

        # Execute
        result = orchestrator._execute_tool(
            "execute_full_pipeline",
            {
                "task_description": sample_task_description,
                "auto_spawn": False,
                "use_clear_thought": False,
            },
        )

        # Verify
        assert result["pipeline_status"] == "success"
        assert "domain_analysis" in result["stage_results"]
        assert "workflow_model" in result["stage_results"]
        assert "loom_topology" in result["stage_results"]
        assert "gap_analysis" in result["stage_results"]
        assert "pipeline_execution" in result["stage_results"]

        # Verify all stages were called
        mock_analyzer.analyze.assert_called_once()
        mock_modeler.model_workflow.assert_called_once()
        mock_builder.build_loom.assert_called_once()
        mock_detector.detect_gaps.assert_called_once()
        mock_executor.execute_pipeline.assert_called_once()

    @patch("gaia.pipeline.orchestrator.DomainAnalyzer")
    @patch("gaia.pipeline.orchestrator.WorkflowModeler")
    @patch("gaia.pipeline.orchestrator.LoomBuilder")
    @patch("gaia.pipeline.orchestrator.GapDetector")
    def test_execute_pipeline_blocked_on_missing_agents(
        self,
        mock_detector_cls,
        mock_builder_cls,
        mock_modeler_cls,
        mock_analyzer_cls,
        orchestrator,
        sample_task_description,
    ):
        """Test pipeline blocks when gaps detected and auto_spawn=False."""
        # Setup mocks for stages 1-3
        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = {"primary_domain": "test-domain"}
        mock_analyzer_cls.return_value = mock_analyzer

        mock_modeler = MagicMock()
        mock_modeler.model_workflow.return_value = {
            "workflow_pattern": "sequential",
            "recommended_agents": ["missing-agent"],
        }
        mock_modeler_cls.return_value = mock_modeler

        mock_builder = MagicMock()
        mock_builder.build_loom.return_value = {"execution_graph": {"nodes": []}}
        mock_builder_cls.return_value = mock_builder

        # Setup gap detector to find gaps
        mock_detector = MagicMock()
        mock_detector.detect_gaps.return_value = {
            "gap_result": {
                "gaps_identified": True,
                "missing_agents": ["missing-agent"],
                "can_proceed": False,
            }
        }
        mock_detector_cls.return_value = mock_detector

        # Execute with auto_spawn=False
        result = orchestrator._execute_tool(
            "execute_full_pipeline",
            {
                "task_description": sample_task_description,
                "auto_spawn": False,
                "use_clear_thought": False,
            },
        )

        # Verify
        assert result["pipeline_status"] == "blocked"
        assert result["block_reason"] == "missing_agents_require_generation"
        assert result["agents_spawned"] == []
        assert result["execution_result"] == {}

    @patch("gaia.pipeline.orchestrator.DomainAnalyzer")
    @patch("gaia.pipeline.orchestrator.WorkflowModeler")
    @patch("gaia.pipeline.orchestrator.LoomBuilder")
    @patch("gaia.pipeline.orchestrator.GapDetector")
    @patch("gaia.pipeline.orchestrator.PipelineExecutor")
    def test_execute_pipeline_with_clear_thought(
        self,
        mock_executor_cls,
        mock_detector_cls,
        mock_builder_cls,
        mock_modeler_cls,
        mock_analyzer_cls,
        orchestrator,
        sample_task_description,
    ):
        """Test pipeline execution with Clear Thought MCP enabled."""
        # Setup mocks
        mock_analyzer = MagicMock()
        mock_analyzer.analyze.return_value = {"primary_domain": "test-domain"}
        mock_analyzer_cls.return_value = mock_analyzer

        mock_modeler = MagicMock()
        mock_modeler.model_workflow.return_value = {
            "workflow_pattern": "sequential",
            "recommended_agents": [],
        }
        mock_modeler_cls.return_value = mock_modeler

        mock_builder = MagicMock()
        mock_builder.build_loom.return_value = {"execution_graph": {"nodes": []}}
        mock_builder_cls.return_value = mock_builder

        mock_detector = MagicMock()
        mock_detector.detect_gaps.return_value = {
            "gap_result": {"gaps_identified": False, "missing_agents": []}
        }
        mock_detector_cls.return_value = mock_detector

        mock_executor = MagicMock()
        mock_executor.execute_pipeline.return_value = {"status": "success"}
        mock_executor_cls.return_value = mock_executor

        # Mock Clear Thought MCP calls
        with patch.object(
            orchestrator, "_clear_thought_domain_analysis"
        ) as mock_ct_domain:
            with patch.object(
                orchestrator, "_clear_thought_workflow_planning"
            ) as mock_ct_workflow:
                with patch.object(
                    orchestrator, "_clear_thought_topology_design"
                ) as mock_ct_topology:
                    mock_ct_domain.return_value = {
                        "summary": "domain analysis complete"
                    }
                    mock_ct_workflow.return_value = {
                        "summary": "workflow modeling complete"
                    }
                    mock_ct_topology.return_value = {
                        "summary": "topology design complete"
                    }

                    # Execute with Clear Thought enabled
                    result = orchestrator._execute_tool(
                        "execute_full_pipeline",
                        {
                            "task_description": sample_task_description,
                            "auto_spawn": False,
                            "use_clear_thought": True,
                        },
                    )

                    # Verify Clear Thought analyses are in result
                    assert "clear_thought_analyses" in result
                    assert "domain_analysis" in result["clear_thought_analyses"]
                    assert "workflow_modeling" in result["clear_thought_analyses"]
                    assert "loom_building" in result["clear_thought_analyses"]

                    # Verify Clear Thought methods were called
                    mock_ct_domain.assert_called_once_with(sample_task_description)
                    mock_ct_workflow.assert_called_once()
                    mock_ct_topology.assert_called_once()


class TestTriggerAgentSpawn:
    """Tests for _trigger_agent_spawn tool."""

    def test_gap_analysis_state(self, orchestrator):
        """Test that gap analysis state can be set and read."""
        orchestrator._gap_analysis = {
            "missing_agents": ["agent-1", "agent-2"],
            "gaps_identified": True,
        }

        assert orchestrator._gap_analysis["gaps_identified"] is True
        assert orchestrator._gap_analysis["missing_agents"] == ["agent-1", "agent-2"]


class TestClearThoughtMethods:
    """Tests for Clear Thought MCP helper methods."""

    def test_clear_thought_methods_exist(self, orchestrator):
        """Test Clear Thought helper methods exist."""
        assert hasattr(orchestrator, "_clear_thought_domain_analysis")
        assert hasattr(orchestrator, "_clear_thought_workflow_planning")
        assert hasattr(orchestrator, "_clear_thought_topology_design")


class TestPipelineStateManagement:
    """Tests for pipeline state management."""

    def test_state_updated_after_execution(
        self,
        orchestrator,
    ):
        """Test that pipeline state is updated after execution."""
        # Mock all stages
        with patch("gaia.pipeline.orchestrator.DomainAnalyzer") as mock_analyzer_cls:
            with patch(
                "gaia.pipeline.orchestrator.WorkflowModeler"
            ) as mock_modeler_cls:
                with patch(
                    "gaia.pipeline.orchestrator.LoomBuilder"
                ) as mock_builder_cls:
                    with patch(
                        "gaia.pipeline.orchestrator.GapDetector"
                    ) as mock_detector_cls:
                        with patch(
                            "gaia.pipeline.orchestrator.PipelineExecutor"
                        ) as mock_executor_cls:
                            # Setup return values
                            mock_analyzer = MagicMock()
                            mock_analyzer.analyze.return_value = {
                                "primary_domain": "test-domain"
                            }
                            mock_analyzer_cls.return_value = mock_analyzer

                            mock_modeler = MagicMock()
                            mock_modeler.model_workflow.return_value = {
                                "workflow_pattern": "sequential",
                                "recommended_agents": [],
                            }
                            mock_modeler_cls.return_value = mock_modeler

                            mock_builder = MagicMock()
                            mock_builder.build_loom.return_value = {
                                "execution_graph": {"nodes": []}
                            }
                            mock_builder_cls.return_value = mock_builder

                            mock_detector = MagicMock()
                            mock_detector.detect_gaps.return_value = {
                                "gap_result": {"gaps_identified": False}
                            }
                            mock_detector_cls.return_value = mock_detector

                            mock_executor = MagicMock()
                            mock_executor.execute_pipeline.return_value = {
                                "status": "success"
                            }
                            mock_executor_cls.return_value = mock_executor

                            # Execute
                            orchestrator._execute_tool(
                                "execute_full_pipeline",
                                {
                                    "task_description": "Test task",
                                    "auto_spawn": False,
                                    "use_clear_thought": False,
                                },
                            )

                            # Verify state was updated
                            assert orchestrator._domain_blueprint != {}
                            assert orchestrator._workflow_model != {}
                            assert orchestrator._loom_topology != {}
                            assert orchestrator._gap_analysis != {}
                            assert orchestrator._execution_result != {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
