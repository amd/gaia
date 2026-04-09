# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Agent Adapter for backward compatibility.

This module provides the AgentAdapter class, which wraps legacy Agent
instances to provide a unified interface with new AgentExecutor-based agents.

Migration Path:
    Legacy Agent -> AgentAdapter -> AgentExecutor Pattern

The adapter allows gradual migration:
    1. Continue using legacy agents as-is
    2. Wrap with AgentAdapter for unified interface
    3. Migrate to AgentProfile + AgentExecutor pattern

Example:
    >>> legacy_agent = CodeAgent(debug=True)
    >>> adapter = AgentAdapter(legacy_agent)
    >>> response = await adapter.run_step("Build API", context={})
"""

import logging
from typing import Any, Dict, List, Optional

from gaia.core.profile import AgentProfile
from gaia.core.capabilities import AgentCapabilities
from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class AgentAdapter:
    """
    Adapter for legacy Agent instances.

    Wraps a legacy Agent subclass to provide compatibility with the
    new AgentExecutor pattern. The adapter extracts an AgentProfile
    from the legacy agent and delegates execution to it.

    Attributes:
        legacy_agent: The wrapped Agent instance
        profile: Extracted AgentProfile

    Example:
        >>> from gaia.agents.code.agent import CodeAgent
        >>> legacy = CodeAgent(max_steps=10)
        >>> adapter = AgentAdapter(legacy)
        >>> print(adapter.profile.id)
        'code-agent'
    """

    def __init__(self, legacy_agent: Any):
        """
        Initialize adapter with legacy agent.

        Args:
            legacy_agent: Legacy Agent instance to wrap

        Raises:
            ValueError: If legacy_agent is None or invalid

        Example:
            >>> adapter = AgentAdapter(CodeAgent())
            >>> print(adapter)
            AgentAdapter(CodeAgent, id=code-agent)
        """
        if legacy_agent is None:
            raise ValueError("legacy_agent cannot be None")

        self.legacy_agent = legacy_agent
        self.profile = self._extract_profile(legacy_agent)

        logger.info(f"Created AgentAdapter for {legacy_agent.__class__.__name__}")

    def _extract_profile(self, agent: Any) -> AgentProfile:
        """
        Extract AgentProfile from legacy agent.

        This method reads configuration from the legacy agent's
        attributes and constructs an AgentProfile.

        Args:
            agent: Legacy Agent instance

        Returns:
            AgentProfile with extracted configuration

        Example:
            >>> agent = CodeAgent(model_id="Qwen3.5-35B", max_steps=15)
            >>> adapter = AgentAdapter(agent)
            >>> profile = adapter.profile
            >>> print(profile.model_config.get("model_id"))
            Qwen3.5-35B
            >>> print(profile.max_steps)
            15
        """
        # Extract identity
        agent_id = getattr(agent, 'agent_id', None)
        if not agent_id:
            agent_id = getattr(agent, 'id', agent.__class__.__name__.lower())

        name = getattr(agent, 'name', agent.__class__.__name__)

        # Extract role/description
        role = getattr(agent, 'role', '')
        if not role:
            role = getattr(agent, 'description', f"{name} Agent")

        # Extract system prompt - store in metadata since AgentProfile doesn't have this field
        system_prompt = getattr(agent, 'system_prompt', '')
        if not system_prompt:
            system_prompt = getattr(agent, 'prompt_template', '')

        # Extract model configuration
        model_id = getattr(agent, 'model_id', None)
        if not model_id:
            model_id = getattr(agent, 'model', 'Qwen3.5-35B-A3B-GGUF')

        model_config = {
            'model_id': model_id,
        }

        # Add additional model settings if present
        for attr in ['temperature', 'max_tokens', 'top_p', 'frequency_penalty']:
            value = getattr(agent, attr, None)
            if value is not None:
                model_config[attr] = value

        # Extract tools
        tools = getattr(agent, 'allowed_tools', [])
        if not tools:
            tools = getattr(agent, 'tools', [])
        tools = list(tools) if tools else []

        # Extract capabilities
        capabilities = AgentCapabilities()

        # Map legacy capability flags
        capability_mappings = {
            'supports_code_execution': 'supports_code_execution',
            'supports_vision': 'supports_vision',
            'supports_audio': 'supports_audio',
            'has_internet_access': 'internet_access',
        }

        for legacy_attr, cap_field in capability_mappings.items():
            value = getattr(agent, legacy_attr, None)
            if value is not None:
                setattr(capabilities, cap_field, value)

        # Build profile - AgentProfile has: id, name, role, description, capabilities, tools, model_config, version, metadata
        profile = AgentProfile(
            id=agent_id,
            name=name,
            role=role,
            description=system_prompt,  # Store system prompt in description field
            capabilities=capabilities,
            tools=tools,
            model_config=model_config,
            metadata={
                'system_prompt': system_prompt,
                'max_steps': getattr(agent, 'max_steps', 20),
                'max_plan_iterations': getattr(agent, 'max_plan_iterations', 3),
            }
        )

        logger.debug(f"Extracted profile from {agent.__class__.__name__}: {profile.id}")
        return profile

    def get_profile(self) -> AgentProfile:
        """
        Get the agent profile.

        Returns:
            AgentProfile instance

        Example:
            >>> adapter = AgentAdapter(CodeAgent())
            >>> profile = adapter.get_profile()
            >>> print(profile.id)
            code-agent
        """
        return self.profile

    def get_legacy_agent(self) -> Any:
        """
        Get the wrapped legacy agent.

        Returns:
            Legacy Agent instance

        Example:
            >>> adapter = AgentAdapter(my_agent)
            >>> assert adapter.get_legacy_agent() is my_agent
        """
        return self.legacy_agent

    async def run_step(
        self,
        topic: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run single agent step (delegates to legacy agent).

        Args:
            topic: Current topic/task
            context: Execution context

        Returns:
            Agent response dictionary

        Raises:
            AttributeError: If legacy agent has no run_step or run method

        Example:
            >>> adapter = AgentAdapter(CodeAgent())
            >>> response = await adapter.run_step("Create API", {"files": [...]})
        """
        context = context or {}

        # Delegate to legacy agent's run_step
        if hasattr(self.legacy_agent, 'run_step'):
            return await self.legacy_agent.run_step(topic, context)

        # Fall back to run method if run_step not available
        if hasattr(self.legacy_agent, 'run'):
            return await self.legacy_agent.run(topic, context)

        raise AttributeError(
            "Legacy agent must have run_step() or run() method"
        )

    async def run(
        self,
        topic: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run agent (alias for run_step).

        Args:
            topic: Current topic/task
            context: Execution context

        Returns:
            Agent response dictionary

        Example:
            >>> adapter = AgentAdapter(CodeAgent())
            >>> response = await adapter.run("Create API", {"files": [...]})
        """
        return await self.run_step(topic, context)

    def __getattr__(self, name: str) -> Any:
        """
        Delegate unknown attributes to legacy agent.

        This allows the adapter to be used as a drop-in replacement
        for the legacy agent.

        Args:
            name: Attribute name

        Returns:
            Attribute value from legacy agent

        Example:
            >>> agent = CodeAgent(debug=True)
            >>> adapter = AgentAdapter(agent)
            >>> print(adapter.debug)  # Delegated to legacy agent
            True
        """
        return getattr(self.legacy_agent, name)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"AgentAdapter({self.legacy_agent.__class__.__name__}, id={self.profile.id})"


class _AttributeDelegator:
    """
    Internal class for attribute delegation.

    This class provides a descriptor-based approach to delegating
    attribute access to a wrapped object. Used internally by
    LegacyAgentWrapper for efficient multi-agent management.

    Attributes:
        _target: The target object to delegate to

    Example:
        >>> delegator = _AttributeDelegator(some_object)
        >>> value = delegator.some_attribute  # Delegated
    """

    def __init__(self, target: Any):
        """Initialize delegator with target object."""
        self._target = target

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to target."""
        return getattr(self._target, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Delegate attribute assignment to target."""
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            setattr(self._target, name, value)


class LegacyAgentWrapper:
    """
    Wrapper for managing multiple legacy agents.

    This class can wrap multiple legacy agents and route
    requests based on agent selection. It provides a unified
    interface for working with multiple agents.

    Attributes:
        _agents: Dictionary of agent adapters
        _default: Default agent name for fallback

    Example:
        >>> wrapper = LegacyAgentWrapper()
        >>> wrapper.add_agent("code", CodeAgent())
        >>> wrapper.add_agent("chat", ChatAgent())
        >>> response = await wrapper.run_agent("code", "Build API")
    """

    def __init__(self):
        """Initialize wrapper with empty agent registry."""
        self._agents: Dict[str, AgentAdapter] = {}
        self._default: Optional[str] = None

    def add_agent(
        self,
        name: str,
        agent: Any,
        set_default: bool = False,
    ) -> "LegacyAgentWrapper":
        """
        Add an agent to the wrapper.

        Args:
            name: Agent name/identifier
            agent: Legacy Agent instance
            set_default: Set as default agent for fallback

        Returns:
            Self for method chaining

        Raises:
            ValueError: If agent is None

        Example:
            >>> wrapper = LegacyAgentWrapper()
            >>> wrapper.add_agent("code", CodeAgent()).add_agent("chat", ChatAgent())
        """
        if agent is None:
            raise ValueError(f"Agent '{name}' cannot be None")

        adapter = AgentAdapter(agent)
        self._agents[name] = adapter

        if set_default or not self._default:
            self._default = name

        logger.info(f"Added agent '{name}' to wrapper")
        return self

    def get_agent(self, name: str) -> AgentAdapter:
        """
        Get agent by name.

        Args:
            name: Agent name

        Returns:
            AgentAdapter instance

        Raises:
            KeyError: If agent not found and no default set

        Example:
            >>> wrapper.add_agent("code", CodeAgent())
            >>> agent = wrapper.get_agent("code")
            >>> print(agent.profile.id)
            code-agent
        """
        if name not in self._agents:
            if self._default:
                logger.debug(f"Agent '{name}' not found, using default '{self._default}'")
                return self._agents[self._default]
            raise KeyError(f"Agent '{name}' not found")
        return self._agents[name]

    async def run_agent(
        self,
        name: str,
        topic: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run specified agent.

        Args:
            name: Agent name (or use default if None)
            topic: Topic/task to run
            context: Execution context

        Returns:
            Agent response dictionary

        Example:
            >>> wrapper.add_agent("code", CodeAgent())
            >>> response = await wrapper.run_agent("code", "Build API", {})
        """
        agent = self.get_agent(name)
        return await agent.run_step(topic, context)

    def list_agents(self) -> List[str]:
        """
        List all registered agent names.

        Returns:
            List of agent names

        Example:
            >>> wrapper.add_agent("code", CodeAgent()).add_agent("chat", ChatAgent())
            >>> names = wrapper.list_agents()
            >>> print(names)
            ['code', 'chat']
        """
        return list(self._agents.keys())

    def get_default_agent(self) -> Optional[AgentAdapter]:
        """
        Get the default agent.

        Returns:
            Default AgentAdapter or None if no default set

        Example:
            >>> wrapper.add_agent("code", CodeAgent(), set_default=True)
            >>> default = wrapper.get_default_agent()
        """
        if self._default:
            return self._agents[self._default]
        return None

    def set_default_agent(self, name: str) -> bool:
        """
        Set the default agent.

        Args:
            name: Agent name to set as default

        Returns:
            True if agent was set, False if not found

        Example:
            >>> wrapper.add_agent("code", CodeAgent()).add_agent("chat", ChatAgent())
            >>> wrapper.set_default_agent("code")
        """
        if name in self._agents:
            self._default = name
            logger.info(f"Set default agent to '{name}'")
            return True
        return False

    def remove_agent(self, name: str) -> bool:
        """
        Remove an agent from the wrapper.

        Args:
            name: Agent name to remove

        Returns:
            True if agent was removed, False if not found

        Example:
            >>> wrapper.add_agent("code", CodeAgent())
            >>> wrapper.remove_agent("code")
        """
        if name in self._agents:
            del self._agents[name]
            if self._default == name:
                self._default = list(self._agents.keys())[0] if self._agents else None
            logger.info(f"Removed agent '{name}'")
            return True
        return False

    def get_agent_count(self) -> int:
        """
        Get the number of registered agents.

        Returns:
            Number of agents

        Example:
            >>> wrapper.add_agent("code", CodeAgent()).add_agent("chat", ChatAgent())
            >>> print(wrapper.get_agent_count())
            2
        """
        return len(self._agents)

    def __repr__(self) -> str:
        """Return string representation."""
        agents = list(self._agents.keys())
        default_info = f", default={self._default}" if self._default else ""
        return f"LegacyAgentWrapper(agents={agents}{default_info})"


# Module version
__version__ = "1.0.0"


def get_version() -> str:
    """Return the module version."""
    return __version__


def extract_profile(agent: Any) -> AgentProfile:
    """
    Convenience function to extract profile from an agent.

    This is a shortcut for creating an AgentAdapter and getting
    the profile, useful when you only need the profile.

    Args:
        agent: Legacy Agent instance

    Returns:
        Extracted AgentProfile

    Example:
        >>> profile = extract_profile(CodeAgent())
        >>> print(profile.id)
        code-agent
    """
    adapter = AgentAdapter(agent)
    return adapter.get_profile()
