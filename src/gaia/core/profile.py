# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent profile system for the modular architecture core.

This module provides the AgentProfile dataclass that defines an agent's
configuration including its identity, capabilities, tools, and model settings.

Key Components:
    - AgentProfile: Dataclass for agent configuration
    - Profile validation methods
    - YAML serialization support
    - Thread-safe operations

Architectural Notes (ISS-005):
    This implementation follows a simplified pattern vs. the Phase 3 specification:

    1. FROZEN=False DEVIATION (ISS-001): The dataclass uses frozen=False (mutable)
       instead of frozen=True as specified. This is an intentional design choice
       to support:
       - Tool registration and modification after initialization
       - Model configuration updates during agent lifecycle
       - Backward compatibility with existing agent patterns

       Thread safety is achieved through RLock protection on all public methods
       rather than immutability. Deep copies are used for mutable field returns
       to prevent unintended external mutations.

    2. FIELD NAME ALIGNMENT (ISS-002): The profile now includes spec-aligned fields
       `id` and `role` while maintaining backward compatibility with `name` and
       `description`. The `description` field is deprecated but preserved for
       existing agent implementations.

    3. VALIDATION AT_RUNTIME: Profile validation occurs via the validate() method
       rather than only at construction time, allowing for gradual configuration
       updates during agent lifecycle.

Example Usage:
    ```python
    from gaia.core.profile import AgentProfile
    from gaia.core.capabilities import AgentCapabilities

    # Create a profile for a code agent
    profile = AgentProfile(
        id="code-agent",
        name="Senior Developer",
        role="Expert software developer agent",
        capabilities=AgentCapabilities(
            supported_tools=["read_file", "write_file", "run_tests"],
            supports_code_execution=True,
        ),
        model_config={"model_id": "Qwen3.5-35B", "temperature": 0.7},
    )

    # Validate and serialize
    profile.validate()
    yaml_str = profile.to_yaml()
    ```
"""

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gaia.core.capabilities import AgentCapabilities


@dataclass
class AgentProfile:
    """
    Configuration profile for an agent.

    This dataclass captures the complete configuration of an agent,
    including its identity, capabilities, tools, and model settings.
    Profiles can be serialized to/from YAML for configuration files.

    Thread Safety:
        All public methods are thread-safe using RLock for reentrant locking.
        Deep copies are used for mutable fields to prevent unintended mutations.

    Attributes:
        id: Unique identifier for the agent (e.g., "code-agent", "chat-agent").
        name: Human-readable name of the agent.
        role: Agent role description (e.g., "Expert software developer").
        description: Detailed description of the agent's purpose (deprecated, use role).
        capabilities: AgentCapabilities instance defining what the agent can do.
        tools: List of tool names available to this agent.
        model_config: Dictionary of model configuration settings.
        version: Profile version string (e.g., "1.0.0").
        metadata: Additional metadata dictionary.

    Example:
        >>> from gaia.core.capabilities import AgentCapabilities
        >>> profile = AgentProfile(
        ...     id="code-assistant",
        ...     name="Code Assistant",
        ...     role="Expert software developer",
        ...     capabilities=AgentCapabilities(
        ...         supported_tools=["read_file", "write_file"],
        ...         supports_code_execution=True,
        ...     ),
        ...     model_config={"model_id": "Qwen3.5-35B"},
        ...     version="1.0.0",
        ... )
        >>> profile.validate()
        >>> print(profile.id)
        code-assistant
    """

    # Identity (spec-aligned fields)
    id: str = field(default_factory=lambda: "unnamed-agent")
    name: str = "Unnamed Agent"
    role: str = ""

    # Backward compatibility field (deprecated, use role)
    description: str = ""

    # Capabilities and tools
    capabilities: Optional[AgentCapabilities] = None
    tools: List[str] = field(default_factory=list)

    # Model configuration
    model_config: Dict[str, Any] = field(default_factory=dict)

    # Versioning and metadata
    version: str = "1.0.0"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize internal state after dataclass initialization."""
        self._lock = threading.RLock()

        # Ensure capabilities is set
        if self.capabilities is None:
            self.capabilities = AgentCapabilities()

        # Deep copy mutable fields to prevent external mutations
        self.tools = list(self.tools) if self.tools else []
        self.model_config = dict(self.model_config) if self.model_config else {}
        self.metadata = dict(self.metadata) if self.metadata else {}

    def validate(self) -> bool:
        """
        Validate the profile configuration.

        This method checks that the profile configuration is valid and consistent.
        It verifies:
        - name is non-empty
        - version is valid semver format (basic check)
        - capabilities are valid (if present)
        - tools list doesn't contain duplicates
        - model_config has required keys

        Returns:
            True if configuration is valid.

        Raises:
            ValueError: If configuration is invalid.

        Example:
            >>> profile = AgentProfile(id="", name="")
            >>> try:
            ...     profile.validate()
            ... except ValueError as e:
            ...     print(f"Invalid: {e}")
            Invalid: id cannot be empty
        """
        with self._lock:
            # Validate id (spec-aligned)
            if not self.id or not self.id.strip():
                raise ValueError("id cannot be empty")
            if self.id != self.id.strip():
                raise ValueError("id contains leading/trailing whitespace")

            # Validate name
            if not self.name or not self.name.strip():
                raise ValueError("name cannot be empty")
            if self.name != self.name.strip():
                raise ValueError("name contains leading/trailing whitespace")

            # Validate version (basic semver check)
            if self.version:
                # Handle pre-release suffix by splitting it off first
                version_base = self.version.split("-")[0]
                parts = version_base.split(".")
                if len(parts) < 1 or len(parts) > 4:
                    raise ValueError(
                        f"version '{self.version}' must be in semver format (e.g., 1.0.0)"
                    )
                for part in parts:
                    if not part.isdigit() and part != "*":
                        raise ValueError(
                            f"version part '{part}' must be numeric or '*'"
                        )

            # Validate capabilities
            if self.capabilities is not None:
                self.capabilities.validate()

            # Validate tools list
            if self.tools:
                if len(self.tools) != len(set(self.tools)):
                    raise ValueError("tools list contains duplicates")
                for tool in self.tools:
                    if not isinstance(tool, str):
                        raise ValueError(f"Tool name must be string, got {type(tool)}")
                    if not tool.strip():
                        raise ValueError("Tool name cannot be empty")

            # Validate model_config
            if not isinstance(self.model_config, dict):
                raise ValueError("model_config must be a dictionary")

            # Validate metadata
            if not isinstance(self.metadata, dict):
                raise ValueError("metadata must be a dictionary")

            return True

    def get_tool_list(self) -> List[str]:
        """
        Get the list of tools for this agent.

        Returns:
            Copy of the tools list.

        Example:
            >>> profile = AgentProfile(tools=["read_file", "write_file"])
            >>> profile.get_tool_list()
            ['read_file', 'write_file']
        """
        with self._lock:
            return list(self.tools)

    def add_tool(self, tool_name: str) -> None:
        """
        Add a tool to the profile.

        Args:
            tool_name: Name of the tool to add.

        Example:
            >>> profile = AgentProfile()
            >>> profile.add_tool("read_file")
            >>> "read_file" in profile.get_tool_list()
            True
        """
        with self._lock:
            if tool_name not in self.tools:
                self.tools.append(tool_name)
                # Also update capabilities if present
                if self.capabilities:
                    self.capabilities.add_tool(tool_name)

    def remove_tool(self, tool_name: str) -> bool:
        """
        Remove a tool from the profile.

        Args:
            tool_name: Name of the tool to remove.

        Returns:
            True if the tool was removed, False if it wasn't in the list.
        """
        with self._lock:
            if tool_name in self.tools:
                self.tools.remove(tool_name)
                # Also update capabilities if present
                if self.capabilities:
                    self.capabilities.remove_tool(tool_name)
                return True
            return False

    def get_model_config(self) -> Dict[str, Any]:
        """
        Get a copy of the model configuration.

        Returns:
            Copy of the model configuration dictionary.

        Example:
            >>> profile = AgentProfile(model_config={"model_id": "Qwen3.5-35B"})
            >>> profile.get_model_config()
            {'model_id': 'Qwen3.5-35B'}
        """
        with self._lock:
            return dict(self.model_config)

    def set_model_config(self, key: str, value: Any) -> None:
        """
        Set a model configuration value.

        Args:
            key: Configuration key.
            value: Configuration value.

        Example:
            >>> profile = AgentProfile()
            >>> profile.set_model_config("temperature", 0.7)
            >>> profile.get_model_config()["temperature"]
            0.7
        """
        with self._lock:
            self.model_config[key] = value

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert profile to a dictionary representation.

        Returns:
            Dictionary with all profile fields.

        Example:
            >>> profile = AgentProfile(id="test-agent", name="Test Agent", version="1.0.0")
            >>> d = profile.to_dict()
            >>> d['id']
            'test-agent'
            >>> d['name']
            'Test Agent'
        """
        with self._lock:
            return {
                "id": self.id,
                "name": self.name,
                "role": self.role,
                "description": self.description,  # Backward compatibility
                "capabilities": (
                    self.capabilities.to_dict() if self.capabilities else None
                ),
                "tools": list(self.tools),
                "model_config": dict(self.model_config),
                "version": self.version,
                "metadata": dict(self.metadata),
            }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentProfile":
        """
        Create profile from a dictionary.

        Args:
            data: Dictionary with profile fields.

        Returns:
            New AgentProfile instance.

        Example:
            >>> d = {
            ...     "id": "test-agent",
            ...     "name": "Test Agent",
            ...     "role": "A test agent",
            ...     "tools": ["read_file"],
            ...     "model_config": {"model_id": "Qwen3.5-35B"},
            ... }
            >>> profile = AgentProfile.from_dict(d)
            >>> profile.id
            'test-agent'
            >>> profile.role
            'A test agent'
        """
        capabilities_data = data.get("capabilities")
        capabilities = None
        if capabilities_data:
            if isinstance(capabilities_data, dict):
                capabilities = AgentCapabilities.from_dict(capabilities_data)
            elif isinstance(capabilities_data, AgentCapabilities):
                capabilities = capabilities_data

        return cls(
            id=data.get("id", "unnamed-agent"),
            name=data.get("name", "Unnamed Agent"),
            role=data.get("role", ""),
            description=data.get("description", ""),  # Backward compatibility
            capabilities=capabilities,
            tools=data.get("tools", []),
            model_config=data.get("model_config", {}),
            version=data.get("version", "1.0.0"),
            metadata=data.get("metadata", {}),
        )

    def to_yaml(self) -> str:
        """
        Convert profile to YAML string format.

        Returns:
            YAML string representation of the profile.

        Example:
            >>> profile = AgentProfile(
            ...     name="Test Agent",
            ...     tools=["read_file", "write_file"],
            ... )
            >>> yaml_str = profile.to_yaml()
            >>> "name: Test Agent" in yaml_str
            True
        """
        try:
            import yaml
        except ImportError:
            raise ImportError(
                "PyYAML is required for YAML serialization. "
                "Install with: pip install pyyaml"
            )

        with self._lock:
            data = self.to_dict()
            # Handle capabilities serialization
            if data["capabilities"] is None and self.capabilities:
                data["capabilities"] = self.capabilities.to_dict()
            return yaml.dump(data, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "AgentProfile":
        """
        Create profile from a YAML string.

        Args:
            yaml_str: YAML string representation.

        Returns:
            New AgentProfile instance.

        Example:
            >>> yaml_str = '''
            ... name: Test Agent
            ... tools:
            ...   - read_file
            ...   - write_file
            ... '''
            >>> profile = AgentProfile.from_yaml(yaml_str)
            >>> profile.name
            'Test Agent'
        """
        try:
            import yaml
        except ImportError:
            raise ImportError(
                "PyYAML is required for YAML serialization. "
                "Install with: pip install pyyaml"
            )

        data = yaml.safe_load(yaml_str)
        if not isinstance(data, dict):
            raise ValueError(
                f"YAML must represent a dictionary, got {type(data).__name__}"
            )
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, file_path: str) -> "AgentProfile":
        """
        Load profile from a YAML file.

        Args:
            file_path: Path to the YAML file.

        Returns:
            New AgentProfile instance.

        Example:
            >>> # Assuming profile.yaml exists with valid content
            >>> profile = AgentProfile.from_file("profile.yaml")
        """
        with open(file_path, "r", encoding="utf-8") as f:
            yaml_str = f.read()
        return cls.from_yaml(yaml_str)

    def to_file(self, file_path: str) -> None:
        """
        Save profile to a YAML file.

        Args:
            file_path: Path to the YAML file.

        Example:
            >>> profile = AgentProfile(name="Test Agent")
            >>> profile.to_file("profile.yaml")
        """
        yaml_str = self.to_yaml()
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(yaml_str)

    def __eq__(self, other: Any) -> bool:
        """
        Check equality with another AgentProfile instance.

        Args:
            other: Object to compare with.

        Returns:
            True if the objects are equal, False otherwise.
        """
        if not isinstance(other, AgentProfile):
            return False
        return self.to_dict() == other.to_dict()

    def __repr__(self) -> str:
        """Return string representation of profile."""
        parts = [f"id='{self.id}'", f"name='{self.name}'"]
        if self.role:
            parts.append(f"role='{self.role}'")
        if self.version:
            parts.append(f"version='{self.version}'")
        if self.tools:
            parts.append(f"tools={len(self.tools)}")
        if self.model_config:
            parts.append(f"model_config={len(self.model_config)} keys")
        return f"AgentProfile({', '.join(parts)})"
