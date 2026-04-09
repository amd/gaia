# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Pipeline Executor - Stage 5 of the GAIA multi-stage pipeline.

The Pipeline Executor takes loom topologies and executes the agent
orchestration with monitoring, adaptive rerouting, and completion detection.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


class PipelineExecutor(Agent):
    """
    Pipeline Executor Agent - Stage 5 of the multi-stage pipeline.

    This agent takes loom topologies and:
    1. Executes agent sequence according to execution graph
    2. Monitors execution with health checks
    3. Performs adaptive rerouting on failures
    4. Collects artifacts from each stage
    5. Detects completion and produces final output
    """

    def __init__(self, **kwargs):
        """Initialize the Pipeline Executor agent."""
        kwargs.setdefault("model_id", "Qwen3.5-35B-A3B-GGUF")
        kwargs.setdefault("max_steps", 20)
        kwargs.setdefault("debug", True)

        super().__init__(**kwargs)

        # Pipeline execution state
        self._execution_status = "pending"
        self._execution_metrics = {
            "start_time": None,
            "end_time": None,
            "duration_seconds": 0,
            "iterations": 0,
            "successful_steps": 0,
            "failed_steps": 0,
        }
        self._artifacts_produced: List[Dict[str, Any]] = []
        self._components_updated: List[str] = []
        self._execution_history: List[Dict[str, Any]] = []

    def _register_tools(self):
        """Register pipeline execution tools."""

        @tool
        def execute_agent_sequence(
            agent_sequence: List[str], loom_topology: Dict[str, Any]
        ) -> Dict[str, Any]:
            """
            Execute a sequence of agents according to loom topology.

            Args:
                agent_sequence: Ordered list of agent IDs
                loom_topology: Loom topology from Loom Builder

            Returns:
                Execution result with:
                - success: bool
                - results: List[Dict]
                - failed_agents: List[str]
            """
            results = []
            failed_agents = []

            for agent_id in agent_sequence:
                logger.info(f"Executing agent: {agent_id}")

                # Simulate agent execution (in real implementation, would invoke actual agent)
                agent_result = self._execute_agent_step(agent_id, loom_topology)
                results.append(agent_result)

                if agent_result.get("status") == "success":
                    self._execution_metrics["successful_steps"] += 1
                else:
                    self._execution_metrics["failed_steps"] += 1
                    failed_agents.append(agent_id)

            self._execution_history.append(
                {
                    "action": "execute_agent_sequence",
                    "agents_executed": len(agent_sequence),
                    "failed": len(failed_agents),
                }
            )

            return {
                "success": len(failed_agents) == 0,
                "results": results,
                "failed_agents": failed_agents,
            }

        @tool
        def monitor_execution_health() -> Dict[str, Any]:
            """
            Monitor health of pipeline execution.

            Returns:
                Health status with:
                - status: str (healthy|degraded|critical)
                - active_agents: int
                - completed_steps: int
                - pending_steps: int
                - errors: List[str]
            """
            # Calculate health metrics
            total_steps = (
                self._execution_metrics["successful_steps"]
                + self._execution_metrics["failed_steps"]
            )
            success_rate = self._execution_metrics["successful_steps"] / max(
                total_steps, 1
            )

            if success_rate >= 0.9:
                status = "healthy"
            elif success_rate >= 0.5:
                status = "degraded"
            else:
                status = "critical"

            health = {
                "status": status,
                "success_rate": round(success_rate, 2),
                "active_agents": 1,  # Current executor
                "completed_steps": self._execution_metrics["successful_steps"],
                "pending_steps": max(
                    0, total_steps - self._execution_metrics["successful_steps"]
                ),
                "errors": [],
            }

            logger.info(f"Execution health: {status}")
            return health

        @tool
        def perform_adaptive_reroute(
            failed_agent: str, _loom_topology: Dict[str, Any]
        ) -> Dict[str, Any]:
            """
            Perform adaptive rerouting when an agent fails.

            Args:
                failed_agent: ID of failed agent
                _loom_topology: Loom topology

            Returns:
                Reroute plan with:
                - alternative_agents: List[str]
                - modified_graph: Dict
                - recovery_strategy: str
            """
            reroute_result = self._analyze_with_llm(
                f"Agent {failed_agent} failed. Suggest rerouting strategy.",
                system_prompt="""Suggest adaptive rerouting for failed agent.
Return JSON:
{
  "alternative_agents": ["backup_agent1", "backup_agent2"],
  "modified_graph": {"nodes": [], "edges": []},
  "recovery_strategy": "description of recovery approach"
}""",
            )

            self._execution_history.append(
                {
                    "action": "adaptive_reroute",
                    "failed_agent": failed_agent,
                    "alternatives": reroute_result.get("alternative_agents", []),
                }
            )

            logger.info(
                f"Adaptive reroute for {failed_agent}: {reroute_result.get('recovery_strategy', 'N/A')}"
            )
            return reroute_result

        @tool
        def collect_artifacts(
            execution_results: List[Dict[str, Any]],
        ) -> Dict[str, Any]:
            """
            Collect artifacts from execution results.

            Args:
                execution_results: Results from agent execution

            Returns:
                Collected artifacts with:
                - artifacts: List[Dict]
                - summary: str
            """
            artifacts = []

            for result in execution_results:
                if isinstance(result, dict) and "output" in result:
                    artifacts.append(
                        {
                            "type": "agent_output",
                            "content": result["output"],
                            "timestamp": time.time(),
                        }
                    )
                    self._artifacts_produced.append(
                        {"type": "agent_output", "content": result["output"]}
                    )

            summary = f"Collected {len(artifacts)} artifacts from {len(execution_results)} execution results"
            logger.info(summary)

            return {"artifacts": artifacts, "summary": summary}

        @tool
        def detect_completion(
            execution_graph: Dict[str, Any], execution_results: List[Dict[str, Any]]
        ) -> Dict[str, Any]:
            """
            Detect if pipeline execution is complete.

            Args:
                execution_graph: Execution graph from loom topology
                execution_results: Results from agent execution

            Returns:
                Completion status with:
                - is_complete: bool
                - completion_percentage: float
                - remaining_nodes: List[str]
                - final_output: Optional[str]
            """
            # Check if all nodes in graph have been executed
            executed_nodes = set()
            for result in execution_results:
                if isinstance(result, dict) and "agent_id" in result:
                    executed_nodes.add(result["agent_id"])

            graph_nodes = {node["id"] for node in execution_graph.get("nodes", [])}
            remaining_nodes = list(graph_nodes - executed_nodes)

            completion_percentage = len(executed_nodes) / max(len(graph_nodes), 1) * 100

            is_complete = len(remaining_nodes) == 0 and completion_percentage >= 100

            completion_status = {
                "is_complete": is_complete,
                "completion_percentage": round(completion_percentage, 1),
                "remaining_nodes": remaining_nodes,
                "final_output": (
                    self._generate_final_output(execution_results)
                    if is_complete
                    else None
                ),
            }

            if is_complete:
                self._execution_status = "completed"
                logger.info("Pipeline execution complete")
            else:
                self._execution_status = "in_progress"
                logger.info(
                    f"Pipeline execution: {completion_percentage:.1f}% complete"
                )

            return completion_status

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
        def update_component_status(component_path: str, status: str) -> str:
            """
            Update component execution status.

            Args:
                component_path: Path to component
                status: New status (pending|in_progress|completed|failed)

            Returns:
                Updated component path
            """
            try:
                component = self.load_component(component_path)
                frontmatter = component.get("frontmatter", {})
                content = component.get("content", "")

                # Add status update to frontmatter
                frontmatter["execution_status"] = status
                frontmatter["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")

                # Append status to content
                content += f"\n\n---\nStatus: {status} at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"

                self.save_component(component_path, content, frontmatter)
                self._components_updated.append(component_path)

                logger.info(f"Updated component {component_path} with status: {status}")
                return component_path
            except Exception as e:
                logger.error(f"Failed to update component: {e}")
                return component_path

        @tool
        def save_execution_summary(
            artifact_name: str, _execution_result: Dict[str, Any]
        ) -> str:
            """
            Save pipeline execution summary.

            Args:
                artifact_name: Name for the artifact
                _execution_result: Final execution result

            Returns:
                Path to saved artifact
            """
            content = f"# Pipeline Execution Summary: {artifact_name}\n\n"
            content += f"## Execution Status\n\n{self._execution_status}\n\n"
            content += "## Metrics\n\n"
            content += f"- Duration: {self._execution_metrics['duration_seconds']:.2f} seconds\n"
            content += f"- Iterations: {self._execution_metrics['iterations']}\n"
            content += (
                f"- Successful Steps: {self._execution_metrics['successful_steps']}\n"
            )
            content += f"- Failed Steps: {self._execution_metrics['failed_steps']}\n"
            content += f"- Artifacts Produced: {len(self._artifacts_produced)}\n"
            content += f"- Components Updated: {len(self._components_updated)}\n\n"

            content += "## Execution History\n\n"
            for entry in self._execution_history[-10:]:  # Last 10 entries
                content += (
                    f"- {entry.get('action', 'N/A')}: {json.dumps(entry, indent=2)}\n"
                )

            component_path = f"documents/execution-summary-{artifact_name.lower().replace(' ', '-')}.md"
            frontmatter = {
                "template_id": f"execution-summary-{artifact_name.lower().replace(' ', '-')}",
                "template_type": "documents",
                "version": "1.0.0",
                "description": f"Execution summary for {artifact_name}",
                "status": self._execution_status,
                "duration": self._execution_metrics["duration_seconds"],
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

    def _execute_agent_step(
        self, agent_id: str, _loom_topology: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a single agent step (stub for actual agent invocation)."""
        # In real implementation, this would:
        # 1. Load agent configuration from loom_topology
        # 2. Initialize agent with tools and prompts
        # 3. Execute agent with bound components
        # 4. Return result

        return {
            "agent_id": agent_id,
            "status": "success",  # Stub: always success
            "output": f"Agent {agent_id} executed successfully",
            "timestamp": time.time(),
        }

    def _generate_final_output(self, execution_results: List[Dict[str, Any]]) -> str:
        """Generate final output from execution results."""
        outputs = []
        for result in execution_results:
            if isinstance(result, dict) and "output" in result:
                outputs.append(result["output"])

        return "\n\n".join(outputs)

    def execute_pipeline(
        self,
        loom_topology: Dict[str, Any],
        _domain_blueprint: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the pipeline with loom topology.

        Args:
            loom_topology: Output from Loom Builder
            domain_blueprint: Optional domain analysis for context

        Returns:
            Pipeline execution result with:
            - execution_status: str
            - artifacts_produced: List[Dict]
            - components_updated: List[str]
            - execution_metrics: Dict
            - final_output: str
        """
        logger.info(
            f"Starting pipeline execution with {len(loom_topology.get('agent_sequence', []))} agents"
        )

        self._execution_metrics["start_time"] = time.time()
        start_time = self._execution_metrics["start_time"]

        agent_sequence = loom_topology.get("agent_sequence", [])
        execution_graph = loom_topology.get("execution_graph", {})

        # Step 1: Execute agent sequence
        execution_result = self.execute_tool(
            "execute_agent_sequence",
            {"agent_sequence": agent_sequence, "loom_topology": loom_topology},
        )

        # Step 2: Monitor health
        health_status = self.execute_tool("monitor_execution_health", {})

        # Step 3: Handle failures with adaptive rerouting
        if execution_result.get("failed_agents"):
            for failed_agent in execution_result["failed_agents"]:
                _reroute_plan = self.execute_tool(
                    "perform_adaptive_reroute",
                    {"failed_agent": failed_agent, "loom_topology": loom_topology},
                )
                # In real implementation, would execute reroute

        # Step 4: Collect artifacts
        _artifacts_result = self.execute_tool(
            "collect_artifacts",
            {"execution_results": execution_result.get("results", [])},
        )

        # Step 5: Detect completion
        completion_status = self.execute_tool(
            "detect_completion",
            {
                "execution_graph": execution_graph,
                "execution_results": execution_result.get("results", []),
            },
        )

        # Update metrics
        end_time = time.time()
        self._execution_metrics["end_time"] = end_time
        self._execution_metrics["duration_seconds"] = end_time - start_time
        self._execution_metrics["iterations"] += 1

        # Build final result
        pipeline_result = {
            "execution_status": self._execution_status,
            "artifacts_produced": self._artifacts_produced,
            "components_updated": self._components_updated,
            "execution_metrics": self._execution_metrics,
            "execution_history": self._execution_history,
            "health_status": health_status,
            "completion_status": completion_status,
            "final_output": completion_status.get("final_output", ""),
        }

        logger.info(
            f"Pipeline execution complete. Status: {self._execution_status}, "
            f"Duration: {self._execution_metrics['duration_seconds']:.2f}s"
        )

        return pipeline_result

    def execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a registered tool by name."""
        from gaia.agents.base.tools import _TOOL_REGISTRY

        if tool_name not in _TOOL_REGISTRY:
            logger.error(f"Tool {tool_name} not registered")
            return {"error": f"Tool {tool_name} not registered"}

        tool_fn = _TOOL_REGISTRY[tool_name]["function"]
        return tool_fn(self, **tool_args)
