# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Configurable Agent - Allows defining agents via configuration (JSON/MD/YAML).

Supports full context injection: persona, voice, style, background, etc.
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
    All configuration fields (persona, voice, style, etc.) are injected into the LLM context.
    """

    def __init__(
        self,
        name: str,
        description: str,
        system_prompt: str,
        tools: List[str] = None,
        persona: Optional[Dict[str, Any]] = None,
        voice_characteristics: Optional[str] = None,
        background: Optional[str] = None,
        expertise: Optional[List[str]] = None,
        communication_style: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize the ConfigurableAgent.

        Args:
            name: Human-readable name of the agent
            description: Brief description of what the agent does
            system_prompt: The base instructions for the LLM
            tools: List of tool names to register for this agent
            persona: Dict with style, focus, background, expertise, etc.
            voice_characteristics: How the agent communicates (tone, style)
            background: Agent's background story/context
            expertise: List of expertise areas
            communication_style: Communication style description
            **kwargs: Standard Agent initialization parameters
        """
        self.agent_name = name
        self.agent_description = description
        self.base_system_prompt = system_prompt
        self.requested_tools = tools or []

        # Store persona fields for context injection
        self.persona = persona or {}
        self.voice_characteristics = voice_characteristics
        self.background = background
        self.expertise = expertise or []
        self.communication_style = communication_style

        # Call parent init
        super().__init__(**kwargs)

    def _register_tools(self) -> None:
        """Register tools specified in the configuration."""
        # This is a bit tricky since _TOOL_REGISTRY is a global dict
        # and standard Agents usually have Mixins that call register_*_tools.
        # For ConfigurableAgent, we assume the tools are already available in _TOOL_REGISTRY
        # or we might need a way to load them if they are not.
        
        # Actually, in GAIA, tool registration often happens by calling specific mixin methods.
        # But if we just want to restrict the LLM's view of tools, we can do that in _format_tools_for_prompt.
        pass

    def _sanitize_persona_value(self, value: str) -> str:
        """
        Sanitize persona field values to prevent prompt injection.

        Removes patterns that could be used to override system instructions.

        Args:
            value: The persona field value to sanitize

        Returns:
            Sanitized value with injection patterns removed
        """
        if not isinstance(value, str):
            return str(value) if value else ""

        value = value.strip()

        # Remove potential prompt injection patterns
        injection_patterns = [
            "IGNORE ABOVE",
            "IGNORE PREVIOUS",
            "SYSTEM:",
            "SYSTEM INSTRUCTION",
            "YOU ARE NOW",
            "NEW INSTRUCTION",
            "### SYSTEM",
            "<<<SYSTEM>>>",
        ]

        for pattern in injection_patterns:
            value = value.replace(pattern, "")

        return value

    def _get_system_prompt(self) -> str:
        """
        Return the pre-configured system prompt with full persona context injection.

        This method injects ALL persona fields into the LLM context:
        - persona.style, persona.focus, persona.background, persona.expertise
        - voice_characteristics, communication_style, background, expertise

        Returns:
            Complete system prompt with all context injected
        """
        logger.debug(f"Building system prompt for agent {self.agent_name}")

        parts = [self.base_system_prompt]

        # Build persona section - inject ALL persona fields into context
        persona_sections = []

        # Handle nested persona dict - with sanitization
        if self.persona:
            if self.persona.get('style'):
                value = self._sanitize_persona_value(self.persona['style'])
                persona_sections.append(f"**Style:** {value}")
            if self.persona.get('focus'):
                value = self._sanitize_persona_value(self.persona['focus'])
                persona_sections.append(f"**Focus:** {value}")
            if self.persona.get('background'):
                value = self._sanitize_persona_value(self.persona['background'])
                persona_sections.append(f"**Background:** {value}")
            if self.persona.get('expertise'):
                expertise = self.persona['expertise']
                if isinstance(expertise, list):
                    expertise = ', '.join(str(e) for e in expertise)
                persona_sections.append(f"**Expertise:** {expertise}")
            if self.persona.get('voice_characteristics'):
                value = self._sanitize_persona_value(self.persona['voice_characteristics'])
                persona_sections.append(f"**Voice:** {value}")
            if self.persona.get('communication_style'):
                value = self._sanitize_persona_value(self.persona['communication_style'])
                persona_sections.append(f"**Communication:** {value}")

        # Handle top-level persona fields (from YAML direct keys)
        if self.voice_characteristics:
            value = self._sanitize_persona_value(self.voice_characteristics)
            persona_sections.append(f"**Voice Characteristics:** {value}")
        if self.background:
            value = self._sanitize_persona_value(self.background)
            persona_sections.append(f"**Background:** {value}")
        if self.expertise:
            # Sanitize each item in the list
            sanitized = [self._sanitize_persona_value(e) for e in self.expertise]
            persona_sections.append(f"**Expertise:** {', '.join(sanitized)}")
        if self.communication_style:
            value = self._sanitize_persona_value(self.communication_style)
            persona_sections.append(f"**Communication Style:** {value}")

        # Add persona section if we have any persona fields
        if persona_sections:
            parts.append("\n==== AGENT PERSONA ====\n" + "\n".join(persona_sections))
            logger.debug(f"Injected {len(persona_sections)} persona sections for {self.agent_name}")
        else:
            logger.debug(f"No persona fields for {self.agent_name}, using base prompt only")

        return "\n\n".join(parts)

    def _format_tools_for_prompt(self) -> str:
        """
        Format only the requested tools for the prompt.
        If no tools requested, return empty string.
        If ['*'] requested, return all tools.
        """
        if not self.requested_tools:
            return ""
            
        tool_descriptions = []
        is_all = "*" in self.requested_tools
        
        for name, tool_info in _TOOL_REGISTRY.items():
            if not is_all and name not in self.requested_tools:
                continue
                
            params_str = ", ".join(
                [
                    f"{param_name}{'' if param_info['required'] else '?'}: {param_info['type']}"
                    for param_name, param_info in tool_info["parameters"].items()
                ]
            )

            description = tool_info["description"].strip()
            tool_descriptions.append(f"- {name}({params_str}): {description}")

        return "\n".join(tool_descriptions)

    def get_model_info(self) -> Dict[str, Any]:
        """Return model metadata for API registry."""
        return {
            "id": self.agent_name.lower().replace(" ", "-"),
            "description": self.agent_description,
            "max_input_tokens": 8192,
            "max_output_tokens": 4096,
        }
