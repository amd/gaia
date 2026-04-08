# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Gap Detector - Agent availability analysis for the GAIA pipeline.

The Gap Detector scans available agents, compares against recommended agents,
and identifies gaps that require agent generation via Master Ecosystem Creator.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool

logger = logging.getLogger(__name__)


class GapDetector(Agent):
    """
    Gap Detector Agent - Identifies missing agents for pipeline execution.

    This agent:
    1. Scans available agents from agents/ directory and .claude/agents/
    2. Parses agent frontmatter for capabilities and IDs
    3. Compares against recommended_agents from WorkflowModeler
    4. Identifies gaps (missing agents)
    5. Triggers Master Ecosystem Creator when gaps detected

    The output gap analysis is used to conditionally spawn missing agents
    before PipelineExecutor runs.
    """

    def __init__(self, **kwargs):
        """Initialize the Gap Detector agent."""
        kwargs.setdefault("model_id", "Qwen3.5-35B-A3B-GGUF")
        kwargs.setdefault("max_steps", 10)
        kwargs.setdefault("debug", False)

        super().__init__(**kwargs)

        # Gap detection state
        self._available_agents: List[Dict[str, Any]] = []
        self._recommended_agents: List[str] = []
        self._missing_agents: List[str] = []
        self._gap_analysis: Dict[str, Any] = {}

    def _register_tools(self):
        """Register gap detection tools."""

        @tool
        def scan_available_agents(
            agents_dir: str = "agents", claude_agents_dir: str = ".claude/agents"
        ) -> Dict[str, Any]:
            """
            Scan available agents from filesystem.

            Args:
                agents_dir: Path to agents/ directory
                claude_agents_dir: Path to .claude/agents/ directory

            Returns:
                Dictionary with:
                - agents: List[Dict] - available agents with id, capabilities, source
                - total_count: int - number of agents found
                - sources: Dict[str, int] - count by source directory
            """
            available = []
            sources = {"agents/": 0, ".claude/agents/": 0}

            # Scan agents/*.md files
            agents_path = Path(agents_dir)
            if agents_path.exists():
                for agent_file in agents_path.glob("*.md"):
                    agent_info = self._parse_agent_file(agent_file, "agents/")
                    if agent_info:
                        available.append(agent_info)
                        sources["agents/"] += 1
                        logger.debug(f"Found agent: {agent_info['id']} in agents/")

            # Scan .claude/agents/*.yml files
            claude_path = Path(claude_agents_dir)
            if claude_path.exists():
                for agent_file in claude_path.glob("*.yml"):
                    agent_info = self._parse_yaml_agent_file(
                        agent_file, ".claude/agents/"
                    )
                    if agent_info:
                        available.append(agent_info)
                        sources[".claude/agents/"] += 1
                        logger.debug(
                            f"Found agent: {agent_info['id']} in .claude/agents/"
                        )

            self._available_agents = available

            result = {
                "agents": available,
                "total_count": len(available),
                "sources": sources,
            }

            logger.info(
                f"Scanned {len(available)} available agents from {sum(sources.values())} sources"
            )
            return result

        @tool
        def compare_agents(
            available_agents: List[Dict], recommended_agents: List[str]
        ) -> Dict[str, Any]:
            """
            Compare available agents against recommended agents.

            Args:
                available_agents: List of available agent info from scan_available_agents
                recommended_agents: List of recommended agent IDs from WorkflowModeler

            Returns:
                Dictionary with:
                - available_ids: List[str] - IDs of available agents
                - recommended_ids: List[str] - IDs of recommended agents
                - missing_ids: List[str] - IDs of missing agents
                - covered_ids: List[str] - IDs of agents that exist
                - coverage_rate: float - percentage of recommended agents available
            """
            available_ids = set(agent["id"] for agent in available_agents)
            recommended_ids = set(recommended_agents)

            missing_ids = list(recommended_ids - available_ids)
            covered_ids = list(recommended_ids & available_ids)

            coverage_rate = (
                len(covered_ids) / len(recommended_ids) if recommended_ids else 1.0
            )

            self._missing_agents = missing_ids

            result = {
                "available_ids": sorted(list(available_ids)),
                "recommended_ids": sorted(list(recommended_ids)),
                "missing_ids": sorted(missing_ids),
                "covered_ids": sorted(covered_ids),
                "coverage_rate": round(coverage_rate, 2),
            }

            logger.info(
                f"Agent coverage: {len(covered_ids)}/{len(recommended_ids)} ({coverage_rate:.0%})"
            )
            return result

        @tool
        def analyze_gaps(
            missing_agents: List[str], task_objective: str
        ) -> Dict[str, Any]:
            """
            Analyze gaps and prepare generation plan for missing agents.

            Args:
                missing_agents: List of missing agent IDs
                task_objective: The original task/objective that requires these agents

            Returns:
                Dictionary with:
                - gaps_identified: bool - True if gaps exist
                - missing_agents: List[str] - agents to generate
                - generation_required: bool - whether generation is needed
                - generation_plan: Dict - plan for Master Ecosystem Creator
                - can_proceed: bool - whether pipeline can proceed without generation
            """
            gaps_identified = len(missing_agents) > 0

            # Generate plan for Master Ecosystem Creator
            generation_plan = {
                "agents_to_generate": missing_agents,
                "target_domain": task_objective,
                "priority": "high" if gaps_identified else "none",
                "block_pipeline": gaps_identified,  # Block pipeline until generated
            }

            self._gap_analysis = {
                "gaps_identified": gaps_identified,
                "missing_agents": missing_agents,
                "generation_required": gaps_identified,
                "generation_plan": generation_plan,
                "can_proceed": not gaps_identified,
            }

            if gaps_identified:
                logger.warning(
                    f"Gap analysis: {len(missing_agents)} missing agents - generation required"
                )
            else:
                logger.info(
                    "Gap analysis: All required agents available - no generation needed"
                )

            return self._gap_analysis

        @tool
        def trigger_agent_generation(generation_plan: Dict[str, Any]) -> Dict[str, Any]:
            """
            Trigger Master Ecosystem Creator to generate missing agents.

            This tool invokes the Master Ecosystem Creator agent via MCP protocol
            to generate the missing agents specified in the generation plan.

            Args:
                generation_plan: Output from analyze_gaps tool

            Returns:
                Dictionary with:
                - generation_triggered: bool
                - mcp_tool_call: str - the MCP tool call format
                - agents_to_spawn: List[str]
                - status: str - pending/running/completed
            """
            if not generation_plan.get("generation_required", False):
                logger.info("No generation required - skipping trigger")
                return {
                    "generation_triggered": False,
                    "reason": "no_gaps_identified",
                    "status": "skipped",
                }

            agents_to_spawn = generation_plan.get("agents_to_generate", [])
            target_domain = generation_plan.get("target_domain", "unknown")

            # Format MCP tool call for Master Ecosystem Creator
            mcp_tool_call = f"""```tool-call
CALL: mcp__master-ecosystem-creator__spawn_agents
purpose: Generate missing agents for pipeline execution
prompt: |
  TARGET_DOMAIN: {target_domain}
  AGENTS_TO_GENERATE: {agents_to_spawn}
  PRIORITY: high
  BLOCK_UNTIL_COMPLETE: true
```"""

            logger.info(
                f"Triggering agent generation for {len(agents_to_spawn)} agents"
            )
            logger.debug(f"MCP tool call:\n{mcp_tool_call}")

            return {
                "generation_triggered": True,
                "mcp_tool_call": mcp_tool_call,
                "agents_to_spawn": agents_to_spawn,
                "status": "pending",
                "target_domain": target_domain,
            }

        @tool
        def get_gap_analysis() -> Dict[str, Any]:
            """
            Get the current gap analysis state.

            Returns:
                Dictionary with current gap analysis results
            """
            return self._gap_analysis or {
                "gaps_identified": False,
                "missing_agents": [],
                "generation_required": False,
                "can_proceed": True,
            }

    def _parse_agent_file(
        self, file_path: Path, source: str
    ) -> Optional[Dict[str, Any]]:
        """Parse a .md agent file to extract frontmatter info."""
        try:
            content = file_path.read_text()

            # Extract YAML frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1].strip()
                    agent_id = None
                    capabilities = []

                    # Parse frontmatter lines
                    for line in frontmatter.split("\n"):
                        if line.startswith("id:"):
                            agent_id = line.split(":", 1)[1].strip()
                        elif line.startswith("capabilities:"):
                            # Capabilities might be multi-line YAML
                            idx = frontmatter.split("\n").index(line)
                            lines = frontmatter.split("\n")[idx + 1 :]
                            for cap_line in lines:
                                if cap_line.strip().startswith("- "):
                                    capabilities.append(cap_line.strip()[2:])
                                elif cap_line.strip() and not cap_line.startswith(" "):
                                    break

                    if agent_id:
                        return {
                            "id": agent_id,
                            "capabilities": capabilities,
                            "source": source,
                            "file": str(file_path),
                        }
        except Exception as e:
            logger.warning(f"Failed to parse agent file {file_path}: {e}")

        return None

    def _parse_yaml_agent_file(
        self, file_path: Path, source: str
    ) -> Optional[Dict[str, Any]]:
        """Parse a .yml agent file to extract agent info."""
        try:
            import yaml

            content = file_path.read_text()
            data = yaml.safe_load(content)

            agent_id = data.get("id", file_path.stem)
            capabilities = data.get("capabilities", [])

            return {
                "id": agent_id,
                "capabilities": capabilities if isinstance(capabilities, list) else [],
                "source": source,
                "file": str(file_path),
            }
        except Exception as e:
            logger.warning(f"Failed to parse YAML agent file {file_path}: {e}")

        return None

    def detect_gaps(
        self,
        recommended_agents: List[str],
        task_objective: str,
        agents_dir: str = "agents",
        claude_agents_dir: str = ".claude/agents",
    ) -> Dict[str, Any]:
        """
        Main method to detect agent gaps.

        This method:
        1. Scans available agents
        2. Compares against recommended agents
        3. Analyzes gaps
        4. Returns gap analysis with generation plan

        Args:
            recommended_agents: List of agent IDs recommended by WorkflowModeler
            task_objective: The task/objective requiring these agents
            agents_dir: Path to agents/ directory
            claude_agents_dir: Path to .claude/agents/ directory

        Returns:
            Gap analysis result with generation plan
        """
        logger.info(
            f"Starting gap detection for {len(recommended_agents)} recommended agents"
        )

        # Step 1: Scan available agents
        scan_result = self.execute_tool(
            "scan_available_agents",
            {"agents_dir": agents_dir, "claude_agents_dir": claude_agents_dir},
        )

        # Step 2: Compare against recommended
        compare_result = self.execute_tool(
            "compare_agents",
            {
                "available_agents": scan_result["agents"],
                "recommended_agents": recommended_agents,
            },
        )

        # Step 3: Analyze gaps
        gap_result = self.execute_tool(
            "analyze_gaps",
            {
                "missing_agents": compare_result["missing_ids"],
                "task_objective": task_objective,
            },
        )

        return {
            "scan_result": scan_result,
            "compare_result": compare_result,
            "gap_result": gap_result,
        }


def detect_agent_gaps(
    recommended_agents: List[str],
    task_objective: str,
    agents_dir: str = "agents",
    claude_agents_dir: str = ".claude/agents",
) -> Dict[str, Any]:
    """
    Convenience function to detect agent gaps without instantiating class.

    Args:
        recommended_agents: List of recommended agent IDs
        task_objective: Task requiring these agents
        agents_dir: Path to agents directory
        claude_agents_dir: Path to .claude/agents directory

    Returns:
        Gap analysis result
    """
    detector = GapDetector(debug=False)
    return detector.detect_gaps(
        recommended_agents=recommended_agents,
        task_objective=task_objective,
        agents_dir=agents_dir,
        claude_agents_dir=claude_agents_dir,
    )
