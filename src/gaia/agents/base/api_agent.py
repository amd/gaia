# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
API Agent Base Class - Mixin for OpenAI API-compatible agents

This module provides ApiAgent, an optional mixin class for agents that want to be
exposed via the OpenAI-compatible API with custom behavior.

Inheritance patterns:
    - CodeAgent(ApiAgent, Agent) - API-only agent
    - DockerAgent(MCPAgent, Agent) - MCP-only agent
    - JiraAgent(MCPAgent, ApiAgent, Agent) - Both protocols
    - Future: FooAgent(MCPAgent, ApiAgent, Agent) - Multiple inheritance
"""

from typing import Any, Dict

from .agent import Agent


class ApiAgent(Agent):
    """
    Optional mixin for agents exposed via OpenAI-compatible API.

    Agents that inherit from ApiAgent can customize:
    - Model ID and metadata (get_model_id, get_model_info)
    - Token counting (estimate_tokens)
    - Response formatting (format_for_api)

    The API server can work with ANY Agent subclass via process_query().
    ApiAgent is only needed for customization beyond the defaults.

    Usage:
        class MyAgent(ApiAgent, Agent):
            '''Agent exposed via API with custom behavior'''
            pass

        class MyMultiAgent(MCPAgent, ApiAgent, Agent):
            '''Agent exposed via BOTH MCP and API protocols'''
            pass

    Example:
        >>> class CodeAgent(ApiAgent, MCPAgent, Agent):
        ...     def get_model_id(self) -> str:
        ...         return "gaia-code-agent"
        ...
        ...     def get_model_info(self) -> Dict:
        ...         return {
        ...             "max_input_tokens": 32768,
        ...             "max_output_tokens": 8192
        ...         }
    """

    def get_model_id(self) -> str:
        """
        Get the OpenAI-compatible model ID for this agent.

        Override to customize model ID.
        Default: gaia-{classname} (with 'Agent' suffix removed)

        Returns:
            Model ID string (e.g., "gaia-code", "gaia-jira")

        Example:
            CodeAgent -> gaia-code
            JiraAgent -> gaia-jira
            DockerAgent -> gaia-docker
        """
        # All agents follow *Agent naming convention, strip "Agent" suffix
        class_name = self.__class__.__name__[:-5].lower()  # Remove "Agent"
        return f"gaia-{class_name}"

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get model metadata for /v1/models endpoint.

        Override to provide custom metadata about the model's capabilities.

        Returns:
            Dictionary with model metadata:
                - max_input_tokens: Maximum input context size
                - max_output_tokens: Maximum output length
                - description (optional): Human-readable description
                - Any other custom metadata

        Example:
            >>> def get_model_info(self):
            ...     return {
            ...         "max_input_tokens": 32768,
            ...         "max_output_tokens": 8192,
            ...         "description": "Autonomous Python coding agent"
            ...     }
        """
        return {
            "max_input_tokens": 8192,
            "max_output_tokens": 4096,
        }

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for text.

        Override for model-specific tokenization.
        Default: Simple char count / 4 heuristic (works reasonably for English)

        Args:
            text: Input text to count tokens for

        Returns:
            Estimated token count

        Example:
            >>> def estimate_tokens(self, text: str) -> int:
            ...     # Use tiktoken for accurate GPT-style counting
            ...     import tiktoken
            ...     enc = tiktoken.get_encoding("cl100k_base")
            ...     return len(enc.encode(text))
        """
        return len(text) // 4
