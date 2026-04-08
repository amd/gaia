# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
End-to-End Pipeline Tests - Quality Gate 7 Validation

Tests for the complete 4-stage pipeline:
DomainAnalyzer → WorkflowModeler → LoomBuilder → PipelineExecutor

These tests validate Quality Gate 7 integration criteria:
- INTEGRATION-001: End-to-end pipeline execution
- INTEGRATION-002: Generated agents functional in pipeline
"""

import pytest
import time
from pathlib import Path
from unittest.mock import Mock


class TestFullPipelineExecution:
    """
    Test the complete 4-stage pipeline from task description to agent generation.

    Validates:
    - INTEGRATION-001: End-to-end pipeline execution
    - INTEGRATION-002: Generated agents functional in pipeline
    """

    @pytest.fixture
    def sample_task_description(self):
        """Sample task for pipeline testing."""
        return "Create a data analysis agent that processes CSV files, generates statistical summaries, and creates visualizations using matplotlib"

    @pytest.fixture
    def mock_execute_tool_domain_analyzer(self):
        """Mock execute_tool for DomainAnalyzer."""
        def mock_execute(tool_name, tool_args):
            if tool_name == "identify_domains":
                return {
                    "primary_domain": "Data Analysis",
                    "secondary_domains": ["File Processing", "Statistics", "Data Visualization"],
                    "domain_descriptions": {
                        "Data Analysis": "Processing and interpreting data",
                        "File Processing": "Reading and parsing CSV files"
                    }
                }
            elif tool_name == "extract_requirements":
                return {
                    "functional_requirements": ["Read CSV files", "Compute statistics"],
                    "non_functional_requirements": ["Handle large files"],
                    "domain_knowledge_needed": ["CSV parsing", "Statistics"]
                }
            elif tool_name == "map_dependencies":
                return {
                    "from_domain": "File Processing",
                    "to_domain": "Data Analysis",
                    "dependency_type": "data",
                    "description": "Data flows from file parsing to analysis",
                    "direction": "unidirectional"
                }
            return {}
        return mock_execute

    @pytest.fixture
    def mock_execute_tool_workflow_modeler(self):
        """Mock execute_tool for WorkflowModeler."""
        def mock_execute(tool_name, tool_args):
            if tool_name == "select_workflow_pattern":
                return {
                    "pattern": "pipeline",
                    "rationale": "Sequential data processing workflow",
                    "suitability_score": 0.9
                }
            elif tool_name == "define_phases":
                return {
                    "phases": [
                        {
                            "name": "Data Ingestion",
                            "objectives": ["Load CSV", "Validate format"],
                            "tasks": ["Parse CSV", "Check schema"],
                            "exit_criteria": {"deliverable": "parsed_data"},
                            "estimated_duration": "1-2 hours"
                        },
                        {
                            "name": "Analysis",
                            "objectives": ["Compute statistics"],
                            "tasks": ["Calculate metrics"],
                            "exit_criteria": {"deliverable": "analysis_results"},
                            "estimated_duration": "2-4 hours"
                        }
                    ]
                }
            elif tool_name == "plan_milestones":
                return {
                    "milestones": [
                        {
                            "name": "Data Loaded",
                            "phase": "Data Ingestion",
                            "deliverables": ["parsed_data.csv"],
                            "success_criteria": ["Data validated"]
                        }
                    ]
                }
            elif tool_name == "estimate_complexity":
                return {
                    "complexity_score": 0.6,
                    "complexity_factors": ["Multiple domains", "File I/O"],
                    "resource_estimate": "4-8 hours"
                }
            elif tool_name == "recommend_agents":
                return {
                    "recommended_agents": ["file-processor", "data-analyst"],
                    "agent_phase_mapping": {
                        "Data Ingestion": ["file-processor"],
                        "Analysis": ["data-analyst"]
                    },
                    "rationale": "Specialized agents for each phase"
                }
            return {}
        return mock_execute

    @pytest.fixture
    def mock_execute_tool_loom_builder(self):
        """Mock execute_tool for LoomBuilder."""
        def mock_execute(tool_name, tool_args):
            if tool_name == "select_agents_for_phase":
                return {
                    "selected_agents": ["file-processor", "data-analyst"],
                    "agent_roles": {
                        "file-processor": "Handle CSV parsing",
                        "data-analyst": "Compute statistics"
                    },
                    "selection_rationale": "Specialized agents"
                }
            elif tool_name == "configure_agent":
                return {
                    "model_id": "Qwen3.5-35B-A3B-GGUF",
                    "tools": ["file_read", "statistics"],
                    "prompt_additions": "Focus on accurate processing",
                    "parameters": {"max_steps": 10}
                }
            elif tool_name == "build_execution_graph":
                return {
                    "nodes": [
                        {"id": "file-processor", "type": "agent", "order": 0},
                        {"id": "data-analyst", "type": "agent", "order": 1}
                    ],
                    "edges": [
                        {"from": "file-processor", "to": "data-analyst", "condition": "on_success"}
                    ],
                    "entry_point": "file-processor",
                    "exit_point": "data-analyst"
                }
            elif tool_name == "bind_components":
                return {
                    "read_components": ["knowledge/data-processing.md"],
                    "write_components": ["tasks/analysis-task.md"],
                    "templates": ["checklists/data-validation.md"]
                }
            elif tool_name == "identify_agent_gaps":
                return {
                    "available_agents": ["file-processor", "data-analyst"],
                    "missing_agents": [],
                    "generation_needed": []
                }
            return {}
        return mock_execute

    @pytest.fixture
    def mock_execute_tool_pipeline_executor(self):
        """Mock execute_tool for PipelineExecutor."""
        def mock_execute(tool_name, tool_args):
            if tool_name == "execute_agent_sequence":
                return {
                    "success": True,
                    "results": [
                        {"agent_id": "file-processor", "status": "success", "output": "Data loaded"},
                        {"agent_id": "data-analyst", "status": "success", "output": "Analysis complete"}
                    ],
                    "failed_agents": []
                }
            elif tool_name == "monitor_execution_health":
                return {
                    "status": "healthy",
                    "success_rate": 1.0,
                    "active_agents": 1,
                    "completed_steps": 2,
                    "pending_steps": 0
                }
            elif tool_name == "perform_adaptive_reroute":
                return {
                    "alternative_agents": ["backup-agent"],
                    "modified_graph": {"nodes": [], "edges": []},
                    "recovery_strategy": "Use backup agent"
                }
            elif tool_name == "collect_artifacts":
                return {
                    "artifacts": [{"type": "agent_output", "content": "Results"}],
                    "summary": "Collected 1 artifacts"
                }
            elif tool_name == "detect_completion":
                return {
                    "is_complete": True,
                    "completion_percentage": 100.0,
                    "remaining_nodes": [],
                    "final_output": "Pipeline execution complete"
                }
            return {}
        return mock_execute

    def test_stage1_domain_analyzer(self, sample_task_description, mock_execute_tool_domain_analyzer):
        """
        INTEGRATION-001.1: Domain Analyzer produces valid blueprint.
        """
        from gaia.pipeline.stages.domain_analyzer import DomainAnalyzer

        # Create instance
        analyzer = DomainAnalyzer(model_id='test-model', debug=False, max_steps=5)
        analyzer.execute_tool = Mock(side_effect=mock_execute_tool_domain_analyzer)

        # Execute Stage 1
        blueprint = analyzer.analyze(sample_task_description)

        # Validate output structure
        assert "primary_domain" in blueprint
        assert "secondary_domains" in blueprint
        assert "domain_requirements" in blueprint
        assert "domain_constraints" in blueprint
        assert "cross_domain_dependencies" in blueprint
        assert "complexity_score" in blueprint
        assert "confidence_score" in blueprint
        assert "reasoning" in blueprint

        # Validate content
        assert blueprint["primary_domain"] == "Data Analysis"
        assert len(blueprint["secondary_domains"]) >= 1
        assert isinstance(blueprint["complexity_score"], float)
        assert 0.0 <= blueprint["complexity_score"] <= 1.0

    def test_stage2_workflow_modeler(self, mock_execute_tool_workflow_modeler):
        """
        INTEGRATION-001.2: Workflow Modeler produces valid workflow model.
        """
        from gaia.pipeline.stages.workflow_modeler import WorkflowModeler

        # Sample domain blueprint from Stage 1
        domain_blueprint = {
            "primary_domain": "Data Analysis",
            "secondary_domains": ["File Processing", "Statistics"],
            "complexity_score": 0.6
        }

        # Create instance
        modeler = WorkflowModeler(model_id='test-model', debug=False, max_steps=5)
        modeler.execute_tool = Mock(side_effect=mock_execute_tool_workflow_modeler)

        # Execute Stage 2
        workflow_model = modeler.model_workflow(domain_blueprint)

        # Validate output structure
        assert "workflow_pattern" in workflow_model
        assert "phases" in workflow_model
        assert "milestones" in workflow_model
        assert "complexity_score" in workflow_model
        assert "recommended_agents" in workflow_model
        assert "reasoning" in workflow_model

        # Validate content
        assert workflow_model["workflow_pattern"] in ["waterfall", "agile", "spiral", "v-model", "pipeline", "iterative"]
        assert len(workflow_model["phases"]) >= 1
        assert len(workflow_model["recommended_agents"]) >= 1

    def test_stage3_loom_builder(self, mock_execute_tool_loom_builder):
        """
        INTEGRATION-001.3: Loom Builder produces valid loom topology.
        """
        from gaia.pipeline.stages.loom_builder import LoomBuilder

        # Sample workflow model from Stage 2
        workflow_model = {
            "workflow_pattern": "pipeline",
            "phases": [
                {"name": "Data Ingestion", "objectives": ["Load CSV"]},
                {"name": "Analysis", "objectives": ["Compute statistics"]}
            ],
            "recommended_agents": ["file-processor", "data-analyst"]
        }

        # Create instance
        builder = LoomBuilder(model_id='test-model', debug=False, max_steps=5)
        builder.execute_tool = Mock(side_effect=mock_execute_tool_loom_builder)

        # Execute Stage 3
        domain_blueprint = {"primary_domain": "Data Analysis"}
        loom_topology = builder.build_loom(workflow_model, domain_blueprint)

        # Validate output structure
        assert "execution_graph" in loom_topology
        assert "agent_sequence" in loom_topology
        assert "component_bindings" in loom_topology
        assert "agent_configurations" in loom_topology
        assert "gaps_identified" in loom_topology
        assert "reasoning" in loom_topology

        # Validate execution graph
        execution_graph = loom_topology["execution_graph"]
        assert "nodes" in execution_graph
        assert "edges" in execution_graph
        assert len(execution_graph["nodes"]) >= 1

    def test_stage4_pipeline_executor(self, mock_execute_tool_pipeline_executor):
        """
        INTEGRATION-001.4: Pipeline Executor executes loom topology.
        """
        from gaia.pipeline.stages.pipeline_executor import PipelineExecutor

        # Sample loom topology from Stage 3
        loom_topology = {
            "agent_sequence": ["file-processor", "data-analyst"],
            "execution_graph": {
                "nodes": [
                    {"id": "file-processor", "type": "agent", "order": 0},
                    {"id": "data-analyst", "type": "agent", "order": 1}
                ],
                "edges": [
                    {"from": "file-processor", "to": "data-analyst", "condition": "on_success"}
                ],
                "entry_point": "file-processor",
                "exit_point": "data-analyst"
            }
        }

        # Create instance
        executor = PipelineExecutor(model_id='test-model', debug=False, max_steps=5)
        executor.execute_tool = Mock(side_effect=mock_execute_tool_pipeline_executor)

        # Execute Stage 4
        domain_blueprint = {"primary_domain": "Data Analysis"}
        result = executor.execute_pipeline(loom_topology, domain_blueprint)

        # Validate output structure
        assert "execution_status" in result
        assert "artifacts_produced" in result
        assert "components_updated" in result
        assert "execution_metrics" in result
        assert "execution_history" in result
        assert "health_status" in result
        assert "completion_status" in result
        assert "final_output" in result

        # Validate health status
        health = result["health_status"]
        assert "status" in health
        assert health["status"] in ["healthy", "degraded", "critical"]

        # Validate completion status
        completion = result["completion_status"]
        assert "is_complete" in completion
        assert completion["is_complete"] == True

    def test_full_pipeline_integration(self, sample_task_description,
                                       mock_execute_tool_domain_analyzer,
                                       mock_execute_tool_workflow_modeler,
                                       mock_execute_tool_loom_builder,
                                       mock_execute_tool_pipeline_executor):
        """
        INTEGRATION-001: Complete end-to-end pipeline execution.

        This test validates the FULL pipeline:
        Task Description → DomainAnalyzer → WorkflowModeler →
        LoomBuilder → PipelineExecutor → Execution Result
        """
        start_time = time.time()

        # Stage 1: Domain Analysis
        from gaia.pipeline.stages.domain_analyzer import DomainAnalyzer
        analyzer = DomainAnalyzer(model_id='test-model', debug=False, max_steps=5)
        analyzer.execute_tool = Mock(side_effect=mock_execute_tool_domain_analyzer)
        domain_blueprint = analyzer.analyze(sample_task_description)
        assert domain_blueprint["primary_domain"] is not None

        # Stage 2: Workflow Modeling
        from gaia.pipeline.stages.workflow_modeler import WorkflowModeler
        modeler = WorkflowModeler(model_id='test-model', debug=False, max_steps=5)
        modeler.execute_tool = Mock(side_effect=mock_execute_tool_workflow_modeler)
        workflow_model = modeler.model_workflow(domain_blueprint)
        assert workflow_model["workflow_pattern"] is not None

        # Stage 3: Loom Building
        from gaia.pipeline.stages.loom_builder import LoomBuilder
        builder = LoomBuilder(model_id='test-model', debug=False, max_steps=5)
        builder.execute_tool = Mock(side_effect=mock_execute_tool_loom_builder)
        loom_topology = builder.build_loom(workflow_model, domain_blueprint)
        assert "execution_graph" in loom_topology

        # Stage 4: Pipeline Execution
        from gaia.pipeline.stages.pipeline_executor import PipelineExecutor
        executor = PipelineExecutor(model_id='test-model', debug=False, max_steps=5)
        executor.execute_tool = Mock(side_effect=mock_execute_tool_pipeline_executor)
        execution_result = executor.execute_pipeline(loom_topology, domain_blueprint)
        assert execution_result["execution_status"] is not None

        # Validate total execution time (should be fast with mocks)
        elapsed_time = time.time() - start_time
        assert elapsed_time < 5.0, f"Pipeline took too long: {elapsed_time:.2f}s"


class TestComponentFrameworkIntegration:
    """
    Test component framework integration throughout the pipeline.

    Validates:
    - Components loaded during pipeline execution
    - Components created/updated by agents
    - Component templates used correctly
    """

    def test_component_loader_integration(self):
        """
        INTEGRATION-002: Component framework accessible to agents.
        """
        from gaia.utils.component_loader import ComponentLoader, ComponentLoaderError
        from pathlib import Path

        # Initialize loader
        loader = ComponentLoader(framework_dir=Path("component-framework"))

        # Test loading a template
        component = loader.load_component("templates/agent-definition.md")
        assert component is not None
        assert "frontmatter" in component
        assert "content" in component
        assert component["frontmatter"]["template_type"] == "templates"

        # Test listing components
        all_components = loader.list_components()
        assert len(all_components) > 0

        templates = loader.list_components("templates")
        assert len(templates) >= 3  # At least the 3 base templates

        # Test validation
        errors = loader.validate_component("templates/agent-definition.md")
        assert len(errors) == 0, f"Validation errors: {errors}"

    def test_component_framework_structure(self):
        """
        Validate component-framework directory structure.
        """
        from pathlib import Path

        framework_dir = Path("component-framework")
        assert framework_dir.exists(), "component-framework directory not found"

        expected_dirs = [
            "memory",
            "knowledge",
            "tasks",
            "commands",
            "documents",
            "checklists",
            "personas",
            "workflows",
            "templates"
        ]

        for dir_name in expected_dirs:
            dir_path = framework_dir / dir_name
            assert dir_path.exists(), f"Missing directory: {dir_name}"
            assert dir_path.is_dir(), f"Not a directory: {dir_name}"

            # Each directory should have at least one .md file
            md_files = list(dir_path.glob("*.md"))
            assert len(md_files) > 0, f"No .md files in {dir_name}/"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])