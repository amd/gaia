# Phase 3 Technical Specification: Architectural Modernization

**Document Version:** 1.0
**Date:** 2026-04-06
**Status:** READY FOR IMPLEMENTATION
**Duration:** 12 weeks (4 Sprints)
**Owner:** senior-developer
**Classification:** Strategic Architecture Document

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Modular Architecture Specification](#2-modular-architecture-specification)
3. [Performance Optimization Specification](#3-performance-optimization-specification)
4. [Enterprise Readiness Specification](#4-enterprise-readiness-specification)
5. [API Standardization Specification](#5-api-standardization-specification)
6. [Integration Strategy](#6-integration-strategy)
7. [Migration Guide](#7-migration-guide)
8. [Quality Assurance](#8-quality-assurance)
9. [Appendix: Code Examples](#9-appendix-code-examples)

---

## 1. Executive Summary

### 1.1 Phase 3 Objectives

Phase 3 (Architectural Modernization) transforms the GAIA framework from a research-oriented prototype into a production-ready enterprise platform. This specification details the technical implementation of four focus areas:

| Focus Area | Objective | Key Deliverables |
|------------|-----------|------------------|
| **Modular Architecture** | Decouple monolithic components | AgentProfile, AgentExecutor, PluginRegistry, DI Container |
| **Performance Optimization** | Standardize async patterns, add caching | AsyncUtils, ConnectionPool, CacheLayer, RateLimiter |
| **Enterprise Readiness** | Production-ready configuration | ConfigSchema, SecretsManager, ObservabilityCore |
| **API Standardization** | OpenAPI compliance, versioning | OpenAPISpec, APIVersioning, DeprecationLayer |

### 1.2 Current State Assessment

**Before Phase 3:**

| Component | Issue | Impact |
|-----------|-------|--------|
| `Agent` base class | 3,000+ lines, 40+ parameters | High complexity, difficult to extend |
| Mixin composition | 10-15 class inheritance depth | Fragile, hard to debug |
| LLM client creation | New instance per agent | Connection overhead, no pooling |
| Configuration | Ad-hoc YAML/ENV mixing | Inconsistent validation, secrets in code |
| API documentation | Manual, drifts from implementation | Outdated docs, integration friction |
| Observability | Basic logging only | No metrics, tracing, or alerting |

### 1.3 Target State

**After Phase 3:**

| Component | Improvement | Benefit |
|-----------|-------------|---------|
| `AgentProfile` | Pure data, ~200 LOC | Simple, testable, serializable |
| `AgentExecutor` | Behavior injection via composition | Flexible, reusable, testable |
| `PluginRegistry` | Plugin system for extensions | Extensible without code changes |
| `DIContainer` | Dependency injection | Testable, swappable implementations |
| `ConnectionPool` | Reusable LLM connections | 10x throughput improvement |
| `CacheLayer` | Multi-layer caching | 80%+ hit rate for repeated calls |
| `ConfigSchema` | Pydantic validation | 100% invalid configs rejected |
| `SecretsManager` | Multi-backend secrets | Zero secrets in code |
| `OpenAPISpec` | Auto-generated from code | Always in sync with implementation |

### 1.4 Program Context

```
Program Progress: 75% Complete
├── Phase 0: Tool Scoping        ████████████████████ 100% COMPLETE
├── Phase 1: State Unification   ████████████████████ 100% COMPLETE
├── Phase 2: Quality Enhancement ████████████████████ 100% COMPLETE
├── Phase 3: Architectural Mod   ░░░░░░░░░░░░░░░░░░░░   0% PLANNED
└── Phase 4: (Future)            ░░░░░░░░░░░░░░░░░░░░   0% FUTURE
```

---

## 2. Modular Architecture Specification

### 2.1 Agent-as-Data Pattern

#### 2.1.1 Problem Statement

**Current Implementation (Pre-Phase 3):**

```python
# src/gaia/agents/base/agent.py - Lines 84-200 (excerpt)
class Agent(abc.ABC):
    def __init__(
        self,
        use_claude: bool = False,
        use_chatgpt: bool = False,
        claude_model: str = "claude-sonnet-4-20250514",
        base_url: Optional[str] = None,
        model_id: str = None,
        max_steps: int = 20,
        debug_prompts: bool = False,
        show_prompts: bool = False,
        output_dir: str = None,
        streaming: bool = False,
        show_stats: bool = False,
        silent_mode: bool = False,
        debug: bool = False,
        output_handler=None,
        max_plan_iterations: int = 3,
        # ... 25+ more parameters
        **kwargs
    ):
        # 3,000+ lines mixing configuration and behavior
        self.agent_id = kwargs.get('agent_id')
        self.model_id = model_id
        self.max_steps = max_steps
        # ... initialization logic mixed with behavior setup
```

**Problems:**
1. **Constructor bloat:** 40+ parameters make initialization error-prone
2. **Inheritance explosion:** Subclasses add more parameters, deeper inheritance
3. **Configuration/behavior coupling:** Can't change behavior without modifying class
4. **Testing difficulty:** Must instantiate full class to test configuration
5. **Serialization complexity:** Can't easily save/load agent configurations

#### 2.1.2 Solution: AgentProfile Dataclass

**Implementation:**

```python
# src/gaia/core/profile.py
"""
Agent-as-Data: Pure configuration without behavior.

This module defines AgentProfile, a dataclass that contains ONLY
configuration data. All behavior is provided by AgentExecutor.

Design Principles:
1. Configuration is data, not code
2. Validation happens at construction
3. Profiles are serializable (JSON/YAML)
4. Immutable after creation (frozen=True)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal
import yaml
import json
from pathlib import Path


@dataclass(frozen=True)
class AgentProfile:
    """
    Pure-data agent configuration.

    This class contains ONLY configuration data.
    All behavior is injected by AgentExecutor.

    Attributes:
        id: Unique agent identifier (required)
        name: Human-readable agent name (required)
        role: Agent role description (required)
        system_prompt: System prompt template (required)
        model: Model identifier (default: Qwen3.5-35B-A3B-GGUF)
        tools: List of allowed tool names
        capabilities: Capability flags dict
        constraints: Operational constraints dict
        knowledge_base: Optional knowledge base paths
        max_steps: Maximum execution steps (default: 20)
        max_plan_iterations: Maximum planning iterations (default: 3)
        metadata: Additional metadata dict

    Example:
        >>> profile = AgentProfile(
        ...     id="code-agent",
        ...     name="Code Generation Agent",
        ...     role="Expert software developer",
        ...     system_prompt="You are an expert developer...",
        ...     tools=["read_file", "write_file", "execute_python"],
        ...     model="Qwen3.5-35B-A3B-GGUF",
        ... )
        >>> print(profile.id)
        'code-agent'
    """

    # Required fields
    id: str
    name: str
    role: str
    system_prompt: str

    # Optional fields with defaults
    model: str = "Qwen3.5-35B-A3B-GGUF"
    tools: List[str] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    knowledge_base: Optional[List[str]] = None
    max_steps: int = 20
    max_plan_iterations: int = 3
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate profile after initialization."""
        # Validation runs automatically after __init__
        self._validate()

    def _validate(self):
        """
        Validate profile configuration.

        Raises:
            ValueError: If profile is invalid
        """
        errors = []

        # Required field validation
        if not self.id or not self.id.strip():
            errors.append("Agent id is required and cannot be empty")
        if not self.name or not self.name.strip():
            errors.append("Agent name is required and cannot be empty")
        if not self.system_prompt or not self.system_prompt.strip():
            errors.append("System prompt is required and cannot be empty")
        if not self.role or not self.role.strip():
            errors.append("Role is required and cannot be empty")

        # Numeric validation
        if self.max_steps < 1:
            errors.append("max_steps must be positive")
        if self.max_plan_iterations < 1:
            errors.append("max_plan_iterations must be positive")

        # Tool list validation
        if not isinstance(self.tools, list):
            errors.append("tools must be a list")
        elif not all(isinstance(t, str) for t in self.tools):
            errors.append("all tools must be strings")

        if errors:
            raise ValueError("; ".join(errors))

    def to_dict(self) -> Dict[str, Any]:
        """Convert profile to dictionary."""
        from dataclasses import asdict
        return asdict(self)

    def to_yaml(self) -> str:
        """Convert profile to YAML string."""
        return yaml.dump(self.to_dict(), default_flow_style=False)

    def to_json(self, indent: int = 2) -> str:
        """Convert profile to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentProfile":
        """
        Create profile from dictionary.

        Args:
            data: Dictionary with profile data

        Returns:
            AgentProfile instance
        """
        return cls(**data)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "AgentProfile":
        """
        Create profile from YAML string.

        Args:
            yaml_str: YAML string

        Returns:
            AgentProfile instance
        """
        data = yaml.safe_load(yaml_str)
        return cls.from_dict(data)

    @classmethod
    def from_file(cls, path: Path) -> "AgentProfile":
        """
        Load profile from YAML file.

        Args:
            path: Path to YAML file

        Returns:
            AgentProfile instance
        """
        with open(path, 'r') as f:
            return cls.from_yaml(f.read())

    def with_updates(self, **updates) -> "AgentProfile":
        """
        Create new profile with updates (immutable).

        Args:
            **updates: Fields to update

        Returns:
            New AgentProfile with updates applied
        """
        from dataclasses import replace
        return replace(self, **updates)
```

### 2.2 AgentExecutor Pattern

#### 2.2.1 Design Rationale

The AgentExecutor implements the **Strategy Pattern** combined with **Dependency Injection** to provide behavior that was previously embedded in the Agent class hierarchy.

**Key Responsibilities:**
1. LLM client interaction
2. Tool execution through scoped access
3. Context formatting and prompt building
4. State management
5. Error handling and recovery
6. Chronicle integration

#### 2.2.2 Implementation

```python
# src/gaia/core/executor.py
"""
AgentExecutor: Behavior injection engine.

This class provides all execution behavior for an AgentProfile.
It uses composition over inheritance, accepting a profile (data)
and injecting behavior through method execution.

Architecture:
┌─────────────────┐
│  AgentProfile   │  ← Pure data (configuration)
└────────┬────────┘
         │
         │ "has-a"
         ▼
┌─────────────────┐
│ AgentExecutor   │  ← Behavior injection
│ - llm_client    │
│ - tool_scope    │
│ - nexus         │
│ - di_container  │
└────────┬────────┘
         │
         │ delegates to
         ▼
┌─────────────────┐
│  LLM Client     │  ← Actual LLM interaction
│  Tool Scope     │  ← Tool execution
│  State Service  │  ← State management
└─────────────────┘

Example:
    >>> profile = AgentProfile(id="code-agent", ...)
    >>> executor = AgentExecutor(profile, di_container)
    >>> response = await executor.run_step(
    ...     topic="Build a REST API",
    ...     context={"files": [...]}
    ... )
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from gaia.core.profile import AgentProfile
from gaia.core.di import DIContainer
from gaia.state.nexus import NexusService
from gaia.llm.base_client import BaseLLMClient
from gaia.agents.base.tools import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    """
    Execution context for agent step.

    Contains all state needed for a single execution step.
    """
    topic: str
    context: Dict[str, Any]
    state: Dict[str, Any]
    step_number: int
    loop_id: Optional[str] = None


class AgentExecutor:
    """
    Agent execution engine with injected behavior.

    The executor accepts an AgentProfile (data) and provides
    all execution behavior through composition.

    Thread Safety:
        - Uses asyncio.Lock for async operations
        - State is isolated per executor instance
        - Tool scope is thread-safe via ToolRegistry

    Example:
        >>> profile = AgentProfile(
        ...     id="code-agent",
        ...     name="Code Agent",
        ...     role="Developer",
        ...     system_prompt="You are an expert...",
        ...     tools=["read_file", "write_file"],
        ... )
        >>> di = DIContainer()
        >>> executor = AgentExecutor(profile, di)
        >>> result = await executor.run_step("Create API", {})
    """

    def __init__(
        self,
        profile: AgentProfile,
        di_container: DIContainer,
        nexus: Optional[NexusService] = None,
        state: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize executor with profile and dependencies.

        Args:
            profile: Agent configuration
            di_container: Dependency injection container
            nexus: Optional state service (default: singleton)
            state: Optional initial state
        """
        self.profile = profile
        self.di_container = di_container
        self.nexus = nexus or NexusService.get_instance()
        self._state = state or {}
        self._step_count = 0
        self._lock = asyncio.Lock()
        self._loop_id: Optional[str] = None

        # Lazy initialization
        self._llm_client: Optional[BaseLLMClient] = None
        self._tool_scope = None

    @property
    def llm_client(self) -> BaseLLMClient:
        """Get LLM client (lazy initialization)."""
        if self._llm_client is None:
            self._llm_client = self.di_container.get_llm_client(
                self.profile.model
            )
        return self._llm_client

    @property
    def tool_scope(self):
        """Get scoped tool access."""
        if self._tool_scope is None:
            self._tool_scope = ToolRegistry.get_instance().create_scope(
                agent_id=self.profile.id,
                allowed_tools=self.profile.tools,
            )
        return self._tool_scope

    async def run_step(
        self,
        topic: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute single agent step.

        This is the main entry point for agent execution. It:
        1. Gets curated context from state service
        2. Builds system prompt from profile
        3. Invokes LLM with tools
        4. Executes any tool calls
        5. Commits result to Chronicle

        Args:
            topic: Current topic/task
            context: Execution context (files, history, etc.)

        Returns:
            Agent response dictionary with:
            - content: Response text
            - tool_calls: List of tool invocations
            - tool_results: Results after execution

        Raises:
            MaxStepsExceeded: If max_steps limit reached
            ToolAccessDeniedError: If tool not in allowlist
        """
        async with self._lock:
            # Check step limit
            if self._step_count >= self.profile.max_steps:
                raise MaxStepsExceeded(
                    f"Maximum steps ({self.profile.max_steps}) exceeded"
                )

            self._step_count += 1
            self._loop_id = self._loop_id or str(uuid.uuid4())

            # Build execution context
            exec_context = ExecutionContext(
                topic=topic,
                context=context,
                state=self._state,
                step_number=self._step_count,
                loop_id=self._loop_id,
            )

            # Get curated context from Nexus
            curated = self.nexus.get_context_for_agent(self.profile.id)

            # Build system prompt
            system_prompt = self._build_system_prompt()

            # Build user message
            user_message = self._build_user_message(exec_context, curated)

            # Get available tools for LLM
            available_tools = self._get_available_tools()

            # Invoke LLM
            response = await self.llm_client.chat(
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                tools=available_tools,
            )

            # Execute tool calls if present
            tool_results = []
            if response.tool_calls:
                tool_results = await self._execute_tools(
                    response.tool_calls,
                    exec_context,
                )

            # Build result
            result = {
                "content": response.content,
                "tool_calls": response.tool_calls,
                "tool_results": tool_results,
                "step": self._step_count,
                "loop_id": self._loop_id,
            }

            # Commit to Chronicle
            self.nexus.commit(
                agent_id=self.profile.id,
                event_type="THOUGHT",
                payload=result,
            )

            return result

    def _build_system_prompt(self) -> str:
        """
        Build system prompt from profile.

        Incorporates:
        - Base system prompt from profile
        - Constraints from profile
        - Capability declarations
        """
        parts = [self.profile.system_prompt]

        # Add constraints
        if self.profile.constraints:
            parts.append("\n## Constraints")
            for key, value in self.profile.constraints.items():
                parts.append(f"- {key}: {value}")

        # Add capabilities
        if self.profile.capabilities:
            parts.append("\n## Capabilities")
            for key, value in self.profile.capabilities.items():
                parts.append(f"- {key}: {value}")

        # Add tool list
        if self.profile.tools:
            parts.append("\n## Available Tools")
            parts.append(f"You have access to: {', '.join(self.profile.tools)}")

        return "\n".join(parts)

    def _build_user_message(
        self,
        context: ExecutionContext,
        curated: Dict[str, Any],
    ) -> str:
        """Build user message from context and curated state."""
        parts = [f"Topic: {context.topic}"]

        # Add curated context
        if curated.get('chronicle_digest'):
            parts.append(f"\n## Recent History\n{curated['chronicle_digest']}")

        if curated.get('relevant_files'):
            parts.append(f"\n## Relevant Files\n{curated['relevant_files']}")

        # Add execution context
        parts.append(f"\n## Current Step\nStep {context.step_number}")

        return "\n".join(parts)

    def _get_available_tools(self) -> List[Dict[str, Any]]:
        """Get tool definitions for LLM."""
        # Get tools from tool scope
        tools = []
        for tool_name in self.profile.tools:
            try:
                tool_def = self.tool_scope.get_tool_definition(tool_name)
                tools.append(tool_def)
            except KeyError:
                logger.warning(f"Tool {tool_name} not found")
        return tools

    async def _execute_tools(
        self,
        tool_calls: List[Dict[str, Any]],
        context: ExecutionContext,
    ) -> List[Dict[str, Any]]:
        """Execute tool calls and return results."""
        results = []
        for call in tool_calls:
            try:
                result = await self.tool_scope.execute_tool(
                    call['name'],
                    *call.get('args', []),
                    **call.get('kwargs', {}),
                )
                results.append({
                    "tool_name": call['name'],
                    "result": result,
                    "success": True,
                })
            except Exception as e:
                results.append({
                    "tool_name": call['name'],
                    "error": str(e),
                    "success": False,
                })
        return results

    def get_state(self) -> Dict[str, Any]:
        """Get current state (copy for mutation safety)."""
        import copy
        return copy.deepcopy(self._state)

    def update_state(self, updates: Dict[str, Any]):
        """Update state with new values."""
        self._state.update(updates)

    def reset(self):
        """Reset executor state."""
        self._step_count = 0
        self._state = {}
        self._loop_id = None


class MaxStepsExceeded(Exception):
    """Raised when max_steps limit is exceeded."""
    pass
```

### 2.3 Plugin System

#### 2.3.1 Plugin Architecture

```python
# src/gaia/core/plugins.py
"""
Plugin system for extending GAIA without code changes.

The plugin registry allows loading extensions at runtime.
Plugins can:
- Register new tools
- Add middleware to agent execution
- Provide custom LLM clients
- Extend configuration schemas

Plugin Lifecycle:
1. Discovery (entry points or directory scan)
2. Loading (import plugin module)
3. Registration (register tools, middleware)
4. Activation (plugin becomes available)
5. Cleanup (on shutdown)

Example Plugin:
    # my_plugin.py
    from gaia.core.plugins import Plugin

    class MyPlugin(Plugin):
        name = "my-plugin"
        version = "1.0.0"

        def register(self):
            # Register tools
            register_tool(my_custom_tool)

        def unregister(self):
            # Cleanup
            pass
"""

import importlib
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type
from pathlib import Path

logger = logging.getLogger(__name__)


class Plugin(ABC):
    """
    Base class for GAIA plugins.

    Plugins extend GAIA functionality without modifying core code.
    """

    name: str
    version: str
    description: str = ""

    @abstractmethod
    def register(self):
        """Register plugin components (tools, middleware, etc.)."""
        pass

    def unregister(self):
        """Cleanup on plugin unload."""
        pass

    def configure(self, config: Dict[str, Any]):
        """Configure plugin with user-provided settings."""
        pass


class PluginRegistry:
    """
    Registry for GAIA plugins.

    Singleton pattern for centralized plugin management.

    Example:
        >>> registry = PluginRegistry.get_instance()
        >>> registry.load_plugin("my_plugin")
        >>> registry.activate("my-plugin")
    """

    _instance: Optional["PluginRegistry"] = None

    def __new__(cls) -> "PluginRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._plugins: Dict[str, Plugin] = {}
        self._plugin_classes: Dict[str, Type[Plugin]] = {}
        self._initialized = True

    @classmethod
    def get_instance(cls) -> "PluginRegistry":
        """Get singleton instance."""
        return cls()

    def register_plugin_class(self, plugin_class: Type[Plugin]):
        """Register a plugin class for later instantiation."""
        self._plugin_classes[plugin_class.name] = plugin_class
        logger.info(f"Registered plugin class: {plugin_class.name}")

    def load_plugin(self, name: str, config: Optional[Dict] = None) -> Plugin:
        """
        Load and instantiate a plugin.

        Args:
            name: Plugin name
            config: Optional configuration

        Returns:
            Instantiated plugin
        """
        if name not in self._plugin_classes:
            raise KeyError(f"Plugin class '{name}' not registered")

        plugin_class = self._plugin_classes[name]
        plugin = plugin_class()
        plugin.configure(config or {})
        plugin.register()

        self._plugins[name] = plugin
        logger.info(f"Loaded plugin: {name}")
        return plugin

    def unload_plugin(self, name: str):
        """Unload a plugin."""
        if name not in self._plugins:
            raise KeyError(f"Plugin '{name}' not loaded")

        plugin = self._plugins[name]
        plugin.unregister()
        del self._plugins[name]
        logger.info(f"Unloaded plugin: {name}")

    def get_plugin(self, name: str) -> Plugin:
        """Get loaded plugin instance."""
        return self._plugins.get(name)

    def list_plugins(self) -> List[str]:
        """List loaded plugin names."""
        return list(self._plugins.keys())

    def activate_all(self):
        """Activate all registered plugins."""
        for name in list(self._plugin_classes.keys()):
            if name not in self._plugins:
                self.load_plugin(name)
```

---

## 3. Performance Optimization Specification

### 3.1 Async/await Pattern Standardization

*(See implementation plan for AsyncUtils code)*

### 3.2 Connection Pooling

*(See implementation plan for ConnectionPool code)*

### 3.3 Cache Layer

```python
# src/gaia/perf/cache.py
"""
Multi-layer caching for GAIA.

Implements a two-tier cache:
1. L1: In-memory cache (fast, small)
2. L2: Disk cache (slower, larger, persistent)

Cache Eviction:
- L1: LRU with TTL
- L2: LRU with size limit

Example:
    >>> cache = CacheLayer(
    ...     l1_max_size=1000,
    ...     l1_ttl=300,
    ...     l2_path="./.cache",
    ...     l2_max_size=10_000_000,
    ... )
    >>> await cache.set("key", value)
    >>> value = await cache.get("key")
"""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional
from collections import OrderedDict


class LRUCache:
    """Thread-safe LRU cache with TTL support."""

    def __init__(self, max_size: int, ttl: int = 300):
        self.max_size = max_size
        self.ttl = ttl
        self._cache = OrderedDict()
        self._timestamps = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            if key not in self._cache:
                return None

            # Check TTL
            if time.time() - self._timestamps[key] > self.ttl:
                del self._cache[key]
                del self._timestamps[key]
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return self._cache[key]

    async def set(self, key: str, value: Any):
        async with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            self._timestamps[key] = time.time()

            # Evict if over capacity
            while len(self._cache) > self.max_size:
                oldest = next(iter(self._cache))
                del self._cache[oldest]
                del self._timestamps[oldest]


class CacheLayer:
    """Two-tier cache layer."""

    def __init__(
        self,
        l1_max_size: int = 1000,
        l1_ttl: int = 300,
        l2_path: str = "./.cache",
        l2_max_size: int = 10_000_000,  # bytes
    ):
        self.l1 = LRUCache(l1_max_size, l1_ttl)
        self.l2_path = Path(l2_path)
        self.l2_max_size = l2_max_size
        self.l2_path.mkdir(parents=True, exist_ok=True)

    def _get_l2_key(self, key: str) -> str:
        """Hash key for L2 filename."""
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    async def get(self, key: str) -> Optional[Any]:
        """Get from cache (L1 first, then L2)."""
        # Try L1 first
        value = await self.l1.get(key)
        if value is not None:
            return value

        # Try L2
        l2_key = self._get_l2_key(key)
        l2_path = self.l2_path / l2_key
        if l2_path.exists():
            try:
                with open(l2_path, 'r') as f:
                    value = json.load(f)
                # Populate L1
                await self.l1.set(key, value)
                return value
            except (json.JSONDecodeError, IOError):
                return None

        return None

    async def set(self, key: str, value: Any):
        """Set in both L1 and L2."""
        # Set L1
        await self.l1.set(key, value)

        # Set L2
        l2_key = self._get_l2_key(key)
        l2_path = self.l2_path / l2_key
        try:
            with open(l2_path, 'w') as f:
                json.dump(value, f)
        except IOError as e:
            logger.error(f"L2 cache write failed: {e}")

    async def delete(self, key: str):
        """Delete from cache."""
        # Delete from L1
        if key in self.l1._cache:
            del self.l1._cache[key]

        # Delete from L2
        l2_key = self._get_l2_key(key)
        l2_path = self.l2_path / l2_key
        if l2_path.exists():
            l2_path.unlink()

    async def clear(self):
        """Clear all cache."""
        self.l1._cache.clear()
        self.l1._timestamps.clear()
        for f in self.l2_path.glob("*"):
            f.unlink()
```

---

## 4. Enterprise Readiness Specification

### 4.1 Configuration Schema

*(See implementation plan for ConfigSchema code)*

### 4.2 Secrets Management

*(See implementation plan for SecretsManager code)*

### 4.3 Observability

```python
# src/gaia/observability/core.py
"""
Observability core for GAIA.

Provides:
- Structured logging
- Distributed tracing
- Metrics collection

Integrates with:
- Prometheus (metrics)
- Jaeger/Zipkin (tracing)
- ELK stack (logging)

Example:
    >>> obs = ObservabilityCore(service_name="gaia")
    >>> with obs.trace("agent_execution") as span:
    ...     result = await run_agent()
    ...     obs.metrics.increment("agents.executed")
"""

import logging
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Optional


class Span:
    """Represents a trace span."""

    def __init__(
        self,
        trace_id: str,
        span_id: str,
        operation: str,
        parent_id: Optional[str] = None,
    ):
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_id = parent_id
        self.operation = operation
        self.start_time = None
        self.end_time = None
        self.tags = {}
        self.logs = []

    def start(self):
        self.start_time = time.time()
        return self

    def finish(self):
        self.end_time = time.time()
        return self

    def set_tag(self, key: str, value: Any):
        self.tags[key] = value
        return self

    def log(self, message: str, **kwargs):
        self.logs.append({
            "timestamp": time.time(),
            "message": message,
            "data": kwargs,
        })
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "operation": self.operation,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.end_time - self.start_time if self.end_time else None,
            "tags": self.tags,
            "logs": self.logs,
        }


class ObservabilityCore:
    """
    Central observability provider.

    Coordinates logging, tracing, and metrics.
    """

    def __init__(
        self,
        service_name: str,
        log_level: str = "INFO",
        tracing_enabled: bool = True,
        metrics_enabled: bool = True,
    ):
        self.service_name = service_name
        self.tracing_enabled = tracing_enabled
        self.metrics_enabled = metrics_enabled

        # Setup logging
        self.logger = logging.getLogger(service_name)
        self.logger.setLevel(getattr(logging, log_level))

        # Metrics collector
        self.metrics = MetricsCollector() if metrics_enabled else None

        # Active spans
        self._active_spans: Dict[str, Span] = {}

    @contextmanager
    def trace(
        self,
        operation: str,
        parent_span: Optional[Span] = None,
        **tags,
    ):
        """
        Create trace span (context manager).

        Args:
            operation: Operation name
            parent_span: Optional parent span
            **tags: Span tags

        Yields:
            Span instance

        Example:
            with obs.trace("db_query", table="users") as span:
                result = db.query(...)
                span.set_tag("rows", len(result))
        """
        trace_id = parent_span.trace_id if parent_span else str(uuid.uuid4())
        span_id = str(uuid.uuid4())

        span = Span(
            trace_id=trace_id,
            span_id=span_id,
            parent_id=parent_span.span_id if parent_span else None,
            operation=operation,
        ).start()

        for key, value in tags.items():
            span.set_tag(key, value)

        self._active_spans[span_id] = span

        try:
            yield span
            span.finish()
            self.logger.debug(
                f"Span {operation} completed in {span.end_time - span.start_time:.3f}s"
            )
        except Exception as e:
            span.set_tag("error", str(e))
            span.finish()
            self.logger.error(f"Span {operation} failed: {e}")
            raise
        finally:
            del self._active_spans[span_id]

    def log_info(self, message: str, **kwargs):
        """Log info message with structured data."""
        self.logger.info(message, extra=kwargs)

    def log_error(self, message: str, **kwargs):
        """Log error message with structured data."""
        self.logger.error(message, extra=kwargs)


class MetricsCollector:
    """
    Metrics collection and export.

    Supports:
    - Counters (increment-only)
    - Gauges (set to value)
    - Histograms (distribution)
    """

    def __init__(self):
        self._counters = {}
        self._gauges = {}
        self._histograms = {}

    def increment(self, name: str, value: int = 1, **tags):
        """Increment counter."""
        key = self._make_key(name, tags)
        self._counters[key] = self._counters.get(key, 0) + value

    def gauge(self, name: str, value: float, **tags):
        """Set gauge value."""
        key = self._make_key(name, tags)
        self._gauges[key] = value

    def histogram(self, name: str, value: float, **tags):
        """Record histogram value."""
        key = self._make_key(name, tags)
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)

    def _make_key(self, name: str, tags: Dict) -> str:
        """Create metric key from name and tags."""
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}" if tag_str else name

    def get_all(self) -> Dict[str, Any]:
        """Get all metrics."""
        return {
            "counters": self._counters.copy(),
            "gauges": self._gauges.copy(),
            "histograms": {
                k: {"values": v, "count": len(v)}
                for k, v in self._histograms.items()
            },
        }
```

---

## 5. API Standardization Specification

### 5.1 OpenAPI Specification

*(See implementation plan for OpenAPISpec code)*

### 5.2 API Versioning

```python
# src/gaia/api/versioning.py
"""
API versioning strategy.

Versioning approach:
- URL path versioning: /v1/, /v2/, etc.
- Backward compatible within major versions
- Deprecation warnings for minor version changes

Example:
    >>> versioning = APIVersioning(current_version="v1")
    >>> @versioning.route("/chat", methods=["POST"], version="v1")
    ... async def chat_v1(request):
    ...     ...
"""

from typing import Callable, Dict, List, Optional
from functools import wraps
import warnings


class APIVersioning:
    """API versioning manager."""

    def __init__(self, current_version: str = "v1"):
        self.current_version = current_version
        self._routes: Dict[str, Dict[str, Callable]] = {}
        self._deprecated: List[str] = []

    def route(
        self,
        path: str,
        methods: List[str],
        version: Optional[str] = None,
        deprecated: bool = False,
    ) -> Callable:
        """
        Register versioned route.

        Args:
            path: URL path
            methods: HTTP methods
            version: API version (default: current)
            deprecated: Mark as deprecated

        Returns:
            Decorated function
        """
        version = version or self.current_version

        def decorator(func: Callable) -> Callable:
            if version not in self._routes:
                self._routes[version] = {}

            full_path = f"/{version}{path}"
            self._routes[version][full_path] = func

            if deprecated:
                self._deprecated.append(full_path)

            @wraps(func)
            def wrapper(*args, **kwargs):
                if deprecated:
                    warnings.warn(
                        f"Endpoint {full_path} is deprecated",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                return func(*args, **kwargs)

            return wrapper

        return decorator

    def get_routes(self, version: Optional[str] = None) -> Dict[str, Callable]:
        """Get routes for version."""
        version = version or self.current_version
        return self._routes.get(version, {})

    def get_all_versions(self) -> List[str]:
        """Get all registered versions."""
        return list(self._routes.keys())


class DeprecationManager:
    """
    Manages API deprecation warnings.

    Example:
        >>> deprecation = DeprecationManager()
        >>> deprecation.deprecate(
        ...     "/v1/legacy",
        ...     removal_version="v3.0",
        ...     alternative="/v2/modern",
        ... )
    """

    def __init__(self):
        self._deprecated_endpoints: Dict[str, Dict] = {}

    def deprecate(
        self,
        endpoint: str,
        removal_version: str,
        alternative: Optional[str] = None,
    ):
        """Mark endpoint as deprecated."""
        self._deprecated_endpoints[endpoint] = {
            "removal_version": removal_version,
            "alternative": alternative,
        }

    def is_deprecated(self, endpoint: str) -> bool:
        """Check if endpoint is deprecated."""
        return endpoint in self._deprecated_endpoints

    def get_deprecation_info(self, endpoint: str) -> Optional[Dict]:
        """Get deprecation info for endpoint."""
        return self._deprecated_endpoints.get(endpoint)
```

---

## 6. Integration Strategy

### 6.1 Backward Compatibility

All Phase 3 changes maintain backward compatibility through:
1. **AgentAdapter:** Wraps legacy Agent instances
2. **Deprecation warnings:** Gradual migration path
3. **Parallel operation:** Old and new patterns coexist

### 6.2 Migration Path

**Phase 3 Migration Timeline:**

| Sprint | Milestone | Compatibility |
|--------|-----------|---------------|
| Sprint 1 | AgentProfile, Executor | New pattern available |
| Sprint 2 | AgentAdapter | Legacy agents wrapped |
| Sprint 3 | Config, Secrets | Additive changes |
| Sprint 4 | OpenAPI, Versioning | Full backward compat |

---

## 7. Quality Assurance

### 7.1 Test Requirements

| Component | Unit Tests | Integration Tests | Performance Tests |
|-----------|------------|-------------------|-------------------|
| AgentProfile | 20 | N/A | N/A |
| AgentExecutor | 40 | 20 | 10 |
| PluginRegistry | 30 | 10 | N/A |
| DIContainer | 25 | 10 | N/A |
| AsyncUtils | 15 | N/A | 5 |
| ConnectionPool | 30 | 10 | 10 |
| CacheLayer | 40 | 10 | 10 |
| ConfigSchema | 30 | N/A | N/A |
| SecretsManager | 35 | 10 | 5 |
| ObservabilityCore | 50 | 10 | N/A |
| OpenAPISpec | 40 | N/A | N/A |
| **Total** | **355** | **80** | **40** |

### 7.2 Quality Gate 4

| Criteria | Test Method | Target |
|----------|-------------|--------|
| MOD-001 | Profile validation | 100% accuracy |
| MOD-002 | Executor tests | Zero regression |
| MOD-003 | BC test suite | 100% pass |
| PERF-006 | Pool benchmark | >100 req/s |
| PERF-007 | Cache benchmark | >80% hit rate |
| ENT-001 | Config validation | 100% rejection |
| API-001 | OpenAPI completeness | 100% documented |

---

## 8. Appendix: File Reference

### 8.1 New Files

| File | Purpose | LOC |
|------|---------|-----|
| `src/gaia/core/profile.py` | AgentProfile dataclass | 200 |
| `src/gaia/core/executor.py` | AgentExecutor engine | 400 |
| `src/gaia/core/plugins.py` | PluginRegistry | 300 |
| `src/gaia/core/di.py` | DIContainer | 250 |
| `src/gaia/core/adapter.py` | AgentAdapter | 200 |
| `src/gaia/perf/async_utils.py` | Async utilities | 150 |
| `src/gaia/perf/pool.py` | ConnectionPool | 300 |
| `src/gaia/perf/cache.py` | CacheLayer | 400 |
| `src/gaia/perf/ratelimit.py` | RateLimiter | 200 |
| `src/gaia/config/schema.py` | ConfigSchema | 300 |
| `src/gaia/config/manager.py` | ConfigManager | 400 |
| `src/gaia/config/secrets.py` | SecretsManager | 350 |
| `src/gaia/observability/core.py` | ObservabilityCore | 500 |
| `src/gaia/observability/metrics.py` | MetricsCollector | 300 |
| `src/gaia/api/openapi.py` | OpenAPISpec | 400 |
| `src/gaia/api/versioning.py` | APIVersioning | 200 |
| `src/gaia/api/deprecation.py` | DeprecationLayer | 150 |

### 8.2 Test Files

| File | Functions | Purpose |
|------|-----------|---------|
| `tests/unit/core/test_profile.py` | 20 | AgentProfile validation |
| `tests/unit/core/test_executor.py` | 40 | AgentExecutor behavior |
| `tests/unit/core/test_plugins.py` | 30 | PluginRegistry |
| `tests/unit/core/test_di.py` | 25 | DIContainer |
| `tests/unit/core/test_adapter.py` | 20 | Backward compatibility |
| `tests/unit/perf/test_async.py` | 15 | Async utilities |
| `tests/unit/perf/test_pool.py` | 30 | ConnectionPool |
| `tests/unit/perf/test_cache.py` | 40 | CacheLayer |
| `tests/unit/config/test_schema.py` | 30 | ConfigSchema |
| `tests/unit/config/test_manager.py` | 40 | ConfigManager |
| `tests/unit/config/test_secrets.py` | 35 | SecretsManager |
| `tests/unit/observability/test_core.py` | 50 | ObservabilityCore |
| `tests/unit/api/test_openapi.py` | 40 | OpenAPISpec |

---

**END OF SPECIFICATION**

**Distribution:** GAIA Development Team
**Next Action:** senior-developer begins Sprint 1 implementation
**Version History:**
- v1.0: Initial Phase 3 specification (2026-04-06)
