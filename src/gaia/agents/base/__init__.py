# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Base agent functionality for building domain-specific agents.
"""

from gaia.agents.base.agent import Agent  # noqa: F401
from gaia.agents.base.mcp_agent import MCPAgent  # noqa: F401
from gaia.agents.base.tools import _TOOL_REGISTRY, tool  # noqa: F401

# Pipeline orchestration agent definitions
from gaia.agents.base.context import (  # noqa: F401
    AgentState,
    AgentCapabilities,
    AgentTriggers,
    AgentConstraints,
    AgentDefinition,
    BaseAgent,
)

__all__ = [
    # Existing exports
    "Agent",
    "MCPAgent",
    "tool",
    "_TOOL_REGISTRY",
    # Pipeline orchestration
    "AgentState",
    "AgentCapabilities",
    "AgentTriggers",
    "AgentConstraints",
    "AgentDefinition",
    "BaseAgent",
]
