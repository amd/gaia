# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Workflow Modeler - Stage 2 of the GAIA multi-stage pipeline.

The Workflow Modeler takes domain analysis blueprints and generates
execution workflows with phases, milestones, and task dependencies.
"""

import json
import logging
from typing import Any, Dict, List

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


class WorkflowModeler(Agent):
    """
    Workflow Modeler Agent - Stage 2 of the multi-stage pipeline.

    This agent takes domain analysis blueprints and:
    1. Selects appropriate workflow patterns
    2. Defines execution phases with exit criteria
    3. Plans milestones with deliverables
    4. Estimates complexity and resource requirements
    5. Recommends agents for each phase

    The output workflow model is consumed by the Loom Builder (Stage 3).
    """

    def __init__(self, **kwargs):
        """Initialize the Workflow Modeler agent."""
        kwargs.setdefault("model_id", "Qwen3.5-35B-A3B-GGUF")
        kwargs.setdefault("max_steps", 15)
        kwargs.setdefault("debug", False)

        super().__init__(**kwargs)

        # Workflow modeling state
        self._workflow_pattern: str = ""
        self._phases: List[Dict[str, Any]] = []
        self._milestones: List[Dict[str, Any]] = []
        self._estimated_complexity: float = 0.0
        self._recommended_agents: List[str] = []

    def _register_tools(self):
        """Register workflow modeling tools."""

        @tool
        def select_workflow_pattern(domain_blueprint: Dict[str, Any]) -> Dict[str, Any]:
            """
            Select appropriate workflow pattern based on domain analysis.

            Args:
                domain_blueprint: Output from Domain Analyzer

            Returns:
                Dictionary with:
                - pattern: str (waterfall|agile|spiral|v-model|pipeline)
                - rationale: str
                - suitability_score: float (0.0-1.0)
            """
            pattern_result = self._analyze_with_llm(
                f"Select workflow pattern for domains: {domain_blueprint.get('primary_domain', 'unknown')}, "
                f"complexity: {domain_blueprint.get('complexity_score', 0.0)}",
                system_prompt="""Select the best workflow pattern.
Return JSON:
{
  "pattern": "waterfall|agile|spiral|v-model|pipeline|iterative",
  "rationale": "why this pattern fits",
  "suitability_score": 0.0-1.0,
  "alternative_patterns": ["other", "options"]
}""",
            )

            self._workflow_pattern = pattern_result.get("pattern", "pipeline")
            logger.info(f"Selected workflow pattern: {self._workflow_pattern}")
            return pattern_result

        @tool
        def define_phases(
            domain_blueprint: Dict[str, Any], workflow_pattern: str
        ) -> Dict[str, Any]:
            """
            Define execution phases for the workflow.

            Args:
                domain_blueprint: Output from Domain Analyzer
                workflow_pattern: Selected workflow pattern

            Returns:
                List of phases with:
                - name: str
                - objectives: List[str]
                - tasks: List[str]
                - exit_criteria: Dict[str, Any]
                - estimated_duration: str
            """
            phases_result = self._analyze_with_llm(
                f"Define phases for {workflow_pattern} workflow with domains: "
                f"{domain_blueprint.get('primary_domain', 'unknown')}",
                system_prompt="""Define workflow phases.
Return JSON:
{
  "phases": [
    {
      "name": "Phase 1",
      "objectives": ["obj1", "obj2"],
      "tasks": ["task1", "task2"],
      "exit_criteria": {"deliverable": "ready", "tests_passed": true},
      "estimated_duration": "2-3 days"
    }
  ]
}""",
            )

            self._phases = phases_result.get("phases", [])
            logger.info(f"Defined {len(self._phases)} phases")
            return phases_result

        @tool
        def plan_milestones(phases: List[Dict[str, Any]]) -> Dict[str, Any]:
            """
            Plan milestones based on phases.

            Args:
                phases: List of phase definitions

            Returns:
                List of milestones with:
                - name: str
                - phase: str
                - deliverables: List[str]
                - success_criteria: List[str]
            """
            milestones_result = self._analyze_with_llm(
                f"Plan milestones for {len(phases)} phases",
                system_prompt="""Plan project milestones.
Return JSON:
{
  "milestones": [
    {
      "name": "Milestone 1",
      "phase": "Phase 1",
      "deliverables": ["doc1", "code1"],
      "success_criteria": ["criteria1", "criteria2"]
    }
  ]
}""",
            )

            self._milestones = milestones_result.get("milestones", [])
            logger.info(f"Planned {len(self._milestones)} milestones")
            return milestones_result

        @tool
        def estimate_complexity(domain_blueprint: Dict[str, Any]) -> Dict[str, Any]:
            """
            Estimate overall workflow complexity.

            Args:
                domain_blueprint: Output from Domain Analyzer

            Returns:
                Dictionary with:
                - complexity_score: float (0.0-1.0)
                - complexity_factors: List[str]
                - resource_estimate: str
            """
            complexity_result = self._analyze_with_llm(
                f"Estimate complexity for domains: {domain_blueprint.get('secondary_domains', [])}, "
                f"dependencies: {len(domain_blueprint.get('cross_domain_dependencies', []))}",
                system_prompt="""Estimate workflow complexity.
Return JSON:
{
  "complexity_score": 0.0-1.0,
  "complexity_factors": ["factor1", "factor2"],
  "resource_estimate": "resource description",
  "risk_level": "low|medium|high"
}""",
            )

            self._estimated_complexity = complexity_result.get("complexity_score", 0.5)
            logger.info(f"Estimated complexity: {self._estimated_complexity}")
            return complexity_result

        @tool
        def recommend_agents(
            workflow_pattern: str, phases: List[Dict[str, Any]]
        ) -> Dict[str, Any]:
            """
            Recommend agents for workflow execution.

            Args:
                workflow_pattern: Selected workflow pattern
                phases: List of phase definitions

            Returns:
                Dictionary with:
                - recommended_agents: List[str]
                - agent_phase_mapping: Dict[str, List[str]]
                - rationale: str
            """
            agents_result = self._analyze_with_llm(
                f"Recommend agents for {workflow_pattern} workflow with {len(phases)} phases",
                system_prompt="""Recommend agents for workflow execution.
Return JSON:
{
  "recommended_agents": ["agent1", "agent2"],
  "agent_phase_mapping": {"Phase 1": ["agent1"], "Phase 2": ["agent2"]},
  "rationale": "why these agents are selected"
}""",
            )

            self._recommended_agents = agents_result.get("recommended_agents", [])
            logger.info(f"Recommended {len(self._recommended_agents)} agents")
            return agents_result

        @tool
        def load_component_template(component_path: str) -> Dict[str, Any]:
            """
            Load a component template from component-framework.

            Args:
                component_path: Path relative to component-framework/

            Returns:
                Component with frontmatter and content
            """
            return self.load_component(component_path)

        @tool
        def save_workflow_artifact(
            artifact_name: str, _workflow_model: Dict[str, Any]
        ) -> str:
            """
            Save workflow model as an artifact.

            Args:
                artifact_name: Name for the artifact
                workflow_model: Workflow model dictionary

            Returns:
                Path to saved artifact
            """
            content = f"# Workflow Model: {artifact_name}\n\n"
            content += f"## Pattern\n\n{self._workflow_pattern}\n\n"
            content += "## Phases\n\n"
            for phase in self._phases:
                content += f"### {phase.get('name', 'Phase')}\n"
                content += f"Objectives: {', '.join(phase.get('objectives', []))}\n"
                content += f"Tasks: {', '.join(phase.get('tasks', []))}\n\n"

            content += "## Milestones\n\n"
            for milestone in self._milestones:
                content += f"- **{milestone.get('name', 'Milestone')}**: "
                content += f"{', '.join(milestone.get('deliverables', []))}\n"

            component_path = (
                f"workflows/workflow-{artifact_name.lower().replace(' ', '-')}.md"
            )
            frontmatter = {
                "template_id": f"workflow-{artifact_name.lower().replace(' ', '-')}",
                "template_type": "workflows",
                "version": "1.0.0",
                "description": f"Workflow model for {artifact_name}",
                "pattern": self._workflow_pattern,
                "phases_count": len(self._phases),
                "milestones_count": len(self._milestones),
            }

            return self.save_component(component_path, content, frontmatter)

    def _analyze_with_llm(self, query: str, system_prompt: str) -> Dict[str, Any]:
        """Helper method to analyze with LLM and parse JSON response."""
        try:
            messages = [{"role": "user", "content": query}]
            response = self.chat.send_messages(messages, system_prompt=system_prompt)
            response_text = (
                response.text if hasattr(response, "text") else str(response)
            )

            if isinstance(response_text, str):
                import re

                json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group())
                else:
                    logger.warning(f"Could not extract JSON: {response_text[:200]}")
                    return {"raw_response": response_text}
            elif isinstance(response, dict):
                return response
            else:
                return {"raw_response": response_text}

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return {"error": f"JSON parse error: {e}"}
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return {"error": str(e)}

    def model_workflow(self, domain_blueprint: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a workflow model from domain analysis blueprint.

        Args:
            domain_blueprint: Output from Domain Analyzer stage

        Returns:
            Workflow model with:
            - workflow_pattern: str
            - phases: List[Dict]
            - milestones: List[Dict]
            - complexity_score: float
            - recommended_agents: List[str]
            - reasoning: str
        """
        logger.info(
            f"Starting workflow modeling for domain: {domain_blueprint.get('primary_domain', 'unknown')}"
        )

        # Step 1: Select workflow pattern
        self.execute_tool(
            "select_workflow_pattern", {"domain_blueprint": domain_blueprint}
        )

        # Step 2: Define phases
        self.execute_tool(
            "define_phases",
            {
                "domain_blueprint": domain_blueprint,
                "workflow_pattern": self._workflow_pattern,
            },
        )

        # Step 3: Plan milestones
        self.execute_tool("plan_milestones", {"phases": self._phases})

        # Step 4: Estimate complexity
        self.execute_tool("estimate_complexity", {"domain_blueprint": domain_blueprint})

        # Step 5: Recommend agents
        self.execute_tool(
            "recommend_agents",
            {"workflow_pattern": self._workflow_pattern, "phases": self._phases},
        )

        # Build workflow model
        workflow_model = {
            "workflow_pattern": self._workflow_pattern,
            "phases": self._phases,
            "milestones": self._milestones,
            "complexity_score": self._estimated_complexity,
            "recommended_agents": self._recommended_agents,
            "reasoning": self._generate_reasoning(domain_blueprint),
        }

        logger.info(
            f"Workflow modeling complete. Pattern: {self._workflow_pattern}, Complexity: {self._estimated_complexity:.2f}"
        )
        return workflow_model

    def _generate_reasoning(self, domain_blueprint: Dict[str, Any]) -> str:
        """Generate human-readable reasoning for the workflow model."""
        reasoning_parts = []

        if self._workflow_pattern:
            reasoning_parts.append(
                f"Selected {self._workflow_pattern} workflow pattern based on "
                f"domain: {domain_blueprint.get('primary_domain', 'unknown')}."
            )

        if self._phases:
            reasoning_parts.append(
                f"Defined {len(self._phases)} phases with {len(self._milestones)} milestones."
            )

        if self._recommended_agents:
            reasoning_parts.append(
                f"Recommended {len(self._recommended_agents)} agents for execution."
            )

        return " ".join(reasoning_parts)

    def execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a registered tool by name."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        if tool_name not in _TOOL_REGISTRY:
            logger.error(f"Tool {tool_name} not registered")
            return {"error": f"Tool {tool_name} not registered"}

        tool_fn = _TOOL_REGISTRY[tool_name]["function"]
        return tool_fn(**tool_args)
