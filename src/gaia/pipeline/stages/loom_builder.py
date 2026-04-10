# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Loom Builder - Stage 3 of the GAIA multi-stage pipeline.

The Loom Builder takes workflow models and constructs agent execution graphs
with agent configurations, tool bindings, and component framework integration.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


class LoomBuilder(Agent):
    """
    Loom Builder Agent - Stage 3 of the multi-stage pipeline.

    This agent takes workflow models and:
    1. Selects agents from registry for each phase
    2. Configures agents with domain-specific parameters
    3. Builds execution graph with dependencies
    4. Binds component templates to agents
    5. Produces loom topology for Pipeline Executor

    The output loom topology is consumed by Pipeline Executor (Stage 4).
    """

    def __init__(self, **kwargs):
        """Initialize the Loom Builder agent."""
        kwargs.setdefault("model_id", "Qwen3.5-35B-A3B-GGUF")
        kwargs.setdefault("max_steps", 15)
        kwargs.setdefault("debug", False)

        super().__init__(**kwargs)

        # Loom builder state
        self._execution_graph: Dict[str, Any] = {"nodes": [], "edges": []}
        self._agent_sequence: List[str] = []
        self._component_bindings: Dict[str, List[str]] = {}
        self._agent_configurations: Dict[str, Dict[str, Any]] = {}

    def _register_tools(self):
        """Register loom building tools."""

        @tool
        def select_agents_for_phase(
            phase: Dict[str, Any], workflow_pattern: str
        ) -> Dict[str, Any]:
            """
            Select appropriate agents for a workflow phase.

            Args:
                phase: Phase definition from Workflow Modeler
                workflow_pattern: Selected workflow pattern

            Returns:
                Dictionary with:
                - selected_agents: List[str]
                - agent_roles: Dict[str, str]
                - selection_rationale: str
            """
            agents_result = self._analyze_with_llm(
                f"Select agents for phase: {phase.get('name', 'Phase')}, "
                f"objectives: {phase.get('objectives', [])}, "
                f"workflow: {workflow_pattern}",
                system_prompt="""Select agents for phase execution.
Return JSON:
{
  "selected_agents": ["agent1", "agent2"],
  "agent_roles": {"agent1": "role1", "agent2": "role2"},
  "selection_rationale": "why these agents"
}""",
            )

            logger.info(
                f"Selected agents for {phase.get('name')}: {agents_result.get('selected_agents', [])}"
            )
            return agents_result

        @tool
        def configure_agent(agent_id: str, phase: Dict[str, Any]) -> Dict[str, Any]:
            """
            Configure an agent for phase execution.

            Args:
                agent_id: Agent identifier
                phase: Phase definition

            Returns:
                Agent configuration with:
                - model_id: str
                - tools: List[str]
                - prompt_additions: str
                - parameters: Dict[str, Any]
            """
            config_result = self._analyze_with_llm(
                f"Configure agent {agent_id} for phase: {phase.get('name')}",
                system_prompt="""Configure agent for phase.
Return JSON:
{
  "model_id": "Qwen3.5-35B-A3B-GGUF",
  "tools": ["tool1", "tool2"],
  "prompt_additions": "phase-specific instructions",
  "parameters": {"max_steps": 10, "temperature": 0.7}
}""",
            )

            self._agent_configurations[agent_id] = config_result
            logger.info(f"Configured agent {agent_id}")
            return config_result

        @tool
        def build_execution_graph(agent_sequence: List[str]) -> Dict[str, Any]:
            """
            Build execution graph from agent sequence.

            Args:
                agent_sequence: Ordered list of agent IDs

            Returns:
                Execution graph with:
                - nodes: List[Dict]
                - edges: List[Dict]
                - entry_point: str
                - exit_point: str
            """
            nodes = []
            edges = []

            for i, agent_id in enumerate(agent_sequence):
                nodes.append({"id": agent_id, "type": "agent", "order": i})

                if i > 0:
                    edges.append(
                        {
                            "from": agent_sequence[i - 1],
                            "to": agent_id,
                            "condition": "on_success",
                        }
                    )

            self._execution_graph = {
                "nodes": nodes,
                "edges": edges,
                "entry_point": agent_sequence[0] if agent_sequence else None,
                "exit_point": agent_sequence[-1] if agent_sequence else None,
            }

            logger.info(
                f"Built execution graph with {len(nodes)} nodes, {len(edges)} edges"
            )
            return self._execution_graph

        @tool
        def bind_components(agent_id: str, phase: Dict[str, Any]) -> Dict[str, Any]:
            """
            Bind component templates to an agent.

            Args:
                agent_id: Agent identifier
                phase: Phase definition

            Returns:
                Component bindings with:
                - read_components: List[str]
                - write_components: List[str]
                - templates: List[str]
            """
            bindings_result = self._analyze_with_llm(
                f"Bind components for agent {agent_id} in phase: {phase.get('name')}",
                system_prompt="""Bind component templates to agent.
Return JSON:
{
  "read_components": ["knowledge/domain-knowledge.md"],
  "write_components": ["tasks/task-tracking.md"],
  "templates": ["checklists/phase-checklist.md"]
}""",
            )

            if agent_id not in self._component_bindings:
                self._component_bindings[agent_id] = []
            self._component_bindings[agent_id].extend(
                bindings_result.get("read_components", [])
                + bindings_result.get("write_components", [])
            )

            logger.info(
                f"Bound {len(bindings_result.get('read_components', []))} components to {agent_id}"
            )
            return bindings_result

        @tool
        def identify_agent_gaps(recommended_agents: List[str]) -> Dict[str, Any]:
            """
            Identify gaps between recommended agents and available agents.

            Args:
                recommended_agents: List of recommended agent IDs

            Returns:
                Gap analysis with:
                - available_agents: List[str]
                - missing_agents: List[str]
                - generation_needed: List[str]
            """
            # Get available agents from registry
            from gaia.agents.base.tools import _TOOL_REGISTRY

            available = list(_TOOL_REGISTRY.keys())

            gaps_result = self._analyze_with_llm(
                f"Compare recommended: {recommended_agents} vs available: {available}",
                system_prompt="""Identify agent gaps.
Return JSON:
{
  "available_agents": ["agent1"],
  "missing_agents": ["agent2"],
  "generation_needed": ["agent2"]
}""",
            )

            logger.info(
                f"Identified {len(gaps_result.get('missing_agents', []))} agent gaps"
            )
            return gaps_result

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
        def save_loom_topology(
            artifact_name: str, _loom_topology: Dict[str, Any]
        ) -> str:
            """
            Save loom topology as an artifact.

            Args:
                artifact_name: Name for the artifact
                _loom_topology: Loom topology dictionary

            Returns:
                Path to saved artifact
            """
            content = f"# Loom Topology: {artifact_name}\n\n"
            content += "## Execution Graph\n\n"
            content += (
                f"Entry Point: {self._execution_graph.get('entry_point', 'N/A')}\n"
            )
            content += (
                f"Exit Point: {self._execution_graph.get('exit_point', 'N/A')}\n\n"
            )

            content += "### Nodes\n\n"
            for node in self._execution_graph.get("nodes", []):
                content += f"- {node['id']} (order: {node.get('order', 0)})\n"

            content += "\n### Edges\n\n"
            for edge in self._execution_graph.get("edges", []):
                content += f"- {edge['from']} -> {edge['to']} ({edge.get('condition', 'on_success')})\n"

            content += "\n### Agent Sequence\n\n"
            for i, agent_id in enumerate(self._agent_sequence):
                content += f"{i+1}. {agent_id}\n"

            content += "\n### Component Bindings\n\n"
            for agent_id, components in self._component_bindings.items():
                content += f"**{agent_id}**: {len(components)} components\n"

            component_path = (
                f"documents/loom-{artifact_name.lower().replace(' ', '-')}.md"
            )
            frontmatter = {
                "template_id": f"loom-{artifact_name.lower().replace(' ', '-')}",
                "template_type": "documents",
                "version": "1.0.0",
                "description": f"Loom topology for {artifact_name}",
                "nodes_count": len(self._execution_graph.get("nodes", [])),
                "edges_count": len(self._execution_graph.get("edges", [])),
                "agents_count": len(self._agent_sequence),
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

    def build_loom(
        self,
        workflow_model: Dict[str, Any],
        _domain_blueprint: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build loom topology from workflow model.

        Args:
            workflow_model: Output from Workflow Modeler
            domain_blueprint: Optional domain analysis for context

        Returns:
            Loom topology with:
            - execution_graph: Dict
            - agent_sequence: List[str]
            - component_bindings: Dict[str, List[str]]
            - agent_configurations: Dict[str, Dict]
            - gaps_identified: Dict
            - reasoning: str
        """
        logger.info(
            f"Building loom for workflow pattern: {workflow_model.get('workflow_pattern', 'unknown')}"
        )

        phases = workflow_model.get("phases", [])
        recommended_agents = workflow_model.get("recommended_agents", [])

        # Step 1: Select agents for each phase
        selected_agents = []
        for phase in phases:
            agents_result = self.execute_tool(
                "select_agents_for_phase",
                {
                    "phase": phase,
                    "workflow_pattern": workflow_model.get(
                        "workflow_pattern", "pipeline"
                    ),
                },
            )
            selected_agents.extend(agents_result.get("selected_agents", []))

        # Add recommended agents
        selected_agents.extend(recommended_agents)
        # Remove duplicates while preserving order
        self._agent_sequence = list(dict.fromkeys(selected_agents))

        # Step 2: Configure agents
        for agent_id in self._agent_sequence[:5]:  # Limit to 5 agents
            for phase in phases[:2]:  # Configure for first 2 phases
                self.execute_tool(
                    "configure_agent", {"agent_id": agent_id, "phase": phase}
                )

        # Step 3: Build execution graph
        self.execute_tool(
            "build_execution_graph", {"agent_sequence": self._agent_sequence}
        )

        # Step 4: Bind components
        for agent_id in self._agent_sequence[:3]:  # Bind for first 3 agents
            for phase in phases[:2]:
                self.execute_tool(
                    "bind_components", {"agent_id": agent_id, "phase": phase}
                )

        # Step 5: Identify gaps
        gaps = self.execute_tool(
            "identify_agent_gaps", {"recommended_agents": recommended_agents}
        )

        # Build loom topology
        loom_topology = {
            "execution_graph": self._execution_graph,
            "agent_sequence": self._agent_sequence,
            "component_bindings": self._component_bindings,
            "agent_configurations": self._agent_configurations,
            "gaps_identified": gaps,
            "reasoning": self._generate_reasoning(workflow_model),
        }

        logger.info(
            f"Loom building complete. Agents: {len(self._agent_sequence)}, "
            f"Graph nodes: {len(self._execution_graph['nodes'])}, "
            f"Component bindings: {sum(len(v) for v in self._component_bindings.values())}"
        )
        return loom_topology

    def _generate_reasoning(self, workflow_model: Dict[str, Any]) -> str:
        """Generate human-readable reasoning for the loom topology."""
        reasoning_parts = []

        if self._agent_sequence:
            reasoning_parts.append(
                f"Sequenced {len(self._agent_sequence)} agents for {workflow_model.get('workflow_pattern', 'workflow')} workflow."
            )

        if self._execution_graph.get("nodes"):
            reasoning_parts.append(
                f"Built execution graph with {len(self._execution_graph['nodes'])} nodes "
                f"and {len(self._execution_graph['edges'])} edges."
            )

        if self._component_bindings:
            total_bindings = sum(len(v) for v in self._component_bindings.values())
            reasoning_parts.append(
                f"Bound {total_bindings} component templates across {len(self._component_bindings)} agents."
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
