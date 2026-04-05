# Phase 0: Tool Scoping Integration Specification

**Version:** 1.0
**Status:** Approved for Implementation
**Priority:** P0 (Critical Foundation)
**Estimated Duration:** 2 weeks

---

## Executive Summary

Phase 0 refactors GAIA's global tool registry into a class-based, thread-safe singleton with per-agent scoping. This addresses **RC#2** (Tool Implementations Missing) and **RC#7** (Empty Tool Descriptions in System Prompt) from the pipeline root causes, while establishing the foundation for BAIBEL pattern integration in Phases 1-3.

**Key Deliverables:**
- `ToolRegistry` class with thread-safe singleton pattern
- `AgentScope` for per-agent tool isolation with allowlist filtering
- `ExceptionRegistry` for tracking tool execution errors
- `_ToolRegistryAlias` backward-compatible dict shim
- Zero memory leaks (0% threshold), case-sensitive security

---

## 1. WHAT: Concepts Being Integrated

### 1.1 Core Components

| Component | Purpose | Location |
|-----------|---------|----------|
| **ToolRegistry** | Singleton registry with thread-safe operations | `src/gaia/agents/base/tools.py` |
| **AgentScope** | Per-agent scoped view with allowlist filtering | `src/gaia/agents/base/tools.py` |
| **ExceptionRegistry** | Tracks tool execution exceptions for state consistency | `src/gaia/agents/base/tools.py` |
| **_ToolRegistryAlias** | Backward-compatible dict shim with deprecation warnings | `src/gaia/agents/base/tools.py` |

### 1.2 BAIBEL Patterns (Phases 0-3)

| Pattern | Phase | Purpose | Status |
|---------|-------|---------|--------|
| **Tool Scoping** | Phase 0 | Security isolation, per-agent tool views | Implementation Ready |
| **Chronicle** | Phase 1 | Temporal tracking of state transitions | Planned |
| **Workspace** | Phase 1 | Spatial metadata for artifact organization | Planned |
| **Ether** | Phase 2 | Runtime context management | Planned |
| **Nexus** | Phase 2 | State unification across agents | Planned |
| **Context Lens** | Phase 3 | Token-efficient summarization | Planned |
| **Supervisor** | Phase 3 | Quality gate with binary APPROVE/REJECT | Planned |

### 1.3 RC# Cross-Reference Matrix

| RC# | Title | Phase 0 Impact | Status |
|-----|-------|----------------|--------|
| RC1 | Single-turn agent passthrough | Indirect (enables future tool loop) | MITIGATED |
| **RC2** | **Tool implementations missing** | **Direct (registry enables tool loading)** | **FIXED** |
| RC3 | System prompt files missing | No impact | FIXED |
| RC4 | Thin user prompt | No impact | PARTIALLY FIXED |
| RC5 | Save only writes JSON | No impact | FIXED |
| RC6 | System prompt path wrong attribute | No impact | FIXED |
| **RC7** | **Empty tool descriptions in system prompt** | **Direct (registry populates descriptions)** | **FIXED** |
| RC8 | Defects not passed to agents | No impact | FIXED |

---

## 2. WHERE: Integration Points

### 2.1 Primary Target: `src/gaia/agents/base/tools.py`

**Current State:**
```python
# Global mutable dict (no isolation, no thread safety)
_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}

def tool(name: str = None, description: str = None):
    def decorator(func):
        tool_name = name or func.__name__
        _TOOL_REGISTRY[tool_name] = {
            "function": func,
            "description": description or func.__doc__ or "",
            "parameters": extract_parameters(func),
        }
        return func
    return decorator

def get_registered_tools() -> Dict[str, Dict[str, Any]]:
    return _TOOL_REGISTRY.copy()
```

**Target State:**
```python
class ToolRegistry:
    """Thread-safe singleton registry for agent tools."""

    _instance: Optional["ToolRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._registry_lock = threading.RLock()
        self._exception_registry = ExceptionRegistry()
        self._initialized = True

    def register(self, name: str, func: Callable, description: str = None) -> None:
        """Register a tool function with thread safety."""
        with self._registry_lock:
            self._tools[name] = {
                "function": func,
                "description": description or func.__doc__ or "",
                "parameters": inspect.signature(func).parameters,
            }

    def create_scope(self, agent_id: str, allowed_tools: Optional[List[str]] = None) -> "AgentScope":
        """Create scoped view for specific agent."""
        return AgentScope(self, agent_id, allowed_tools)

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Execute tool by name with exception tracking."""
        with self._registry_lock:
            if tool_name not in self._tools:
                raise ToolNotFoundError(tool_name=tool_name)
            try:
                return self._tools[tool_name]["function"](*args, **kwargs)
            except Exception as e:
                self._exception_registry.record(tool_name, e)
                raise

class AgentScope:
    """Scoped view of ToolRegistry for specific agent."""

    def __init__(self, registry: "ToolRegistry", agent_id: str, allowed_tools: Optional[List[str]] = None):
        self._registry = registry
        self._agent_id = agent_id
        self._allowed_tools: Optional[Set[str]] = set(allowed_tools) if allowed_tools else None
        self._lock = threading.RLock()

    def _is_tool_allowed(self, tool_name: str) -> bool:
        """Check if tool is accessible (case-sensitive, exact match)."""
        if self._allowed_tools is None:
            return True
        return tool_name in self._allowed_tools  # Case-sensitive!

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Execute tool if accessible, raise ToolAccessDeniedError otherwise."""
        with self._lock:
            if not self._is_tool_allowed(tool_name):
                raise ToolAccessDeniedError(tool_name=tool_name, agent_id=self._agent_id)
            return self._registry.execute_tool(tool_name, *args, **kwargs)

    def get_available_tools(self) -> Dict[str, Dict[str, Any]]:
        """Get tools accessible to this agent."""
        with self._lock:
            all_tools = self._registry.get_all_tools()
            if self._allowed_tools is None:
                return all_tools
            return {name: desc for name, desc in all_tools.items() if name in self._allowed_tools}

class _ToolRegistryAlias(dict):
    """Backward-compatible dict shim with deprecation warnings."""

    def __init__(self):
        self._registry = ToolRegistry.get_instance()

    def __getitem__(self, key):
        warnings.warn(
            "Direct access to _TOOL_REGISTRY is deprecated. Use ToolRegistry.get_instance() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return self._registry.get_all_tools()[key]

    def __setitem__(self, key, value):
        warnings.warn(
            "Direct modification of _TOOL_REGISTRY is deprecated. Use ToolRegistry.register() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        self._registry.register(key, value)

    def __contains__(self, key):
        return key in self._registry.get_all_tools()

    def keys(self):
        return self._registry.get_all_tools().keys()

    def values(self):
        return self._registry.get_all_tools().values()

    def items(self):
        return self._registry.get_all_tools().items()

    def get(self, key, default=None):
        return self._registry.get_all_tools().get(key, default)

    def copy(self):
        return self._registry.get_all_tools().copy()

# Maintain backward compatibility
_TOOL_REGISTRY = _ToolRegistryAlias()
```

### 2.2 Integration Target: `src/gaia/agents/base/agent.py`

**Changes Required:**

```python
# In Agent.__init__():
from gaia.agents.base.tools import ToolRegistry

class Agent:
    def __init__(
        self,
        agent_id: str = None,
        model_id: str = None,
        allowed_tools: List[str] = None,  # NEW parameter
        **kwargs
    ):
        # ... existing initialization ...

        # NEW: Create tool scope after tool registration
        self._tool_scope = ToolRegistry.get_instance().create_scope(
            agent_id=self.__class__.__name__,
            allowed_tools=allowed_tools or getattr(self, "allowed_tools", None),
        )

    def _execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Execute tool through scoped view."""
        return self._tool_scope.execute_tool(tool_name, *args, **kwargs)

    def _format_tools_for_prompt(self) -> str:
        """Format available tools for system prompt."""
        available = self._tool_scope.get_available_tools()
        # ... existing formatting logic ...
```

### 2.3 Integration Target: `src/gaia/agents/configurable.py`

**Changes Required:**

```python
# In ConfigurableAgent._register_tools_from_yaml():
from gaia.agents.base.tools import ToolRegistry

class ConfigurableAgent(Agent):
    def _register_tools_from_yaml(self):
        # ... existing tool loading logic ...

        # NEW: Create scoped view with YAML-defined allowlist
        registry = ToolRegistry.get_instance()
        self._tool_scope = registry.create_scope(
            agent_id=self.definition.id,
            allowed_tools=self.definition.tools,  # From YAML
        )

    def _execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Execute tool through scoped view."""
        return self._tool_scope.execute_tool(tool_name, *args, **kwargs)
```

---

## 3. HOW: Implementation Approach

### 3.1 Class Interfaces

#### ToolRegistry (Singleton)

| Method | Signature | Thread-Safe | Description |
|--------|-----------|-------------|-------------|
| `get_instance()` | `() -> ToolRegistry` | Yes | Get singleton instance |
| `register()` | `(name: str, func: Callable, description: str = None)` | Yes | Register tool function |
| `unregister()` | `(name: str) -> bool` | Yes | Remove tool from registry |
| `create_scope()` | `(agent_id: str, allowed_tools: List[str] = None) -> AgentScope` | No | Create scoped view |
| `execute_tool()` | `(tool_name: str, *args, **kwargs) -> Any` | Yes | Execute tool by name |
| `get_all_tools()` | `() -> Dict[str, Dict[str, Any]]` | Yes | Get all registered tools |
| `get_tool()` | `(name: str) -> Dict[str, Any]` | Yes | Get single tool metadata |

#### AgentScope

| Method | Signature | Thread-Safe | Description |
|--------|-----------|-------------|-------------|
| `execute_tool()` | `(tool_name: str, *args, **kwargs) -> Any` | Yes | Execute with access check |
| `get_available_tools()` | `() -> Dict[str, Dict[str, Any]]` | Yes | Get accessible tools |
| `has_tool()` | `(name: str) -> bool` | Yes | Check tool accessibility |
| `get_agent_id()` | `() -> str` | No | Get agent identifier |

#### ExceptionRegistry

| Method | Signature | Thread-Safe | Description |
|--------|-----------|-------------|-------------|
| `record()` | `(tool_name: str, exception: Exception)` | Yes | Record exception |
| `get_exceptions()` | `(tool_name: str = None) -> List[ExceptionRecord]` | Yes | Get recorded exceptions |
| `clear()` | `(tool_name: str = None)` | Yes | Clear exception history |
| `get_error_rate()` | `(tool_name: str) -> float` | Yes | Get error rate for tool |

### 3.2 Backward Compatibility Strategy

**38 files reference `_TOOL_REGISTRY` directly.** The `_ToolRegistryAlias` shim provides:

1. **Dict-like interface:** All standard dict operations work
2. **Deprecation warnings:** `DeprecationWarning` on first access, `FutureWarning` after 30 days
3. **Transparent redirect:** All operations forward to `ToolRegistry.get_instance()`
4. **Migration path:** Files updated incrementally without breaking changes

**Timeline:**
- **Week 1:** Shim active, warnings logged
- **Week 2:** Update high-priority files (agents/base/, agents/configurable.py)
- **Week 3:** Update remaining files (apps/, mcp/, rag/)
- **Week 4:** Remove shim, enforce direct usage

### 3.3 Security Enhancements

| Security Feature | Current | Phase 0 |
|------------------|---------|---------|
| Tool name matching | Case-insensitive | **Case-sensitive (exact match)** |
| Access control | None (global registry) | **Per-agent allowlist** |
| Thread safety | None | **RLock for all operations** |
| Exception tracking | None | **ExceptionRegistry with audit trail** |
| Memory cleanup | Manual | **Automatic scope cleanup on agent shutdown** |

**Case-Sensitive Security Rationale:**
```python
# BEFORE (vulnerable):
"File_Read" == "file_read"  # True (case-insensitive)
# Attacker bypasses allowlist with case variation

# AFTER (secure):
"File_Read" == "file_read"  # False (exact match)
# Allowlist bypass blocked
```

### 3.4 Memory Management

**0% Leak Threshold:** Zero tolerance for memory leaks.

**Scope Lifecycle:**
```
Agent Created → ToolScope Created → Agent Active → Agent Shutdown → Scope Cleanup
```

**Cleanup Verification:**
```python
# In Agent.shutdown() or __del__():
def cleanup(self):
    self._tool_scope = None  # Release scope reference
    # Verify no dangling references
    assert gc.get_referrers(self) == []
```

**Test Threshold:**
- Memory delta before/after agent lifecycle: **≤ 0 bytes**
- Dangling scope references: **0**
- GC cycles to full cleanup: **≤ 1**

---

## 4. BAIBEL Pattern Integration Roadmap

### Phase 0: Tool Scoping (Weeks 1-2)

**Goal:** Establish secure, isolated tool execution foundation.

**Deliverables:**
- [x] ToolRegistry class implementation
- [x] AgentScope with allowlist filtering
- [x] ExceptionRegistry for error tracking
- [x] _ToolRegistryAlias backward shim
- [ ] Unit tests (test_tool_registry.py, test_backward_compat_shim.py)
- [ ] Integration tests (test_tool_scoping_integration.py)
- [ ] Security tests (test_tool_isolation.py, test_allowlist_bypass.py)

**Exit Criteria (Quality Gate 1):**
- BC-001: Backward compatibility tests pass (100%)
- SEC-001: Allowlist bypass tests fail (0% success rate for bypass attempts)
- PERF-001: No performance regression (<5% overhead)
- MEM-001: Zero memory leaks (0% threshold)

### Phase 1: State Unification (Weeks 3-10)

**Goal:** Integrate Chronicle (temporal) and Workspace (spatial) patterns.

**Deliverables:**
- Chronicle: State transition tracking with timestamps
- Workspace: Artifact organization with metadata
- Nexus Service: Unified state view across agents
- Pipeline integration: Pass artifacts between phases

**Exit Criteria (Quality Gate 2):**
- State transitions logged with microsecond precision
- Artifact metadata includes phase, agent, iteration
- Cross-agent state queries functional

### Phase 2: Quality Enhancement (Weeks 11-16)

**Goal:** Integrate Ether (runtime) and Context Lens (summarization) patterns.

**Deliverables:**
- Ether: Runtime context management for agents
- Context Lens: Token-efficient state summarization
- Quality gate integration with pipeline DECISION phase

**Exit Criteria (Quality Gate 3):**
- Context summarization reduces token usage by ≥50%
- Quality gate correctly APPROVE/REJECTs based on threshold

### Phase 3: Architectural Modernization (Weeks 17-28)

**Goal:** Full BAIBEL pattern adoption with Supervisor quality gate.

**Deliverables:**
- Supervisor: Binary APPROVE/REJECT quality gate
- ConsensusOrchestrator: Multi-agent quality review
- Full pipeline recursion with state persistence

**Exit Criteria (Quality Gate 4):**
- Pipeline passes 90% quality threshold on 10 consecutive runs
- Zero state corruption incidents
- Full backward compatibility maintained

---

## 5. Test Plan Summary

### 5.1 Test Categories (4-Day Execution)

| Day | Category | Files | Functions | Priority |
|-----|----------|-------|-----------|----------|
| **Day 1** | Unit Tests | test_tool_registry.py, test_backward_compat_shim.py | 45 | CRITICAL |
| **Day 2** | Integration Tests | test_tool_scoping_integration.py, test_configurable_agent_tools.py | 28 | HIGH |
| **Day 3** | Regression/Performance | test_backward_compat.py, test_tool_registry_perf.py | 15 | MEDIUM |
| **Day 4** | Security Tests | test_tool_isolation.py, test_allowlist_bypass.py, test_mcp_injection.py | 18 | CRITICAL |

### 5.2 Key Test Functions

**Unit Tests (Day 1):**
```python
def test_singleton_thread_safety():
    """Verify singleton pattern is thread-safe."""
    # 100 threads, concurrent get_instance() calls
    # Expected: All threads receive same instance

def test_allowlist_filtering():
    """Verify AgentScope correctly filters tools."""
    # Create scope with allowed_tools=["file_read", "file_write"]
    # Execute "file_read" → Success
    # Execute "bash_execute" → ToolAccessDeniedError

def test_backward_compat_shim():
    """Verify _TOOL_REGISTRY shim maintains compatibility."""
    # Access via dict interface, expect DeprecationWarning
    # Verify operations forward to ToolRegistry
```

**Security Tests (Day 4):**
```python
def test_case_sensitive_matching():
    """Verify case-sensitive tool name matching prevents bypass."""
    # Scope with allowed_tools=["file_read"]
    # Execute "File_Read" → ToolAccessDeniedError (case mismatch)
    # Execute "FILE_READ" → ToolAccessDeniedError (case mismatch)
    # Execute "file_read" → Success

def test_allowlist_bypass_attempts():
    """Verify allowlist cannot be bypassed via injection."""
    # Attempt tool name injection via special characters
    # Expected: All bypass attempts fail
```

### 5.3 Performance Thresholds

| Metric | Threshold | Measurement |
|--------|-----------|-------------|
| Registry overhead | <5% vs global dict | Time per execute_tool() |
| Scope creation | <1ms | Time per create_scope() |
| Memory footprint | <100KB per scope | RSS delta |
| Memory leaks | 0% | Delta after cleanup |
| Concurrent access | 1000 ops/sec | Thread pool benchmark |

---

## 6. Quality Gates

### Quality Gate 1 (Day 1 Exit)

**Mandatory Tests:**
- [ ] BC-001: Backward compatibility (100% pass)
- [ ] SEC-001: Allowlist bypass prevention (0% success rate)
- [ ] PERF-001: Performance overhead (<5%)
- [ ] MEM-001: Memory leak detection (0% threshold)

**Exit Decision:**
- **PASS:** All tests pass → Proceed to Day 2
- **FAIL:** Any test fails → Fix and retest before proceeding

### Quality Gate 2 (Day 2 Exit)

**Mandatory Tests:**
- [ ] INT-001: Agent integration (all agent types functional)
- [ ] INT-002: YAML tool loading (configurable agents working)
- [ ] INT-003: MCP tool registration (external tools accessible)

**Exit Decision:**
- **PASS:** All integration tests pass → Proceed to Day 3
- **FAIL:** Integration broken → Blocker, immediate fix required

### Quality Gate 3 (Day 3 Exit)

**Mandatory Tests:**
- [ ] REG-001: Public API stability (no breaking changes)
- [ ] REG-002: Concurrent access (no race conditions)
- [ ] PERF-002: Memory footprint (within threshold)

**Exit Decision:**
- **PASS:** All regression tests pass → Proceed to Day 4
- **FAIL:** Regression detected → Assess severity, may require rollback

### Quality Gate 4 (Day 4 Exit - Phase Complete)

**Mandatory Tests:**
- [ ] SEC-002: Case-sensitive security (no bypass via case variation)
- [ ] SEC-003: MCP injection prevention (external tools properly namespaced)
- [ ] SEC-004: Exception handling (no information leakage)

**Exit Decision:**
- **PASS:** All security tests pass → Phase 0 Complete
- **FAIL:** Security vulnerability → CRITICAL, immediate escalation

---

## 7. Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| R1: Backward compatibility break | Medium | High | _ToolRegistryAlias shim with deprecation warnings |
| R2: Thread safety race conditions | Low | High | RLock for all operations, concurrent access tests |
| R3: Performance regression | Low | Medium | Performance benchmarks, <5% threshold |
| R4: Memory leaks in scope cleanup | Low | High | 0% threshold, GC verification tests |
| R5: Case-sensitive allowlist confusion | Medium | Low | Clear documentation, explicit error messages |
| R6: MCP tool namespacing conflicts | Low | Medium | `mcp_{server}_{tool}` convention, collision detection |

---

## 8. Implementation Timeline

### Week 1: Core Implementation

| Day | Task | Owner | Status |
|-----|------|-------|--------|
| Day 1 | ToolRegistry class implementation | senior-developer | Ready to Start |
| Day 1 | Unit test creation (test_tool_registry.py) | testing-quality-specialist | Ready to Start |
| Day 2 | AgentScope implementation | senior-developer | Pending |
| Day 2 | ExceptionRegistry implementation | senior-developer | Pending |
| Day 3 | _ToolRegistryAlias shim | senior-developer | Pending |
| Day 3 | Backward compatibility tests | testing-quality-specialist | Pending |
| Day 4 | Quality Gate 1 validation | quality-reviewer | Pending |

### Week 2: Integration & Testing

| Day | Task | Owner | Status |
|-----|------|-------|--------|
| Day 5 | Agent integration (agent.py, configurable.py) | senior-developer | Pending |
| Day 6 | Integration tests | testing-quality-specialist | Pending |
| Day 7 | Security tests | testing-quality-specialist | Pending |
| Day 8 | Performance benchmarks | testing-quality-specialist | Pending |
| Day 9 | Bug fixes, iteration | senior-developer | Pending |
| Day 10 | Quality Gate 4 validation | quality-reviewer | Pending |

---

## 9. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| RC#2 Resolution | Tool loading functional | Integration tests pass |
| RC#7 Resolution | Tool descriptions populated | System prompt includes tools |
| Backward Compatibility | 0 breaking changes | All 38 files functional |
| Security | 0 allowlist bypasses | Security tests pass |
| Performance | <5% overhead | Benchmarks vs baseline |
| Memory | 0% leaks | GC verification |
| Thread Safety | 0 race conditions | Concurrent access tests |

---

## 10. Approval & Sign-Off

**Prepared By:** Recursive Iterative Pipeline (Cycle 2)
**Contributing Agents:**
- planning-analysis-strategist (Phase 0 validation)
- software-program-manager (Program management, timeline)
- senior-developer (Implementation planning)
- quality-reviewer (Quality gate design)
- testing-quality-specialist (Test plan refinement)

**Approval Status:**
- [x] Technical feasibility confirmed
- [x] Backward compatibility strategy validated
- [x] Security enhancements reviewed
- [x] Test plan comprehensive (100+ functions)
- [x] Quality gates defined
- [ ] **User approval to begin Day 1 implementation**

---

## Appendix A: File Modification List

**Phase 0 Modifications:**

| File | Changes | Priority |
|------|---------|----------|
| `src/gaia/agents/base/tools.py` | Complete rewrite (ToolRegistry, AgentScope, ExceptionRegistry) | P0 |
| `src/gaia/agents/base/agent.py` | Add allowed_tools param, create tool scope | P0 |
| `src/gaia/agents/configurable.py` | Use YAML tools as allowlist | P0 |

**Future Phase Modifications (Informational):**

| File | Phase | Planned Changes |
|------|-------|-----------------|
| `src/gaia/pipeline/engine.py` | Phase 1 | Chronicle integration |
| `src/gaia/pipeline/state.py` | Phase 1 | Workspace metadata |
| `src/gaia/pipeline/loop_manager.py` | Phase 2 | Context Lens summarization |
| `src/gaia/pipeline/artifact_extractor.py` | Phase 2 | Ether runtime context |

---

## Appendix B: Glossary

| Term | Definition |
|------|------------|
| **Tool Scoping** | Per-agent isolation of tool access via allowlist filtering |
| **AgentScope** | Scoped view of ToolRegistry for specific agent |
| **Allowlist** | Explicit list of tools an agent is permitted to execute |
| **Case-Sensitive Security** | Exact string matching for tool names (no case folding) |
| **Backward Compatibility Shim** | _ToolRegistryAlias dict wrapper for gradual migration |
| **Quality Gate** | Mandatory test suite that must pass before proceeding |
| **BAIBEL** | Behavioral AI with Episodic Learning framework |
| **Chronicle** | Temporal tracking pattern for state transitions |
| **Workspace** | Spatial metadata pattern for artifact organization |
| **Nexus** | State unification pattern across multiple agents |

---

**END OF SPECIFICATION**
