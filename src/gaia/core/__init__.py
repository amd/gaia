# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Core module for the modular architecture system.

This module provides the foundational components for building modular agents:
- AgentProfile: Configuration profiles for agents
- AgentCapabilities: Capability definitions
- AgentExecutor: Execution framework with behavior injection
- PluginRegistry: Plugin management system

Example Usage:
    ```python
    from gaia.core import (
        AgentProfile,
        AgentCapabilities,
        AgentExecutor,
        PluginRegistry,
        PluginMetadata,
    )

    # Create a profile
    profile = AgentProfile(
        name="My Agent",
        capabilities=AgentCapabilities(
            supported_tools=["read_file", "write_file"],
        ),
    )

    # Create executor
    executor = AgentExecutor(profile=profile)

    # Register and use plugins
    registry = PluginRegistry.get_instance()
    registry.register_plugin("my-plugin", lambda ctx: "Hello")
    result = registry.execute_plugin("my-plugin", context={})
    ```
"""

from gaia.core.capabilities import AgentCapabilities
from gaia.core.di_container import (
    DIContainer,
    ServiceLifetime,
    ServiceDescriptor,
    ServiceResolutionError,
    CircularDependencyError,
)
from gaia.core.executor import AgentExecutor, ExecutionContext, ExecutionResult
from gaia.core.plugin import PluginMetadata, PluginRegistry
from gaia.core.profile import AgentProfile
from gaia.core.adapter import AgentAdapter, LegacyAgentWrapper, extract_profile

__all__ = [
    # Profile
    "AgentProfile",
    "AgentCapabilities",
    # Executor
    "AgentExecutor",
    "ExecutionContext",
    "ExecutionResult",
    # Plugin
    "PluginRegistry",
    "PluginMetadata",
    # DI Container
    "DIContainer",
    "ServiceLifetime",
    "ServiceDescriptor",
    "ServiceResolutionError",
    "CircularDependencyError",
    # Adapter
    "AgentAdapter",
    "LegacyAgentWrapper",
    "extract_profile",
]

# Module version
__version__ = "1.0.0"


def get_version() -> str:
    """Return the module version."""
    return __version__


def get_core_components() -> dict:
    """
    Get all core components as a dictionary.

    Returns:
        Dictionary mapping component names to classes.
    """
    return {
        # Profile
        "AgentProfile": AgentProfile,
        "AgentCapabilities": AgentCapabilities,
        # Executor
        "AgentExecutor": AgentExecutor,
        "ExecutionContext": ExecutionContext,
        "ExecutionResult": ExecutionResult,
        # Plugin
        "PluginRegistry": PluginRegistry,
        "PluginMetadata": PluginMetadata,
        # DI Container
        "DIContainer": DIContainer,
        "ServiceLifetime": ServiceLifetime,
        "ServiceDescriptor": ServiceDescriptor,
        # Adapter
        "AgentAdapter": AgentAdapter,
        "LegacyAgentWrapper": LegacyAgentWrapper,
    }
