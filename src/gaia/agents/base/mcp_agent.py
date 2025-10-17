# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
MCP-Capable Agent Base Class
Intermediate class for agents that support Model Context Protocol (MCP)
"""

from abc import abstractmethod
from typing import Any, Dict, List

from .agent import Agent


class MCPAgent(Agent):
    """
    Base class for agents that support MCP.

    Agents that inherit from MCPAgent can be exposed via MCP servers,
    allowing external tools (like VSCode) to interact with them.
    """

    @abstractmethod
    def get_mcp_tool_definitions(self) -> List[Dict[str, Any]]:
        """
        Return MCP tool definitions for this agent.

        Each tool definition should include:
        - name: Tool name (lowercase, dashes allowed)
        - description: What the tool does
        - inputSchema: JSON schema for parameters

        Returns:
            List of tool definition dictionaries
        """

    @abstractmethod
    def execute_mcp_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute an MCP tool call.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments from MCP client

        Returns:
            Result dictionary (will be JSON-serialized)

        Raises:
            ValueError: If tool_name is unknown
        """

    def get_mcp_prompts(self) -> List[Dict[str, Any]]:
        """
        Optional: Return MCP prompts.

        Prompts are pre-defined templates that clients can use.
        Override this method if your agent provides prompts.

        Returns:
            List of prompt definitions (empty by default)
        """
        return []

    def get_mcp_resources(self) -> List[Dict[str, Any]]:
        """
        Optional: Return MCP resources.

        Resources are data sources the agent can provide.
        Override this method if your agent exposes resources.

        Returns:
            List of resource definitions (empty by default)
        """
        return []

    def get_mcp_server_info(self) -> Dict[str, Any]:
        """
        Get MCP server metadata for this agent.

        Returns:
            Server info dictionary with name and version
        """
        return {"name": f"GAIA {self.__class__.__name__}", "version": "2.0.0"}
