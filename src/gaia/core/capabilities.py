# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent capabilities definition for the modular architecture core.

This module provides the AgentCapabilities dataclass that defines what an agent
can do, including supported tools, models, and special capabilities like vision,
audio, and code execution.

Key Components:
    - AgentCapabilities: Dataclass defining agent capabilities
    - Capability validation methods
    - Thread-safe capability checking

Example Usage:
    ```python
    from gaia.core.capabilities import AgentCapabilities

    # Define capabilities for a code agent
    caps = AgentCapabilities(
        supported_tools=["read_file", "write_file", "run_tests"],
        supported_models=["Qwen3.5-35B", "Qwen3-0.6B"],
        max_context_tokens=32768,
        requires_workspace=True,
        supports_code_execution=True,
    )

    # Check specific capabilities
    if caps.supports_code_execution:
        print("Agent can execute code")

    # Validate capabilities
    caps.validate()
    ```
"""

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class AgentCapabilities:
    """
    Defines the capabilities of an agent.

    This dataclass captures all the capabilities an agent possesses,
    including tool support, model compatibility, resource requirements,
    and special capabilities like vision or audio processing.

    Thread Safety:
        All public methods are thread-safe using RLock for reentrant locking.

    Attributes:
        supported_tools: List of tool names the agent supports.
        supported_models: List of model IDs the agent is compatible with.
        max_context_tokens: Maximum context window size in tokens.
        requires_workspace: Whether the agent requires a workspace directory.
        requires_internet: Whether the agent requires internet connectivity.
        requires_api_keys: Whether the agent requires API keys configuration.
        supports_vision: Whether the agent supports vision-language models.
        supports_audio: Whether the agent supports audio processing (ASR/TTS).
        supports_code_execution: Whether the agent can execute generated code.

    Example:
        >>> caps = AgentCapabilities(
        ...     supported_tools=["read_file", "write_file"],
        ...     max_context_tokens=16384,
        ...     requires_workspace=True,
        ...     supports_code_execution=True,
        ... )
        >>> caps.validate()
        >>> print(caps.supports_code_execution)
        True
    """

    # Core capabilities
    supported_tools: List[str] = field(default_factory=list)
    supported_models: List[str] = field(default_factory=list)

    # Resource requirements
    max_context_tokens: Optional[int] = None
    requires_workspace: bool = False
    requires_internet: bool = False
    requires_api_keys: bool = False

    # Special capabilities
    supports_vision: bool = False
    supports_audio: bool = False
    supports_code_execution: bool = False

    # Additional metadata (for extensibility)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize internal state after dataclass initialization."""
        self._lock = threading.RLock()
        # Convert lists to sets for O(1) lookup internally
        self._tool_set: Set[str] = set(self.supported_tools)
        self._model_set: Set[str] = set(self.supported_models)

    def validate(self) -> bool:
        """
        Validate the capabilities configuration.

        This method checks that the capabilities configuration is valid
        and consistent. It verifies:
        - max_context_tokens is positive if specified
        - supported_tools contains only valid tool names
        - supported_models contains only valid model names

        Returns:
            True if configuration is valid.

        Raises:
            ValueError: If configuration is invalid.

        Example:
            >>> caps = AgentCapabilities(max_context_tokens=-100)
            >>> try:
            ...     caps.validate()
            ... except ValueError as e:
            ...     print(f"Invalid: {e}")
            Invalid: max_context_tokens must be positive
        """
        with self._lock:
            # Validate max_context_tokens
            if self.max_context_tokens is not None:
                if self.max_context_tokens <= 0:
                    raise ValueError("max_context_tokens must be positive")
                if self.max_context_tokens > 1000000:
                    raise ValueError(
                        f"max_context_tokens {self.max_context_tokens} exceeds maximum (1000000)"
                    )

            # Validate supported_tools
            for tool in self.supported_tools:
                if not isinstance(tool, str):
                    raise ValueError(f"Tool name must be string, got {type(tool)}")
                if not tool.strip():
                    raise ValueError("Tool name cannot be empty or whitespace")
                if tool != tool.strip():
                    raise ValueError(
                        f"Tool name '{tool}' contains leading/trailing whitespace"
                    )

            # Validate supported_models
            for model in self.supported_models:
                if not isinstance(model, str):
                    raise ValueError(f"Model name must be string, got {type(model)}")
                if not model.strip():
                    raise ValueError("Model name cannot be empty or whitespace")

            # Validate metadata
            if not isinstance(self.metadata, dict):
                raise ValueError("metadata must be a dictionary")

            return True

    def has_tool(self, tool_name: str) -> bool:
        """
        Check if the agent supports a specific tool.

        Args:
            tool_name: Name of the tool to check.

        Returns:
            True if the tool is supported, False otherwise.

        Example:
            >>> caps = AgentCapabilities(supported_tools=["read_file", "write_file"])
            >>> caps.has_tool("read_file")
            True
            >>> caps.has_tool("execute_command")
            False
        """
        with self._lock:
            return tool_name in self._tool_set

    def has_model(self, model_id: str) -> bool:
        """
        Check if the agent supports a specific model.

        Args:
            model_id: Model ID to check.

        Returns:
            True if the model is supported, False otherwise.

        Example:
            >>> caps = AgentCapabilities(supported_models=["Qwen3.5-35B", "Qwen3-0.6B"])
            >>> caps.has_model("Qwen3.5-35B")
            True
        """
        with self._lock:
            return model_id in self._model_set

    def add_tool(self, tool_name: str) -> None:
        """
        Add a tool to the supported tools list.

        Args:
            tool_name: Name of the tool to add.

        Example:
            >>> caps = AgentCapabilities()
            >>> caps.add_tool("read_file")
            >>> caps.has_tool("read_file")
            True
        """
        with self._lock:
            if tool_name not in self._tool_set:
                self._tool_set.add(tool_name)
                self.supported_tools.append(tool_name)

    def add_model(self, model_id: str) -> None:
        """
        Add a model to the supported models list.

        Args:
            model_id: Model ID to add.

        Example:
            >>> caps = AgentCapabilities()
            >>> caps.add_model("Qwen3.5-35B")
            >>> caps.has_model("Qwen3.5-35B")
            True
        """
        with self._lock:
            if model_id not in self._model_set:
                self._model_set.add(model_id)
                self.supported_models.append(model_id)

    def remove_tool(self, tool_name: str) -> bool:
        """
        Remove a tool from the supported tools list.

        Args:
            tool_name: Name of the tool to remove.

        Returns:
            True if the tool was removed, False if it wasn't in the list.

        Example:
            >>> caps = AgentCapabilities(supported_tools=["read_file", "write_file"])
            >>> caps.remove_tool("read_file")
            True
            >>> caps.remove_tool("read_file")
            False
        """
        with self._lock:
            if tool_name in self._tool_set:
                self._tool_set.remove(tool_name)
                self.supported_tools.remove(tool_name)
                return True
            return False

    def remove_model(self, model_id: str) -> bool:
        """
        Remove a model from the supported models list.

        Args:
            model_id: Model ID to remove.

        Returns:
            True if the model was removed, False if it wasn't in the list.
        """
        with self._lock:
            if model_id in self._model_set:
                self._model_set.remove(model_id)
                self.supported_models.remove(model_id)
                return True
            return False

    def get_required_resources(self) -> List[str]:
        """
        Get a list of required resources based on capability flags.

        Returns:
            List of resource names (e.g., ["workspace", "internet", "api_keys"]).

        Example:
            >>> caps = AgentCapabilities(
            ...     requires_workspace=True,
            ...     requires_internet=True,
            ... )
            >>> caps.get_required_resources()
            ['workspace', 'internet']
        """
        with self._lock:
            resources = []
            if self.requires_workspace:
                resources.append("workspace")
            if self.requires_internet:
                resources.append("internet")
            if self.requires_api_keys:
                resources.append("api_keys")
            return resources

    def get_special_capabilities(self) -> List[str]:
        """
        Get a list of special capabilities enabled.

        Returns:
            List of special capability names (e.g., ["vision", "audio", "code_execution"]).

        Example:
            >>> caps = AgentCapabilities(
            ...     supports_vision=True,
            ...     supports_code_execution=True,
            ... )
            >>> caps.get_special_capabilities()
            ['vision', 'code_execution']
        """
        with self._lock:
            capabilities = []
            if self.supports_vision:
                capabilities.append("vision")
            if self.supports_audio:
                capabilities.append("audio")
            if self.supports_code_execution:
                capabilities.append("code_execution")
            return capabilities

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert capabilities to a dictionary representation.

        Returns:
            Dictionary with all capability fields.

        Example:
            >>> caps = AgentCapabilities(
            ...     supported_tools=["read_file"],
            ...     max_context_tokens=16384,
            ... )
            >>> d = caps.to_dict()
            >>> d['supported_tools']
            ['read_file']
            >>> d['max_context_tokens']
            16384
        """
        with self._lock:
            return {
                "supported_tools": list(self.supported_tools),
                "supported_models": list(self.supported_models),
                "max_context_tokens": self.max_context_tokens,
                "requires_workspace": self.requires_workspace,
                "requires_internet": self.requires_internet,
                "requires_api_keys": self.requires_api_keys,
                "supports_vision": self.supports_vision,
                "supports_audio": self.supports_audio,
                "supports_code_execution": self.supports_code_execution,
                "metadata": dict(self.metadata),
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentCapabilities":
        """
        Create capabilities from a dictionary.

        Args:
            data: Dictionary with capability fields.

        Returns:
            New AgentCapabilities instance.

        Example:
            >>> d = {
            ...     "supported_tools": ["read_file", "write_file"],
            ...     "max_context_tokens": 16384,
            ...     "requires_workspace": True,
            ... }
            >>> caps = AgentCapabilities.from_dict(d)
            >>> caps.supported_tools
            ['read_file', 'write_file']
        """
        return cls(
            supported_tools=data.get("supported_tools", []),
            supported_models=data.get("supported_models", []),
            max_context_tokens=data.get("max_context_tokens"),
            requires_workspace=data.get("requires_workspace", False),
            requires_internet=data.get("requires_internet", False),
            requires_api_keys=data.get("requires_api_keys", False),
            supports_vision=data.get("supports_vision", False),
            supports_audio=data.get("supports_audio", False),
            supports_code_execution=data.get("supports_code_execution", False),
            metadata=data.get("metadata", {}),
        )

    def __eq__(self, other: Any) -> bool:
        """
        Check equality with another AgentCapabilities instance.

        Args:
            other: Object to compare with.

        Returns:
            True if the objects are equal, False otherwise.
        """
        if not isinstance(other, AgentCapabilities):
            return False
        return self.to_dict() == other.to_dict()

    def __repr__(self) -> str:
        """Return string representation of capabilities."""
        parts = []
        if self.supported_tools:
            parts.append(f"tools={len(self.supported_tools)}")
        if self.supported_models:
            parts.append(f"models={len(self.supported_models)}")
        if self.max_context_tokens:
            parts.append(f"context={self.max_context_tokens}")
        special = self.get_special_capabilities()
        if special:
            parts.append(f"special={special}")
        return f"AgentCapabilities({', '.join(parts)})"
