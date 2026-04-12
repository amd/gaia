# Phase 3 Sprint 2 Technical Specification: Dependency Injection + Performance Start

**Document Version:** 1.0
**Date:** 2026-04-06
**Status:** READY FOR IMPLEMENTATION
**Duration:** 3 weeks (Weeks 4-6)
**Owner:** senior-developer
**Classification:** Technical Implementation Specification

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Sprint 2 Overview](#2-sprint-2-overview)
3. [DIContainer Specification](#3-dicontainer-specification)
4. [AgentAdapter Specification](#4-agentadapter-specification)
5. [AsyncUtils Specification](#5-asyncutils-specification)
6. [ConnectionPool Specification](#6-connectionpool-specification)
7. [Integration Strategy](#7-integration-strategy)
8. [Test Strategy](#8-test-strategy)
9. [Quality Gate 4 Criteria](#9-quality-gate-4-criteria)
10. [Implementation Checklist](#10-implementation-checklist)

---

## 1. Executive Summary

### 1.1 Sprint 2 Objectives

Phase 3 Sprint 2 builds upon the modular architecture core delivered in Sprint 1 by adding dependency injection capabilities and initiating performance optimization. This sprint delivers four key components:

| Component | File | LOC Estimate | Tests | Priority |
|-----------|------|--------------|-------|----------|
| **DIContainer** | `src/gaia/core/di_container.py` | ~250 | 40 | P0 |
| **AgentAdapter** | `src/gaia/core/adapter.py` | ~200 | 30 | P0 |
| **AsyncUtils** | `src/gaia/perf/async_utils.py` | ~150 | 20 | P1 |
| **ConnectionPool** | `src/gaia/perf/connection_pool.py` | ~300 | 30 | P1 |

### 1.2 Sprint 2 Context

**Program Progress:** ~80% Complete

```
Program Progress: 80% Complete
├── Phase 0: Tool Scoping        ████████████████████ 100% COMPLETE
├── Phase 1: State Unification   ████████████████████ 100% COMPLETE
├── Phase 2: Quality Enhancement ████████████████████ 100% COMPLETE
├── Phase 3 Sprint 1: Core       ████████████████████ 100% COMPLETE
├── Phase 3 Sprint 2: DI+Perf    ░░░░░░░░░░░░░░░░░░░░   0% READY
└── Phase 3 Sprint 3-4: Future   ░░░░░░░░░░░░░░░░░░░░   0% PENDING
```

### 1.3 Sprint 1 Handoff Summary

Phase 3 Sprint 1 delivered the modular architecture core:
- **AgentProfile** (360 LOC): Agent configuration dataclass with validation
- **AgentCapabilities** (340 LOC): Capability definitions and tool tracking
- **AgentExecutor** (650 LOC): Behavior injection engine
- **PluginRegistry** (680 LOC): Plugin lifecycle management

**Sprint 1 Test Results:** 195 tests at 100% pass rate
**Quality Gate 4:** PASS (all 5 criteria met)

### 1.4 Sprint 2 Deliverables

1. **Dependency Injection Container** - Service registration and resolution
2. **Agent Adapter** - Backward compatibility layer for legacy agents
3. **Async Utilities** - Standardized async/await patterns and decorators
4. **Connection Pool** - LLM client connection pooling for performance

---

## 2. Sprint 2 Overview

### 2.1 Week 4: Dependency Injection Core

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create DIContainer class | senior-developer | Dependency injection container (~250 LOC) |
| 3 | Unit tests for DIContainer | testing-quality-specialist | 40 test functions |
| 4-5 | Create AgentAdapter class | senior-developer | Backward compatibility adapter (~200 LOC) |

### 2.2 Week 5: Performance Layer Start

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create AsyncUtils | senior-developer | Async utilities, decorators (~150 LOC) |
| 3-5 | Create ConnectionPool | senior-developer | Connection pooling for LLM clients (~300 LOC) |

### 2.3 Week 6: Testing & Validation

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Unit tests for async/pool | testing-quality-specialist | 50 test functions |
| 3-4 | Performance benchmarks | testing-quality-specialist | Baseline metrics |
| 5 | Sprint 2 closeout | software-program-manager | Sprint 2 summary document |

### 2.4 Dependencies

**Input Dependencies (from Sprint 1):**
- `gaia.core.profile.AgentProfile` - Agent configuration
- `gaia.core.executor.AgentExecutor` - Agent execution engine
- `gaia.core.plugin.PluginRegistry` - Plugin management
- `gaia.state.nexus.NexusService` - State management
- `gaia.llm.base_client.BaseLLMClient` - LLM client interface

**Output Dependencies (for Sprint 3):**
- Cache layer will use DIContainer for service resolution
- ConfigManager will use DIContainer for dependency injection
- RateLimiter will use AsyncUtils for async patterns

---

## 3. DIContainer Specification

### 3.1 Design Overview

The DIContainer provides dependency injection capabilities for the GAIA framework. It supports:
- **Singleton** - Single shared instance across application lifetime
- **Transient** - New instance created on each resolution
- **Scoped** - Instance created per request/context scope

**Architecture:**
```
┌─────────────────────────────────────┐
│         DIContainer                 │
├─────────────────────────────────────┤
│ - _services: Dict[str, ServiceDef]  │
│ - _singletons: Dict[str, Any]       │
│ - _scopes: Dict[str, Dict]          │
│ - _lock: RLock                      │
├─────────────────────────────────────┤
│ + register_singleton()              │
│ + register_transient()              │
│ + register_scoped()                 │
│ + resolve()                         │
│ + get_llm_client()                  │
│ + enter_scope()                     │
│ + exit_scope()                      │
└─────────────────────────────────────┘
```

### 3.2 Implementation

```python
# src/gaia/core/di_container.py
"""
Dependency Injection Container for GAIA.

This module provides a lightweight dependency injection container
supporting singleton, transient, and scoped service lifetimes.

Service Lifetimes:
    - Singleton: Single instance shared across application
    - Transient: New instance created on each resolution
    - Scoped: Instance created per scope (request/context)

Thread Safety:
    All operations are thread-safe using RLock for reentrant locking.
    Scoped services are isolated per scope.

Example:
    >>> container = DIContainer()
    >>> container.register_singleton("logger", LoggerFactory)
    >>> container.register_transient("llm_client", LemonadeClient, model="Qwen3.5-35B")
    >>> logger = container.resolve("logger")
    >>> async with container.enter_scope("request"):
    ...     client = container.resolve("llm_client")
"""

import asyncio
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Type, TypeVar, Generic

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class ServiceDefinition:
    """
    Service registration definition.

    Attributes:
        service_type: Type of service (class or interface)
        implementation: Implementation class or factory function
        lifetime: Service lifetime ('singleton', 'transient', 'scoped')
        dependencies: List of dependency service names
        init_kwargs: Keyword arguments for instantiation
    """
    service_type: Type
    implementation: Any
    lifetime: str
    dependencies: list = field(default_factory=list)
    init_kwargs: Dict[str, Any] = field(default_factory=dict)


class ServiceResolutionError(Exception):
    """Raised when a service cannot be resolved."""
    pass


class CircularDependencyError(Exception):
    """Raised when circular dependencies are detected."""
    pass


class DIContainer:
    """
    Dependency Injection Container.

    Supports singleton, transient, and scoped service lifetimes.
    Thread-safe with automatic dependency resolution.

    Example:
        >>> container = DIContainer()
        >>> container.register_singleton("config", ConfigManager)
        >>> container.register_transient(
        ...     "llm_client",
        ...     LemonadeClient,
        ...     model_id="Qwen3.5-35B"
        ... )
        >>> config = container.resolve("config")
    """

    _instance: Optional["DIContainer"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "DIContainer":
        """Get singleton instance of container."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize container."""
        if self._initialized:
            return

        self._services: Dict[str, ServiceDefinition] = {}
        self._singletons: Dict[str, Any] = {}
        self._scopes: Dict[str, Dict[str, Any]] = {}
        self._current_scope: Optional[str] = None
        self._resolution_stack: list = []  # For circular dependency detection
        self._lock = threading.RLock()
        self._async_lock = asyncio.Lock()
        self._initialized = True

        logger.info("DIContainer initialized")

    @classmethod
    def get_instance(cls) -> "DIContainer":
        """Get singleton container instance."""
        return cls()

    def reset(self) -> None:
        """Reset container state (for testing)."""
        with self._lock:
            self._services.clear()
            self._singletons.clear()
            self._scopes.clear()
            self._current_scope = None
            self._resolution_stack.clear()
            logger.info("DIContainer reset")

    # ==================== Registration ====================

    def register_singleton(
        self,
        name: str,
        implementation: Type[T],
        **kwargs: Any,
    ) -> "DIContainer":
        """
        Register a singleton service.

        Singleton services are instantiated once and reused.

        Args:
            name: Service name for resolution
            implementation: Implementation class
            **kwargs: Keyword arguments for instantiation

        Returns:
            Self for method chaining

        Example:
            >>> container.register_singleton("config", ConfigManager, config_path="config.yaml")
        """
        with self._lock:
            self._services[name] = ServiceDefinition(
                service_type=implementation,
                implementation=implementation,
                lifetime='singleton',
                init_kwargs=kwargs,
            )
            logger.debug(f"Registered singleton service: {name}")
        return self

    def register_transient(
        self,
        name: str,
        implementation: Type[T],
        **kwargs: Any,
    ) -> "DIContainer":
        """
        Register a transient service.

        Transient services are created fresh on each resolution.

        Args:
            name: Service name for resolution
            implementation: Implementation class
            **kwargs: Keyword arguments for instantiation

        Returns:
            Self for method chaining

        Example:
            >>> container.register_transient("request", RequestHandler)
        """
        with self._lock:
            self._services[name] = ServiceDefinition(
                service_type=implementation,
                implementation=implementation,
                lifetime='transient',
                init_kwargs=kwargs,
            )
            logger.debug(f"Registered transient service: {name}")
        return self

    def register_scoped(
        self,
        name: str,
        implementation: Type[T],
        **kwargs: Any,
    ) -> "DIContainer":
        """
        Register a scoped service.

        Scoped services are created once per scope (request/context).

        Args:
            name: Service name for resolution
            implementation: Implementation class
            **kwargs: Keyword arguments for instantiation

        Returns:
            Self for method chaining

        Example:
            >>> container.register_scoped("session", DatabaseSession)
        """
        with self._lock:
            self._services[name] = ServiceDefinition(
                service_type=implementation,
                implementation=implementation,
                lifetime='scoped',
                init_kwargs=kwargs,
            )
            logger.debug(f"Registered scoped service: {name}")
        return self

    def register_factory(
        self,
        name: str,
        factory: Callable[..., T],
        lifetime: str = 'transient',
    ) -> "DIContainer":
        """
        Register a service factory.

        Factories are called to create service instances.

        Args:
            name: Service name
            factory: Factory function/callable
            lifetime: Service lifetime

        Returns:
            Self for method chaining

        Example:
            >>> def create_client():
            ...     return LemonadeClient(model_id="Qwen3.5-35B")
            >>> container.register_factory("llm_client", create_client)
        """
        with self._lock:
            self._services[name] = ServiceDefinition(
                service_type=Callable,
                implementation=factory,
                lifetime=lifetime,
            )
            logger.debug(f"Registered factory service: {name}")
        return self

    # ==================== Resolution ====================

    def resolve(self, name: str) -> Any:
        """
        Resolve a service by name.

        Args:
            name: Service name

        Returns:
            Service instance

        Raises:
            ServiceResolutionError: If service not found or cannot be resolved
            CircularDependencyError: If circular dependency detected

        Example:
            >>> config = container.resolve("config")
        """
        with self._lock:
            # Check for circular dependencies
            if name in self._resolution_stack:
                cycle = " -> ".join(self._resolution_stack + [name])
                raise CircularDependencyError(f"Circular dependency detected: {cycle}")

            if name not in self._services:
                raise ServiceResolutionError(f"Service '{name}' not registered")

            service_def = self._services[name]
            lifetime = service_def.lifetime

            # Handle singleton
            if lifetime == 'singleton':
                if name not in self._singletons:
                    self._resolution_stack.append(name)
                    try:
                        instance = self._create_instance(service_def)
                        self._singletons[name] = instance
                    finally:
                        self._resolution_stack.pop()
                return self._singletons[name]

            # Handle scoped
            if lifetime == 'scoped':
                if self._current_scope is None:
                    raise ServiceResolutionError(
                        f"Scoped service '{name}' requires active scope. "
                        "Use 'with container.enter_scope()'"
                    )
                scope = self._scopes[self._current_scope]
                if name not in scope:
                    self._resolution_stack.append(name)
                    try:
                        instance = self._create_instance(service_def)
                        scope[name] = instance
                    finally:
                        self._resolution_stack.pop()
                return scope[name]

            # Handle transient
            if lifetime == 'transient':
                return self._create_instance(service_def)

            raise ServiceResolutionError(f"Unknown lifetime: {lifetime}")

    def _create_instance(self, service_def: ServiceDefinition) -> Any:
        """Create service instance."""
        impl = service_def.implementation

        # Handle factory functions
        if callable(impl) and not isinstance(impl, type):
            return impl()

        # Resolve dependencies
        kwargs = dict(service_def.init_kwargs)
        for dep_name in service_def.dependencies:
            kwargs[dep_name] = self.resolve(dep_name)

        # Create instance
        try:
            if isinstance(impl, type):
                return impl(**kwargs)
            else:
                return impl(**kwargs)
        except Exception as e:
            raise ServiceResolutionError(
                f"Failed to create service '{service_def.service_type.__name__}': {e}"
            ) from e

    def resolve_optional(self, name: str, default: Any = None) -> Any:
        """
        Resolve a service or return default.

        Args:
            name: Service name
            default: Default value if not found

        Returns:
            Service instance or default
        """
        try:
            return self.resolve(name)
        except ServiceResolutionError:
            return default

    def is_registered(self, name: str) -> bool:
        """Check if service is registered."""
        with self._lock:
            return name in self._services

    # ==================== LLM Client Helpers ====================

    def get_llm_client(self, model_id: Optional[str] = None) -> Any:
        """
        Get LLM client from container.

        Args:
            model_id: Optional model identifier

        Returns:
            LLM client instance

        Example:
            >>> client = container.get_llm_client("Qwen3.5-35B")
        """
        # Try specific model registration first
        if model_id:
            service_name = f"llm_client:{model_id}"
            if self.is_registered(service_name):
                return self.resolve(service_name)

        # Fall back to default
        if self.is_registered("llm_client"):
            return self.resolve("llm_client")

        # Auto-create default client
        from gaia.llm.lemonade_client import LemonadeClient
        client = LemonadeClient(model_id=model_id or "Qwen3.5-35B-A3B-GGUF")
        self.register_singleton("llm_client", LemonadeClient, model_id=model_id)
        return client

    # ==================== Scope Management ====================

    @asynccontextmanager
    async def enter_scope(self, scope_id: Optional[str] = None):
        """
        Enter a new service scope.

        Args:
            scope_id: Optional scope identifier (auto-generated if not provided)

        Yields:
            Scope identifier

        Example:
            >>> async with container.enter_scope("request-123"):
            ...     session = container.resolve("session")
        """
        scope_id = scope_id or str(uuid.uuid4())

        async with self._async_lock:
            self._scopes[scope_id] = {}
            old_scope = self._current_scope
            self._current_scope = scope_id
            logger.debug(f"Entered scope: {scope_id}")

        try:
            yield scope_id
        finally:
            async with self._async_lock:
                # Clean up scoped services
                if scope_id in self._scopes:
                    # Call cleanup on scoped services if they have it
                    for name, service in self._scopes[scope_id].items():
                        if hasattr(service, 'cleanup'):
                            try:
                                service.cleanup()
                            except Exception as e:
                                logger.error(f"Error cleaning up {name}: {e}")
                    del self._scopes[scope_id]

                self._current_scope = old_scope
                logger.debug(f"Exited scope: {scope_id}")

    def get_current_scope(self) -> Optional[str]:
        """Get current scope identifier."""
        return self._current_scope

    # ==================== Introspection ====================

    def get_registered_services(self) -> Dict[str, str]:
        """
        Get all registered services.

        Returns:
            Dictionary mapping service names to lifetimes
        """
        with self._lock:
            return {
                name: defn.lifetime
                for name, defn in self._services.items()
            }

    def get_service_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a service.

        Args:
            name: Service name

        Returns:
            Service information dictionary or None
        """
        with self._lock:
            if name not in self._services:
                return None

            defn = self._services[name]
            return {
                "name": name,
                "type": defn.service_type.__name__,
                "lifetime": defn.lifetime,
                "dependencies": defn.dependencies,
                "init_kwargs": defn.init_kwargs,
            }
```

### 3.3 Integration with AgentExecutor

```python
# Integration example showing DIContainer wired with AgentExecutor
from gaia.core.di_container import DIContainer
from gaia.core.profile import AgentProfile
from gaia.core.executor import AgentExecutor

# Create and configure container
container = DIContainer()
container.register_singleton("config", ConfigManager, config_path="config.yaml")
container.register_transient("llm_client", LemonadeClient, model_id="Qwen3.5-35B")
container.register_scoped("nexus", NexusService)

# Create agent profile
profile = AgentProfile(
    id="code-agent",
    name="Code Assistant",
    role="Expert developer",
    system_prompt="You are an expert developer...",
    tools=["read_file", "write_file"],
)

# Create executor with DI container
executor = AgentExecutor(profile=profile, di_container=container)

# Use within scope
async with container.enter_scope():
    result = await executor.run_step("Create API", {"files": [...]})
```

### 3.4 Test Cases for DIContainer

| Test ID | Test Name | Description | Expected Result |
|---------|-----------|-------------|-----------------|
| DI-001 | test_register_singleton | Register and resolve singleton | Same instance returned |
| DI-002 | test_register_transient | Register and resolve transient | New instance each time |
| DI-003 | test_register_scoped | Scoped service resolution | Same instance in scope |
| DI-004 | test_scope_isolation | Multiple scopes isolated | Different instances per scope |
| DI-005 | test_circular_dependency | Circular dependency detection | CircularDependencyError |
| DI-006 | test_unregistered_service | Resolve unregistered service | ServiceResolutionError |
| DI-007 | test_resolve_optional | Optional resolution with default | Default returned |
| DI-008 | test_factory_registration | Factory function resolution | Factory result returned |
| DI-009 | test_dependency_injection | Auto-inject dependencies | Dependencies resolved |
| DI-010 | test_get_llm_client | LLM client resolution | LLM client returned |
| DI-011 | test_thread_safety | Concurrent resolution | No race conditions |
| DI-012 | test_singleton_instance | Get instance method | Singleton returned |
| DI-013 | test_reset_container | Reset container state | All services cleared |
| DI-014 | test_is_registered | Check registration status | Boolean result |
| DI-015 | test_get_registered_services | List all services | Dictionary returned |
| DI-016 | test_service_info | Get service details | Info dictionary |
| DI-017 | test_scope_cleanup | Cleanup on scope exit | cleanup() called |
| DI-018 | test_nested_scopes | Nested scope support | Proper isolation |
| DI-019 | test_method_chaining | Register methods chain | Returns self |
| DI-020 | test_kwargs_passthrough | Init kwargs passed | Instance configured |

---

## 4. AgentAdapter Specification

### 4.1 Design Overview

The AgentAdapter provides backward compatibility for legacy Agent instances, allowing them to work alongside new AgentExecutor-based agents. This enables gradual migration without breaking existing code.

**Architecture:**
```
┌─────────────────────────────────────┐
│        AgentAdapter                 │
├─────────────────────────────────────┤
│ - legacy_agent: Agent               │
│ - profile: AgentProfile             │
│ - executor: Optional[AgentExecutor] │
├─────────────────────────────────────┤
│ + __init__(legacy_agent)            │
│ + run_step()                        │
│ + run()                             │
│ + get_profile()                     │
│ + __getattr__() -> legacy_agent     │
└─────────────────────────────────────┘
         │
         │ wraps
         ▼
┌─────────────────────────────────────┐
│      Legacy Agent (Agent)           │
│ - run_step()                        │
│ - run()                             │
│ - __getattr__()                     │
└─────────────────────────────────────┘
```

### 4.2 Implementation

```python
# src/gaia/core/adapter.py
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
from gaia.core.executor import AgentExecutor
from gaia.core.di_container import DIContainer

logger = logging.getLogger(__name__)


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
        """
        if legacy_agent is None:
            raise ValueError("legacy_agent cannot be None")

        self.legacy_agent = legacy_agent
        self.profile = self._extract_profile(legacy_agent)
        self._executor: Optional[AgentExecutor] = None
        self._di_container: Optional[DIContainer] = None

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

        # Extract system prompt
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

        # Extract limits
        max_steps = getattr(agent, 'max_steps', 20)
        max_plan_iterations = getattr(agent, 'max_plan_iterations', 3)

        # Build profile
        profile = AgentProfile(
            id=agent_id,
            name=name,
            role=role,
            system_prompt=system_prompt,
            capabilities=capabilities,
            tools=tools,
            model_config=model_config,
            max_steps=max_steps,
            max_plan_iterations=max_plan_iterations,
        )

        logger.debug(f"Extracted profile from {agent.__class__.__name__}: {profile.id}")
        return profile

    def get_profile(self) -> AgentProfile:
        """
        Get the agent profile.

        Returns:
            AgentProfile instance
        """
        return self.profile

    def get_legacy_agent(self) -> Any:
        """
        Get the wrapped legacy agent.

        Returns:
            Legacy Agent instance
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
        """
        return getattr(self.legacy_agent, name)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"AgentAdapter({self.legacy_agent.__class__.__name__}, id={self.profile.id})"


class LegacyAgentWrapper:
    """
    Alternative wrapper for multiple legacy agents.

    This class can wrap multiple legacy agents and route
    requests based on agent selection.

    Example:
        >>> wrapper = LegacyAgentWrapper()
        >>> wrapper.add_agent("code", CodeAgent())
        >>> wrapper.add_agent("chat", ChatAgent())
        >>> response = await wrapper.run_agent("code", "Build API")
    """

    def __init__(self):
        """Initialize wrapper."""
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
            set_default: Set as default agent

        Returns:
            Self for method chaining
        """
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
            KeyError: If agent not found
        """
        if name not in self._agents:
            if self._default:
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
            name: Agent name
            topic: Topic/task
            context: Execution context

        Returns:
            Agent response
        """
        agent = self.get_agent(name)
        return await agent.run_step(topic, context)

    def list_agents(self) -> List[str]:
        """
        List all registered agent names.

        Returns:
            List of agent names
        """
        return list(self._agents.keys())
```

### 4.3 Test Cases for AgentAdapter

| Test ID | Test Name | Description | Expected Result |
|---------|-----------|-------------|-----------------|
| AA-001 | test_adapter_creation | Create adapter with legacy agent | Adapter created |
| AA-002 | test_profile_extraction | Extract profile from agent | Profile with correct fields |
| AA-003 | test_null_agent | Adapter with None agent | ValueError raised |
| AA-004 | test_run_step_delegation | Delegate run_step to legacy | Legacy method called |
| AA-005 | test_run_delegation | Delegate run to legacy | Legacy method called |
| AA-006 | test_getattr_delegation | Access unknown attribute | Delegated to legacy |
| AA-007 | test_profile_id_extraction | Extract agent_id field | Correct ID in profile |
| AA-008 | test_profile_tools_extraction | Extract allowed_tools | Tools list in profile |
| AA-009 | test_profile_model_extraction | Extract model_id | Model in profile |
| AA-010 | test_profile_capabilities | Extract capability flags | Capabilities set |
| AA-011 | test_repr_string | String representation | Contains class name |
| AA-012 | test_wrapper_add_agent | Add agent to wrapper | Agent stored |
| AA-013 | test_wrapper_get_agent | Get agent by name | Correct agent |
| AA-014 | test_wrapper_default_agent | Get with default fallback | Default returned |
| AA-015 | test_wrapper_list_agents | List all agents | List of names |
| AA-016 | test_wrapper_run_agent | Run specific agent | Response returned |
| AA-017 | test_wrapper_unknown_agent | Get unknown agent | KeyError |
| AA-018 | test_method_chaining | Add agent returns self | Chain works |
| AA-019 | test_code_agent_adaptation | Adapt CodeAgent | Profile extracted |
| AA-020 | test_chat_agent_adaptation | Adapt ChatAgent | Profile extracted |

---

## 5. AsyncUtils Specification

### 5.1 Design Overview

AsyncUtils provides standardized async/await patterns and decorators for the GAIA framework. This ensures consistent async behavior across all components.

### 5.2 Implementation

```python
# src/gaia/perf/async_utils.py
"""
Async utilities for standardized async/await patterns.

This module provides:
- Async caching decorators
- Rate limiting utilities
- Retry logic with backoff
- Timeout wrappers
- Async context managers

Example:
    >>> @async_cached(timeout=300)
    ... async def get_llm_response(prompt):
    ...     return await client.chat(prompt)
"""

import asyncio
import functools
import logging
import time
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

T = TypeVar('T')


# ==================== Caching Decorators ====================

def async_cached(timeout: int = 300, key_func: Optional[Callable] = None):
    """
    Decorator for async function caching.

    Args:
        timeout: Cache TTL in seconds
        key_func: Optional function to generate cache key

    Example:
        >>> @async_cached(timeout=600)
        ... async def get_response(prompt: str) -> str:
        ...     return await llm.chat(prompt)
    """
    cache: Dict[str, tuple] = {}
    _lock = asyncio.Lock()

    def make_key(func: Callable, args: tuple, kwargs: dict) -> str:
        if key_func:
            return key_func(*args, **kwargs)
        key_parts = [func.__name__]
        key_parts.extend(str(a) for a in args)
        key_parts.extend(f"{k}={v}" for k, v in sorted(kwargs.items()))
        return ":".join(key_parts)

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            key = make_key(func, args, kwargs)

            async with _lock:
                if key in cache:
                    result, timestamp = cache[key]
                    if time.time() - timestamp < timeout:
                        logger.debug(f"Cache hit for {key}")
                        return result
                    else:
                        logger.debug(f"Cache expired for {key}")
                        del cache[key]

            result = await func(*args, **kwargs)

            async with _lock:
                cache[key] = (result, time.time())
                logger.debug(f"Cached result for {key}")

            return result

        wrapper.cache_clear = lambda: cache.clear()
        wrapper.cache_info = lambda: dict(cache)

        return wrapper

    return decorator


# ==================== Rate Limiting ====================

class AsyncRateLimiter:
    """
    Async rate limiter using token bucket algorithm.

    Example:
        >>> limiter = AsyncRateLimiter(rate=10, capacity=20)
        >>> async with limiter:
        ...     await make_api_call()
    """

    def __init__(self, rate: float, capacity: int):
        """
        Initialize rate limiter.

        Args:
            rate: Tokens per second
            capacity: Maximum token capacity
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self._last_update = time.time()
        self._lock = asyncio.Lock()
        self._waiters: List[asyncio.Future] = []

    async def acquire(self, tokens: int = 1) -> None:
        """
        Acquire tokens (wait if necessary).

        Args:
            tokens: Number of tokens to acquire
        """
        async with self._lock:
            now = time.time()
            elapsed = now - self._last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self._last_update = now

            if self.tokens < tokens:
                wait_time = (tokens - self.tokens) / self.rate
                logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= tokens

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    @property
    def available_tokens(self) -> float:
        """Get current available tokens."""
        now = time.time()
        elapsed = now - self._last_update
        return min(self.capacity, self.tokens + elapsed * self.rate)


# ==================== Retry Logic ====================

def async_retry(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """
    Decorator for async function retry with exponential backoff.

    Args:
        max_retries: Maximum retry attempts
        delay: Initial delay in seconds
        backoff: Backoff multiplier
        exceptions: Exception types to catch

    Example:
        >>> @async_retry(max_retries=3, delay=1.0)
        ... async def flaky_api_call():
        ...     return await api.request()
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            current_delay = delay

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Retry {attempt + 1}/{max_retries} after: {e}"
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(f"All {max_retries} retries exhausted")

            raise last_exception

        return wrapper
    return decorator


# ==================== Timeout Wrapper ====================

def async_timeout(timeout: float):
    """
    Decorator for async function timeout.

    Args:
        timeout: Timeout in seconds

    Example:
        >>> @async_timeout(30.0)
        ... async def slow_operation():
        ...     await asyncio.sleep(100)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.error(f"Operation timed out after {timeout}s")
                raise

        return wrapper
    return decorator


# ==================== Semaphore Bounded Concurrency ====================

class AsyncBoundedExecutor:
    """
    Async executor with bounded concurrency.

    Example:
        >>> executor = AsyncBoundedExecutor(max_concurrent=5)
        >>> results = await executor.map(process_item, items)
    """

    def __init__(self, max_concurrent: int = 10):
        """
        Initialize bounded executor.

        Args:
            max_concurrent: Maximum concurrent operations
        """
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: List[asyncio.Task] = []

    async def submit(self, coro: Any) -> asyncio.Task:
        """
        Submit coroutine for execution.

        Args:
            coro: Coroutine to execute

        Returns:
            asyncio.Task
        """
        async def wrapped():
            async with self._semaphore:
                return await coro

        task = asyncio.create_task(wrapped())
        self._tasks.append(task)
        return task

    async def map(
        self,
        func: Callable,
        items: List[Any],
    ) -> List[Any]:
        """
        Map function over items with bounded concurrency.

        Args:
            func: Async function to apply
            items: Items to process

        Returns:
            List of results
        """
        tasks = [self.submit(func(item)) for item in items]
        return await asyncio.gather(*tasks)

    async def wait_all(self) -> List[Any]:
        """
        Wait for all submitted tasks.

        Returns:
            List of results
        """
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        return results


# ==================== Debounce/Throttle ====================

def async_debounce(wait: float = 0.5):
    """
    Decorator for debouncing async function calls.

    Args:
        wait: Wait time in seconds

    Example:
        >>> @async_debounce(wait=0.5)
        ... async def save_document(content):
        ...     await db.save(content)
    """
    def decorator(func: Callable) -> Callable:
        task: Optional[asyncio.Task] = None

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal task
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            async def delayed_call():
                await asyncio.sleep(wait)
                return await func(*args, **kwargs)

            task = asyncio.create_task(delayed_call())
            return await task

        return wrapper
    return decorator


def async_throttle(period: float):
    """
    Decorator for throttling async function calls.

    Args:
        period: Minimum time between calls in seconds

    Example:
        >>> @async_throttle(period=1.0)
        ... async def api_call():
        ...     return await api.request()
    """
    def decorator(func: Callable) -> Callable:
        last_call = 0.0
        _lock = asyncio.Lock()

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal last_call
            async with _lock:
                now = time.time()
                elapsed = now - last_call
                if elapsed < period:
                    await asyncio.sleep(period - elapsed)
                last_call = time.time()
            return await func(*args, **kwargs)

        return wrapper
    return decorator
```

### 5.3 Test Cases for AsyncUtils

| Test ID | Test Name | Description | Expected Result |
|---------|-----------|-------------|-----------------|
| AU-001 | test_async_cached_hit | Cache hit returns cached value | Cached result |
| AU-002 | test_async_cached_miss | Cache miss calls function | New result |
| AU-003 | test_async_cached_expiry | Cache expires after timeout | Fresh result |
| AU-004 | test_async_cached_key_func | Custom key function | Correct key |
| AU-005 | test_rate_limiter_acquire | Acquire tokens | Tokens deducted |
| AU-006 | test_rate_limiter_wait | Rate limit waits | Delay applied |
| AU-007 | test_rate_limiter_context | Context manager usage | Works correctly |
| AU-008 | test_retry_success | Retry on success | Result returned |
| AU-009 | test_retry_exhausted | All retries fail | Exception raised |
| AU-010 | test_retry_backoff | Exponential backoff | Increasing delays |
| AU-011 | test_timeout_success | Operation completes in time | Result returned |
| AU-012 | test_timeout_exceeded | Operation times out | TimeoutError |
| AU-013 | test_bounded_executor | Bounded concurrent tasks | Max limit respected |
| AU-014 | test_bounded_map | Map with bounded concurrency | All results |
| AU-015 | test_debounce_cancels | Debounce cancels previous | Only last runs |
| AU-016 | test_throttle_delays | Throttle enforces period | Minimum delay |
| AU-017 | test_thread_safety | Concurrent async calls | No race conditions |
| AU-018 | test_cache_clear | Clear cache manually | Cache empty |
| AU-019 | test_cache_info | Get cache info | Info dict |
| AU-020 | test_semaphore_release | Semaphore properly released | No deadlock |

---

## 6. ConnectionPool Specification

### 6.1 Design Overview

ConnectionPool provides connection pooling for LLM clients to reduce connection overhead and improve throughput. The pool manages reusable connections with configurable sizing and idle timeouts.

**Architecture:**
```
┌─────────────────────────────────────────┐
│         ConnectionPool                  │
├─────────────────────────────────────────┤
│ - _pool: Queue[LLMClient]               │
│ - _client_factory: Callable             │
│ - _max_size: int                        │
│ - _min_size: int                        │
│ - _created: int                         │
│ - _idle_timeout: float                  │
│ - _lock: asyncio.Lock                   │
├─────────────────────────────────────────┤
│ + acquire() -> LLMClient                │
│ + release(client)                       │
│ + get_connection()                      │
│ + close()                               │
│ + stats() -> PoolStats                  │
└─────────────────────────────────────────┘
```

### 6.2 Implementation

```python
# src/gaia/perf/connection_pool.py
"""
Connection pooling for LLM clients.

This module provides async connection pooling to reduce LLM client
creation overhead and improve throughput.

Features:
    - Configurable pool size (min/max)
    - Idle timeout for connection cleanup
    - Health checking before returning connections
    - Statistics tracking
    - Graceful shutdown

Example:
    >>> pool = ConnectionPool(
    ...     client_factory=lambda: LemonadeClient(model_id="Qwen3.5-35B"),
    ...     max_size=10,
    ...     min_size=2,
    ... )
    >>> async with pool.get_connection() as client:
    ...     response = await client.chat("Hello")
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class PooledConnection:
    """
    Wrapper for pooled connections.

    Attributes:
        client: The actual client instance
        created_at: Connection creation timestamp
        last_used_at: Last usage timestamp
        use_count: Number of times connection was used
    """
    client: Any
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    use_count: int = 0


@dataclass
class PoolStats:
    """
    Connection pool statistics.

    Attributes:
        size: Current pool size
        available: Available connections
        in_use: Connections currently in use
        created: Total connections created
        max_size: Maximum pool size
        min_size: Minimum pool size
    """
    size: int
    available: int
    in_use: int
    created: int
    max_size: int
    min_size: int
    avg_acquire_time_ms: float = 0.0

    def __repr__(self) -> str:
        return (
            f"PoolStats(size={self.size}, available={self.available}, "
            f"in_use={self.in_use}, created={self.created})"
        )


class ConnectionPoolError(Exception):
    """Base exception for connection pool errors."""
    pass


class PoolExhaustedError(ConnectionPoolError):
    """Raised when pool is exhausted and cannot create new connections."""
    pass


class ConnectionPool:
    """
    Async connection pool for LLM clients.

    Manages a pool of reusable connections with configurable sizing
    and idle timeouts.

    Example:
        >>> pool = ConnectionPool(
        ...     client_factory=lambda: LemonadeClient(),
        ...     max_size=10,
        ...     min_size=2,
        ... )
        >>> client = await pool.acquire()
        >>> try:
        ...     response = await client.chat("Hello")
        ... finally:
        ...     await pool.release(client)
    """

    def __init__(
        self,
        client_factory: Callable[[], Any],
        max_size: int = 10,
        min_size: int = 2,
        max_idle_time: float = 300.0,
        health_check_interval: float = 60.0,
        name: str = "default",
    ):
        """
        Initialize connection pool.

        Args:
            client_factory: Factory function to create new clients
            max_size: Maximum pool size
            min_size: Minimum pool size (pre-created)
            max_idle_time: Max idle time before connection closed (seconds)
            health_check_interval: How often to run health checks
            name: Pool name for logging

        Raises:
            ValueError: If min_size > max_size or invalid parameters
        """
        if min_size > max_size:
            raise ValueError(f"min_size ({min_size}) cannot exceed max_size ({max_size})")
        if max_size < 1:
            raise ValueError("max_size must be at least 1")
        if min_size < 0:
            raise ValueError("min_size must be non-negative")

        self.client_factory = client_factory
        self.max_size = max_size
        self.min_size = min_size
        self.max_idle_time = max_idle_time
        self.health_check_interval = health_check_interval
        self.name = name

        self._pool: asyncio.Queue[PooledConnection] = asyncio.Queue(maxsize=max_size)
        self._created = 0
        self._in_use = 0
        self._closed = False
        self._lock = asyncio.Lock()
        self._initialized = False
        self._health_check_task: Optional[asyncio.Task] = None

        # Statistics
        self._acquire_times: list = []
        self._total_acquires = 0

        logger.info(f"ConnectionPool '{name}' created: min={min_size}, max={max_size}")

    async def initialize(self) -> None:
        """
        Initialize pool with minimum connections.

        Pre-creates min_size connections for immediate availability.
        """
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            logger.info(f"Initializing connection pool '{self.name}'")

            for i in range(self.min_size):
                try:
                    client = await self._create_client()
                    pooled = PooledConnection(client=client)
                    await self._pool.put(pooled)
                    logger.debug(f"Pre-created connection {i + 1}/{self.min_size}")
                except Exception as e:
                    logger.error(f"Failed to pre-create connection: {e}")

            self._initialized = True
            logger.info(f"Connection pool '{self.name}' initialized")

    async def _create_client(self) -> Any:
        """Create new client using factory."""
        client = self.client_factory()

        # Handle async factory
        if asyncio.iscoroutinefunction(client):
            client = await client

        self._created += 1
        logger.debug(f"Created new client (total: {self._created})")
        return client

    async def _destroy_client(self, client: Any) -> None:
        """Destroy client and cleanup resources."""
        try:
            if hasattr(client, 'close'):
                if asyncio.iscoroutinefunction(client.close):
                    await client.close()
                else:
                    client.close()
            logger.debug("Destroyed client")
        except Exception as e:
            logger.warning(f"Error destroying client: {e}")

    async def _is_healthy(self, client: Any) -> bool:
        """
        Check if client is healthy.

        Override this method in subclasses for custom health checks.
        """
        # Default: assume healthy if has no is_closed attribute
        if hasattr(client, 'is_closed'):
            return not client.is_closed
        return True

    async def acquire(self, timeout: Optional[float] = None) -> Any:
        """
        Acquire connection from pool.

        Args:
            timeout: Optional timeout in seconds

        Returns:
            Client instance

        Raises:
            PoolExhaustedError: If pool exhausted and at max capacity
            ConnectionPoolError: If pool is closed
        """
        if self._closed:
            raise ConnectionPoolError("Connection pool is closed")

        if not self._initialized:
            await self.initialize()

        start_time = time.time()

        # Try to get existing connection
        try:
            pooled = self._pool.get_nowait()

            # Check health and idle timeout
            now = time.time()
            is_healthy = await self._is_healthy(pooled.client)
            is_idle_expired = (now - pooled.last_used_at) > self.max_idle_time

            if is_healthy and not is_idle_expired:
                pooled.use_count += 1
                pooled.last_used_at = now
                async with self._lock:
                    self._in_use += 1
                    self._record_acquire_time(start_time)
                logger.debug(f"Acquired existing connection (use_count={pooled.use_count})")
                return pooled.client
            else:
                # Connection unhealthy or idle expired - destroy and create new
                logger.debug("Connection unhealthy/idle-expired, replacing")
                await self._destroy_client(pooled.client)
                self._created -= 1

        except asyncio.QueueEmpty:
            pass  # Pool empty, will create new if under limit

        # Create new connection if under max
        async with self._lock:
            if self._created < self.max_size:
                try:
                    client = await self._create_client()
                    self._in_use += 1
                    self._record_acquire_time(start_time)
                    return client
                except Exception as e:
                    raise ConnectionPoolError(f"Failed to create connection: {e}") from e

            # At max capacity - wait for available connection
            logger.debug("Pool at capacity, waiting for available connection")

        try:
            pooled = await asyncio.wait_for(self._pool.get(), timeout=timeout)

            # Health check
            if not await self._is_healthy(pooled.client):
                logger.debug("Acquired unhealthy connection, replacing")
                await self._destroy_client(pooled.client)
                self._created -= 1
                client = await self._create_client()
                pooled.client = client

            pooled.use_count += 1
            pooled.last_used_at = time.time()
            async with self._lock:
                self._in_use += 1
                self._record_acquire_time(start_time)

            return pooled.client

        except asyncio.TimeoutError:
            raise PoolExhaustedError(
                f"Connection pool exhausted (max_size={self.max_size})"
            )

    async def release(self, client: Any) -> None:
        """
        Release connection back to pool.

        Args:
            client: Client instance to release
        """
        if self._closed:
            await self._destroy_client(client)
            return

        async with self._lock:
            self._in_use = max(0, self._in_use - 1)

        # Return to pool if not at capacity
        try:
            pooled = PooledConnection(client=client)
            self._pool.put_nowait(pooled)
            logger.debug("Released connection back to pool")
        except asyncio.QueueFull:
            logger.debug("Pool full, destroying excess connection")
            await self._destroy_client(client)
            self._created -= 1

    async def get_connection(self):
        """
        Context manager for acquiring connection.

        Example:
            >>> async with pool.get_connection() as client:
            ...     response = await client.chat("Hello")
        """
        return _ConnectionContextManager(self)

    def _record_acquire_time(self, start_time: float) -> None:
        """Record acquisition time for statistics."""
        elapsed_ms = (time.time() - start_time) * 1000
        self._acquire_times.append(elapsed_ms)
        self._total_acquires += 1

        # Keep last 100 measurements
        if len(self._acquire_times) > 100:
            self._acquire_times = self._acquire_times[-100:]

    async def stats(self) -> PoolStats:
        """
        Get pool statistics.

        Returns:
            PoolStats instance
        """
        avg_acquire_time = (
            sum(self._acquire_times) / len(self._acquire_times)
            if self._acquire_times else 0.0
        )

        return PoolStats(
            size=self._pool.qsize(),
            available=self._pool.qsize(),
            in_use=self._in_use,
            created=self._created,
            max_size=self.max_size,
            min_size=self.min_size,
            avg_acquire_time_ms=round(avg_acquire_time, 2),
        )

    async def close(self) -> None:
        """
        Close pool and all connections.

        Waits for in-use connections to be returned before closing.
        """
        logger.info(f"Closing connection pool '{self.name}'")

        self._closed = True

        # Cancel health check task
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # Wait for in-use connections (with timeout)
        wait_start = time.time()
        while self._in_use > 0 and (time.time() - wait_start) < 30:
            await asyncio.sleep(0.5)

        # Destroy all pooled connections
        while not self._pool.empty():
            try:
                pooled = self._pool.get_nowait()
                await self._destroy_client(pooled.client)
            except asyncio.QueueEmpty:
                break

        self._created = 0
        self._in_use = 0
        logger.info(f"Connection pool '{self.name}' closed")

    async def start_health_checker(self) -> None:
        """Start background health check task."""
        async def health_check_loop():
            while not self._closed:
                try:
                    await asyncio.sleep(self.health_check_interval)

                    # Check idle connections
                    connections_to_check = []
                    while not self._pool.empty():
                        pooled = self._pool.get_nowait()
                        connections_to_check.append(pooled)

                    for pooled in connections_to_check:
                        is_healthy = await self._is_healthy(pooled.client)
                        is_idle_expired = (
                            time.time() - pooled.last_used_at
                        ) > self.max_idle_time

                        if is_healthy and not is_idle_expired:
                            await self._pool.put(pooled)
                        else:
                            logger.debug("Health check: removing unhealthy/idle connection")
                            await self._destroy_client(pooled.client)
                            self._created -= 1

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Health check error: {e}")

        self._health_check_task = asyncio.create_task(health_check_loop())
        logger.debug("Health checker started")


class _ConnectionContextManager:
    """Context manager for connection acquisition."""

    def __init__(self, pool: ConnectionPool):
        self.pool = pool
        self.client = None

    async def __aenter__(self) -> Any:
        self.client = await self.pool.acquire()
        return self.client

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.pool.release(self.client)


# ==================== Default Pool Instances ====================

class PoolManager:
    """
    Manager for multiple connection pools.

    Example:
        >>> manager = PoolManager()
        >>> manager.create_pool("lemonade", lambda: LemonadeClient())
        >>> client = await manager.get_client("lemonade")
    """

    def __init__(self):
        """Initialize pool manager."""
        self._pools: Dict[str, ConnectionPool] = {}
        self._lock = asyncio.Lock()

    def create_pool(
        self,
        name: str,
        client_factory: Callable,
        **kwargs,
    ) -> ConnectionPool:
        """
        Create a new connection pool.

        Args:
            name: Pool name
            client_factory: Factory function
            **kwargs: Pool configuration

        Returns:
            ConnectionPool instance
        """
        pool = ConnectionPool(client_factory=client_factory, name=name, **kwargs)
        self._pools[name] = pool
        logger.info(f"Created pool '{name}'")
        return pool

    def get_pool(self, name: str) -> Optional[ConnectionPool]:
        """Get pool by name."""
        return self._pools.get(name)

    async def get_client(self, name: str) -> Any:
        """
        Get client from named pool.

        Args:
            name: Pool name

        Returns:
            Client instance
        """
        pool = self._pools.get(name)
        if not pool:
            raise KeyError(f"Pool '{name}' not found")
        return await pool.acquire()

    async def release_client(self, name: str, client: Any) -> None:
        """
        Release client back to named pool.

        Args:
            name: Pool name
            client: Client instance
        """
        pool = self._pools.get(name)
        if pool:
            await pool.release(client)

    async def close_all(self) -> None:
        """Close all pools."""
        for pool in self._pools.values():
            await pool.close()
        self._pools.clear()
```

### 6.3 Test Cases for ConnectionPool

| Test ID | Test Name | Description | Expected Result |
|---------|-----------|-------------|-----------------|
| CP-001 | test_pool_creation | Create pool with params | Pool created |
| CP-002 | test_pool_initialize | Initialize pre-creates min connections | min_size connections |
| CP-003 | test_pool_acquire | Acquire connection | Client returned |
| CP-004 | test_pool_release | Release connection | Connection returned |
| CP-005 | test_pool_reuse | Reuse released connection | Same connection |
| CP-006 | test_pool_max_size | Pool respects max size | Max limit enforced |
| CP-007 | test_pool_exhausted | Acquire when exhausted | PoolExhaustedError |
| CP-008 | test_pool_idle_timeout | Idle connections timeout | Connection destroyed |
| CP-009 | test_pool_health_check | Health check removes bad | Unhealthy removed |
| CP-010 | test_pool_close | Close pool gracefully | All connections closed |
| CP-011 | test_pool_context_manager | Context manager usage | Proper acquire/release |
| CP-012 | test_pool_stats | Get pool statistics | Stats returned |
| CP-013 | test_pool_concurrent | Concurrent acquires | No race conditions |
| CP-014 | test_pool_factory_async | Async factory function | Works correctly |
| CP-015 | test_pool_factory_sync | Sync factory function | Works correctly |
| CP-016 | test_pool_acquire_timeout | Acquire with timeout | TimeoutError |
| CP-017 | test_pool_invalid_config | Invalid min/max config | ValueError |
| CP-018 | test_pool_closed_acquire | Acquire from closed pool | ConnectionPoolError |
| CP-019 | test_pool_manager_create | Create pool via manager | Pool stored |
| CP-020 | test_pool_manager_get_client | Get client via manager | Client returned |
| CP-021 | test_pool_release_excess | Release when pool full | Excess destroyed |
| CP-022 | test_pool_connection_count | Track in-use connections | Accurate count |
| CP-023 | test_pool_acquire_time_stats | Record acquire times | Avg time tracked |
| CP-024 | test_pool_health_checker | Background health check | Runs periodically |
| CP-025 | test_pool_multiple_clients | Multiple pools | Isolated pools |
| CP-026 | test_pool_zero_min_size | Pool with min_size=0 | Works correctly |
| CP-027 | test_pool_single_connection | Pool with max_size=1 | Single connection |
| CP-028 | test_pool_stress | Stress test with many acquires | No failures |
| CP-029 | test_pool_llm_integration | Integration with LLM client | Works with real client |
| CP-030 | test_pool_performance | Performance benchmark | Throughput target |

---

## 7. Integration Strategy

### 7.1 DIContainer with AgentExecutor

```python
# Integration: DIContainer + AgentExecutor
from gaia.core.di_container import DIContainer
from gaia.core.executor import AgentExecutor
from gaia.core.profile import AgentProfile

# Configure container
container = DIContainer()
container.register_singleton("config", ConfigManager)
container.register_transient("llm_client", LemonadeClient)

# Create agent with DI
profile = AgentProfile(id="agent-1", name="Agent", role="Assistant", system_prompt="...")
executor = AgentExecutor(profile=profile, di_container=container)

# Use in scope
async with container.enter_scope():
    result = await executor.run_step("Task", {})
```

### 7.2 ConnectionPool with DIContainer

```python
# Integration: ConnectionPool registered in DI
container = DIContainer()

# Register pool as singleton
pool = ConnectionPool(
    client_factory=lambda: LemonadeClient(model_id="Qwen3.5-35B"),
    max_size=10,
)
container.register_singleton("llm_pool", pool)

# Register factory that uses pool
container.register_factory("llm_client", lambda: pool.acquire())
```

### 7.3 AgentAdapter Migration Path

```
Migration Path:
1. [Current] Legacy Agent class with deep inheritance
2. [Sprint 2] Wrap with AgentAdapter for unified interface
3. [Future] Migrate to AgentProfile + AgentExecutor pattern

Code Evolution:
# Before (Sprint 1)
agent = CodeAgent(debug=True, max_steps=10)

# Sprint 2 (Transition)
legacy = CodeAgent(debug=True)
adapter = AgentAdapter(legacy)
response = await adapter.run_step("Task")

# Target (Sprint 3+)
profile = AgentProfile(id="code", name="Code Agent", ...)
executor = AgentExecutor(profile, container)
response = await executor.run_step("Task")
```

---

## 8. Test Strategy

### 8.1 Unit Tests

| Component | Test File | Test Count | Focus Areas |
|-----------|-----------|------------|-------------|
| DIContainer | `tests/unit/core/test_di_container.py` | 40 | Registration, resolution, scopes |
| AgentAdapter | `tests/unit/core/test_agent_adapter.py` | 30 | Profile extraction, delegation |
| AsyncUtils | `tests/unit/perf/test_async_utils.py` | 20 | Caching, rate limiting, retry |
| ConnectionPool | `tests/unit/perf/test_connection_pool.py` | 30 | Pool management, concurrency |

### 8.2 Integration Tests

| Test ID | Test Name | Description |
|---------|-----------|-------------|
| INT-001 | test_di_with_executor | DIContainer + AgentExecutor integration |
| INT-002 | test_di_with_pool | DIContainer + ConnectionPool integration |
| INT-003 | test_adapter_with_legacy_code | AgentAdapter with real legacy agents |
| INT-004 | test_async_utils_real_api | Async retry with real API |
| INT-005 | test_pool_with_llm | ConnectionPool with LLM client |

### 8.3 Performance Benchmarks

| Benchmark | Target | Measurement |
|-----------|--------|-------------|
| DI resolution latency | <0.1ms | Time to resolve service |
| Pool acquire latency | <5ms | Time to acquire connection |
| Pool throughput | >100 req/s | Requests per second |
| Cache hit rate | >80% | Cache effectiveness |
| Async overhead | <5% | Sync vs async comparison |

---

## 9. Quality Gate 4 Criteria (Sprint 2 Continuation)

| Criteria | Test Method | Target | Owner |
|----------|-------------|--------|-------|
| **DI-001** | DIContainer resolution accuracy | 100% | senior-developer |
| **DI-002** | Service lifetime correctness | Singleton/Transient/Scoped work | testing-quality |
| **BC-001** | AgentAdapter backward compat | 100% legacy agents work | senior-developer |
| **PERF-001** | Connection pool throughput | >100 req/s | testing-quality |
| **PERF-002** | Async utils overhead | <5% vs raw async | testing-quality |
| **THREAD-001** | Thread safety (100+ concurrent) | No race conditions | testing-quality |

---

## 10. Implementation Checklist

### Week 4: Dependency Injection Core

- [ ] Create `src/gaia/core/di_container.py` (250 LOC)
- [ ] Implement ServiceDefinition dataclass
- [ ] Implement singleton registration/resolution
- [ ] Implement transient registration/resolution
- [ ] Implement scoped registration/resolution
- [ ] Implement factory registration
- [ ] Implement circular dependency detection
- [ ] Implement LLM client helper methods
- [ ] Implement scope management (enter_scope/exit_scope)
- [ ] Create `tests/unit/core/test_di_container.py` (40 tests)
- [ ] Create `src/gaia/core/adapter.py` (200 LOC)
- [ ] Implement AgentAdapter class
- [ ] Implement profile extraction logic
- [ ] Implement attribute delegation
- [ ] Implement LegacyAgentWrapper
- [ ] Create `tests/unit/core/test_agent_adapter.py` (30 tests)

### Week 5: Performance Layer Start

- [ ] Create `src/gaia/perf/async_utils.py` (150 LOC)
- [ ] Implement async_cached decorator
- [ ] Implement AsyncRateLimiter class
- [ ] Implement async_retry decorator
- [ ] Implement async_timeout decorator
- [ ] Implement AsyncBoundedExecutor class
- [ ] Implement debounce/throttle decorators
- [ ] Create `tests/unit/perf/test_async_utils.py` (20 tests)
- [ ] Create `src/gaia/perf/connection_pool.py` (300 LOC)
- [ ] Implement PooledConnection wrapper
- [ ] Implement PoolStats dataclass
- [ ] Implement ConnectionPool class
- [ ] Implement acquire/release methods
- [ ] Implement health checking
- [ ] Implement PoolManager for multiple pools
- [ ] Create `tests/unit/perf/test_connection_pool.py` (30 tests)

### Week 6: Testing & Validation

- [ ] Run all unit tests (120+ tests)
- [ ] Run integration tests (5+ tests)
- [ ] Execute performance benchmarks
- [ ] Validate Quality Gate 4 criteria
- [ ] Create Sprint 2 closeout document
- [ ] Update `future-where-to-resume-left-off.md`

---

## Appendix A: File Reference

| File | Absolute Path | Purpose | LOC |
|------|---------------|---------|-----|
| `di_container.py` | `C:\Users\antmi\gaia\src\gaia\core\di_container.py` | Dependency injection | 250 |
| `adapter.py` | `C:\Users\antmi\gaia\src\gaia\core\adapter.py` | Backward compatibility | 200 |
| `async_utils.py` | `C:\Users\antmi\gaia\src\gaia\perf\async_utils.py` | Async utilities | 150 |
| `connection_pool.py` | `C:\Users\antmi\gaia\src\gaia\perf\connection_pool.py` | Connection pooling | 300 |
| `test_di_container.py` | `C:\Users\antmi\gaia\tests\unit\core\test_di_container.py` | DI tests | 40 tests |
| `test_agent_adapter.py` | `C:\Users\antmi\gaia\tests\unit\core\test_agent_adapter.py` | Adapter tests | 30 tests |
| `test_async_utils.py` | `C:\Users\antmi\gaia\tests\unit\perf\test_async_utils.py` | Async tests | 20 tests |
| `test_connection_pool.py` | `C:\Users\antmi\gaia\tests\unit\perf\test_connection_pool.py` | Pool tests | 30 tests |

---

**END OF SPECIFICATION**

**Distribution:** GAIA Development Team
**Next Action:** senior-developer begins Sprint 2 implementation (Week 4, Day 1)
**Version History:**
- v1.0: Initial Sprint 2 specification (2026-04-06)
