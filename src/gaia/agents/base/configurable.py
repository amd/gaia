# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Configurable Agent - Allows defining agents via configuration (JSON/MD).
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
    """

    def __init__(
        self,
        name: str,
        description: str,
        system_prompt: str,
        tools: List[str] = None,
        **kwargs
    ):
        """
        Initialize the ConfigurableAgent.

        Args:
            name: Human-readable name of the agent
            description: Brief description of what the agent does
            system_prompt: The base instructions for the LLM
            tools: List of tool names to register for this agent
            **kwargs: Standard Agent initialization parameters
        """
        self.agent_name = name
        self.agent_description = description
        self.base_system_prompt = system_prompt
        self.requested_tools = tools or []
        
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

    def _get_system_prompt(self) -> str:
        """Return the pre-configured system prompt."""
        return self.base_system_prompt

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
