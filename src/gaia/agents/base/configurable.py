# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Configurable Agent - Allows defining agents via configuration (JSON/MD/YAML).

Supports full context injection via a unified persona dictionary.
"""

import logging
from typing import Any, Dict, List, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY

logger = logging.getLogger(__name__)


class ConfigurableAgent(Agent):
    """
    Agent that is initialized and configured via a dictionary or file.

    This allows creating new agent 'personalities' without writing new Python classes.
    All configuration fields are injected into the LLM context via a unified persona structure.

    Unified Persona Structure:
        persona: {
            "style": "Analytical and methodical",
            "focus": "Information gathering and synthesis",
            "background": "PhD in Information Science...",
            "expertise": ["research", "analysis"],
            "voice": "Precise, measured language",
            "communication": "Professional, thorough"
        }
    """

    def __init__(
        self,
        name: str,
        description: str,
        system_prompt: str,
        tools: List[str] = None,
        persona: Optional[Dict[str, Any]] = None,
        **kwargs,
    ):
        """
        Initialize the ConfigurableAgent.

        Args:
            name: Human-readable name of the agent
            description: Brief description of what the agent does
            system_prompt: The base instructions for the LLM
            tools: List of tool names to register for this agent (must match @tool registered functions)
            persona: Unified dict with style, focus, background, expertise, voice, communication
            **kwargs: Standard Agent initialization parameters
        """
        self.agent_name = name
        self.agent_description = description
        self.base_system_prompt = system_prompt
        self.requested_tools = tools or []

        # Consolidate all persona fields into single dict
        self.persona = persona or {}

        # Call parent init
        super().__init__(**kwargs)

        # Register tools after parent init
        self._register_tools()

    def _register_tools(self) -> None:
        """
        Register tools specified in the configuration.

        Filters the global tool registry to only include tools requested
        in the agent configuration. Tools must be registered via @tool decorator
        elsewhere in the system.
        """
        if not self.requested_tools:
            logger.debug(f"No tools requested for agent {self.agent_name}")
            return

        # Validate requested tools exist in registry
        available_tools = set(_TOOL_REGISTRY.keys())
        requested = set(self.requested_tools)

        if "*" in requested:
            logger.debug(f"Agent {self.agent_name} requested all tools")
            return  # All tools available, no filtering needed

        # Check for unknown tools
        unknown = requested - available_tools
        if unknown:
            logger.warning(
                f"Agent {self.agent_name} requested unknown tools: {unknown}. "
                f"Available: {sorted(available_tools)}"
            )

        logger.debug(
            f"Agent {self.agent_name} registered tools: {self.requested_tools}"
        )

    def _get_system_prompt(self) -> str:
        """
        Return the pre-configured system prompt with unified persona context injection.

        This method injects all persona fields into the LLM context:
        - style, focus, background, expertise
        - voice (was voice_characteristics)
        - communication (was communication_style)

        Returns:
            Complete system prompt with all context injected
        """
        logger.debug(f"Building system prompt for agent {self.agent_name}")

        parts = [self.base_system_prompt]

        # Build persona section - inject all persona fields into context
        persona_sections = []

        if self.persona:
            # Unified persona dict - all fields handled consistently
            if self.persona.get("style"):
                value = self.persona["style"]
                persona_sections.append(f"**Style:** {value}")
            if self.persona.get("focus"):
                value = self.persona["focus"]
                persona_sections.append(f"**Focus:** {value}")
            if self.persona.get("background"):
                value = self.persona["background"]
                persona_sections.append(f"**Background:** {value}")
            if self.persona.get("expertise"):
                expertise = self.persona["expertise"]
                if isinstance(expertise, list):
                    expertise = ", ".join(str(e) for e in expertise)
                persona_sections.append(f"**Expertise:** {expertise}")
            if self.persona.get("voice"):
                value = self.persona["voice"]
                persona_sections.append(f"**Voice:** {value}")
            if self.persona.get("communication"):
                value = self.persona["communication"]
                persona_sections.append(f"**Communication:** {value}")

        # Add persona section if we have any persona fields
        if persona_sections:
            parts.append("\n## AGENT PERSONA\n" + "\n".join(persona_sections))
            logger.debug(
                f"Injected {len(persona_sections)} persona sections for {self.agent_name}"
            )
        else:
            logger.debug(
                f"No persona fields for {self.agent_name}, using base prompt only"
            )

        return "\n\n".join(parts)

    def _format_tools_for_prompt(self) -> str:
        """
        Format only the requested tools for the prompt.

        Delegates to parent class for tool formatting, then filters
        to only include tools requested in the configuration.

        Returns:
            Formatted tool descriptions for requested tools only
        """
        if not self.requested_tools:
            return ""

        # Get all formatted tools from parent
        all_tools_text = super()._format_tools_for_prompt()
        if not all_tools_text:
            return ""

        # If wildcard, return all tools
        if "*" in self.requested_tools:
            return all_tools_text

        # Filter to only requested tools
        requested_set = set(self.requested_tools)
        filtered_lines = []

        for line in all_tools_text.split("\n"):
            # Extract tool name from line format: "- tool_name(...): description"
            if line.startswith("- "):
                tool_name = line[2:].split("(")[0].strip()
                if tool_name in requested_set:
                    filtered_lines.append(line)

        return "\n".join(filtered_lines) if filtered_lines else ""

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        """
        Execute a tool by name with enforcement of tool filtering.

        Overrides parent class to ensure only configured tools can be executed,
        not just displayed in the prompt.

        Args:
            tool_name: Name of the tool to execute
            tool_args: Arguments to pass to the tool

        Returns:
            Result of the tool execution

        Raises:
            ValueError: If tool_name is not in the configured tools list
        """
        # Enforce tool filtering at execution time
        if "*" not in self.requested_tools and tool_name not in self.requested_tools:
            logger.warning(
                f"Tool '{tool_name}' not available for agent {self.agent_name}. "
                f"Available tools: {self.requested_tools}"
            )
            return {
                "status": "error",
                "error": f"Tool '{tool_name}' is not available. "
                f"Available tools: {', '.join(self.requested_tools)}",
            }

        # Call parent class execution logic
        return super()._execute_tool(tool_name, tool_args)

    def get_model_info(self) -> Dict[str, Any]:
        """Return model metadata for API registry."""
        return {
            "id": self.agent_name.lower().replace(" ", "-"),
            "description": self.agent_description,
            "max_input_tokens": 8192,
            "max_output_tokens": 4096,
        }
