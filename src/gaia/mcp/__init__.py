"""Model Context Protocol (MCP) integration for GAIA.

This module provides both server and client implementations for MCP:
- MCP Server: Expose GAIA agents as MCP servers
- MCP Client: Connect to external MCP servers from agents
"""

# MCP Client exports
from gaia.mcp.client import MCPClient, MCPTool
from gaia.mcp.client.config import MCPConfig
from gaia.mcp.client.mcp_client_manager import MCPClientManager
from gaia.mcp.mixin import MCPClientMixin

__all__ = [
    "MCPClient",
    "MCPTool",
    "MCPConfig",
    "MCPClientManager",
    "MCPClientMixin",
]
