# Phase 3 Implementation Plan: Architectural Modernization

**Document Version:** 2.0
**Date:** 2026-04-06
**Status:** SPRINT 1 COMPLETE - SPRINT 2 READY
**Duration:** 12 weeks (4 Sprints)
**Owner:** senior-developer
**Classification:** Strategic Architecture Document

---

## Executive Summary

Phase 3 (Architectural Modernization) addresses deep structural issues in the GAIA framework to prepare for production-scale deployment. This 12-week implementation refactors the monolithic Agent class, decouples service dependencies, and establishes enterprise-ready patterns for configuration, observability, and API standardization.

### Phase 3 Overview

| Dimension | Target | Notes |
|-----------|--------|-------|
| **Duration** | 12 weeks | 4 sprints (3 weeks each) |
| **FTE Effort** | 36 person-weeks | senior-developer primary |
| **Deliverables** | 4 focus areas | Modular Architecture, Performance, Enterprise Readiness, API Standardization |
| **Exit Criteria** | Quality Gate 4 | 10 criteria, 180+ tests |

### Phase 2 Handoff Summary

Phase 2 established quality enhancement and security hardening:
- **SupervisorAgent** (848 LOC): LLM-based quality review orchestration
- **ReviewOps** (526 LOC): Consensus aggregation tools
- **TokenCounter** (336 LOC): Tiktoken integration for accurate counting
- **ContextLens** (569 LOC): Relevance-based context prioritization
- **EmbeddingRelevance** (443 LOC): Semantic similarity scoring
- **WorkspacePolicy** (667 LOC): Hard filesystem boundaries
- **SecurityValidator** (503 LOC): Audit logging and path validation
- **PipelineIsolation** (541 LOC): Cross-pipeline isolation

**Phase 2 Test Coverage:** 274 tests at 100% pass rate
**Quality Gate 3:** PASS (6/6 criteria met)
**Program Progress:** 75% complete (Phase 0, 1, 2 done)

### Phase 3 Action Items from Phase 2

| ID | Action Item | Priority | Sprint | Owner |
|----|-------------|----------|--------|-------|
| AI-2.1 | Document Agent-as-Data migration path | HIGH | Sprint 1 | technical-writer |
| AI-2.2 | Define service layer interface contracts | HIGH | Sprint 1 | senior-developer |
| AI-2.3 | Establish OpenAPI specification standards | MEDIUM | Sprint 2 | senior-developer |
| AI-2.4 | Create configuration schema documentation | MEDIUM | Sprint 2 | technical-writer |

---

## 1. Implementation Scope

### 1.1 Focus Areas & Components

#### Focus Area 1: Modular Architecture (Sprints 1-2)

| Component | File | LOC Estimate | Tests | Priority | Sprint |
|-----------|------|--------------|-------|----------|--------|
| **AgentProfile** | `src/gaia/core/profile.py` | ~200 | 20 | P0 | Sprint 1 |
| **AgentExecutor** | `src/gaia/core/executor.py` | ~400 | 40 | P0 | Sprint 1 |
| **PluginRegistry** | `src/gaia/core/plugins.py` | ~300 | 30 | P1 | Sprint 1 |
| **DependencyInjection** | `src/gaia/core/di.py` | ~250 | 25 | P1 | Sprint 2 |
| **AgentAdapter** | `src/gaia/core/adapter.py` | ~200 | 20 | P0 | Sprint 2 |

#### Focus Area 2: Performance Optimization (Sprints 2-3)

| Component | File | LOC Estimate | Tests | Priority | Sprint |
|-----------|------|--------------|-------|----------|--------|
| **AsyncUtils** | `src/gaia/perf/async_utils.py` | ~150 | 15 | P0 | Sprint 2 |
| **ConnectionPool** | `src/gaia/perf/pool.py` | ~300 | 30 | P0 | Sprint 2 |
| **CacheLayer** | `src/gaia/perf/cache.py` | ~400 | 40 | P1 | Sprint 3 |
| **RateLimiter** | `src/gaia/perf/ratelimit.py` | ~200 | 20 | P1 | Sprint 3 |

#### Focus Area 3: Enterprise Readiness (Sprints 3-4)

| Component | File | LOC Estimate | Tests | Priority | Sprint |
|-----------|------|--------------|-------|----------|--------|
| **ConfigSchema** | `src/gaia/config/schema.py` | ~300 | 30 | P0 | Sprint 3 |
| **ConfigManager** | `src/gaia/config/manager.py` | ~400 | 40 | P0 | Sprint 3 |
| **SecretsManager** | `src/gaia/config/secrets.py` | ~350 | 35 | P0 | Sprint 3 |
| **ObservabilityCore** | `src/gaia/observability/core.py` | ~500 | 50 | P1 | Sprint 4 |
| **MetricsCollector** | `src/gaia/observability/metrics.py` | ~300 | 30 | P1 | Sprint 4 |

#### Focus Area 4: API Standardization (Sprint 4)

| Component | File | LOC Estimate | Tests | Priority | Sprint |
|-----------|------|--------------|-------|----------|--------|
| **OpenAPISpec** | `src/gaia/api/openapi.py` | ~400 | 40 | P0 | Sprint 4 |
| **APIVersioning** | `src/gaia/api/versioning.py` | ~200 | 20 | P0 | Sprint 4 |
| **DeprecationLayer** | `src/gaia/api/deprecation.py` | ~150 | 15 | P1 | Sprint 4 |

### 1.2 What's In Scope

- Agent-as-Data refactoring with flat configuration
- Behavior injection via AgentExecutor (composition over inheritance)
- Plugin system for extensibility
- Dependency injection container
- Async/await pattern standardization
- Connection pooling for LLM clients
- Multi-layer caching (memory, disk, distributed-ready)
- Rate limiting and throttling
- Configuration schema validation
- Secrets management (environment, file, vault-ready)
- Observability stack (metrics, tracing, logging)
- OpenAPI 3.0 specification
- API versioning strategy
- Deprecation policy and backward compatibility

### 1.3 What's Out of Scope

- Complete rewrite of existing agents (adaptation only)
- Migration of all tools to new patterns (gradual migration)
- Breaking changes to external APIs (backward compatible)
- Database schema changes (additive only)
- UI/frontend changes (backend only)

---

## 2. Sprint Schedule

### Sprint 1: Modular Architecture Core (Weeks 1-3)

**Objective:** Implement Agent-as-Data pattern with AgentProfile and AgentExecutor.

#### Week 1: AgentProfile Implementation

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/core/profile.py` | senior-developer | AgentProfile dataclass |
| 3 | Implement profile validation | senior-developer | Schema validation |
| 4-5 | Unit tests for AgentProfile | testing-quality-specialist | 20 test functions |

#### Week 2: AgentExecutor Core

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-3 | Create `src/gaia/core/executor.py` | senior-developer | AgentExecutor engine |
| 4 | Implement behavior injection | senior-developer | Tool execution, context formatting |
| 5 | Unit tests for AgentExecutor | testing-quality-specialist | 20 test functions |

#### Week 3: Plugin System & Adapter

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/core/plugins.py` | senior-developer | PluginRegistry |
| 3 | Create `src/gaia/core/adapter.py` | senior-developer | Backward-compatible AgentAdapter |
| 4-5 | Integration tests | testing-quality-specialist | 20 test functions |

### Sprint 2: Modular Architecture Completion & Performance Start (Weeks 4-6)

**Objective:** Complete dependency injection and begin performance optimization layer.

**Status:** READY FOR IMPLEMENTATION
**Technical Specification:** `docs/reference/phase3-sprint2-technical-spec.md`

#### Week 4: Dependency Injection

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/core/di_container.py` | senior-developer | DI container, service registration (~250 LOC) |
| 3 | Unit tests for DIContainer | testing-quality-specialist | 40 test functions |
| 4-5 | Create `src/gaia/core/adapter.py` | senior-developer | AgentAdapter for backward compatibility (~200 LOC) |

#### Week 5: Async Utils & Connection Pool

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/perf/async_utils.py` | senior-developer | Async utilities, decorators (~150 LOC) |
| 3-5 | Create `src/gaia/perf/connection_pool.py` | senior-developer | ConnectionPool for LLM clients (~300 LOC) |

#### Week 6: Performance Tests

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Unit tests for async/pool | testing-quality-specialist | 50 test functions |
| 3-4 | Performance benchmarks | testing-quality-specialist | Baseline metrics |
| 5 | Sprint 2 closeout | software-program-manager | Sprint 2 summary

**Sprint 2 Deliverables:**
- DIContainer: `src/gaia/core/di_container.py` (250 LOC, 40 tests)
- AgentAdapter: `src/gaia/core/adapter.py` (200 LOC, 30 tests)
- AsyncUtils: `src/gaia/perf/async_utils.py` (150 LOC, 20 tests)
- ConnectionPool: `src/gaia/perf/connection_pool.py` (300 LOC, 30 tests)
- Total: 900 LOC, 120 tests |

### Sprint 3: Caching & Enterprise Config (Weeks 7-9)

**Objective:** Implement caching layer and enterprise-ready configuration management.

#### Week 7: Cache Layer

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-3 | Create `src/gaia/perf/cache.py` | senior-developer | Multi-layer cache (memory, disk) |
| 4-5 | Unit tests for caching | testing-quality-specialist | 40 test functions |

#### Week 8: Configuration Management

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/config/schema.py` | senior-developer | ConfigSchema with validation |
| 3-5 | Create `src/gaia/config/manager.py` | senior-developer | ConfigManager with hot-reload |

#### Week 9: Secrets & Sprint 3 Closeout

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/config/secrets.py` | senior-developer | SecretsManager |
| 3 | Unit tests for config/secrets | testing-quality-specialist | 65 test functions |
| 4-5 | Quality Gate 4 prep | quality-reviewer | QG4 assessment |

### Sprint 4: Observability & API Standardization (Weeks 10-12)

**Objective:** Implement observability stack and API standardization.

#### Week 10: Observability Core

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-3 | Create `src/gaia/observability/core.py` | senior-developer | ObservabilityCore (tracing, logging) |
| 4-5 | Create `src/gaia/observability/metrics.py` | senior-developer | MetricsCollector |

#### Week 11: API Standardization

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-3 | Create `src/gaia/api/openapi.py` | senior-developer | OpenAPI 3.0 spec generation |
| 4 | Create `src/gaia/api/versioning.py` | senior-developer | API versioning strategy |
| 5 | Create `src/gaia/api/deprecation.py` | senior-developer | Deprecation warnings |

#### Week 12: Final Testing & Quality Gate 4

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Full regression testing | testing-quality-specialist | All 180+ tests passing |
| 3-4 | Performance benchmarks | testing-quality-specialist | All targets met |
| 5 | Quality Gate 4 validation | quality-reviewer | QG4 decision |
| 6-7 | Phase 3 closeout | software-program-manager | Phase 3 summary |
| 8-9 | Documentation finalization | technical-writer | Migration guides |
| 10 | Phase 3 retrospective | software-program-manager | Lessons learned |

---

## 3. Technical Architecture

### 3.1 Modular Architecture

#### 3.1.1 Agent-as-Data Pattern

**Current State (Problem):**
```python
# Current: Deep inheritance, 40+ parameter constructor
class CodeAgent(Agent, MCPAgent, ApiAgent, RAGMixin, ToolMixin, ...):
    def __init__(
        self,
        agent_id=None,
        model_id=None,
        system_prompt=None,
        user_info=None,
        max_plan_iterations=3,
        max_steps_per_plan=15,
        # ... 35+ more parameters
        **kwargs
    ):
        # 3,000+ lines of mixed behavior and configuration
```

**Target State (Solution):**
```python
# src/gaia/core/profile.py
"""
Agent-as-Data: Pure configuration, no behavior.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Literal


@dataclass
class AgentProfile:
    """
    Pure-data agent configuration.

    This class contains ONLY configuration data.
    All behavior is injected by AgentExecutor.

    Attributes:
        id: Unique agent identifier
        name: Human-readable name
        role: Agent role description
        system_prompt: System prompt template
        model: Model identifier
        tools: List of allowed tool names
        capabilities: Capability flags
        constraints: Operational constraints
        knowledge_base: Optional knowledge base paths
        max_steps: Maximum execution steps
        max_plan_iterations: Maximum planning iterations
    """

    id: str
    name: str
    role: str
    system_prompt: str
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
        self._validate()

    def _validate(self):
        """Validate profile configuration."""
        if not self.id:
            raise ValueError("Agent id is required")
        if not self.name:
            raise ValueError("Agent name is required")
        if not self.system_prompt:
            raise ValueError("System prompt is required")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
```

#### 3.1.2 AgentExecutor Pattern

```python
# src/gaia/core/executor.py
"""
AgentExecutor: Behavior injection engine.

This class provides all execution behavior for an AgentProfile.
Uses composition over inheritance pattern.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional
from gaia.core.profile import AgentProfile
from gaia.core.di import DIContainer
from gaia.state.nexus import NexusService
from gaia.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)


class AgentExecutor:
    """
    Agent execution engine with injected behavior.

    The executor accepts an AgentProfile (data) and provides
    all execution behavior through composition.

    Key Responsibilities:
    - LLM interaction
    - Tool execution
    - State management
    - Context formatting
    - Error handling

    Example:
        >>> profile = AgentProfile(id="code-agent", ...)
        >>> executor = AgentExecutor(profile, di_container)
        >>> response = await executor.run_step(topic="Build API", context={})
    """

    def __init__(
        self,
        profile: AgentProfile,
        di_container: DIContainer,
        nexus: Optional[NexusService] = None,
    ):
        self.profile = profile
        self.di_container = di_container
        self.nexus = nexus or NexusService.get_instance()
        self.llm_client = self._get_llm_client()
        self.tool_scope = self._create_tool_scope()
        self._state = {}
        self._lock = asyncio.Lock()

    def _get_llm_client(self) -> BaseLLMClient:
        """Get LLM client from DI container."""
        return self.di_container.get_llm_client(self.profile.model)

    def _create_tool_scope(self):
        """Create scoped tool access for this agent."""
        from gaia.agents.base.tools import ToolRegistry
        return ToolRegistry.get_instance().create_scope(
            agent_id=self.profile.id,
            allowed_tools=self.profile.tools,
        )

    async def run_step(
        self,
        topic: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Run single agent step with injected behavior.

        Args:
            topic: Current topic/task
            context: Execution context

        Returns:
            Agent response dictionary
        """
        async with self._lock:
            # 1. Get curated context from state service
            curated = self.nexus.get_context_for_agent(self.profile.id)

            # 2. Build system prompt from profile
            system_prompt = self._build_system_prompt()

            # 3. Invoke LLM
            response = await self.llm_client.chat(
                system_prompt=system_prompt,
                user_message=f"Topic: {topic}\nContext: {curated}",
                tools=self._get_available_tools(),
            )

            # 4. Execute tool calls if any
            if response.tool_calls:
                results = await self._execute_tools(response.tool_calls)
                response.tool_results = results

            # 5. Commit to Chronicle
            self.nexus.commit(
                agent_id=self.profile.id,
                event_type="THOUGHT",
                payload=response.dict(),
            )

            return response.dict()

    def _build_system_prompt(self) -> str:
        """Build system prompt from profile."""
        # Inject constraints, capabilities from profile
        prompt = self.profile.system_prompt
        if self.profile.constraints:
            prompt += "\n\nConstraints:\n"
            for key, value in self.profile.constraints.items():
                prompt += f"- {key}: {value}\n"
        return prompt
```

#### 3.1.3 Backward Compatibility Adapter

```python
# src/gaia/core/adapter.py
"""
AgentAdapter: Backward-compatible wrapper.

Wraps legacy Agent class instances to work with new AgentExecutor pattern.
Enables gradual migration without breaking existing code.
"""

from typing import Any, Dict, Optional
from gaia.core.profile import AgentProfile
from gaia.core.executor import AgentExecutor
from gaia.agents.base.agent import Agent


class AgentAdapter:
    """
    Adapter for legacy Agent instances.

    This wrapper allows legacy Agent subclasses to work
    alongside new AgentExecutor-based agents.

    Example:
        >>> legacy_agent = CodeAgent(...)
        >>> adapter = AgentAdapter(legacy_agent)
        >>> response = await adapter.run_step(topic="...", context={})
    """

    def __init__(self, legacy_agent: Agent):
        self.legacy_agent = legacy_agent
        self.profile = self._extract_profile(legacy_agent)
        self._wrapped = False

    def _extract_profile(self, agent: Agent) -> AgentProfile:
        """Extract AgentProfile from legacy agent."""
        return AgentProfile(
            id=getattr(agent, 'agent_id', 'legacy-agent'),
            name=getattr(agent, 'name', agent.__class__.__name__),
            role=getattr(agent, 'role', 'Legacy Agent'),
            system_prompt=getattr(agent, 'system_prompt', ''),
            model=getattr(agent, 'model_id', 'Qwen3.5-35B-A3B-GGUF'),
            tools=getattr(agent, 'allowed_tools', []),
            max_steps=getattr(agent, 'max_steps', 20),
            max_plan_iterations=getattr(agent, 'max_plan_iterations', 3),
        )

    async def run_step(
        self,
        topic: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Delegate to legacy agent."""
        return await self.legacy_agent.run_step(topic, context)

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to legacy agent."""
        return getattr(self.legacy_agent, name)
```

### 3.2 Performance Optimization

#### 3.2.1 Async/await Pattern Standardization

```python
# src/gaia/perf/async_utils.py
"""
Async utilities for standardized async/await patterns.
"""

import asyncio
import functools
from typing import Any, Callable, Optional


def async_cached(timeout: int = 300):
    """
    Decorator for async function caching.

    Args:
        timeout: Cache TTL in seconds

    Example:
        @async_cached(timeout=600)
        async def get_llm_response(prompt: str) -> str:
            ...
    """
    cache = {}

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            key = (args, tuple(sorted(kwargs.items())))
            if key in cache:
                result, timestamp = cache[key]
                if asyncio.get_event_loop().time() - timestamp < timeout:
                    return result
            result = await func(*args, **kwargs)
            cache[key] = (result, asyncio.get_event_loop().time())
            return result
        return wrapper
    return decorator


class AsyncRateLimiter:
    """
    Async rate limiter with token bucket algorithm.

    Example:
        limiter = AsyncRateLimiter(rate=10, capacity=20)
        async with limiter:
            await make_api_call()
    """

    def __init__(self, rate: float, capacity: int):
        self.rate = rate  # tokens per second
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = asyncio.get_event_loop().time()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """Acquire a token (wait if necessary)."""
        async with self._lock:
            now = asyncio.get_event_loop().time()
            self.tokens = min(
                self.capacity,
                self.tokens + (now - self.last_update) * self.rate
            )
            self.last_update = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
```

#### 3.2.2 Connection Pooling

```python
# src/gaia/perf/pool.py
"""
Connection pooling for LLM clients.
"""

import asyncio
from typing import Any, Dict, Optional
from gaia.llm.base_client import BaseLLMClient


class ConnectionPool:
    """
    Async connection pool for LLM clients.

    Manages a pool of reusable LLM client connections
    to reduce connection overhead and improve throughput.

    Example:
        pool = ConnectionPool(max_size=10)
        async with pool.get_connection() as client:
            response = await client.chat(...)
    """

    def __init__(
        self,
        client_factory: Callable,
        max_size: int = 10,
        min_size: int = 2,
        max_idle_time: float = 300,
    ):
        self.client_factory = client_factory
        self.max_size = max_size
        self.min_size = min_size
        self.max_idle_time = max_idle_time
        self._pool = asyncio.Queue(maxsize=max_size)
        self._created = 0
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self):
        """Pre-create minimum connections."""
        if self._initialized:
            return
        async with self._lock:
            for _ in range(self.min_size):
                client = await self._create_client()
                await self._pool.put(client)
            self._initialized = True

    async def _create_client(self) -> BaseLLMClient:
        """Create new LLM client."""
        return await self.client_factory()

    async def get_connection(self) -> BaseLLMClient:
        """Get connection from pool (create if necessary)."""
        if not self._initialized:
            await self.initialize()

        try:
            # Try to get existing connection
            client = self._pool.get_nowait()
            return client
        except asyncio.QueueEmpty:
            async with self._lock:
                if self._created < self.max_size:
                    # Create new connection
                    client = await self._create_client()
                    self._created += 1
                    return client
                else:
                    # Wait for available connection
                    return await self._pool.get()

    async def return_connection(self, client: BaseLLMClient):
        """Return connection to pool."""
        try:
            self._pool.put_nowait(client)
        except asyncio.QueueFull:
            # Pool is full, close connection
            await client.close()
            self._created -= 1

    async def close(self):
        """Close all connections."""
        while not self._pool.empty():
            client = await self._pool.get()
            await client.close()
```

### 3.3 Enterprise Readiness

#### 3.3.1 Configuration Schema

```python
# src/gaia/config/schema.py
"""
Configuration schema with validation.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, validator


class LLMConfig(BaseModel):
    """LLM configuration."""
    provider: str = Field(..., description="LLM provider (lemonade, claude, openai)")
    model: str = Field(..., description="Model identifier")
    base_url: Optional[str] = Field(None, description="Custom base URL")
    api_key_env: Optional[str] = Field(None, description="Env var for API key")
    timeout: int = Field(300, description="Request timeout in seconds")
    max_retries: int = Field(3, description="Max retry attempts")


class AgentConfig(BaseModel):
    """Agent configuration."""
    id: str = Field(..., description="Unique agent identifier")
    name: str = Field(..., description="Agent display name")
    model: str = Field(..., description="Default model")
    tools: List[str] = Field(default_factory=list, description="Allowed tools")
    max_steps: int = Field(20, description="Max execution steps")


class GAIAConfig(BaseModel):
    """Root GAIA configuration."""
    llm: LLMConfig
    agents: Dict[str, AgentConfig] = Field(default_factory=dict)
    workspace_root: str = Field("./workspace", description="Workspace root")
    log_level: str = Field("INFO", description="Logging level")
    debug: bool = Field(False, description="Debug mode")

    class Config:
        env_prefix = "GAIA_"
        env_file = ".env"
```

#### 3.3.2 Secrets Management

```python
# src/gaia/config/secrets.py
"""
Secrets management with multiple backends.
"""

import os
from abc import ABC, abstractmethod
from typing import Optional


class SecretsBackend(ABC):
    """Abstract secrets backend."""

    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        pass

    @abstractmethod
    def set(self, key: str, value: str) -> None:
        pass


class EnvironmentBackend(SecretsBackend):
    """Environment variable backend."""

    def get(self, key: str) -> Optional[str]:
        return os.environ.get(key)

    def set(self, key: str, value: str) -> None:
        os.environ[key] = value


class FileBackend(SecretsBackend):
    """File-based secrets backend."""

    def __init__(self, path: str):
        self.path = path
        self._secrets = self._load()

    def _load(self) -> Dict[str, str]:
        # Load from encrypted file
        ...

    def get(self, key: str) -> Optional[str]:
        return self._secrets.get(key)

    def set(self, key: str, value: str) -> None:
        self._secrets[key] = value
        self._save()


class SecretsManager:
    """
    Unified secrets manager.

    Supports multiple backends with fallback chain.

    Example:
        secrets = SecretsManager()
        api_key = secrets.get("LLM_API_KEY")
    """

    def __init__(self, backends: Optional[List[SecretsBackend]] = None):
        self.backends = backends or [
            EnvironmentBackend(),
            FileBackend("~/.gaia/secrets"),
        ]

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get secret from first available backend."""
        for backend in self.backends:
            value = backend.get(key)
            if value is not None:
                return value
        return default

    def set(self, key: str, value: str, backend_index: int = 0) -> None:
        """Set secret in specified backend."""
        self.backends[backend_index].set(key, value)
```

### 3.4 API Standardization

#### 3.4.1 OpenAPI Specification

```python
# src/gaia/api/openapi.py
"""
OpenAPI 3.0 specification generation.
"""

from typing import Any, Dict, List


class OpenAPISpec:
    """
    OpenAPI 3.0 specification builder.

    Generates OpenAPI spec from API routes.

    Example:
        spec = OpenAPISpec(
            title="GAIA API",
            version="3.0.0"
        )
        spec.add_path("/v1/chat/completions", post=completions_op)
        openapi_json = spec.to_dict()
    """

    def __init__(
        self,
        title: str,
        version: str,
        description: str = "",
    ):
        self.spec = {
            "openapi": "3.0.0",
            "info": {
                "title": title,
                "version": version,
                "description": description,
            },
            "paths": {},
            "components": {
                "schemas": {},
                "securitySchemes": {},
            },
        }

    def add_path(
        self,
        path: str,
        get: Optional[Dict] = None,
        post: Optional[Dict] = None,
        put: Optional[Dict] = None,
        delete: Optional[Dict] = None,
    ):
        """Add path to specification."""
        self.spec["paths"][path] = {}
        for method, op in [("get", get), ("post", post),
                           ("put", put), ("delete", delete)]:
            if op:
                self.spec["paths"][path][method] = op

    def add_schema(self, name: str, schema: Dict[str, Any]):
        """Add schema component."""
        self.spec["components"]["schemas"][name] = schema

    def to_dict(self) -> Dict[str, Any]:
        """Return spec as dictionary."""
        return self.spec

    def to_json(self) -> str:
        """Return spec as JSON string."""
        import json
        return json.dumps(self.spec, indent=2)
```

---

## 4. Test Strategy

### 4.1 Test Matrix

| Test File | Functions | Coverage | Priority | Sprint |
|-----------|-----------|----------|----------|--------|
| `test_agent_profile.py` | 20 | AgentProfile | CRITICAL | Sprint 1 |
| `test_agent_executor.py` | 40 | AgentExecutor | CRITICAL | Sprint 1 |
| `test_plugin_registry.py` | 30 | PluginRegistry | HIGH | Sprint 1 |
| `test_di_container.py` | 25 | DependencyInjection | CRITICAL | Sprint 2 |
| `test_agent_adapter.py` | 20 | AgentAdapter | CRITICAL | Sprint 2 |
| `test_async_utils.py` | 15 | AsyncUtils | HIGH | Sprint 2 |
| `test_connection_pool.py` | 30 | ConnectionPool | CRITICAL | Sprint 2 |
| `test_cache_layer.py` | 40 | CacheLayer | HIGH | Sprint 3 |
| `test_rate_limiter.py` | 20 | RateLimiter | MEDIUM | Sprint 3 |
| `test_config_schema.py` | 30 | ConfigSchema | CRITICAL | Sprint 3 |
| `test_config_manager.py` | 40 | ConfigManager | CRITICAL | Sprint 3 |
| `test_secrets_manager.py` | 35 | SecretsManager | CRITICAL | Sprint 3 |
| `test_observability_core.py` | 50 | ObservabilityCore | HIGH | Sprint 4 |
| `test_metrics_collector.py` | 30 | MetricsCollector | HIGH | Sprint 4 |
| `test_openapi_spec.py` | 40 | OpenAPISpec | CRITICAL | Sprint 4 |
| `test_api_versioning.py` | 20 | APIVersioning | HIGH | Sprint 4 |
| `test_deprecation_layer.py` | 15 | DeprecationLayer | MEDIUM | Sprint 4 |
| **Total** | **500+** | **Full coverage** | | |

### 4.2 Quality Gate 4 Criteria

| Criteria | Test | Target | Priority |
|----------|------|--------|----------|
| **MOD-001** | AgentProfile validation | 100% accuracy | CRITICAL |
| **MOD-002** | AgentExecutor behavior injection | Zero regression | CRITICAL |
| **MOD-003** | Backward compatibility | 100% existing agents work | CRITICAL |
| **PERF-006** | Connection pool throughput | >100 req/s | CRITICAL |
| **PERF-007** | Cache hit rate | >80% for repeated calls | HIGH |
| **PERF-008** | Async overhead | <5% vs sync | HIGH |
| **ENT-001** | Config schema validation | 100% invalid configs rejected | CRITICAL |
| **ENT-002** | Secrets retrieval | <10ms latency | HIGH |
| **API-001** | OpenAPI spec completeness | 100% endpoints documented | CRITICAL |
| **API-002** | API versioning | Zero breaking changes | CRITICAL |

---

## 5. Risk Management

### 5.1 Active Risks

| ID | Risk | Probability | Impact | Exposure | Mitigation Strategy | Sprint |
|----|------|-------------|--------|----------|---------------------|--------|
| R3.1 | Breaking change in Agent refactoring | MEDIUM | HIGH | HIGH | AgentAdapter layer, extensive BC testing | Sprint 1-2 |
| R3.2 | Performance regression in new patterns | MEDIUM | MEDIUM | MEDIUM | Benchmark each sprint, performance gates | All |
| R3.3 | Migration complexity for existing agents | HIGH | MEDIUM | MEDIUM | Gradual migration path, documentation | Sprint 2-4 |
| R3.4 | DI container complexity | LOW | MEDIUM | LOW | Simple API, comprehensive docs | Sprint 2 |
| R3.5 | Cache invalidation bugs | MEDIUM | MEDIUM | MEDIUM | TTL-based expiry, testing | Sprint 3 |
| R3.6 | OpenAPI spec drift from implementation | MEDIUM | LOW | LOW | Auto-generation from code | Sprint 4 |

### 5.2 Risk Triggers

| Risk | Trigger | Action |
|------|---------|--------|
| R3.1 | >5% existing agent tests fail | Immediate fix or rollback |
| R3.2 | Any benchmark worse than baseline | Optimize before proceeding |
| R3.3 | Team confusion on migration | Add migration examples, office hours |
| R3.4 | DI container misuse in code | Add linting rules, code review |
| R3.5 | Stale cache causing incorrect results | Add cache invalidation tests |
| R3.6 | OpenAPI spec doesn't match API | Auto-generate spec from routes |

---

## 6. Resource Estimates

### 6.1 Developer Effort

| Sprint | Duration | FTE | Person-Weeks | Focus |
|--------|----------|-----|--------------|-------|
| Sprint 1 | 3 weeks | 1.0 | 3.0 | Modular Architecture Core |
| Sprint 2 | 3 weeks | 1.0 | 3.0 | DI + Performance Start |
| Sprint 3 | 3 weeks | 1.0 | 3.0 | Caching + Enterprise Config |
| Sprint 4 | 3 weeks | 1.0 | 3.0 | Observability + API Standardization |
| **Total** | **12 weeks** | | **12.0** | |

### 6.2 Testing Effort

| Sprint | Testing FTE | Person-Weeks | Focus |
|--------|-------------|--------------|-------|
| Sprint 1 | 0.5 | 1.5 | AgentProfile, Executor, Plugin tests |
| Sprint 2 | 0.5 | 1.5 | DI, Adapter, Performance tests |
| Sprint 3 | 0.5 | 1.5 | Cache, Config, Secrets tests |
| Sprint 4 | 0.5 | 1.5 | Observability, API tests |
| **Total** | | **6.0** | |

### 6.3 Documentation Effort

| Document | Owner | Effort | Sprint |
|----------|-------|--------|--------|
| Agent-as-Data Migration Guide | technical-writer | 1.0 week | Sprint 2 |
| Performance Best Practices | technical-writer | 0.5 weeks | Sprint 3 |
| Configuration Reference | technical-writer | 0.5 weeks | Sprint 3 |
| API Migration Guide | technical-writer | 0.5 weeks | Sprint 4 |
| Phase 3 Closeout Report | software-program-manager | 0.5 weeks | Sprint 4 |
| **Total** | | **3.0 weeks** | |

---

## 7. Dependencies Map

### 7.1 Internal Dependencies

```
Phase 0 Complete (Tool Scoping)
       │
       ▼
Phase 1 Complete (State Unification)
       │
       ▼
Phase 2 Complete (Quality Enhancement)
       │
       ▼
┌─────────────────────────────────────┐
│         Phase 3 (Modernization)     │
│                                     │
│  Sprint 1-2: Modular Architecture   │
│  ├── AgentProfile                   │
│  ├── AgentExecutor                  │
│  ├── PluginRegistry                 │
│  └── DependencyInjection            │
│                                     │
│  Sprint 2-3: Performance Layer      │
│  ├── AsyncUtils                     │
│  ├── ConnectionPool                 │
│  ├── CacheLayer                     │
│  └── RateLimiter                    │
│                                     │
│  Sprint 3-4: Enterprise Readiness   │
│  ├── ConfigSchema                   │
│  ├── ConfigManager                  │
│  ├── SecretsManager                 │
│  └── ObservabilityCore              │
│                                     │
│  Sprint 4: API Standardization      │
│  ├── OpenAPISpec                    │
│  ├── APIVersioning                  │
│  └── DeprecationLayer               │
└─────────────────────────────────────┘
```

### 7.2 File Modification Summary

| Directory | Files | LOC Estimate | Tests |
|-----------|-------|--------------|-------|
| `src/gaia/core/` | 5 | ~1350 | 135 |
| `src/gaia/perf/` | 4 | ~1050 | 105 |
| `src/gaia/config/` | 3 | ~1050 | 105 |
| `src/gaia/observability/` | 2 | ~800 | 80 |
| `src/gaia/api/` | 3 | ~750 | 75 |
| `tests/unit/core/` | 5 | N/A | 135 |
| `tests/unit/perf/` | 4 | N/A | 105 |
| `tests/unit/config/` | 3 | N/A | 105 |
| `tests/unit/observability/` | 2 | N/A | 80 |
| `tests/unit/api/` | 3 | N/A | 75 |
| **Total** | **32** | **~5000** | **500+** |

---

## 8. Success Metrics

### 8.1 Technical Metrics

| Metric | Baseline (Phase 2) | Target | Measurement |
|--------|-------------------|--------|-------------|
| Base Agent LOC | ~3,000 | <500 (new pattern) | Code analysis |
| Mixin depth | 10-15 classes | 2-3 (composition) | Inheritance analysis |
| Agent creation time | Hours (class) | Minutes (YAML) | Developer survey |
| LLM connection overhead | N/A | <100ms | Benchmark |
| Cache hit rate | N/A | >80% | Metrics |
| Config validation time | N/A | <50ms | Benchmark |
| API documentation coverage | Partial | 100% | OpenAPI spec |

### 8.2 Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Test coverage | 100% new code | `pytest --cov` |
| Test pass rate | 100% | All tests pass |
| Backward compatibility | 100% existing agents | BC test suite |
| Performance | No regression | Benchmark suite |
| Security | 0 critical findings | Security audit |

---

## 9. Handoff Notes

### 9.1 For software-program-manager

**Resource Allocation:**
- senior-developer: 12 weeks full-time
- testing-quality-specialist: 6 weeks (50% throughout)
- quality-reviewer: Week 12 for Quality Gate 4
- technical-writer: 3 weeks (Sprint 2-4)

**Milestone Tracking:**
- Weekly progress reviews every Friday
- Escalate R3.1 (breaking changes) immediately
- Track against sprint schedule
- Monitor performance benchmarks each sprint

### 9.2 For senior-developer

**Implementation Notes:**
1. Start with AgentProfile (simple dataclass)
2. AgentExecutor is the core - invest in clean design
3. AgentAdapter is critical for backward compatibility
4. DI container should be simple, not over-engineered
5. Benchmark performance at end of each sprint
6. Auto-generate OpenAPI spec from code

**Key Design Decisions:**
- Agent-as-Data: Pure configuration, no behavior
- Behavior injection: Via AgentExecutor composition
- Backward compatibility: AgentAdapter wrapper
- DI: Simple container, not full IoC framework
- Caching: TTL-based with manual invalidation
- Secrets: Multi-backend with fallback chain

### 9.3 For testing-quality-specialist

**Test Priorities:**
1. Backward compatibility (100% existing agents must work)
2. Performance benchmarks (no regression)
3. Security (secrets handling, config validation)
4. Integration tests (end-to-end agent workflows)

**Test Infrastructure:**
- pytest 8.4.2+
- pytest-benchmark for performance
- pytest-asyncio for async tests
- pytest-cov for coverage

---

## 10. Approval & Sign-Off

**Prepared By:** Dr. Sarah Kim, planning-analysis-strategist
**Date:** 2026-04-06
**Next Action:** senior-developer begins Sprint 1

### Sign-Off Checklist

- [x] Technical feasibility confirmed
- [x] Resource allocation confirmed
- [x] Risk assessment acceptable
- [x] Test strategy comprehensive
- [x] Quality criteria defined
- [ ] **Team approval to begin Phase 3**

---

**END OF PLAN**

**Distribution:** GAIA Development Team, AMD AI Framework Team
**Review Cadence:** Weekly status reviews
**Version History:**
- v1.0: Initial Phase 3 specification (2026-04-06)
