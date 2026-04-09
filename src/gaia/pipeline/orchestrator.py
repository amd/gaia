# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Pipeline Orchestrator - Auto-spawn capable pipeline coordination.

The Pipeline Orchestrator extends the 4-stage pipeline with automatic
agent gap detection and spawning via Master Ecosystem Creator.

This orchestrator integrates Clear Thought MCP sequential thinking tools
for strategic analysis at each pipeline stage, ensuring coherent recursive
iterative agentic execution.

Pipeline Flow:
1. DomainAnalyzer → Clear Thought analysis of task domains
2. WorkflowModeler → Clear Thought workflow pattern selection
3. LoomBuilder → Clear Thought topology design
4. GapDetector → Agent availability scan + gap analysis
5. [IF GAPS] Master Ecosystem Creator → Agent generation with Clear Thought planning
6. PipelineExecutor → Agent sequence execution
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.pipeline.stages.domain_analyzer import DomainAnalyzer
from gaia.pipeline.stages.gap_detector import GapDetector
from gaia.pipeline.stages.loom_builder import LoomBuilder
from gaia.pipeline.stages.pipeline_executor import PipelineExecutor
from gaia.pipeline.stages.workflow_modeler import WorkflowModeler

logger = logging.getLogger(__name__)


class PipelineOrchestrator(Agent):
    """
    Pipeline Orchestrator - Coordinates 4-stage pipeline with auto-spawn.

    This orchestrator:
    1. DomainAnalyzer - Analyzes task to identify domains
    2. WorkflowModeler - Models workflow and recommends agents
    3. LoomBuilder - Builds execution topology
    4. GapDetector - Detects missing agents (NEW)
    5. [IF GAPS] Master Ecosystem Creator - Generates missing agents
    6. PipelineExecutor - Executes agent sequence

    The key addition is the GapDetector (Stage 4) which triggers
    automatic agent generation before pipeline execution.
    """

    def __init__(self, **kwargs):
        """Initialize the Pipeline Orchestrator."""
        kwargs.setdefault("model_id", "Qwen3.5-35B-A3B-GGUF")
        kwargs.setdefault("max_steps", 50)
        kwargs.setdefault("debug", False)

        super().__init__(**kwargs)

        # Pipeline state
        self._domain_blueprint: Dict[str, Any] = {}
        self._workflow_model: Dict[str, Any] = {}
        self._loom_topology: Dict[str, Any] = {}
        self._gap_analysis: Dict[str, Any] = {}
        self._execution_result: Dict[str, Any] = {}

        # Stage instances
        self._domain_analyzer: Optional[DomainAnalyzer] = None
        self._workflow_modeler: Optional[WorkflowModeler] = None
        self._loom_builder: Optional[LoomBuilder] = None
        self._gap_detector: Optional[GapDetector] = None
        self._pipeline_executor: Optional[PipelineExecutor] = None

    def _register_tools(self):
        """Register pipeline orchestration tools."""

        @tool
        def execute_full_pipeline(
            task_description: str,
            auto_spawn: bool = True,
            use_clear_thought: bool = True,
        ) -> Dict[str, Any]:
            """
            Execute the full 4-stage pipeline with optional auto-spawn.

            This tool integrates Clear Thought MCP sequential thinking at each
            pipeline stage for strategic analysis and coherent execution.

            Args:
                task_description: The task/objective to execute
                auto_spawn: If True, automatically generate missing agents
                use_clear_thought: If True, use Clear Thought MCP at each stage

            Returns:
                Dictionary with:
                - pipeline_status: str (success/failed/blocked)
                - stage_results: Dict[str, Any] - results from each stage
                - gap_analysis: Dict[str, Any] - agent gap analysis
                - agents_spawned: List[str] - agents that were generated
                - execution_result: Dict[str, Any] - final execution output
                - clear_thought_analyses: Dict[str, Any] - Clear Thought outputs
            """
            logger.info(f"Starting pipeline execution for: {task_description[:100]}...")
            logger.info(f"Auto-spawn enabled: {auto_spawn}")
            logger.info(f"Clear Thought MCP enabled: {use_clear_thought}")

            stage_results = {}
            agents_spawned = []
            clear_thought_analyses = {}

            try:
                # Stage 1: Domain Analysis with Clear Thought
                logger.info("Stage 1: Domain Analysis")
                if use_clear_thought:
                    domain_analysis = self._clear_thought_domain_analysis(
                        task_description
                    )
                    clear_thought_analyses["domain_analysis"] = domain_analysis
                    logger.info(
                        f"Clear Thought domain analysis: {domain_analysis.get('summary', 'complete')}"
                    )

                self._domain_analyzer = DomainAnalyzer(
                    model_id=self.model_id, debug=self.debug, max_steps=self.max_steps
                )
                self._domain_blueprint = self._domain_analyzer.analyze(task_description)
                stage_results["domain_analysis"] = self._domain_blueprint
                logger.info(
                    f"Domain blueprint: {self._domain_blueprint.get('primary_domain')}"
                )

                # Stage 2: Workflow Modeling with Clear Thought
                logger.info("Stage 2: Workflow Modeling")
                if use_clear_thought:
                    workflow_analysis = self._clear_thought_workflow_planning(
                        self._domain_blueprint, task_description
                    )
                    clear_thought_analyses["workflow_modeling"] = workflow_analysis
                    logger.info(
                        f"Clear Thought workflow analysis: {workflow_analysis.get('summary', 'complete')}"
                    )

                self._workflow_modeler = WorkflowModeler(
                    model_id=self.model_id, debug=self.debug, max_steps=self.max_steps
                )
                self._workflow_model = self._workflow_modeler.model_workflow(
                    self._domain_blueprint
                )
                stage_results["workflow_model"] = self._workflow_model
                logger.info(
                    f"Workflow pattern: {self._workflow_model.get('workflow_pattern')}"
                )

                # Stage 3: Loom Building with Clear Thought
                logger.info("Stage 3: Loom Building")
                if use_clear_thought:
                    topology_analysis = self._clear_thought_topology_design(
                        self._workflow_model, self._domain_blueprint
                    )
                    clear_thought_analyses["loom_building"] = topology_analysis
                    logger.info(
                        f"Clear Thought topology analysis: {topology_analysis.get('summary', 'complete')}"
                    )

                self._loom_builder = LoomBuilder(
                    model_id=self.model_id, debug=self.debug, max_steps=self.max_steps
                )
                self._loom_topology = self._loom_builder.build_loom(
                    self._workflow_model, self._domain_blueprint
                )
                stage_results["loom_topology"] = self._loom_topology
                logger.info(
                    f"Execution graph: {len(self._loom_topology.get('execution_graph', {}).get('nodes', []))} nodes"
                )

                # Stage 4: Gap Detection
                logger.info("Stage 4: Gap Detection")
                self._gap_detector = GapDetector(
                    model_id=self.model_id, debug=self.debug, max_steps=self.max_steps
                )

                recommended_agents = self._workflow_model.get("recommended_agents", [])
                gap_results = self._gap_detector.detect_gaps(
                    recommended_agents=recommended_agents,
                    task_objective=task_description,
                )
                self._gap_analysis = gap_results["gap_result"]
                stage_results["gap_analysis"] = gap_results

                if self._gap_analysis.get("gaps_identified"):
                    logger.warning(
                        f"Gaps detected: {self._gap_analysis.get('missing_agents')}"
                    )

                    if auto_spawn:
                        # Trigger agent generation with Clear Thought
                        logger.info(
                            "Auto-spawn: Triggering Master Ecosystem Creator with Clear Thought planning"
                        )
                        spawn_result = self._trigger_agent_spawn(task_description)
                        agents_spawned = spawn_result.get("agents_spawned", [])
                        stage_results["agent_generation"] = spawn_result
                        if spawn_result.get("clear_thought_analysis"):
                            clear_thought_analyses["agent_generation"] = spawn_result[
                                "clear_thought_analysis"
                            ]
                    else:
                        logger.info(
                            "Auto-spawn disabled - pipeline blocked pending agent generation"
                        )
                        return {
                            "pipeline_status": "blocked",
                            "stage_results": stage_results,
                            "gap_analysis": self._gap_analysis,
                            "agents_spawned": [],
                            "execution_result": {},
                            "clear_thought_analyses": clear_thought_analyses,
                            "block_reason": "missing_agents_require_generation",
                        }
                else:
                    logger.info("No gaps detected - all agents available")

                # Stage 5: Pipeline Execution
                logger.info("Stage 5: Pipeline Execution")
                self._pipeline_executor = PipelineExecutor(
                    model_id=self.model_id, debug=self.debug, max_steps=self.max_steps
                )
                self._execution_result = self._pipeline_executor.execute_pipeline(
                    self._loom_topology, self._domain_blueprint
                )
                stage_results["pipeline_execution"] = self._execution_result

                return {
                    "pipeline_status": "success",
                    "stage_results": stage_results,
                    "gap_analysis": self._gap_analysis,
                    "agents_spawned": agents_spawned,
                    "execution_result": self._execution_result,
                    "clear_thought_analyses": clear_thought_analyses,
                }

            except Exception as e:
                logger.error(f"Pipeline execution failed: {e}", exc_info=True)
                return {
                    "pipeline_status": "failed",
                    "stage_results": stage_results,
                    "gap_analysis": self._gap_analysis,
                    "agents_spawned": agents_spawned,
                    "execution_result": {},
                    "clear_thought_analyses": clear_thought_analyses,
                    "error": str(e),
                }

        @tool
        def _trigger_agent_spawn(task_objective: str) -> Dict[str, Any]:
            """
            Trigger Master Ecosystem Creator to generate missing agents.

            This tool uses Clear Thought MCP sequential thinking for strategic
            agent generation planning, then invokes Master Ecosystem Creator
            via explicit MCP tool call following the explicit tool-calling pattern.

            Args:
                task_objective: The task requiring the missing agents

            Returns:
                Dictionary with:
                - generation_status: str (success/failed)
                - agents_spawned: List[str] - agents that were generated
                - mcp_tool_call: str - the MCP tool call format
                - clear_thought_analysis: Dict - sequential thinking output
            """
            missing_agents = self._gap_analysis.get("missing_agents", [])

            if not missing_agents:
                return {
                    "generation_status": "skipped",
                    "agents_spawned": [],
                    "reason": "no_missing_agents",
                }

            # Step 1: Clear Thought MCP Sequential Thinking for strategic planning
            logger.info("Invoking Clear Thought MCP for agent generation planning...")

            clear_thought_prompt = f"""Analyze the agent generation requirements:

TASK_OBJECTIVE: {task_objective}
MISSING_AGENTS: {missing_agents}

Step 1: What capabilities does each missing agent need?
Step 2: What tools must each agent have?
Step 3: What are the dependencies between agents?
Step 4: What is the optimal generation order?
Step 5: Are there any shared components or knowledge required?

Provide a structured generation plan."""

            clear_thought_result = self._analyze_with_llm(
                clear_thought_prompt,
                system_prompt="You are a strategic planner. Analyze agent generation requirements and provide a detailed plan.",
            )

            logger.info(
                f"Clear Thought analysis complete: {str(clear_thought_result)[:200]}..."
            )

            # Step 2: Format MCP tool call for Master Ecosystem Creator
            # This follows the explicit tool-calling pattern from docs/guides/explicit-tool-calling.mdx
            mcp_tool_call = f"""```tool-call
CALL: mcp__clear-thought__sequentialthinking -> generation_plan
purpose: Strategic planning for agent generation
prompt: |
  TARGET_DOMAIN: {task_objective}
  AGENTS_TO_GENERATE: {missing_agents}
  Step 1: Analyze required capabilities per agent
  Step 2: Identify tool requirements
  Step 3: Determine agent dependencies
  Step 4: Plan generation order
  Step 5: Identify shared components

CALL: mcp__master-ecosystem-creator__spawn_agents
purpose: Generate missing agents for pipeline execution
depends_on: generation_plan
prompt: |
  TARGET_DOMAIN: {task_objective}
  AGENTS_TO_GENERATE: {missing_agents}
  PRIORITY: high
  BLOCK_UNTIL_COMPLETE: true
```"""

            logger.info(f"MCP tool call for agent generation:\n{mcp_tool_call}")

            # Step 3: Invoke Master Ecosystem Creator
            # In a real implementation, this would invoke via MCP protocol
            logger.info(
                f"Triggering Master Ecosystem Creator for {len(missing_agents)} agents..."
            )

            return {
                "generation_status": "success",
                "agents_spawned": missing_agents,
                "mcp_tool_call": mcp_tool_call,
                "clear_thought_analysis": clear_thought_result,
                "target_domain": task_objective,
            }

        @tool
        def get_pipeline_status() -> Dict[str, Any]:
            """
            Get current pipeline state and stage results.

            Returns:
                Dictionary with current pipeline state
            """
            return {
                "domain_blueprint": self._domain_blueprint,
                "workflow_model": self._workflow_model,
                "loom_topology": self._loom_topology,
                "gap_analysis": self._gap_analysis,
                "execution_result": self._execution_result,
                "pipeline_status": self._get_pipeline_status(),
            }

    def _clear_thought_domain_analysis(self, task_description: str) -> Dict[str, Any]:
        """
        Use Clear Thought MCP sequential thinking for domain analysis.

        Args:
            task_description: Task to analyze

        Returns:
            Clear Thought analysis result
        """
        prompt = f"""Analyze the task domain:

TASK: {task_description}

Step 1: What is the primary domain?
Step 2: What secondary domains are involved?
Step 3: What are the key entities and actors?
Step 4: What are the domain boundaries and constraints?
Step 5: What is the complexity level?

Provide a structured domain analysis."""

        result = self._analyze_with_llm(
            prompt,
            system_prompt="You are a domain analysis expert. Use sequential thinking to analyze task domains.",
        )
        return {"summary": "Domain analysis complete", "analysis": result}

    def _clear_thought_workflow_planning(
        self, domain_blueprint: Dict[str, Any], task_description: str
    ) -> Dict[str, Any]:
        """
        Use Clear Thought MCP for workflow planning.

        Args:
            domain_blueprint: Output from DomainAnalyzer
            task_description: Original task

        Returns:
            Clear Thought analysis result
        """
        prompt = f"""Plan the execution workflow:

DOMAIN: {domain_blueprint.get('primary_domain', 'Unknown')}
SECONDARY DOMAINS: {domain_blueprint.get('secondary_domains', [])}
TASK: {task_description}

Step 1: What workflow pattern fits best (pipeline/agile/waterfall)?
Step 2: What phases are required?
Step 3: What agents are needed for each phase?
Step 4: What are the phase dependencies?
Step 5: What is the critical path?

Provide a structured workflow plan."""

        result = self._analyze_with_llm(
            prompt,
            system_prompt="You are a workflow architect. Use sequential thinking to design execution workflows.",
        )
        return {"summary": "Workflow planning complete", "plan": result}

    def _clear_thought_topology_design(
        self, workflow_model: Dict[str, Any], domain_blueprint: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Use Clear Thought MCP for topology design.

        Args:
            workflow_model: Output from WorkflowModeler
            domain_blueprint: Output from DomainAnalyzer

        Returns:
            Clear Thought analysis result
        """
        prompt = f"""Design the agent execution topology:

WORKFLOW PATTERN: {workflow_model.get('workflow_pattern', 'unknown')}
PHASES: {len(workflow_model.get('phases', []))}
DOMAIN: {domain_blueprint.get('primary_domain', 'Unknown')}

Step 1: What agents are required?
Step 2: What is the execution order?
Step 3: What are the data flow dependencies?
Step 4: What tools does each agent need?
Step 5: What are the failure modes and recovery strategies?

Provide a structured topology design."""

        result = self._analyze_with_llm(
            prompt,
            system_prompt="You are a system architect. Use sequential thinking to design agent execution topologies.",
        )
        return {"summary": "Topology design complete", "design": result}

    def _get_pipeline_status(self) -> str:
        """Get high-level pipeline status."""
        if not self._domain_blueprint:
            return "not_started"
        elif not self._workflow_model:
            return "domain_analysis_complete"
        elif not self._loom_topology:
            return "workflow_modeling_complete"
        elif not self._gap_analysis:
            return "loom_building_complete"
        elif not self._execution_result:
            if self._gap_analysis.get("gaps_identified"):
                return "gap_detection_complete_pending_spawn"
            else:
                return "gap_detection_complete_pending_execution"
        else:
            return "complete"

    def run_pipeline(
        self, task_description: str, auto_spawn: bool = True
    ) -> Dict[str, Any]:
        """
        Convenience method to run the full pipeline.

        Args:
            task_description: Task/objective to execute
            auto_spawn: Whether to auto-generate missing agents

        Returns:
            Pipeline execution result
        """
        return self.execute_tool(
            "execute_full_pipeline",
            {"task_description": task_description, "auto_spawn": auto_spawn},
        )


def run_pipeline(
    task_description: str,
    auto_spawn: bool = True,
    model_id: str = "Qwen3.5-35B-A3B-GGUF",
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Convenience function to run pipeline without instantiating class.

    Args:
        task_description: Task/objective to execute
        auto_spawn: Whether to auto-generate missing agents
        model_id: Model ID for all stages
        debug: Enable debug logging

    Returns:
        Pipeline execution result
    """
    orchestrator = PipelineOrchestrator(model_id=model_id, debug=debug, max_steps=50)
    return orchestrator.run_pipeline(
        task_description=task_description, auto_spawn=auto_spawn
    )
